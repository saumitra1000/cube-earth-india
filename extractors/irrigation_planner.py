"""
Irrigation Planner for Indian Crops
Based on FAO-56 Penman-Monteith ET0
Supports: Grapes, Sugarcane, Cotton, Onion, Tomato

Science basis:
- ET0 = reference evapotranspiration (Open-Meteo)
- Kc = crop coefficient per growth stage
- ETc = Kc × ET0 (crop water demand)
- Irrigation need = ETc - effective rainfall
"""
import datetime

# Crop coefficients (Kc) by growth stage — FAO-56
CROP_KC = {
    "grapes": {
        "dormant": 0.30,
        "budbreak": 0.45,
        "flowering": 0.70,
        "berry_development": 0.85,
        "veraison": 0.80,
        "harvest": 0.45,
        "default": 0.70
    },
    "sugarcane": {
        "establishment": 0.40,
        "tillering": 0.80,
        "grand_growth": 1.25,
        "maturity": 0.75,
        "default": 0.85
    },
    "onion": {
        "initial": 0.50,
        "development": 0.70,
        "mid": 1.00,
        "late": 0.75,
        "default": 0.75
    },
    "tomato": {
        "initial": 0.60,
        "development": 0.75,
        "mid": 1.15,
        "late": 0.80,
        "default": 0.90
    },
    "cotton": {
        "initial": 0.45,
        "development": 0.75,
        "mid": 1.15,
        "late": 0.70,
        "default": 0.85
    },
    "default": {"default": 0.75}
}

# Stress thresholds per crop
STRESS_THRESHOLDS = {
    "grapes": {
        "temp_max_alert": 35,  # °C — berry discolouration
        "temp_min_alert": 5,   # °C — frost damage
        "rain_disease_risk": 15,  # mm — downy mildew risk
        "soil_moisture_min": 0.20,
        "soil_moisture_max": 0.38
    },
    "sugarcane": {
        "temp_max_alert": 38,
        "temp_min_alert": 8,
        "rain_disease_risk": 25,
        "soil_moisture_min": 0.25,
        "soil_moisture_max": 0.42
    },
    "onion": {
        "temp_max_alert": 35,
        "temp_min_alert": 7,
        "rain_disease_risk": 20,
        "soil_moisture_min": 0.22,
        "soil_moisture_max": 0.35
    },
    "tomato": {
        "temp_max_alert": 35,
        "temp_min_alert": 10,
        "rain_disease_risk": 20,
        "soil_moisture_min": 0.22,
        "soil_moisture_max": 0.38
    },
    "default": {
        "temp_max_alert": 36,
        "temp_min_alert": 5,
        "rain_disease_risk": 20,
        "soil_moisture_min": 0.20,
        "soil_moisture_max": 0.40
    }
}

def get_grape_stage():
    """Estimate grape growth stage from month"""
    month = datetime.datetime.utcnow().month
    if month in [12, 1]:
        return "dormant"
    elif month == 2:
        return "budbreak"
    elif month == 3:
        return "flowering"
    elif month in [4, 5]:
        return "berry_development"
    elif month == 6:
        return "veraison"
    elif month in [7, 8, 9]:
        return "harvest"
    else:
        return "default"

