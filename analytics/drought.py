def drought_stress_index(surface_sm, rootzone_sm):
    score = 0
    sm = rootzone_sm or surface_sm
    if sm is not None:
        if sm < 0.15: score += 4
        elif sm < 0.20: score += 3
        elif sm < 0.28: score += 2
        elif sm < 0.32: score += 1
    severity = (
        "extreme" if score >= 9 else
        "severe"  if score >= 7 else
        "moderate" if score >= 5 else
        "mild"    if score >= 2 else
        "none"
    )
    return {
        "score":    score,
        "severity": severity,
        "label":    "No drought stress" if severity == "none"
                    else f"{severity.capitalize()} drought stress",
    }

def waterlogging_probability(surface_sm, rootzone_sm, slope):
    score = 0
    if surface_sm is not None:
        if surface_sm > 0.42: score += 3
        elif surface_sm > 0.38: score += 2
        elif surface_sm > 0.35: score += 1
    if rootzone_sm is not None:
        if rootzone_sm > 0.38: score += 3
        elif rootzone_sm > 0.33: score += 2
        elif rootzone_sm > 0.30: score += 1
    if (slope or 99) < 1: score += 2
    elif (slope or 99) < 3: score += 1
    probability = (
        "high"     if score >= 7 else
        "moderate" if score >= 4 else
        "low"
    )
    return {
        "score":       score,
        "probability": probability,
        "note": (
            "Multiple indicators confirm high waterlogging risk"
            if probability == "high" else
            "Elevated moisture with some drainage limitation"
            if probability == "moderate" else
            "Moisture within acceptable range"
        ),
    }
