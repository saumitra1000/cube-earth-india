"""
GEE Sentinel-1 SAR extractor for Cube Earth.
Replaces CDSE dependency with GEE SAR collection.
"""
import datetime
from extractors.base_extractor import BaseExtractor


class GEESARExtractor(BaseExtractor):

    def __init__(self, project='ireland-mrv-prototype'):
        super().__init__("gee_sar")
        self.project = project
        self._initialized = False

    def _ensure_init(self):
        if not self._initialized:
            try:
                import ee
                ee.Initialize(project=self.project)
                self._initialized = True
            except Exception:
                try:
                    import os, json, ee
                    creds_json = os.getenv("GEE_CREDENTIALS")
                    if creds_json:
                        creds_path = os.path.expanduser("~/.config/earthengine/credentials")
                        os.makedirs(os.path.dirname(creds_path), exist_ok=True)
                        with open(creds_path, "w") as f:
                            json.dump(json.loads(creds_json), f)
                    ee.Initialize(project=self.project)
                    self._initialized = True
                except Exception as e:
                    print(f"GEE SAR init failed: {e}")
        return self._initialized

    async def extract(self, lat, lng, days=14):
        if not self._ensure_init():
            return {"available": False, "error": "GEE not initialized"}

        try:
            import ee

            point  = ee.Geometry.Point([lng, lat])
            buffer = point.buffer(200)

            now   = datetime.datetime.now(datetime.timezone.utc)
            start = now - datetime.timedelta(days=days)

            s1 = (ee.ImageCollection('COPERNICUS/S1_GRD')
                  .filterBounds(buffer)
                  .filterDate(start.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'))
                  .filter(ee.Filter.eq('instrumentMode', 'IW'))
                  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
                  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
                  .sort('system:time_start', False))

            count = s1.size().getInfo()

            if count == 0 and days < 30:
                return await self.extract(lat, lng, days=30)

            if count == 0:
                return {"available": False, "error": "No S1 imagery available"}

            # Mean composite over period
            composite = s1.mean()
            bands = composite.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=buffer,
                scale=10
            ).getInfo()

            vv_db = bands.get('VV', 0) or 0
            vh_db = bands.get('VH', 0) or 0

            # Convert dB to linear for RVI
            vv_lin = 10 ** (vv_db / 10)
            vh_lin = 10 ** (vh_db / 10)
            rvi = round((4 * vh_lin) / (vv_lin + vh_lin + 1e-9), 4)

            # Latest acquisition date
            latest    = s1.first()
            date_ms   = latest.date().millis().getInfo()
            latest_dt = datetime.datetime.fromtimestamp(
                date_ms / 1000, tz=datetime.timezone.utc
            )
            latest_date = latest_dt.strftime('%Y-%m-%d')
            age_days    = (now - latest_dt).days

            # RVI interpretation
            def interpret_rvi(v):
                if v > 0.8:  return "High canopy structure"
                if v > 0.6:  return "Moderate-high canopy structure"
                if v > 0.5:  return "Moderate canopy structure"
                if v > 0.25: return "Low canopy structure"
                return "Residual structural vegetation"

            return {
                "available":          True,
                "vv_db":              round(vv_db, 3),
                "vh_db":              round(vh_db, 3),
                "rvi":                rvi,
                "rvi_interpretation": interpret_rvi(rvi),
                "acquisitions":       count,
                "latest_date":        latest_date,
                "age_days":           age_days,
                "composite_days":     days,
                "source":             f"GEE Sentinel-1 IW ({count} acquisitions)",
                "calibration":        "Sigma0 terrain corrected",
                "speckle_filter":     f"{count}-pass temporal average",
            }

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False, "source": "GEE SAR"}
        return raw

    def quality(self):
        return {
            "sensor":     "GEE Sentinel-1",
            "confidence": "high",
            "resolution": "10m",
        }