def calculate_irrigation(weather, soil_moisture, crop="grapes", area_ha=1.0):
    """
    Calculate irrigation need for today and next 7 days
    Returns daily irrigation recommendations in mm
    """
    if not weather or not weather.get("available"):
        return {"available": False, "error": "No weather data"}

    forecast = weather.get("forecast_7d", [])
    current = weather.get("current", {})

    # Get crop parameters
    crop_lower = crop.lower()
    crop_key = "default"
    for k in CROP_KC.keys():
        if k in crop_lower:
            crop_key = k
            break

    kc_table = CROP_KC.get(crop_key, CROP_KC["default"])
    thresholds = STRESS_THRESHOLDS.get(crop_key, STRESS_THRESHOLDS["default"])

    # Get growth stage
    if crop_key == "grapes":
        stage = get_grape_stage()
    else:
        stage = "default"

    kc = kc_table.get(stage, kc_table.get("default", 0.75))

    # Soil moisture
    smap = soil_moisture or 0.25
    sm_min = thresholds["soil_moisture_min"]
    sm_max = thresholds["soil_moisture_max"]

    # Current soil water deficit
    sm_deficit = max(0, (sm_min + sm_max) / 2 - smap)

    # Daily irrigation recommendations
    daily_recs = []
    alerts = []
    total_irrigation_7d = 0

    for i, day in enumerate(forecast[:7]):
        et0 = day.get("et0_mm") or 0
        rain = day.get("rain_mm") or 0
        temp_max = day.get("temp_max") or 30
        temp_min = day.get("temp_min") or 20
        date = day.get("date", "")

        # Crop water demand
        etc = round(kc * et0, 1) if et0 else round(kc * 6.0, 1)

        # Effective rainfall (only 70% usable)
        eff_rain = round(rain * 0.7, 1)

        # Net irrigation need
        net_irr = max(0, round(etc - eff_rain, 1))

        # Add soil deficit to first day
        if i == 0:
            net_irr = round(net_irr + sm_deficit * 100, 1)

        total_irrigation_7d += net_irr

        # Stress alerts
        day_alerts = []
        if temp_max >= thresholds["temp_max_alert"]:
            if crop_key == "grapes":
                day_alerts.append(f"🌡 {temp_max}°C — द्राक्षे गुलाबी होण्याचा धोका (Berry discolouration risk)")
            else:
                day_alerts.append(f"🌡 {temp_max}°C — Heat stress risk")

        if rain >= thresholds["rain_disease_risk"]:
            if crop_key == "grapes":
                day_alerts.append(f"🌧 {rain}mm — डाऊनी मिल्ड्यू धोका (Downy mildew risk)")
            else:
                day_alerts.append(f"🌧 {rain}mm — Disease risk — avoid spraying")

        if day_alerts:
            alerts.extend(day_alerts)

        daily_recs.append({
            "date": date,
            "et0_mm": et0,
            "etc_mm": etc,
            "rain_mm": rain,
            "irrigation_mm": net_irr,
            "temp_max": temp_max,
            "alerts": day_alerts
        })

    # Today's recommendation
    today = daily_recs[0] if daily_recs else {}
    irr_today = today.get("irrigation_mm", 0)

    # Traffic light
    if irr_today > 8:
        status = "red"
        label = "🔴 आज सिंचन करा — तातडीचे (Irrigate Today — Urgent)"
        label_en = "Irrigate Today — Urgent"
    elif irr_today > 4:
        status = "yellow"
        label = "🟡 आज सिंचन करा (Irrigate Today)"
        label_en = "Irrigate Today"
    elif irr_today > 0:
        status = "green"
        label = "🟢 थोडे सिंचन (Light Irrigation)"
        label_en = "Light Irrigation Needed"
    else:
        status = "green"
        label = "🟢 सिंचन नको (No Irrigation Needed)"
        label_en = "No Irrigation Needed"

    # Cost/saving estimate
    # Drip irrigation: ₹2/1000L, 1mm = 10,000L/ha
    water_cost_per_mm = area_ha * 10000 * 2 / 1000  # ₹ per mm per ha
    saving = round(max(0, (irr_today - total_irrigation_7d/7) * water_cost_per_mm), 0)

    return {
        "available": True,
        "crop": crop,
        "growth_stage": stage,
        "kc": kc,
        "status": status,
        "label": label,
        "label_en": label_en,
        "irrigation_today_mm": irr_today,
        "total_7d_mm": round(total_irrigation_7d, 1),
        "soil_moisture": round(smap, 3),
        "daily": daily_recs,
        "alerts": list(set(alerts)),
        "area_ha": area_ha,
        "note": "FAO-56 Penman-Monteith ET0 · Kc crop coefficient method"
    }
