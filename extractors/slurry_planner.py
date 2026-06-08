"""
Slurry Management System
Traffic light based on:
- Soil moisture (SMAP)
- 48hr rainfall forecast (UKMO)
- Soil temperature
- Irish Nitrates Action Programme closed seasons
- Watercourse proximity (field geometry)

Legal basis: SI 378/2022 Irish Nitrates Action Programme
"""
import datetime

def get_closed_season(slurry_type="cattle"):
    """
    Irish NAP closed seasons for slurry spreading
    SI 378/2022
    """
    today = datetime.datetime.utcnow()
    month = today.month
    day = today.day

    if slurry_type == "cattle":
        # Cattle slurry: Oct 15 - Jan 12
        closed = (
            (month == 10 and day >= 15) or
            month == 11 or
            month == 12 or
            (month == 1 and day <= 12)
        )
        close_date = "15 October"
        open_date = "13 January"
    elif slurry_type == "pig":
        # Pig/poultry: Oct 15 - Jan 31
        closed = (
            (month == 10 and day >= 15) or
            month == 11 or
            month == 12 or
            month == 1
        )
        close_date = "15 October"
        open_date = "1 February"
    else:
        # Soiled water: Nov 1 - Jan 31
        closed = (
            month == 11 or
            month == 12 or
            month == 1
        )
        close_date = "1 November"
        open_date = "1 February"

    # Days to close or open
    if closed:
        # Find days until open
        open_month = int(open_date.split()[1] if len(open_date.split()) > 1 else 1)
        open_day = int(open_date.split()[0])
        next_open = datetime.datetime(today.year + (1 if open_month <= today.month else 0), open_month, open_day)
        days_to_open = (next_open - today).days
    else:
        # Find days until close
        close_month = 10  # October
        close_day = 15
        next_close = datetime.datetime(today.year, close_month, close_day)
        if next_close < today:
            next_close = datetime.datetime(today.year + 1, close_month, close_day)
        days_to_close = (next_close - today).days

    return {
        "closed": closed,
        "close_date": close_date,
        "open_date": open_date,
        "days_to_open": days_to_open if closed else None,
        "days_to_close": days_to_close if not closed else None
    }


