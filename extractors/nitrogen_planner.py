"""
Nitrogen Efficiency Planner
Uses soil temperature, soil moisture, rainfall forecast
to determine optimal N application window.

Science basis:
- Soil temp > 6°C → grass responding to N
- Soil temp > 8°C → optimal N uptake
- Soil moisture 0.20-0.32 → ideal conditions
- Rain forecast < 15mm/48hr → low leaching risk
- Teagasc Nitrates Action Programme rules
"""

def calculate_n_window(weather, soil_moisture, cover_kg=None, crop=None):
    """
    Returns N application recommendation
    """
    if not weather or not weather.get("available"):
        return {"available": False, "error": "No weather data"}

    current = weather.get("current", {})
    forecast = weather.get("forecast_7d", [])
    gc = weather.get("growth_conditions", {})

    # Soil temperatures
    soil_temp_6cm = current.get("soil_temp_6cm") or current.get("soil_temp_c") or 0
    soil_temp_0cm = current.get("soil_temp_0cm") or current.get("soil_temp_c") or 0
    air_temp = current.get("temp_c") or 0

    # Use 6cm as primary — grass root zone
    soil_temp = soil_temp_6cm or soil_temp_0cm

    # Rainfall next 48hrs
    rain_48h = sum([
        d.get("rain_mm", 0) or 0
        for d in forecast[:2]
    ]) if forecast else gc.get("rain_7d_mm", 0) / 7 * 2

    # Soil moisture
    smap = soil_moisture or 0

    # Irish closed seasons (Nitrates Action Programme)
    import datetime
    today = datetime.datetime.utcnow()
    month = today.month
    day = today.day

    # Closed season: Oct 15 - Jan 12 for cattle slurry
    # For chemical N: no closed season but conditions apply
    in_closed_season = (
        (month == 10 and day >= 15) or
        month == 11 or
        month == 12 or
        (month == 1 and day <= 12)
    )

    # Scoring system
    score = 0
    reasons_good = []
    reasons_bad = []
    reasons_caution = []

    # 1. Soil temperature check (most important)
    if soil_temp >= 10:
        score += 3
        reasons_good.append(f"🌡 Soil temp {soil_temp}°C — excellent N response expected")
    elif soil_temp >= 7:
        score += 2
        reasons_good.append(f"🌡 Soil temp {soil_temp}°C — good N response expected")
    elif soil_temp >= 6:
        score += 1
        reasons_caution.append(f"🌡 Soil temp {soil_temp}°C — borderline, monitor daily")
    else:
        score -= 2
        reasons_bad.append(f"🌡 Soil temp {soil_temp}°C — below 6°C threshold, N will be wasted")

    # 2. Rainfall forecast check
    if rain_48h < 10:
        score += 2
        reasons_good.append(f"🌧 {rain_48h:.0f}mm forecast in 48hrs — low leaching risk")
    elif rain_48h < 15:
        score += 1
        reasons_caution.append(f"🌧 {rain_48h:.0f}mm forecast — moderate leaching risk, apply early morning")
    else:
        score -= 2
        reasons_bad.append(f"🌧 {rain_48h:.0f}mm forecast — high leaching risk, N will wash away")

    # 3. Soil moisture check
    if 0.20 <= smap <= 0.28:
        score += 2
        reasons_good.append(f"💧 Soil moisture {smap:.2f} m³/m³ — ideal for N uptake")
    elif smap < 0.20:
        score += 1
        reasons_caution.append(f"💧 Soil moisture {smap:.2f} m³/m³ — dry, N uptake may be slow")
    elif smap <= 0.33:
        score += 0
        reasons_caution.append(f"💧 Soil moisture {smap:.2f} m³/m³ — moist, watch for runoff")
    else:
        score -= 1
        reasons_bad.append(f"💧 Soil moisture {smap:.2f} m³/m³ — saturated, high runoff risk")

    # 4. Grass cover check
    if cover_kg:
        if cover_kg < 800:
            score -= 1
            reasons_bad.append("🌿 Cover too low — grass not actively growing, N response poor")
        elif cover_kg < 1200:
            score += 0
            reasons_caution.append("🌿 Cover building — N will help but response moderate")
        elif cover_kg <= 2500:
            score += 1
            reasons_good.append(f"🌿 Cover {cover_kg} kg/ha — grass actively growing, good N response")
        else:
            score -= 1
            reasons_caution.append("🌿 Surplus cover — N not needed right now")

    # 5. Closed season
    if in_closed_season:
        score = -10
        reasons_bad.append("📅 Closed season — chemical N application restricted")

    # Determine traffic light
    if score >= 5:
        status = "green"
        label = "✅ Optimal Window — Apply Now"
        sub = "All conditions favour N application"
        color = "#16a34a"
    elif score >= 3:
        status = "green"
        label = "✅ Good Window — Apply Soon"
        sub = "Conditions suitable for N application"
        color = "#16a34a"
    elif score >= 1:
        status = "yellow"
        label = "⚠ Marginal — Apply with Caution"
        sub = "Some conditions not ideal"
        color = "#d97706"
    elif score >= -1:
        status = "yellow"
        label = "⚠ Wait — Conditions Improving"
        sub = "Monitor daily, apply when conditions improve"
        color = "#d97706"
    else:
        status = "red"
        label = "🔴 Do Not Apply"
        sub = "Conditions unfavourable — N will be wasted or lost"
        color = "#dc2626"

    # Recommended N rate
    if status == "green" and cover_kg and 1200 <= cover_kg <= 2000:
        rec_rate = 25  # kg N/ha — standard spring/summer split
    elif status == "green":
        rec_rate = 20
    elif status == "yellow":
        rec_rate = 15
    else:
        rec_rate = 0

    # Estimated grass response (Teagasc: 1kg N → 15-20kg DM in good conditions)
    response_kg_dm = rec_rate * (18 if status == "green" else 12) if rec_rate > 0 else 0

    # Cost estimate (€1.20/kg N for CAN)
    cost = rec_rate * 1.20 if rec_rate > 0 else 0
    value = response_kg_dm * 0.18 if response_kg_dm > 0 else 0  # €0.18/kg DM

    # Find next good window
    next_window = None
    if status != "green":
        for i, d in enumerate(forecast):
            day_rain = d.get("rain_mm", 0) or 0
            day_temp = d.get("temp_max", 0) or 0
            if day_rain < 5 and day_temp > 8 and smap < 0.30:
                next_window = f"In {i+1} day{'s' if i > 0 else ''}"
                break

    return {
        "available": True,
        "status": status,
        "label": label,
        "sub": sub,
        "color": color,
        "score": score,
        "soil_temp_6cm": round(soil_temp, 1),
        "soil_temp_threshold": 6,
        "rain_48h_mm": round(rain_48h, 1),
        "soil_moisture": round(smap, 3),
        "rec_rate_kg_n_ha": rec_rate,
        "response_kg_dm_ha": response_kg_dm,
        "cost_eur": round(cost, 2),
        "value_eur": round(value, 2),
        "reasons_good": reasons_good,
        "reasons_caution": reasons_caution,
        "reasons_bad": reasons_bad,
        "next_good_window": next_window,
        "in_closed_season": in_closed_season,
        "note": "Based on Teagasc NAP guidelines · CAN @ €1.20/kg N · 15-20 kg DM response per kg N"
    }
