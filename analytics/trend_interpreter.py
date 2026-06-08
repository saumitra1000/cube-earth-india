"""
Trend interpretation engine for Cube Earth.
Combines NDVI trend, anomaly, events and crop type for accurate interpretation.
"""

CROP_TREND_LABELS = {
    "grassland": {
        "strong_increase": "Strong spring growth",
        "increasing":      "Active pasture growth",
        "stable":          "Stable sward condition",
        "declining":       "Pasture declining — possible grazing pressure or dry spell",
        "strong_decline":  "Strong pasture decline — check grazing management",
    },
    "tillage": {
        "strong_increase": "Rapid canopy development",
        "increasing":      "Crop canopy establishing",
        "stable":          "Stable crop canopy",
        "declining":       "Crop senescence or maturity",
        "strong_decline":  "Harvest transition or crop disturbance",
    },
    "arable": {
        "strong_increase": "Rapid crop establishment",
        "increasing":      "Crop canopy developing",
        "stable":          "Stable arable canopy",
        "declining":       "Crop senescence or natural decline",
        "strong_decline":  "Post-harvest or disturbance event",
    },
    "unknown": {
        "strong_increase": "Strong vegetation growth",
        "increasing":      "Increasing vegetation",
        "stable":          "Stable vegetation",
        "declining":       "Declining vegetation",
        "strong_decline":  "Strong vegetation decline",
    },
}

ANOMALY_LABELS = {
    "grassland": {
        "above":  "Above seasonal average — productive conditions",
        "normal": "Near seasonal average — typical conditions",
        "below":  "Below seasonal average — possible stress or late season",
        "strong_below": "Strongly below seasonal average — drought or management change",
    },
    "tillage": {
        "above":  "Above seasonal average — good canopy development",
        "normal": "Near seasonal average — normal crop cycle",
        "below":  "Below seasonal average — harvest or crop change",
        "strong_below": "Strongly below seasonal average — confirms harvest or disturbance",
    },
    "arable": {
        "above":  "Above seasonal average — good crop establishment",
        "normal": "Near seasonal average — typical crop cycle",
        "below":  "Below seasonal average — harvest or post-cultivation",
        "strong_below": "Strongly below seasonal average — confirms disturbance event",
    },
    "unknown": {
        "above":  "Above seasonal average",
        "normal": "Near seasonal average",
        "below":  "Below seasonal average",
        "strong_below": "Strongly below seasonal average",
    },
}

DISTURBANCE_THRESHOLDS = {
    "harvest_or_cut":   {"min_drop": 0.25, "max_after": 0.30},
    "vegetation_loss":  {"min_drop": 0.10, "max_after": 0.50},
    "bare_soil":        {"min_ndvi":  0.00, "max_ndvi":  0.20},
}


def interpret_trend(long_trend, short_trend, long_diff, ndvi, 
                    crop_class, anomaly_pct=None, events=None):
    """
    Full trend interpretation combining all evidence.
    Returns enriched trend object.
    """
    labels = CROP_TREND_LABELS.get(crop_class, CROP_TREND_LABELS["unknown"])
    trend_label = labels.get(long_trend, "Vegetation change detected")

    # Disturbance detection — only trigger if evidence is strong
    has_disturbance = False
    disturbance_confidence = None
    if events:
        high_conf = [e for e in events if e.get("confidence") == "high"
                     and e.get("type") in ("harvest_or_cut", "bare_soil")]
        if high_conf:
            has_disturbance = True
            disturbance_confidence = "high"
        elif any(e.get("type") == "vegetation_loss" for e in events):
            has_disturbance = True
            disturbance_confidence = "moderate"

    # Override trend label if disturbance confirmed
    if has_disturbance and disturbance_confidence == "high":
        if crop_class in ("tillage", "arable"):
            trend_label = "Confirmed harvest or disturbance event"
        elif crop_class == "grassland":
            trend_label = "Confirmed cutting or grazing event"
        else:
            trend_label = "Confirmed disturbance event"

    # Anomaly interpretation
    anomaly_label = None
    if anomaly_pct is not None:
        a_labels = ANOMALY_LABELS.get(crop_class, ANOMALY_LABELS["unknown"])
        if anomaly_pct > 20:
            anomaly_label = a_labels["above"]
        elif anomaly_pct < -50:
            anomaly_label = a_labels["strong_below"]
        elif anomaly_pct < -20:
            anomaly_label = a_labels["below"]
        else:
            anomaly_label = a_labels["normal"]

    # Operational summary
    if has_disturbance and disturbance_confidence == "high":
        if crop_class in ("tillage", "arable"):
            operational = "Field likely post-harvest — monitor for next cultivation cycle"
        else:
            operational = "Recent field disturbance — verify with field observation"
    elif long_trend in ("strong_increase", "increasing") and (anomaly_pct or 0) > 10:
        operational = "Good growth conditions — productive period"
    elif long_trend == "stable":
        operational = "Field conditions stable — no significant change detected"
    elif long_trend in ("declining", "strong_decline") and not has_disturbance:
        if crop_class == "grassland":
            operational = "Pasture declining — review grazing management or moisture stress"
        else:
            operational = "Canopy declining — possible senescence or crop maturity"
    else:
        operational = "Monitor field conditions"

    return {
        "trend_label":             trend_label,
        "anomaly_label":           anomaly_label,
        "operational_summary":     operational,
        "has_disturbance":         has_disturbance,
        "disturbance_confidence":  disturbance_confidence,
    }
