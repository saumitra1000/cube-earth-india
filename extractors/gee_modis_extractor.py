"""
GEE MODIS extractor for Cube Earth.
Provides daily LST and vegetation indices history.
"""
import datetime
from extractors.base_extractor import BaseExtractor


class GEEMODISExtractor(BaseExtractor):

    def __init__(self, project='ireland-mrv-prototype'):
        super().__init__("gee_modis")
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
                    print(f"GEE MODIS init failed: {e}")
        return self._initialized

    async def extract(self, lat, lng, days=30):
        if not self._ensure_init():
            return {"available": False, "error": "GEE not initialized"}

        try:
            import ee
            point  = ee.Geometry.Point([lng, lat])
            buffer = point.buffer(1000)  # 1km for MODIS resolution

            now   = datetime.datetime.now(datetime.timezone.utc)
            start = now - datetime.timedelta(days=days)

            # MODIS Terra LST daily
            modis_lst = (ee.ImageCollection('MODIS/061/MOD11A1')
                         .filterBounds(buffer)
                         .filterDate(start.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'))
                         .sort('system:time_start', False))

            # MODIS NDVI 16-day — use 60 day window to ensure coverage
            ndvi_start = now - datetime.timedelta(days=60)
            modis_ndvi = (ee.ImageCollection('MODIS/061/MOD13A1')
                          .filterBounds(buffer)
                          .filterDate(ndvi_start.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'))
                          .sort('system:time_start', False))

            lst_count  = modis_lst.size().getInfo()
            ndvi_count = modis_ndvi.size().getInfo()

            result = {"available": True, "days": days}

            # Get latest LST
            if lst_count > 0:
                latest_lst = modis_lst.first()
                date_ms    = latest_lst.date().millis().getInfo()
                latest_dt  = datetime.datetime.fromtimestamp(
                    date_ms/1000, tz=datetime.timezone.utc)
                bands = latest_lst.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=buffer,
                    scale=1000
                ).getInfo()
                lst_raw = bands.get('LST_Day_1km') or 0
                lst_k   = lst_raw * 0.02
                lst_c   = round(lst_k - 273.15, 2)

                result.update({
                    "lst_celsius":   lst_c,
                    "lst_date":      latest_dt.strftime('%Y-%m-%d'),
                    "lst_age_days":  (now - latest_dt).days,
                    "lst_count":     lst_count,
                })

            # Get LST time series (last 30 days)
            def get_lst(img):
                val = img.select('LST_Day_1km').multiply(0.02).subtract(273.15)
                mean = val.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=buffer,
                    scale=1000
                )
                return img.set('lst_c', mean.get('LST_Day_1km'))

            lst_series_col = modis_lst.limit(7).map(get_lst)
            lst_vals  = lst_series_col.aggregate_array('lst_c').getInfo()
            lst_dates = lst_series_col.aggregate_array('system:time_start').getInfo()

            lst_series = []
            for d, v in zip(lst_dates, lst_vals):
                if v is not None:
                    date_str = datetime.datetime.fromtimestamp(
                        d/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
                    lst_series.append({"date": date_str, "lst_c": round(v, 1)})

            lst_series.sort(key=lambda x: x["date"])
            result["lst_series"] = lst_series

            # Get latest MODIS NDVI
            if ndvi_count > 0:
                latest_ndvi = modis_ndvi.first()
                date_ms     = latest_ndvi.date().millis().getInfo()
                ndvi_dt     = datetime.datetime.fromtimestamp(
                    date_ms/1000, tz=datetime.timezone.utc)
                bands = latest_ndvi.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=buffer,
                    scale=500
                ).getInfo()
                ndvi_raw = bands.get('NDVI') or 0
                ndvi_val = round(ndvi_raw * 0.0001, 4)

                result.update({
                    "modis_ndvi":      ndvi_val,
                    "modis_ndvi_date": ndvi_dt.strftime('%Y-%m-%d'),
                    "modis_ndvi_age":  (now - ndvi_dt).days,
                })

            result["source"] = "GEE MODIS Terra 1km daily"
            return result

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False, "source": "MODIS"}
        return raw

    def quality(self):
        return {
            "sensor":     "MODIS Terra",
            "confidence": "moderate",
            "resolution": "1km",
        }
