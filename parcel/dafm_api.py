"""
DAFM GeoAPI extractor for Cube Earth.
Replaces Supabase centroid matching with live point-in-polygon queries.
"""
import httpx
import asyncio

GEOAPI_BASE = "https://geoapi.opendata.agriculture.gov.ie/shps/collections"
COLLECTION  = "anonymous-lpis-data-for-2024_2024-lpis-data"


async def get_parcel_at_point(lat, lng, tolerance=0.015):
    """
    Query DAFM GeoAPI for parcel at exact point.
    Uses bbox around point then checks geometry.
    """
    bbox = f"{lng-tolerance},{lat-tolerance},{lng+tolerance},{lat+tolerance}"
    url  = f"{GEOAPI_BASE}/{COLLECTION}/items"

    params = {
        "bbox":  bbox,
        "f":     "json",
        "limit": 10,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()

    features = data.get("features", [])
    if not features:
        return None

    # Find best match using point-in-polygon test
    def point_in_polygon(lat, lng, polygon_coords):
        """Ray casting algorithm — coords are [lng, lat] in GeoJSON."""
        inside = False
        n = len(polygon_coords)
        j = n - 1
        for i in range(n):
            # GeoJSON coords are [longitude, latitude]
            xi, yi = polygon_coords[i][0], polygon_coords[i][1]
            xj, yj = polygon_coords[j][0], polygon_coords[j][1]
            # Test against lng/lat correctly
            if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    best = None
    best_fallback = None
    best_fallback_area = float('inf')

    for f in features:
        props = f.get("properties", {})
        crop  = props.get("CROP", "")
        geom  = f.get("geometry", {})

        # Skip non-agricultural features
        if crop.lower() in ("building", "road", "water", ""):
            continue

        # Try exact point-in-polygon first
        if geom and geom.get("type") == "Polygon":
            coords = geom.get("coordinates", [[]])[0]
            if coords and point_in_polygon(lat, lng, coords):
                best = f
                break

        # Fallback: closest centroid
        area = float(props.get("CLAIM_AREA") or props.get("DIGITISED") or 0)
        if area > 0 and area < best_fallback_area:
            best_fallback_area = area
            best_fallback = f

    if not best:
        best = best_fallback or features[0]

    props = best.get("properties", {})
    geom  = best.get("geometry", {})

    # Calculate centroid from geometry if available
    centroid_lat, centroid_lng = lat, lng
    if geom and geom.get("type") == "Polygon":
        coords = geom.get("coordinates", [[]])[0]
        if coords:
            centroid_lng = sum(c[0] for c in coords) / len(coords)
            centroid_lat = sum(c[1] for c in coords) / len(coords)

    import math
    dlat = (centroid_lat - lat) * 111320
    dlng = (centroid_lng - lng) * 111320 * math.cos(math.radians(lat))
    dist_m = round(math.sqrt(dlat**2 + dlng**2))

    crop_str = props.get("CROP", "Unknown")

    return {
        "par_lab":      props.get("PAR_LAB"),
        "herd":         props.get("HERD"),
        "claim_area":   float(props.get("CLAIM_AREA") or 0),
        "ref_area":     float(props.get("REF_AREA") or 0),
        "crop":         crop_str,
        "grassland":    props.get("GRASSLND") == "Y",
        "tillage":      props.get("TILLAGE") == "Y",
        "arable":       props.get("ARABL_IND") == "Y",
        "permanent":    props.get("PERM_IND") == "Y",
        "biss":         props.get("BISS") == "Y",
        "criss":        props.get("CRISS") == "Y",
        "eco":          props.get("ECO") == "Y",
        "anc":          props.get("ANC") == "Y",
        "acres":        props.get("ACRES") == "Y",
        "organics":     props.get("ORGANICS") == "Y",
        "olr":          props.get("OLR"),
        "comm_ind":     props.get("COMM_IND") == "Y",
        "centroid_lat": centroid_lat,
        "centroid_lng": centroid_lng,
        "_match_distance_m": dist_m,
        "_match_quality": (
            "exact"   if dist_m < 50   else
            "close"   if dist_m < 200  else
            "nearby"  if dist_m < 500  else
            "distant"
        ),
        "_source": "DAFM GeoAPI 2024",
    }


def _is_grassland(crop):
    c = crop.lower()
    return any(k in c for k in ["pasture","grass","silage","hay","grazing","meadow"])

def _is_tillage(crop):
    c = crop.lower()
    return any(k in c for k in ["rape","wheat","barley","oats","cereal","maize","rye"])

def _is_arable(crop):
    c = crop.lower()
    return any(k in c for k in ["potato","beet","vegetable","bean","pea"])
