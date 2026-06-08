"""
GEE Thermal extractor for Cube Earth.
Uses Landsat 8/9 ST_B10 as ECOSTRESS fallback.
"""
import datetime
from extractors.base_extractor import BaseExtractor


class GEEThermalExtractor(BaseExtractor):

    def __init__(self, project='ireland-mrv-prototype'):
        super().__init__("gee_thermal")
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
                    print(f"GEE thermal init failed: {e}")
        return self._initialized

    async def extract(self, lat, lng, days=60):
        if not self._ensure_init():
            return {"available": False, "error": "GEE not initialized"}

        try:
            import ee

            point  = ee.Geometry.Point([lng, lat])
            buffer = point.buffer(500)

            now   = datetime.datetime.now(datetime.timezone.utc)
            start = now - datetime.timedelta(days=days)

            l89 = (ee.ImageCollection('LANDSAT/LC09/C02/T1_L2')
                   .merge(ee.ImageCollection('LANDSAT/LC08/C02/T1_L2'))
                   .filterBounds(buffer)
                   .filterDate(start.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'))
                   .filter(ee.Filter.lt('CLOUD_COVER', 30))
                   .sort('system:time_start', False))

            count = l89.size().getInfo()

            if count == 0 and days < 90:
                return await self.extract(lat, lng, days=90)

            if count == 0:
                return {"available": False, "error": "No Landsat thermal data"}

            composite = l89.median()

            # Apply LST scale factor
            lst_img = composite.select('ST_B10').multiply(0.00341802).add(149.0)

            val = lst_img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=buffer,
                scale=30
            ).getInfo()

            lst_k = val.get('ST_B10') or 0
            if lst_k == 0:
                return {"available": False, "error": "No valid LST pixel"}

            lst_c = round(lst_k - 273.15, 2)

            # Latest date
            latest  = l89.first()
            date_ms = latest.date().millis().getInfo()
            latest_dt = datetime.datetime.fromtimestamp(
                date_ms / 1000, tz=datetime.timezone.utc
            )
            age_days = (now - latest_dt).days

            # Thermal stress interpretation
            def interpret_lst(t):
                if t > 35:  return "Heat stress risk"
                if t > 28:  return "Warm surface temperatures"
                if t > 15:  return "No thermal stress detected"
                if t > 5:   return "Moderate surface temperatures"
                return "Cold surface — growth suppressed"

            return {
                "available":       True,
                "lst_celsius":     lst_c,
                "lst_kelvin":      round(lst_k, 2),
                "interpretation":  interpret_lst(lst_c),
                "age_days":        age_days,
                "latest_date":     latest_dt.strftime('%Y-%m-%d'),
                "acquisitions":    count,
                "source":          f"GEE Landsat 8/9 Thermal ({count} images)",
                "resolution":      "30m",
                "note":            "Landsat thermal fallback — ECOSTRESS unavailable",
            }

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False, "source": "GEE Thermal"}
        return raw

    def quality(self):
        return {
            "sensor":     "GEE Landsat Thermal",
            "confidence": "moderate",
            "resolution": "30m",
        }
