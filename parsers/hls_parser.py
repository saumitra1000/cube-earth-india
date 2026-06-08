import httpx
import struct
import math
import zlib
import numpy as np
from config.settings import settings


def _headers():
    return {"Authorization": f"Bearer {settings.NASA_TOKEN}"}


async def read_pixel(url, lat, lng):
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(url, headers={**_headers(), "Range": "bytes=0-65535"})
        if not r.is_success:
            return None
        buf = r.content
        if len(buf) < 8:
            return None

        le = buf[0] == 0x49
        fmt = "<" if le else ">"

        ifd_off = struct.unpack_from(f"{fmt}I", buf, 4)[0]
        n_tags = struct.unpack_from(f"{fmt}H", buf, ifd_off)[0]
        tags = {}
        for i in range(n_tags):
            off = ifd_off + 2 + i * 12
            if off + 12 > len(buf):
                break
            tag = struct.unpack_from(f"{fmt}H", buf, off)[0]
            val = struct.unpack_from(f"{fmt}I", buf, off + 8)[0]
            tags[tag] = val

        w  = tags.get(256, 0)
        h  = tags.get(257, 0)
        tw = tags.get(322, 256)
        th = tags.get(323, 256)
        to = tags.get(324, 0)
        tc = tags.get(325, 0)
        scale_off = tags.get(33550, 0)
        tie_off   = tags.get(33922, 0)

        if not w or not h or not scale_off or not tie_off:
            return None

        sc_x  = struct.unpack_from(f"{fmt}d", buf, scale_off)[0]
        sc_y  = struct.unpack_from(f"{fmt}d", buf, scale_off + 8)[0]
        tie_x = struct.unpack_from(f"{fmt}d", buf, tie_off + 24)[0]
        tie_y = struct.unpack_from(f"{fmt}d", buf, tie_off + 32)[0]

        # WGS84 to UTM
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
        if lat < 0:
            northing += 10000000

        px = int((easting  - tie_x) / sc_x)
        py = int((tie_y - northing) / sc_y)

        if px < 0 or py < 0 or px >= w or py >= h:
            return None

        ta  = math.ceil(w / tw)
        ti  = (py // th) * ta + (px // tw)

        or_ = await client.get(url, headers={**_headers(), "Range": f"bytes={to+ti*4}-{to+ti*4+3}"})
        cr_ = await client.get(url, headers={**_headers(), "Range": f"bytes={tc+ti*4}-{tc+ti*4+3}"})
        t_off = struct.unpack_from(f"{fmt}I", or_.content)[0]
        t_sz  = struct.unpack_from(f"{fmt}I", cr_.content)[0]

        if not t_off or not t_sz:
            return None

        dr = await client.get(url, headers={**_headers(), "Range": f"bytes={t_off}-{t_off+t_sz-1}"})
        raw = zlib.decompress(dr.content)

        # Undo horizontal differencing predictor (TIFF predictor=2)
        arr = np.frombuffer(raw, dtype="<u2").reshape(th, tw).copy()
        for col in range(1, tw):
            arr[:, col] = (arr[:, col] + arr[:, col-1]) & 0xFFFF

        lx  = px % tw
        ly  = py % th
        val = int(arr[ly, lx])

        # HLS fill value is 0 or 65535
        if val == 0 or val == 65535:
            return None
        return val


async def compute_indices(granule, lat, lng):
    bands = granule.get("bands", {})
    b04_url = bands.get("B04")
    b05_url = bands.get("B05")
    b8a_url = bands.get("B8A")

    if not b04_url or not b8a_url:
        return None

    import asyncio
    b04, b05, b8a = await asyncio.gather(
        read_pixel(b04_url, lat, lng),
        read_pixel(b05_url, lat, lng) if b05_url else asyncio.sleep(0, result=None),
        read_pixel(b8a_url, lat, lng),
    )

    scale = 0.0001
    results = {}

    if b04 is not None and b8a is not None:
        r = b04 * scale
        n = b8a * scale
        if n + r > 0:
            results["ndvi"] = round((n - r) / (n + r), 4)

    if b05 is not None and b8a is not None:
        re = b05 * scale
        n  = b8a * scale
        if n + re > 0:
            results["ndre"] = round((n - re) / (n + re), 4)
            results["cire"] = round((n / (re + 1e-6)) - 1, 4)

    if "ndvi" in results and "ndre" in results:
        ndvi = results["ndvi"]
        ndre = results["ndre"]
        chlorophyll = max(0, min(0.72, ndre * 1.15))
        structure   = max(0, min(1, (ndvi - 0.2) / 0.7))
        results["gcap"] = round(structure * chlorophyll, 4)

    results["b04_dn"] = b04
    results["b05_dn"] = b05
    results["b8a_dn"] = b8a
    return results
