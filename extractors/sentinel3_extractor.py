"""
Sentinel-3 OLCI Extractor
Uses L1 radiance bands with dark object subtraction
Oa08 = 665nm Red, Oa17 = 865nm NIR
"""
import datetime
import numpy as np
import os
import io

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

class Sentinel3Extractor:
    def __init__(self):
        self._token = None
        self._token_expiry = None

    async def _get_token(self):
        import aiohttp
        now = datetime.datetime.utcnow()
        if self._token and self._token_expiry and now < self._token_expiry:
            return self._token
        async with aiohttp.ClientSession() as session:
            data = {
                "grant_type": "client_credentials",
                "client_id": os.environ.get("CDSE_CLIENT_ID", ""),
                "client_secret": os.environ.get("CDSE_CLIENT_SECRET", "")
            }
            async with session.post(TOKEN_URL, data=data) as r:
                resp = await r.json()
                self._token = resp.get("access_token")
                self._token_expiry = now + datetime.timedelta(seconds=500)
                return self._token

    async def get_ndvi(self, lat, lng, days_back=10):
        """
        Get NDVI from Sentinel-3 OLCI L1
        Oa08=665nm (Red), Oa17=865nm (NIR)
        300m resolution, 2-day revisit
        """
        import aiohttp
        try:
            token = await self._get_token()
            if not token:
                return {"available": False, "error": "No token"}

            today = datetime.datetime.utcnow()
            date_from = (today - datetime.timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00Z")
            date_to = today.strftime("%Y-%m-%dT23:59:59Z")

            pad = 0.01  # ~1km buffer for 300m pixels
            bbox = [lng-pad, lat-pad, lng+pad, lat+pad]

            payload = {
                "input": {
                    "bounds": {"bbox": bbox},
                    "data": [{
                        "type": "sentinel-3-olci",
                        "dataFilter": {
                            "timeRange": {
                                "from": date_from,
                                "to": date_to
                            }
                        }
                    }]
                },
                "output": {
                    "width": 7,
                    "height": 7,
                    "responses": [{"identifier": "default", 
                                   "format": {"type": "image/tiff"}}]
                },
                "evalscript": """//VERSION=3
function setup() {
  return {
    input: [{bands: ["B08", "B17", "dataMask"]}],
    output: {bands: 3, sampleType: "FLOAT32"}
  };
}
function evaluatePixel(s) {
  if (s.dataMask === 0) return [-1, -1, 0];
  // Convert radiance to approximate reflectance
  // Solar irradiance at B08(665nm) ~ 1749, B17(865nm) ~ 955 W/m2/um
  // rho = pi * L / (E0 * cos(sza)) — simplified without SZA
  var red = s.B08 / 1749.0;
  var nir = s.B17 / 955.0;
  var ndvi = (nir - red) / (nir + red + 0.0001);
  return [red, nir, ndvi];
}"""
            }

            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
                async with session.post(PROCESS_URL, json=payload, 
                                        headers=headers) as r:
                    if r.status != 200:
                        error = await r.text()
                        return {"available": False, 
                                "error": f"HTTP {r.status}: {error[:200]}"}

                    content_bytes = await r.read()
                    
                    try:
                        import tifffile
                        arr = tifffile.imread(io.BytesIO(content_bytes))
                    except Exception as e:
                        return {"available": False, 
                                "error": f"TIFF parse error: {str(e)}"}

                    if arr.ndim == 3:
                        if arr.shape[0] == 3:
                            red_band = arr[0]
                            nir_band = arr[1]
                            ndvi_band = arr[2]
                        else:
                            red_band = arr[:,:,0]
                            nir_band = arr[:,:,1]
                            ndvi_band = arr[:,:,2]
                    else:
                        return {"available": False, 
                                "error": f"Unexpected shape: {arr.shape}"}

                    valid = (ndvi_band > -0.5) & (ndvi_band < 1.0)
                    
                    if not np.any(valid):
                        return {"available": False, "error": "No valid pixels"}

                    ndvi_mean = float(np.mean(ndvi_band[valid]))
                    return {
                        "available": True,
                        "ndvi_s3": round(ndvi_mean, 4),
                        "pixel_count": int(np.sum(valid)),
                        "resolution_m": 300,
                        "revisit_days": 2,
                        "note": "S3 OLCI L1 B08=665nm B17=865nm"
                    }

        except Exception as e:
            return {"available": False, "error": str(e)}
