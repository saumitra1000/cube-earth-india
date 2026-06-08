"""
CDSE Optical Extractor — uses Process API with numpy pixel analysis.
Process API works with free CDSE credentials. Statistics API requires paid plan.
"""
import httpx
import datetime
import io
import numpy as np
from extractors.base_extractor import BaseExtractor
from config.settings import settings

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"


class CDSEOpticalExtractor(BaseExtractor):

    def __init__(self):
        super().__init__("cdse_optical")
        self._token = None
        self._token_expiry = None

    async def _get_token(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        if self._token and self._token_expiry and now < self._token_expiry:
            return self._token
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(TOKEN_URL, data={
                "grant_type": "client_credentials",
                "client_id": settings.CDSE_CLIENT_ID,
                "client_secret": settings.CDSE_CLIENT_SECRET,
            })
            r.raise_for_status()
            self._token = r.json()["access_token"]
            self._token_expiry = now + datetime.timedelta(seconds=500)
            return self._token

    async def extract(self, lat, lng, start_date=None, end_date=None):
        try:
            token = await self._get_token()
            now = datetime.datetime.now(datetime.timezone.utc)
            t_end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            t_start = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            pad = 0.01
            bbox = [lng-pad, lat-pad, lng+pad, lat+pad]

            # Evalscript: encode NDVI as grayscale PNG (0=NDVI -1, 255=NDVI +1)
            evalscript = """//VERSION=3
function setup(){
  return{
    input:["B04","B08"],
    output:{bands:1,sampleType:"UINT8"}
  }
}
function evaluatePixel(s){
  var ndvi=(s.B08-s.B04)/(s.B08+s.B04+0.0001);
  return[Math.round((ndvi+1)*127.5)];
}"""

            payload = {
                "input": {
                    "bounds": {
                        "bbox": bbox,
                        "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
                    },
                    "data": [{
                        "type": "sentinel-2-l2a",
                        "dataFilter": {
                            "timeRange": {"from": t_start, "to": t_end},
                            "maxCloudCoverage": 80,
                            "mosaickingOrder": "leastCC"
                        }
                    }]
                },
                "evalscript": evalscript,
                "output": {
                    "width": 64,
                    "height": 64,
                    "responses": [{
                        "identifier": "default",
                        "format": {"type": "image/png"}
                    }]
                }
            }

            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    PROCESS_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    }
                )

            if r.status_code != 200:
                return {"available": False, "error": f"API {r.status_code}: {r.text[:200]}"}

            # Parse PNG with PIL
            from PIL import Image
            img = Image.open(io.BytesIO(r.content)).convert("L")
            arr = np.array(img).astype(float)

            # Decode: NDVI = (pixel/127.5) - 1
            ndvi_arr = (arr / 127.5) - 1.0

            # Vegetation mask
            all_pixels = ndvi_arr.flatten()
            total_pixels = len(all_pixels)
            vegetated = all_pixels[all_pixels > 0.15]
            veg_pct = round(len(vegetated) / total_pixels * 100, 1) if total_pixels > 0 else 100

            # Valid pixels for stats
            valid = all_pixels[(all_pixels > 0.1) & (all_pixels < 1.0)]

            if len(valid) < 10:
                return {"available": False, "error": "Insufficient valid pixels",
                        "total_pixels": total_pixels, "valid_count": len(valid)}

            ndvi_mean = float(np.mean(valid))
            ndvi_std = float(np.std(valid))
            ndvi_p25 = float(np.percentile(valid, 25))
            ndvi_p75 = float(np.percentile(valid, 75))

            # Quadrant NDVI from pixel array
            h, w = ndvi_arr.shape
            mh, mw = h//2, w//2
            def qm(arr):
                v=arr[(arr>0.1)&(arr<1.0)]
                return round(float(np.mean(v)),3) if len(v)>5 else None
            quad_ndvi = {'NW':qm(ndvi_arr[:mh,:mw]),'NE':qm(ndvi_arr[:mh,mw:]),'SW':qm(ndvi_arr[mh:,:mw]),'SE':qm(ndvi_arr[mh:,mw:])}
            # Use IQR-based formula matching profile_builder calibration
            iqr = ndvi_p75 - ndvi_p25 if (ndvi_p25 and ndvi_p75) else ndvi_std * 1.35
            uniformity = round(max(1, min(10, 10 - (iqr * 18))), 1)

            img_date = now.strftime("%Y-%m-%d")

            return {
                "available": True,
                "ndvi": round(ndvi_mean, 4),
                "ndre": None,
                "cire": None,
                "gcap": None,
                "ndvi_std": round(ndvi_std, 4),
                "ndvi_p25": round(ndvi_p25, 4),
                "ndvi_p75": round(ndvi_p75, 4),
                "uniformity": uniformity,
                "quad_ndvi": quad_ndvi,
                "date": img_date,
                "age_days": 0,
                "source": "Copernicus CDSE Sentinel-2 L2A 10m",
                "cloud_cover": 0,
                "sample_count": len(valid),
                "veg_pct": veg_pct,
                "non_veg_pct": round(100 - veg_pct, 1)
            }

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False}
        return raw
