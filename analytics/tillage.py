"""
Tillage and arable crop decision engine for Cube Earth.
"""

def crop_condition(ndvi, ndvi_age_days, crop_str):
    if ndvi is None:
        return {
            "status": "unavailable",
            "label": "No optical data",
            "note": "Sentinel-2 data unavailable for this location",
        }
    if ndvi >= 0.75:
        vigour = "High"
        desc = "Dense productive canopy"
    elif ndvi >= 0.55:
        vigour = "Moderate"
        desc = "Developing canopy — normal for season"
    elif ndvi >= 0.40:
        vigour = "Low-moderate"
        desc = "Early or partially developed canopy"
    elif ndvi >= 0.25:
        vigour = "Low"
        desc = "Sparse canopy — possible stress or early stage"
    else:
        vigour = "Very low"
        desc = "Bare or very early stage"
    age_note = None
    if ndvi_age_days and ndvi_age_days > 20:
        age_note = f"Optical data {ndvi_age_days} days old — recent confirmation advised"
    return {
        "status": vigour.lower().replace("-", "_"),
        "label": vigour,
        "description": desc,
        "ndvi": round(ndvi, 3),
        "age_days": ndvi_age_days,
        "age_note": age_note,
    }

def moisture_condition(surface_sm, rootzone_sm):
    if surface_sm is None:
        return {"label": "Unknown", "note": "Soil moisture unavailable"}
    if surface_sm > 0.42:
        label = "Waterlogged"
        note = "Excess moisture — field operations not advised"
    elif surface_sm > 0.35:
        label = "Wet"
        note = "Near field capacity — monitor before operations"
    elif surface_sm > 0.25:
        label = "Adequate"
        note = "Moisture within normal range for crop growth"
    elif surface_sm > 0.15:
        label = "Dry"
        note = "Below field capacity — irrigation may be beneficial"
    else:
        label = "Very dry"
        note = "Significant moisture deficit"
    return {
        "label": label,
        "surface_m3": round(surface_sm, 3),
        "rootzone_m3": round(rootzone_sm, 3) if rootzone_sm else None,
        "note": note,
    }

def _machinery_note(surface_sm, slope, traffic_score):
    if surface_sm and surface_sm > 0.40:
        return "Avoid field operations — waterlogged conditions"
    if surface_sm and surface_sm > 0.35:
        return "Caution — near field capacity, monitor before operations"
    if (slope or 0) > 10:
        return "Steep slope — exercise caution with heavy machinery"
    if traffic_score and traffic_score >= 7:
        return "Good conditions for field operations"
    if traffic_score and traffic_score >= 5:
        return "Moderate conditions — light machinery preferred"
    return "Check field conditions before operations"

def tillage_decisions(ndvi, ndvi_age_days, surface_sm, rootzone_sm,
                      slope, crop_str, traffic_score):
    return {
        "crop_condition":     crop_condition(ndvi, ndvi_age_days, crop_str),
        "moisture_condition": moisture_condition(surface_sm, rootzone_sm),
        "machinery_note":     _machinery_note(surface_sm, slope, traffic_score),
    }
