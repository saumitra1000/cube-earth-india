"""
Google Earth Engine extractor for Cube Earth.
Provides NDVI time series and trend calculation.
"""
import datetime
from extractors.base_extractor import BaseExtractor


def init_gee(project='ireland-mrv-prototype'):
    import os, json
    try:
        import ee
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "earthengine-api==1.7.28", "--quiet"])
        import ee
    try:
        # Try env var first (Render production)
        creds_json = os.getenv("GEE_CREDENTIALS")
        if creds_json:
            import tempfile
            creds = json.loads(creds_json)
            creds_path = os.path.expanduser("~/.config/earthengine/credentials")
            os.makedirs(os.path.dirname(creds_path), exist_ok=True)
            with open(creds_path, "w") as f:
                json.dump(creds, f)
        ee.Initialize(project=project)
        return True
    except Exception as e:
        print(f"GEE init failed: {e}")
        return False


class GEEExtractor(BaseExtractor):

    def __init__(self, project='ireland-mrv-prototype'):
        super().__init__("gee")
        self.project = project
        self._initialized = False

    def _ensure_init(self):
        if not self._initialized:
            self._initialized = init_gee(self.project)
        return self._initialized

    async def extract(self, lat, lng, start_date=None, end_date=None):
        if not self._ensure_init():
            return {"available": False, "error": "GEE not initialized"}

        try:
            import ee
            point = ee.Geometry.Point([lng, lat])

            # Get last 90 days of S2
            end = datetime.datetime.utcnow()
            start = end - datetime.timedelta(days=90)

            s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                  .filterBounds(point)
                  .filterDate(start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30))
                  .sort('system:time_start', False)
                  .limit(10))

            count = s2.size().getInfo()
            if count == 0:
                return {"available": False, "error": "No GEE images found"}

            def get_ndvi(img):
                ndvi = img.normalizedDifference(['B8', 'B4'])
                val = ndvi.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=point.buffer(100),
                    scale=10
                )
                return img.set('ndvi', val.get('nd'))

            results = s2.map(get_ndvi)
            ndvi_list = results.aggregate_array('ndvi').getInfo()
            date_list = results.aggregate_array('system:time_start').getInfo()

            # Build time series
            series = []
            for d, n in zip(date_list, ndvi_list):
                if n is not None:
                    date_str = datetime.datetime.fromtimestamp(
                        d/1000, tz=datetime.timezone.utc
                    ).strftime('%Y-%m-%d')
                    series.append({"date": date_str, "ndvi": round(n, 4)})

            # Sort by date ascending
            series.sort(key=lambda x: x["date"])

            # Deduplicate by date — keep highest NDVI per date
            seen = {}
            for s in series:
                if s["date"] not in seen or s["ndvi"] > seen[s["date"]]["ndvi"]:
                    seen[s["date"]] = s
            series = list(seen.values())
            series.sort(key=lambda x: x["date"])

            # Filter cloud/snow artifacts — single low outliers between high values
            filtered = []
            for i, s in enumerate(series):
                if i == 0 or i == len(series) - 1:
                    filtered.append(s)
                    continue
                prev_ndvi = series[i-1]["ndvi"]
                next_ndvi = series[i+1]["ndvi"]
                # If current is much lower than both neighbours — likely artifact
                if s["ndvi"] < prev_ndvi - 0.25 and s["ndvi"] < next_ndvi - 0.25:
                    continue  # Skip artifact
                filtered.append(s)
            series = filtered

            return {
                "available": True,
                "series": series,
                "count": len(series),
                "source": "GEE Sentinel-2 SR Harmonized",
            }

        except Exception as e:
            return {"available": False, "error": str(e)}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False, "source": "GEE"}

        series = raw.get("series", [])
        if len(series) < 2:
            return {
                "available": True,
                "series": series,
                "trend": "insufficient_data",
                "trend_label": "Insufficient data for trend",
                "source": raw.get("source"),
            "events": detect_events(series),
            }

        # Latest and previous NDVI
        latest = series[-1]
        previous = series[-2] if len(series) >= 2 else None
        oldest = series[0]

        import datetime

        def trend_for_diff(diff, ndvi_base=None):
            if diff > 0.15: return "strong_increase"
            if diff > 0.08: return "increasing"
            if diff < -0.20: return "strong_decline"
            if diff < -0.10: return "declining"
            return "stable"

        # Short term trend (last 2 observations)
        short_diff = None
        short_trend = "stable"
        if previous:
            short_diff = round(latest["ndvi"] - previous["ndvi"], 4)
            short_trend = trend_for_diff(short_diff)

        # Long term trend (first vs last)
        long_diff = round(latest["ndvi"] - oldest["ndvi"], 4)
        long_trend = trend_for_diff(long_diff)

        # 7-day trend
        now = datetime.datetime.utcnow()
        cutoff_7d = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        cutoff_30d = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        recent_7d = [s for s in series if s["date"] >= cutoff_7d]
        recent_30d = [s for s in series if s["date"] >= cutoff_30d]

        trend_7d = "insufficient_data"
        diff_7d = None
        if len(recent_7d) >= 2:
            diff_7d = round(recent_7d[-1]["ndvi"] - recent_7d[0]["ndvi"], 4)
            trend_7d = trend_for_diff(diff_7d)
        elif len(recent_7d) == 1 and previous:
            diff_7d = round(recent_7d[0]["ndvi"] - previous["ndvi"], 4)
            trend_7d = trend_for_diff(diff_7d)

        trend_30d = "insufficient_data"
        diff_30d = None
        if len(recent_30d) >= 2:
            diff_30d = round(recent_30d[-1]["ndvi"] - recent_30d[0]["ndvi"], 4)
            trend_30d = trend_for_diff(diff_30d)

        # Trend arrows
        arrows = {
            "increasing": "↑",
            "strong_increase": "↑↑",
            "declining": "↓",
            "strong_decline": "↓↓",
            "stable": "→",
        }

        trend_labels = {
            "increasing": "Increasing vegetation",
            "strong_increase": "Strong vegetation growth",
            "declining": "Declining vegetation",
            "strong_decline": "Strong vegetation decline",
            "stable": "Stable vegetation",
        }

        return {
            "available": True,
            "series": series,
            "latest": latest,
            "previous": previous,
            "oldest": oldest,
            "short_trend": short_trend,
            "long_trend": long_trend,
            "short_diff": short_diff,
            "long_diff": long_diff,
            "trend_arrow": arrows.get(long_trend, "→"),
            "trend_label": trend_labels.get(long_trend, "Stable"),
            "count": len(series),
            "trend_7d": trend_7d,
            "diff_7d": diff_7d,
            "trend_30d": trend_30d,
            "diff_30d": diff_30d,
            "source": raw.get("source"),
            "events": detect_events(series),
        }

    def quality(self):
        return {
            "sensor": "GEE Sentinel-2",
            "confidence": "high",
            "resolution": "10m",
            "limitations": ["Requires GEE authentication"],
        }


