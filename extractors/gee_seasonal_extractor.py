"""
Seasonal Anomaly Engine for Cube Earth.
Compares current NDVI against multi-year baseline.
"""
import datetime
from extractors.base_extractor import BaseExtractor


class GEESeasonalExtractor(BaseExtractor):

    def __init__(self, project='ireland-mrv-prototype'):
        super().__init__("gee_seasonal")
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
                    print(f"GEE seasonal init failed: {e}")
        return self._initialized

    async def extract(self, lat, lng, current_ndvi=None):
        if not self._ensure_init():
            return {"available": False, "error": "GEE not initialized"}

        try:
            import ee

            point = ee.Geometry.Point([lng, lat])
            buffer = point.buffer(150)

            now = datetime.datetime.now(datetime.timezone.utc)
            month = now.month
            day   = now.day

            # Build same calendar window ±15 days for past 5 years
            start_md = f"{(month):02d}-{max(1, day-15):02d}"
            end_md   = f"{(month):02d}-{min(28, day+15):02d}"

            yearly_ndvi = {}
            for year in range(now.year-4, now.year):
                try:
                    start = f"{year}-{start_md}"
                    end   = f"{year}-{end_md}"
                    col = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                           .filterBounds(buffer)
                           .filterDate(start, end)
                           .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40)))
                    count = col.size().getInfo()
                    if count == 0:
                        continue
                    composite = col.median()
                    bands = composite.reduceRegion(
                        reducer=ee.Reducer.mean(),
                        geometry=buffer,
                        scale=10
                    ).getInfo()
                    b4 = (bands.get('B4') or 0) / 10000
                    b8 = (bands.get('B8') or 0) / 10000
                    ndvi = (b8 - b4) / (b8 + b4 + 1e-9)
                    yearly_ndvi[year] = round(ndvi, 4)
                except Exception:
                    continue

            return {
                "available":    True,
                "yearly_ndvi":  yearly_ndvi,
                "window":       f"{start_md} to {end_md}",
                "years_found":  len(yearly_ndvi),
            }

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw, current_ndvi=None):
        if not raw or not raw.get("available"):
            return {"available": False}

        yearly = raw.get("yearly_ndvi", {})
        if not yearly:
            return {"available": False, "error": "No historical data"}

        values = list(yearly.values())
        baseline_mean = round(sum(values) / len(values), 4)
        baseline_min  = round(min(values), 4)
        baseline_max  = round(max(values), 4)

        result = {
            "available":      True,
            "yearly_ndvi":    yearly,
            "baseline_mean":  baseline_mean,
            "baseline_min":   baseline_min,
            "baseline_max":   baseline_max,
            "years_used":     len(yearly),
            "window":         raw.get("window"),
        }

        if current_ndvi is not None:
            diff = current_ndvi - baseline_mean
            pct  = round((diff / (baseline_mean + 1e-9)) * 100, 1)

            if pct > 20:
                anomaly_label = "Above seasonal average"
                anomaly_level = "above"
            elif pct < -20:
                anomaly_label = "Below seasonal average"
                anomaly_level = "below"
            else:
                anomaly_label = "Near seasonal average"
                anomaly_level = "normal"

            result.update({
                "current_ndvi":   current_ndvi,
                "anomaly_pct":    pct,
                "anomaly_diff":   round(diff, 4),
                "anomaly_label":  anomaly_label,
                "anomaly_level":  anomaly_level,
            })

        return result

    def quality(self):
        return {
            "sensor":     "GEE Historical S2",
            "confidence": "moderate",
            "resolution": "10m",
        }
