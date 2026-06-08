import httpx
import math
import struct
import zlib
import numpy as np
from config.settings import settings


def _headers():
    return {"Authorization": f"Bearer {settings.NASA_TOKEN}"}


def _utm(lat, lng):
    zone = int((lng + 180) / 6) + 1
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi  = math.radians(lat)
    lam  = math.radians(lng)
    a, e2, k0 = 6378137.0, 0.00669438, 0.9996
    N = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    T = math.tan(phi) ** 2
    C = (e2 / (1 - e2)) * math.cos(phi) ** 2
    A = math.cos(phi) * (lam - lon0)
    M = a * (
        (1 - e2/4 - 3*e2**2/64) * phi
        - (3*e2/8 + 3*e2**2/32) * math.sin(2*phi)
        + (15*e2**2/256) * math.sin(4*phi)
    )
    easting  = k0 * N * (A + (1-T+C)*A**3/6) + 500000
    northing = k0 * (M + N*math.tan(phi)*(A**2/2 + (5-T+9*C)*A**4/24))
    return easting, northing


async def _parse_tiff_header(client, url):
    r = await client.get(url, headers={**_headers(), "Range": "bytes=0-65535"})
    if not r.is_success:
        return None
    buf = r.content
    if len(buf) < 8:
        return None
    le  = buf[0] == 0x49
    fmt = "<" if le else ">"
    ifd_off = struct.unpack_from(f"{fmt}I", buf, 4)[0]
    n_tags  = struct.unpack_from(f"{fmt}H", buf, ifd_off)[0]
    tags = {}
    for i in range(n_tags):
        off = ifd_off + 2 + i * 12
        if off + 12 > len(buf): break
        tag = struct.unpack_from(f"{fmt}H", buf, off)[0]
        val = struct.unpack_from(f"{fmt}I", buf, off + 8)[0]
        tags[tag] = val
    return {"buf": buf, "fmt": fmt, "tags": tags}


def _pixel_coords(header, lat, lng):
    tags = header["tags"]
    buf  = header["buf"]
    fmt  = header["fmt"]
    w  = tags.get(256, 0)
    h  = tags.get(257, 0)
    scale_off = tags.get(33550, 0)
    tie_off   = tags.get(33922, 0)
    if not scale_off or not tie_off:
        return None
    sc_x  = struct.unpack_from(f"{fmt}d", buf, scale_off)[0]
    sc_y  = struct.unpack_from(f"{fmt}d", buf, scale_off + 8)[0]
    tie_x = struct.unpack_from(f"{fmt}d", buf, tie_off + 24)[0]
    tie_y = struct.unpack_from(f"{fmt}d", buf, tie_off + 32)[0]
    e, n  = _utm(lat, lng)
    px = int((e - tie_x) / sc_x)
    py = int((tie_y - n) / sc_y)
    inside = 0 <= px < w and 0 <= py < h
    return {
        "px": px, "py": py, "w": w, "h": h,
        "inside": inside,
        "tw": tags.get(322, 256), "th": tags.get(323, 256),
        "to": tags.get(324, 0),   "tc": tags.get(325, 0),
        "bps": tags.get(258, 16), "sf": tags.get(339, 1),
    }


