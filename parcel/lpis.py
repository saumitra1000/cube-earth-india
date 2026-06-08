import httpx
from config.settings import settings


class LPISClient:

    def __init__(self):
        self.url = settings.SUPABASE_URL
        self.key = settings.SUPABASE_KEY

    def _headers(self):
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }

    async def get_parcel(self, lat, lng):
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.url}/rest/v1/ireland_lpis",
                headers=self._headers(),
                params=[
                    ("centroid_lat", f"gte.{round(lat-0.05,4)}"),
                    ("centroid_lat", f"lte.{round(lat+0.05,4)}"),
                    ("centroid_lng", f"gte.{round(lng-0.05,4)}"),
                    ("centroid_lng", f"lte.{round(lng+0.05,4)}"),
                    ("select", "*"),
                    ("limit", "20"),
                ],
                timeout=10,
            )
            if not r.is_success:
                return None
            rows = r.json()
            if not rows:
                return None
            best = min(
                rows,
                key=lambda p: (
                    abs(p["centroid_lat"] - lat) +
                    abs(p["centroid_lng"] - lng)
                )
            )
            # Calculate distance in metres
            import math
            dlat = (best["centroid_lat"] - lat) * 111320
            dlng = (best["centroid_lng"] - lng) * 111320 * math.cos(math.radians(lat))
            dist_m = round(math.sqrt(dlat**2 + dlng**2))
            best["_match_distance_m"] = dist_m
            best["_match_quality"] = (
                "exact"   if dist_m < 50   else
                "close"   if dist_m < 200  else
                "nearby"  if dist_m < 500  else
                "distant"
            )
            return best

    async def get_commonage(self, lat, lng):
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.url}/rest/v1/rpc/terraq_commonage_lookup",
                headers={
                    **self._headers(),
                    "Content-Type": "application/json"
                },
                json={"p_lat": lat, "p_lng": lng},
                timeout=10,
            )
            if not r.is_success:
                return None
            rows = r.json()
            return rows[0] if rows else None


lpis = LPISClient()
