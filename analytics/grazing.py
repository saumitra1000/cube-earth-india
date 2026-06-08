def grazing_suitability(surface_sm, slope, waterlog_prob, area_ha, ndvi=None, crop=None):
    score = 0
    breakdown = {}

    # Grass cover from NDVI
    if ndvi is not None:
        if ndvi >= 0.75:
            score += 3
            breakdown["grass_cover"] = {"points": 3, "reason": f"NDVI {ndvi:.2f} — excellent pasture cover"}
        elif ndvi >= 0.55:
            score += 2
            breakdown["grass_cover"] = {"points": 2, "reason": f"NDVI {ndvi:.2f} — moderate grass cover"}
        elif ndvi >= 0.35:
            score += 1
            breakdown["grass_cover"] = {"points": 1, "reason": f"NDVI {ndvi:.2f} — sparse grass cover"}
        else:
            score += 0
            breakdown["grass_cover"] = {"points": 0, "reason": f"NDVI {ndvi:.2f} — poor grass cover"}
    else:
        breakdown["grass_cover"] = {"points": 0, "reason": "NDVI unavailable — grass cover unknown"}

    # Soil moisture adequacy
    if surface_sm is not None:
        if 0.25 < surface_sm < 0.38:
            score += 3
            breakdown["moisture"] = {"points": 3, "reason": f"Soil moisture {surface_sm:.3f} m³/m³ — optimal for grazing"}
        elif 0.20 <= surface_sm <= 0.42:
            score += 2
            breakdown["moisture"] = {"points": 2, "reason": f"Soil moisture {surface_sm:.3f} m³/m³ — acceptable"}
        elif surface_sm < 0.20:
            score += 1
            breakdown["moisture"] = {"points": 1, "reason": f"Soil moisture {surface_sm:.3f} m³/m³ — dry, watch for stress"}
        else:
            score += 0
            breakdown["moisture"] = {"points": 0, "reason": f"Soil moisture {surface_sm:.3f} m³/m³ — too wet for grazing"}
    else:
        breakdown["moisture"] = {"points": 0, "reason": "Soil moisture unavailable"}

    # Terrain / trafficability
    if (slope or 0) < 3:
        score += 2
        breakdown["terrain"] = {"points": 2, "reason": f"Slope {slope or 0:.1f}° — flat, easy livestock access"}
    elif (slope or 0) < 8:
        score += 1
        breakdown["terrain"] = {"points": 1, "reason": f"Slope {slope or 0:.1f}° — moderate gradient"}
    else:
        score += 0
        breakdown["terrain"] = {"points": 0, "reason": f"Slope {slope or 0:.1f}° — steep, limits grazing access"}

    # Waterlogging penalty
    if waterlog_prob == "high":
        score = max(0, score - 3)
        breakdown["waterlogging"] = {"points": -3, "reason": "High waterlogging risk — poaching likely"}
    elif waterlog_prob == "moderate":
        score = max(0, score - 1)
        breakdown["waterlogging"] = {"points": -1, "reason": "Moderate waterlogging risk"}
    else:
        breakdown["waterlogging"] = {"points": 0, "reason": "No waterlogging risk detected"}

    # Seasonal adjustment for permanent pasture
    if crop and "pasture" in crop.lower():
        breakdown["seasonal"] = {"points": 0, "reason": "Permanent pasture — year-round grazing system"}
    elif crop and ("tillage" in crop.lower() or "rape" in crop.lower() or "cereal" in crop.lower()):
        score = max(0, score - 2)
        breakdown["seasonal"] = {"points": -2, "reason": "Tillage crop — not suitable for grazing"}
    else:
        breakdown["seasonal"] = {"points": 0, "reason": "Crop type neutral"}

    # Tillage override — never suitable for grazing
    is_tillage = crop and any(x in crop.lower() for x in 
        ["rape", "wheat", "barley", "oat", "cereal", "tillage", 
         "maize", "beet", "potato", "vegetable"])
    
    if is_tillage:
        label = "Not suitable — tillage crop"
    else:
        label = (
            "Excellent" if score >= 7 else
            "Good"      if score >= 5 else
            "Moderate"  if score >= 3 else
            "Poor"      if score >= 1 else
            "Not suitable"
        )

    note = None
    if area_ha and area_ha < 0.5:
        note = "Small parcel limits practical grazing management"

    return {
        "score": score,
        "label": label,
        "note": note,
        "breakdown": breakdown,
        "max_score": 8,
    }


def machinery_trafficability(surface_sm, rootzone_sm, slope):
    score = 0
    if surface_sm is not None:
        if surface_sm < 0.25:   score += 4
        elif surface_sm < 0.30: score += 3
        elif surface_sm < 0.35: score += 2
        elif surface_sm < 0.40: score += 1
    if rootzone_sm is not None:
        if rootzone_sm < 0.28:  score += 3
        elif rootzone_sm < 0.33: score += 2
        elif rootzone_sm < 0.38: score += 1
    if (slope or 0) < 2:    score += 3
    elif (slope or 0) < 6:  score += 2
    elif (slope or 0) < 12: score += 1
    label = (
        "Excellent"                          if score >= 8 else
        "Good"                               if score >= 6 else
        "Moderate - proceed with caution"    if score >= 4 else
        "Poor - risk of rutting/compaction"  if score >= 2 else
        "Unsuitable - do not operate machinery"
    )
    return {"score": score, "label": label}


def slurry_suitability(surface_sm, slope, traffic_score):
    if surface_sm and surface_sm > 0.40:
        return {"suitable": "not_suitable",
                "note": "Waterlogged - high runoff and leaching risk"}
    if (slope or 0) > 10:
        return {"suitable": "not_suitable",
                "note": "Slope exceeds 10deg - unacceptable runoff risk"}
    if traffic_score < 3:
        return {"suitable": "not_suitable",
                "note": "Ground too wet for machinery access"}
    if surface_sm and surface_sm > 0.35:
        return {"suitable": "caution",
                "note": "Near field capacity - monitor for runoff"}
    return {"suitable": "suitable",
            "note": "Conditions acceptable for spreading"}
