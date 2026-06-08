"""
CDSE Historical Extractor for Cube Earth.
Gets same week NDVI across multiple years for CV analysis.
Sentinel-2 data available from 2017 onwards for Ireland.
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


class CDSEHistoricalExtractor(BaseExtractor):

    def __init__(self):
        super().__init__("cdse_historical")
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

    async def _get_ndvi_for_period(self, lat, lng, t_start, t_end, token):
        """Get mean NDVI for a time period."""
        pad = 0.005
        bbox = [lng-pad, lat-pad, lng+pad, lat+pad]

        evalscript = """//VERSION=3
function setup(){
  return{input:[{bands:["B04","B08"]}],output:{bands:1,sampleType:"UINT8"}}
}
function evaluatePixel(s){
  var ndvi=(s.B08-s.B04)/(s.B08+s.B04+0.0001);
  return[Math.round((ndvi+1)*127.5)];
}"""

        try:
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
        except Exception:
            return None

    async def extract(self, lat, lng, years=5):
        """Get same-week NDVI for past N years to build baseline."""
        try:
            token = await self._get_token()
            now = datetime.datetime.now(datetime.timezone.utc)
            
            # Get current week of year
            current_week = now.isocalendar()[1]
            current_year = now.year

            yearly_ndvi = {}
            ndvi_values = []

            # Get NDVI for same 2-week window in each past year
            for yr in range(current_year - years, current_year):
                try:
                    # Same week, different year — use 2-week window
                    week_start = datetime.datetime(yr, 1, 1) + datetime.timedelta(weeks=current_week-2)
                    week_end = week_start + datetime.timedelta(days=14)
                    t_start = week_start.strftime("%Y-%m-%dT00:00:00Z")
                    t_end = week_end.strftime("%Y-%m-%dT23:59:59Z")
                    
                    ndvi = await self._get_ndvi_for_period(lat, lng, t_start, t_end, token)
                    if ndvi is not None:
                        yearly_ndvi[str(yr)] = ndvi
                        ndvi_values.append(ndvi)
                except Exception:
                    continue

            # Filter outliers — likely cloud contamination (< 0.3 NDVI in summer = cloud)
            if len(ndvi_values) >= 3:
                ndvi_values_clean = [v for v in ndvi_values if v >= 0.3]
                yearly_ndvi_clean = {yr: val for yr, val in yearly_ndvi.items() if val >= 0.3}
                if len(ndvi_values_clean) >= 2:
                    ndvi_values = ndvi_values_clean
                    yearly_ndvi = yearly_ndvi_clean

            if len(ndvi_values) < 2:
                return {"available": False, "error": "Insufficient historical data"}

            # Calculate statistics
            mean_ndvi = round(float(np.mean(ndvi_values)), 3)
            std_ndvi = round(float(np.std(ndvi_values)), 3)
            cv = round(std_ndvi / mean_ndvi * 100, 1) if mean_ndvi > 0 else 0
            min_ndvi = round(float(np.min(ndvi_values)), 3)
            max_ndvi = round(float(np.max(ndvi_values)), 3)

            # Current year NDVI (from this year's data)
            current_ndvi = await self._get_ndvi_for_period(
                lat, lng,
                (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z"),
                now.strftime("%Y-%m-%dT23:59:59Z"),
                token
            )

            # Anomaly vs baseline
            anomaly_pct = None
            anomaly_label = None
            if current_ndvi and mean_ndvi:
                anomaly_pct = round((current_ndvi - mean_ndvi) / mean_ndvi * 100, 1)
                if cv >= 15:
                    # High CV means baseline is unreliable for anomaly detection
                    anomaly_label = "Variable history — comparison unreliable"
                elif anomaly_pct > 15:
                    anomaly_label = "Above normal"
                elif anomaly_pct > 5:
                    anomaly_label = "Slightly above normal"
                elif anomaly_pct < -15:
                    anomaly_label = "Below normal"
                elif anomaly_pct < -5:
                    anomaly_label = "Slightly below normal"
                else:
                    anomaly_label = "Normal"

            # CV interpretation
            if cv < 5:
                cv_label = "Very consistent — field history is highly stable"
                variability_type = "stable"
            elif cv < 10:
                cv_label = "Minor variation — mainly weather-driven, ground inspection recommended"
                variability_type = "temporary"
            elif cv < 15:
                cv_label = "Recurring variability — investigate persistent causes"
                variability_type = "mixed"
            else:
                cv_label = "Significant historical variability — low zones may reflect recurring soil or management constraints"
                variability_type = "chronic"

            return {
                "available": True,
                "years_analysed": len(ndvi_values),
                "yearly_ndvi": yearly_ndvi,
                "baseline_mean": mean_ndvi,
                "baseline_std": std_ndvi,
                "baseline_min": min_ndvi,
                "baseline_max": max_ndvi,
                "cv_percent": cv,
                "cv_label": cv_label,
                "variability_type": variability_type,
                "current_ndvi": current_ndvi,
                "anomaly_pct": anomaly_pct,
                "anomaly_label": anomaly_label,
                "week": current_week,
                "source": "Copernicus CDSE Sentinel-2 L2A"
            }

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False}
        return raw
