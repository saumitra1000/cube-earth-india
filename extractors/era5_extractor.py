import httpx
from extractors.base_extractor import BaseExtractor


OPEN_METEO_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"


class ERA5Extractor(BaseExtractor):

    def __init__(self):
        super().__init__("era5")

    async def extract(self, lat, lng, start_date, end_date):
        import datetime
        today = datetime.date.today()
        # If end_date is in future, cap at today
        end = min(datetime.date.fromisoformat(end_date), today)
        # If start is after today, use last 30 days
        start = datetime.date.fromisoformat(start_date)
        if start > today:
            start = today - datetime.timedelta(days=30)
        start_date = str(start)
        end_date   = str(end)
        params = {
            "latitude":   lat,
            "longitude":  lng,
            "start_date": start_date,
            "end_date":   end_date,
            "daily": ",".join([
                "soil_moisture_0_to_7cm_mean",
                "soil_moisture_7_to_28cm_mean",
                "soil_moisture_28_to_100cm_mean",
                "precipitation_sum",
                "et0_fao_evapotranspiration",
            ]),
            "timezone": "UTC",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(OPEN_METEO_URL, params=params)
            r.raise_for_status()
            return r.json()

    def parse(self, raw):
        if not raw or "daily" not in raw:
            return {"available": False, "source": "ERA5-Land"}
        d = raw["daily"]
        s0 = [v for v in (d.get("soil_moisture_0_to_7cm_mean") or []) if v is not None]
        s1 = [v for v in (d.get("soil_moisture_7_to_28cm_mean") or []) if v is not None]
        s2 = [v for v in (d.get("soil_moisture_28_to_100cm_mean") or []) if v is not None]
        pr = [v for v in (d.get("precipitation_sum") or []) if v is not None]
        et = [v for v in (d.get("et0_fao_evapotranspiration") or []) if v is not None]
        def avg(lst):
            return round(sum(lst) / len(lst), 4) if lst else None
        return {
            "available":        True,
            "surface_mean":     avg(s0),
            "rootzone_mean":    avg(s1 + s2),
            "rainfall_mm":      round(sum(pr), 1) if pr else None,
            "et0_mm":           round(sum(et), 1) if et else None,
            "obs_count":        len(s0),
            "source":           "ERA5-Land via Open-Meteo",
            "resolution_km":    9,
        }

    def quality(self):
        return {
            "sensor":      "era5",
            "confidence":  "high",
            "resolution":  "9km",
            "limitations": [
                "Model reanalysis - not direct observation",
                "Seasonal trend only - not real-time",
            ],
        }
