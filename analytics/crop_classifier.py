"""
Crop type classifier for Cube Earth decision engine.
Maps DAFM LPIS crop strings to agronomic decision classes.
"""

GRASSLAND_KEYWORDS = [
    "permanent pasture", "grass ley", "temporary grass",
    "pasture", "silage", "hay", "grazing", "meadow",
    "rough grazing", "commonage"
]

TILLAGE_KEYWORDS = [
    "oilseed rape", "winter wheat", "spring wheat",
    "winter barley", "spring barley", "oats", "rye",
    "triticale", "maize", "corn", "cereal",
    "rape", "wheat", "barley"
]

ARABLE_KEYWORDS = [
    "potato", "sugar beet", "fodder beet", "turnip",
    "kale", "vegetable", "bean", "pea", "lentil",
    "sunflower", "hemp", "flax"
]

FALLOW_KEYWORDS = [
    "fallow", "set aside", "cover crop", "green cover",
    "bare", "unseeded"
]


def classify_crop(crop_str):
    """
    Returns crop class and metadata.
    
    Classes:
        grassland  → grazing decisions
        tillage    → machinery + crop canopy
        arable     → soil condition + trafficability  
        fallow     → soil condition only
        unknown    → generic decisions
    """
    if not crop_str:
        return {
            "class": "unknown",
            "label": "Unknown",
            "grazing_relevant": False,
            "ndvi_label": "Vegetation canopy",
        }

    crop_lower = crop_str.lower()

    if any(k in crop_lower for k in GRASSLAND_KEYWORDS):
        return {
            "class": "grassland",
            "label": "Grassland",
            "grazing_relevant": True,
            "ndvi_label": "Grass cover",
            "decisions": ["grazing", "machinery", "slurry"],
        }

    if any(k in crop_lower for k in TILLAGE_KEYWORDS):
        return {
            "class": "tillage",
            "label": "Tillage",
            "grazing_relevant": False,
            "ndvi_label": "Crop canopy vigour",
            "decisions": ["machinery", "crop_condition", "moisture"],
        }

    if any(k in crop_lower for k in ARABLE_KEYWORDS):
        return {
            "class": "arable",
            "label": "Arable",
            "grazing_relevant": False,
            "ndvi_label": "Crop canopy vigour",
            "decisions": ["machinery", "crop_condition", "moisture"],
        }

    if any(k in crop_lower for k in FALLOW_KEYWORDS):
        return {
            "class": "fallow",
            "label": "Fallow / Cover crop",
            "grazing_relevant": False,
            "ndvi_label": "Ground cover",
            "decisions": ["machinery", "moisture"],
        }

    # Default — treat as grassland if LPIS grassland flag set
    return {
        "class": "unknown",
        "label": crop_str,
        "grazing_relevant": True,
        "ndvi_label": "Vegetation canopy",
        "decisions": ["machinery", "moisture"],
    }


def ndvi_status_for_crop(ndvi, crop_class):
    """Crop-aware NDVI interpretation."""
    if ndvi is None:
        return "No optical data"

    if crop_class == "grassland":
        if ndvi >= 0.75: return "Excellent grass cover"
        if ndvi >= 0.55: return "Good grass cover"
        if ndvi >= 0.40: return "Moderate grass cover"
        if ndvi >= 0.25: return "Sparse grass cover"
        return "Poor or bare"

    if crop_class in ("tillage", "arable"):
        if ndvi >= 0.75: return "High canopy vigour"
        if ndvi >= 0.55: return "Moderate canopy vigour"
        if ndvi >= 0.40: return "Developing canopy"
        if ndvi >= 0.25: return "Early or stressed canopy"
        return "Bare or very early stage"

    if crop_class == "fallow":
        if ndvi >= 0.50: return "Good ground cover"
        if ndvi >= 0.30: return "Moderate ground cover"
        return "Sparse ground cover"

    # Unknown
    if ndvi >= 0.75: return "High vegetation vigour"
    if ndvi >= 0.55: return "Moderate vegetation vigour"
    if ndvi >= 0.35: return "Low vegetation vigour"
    return "Very low or no vegetation"
