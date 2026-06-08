import httpx
import h5py
import numpy as np
import io
from extractors.base_extractor import BaseExtractor
from config.settings import settings

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"


class SMAPExtractor(BaseExtractor):

    def __init__(self):
        super().__init__("smap")
        self.collection = settings.SMAP_COLLECTION

    def _headers(self):
        return {
            "Authorization": f"Bearer {settings.NASA_TOKEN}",
            "Accept": "application/json",
        }

    async def find_granule(self, lat, lng, date_str):
        # Search with fallback dates
        import datetime
        base = datetime.date.fromisoformat(date_str)
        dates = [base + datetime.timedelta(days=i) for i in [0, -1, 1, -2, 2, -3, 3]]

        async with httpx.AsyncClient(timeout=30) as client:
            for d in dates:
                r = await client.get(
                    CMR_URL,
                    headers=self._headers(),
                    params={
                        "collection_concept_id": self.collection,
                        "temporal":   f"{d}T00:00:00Z,{d}T23:59:59Z",
                        "bounding_box": f"{lng-5},{lat-5},{lng+5},{lat+5}",
                        "page_size":  8,
                        "sort_key":   "-start_date",
                    },
                )
                entries = r.json().get("feed", {}).get("entry", [])
                for entry in entries:
                    links = entry.get("links", [])
                    for link in links:
                        href = link.get("href", "")
                        if href.startswith("https://") and href.endswith(".h5"):
                            return {
                                "url":        href,
                                "time_start": entry.get("time_start"),
                                "date":       str(d),
                            }
        return None

    async def extract_pixel(self, granule_url, lat, lng):
        headers = {
            "Authorization": f"Bearer {settings.NASA_TOKEN}",
        }
        async with httpx.AsyncClient(
            timeout=120,
            follow_redirects=True,
        ) as client:
            r = await client.get(granule_url, headers=headers)
            if not r.is_success:
                return None
            data = r.content

        with h5py.File(io.BytesIO(data), "r") as f:
            # SMAP L4 grid: 406 rows x 964 cols (EASE-2 9km)
            sm_surf = f["Geophysical_Data"]["sm_surface"][:]
            sm_root = f["Geophysical_Data"]["sm_rootzone"][:]

            # EASE-2 grid coordinates
            row = int((90 - lat) / 180 * 406)
            col = int((lng + 180) / 360 * 964)
            row = max(0, min(405, row))
            col = max(0, min(963, col))

            surf = float(sm_surf[row, col])
            root = float(sm_root[row, col])

            # Fill value check
            if surf < 0 or surf > 1:
                surf = None
            if root < 0 or root > 1:
                root = None

            return {
                "sm_surface":  round(surf, 4) if surf else None,
                "sm_rootzone": round(root, 4) if root else None,
                "row":         row,
                "col":         col,
            }

    async def extract(self, lat, lng, start_date, end_date):
        import datetime
        today = datetime.date.today()
        start = datetime.date.fromisoformat(start_date)
        end   = datetime.date.fromisoformat(end_date)
        # Use most recent date within range
        if today <= end:
            date_str = str(min(today, end))
        else:
            date_str = str(end)

        granule = await self.find_granule(lat, lng, date_str)
        if not granule:
            return None

        pixel = await self.extract_pixel(granule["url"], lat, lng)
        if not pixel:
            return None

        return {
            "granule_url":  granule["url"],
            "granule_time": granule["time_start"],
            "granule_date": granule["date"],
            **pixel,
        }

    def parse(self, raw):
        if not raw:
            return {
                "available": False,
                "source":    "SMAP L4",
            }
        return {
            "available":       True,
            "sm_surface_m3":   raw.get("sm_surface"),
            "sm_rootzone_m3":  raw.get("sm_rootzone"),
            "sm_surface_pct":  round(raw["sm_surface"] * 100, 2) if raw.get("sm_surface") else None,
            "sm_rootzone_pct": round(raw["sm_rootzone"] * 100, 2) if raw.get("sm_rootzone") else None,
            "granule_date":    raw.get("granule_date"),
            "granule_time":    raw.get("granule_time"),
            "resolution":      "9km EASE-2",
            "source":          "SMAP L4 SPL4SMGP V008",
            "collection":      self.collection,
        }

    def quality(self):
        return {
            "sensor":      "smap",
            "confidence":  "high",
            "resolution":  "9km",
            "limitations": [
                "9km grid - not parcel precise",
                "3-hour latency for near-real-time",
                "Dense vegetation reduces accuracy",
            ],
        }
