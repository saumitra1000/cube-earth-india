import httpx
from extractors.base_extractor import BaseExtractor
from config.settings import settings

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"


class OPERAS1Extractor(BaseExtractor):

    def __init__(self):
        super().__init__("opera_s1")
        self.collection = settings.OPERA_RTC_COLLECTION

    def _headers(self):
        return {
            "Authorization": f"Bearer {settings.NASA_TOKEN}",
            "Accept": "application/json",
        }

    def _parse_links(self, entry):
        links = {}
        for l in entry.get("links", []):
            href = l.get("href", "")
            if not href.startswith("https://"):
                continue
            if href.endswith("_VV.tif"):
                links["vv"] = href
            elif href.endswith("_VH.tif"):
                links["vh"] = href
            elif href.endswith("_mask.tif"):
                links["mask"] = href
        return links

    async def extract(self, lat, lng, start_date, end_date):
        bbox = f"{lng-0.15},{lat-0.15},{lng+0.15},{lat+0.15}"
        params = {
            "collection_concept_id": self.collection,
            "temporal":   f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
            "bounding_box": bbox,
            "page_size":  20,
            "sort_key":   "-start_date",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                CMR_URL,
                headers=self._headers(),
                params=params,
            )
            r.raise_for_status()
        entries = r.json().get("feed", {}).get("entry", [])

        granules = []
        for entry in entries:
            links = self._parse_links(entry)
            if links.get("vv") and links.get("vh"):
                granules.append({
                    "title":      entry.get("title"),
                    "time_start": entry.get("time_start"),
                    "links":      links,
                })

        return granules if granules else None

    def parse(self, raw):
        if not raw:
            return {"available": False, "source": "OPERA RTC-S1"}
        return {
            "available":     True,
            "granule_count": len(raw),
            "granules":      raw,
            "source":        "OPERA RTC-S1 (30m)",
        }

    def quality(self):
        return {
            "sensor":      "opera_s1",
            "confidence":  "moderate",
            "resolution":  "30m",
            "limitations": [
                "C-band SAR — speckle requires spatial averaging",
                "Single pixel SAR is agronomically meaningless",
                "Barrett 2014: C+L kappa=0.98 vs C alone kappa=0.87",
                "Multi-date averaging required for stable signal",
            ],
        }
