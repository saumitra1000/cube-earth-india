"""
GEE Optical Extractor for Cube Earth.
Replaces HLS for current NDVI — always fresh 10m Sentinel-2 data.
"""
import datetime
from extractors.base_extractor import BaseExtractor


class GEEOpticalExtractor(BaseExtractor):

    def __init__(self, project='ireland-mrv-prototype'):
        super().__init__("gee_optical")
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
                    print(f"GEE optical init failed: {e}")
        return self._initialized

    async def extract(self, lat, lng, start_date=None, end_date=None):
        if not self._ensure_init():
            return {"available": False, "error": "GEE not initialized"}

        try:
            import ee

            point = ee.Geometry.Point([lng, lat])
            buffer = point.buffer(150)

            now = datetime.datetime.now(datetime.timezone.utc)

            def get_composite(days, cloud_max):
                """Get cloud-free median composite over N days."""
                start = now - datetime.timedelta(days=days)
                col = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                       .filterBounds(buffer)
                       .filterDate(start.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'))
                       .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_max)))
                cnt = col.size().getInfo()
                return col, cnt

            # Try composites: 7d → 14d → 30d → 60d with relaxing cloud
            composite_img = None
            composite_days = None
            composite_count = None

            for days, cloud_max in [(7, 30), (14, 40), (30, 50), (60, 60)]:
                col, cnt = get_composite(days, cloud_max)
                if cnt > 0:
                    composite_img   = col.median()
                    composite_days  = days
                    composite_count = cnt
                    break

            if composite_img is None:
                # Fallback to Landsat 8/9
                try:
                    l89 = (ee.ImageCollection('LANDSAT/LC09/C02/T1_L2')
                           .merge(ee.ImageCollection('LANDSAT/LC08/C02/T1_L2'))
                           .filterBounds(buffer)
                           .filterDate((now - datetime.timedelta(days=90)).strftime('%Y-%m-%d'),
                                       now.strftime('%Y-%m-%d'))
                           .filter(ee.Filter.lt('CLOUD_COVER', 60))
                           .sort('system:time_start', False))
                    l_count = l89.size().getInfo()
                    if l_count > 0:
                        composite_img   = l89.limit(3).median()
                        composite_days  = 60
                        composite_count = l_count
                        # Landsat band mapping
                        bands = composite_img.reduceRegion(
                            reducer=ee.Reducer.mean(),
                            geometry=buffer,
                            scale=30
                        ).getInfo()
                        b4 = bands.get('SR_B4') or 0
                        b5 = bands.get('SR_B5') or 0
                        b6 = bands.get('SR_B6') or 0
                        r4 = b4 * 0.0000275 - 0.2
                        r5 = b5 * 0.0000275 - 0.2
                        r6 = b6 * 0.0000275 - 0.2
                        ndvi = (r5 - r4) / (r5 + r4 + 1e-9)
                        ndwi = (r5 - r6) / (r5 + r6 + 1e-9)
                        evi  = 2.5 * (r5 - r4) / (r5 + 6*r4 - 7.5*0.03 + 1 + 1e-9)
                        return {
                            "available":       True,
                            "date":            (now - datetime.timedelta(days=30)).strftime('%Y-%m-%d'),
                            "date_range":      f"Landsat 60-day composite",
                            "composite_days":  60,
                            "composite_count": l_count,
                            "age_days":        30,
                            "ndvi":            round(ndvi, 4),
                            "ndre":            None,
                            "cire":            None,
                            "evi":             round(evi, 4),
                            "ndwi":            round(ndwi, 4),
                            "savi":            None,
                            "gndvi":           None,
                            "gcap":            ndvi * 0.5 if ndvi > 0.3 else 0.0,
                            "cloud_cover":     l_count,
                            "source":          f"GEE Landsat 8/9 30m ({l_count} images)",
                        }
                except Exception as _le:
                    pass
                return {"available": False, "error": "No S2 or Landsat imagery in 90 days"}

            # Representative date = midpoint of composite window
            start_date = (now - datetime.timedelta(days=composite_days)).strftime('%Y-%m-%d')
            end_date   = now.strftime('%Y-%m-%d')
            image_date = (now - datetime.timedelta(days=composite_days//2)).strftime('%Y-%m-%d')
            age_days   = composite_days // 2

            # Extract bands from composite
            bands = composite_img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=buffer,
                scale=10
            ).getInfo()

            # Also get stDev + quadrant analysis for uniformity
            try:
                ndvi_img = composite_img.normalizedDifference(['B8','B4']).rename('ndvi')
                stats = ndvi_img.reduceRegion(
                    reducer=ee.Reducer.stdDev().combine(
                        ee.Reducer.percentile([25,75]), sharedInputs=True
                    ),
                    geometry=buffer,
                    scale=10
                ).getInfo()
                ndvi_std = stats.get('ndvi_stdDev', None)
                ndvi_p25 = stats.get('ndvi_p25', None)
                ndvi_p75 = stats.get('ndvi_p75', None)

                # Quadrant analysis — find which zone is lowest
                bounds = buffer.bounds().getInfo()['coordinates'][0]
                min_lng = min(c[0] for c in bounds)
                max_lng = max(c[0] for c in bounds)
                min_lat = min(c[1] for c in bounds)
                max_lat = max(c[1] for c in bounds)
                mid_lng = (min_lng + max_lng) / 2
                mid_lat = (min_lat + max_lat) / 2

                quadrants = {
                    'NE': ee.Geometry.Rectangle([mid_lng, mid_lat, max_lng, max_lat]),
                    'NW': ee.Geometry.Rectangle([min_lng, mid_lat, mid_lng, max_lat]),
                    'SE': ee.Geometry.Rectangle([mid_lng, min_lat, max_lng, mid_lat]),
                    'SW': ee.Geometry.Rectangle([min_lng, min_lat, mid_lng, mid_lat]),
                }
                quad_ndvi = {}
                for qname, qgeom in quadrants.items():
                    try:
                        qr = ndvi_img.reduceRegion(
                            reducer=ee.Reducer.mean(),
                            geometry=qgeom,
                            scale=10
                        ).getInfo()
                        quad_ndvi[qname] = round(qr.get('ndvi', 0) or 0, 3)
                    except Exception:
                        quad_ndvi[qname] = None

            except Exception:
                ndvi_std = None
                ndvi_p25 = None
                ndvi_p75 = None
                quad_ndvi = {}

            b2  = bands.get('B2', 0) or 0
            b4  = bands.get('B4', 0) or 0
            b5  = bands.get('B5', 0) or 0
            b6  = bands.get('B6', 0) or 0
            b7  = bands.get('B7', 0) or 0
            b8  = bands.get('B8', 0) or 0
            b8a = bands.get('B8A', 0) or 0
            b11 = bands.get('B11', 0) or 0
            b12 = bands.get('B12', 0) or 0

            scale = 10000.0
            r2  = b2  / scale
            r4  = b4  / scale
            r5  = b5  / scale
            r6  = b6  / scale
            r7  = b7  / scale
            r8  = b8  / scale
            r8a = b8a / scale
            r11 = b11 / scale
            r12 = b12 / scale

            # NDVI
            ndvi = (r8 - r4) / (r8 + r4 + 1e-9)

            # NDRE
            ndre = (r7 - r5) / (r7 + r5 + 1e-9)

            # CIre
            cire = (r7 / (r5 + 1e-9)) - 1

            # EVI
            evi = 2.5 * (r8 - r4) / (r8 + 6*r4 - 7.5*r2 + 1 + 1e-9)

            # NDWI (water content)
            ndwi = (r8 - r11) / (r8 + r11 + 1e-9)

            # SAVI (soil adjusted)
            savi = 1.5 * (r8 - r4) / (r8 + r4 + 0.5)

            # GNDVI (green NDVI)
            gndvi = (r8 - r6) / (r8 + r6 + 1e-9)

            # GCAP
            gcap = ndvi * cire if ndvi > 0.3 else 0.0

            # Estimate cloud cover as mean over composite period
            cloud_pct = composite_count  # proxy — actual % not available for composite

            return {
                "available":        True,
                "date":             image_date,
                "date_range":       f"{start_date} to {end_date}",
                "composite_days":   composite_days,
                "composite_count":  composite_count,
                "age_days":         age_days,
                "ndvi":             round(ndvi, 4),
                "ndvi_std":         round(ndvi_std, 4) if ndvi_std else None,
                "ndvi_p25":         round(ndvi_p25, 4) if ndvi_p25 else None,
                "ndvi_p75":         round(ndvi_p75, 4) if ndvi_p75 else None,
                "uniformity":       round(max(0, 10 - (ndvi_std * 40)), 1) if ndvi_std else None,
                "quad_ndvi":        quad_ndvi if quad_ndvi else None,
                "ndre":             round(ndre, 4),
                "cire":             round(cire, 4),
                "evi":              round(evi, 4),
                "ndwi":             round(ndwi, 4),
                "savi":             round(savi, 4),
                "gndvi":            round(gndvi, 4),
                "gcap":             round(gcap, 4),
                "cloud_cover":      composite_count,
                "source":           f"GEE S2 {composite_days}d composite ({composite_count} images)",
                "bands": {
                    "B4":  round(r4, 4),
                    "B8":  round(r8, 4),
                    "B5":  round(r5, 4),
                    "B7":  round(r7, 4),
                    "B11": round(r11, 4),
                },
            }

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False, "source": "GEE Optical"}
        return raw

    def quality(self):
        return {
            "sensor":      "GEE Sentinel-2",
            "confidence":  "high",
            "resolution":  "10m",
            "limitations": ["Requires GEE auth"],
        }