async def _read_pixel(client, url, coords, header, is_qc=False):
    px, py = coords["px"], coords["py"]
    tw, th = coords["tw"], coords["th"]
    to, tc = coords["to"], coords["tc"]
    fmt    = header["fmt"]
    w      = coords["w"]
    bps    = coords["bps"]
    sf     = coords["sf"]

    ta  = math.ceil(w / tw)
    ti  = (py // th) * ta + (px // tw)

    or_ = await client.get(url, headers={**_headers(), "Range": f"bytes={to+ti*4}-{to+ti*4+3}"})
    cr_ = await client.get(url, headers={**_headers(), "Range": f"bytes={tc+ti*4}-{tc+ti*4+3}"})
    t_off = struct.unpack_from(f"{fmt}I", or_.content)[0]
    t_sz  = struct.unpack_from(f"{fmt}I", cr_.content)[0]

    if not t_off or not t_sz:
        return None

    dr  = await client.get(url, headers={**_headers(), "Range": f"bytes={t_off}-{t_off+t_sz-1}"})
    raw = zlib.decompress(dr.content)

    # Detect dtype from bps and sample format
    if sf == 3 and bps == 32:
        # float32 - no horizontal differencing predictor
        arr = np.frombuffer(raw, dtype="<f4").reshape(th, tw)
        lx  = px % tw
        ly  = py % th
        val = float(arr[ly, lx])
        if not np.isfinite(val) or val <= 0:
            return None
        return val
    elif bps == 8:
        # uint8 (QC)
        arr = np.frombuffer(raw, dtype="<u1").reshape(th, tw).copy()
        lx  = px % tw
        ly  = py % th
        val = int(arr[ly, lx])
        return val if not (is_qc is False and val == 255) else None
    else:
        # uint16 with horizontal differencing predictor
        arr = np.frombuffer(raw, dtype="<u2").reshape(th, tw).copy()
        for col in range(1, tw):
            arr[:, col] = (arr[:, col] + arr[:, col-1]) & 0xFFFF
        lx  = px % tw
        ly  = py % th
        val = int(arr[ly, lx])
        if is_qc:
            return val if val != 65535 else None
        return None if val == 0 or val == 65535 else val


def validate_qc(qc_val):
    if qc_val is None:
        return {"valid": False, "reason": "No QC data"}
    mandatory = qc_val & 0x03
    if mandatory == 0:
        return {"valid": True,  "quality": "excellent", "score": 100}
    if mandatory == 1:
        return {"valid": True,  "quality": "marginal",  "score": 50}
    if mandatory == 2:
        return {"valid": False, "quality": "cloud",     "score": 0}
    return {"valid": False, "quality": "not_produced", "score": 0}


async def extract_lst(granule_data, lat, lng):
    links   = granule_data.get("links", {})
    lst_url = links.get("lst")
    qc_url  = links.get("qc")
    if not lst_url:
        return {"available": False, "reason": "no_lst_link"}

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        if qc_url:
            qc_hdr = await _parse_tiff_header(client, qc_url)
            if qc_hdr:
                coords = _pixel_coords(qc_hdr, lat, lng)
                if coords and not coords["inside"]:
                    return {"available": False, "reason": "pixel_outside_tile"}
                if coords and coords["inside"]:
                    qc_dn = await _read_pixel(client, qc_url, coords, qc_hdr, is_qc=True)
                    qc    = validate_qc(qc_dn)
                    if not qc["valid"]:
                        return {
                            "available": False,
                            "reason":    qc.get("quality", "invalid"),
                            "qc":        qc,
                        }

        lst_hdr = await _parse_tiff_header(client, lst_url)
        if not lst_hdr:
            return {"available": False, "reason": "header_failed"}
        coords = _pixel_coords(lst_hdr, lat, lng)
        if not coords or not coords["inside"]:
            return {"available": False, "reason": "pixel_outside_tile"}

        lst_val = await _read_pixel(client, lst_url, coords, lst_hdr, is_qc=False)
        if lst_val is None:
            return {"available": False, "reason": "fill_value"}

        # Handle both float32 Kelvin and uint16 scaled
        if coords["sf"] == 3:
            kelvin = float(lst_val)
        else:
            kelvin = float(lst_val) * 0.02

        celsius = round(kelvin - 273.15, 2)

        def interpret(c):
            if c < 0:  return "Freezing"
            if c < 10: return "Cold"
            if c < 20: return "Cool"
            if c < 30: return "Warm"
            return "Hot"

        return {
            "available":      True,
            "celsius":        celsius,
            "kelvin":         round(kelvin, 3),
            "interpretation": interpret(celsius),
            "granule_time":   granule_data.get("time_start"),
            "source":         "ECOSTRESS ECO_L2T_LSTE V002",
            "resolution_m":   70,
        }
