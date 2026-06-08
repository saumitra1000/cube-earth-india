import httpx
import math
from extractors.base_extractor import BaseExtractor

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# Ireland average elevation fallback by rough lat/lng zones
def ireland_fallback_elevation(lat, lng):
    # Western mountains
    if lng < -9.0:
        return 150
    # Eastern lowlands
    if lng > -7.0:
        return 50
    # Central
    return 80

class NASADEMExtractor(BaseExtractor):

    def __init__(self):
        super().__init__("nasadem")

    async def extract(self, lat, lng, start_date=None, end_date=None):
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    ELEVATION_URL,
                    params={
                        "latitude": round(lat, 6),
                        "longitude": round(lng, 6),
                    },
                )
                r.raise_for_status()
                data = r.json()
                elev = data.get("elevation", [None])
                center = elev[0] if elev else None
                if center is not None:
                    return {"elevation": center, "source": "api"}
        except Exception:
            pass
        # Fallback
        return {
            "elevation": ireland_fallback_elevation(lat, lng),
            "source": "fallback"
        }

    def parse(self, raw):
        if not raw:
            return {"available": False, "source": "NASADEM"}
        center = raw.get("elevation")
        if center is None:
            return {"available": False, "source": "NASADEM"}
        slope = 1.5  # default gentle slope
        if center > 1500:
            terrain = "mountainous"
        elif center > 500:
            terrain = "hilly"
        elif center > 120:
            terrain = "rolling"
        elif slope > 3:
            terrain = "undulating"
        else:
            terrain = "flat"
        source = "Open-Meteo elevation" if raw.get("source") == "api" else "Estimated (Ireland average)"
        return {
            "available":   True,
            "elevation_m": round(center),
            "slope_deg":   slope,
            "terrain":     terrain,
            "source":      source,
        }

    def quality(self):
        return {
            "sensor":      "nasadem",
            "confidence":  "medium",
            "resolution":  "30m",
            "limitations": [
                "Slope estimated — elevation API unavailable",
            ],
        }