def detect_events(series):
    """
    Detect significant field events from NDVI time series.
    Validates events against surrounding observations to avoid cloud artifacts.
    """
    if not series or len(series) < 3:
        return []

    events = []

    for i in range(1, len(series)):
        prev = series[i-1]
        curr = series[i]
        diff = curr["ndvi"] - prev["ndvi"]

        # Validate: check if recovery follows within 1-2 observations
        # If NDVI recovers quickly, the drop was likely cloud/snow artifact
        next_ndvi = series[i+1]["ndvi"] if i+1 < len(series) else None
        next2_ndvi = series[i+2]["ndvi"] if i+2 < len(series) else None

        is_artifact = False
        if next_ndvi and curr["ndvi"] < 0.30:
            # If next observation is much higher, current is likely artifact
            if next_ndvi > curr["ndvi"] + 0.20:
                is_artifact = True
            if next2_ndvi and next2_ndvi > curr["ndvi"] + 0.20:
                is_artifact = True

        # Harvest / cutting event — sudden large drop, must be sustained
        if diff < -0.20 and not is_artifact:
            # Require low NDVI to persist (not just one bad observation)
            sustained = curr["ndvi"] < 0.35
            events.append({
                "type":        "harvest_or_cut",
                "date":        curr["date"],
                "ndvi_before": prev["ndvi"],
                "ndvi_after":  curr["ndvi"],
                "change":      round(diff, 4),
                "label":       "Harvest or cutting event detected",
                "confidence":  "high" if (diff < -0.30 and sustained) else "moderate",
            })

        # Vegetation loss — moderate drop, sustained
        elif diff < -0.10 and not is_artifact and curr["ndvi"] < 0.40:
            events.append({
                "type":        "vegetation_loss",
                "date":        curr["date"],
                "ndvi_before": prev["ndvi"],
                "ndvi_after":  curr["ndvi"],
                "change":      round(diff, 4),
                "label":       "Significant vegetation loss",
                "confidence":  "moderate",
            })

        # Reseeding / recovery — rise from sustained low base
        elif diff > 0.20 and prev["ndvi"] < 0.25 and not is_artifact:
            # Only flag if previous was genuinely low (not artifact)
            prev_prev = series[i-2]["ndvi"] if i >= 2 else None
            if prev_prev is None or prev_prev < 0.35:
                events.append({
                    "type":        "recovery_or_reseeding",
                    "date":        curr["date"],
                    "ndvi_before": prev["ndvi"],
                    "ndvi_after":  curr["ndvi"],
                    "change":      round(diff, 4),
                    "label":       "Vegetation recovery or reseeding",
                    "confidence":  "moderate",
                })

        # Bare soil — sustained very low NDVI (2+ consecutive)
        elif curr["ndvi"] < 0.15 and prev["ndvi"] < 0.20:
            if not any(e["type"] == "bare_soil" and
                      e["date"] >= series[max(0,i-2)]["date"]
                      for e in events):
                events.append({
                    "type":   "bare_soil",
                    "date":   curr["date"],
                    "ndvi":   curr["ndvi"],
                    "label":  "Post-harvest low canopy state",
                    "confidence": "high",
                })

    return events
