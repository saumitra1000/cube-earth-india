import httpx
from extractors.base_extractor import BaseExtractor
from config.settings import settings

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"


class HLSExtractor(BaseExtractor):

    def __init__(self):
        super().__init__("hls_s30")
        self.collection = settings.HLS_S30_COLLECTION

    def _headers(self):
        return {
            "Authorization": f"Bearer {settings.NASA_TOKEN}",
            "Accept": "application/json",
        }

    async def extract(self, lat, lng, start_date, end_date):
        bbox = (
            f"{lng-0.05},{lat-0.05},"
            f"{lng+0.05},{lat+0.05}"
        )
        params = {
            "collection_concept_id": self.collection,
            "temporal":              f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
            "bounding_box":          bbox,
            "page_size":             20,
            "sort_key":              "-start_date",
            "cloud_cover[max]":      30,
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
                "available":  False,
                "source":     "HLS Sentinel-2",
                "collection": self.collection,
            }
        granules = []
        for entry in raw:
            links = entry.get("links", [])
            bands = {}
            for link in links:
                href = link.get("href", "")
                if not href.startswith("https://"):
                    continue
                for band in ["B04", "B05", "B8A", "Fmask"]:
                    if f".{band}." in href:
                        bands[band] = href
            if bands:
                granules.append({
                    "id":          entry.get("id"),
                    "title":       entry.get("title"),
                    "time_start":  entry.get("time_start"),
                    "time_end":    entry.get("time_end"),
                    "cloud_cover": entry.get("cloud_cover"),
                    "bands":       bands,
                })
        granules.sort(key=lambda g: g.get("cloud_cover") or 100)
        return {
            "available":     len(granules) > 0,
            "granule_count": len(granules),
            "latest":        granules[0] if granules else None,
            "granules":      granules,
            "source":        "HLS Sentinel-2 (30m)",
            "collection":    self.collection,
        }

    def quality(self):
        return {
            "sensor":      "hls_s30",
            "confidence":  "high",
            "resolution":  "30m",
            "limitations": [
                "Cloud cover reduces available observations",
                "6-10 clear days per year in Ireland",
            ],
        }
