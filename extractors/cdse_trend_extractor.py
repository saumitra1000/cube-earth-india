"""
CDSE Trend Extractor for Cube Earth.
Uses Process API PNG approach — proven working.
90-day NDVI series via Sentinel Hub.
"""
import httpx
import datetime
import io
import numpy as np
from PIL import Image
from extractors.base_extractor import BaseExtractor
from config.settings import settings

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"


class CDSETrendExtractor(BaseExtractor):

    def __init__(self):
        super().__init__("cdse_trend")
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

    async def _get_ndvi(self, lat, lng, date_str, token):
        """Get NDVI for a specific date using Process API."""
        pad = 0.005
        bbox = [lng-pad, lat-pad, lng+pad, lat+pad]
        t_start = f"{date_str}T00:00:00Z"
        t_end = f"{date_str}T23:59:59Z"

        evalscript = """//VERSION=3
function setup(){
  return{input:[{bands:["B04","B08"]}],output:{bands:1,sampleType:"UINT8"}}
}
function evaluatePixel(s){
  var ndvi=(s.B08-s.B04)/(s.B08+s.B04+0.0001);
  return[Math.round((ndvi+1)*127.5)];
}"""

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                PROCESS_URL,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "input": {
                        "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                        "data": [{"type": "sentinel-2-l2a", "dataFilter": {
                            "timeRange": {"from": t_start, "to": t_end},
                            "maxCloudCoverage": 80,
                            "mosaickingOrder": "leastCC"
                        }}]
                    },
                    "evalscript": evalscript,
                    "output": {"width": 64, "height": 64, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]}
                }
            )

        if r.status_code != 200:
            return None

        img = Image.open(io.BytesIO(r.content)).convert("L")
        arr = np.array(img).astype(float)
        ndvi_arr = (arr / 127.5) - 1.0
        valid = ndvi_arr[(ndvi_arr > 0.1) & (ndvi_arr < 1.0)]
        if len(valid) < 10:
            return None
        return round(float(np.mean(valid)), 3)

    async def extract(self, lat, lng, days=90):
        try:
            token = await self._get_token()
            now = datetime.datetime.now(datetime.timezone.utc)
            series = []

            # Sample every 10 days over the period
            for i in range(days, -1, -10):
                date = now - datetime.timedelta(days=i)
                date_str = date.strftime("%Y-%m-%d")
                ndvi = await self._get_ndvi(lat, lng, date_str, token)
                if ndvi is not None:
                    series.append({"date": date_str, "ndvi": ndvi})

            if len(series) < 2:
                return {"available": False, "error": "Insufficient data"}

            first = series[0]["ndvi"]
            last = series[-1]["ndvi"]
            diff = round(last - first, 3)

            if diff > 0.15:
                long_trend = "strong_increase"
                trend_arrow = "📈"
                trend_label = "Strong vegetation growth"
            elif diff > 0.05:
                long_trend = "increasing"
                trend_arrow = "↗"
                trend_label = "Vegetation improving"
            elif diff < -0.15:
                long_trend = "strong_decline"
                trend_arrow = "📉"
                trend_label = "Significant vegetation decline"
            elif diff < -0.05:
                long_trend = "declining"
                trend_arrow = "↘"
                trend_label = "Vegetation softening"
            else:
                long_trend = "stable"
                trend_arrow = "→"
                trend_label = "Stable vegetation"

            events = []
            for i in range(1, len(series)):
                drop = series[i-1]["ndvi"] - series[i]["ndvi"]
                if drop > 0.15:
                    events.append({
                        "type": "harvest_or_cut",
                        "label": "Possible mowing or cutting",
                        "date": series[i]["date"],
                        "ndvi_before": series[i-1]["ndvi"],
                        "ndvi_after": series[i]["ndvi"],
                        "confidence": "high" if drop > 0.25 else "moderate"
                    })

            return {
                "available": True,
                "series": series,
                "count": len(series),
                "long_trend": long_trend,
                "long_diff": diff,
                "trend_arrow": trend_arrow,
                "trend_label": trend_label,
                "latest_ndvi": last,
                "latest_date": series[-1]["date"],
                "events": events,
                "source": "Copernicus CDSE Sentinel-2 L2A"
            }

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False}
        return raw