def calculate_slurry_window(weather, soil_moisture, slurry_type="cattle", field_area=None):
    """
    Calculate slurry spreading traffic light
    Returns green/yellow/red with reasons
    """
    if not weather or not weather.get("available"):
        return {"available": False, "error": "No weather data"}

    current = weather.get("current", {})
    forecast = weather.get("forecast_7d", [])
    gc = weather.get("growth_conditions", {})

    # Key parameters
    soil_temp = current.get("soil_temp_6cm") or current.get("soil_temp_c") or 0
    air_temp = current.get("temp_c") or 0
    smap = soil_moisture or 0

    # Rain next 48hrs
    rain_48h = sum([
        d.get("rain_mm", 0) or 0
        for d in forecast[:2]
    ]) if forecast else (gc.get("rain_7d_mm", 0) / 7 * 2)

    # Rain next 24hrs (more critical)
    rain_24h = forecast[0].get("rain_mm", 0) if forecast else rain_48h / 2

    # Closed season check
    season = get_closed_season(slurry_type)

    reasons_good = []
    reasons_caution = []
    reasons_bad = []
    reasons_legal = []

    # LEGAL CHECKS FIRST
    if season["closed"]:
        reasons_legal.append(f"🚫 Closed season — {slurry_type.capitalize()} slurry spreading prohibited until {season['open_date']}")
        return {
            "available": True,
            "status": "red",
            "label": "🔴 Do Not Spread — Closed Season",
            "sub": f"Prohibited until {season['open_date']} · SI 378/2022",
            "color": "#dc2626",
            "legal": True,
            "closed_season": True,
            "days_to_open": season["days_to_open"],
            "rain_48h_mm": round(rain_48h, 1),
            "soil_moisture": round(smap, 3),
            "soil_temp_6cm": round(soil_temp, 1),
            "reasons_legal": reasons_legal,
            "reasons_good": [],
            "reasons_caution": [],
            "reasons_bad": [],
            "compliance_note": "Spreading during closed season risks loss of farm payments and fines up to €5,000",
            "note": "Irish Nitrates Action Programme · SI 378/2022"
        }

    # Warning if close to closed season
    if season["days_to_close"] and season["days_to_close"] <= 14:
        reasons_caution.append(f"⚠ Closed season starts in {season['days_to_close']} days ({season['close_date']})")

    # CONDITION SCORING
    score = 0

    # 1. Soil saturation (most critical for runoff)
    if smap > 0.38:
        score -= 3
        reasons_bad.append(f"💧 Soil saturated ({smap:.2f} m³/m³) — high runoff risk, nutrients will reach watercourse")
    elif smap > 0.33:
        score -= 1
        reasons_caution.append(f"💧 Soil very wet ({smap:.2f} m³/m³) — significant runoff risk")
    elif smap > 0.28:
        score += 0
        reasons_caution.append(f"💧 Soil moist ({smap:.2f} m³/m³) — moderate risk, apply lightly")
    else:
        score += 2
        reasons_good.append(f"💧 Soil moisture {smap:.2f} m³/m³ — good conditions for absorption")

    # 2. Rain in next 24hrs (most critical)
    if rain_24h > 10:
        score -= 3
        reasons_bad.append(f"🌧 {rain_24h:.0f}mm rain forecast today — do not spread")
    elif rain_24h > 5:
        score -= 1
        reasons_caution.append(f"🌧 {rain_24h:.0f}mm rain today — high runoff risk if spreading")
    else:
        score += 1
        reasons_good.append(f"🌧 Only {rain_24h:.0f}mm rain forecast today — low immediate risk")

    # 3. Rain in next 48hrs
    if rain_48h > 20:
        score -= 2
        reasons_bad.append(f"🌧 {rain_48h:.0f}mm in 48hrs — nutrients will leach before absorption")
    elif rain_48h > 10:
        score -= 1
        reasons_caution.append(f"🌧 {rain_48h:.0f}mm in 48hrs — apply early and allow absorption time")
    else:
        score += 1
        reasons_good.append(f"🌧 {rain_48h:.0f}mm forecast in 48hrs — acceptable conditions")

    # 4. Soil temperature
    if soil_temp < 3:
        score -= 1
        reasons_caution.append(f"🌡 Soil temp {soil_temp}°C — slow absorption, risk of runoff in frost")
    elif soil_temp > 5:
        score += 1
        reasons_good.append(f"🌡 Soil temp {soil_temp}°C — soil biology active, good absorption")

    # TRAFFIC LIGHT DECISION
    if score >= 3:
        status = "green"
        label = "🟢 Safe to Spread"
        sub = "Conditions suitable — low runoff and leaching risk"
        color = "#16a34a"
    elif score >= 1:
        status = "green"
        label = "🟢 Suitable — Apply with Care"
        sub = "Monitor conditions — spread early in day"
        color = "#16a34a"
    elif score >= -1:
        status = "yellow"
        label = "🟡 Caution — Risk of Runoff"
        sub = "Conditions marginal — consider waiting"
        color = "#d97706"
    elif score >= -2:
        status = "yellow"
        label = "🟡 Wait — Conditions Improving"
        sub = "Check again in 24 hours"
        color = "#d97706"
    else:
        status = "red"
        label = "🔴 Do Not Spread"
        sub = "High risk of nutrient loss and watercourse pollution"
        color = "#dc2626"

    # Find next good window
    next_window = None
    for i, d in enumerate(forecast[1:4], start=1):
        day_rain = d.get("rain_mm", 0) or 0
        if day_rain < 5 and smap < 0.30:
            next_window = f"In {i} day{'s' if i > 1 else ''}"
            break

    # Compliance record data
    import datetime as dt
    today_str = dt.datetime.utcnow().strftime("%d %B %Y")

    return {
        "available": True,
        "status": status,
        "label": label,
        "sub": sub,
        "color": color,
        "score": score,
        "legal": False,
        "closed_season": False,
        "days_to_close": season.get("days_to_close"),
        "rain_24h_mm": round(rain_24h, 1),
        "rain_48h_mm": round(rain_48h, 1),
        "soil_moisture": round(smap, 3),
        "soil_temp_6cm": round(soil_temp, 1),
        "air_temp": round(air_temp, 1),
        "reasons_good": reasons_good,
        "reasons_caution": reasons_caution,
        "reasons_bad": reasons_bad,
        "reasons_legal": reasons_legal,
        "next_good_window": next_window,
        "compliance_date": today_str,
        "note": "Irish Nitrates Action Programme · SI 378/2022 · Teagasc guidelines"
    }
