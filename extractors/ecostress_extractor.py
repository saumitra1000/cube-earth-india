import httpx
from extractors.base_extractor import BaseExtractor
from config.settings import settings
from parsers.ecostress_parser import extract_lst

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"

# MGRS tiles that cover Ireland for ECOSTRESS
IRELAND_TILES = {"29UPU", "29UPV", "29UQU", "29UQV", "30UUE", "30UVE"}


class ECOSTRESSExtractor(BaseExtractor):

    def __init__(self):
        super().__init__("ecostress")
        self.collection = settings.ECOSTRESS_COLLECTION

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
            if "lp-prod-protected" not in href:
                continue
            if href.endswith("_LST.tif"):
                links["lst"] = href
            elif href.endswith("_QC.tif"):
                links["qc"] = href
        return links

    def _tile(self, entry):
        title = entry.get("title", "")
        for tile in IRELAND_TILES:
            if tile in title:
                return tile
        return None

    async def extract(self, lat, lng, start_date, end_date):
        bbox = f"{lng-4.5},{lat-2.4},{lng+4.5},{lat+2.4}"
        params = {
            "collection_concept_id": self.collection,
            "temporal":   f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
            "bounding_box": bbox,
            "page_size":  50,
            "sort_key":   "-start_date",
        }
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                CMR_URL,
                headers=self._headers(),
                params=params,
            )
            if r.status_code == 401:
                return {"available": False, "error": "NASA token expired"}
        r.raise_for_status()
        entries = r.json().get("feed", {}).get("entry", [])

        def score(e):
            from datetime import datetime, timezone
            t = e.get("time_start", "")
            month = int(t[5:7]) if len(t) >= 7 else 6
            age = 0
            try:
                dt  = datetime.fromisoformat(t.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - dt).days
            except Exception:
                pass
            return (1000 if 4 <= month <= 10 else 0) - age

        entries.sort(key=score, reverse=True)

        for entry in entries:
            if not self._tile(entry):
                continue
            links = self._parse_links(entry)
            if not links.get("lst"):
                continue
            granule = {
                "title":      entry.get("title"),
                "time_start": entry.get("time_start"),
                "links":      links,
            }
            lst = await extract_lst(granule, lat, lng)
            if lst and lst.get("available"):
                return {
                    "granule":   granule,
                    "lst":       lst,
                    "available": True,
                }

        return {"available": False, "source": "ECOSTRESS"}

    def parse(self, raw):
        if not raw or not raw.get("available"):
            return {"available": False, "source": "ECOSTRESS"}
        lst = raw.get("lst", {})
        return {
            "available":      True,
            "celsius":        lst.get("celsius"),
            "kelvin":         lst.get("kelvin"),
            "interpretation": lst.get("interpretation"),
            "granule_time":   lst.get("granule_time"),
            "resolution_m":   70,
            "source":         "ECOSTRESS ECO_L2T_LSTE V002 (70m)",
        }

    def quality(self):
        return {
            "sensor":      "ecostress",
            "confidence":  "high",
            "resolution":  "70m",
            "limitations": [
                "Ireland tiles: 29UPU, 29UPV, 29UQU, 29UQV",
                "Cloud cover limits valid retrievals",
            ],
        }
