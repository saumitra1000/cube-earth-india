import httpx
from extractors.base_extractor import BaseExtractor
from config.settings import settings

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"


class Sentinel1Extractor(BaseExtractor):

    def __init__(self):
        super().__init__("sentinel1")
        self.collection = settings.SENTINEL1_COLLECTION

    def _headers(self):
        return {
            "Authorization": f"Bearer {settings.NASA_TOKEN}",
            "Accept": "application/json",
        }

    async def extract(self, lat, lng, start_date, end_date):
        bbox = (
            f"{lng-0.15},{lat-0.15},"
            f"{lng+0.15},{lat+0.15}"
        )
        params = {
            "collection_concept_id": self.collection,
            "temporal":   f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
            "bounding_box": bbox,
            "page_size":  5,
            "sort_key":   "-start_date",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                CMR_URL,
                headers=self._headers(),
                params=params,
            )
            r.raise_for_status()
            data = r.json()
        return data.get("feed", {}).get("entry", [])

    def parse(self, raw):
        if not raw:
            return {
                "available": False,
                "source":    "Sentinel-1 GRD",
            }
        granules = []
        for entry in raw:
            links = entry.get("links", [])
            data_links = [
                l["href"] for l in links
                if l["href"].startswith("https://")
                and not l["href"].endswith(".md5")
                and not "metadata" in l["href"].lower()
            ]
            granules.append({
                "id":         entry.get("id"),
                "title":      entry.get("title"),
                "time_start": entry.get("time_start"),
                "time_end":   entry.get("time_end"),
                "links":      data_links[:3],
            })
        return {
            "available":     True,
            "granule_count": len(granules),
            "latest":        granules[0] if granules else None,
            "granules":      granules,
            "source":        "Sentinel-1 GRD IW (20m)",
            "collection":    self.collection,
            "note":          "C-band SAR - VV/VH polarisation",
        }

    def quality(self):
        return {
            "sensor":      "sentinel1",
            "confidence":  "moderate",
            "resolution":  "20m",
            "limitations": [
                "C-band only - cannot distinguish management intensities alone",
                "L-band (PALSAR-2) not available for Ireland",
                "Use paired with Sentinel-2 for best results",
            ],
        }
