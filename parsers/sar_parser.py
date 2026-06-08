import httpx
import math
import numpy as np
from config.settings import settings


CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_STATS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{bands: ["VV", "VH"], units: "LINEAR_POWER"}],
    output: [
      {id: "vv", bands: 1, sampleType: "FLOAT32"},
      {id: "vh", bands: 1, sampleType: "FLOAT32"},
      {id: "dataMask", bands: 1, sampleType: "UINT8"}
    ]
  };
}
function evaluatePixel(s) {
  return {vv: [s.VV], vh: [s.VH], dataMask: [1]};
}"""


async def _get_token():
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            CDSE_TOKEN_URL,
            data={
                "grant_type":    "client_credentials",
                "client_id":     settings.CDSE_CLIENT_ID,
                "client_secret": settings.CDSE_CLIENT_SECRET,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


def _bbox_around(lat, lng, half_m=150):
    dlat = half_m / 111320
    dlng = half_m / (111320 * math.cos(math.radians(lat)))
    return [lng - dlng, lat - dlat, lng + dlng, lat + dlat]


async def extract_s1_backscatter(lat, lng, start_date, end_date):
    """
    Calibrated Sentinel-1 VV/VH via CDSE Statistics API.
    - SIGMA0_ELLIPSOID calibration
    - LEE 5x5 speckle filter
    - 150m spatial window
    - Multi-acquisition temporal averaging (Barrett 2014)
    """
    token = await _get_token()
    bbox  = _bbox_around(lat, lng, half_m=150)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    payload = {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {
                        "from": f"{start_date}T00:00:00Z",
                        "to":   f"{end_date}T23:59:59Z",
                    },
                    "acquisitionMode": "IW",
                    "polarization":    "DV",
                },
                "processing": {
                    "backCoeff":     "SIGMA0_ELLIPSOID",
                    "orthorectify":  True,
                    "demInstance":   "COPERNICUS",
                    "speckleFilter": {"type": "LEE", "windowSizeX": 5, "windowSizeY": 5},
                },
            }],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{start_date}T00:00:00Z",
                "to":   f"{end_date}T23:59:59Z",
            },
            "aggregationInterval": {"of": "P12D"},
            "evalscript": EVALSCRIPT,
            "resx": 0.0003,
            "resy": 0.0003,
        },
        "calculations": {
            "default": {
                "statistics": {
                    "default": {
                        "percentiles": {"k": [25, 50, 75]}
                    }
                }
            }
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(CDSE_STATS_URL, json=payload, headers=headers)

    if r.status_code != 200:
        return {
            "available": False,
            "reason":    f"CDSE Stats API {r.status_code}",
            "detail":    r.text[:400],
        }

    data      = r.json()
    intervals = data.get("data", [])
    if not intervals:
        return {"available": False, "reason": "no_data_in_period"}

    vv_vals, vh_vals, times = [], [], []
    for interval in intervals:
        outputs  = interval.get("outputs", {})
        vv_stats = outputs.get("vv", {}).get("bands", {}).get("B0", {}).get("stats", {})
        vh_stats = outputs.get("vh", {}).get("bands", {}).get("B0", {}).get("stats", {})
        vv_mean  = vv_stats.get("mean")
        vh_mean  = vh_stats.get("mean")
        if (vv_mean and vh_mean
                and vv_mean == vv_mean
                and vh_mean == vh_mean
                and 0.0001 < vv_mean < 1.0
                and 0.0001 < vh_mean < 0.5):
            vv_vals.append(vv_mean)
            vh_vals.append(vh_mean)
            times.append(interval.get("interval", {}).get("from", ""))

    if not vv_vals:
        return {"available": False, "reason": "all_acquisitions_masked"}

    vv_final = float(np.mean(vv_vals))
    vh_final = float(np.mean(vh_vals))
    vv_db    = round(10 * math.log10(vv_final), 3)
    vh_db    = round(10 * math.log10(vh_final), 3)
    rvi      = round((4 * vh_final) / (vv_final + vh_final + 1e-9), 4)

    def interpret_rvi(v):
        if v > 1.2:  return "Very dense canopy"
        if v > 0.8:  return "Dense vegetation"
        if v > 0.5:  return "Moderate vegetation"
        if v > 0.25: return "Sparse vegetation"
        return "Very sparse or bare"

    def canopy_wetness(vh):
        if vh > -10: return "Dense wet canopy"
        if vh > -15: return "Moist vegetation"
        if vh > -20: return "Dry or sparse"
        return "Bare or water"

    return {
        "available":          True,
        "vv_db":              vv_db,
        "vh_db":              vh_db,
        "vv_linear":          round(vv_final, 8),
        "vh_linear":          round(vh_final, 8),
        "rvi":                rvi,
        "rvi_interpretation": interpret_rvi(rvi),
        "canopy_wetness":     canopy_wetness(vh_db),
        "acquisitions":       len(vv_vals),
        "latest_acquisition": times[-1][:10] if times else None,
        "window_m":           "150m radius",
        "speckle_filter":     "LEE 5x5",
        "calibration":        "SIGMA0_ELLIPSOID terrain corrected",
        "note":               f"{len(vv_vals)} acquisitions averaged — speckle reduced per Barrett 2014",
        "source":             "CDSE Sentinel Hub — Sentinel-1 GRD",
        "literature":         "Barrett 2014: multi-date SAR for stable agricultural signal",
    }
