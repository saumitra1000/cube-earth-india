import requests
from functools import lru_cache
import time

# Simple in-memory cache: (lat_rounded, lng_rounded) -> (timestamp, data)
_weather_cache = {}
CACHE_TTL = 3600  # 1 hour

def get_weather_data(lat, lng):
    """
    Fetch weather from Open-Meteo UKMO 2km model.
    Cached per hour per location to avoid memory/rate issues.
    """
    # Round to 2dp for cache key (~1km precision)
    key = (round(lat, 2), round(lng, 2))
    now = time.time()
    
    if key in _weather_cache:
        ts, data = _weather_cache[key]
        if now - ts < CACHE_TTL:
            return data

    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lng,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,shortwave_radiation_sum,et0_fao_evapotranspiration",
            "current": "temperature_2m,rain,soil_temperature_0cm,soil_temperature_6cm,soil_temperature_18cm",
            "timezone": "Europe/Dublin",
            "past_days": 7,
            "forecast_days": 7,
            "models": "ukmo_seamless"
        }
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        current = data.get("current", {})

        temps_max = daily.get("temperature_2m_max", [])
        temps_min = daily.get("temperature_2m_min", [])
        radiation = daily.get("shortwave_radiation_sum", [])
        et0 = daily.get("et0_fao_evapotranspiration", [])
        rain = daily.get("precipitation_sum", [])
        times = daily.get("time", [])

        # Past 7 days averages
        past_temps = [(temps_max[i] + temps_min[i]) / 2 for i in range(min(7, len(temps_max))) if temps_max[i] is not None and temps_min[i] is not None]
        avg_temp_7d = round(sum(past_temps) / len(past_temps), 1) if past_temps else None

        past_rad = [x for x in radiation[:7] if x is not None]
        total_rad_7d = round(sum(past_rad), 1) if past_rad else 0

        past_rain = [x for x in rain[:7] if x is not None]
        total_rain_7d = round(sum(past_rain), 1) if past_rain else 0

        # 7-day forecast
        forecast = []
        for i in range(7, min(14, len(times))):
            forecast.append({
                "date": times[i],
                "temp_max": temps_max[i] if i < len(temps_max) else None,
                "temp_min": temps_min[i] if i < len(temps_min) else None,
                "rain_mm": rain[i] if i < len(rain) else None,
                "radiation_mj": radiation[i] if i < len(radiation) else None,
                "et0_mm": et0[i] if i < len(et0) else None,
            })

        # Growth score
        score = 0
        if avg_temp_7d:
            score += 5 if 8 <= avg_temp_7d <= 18 else (3 if 5 <= avg_temp_7d < 8 else 1)
        daily_rad = total_rad_7d / 7 if total_rad_7d else 0
        score += 3 if daily_rad >= 12 else (2 if daily_rad >= 8 else 1)
        score += 2 if 10 <= total_rain_7d <= 40 else 1

        label = "Excellent" if score >= 9 else "Good" if score >= 7 else "Moderate" if score >= 5 else "Poor"

        result = {
            "available": True,
            "source": "Open-Meteo UKMO UKV 2km",
            "current": {
                "temp_c": current.get("temperature_2m"),
                "rain_mm": current.get("rain"),
                "soil_temp_c": current.get("soil_temperature_0cm"),
                "soil_temp_6cm": None,
                "soil_temp_18cm": None,
            },
            "past_7d": {
                "avg_temp_c": avg_temp_7d,
                "total_rain_mm": total_rain_7d,
                "total_radiation_mj": total_rad_7d,
            },
            "growth_conditions": {
                "score": score,
                "label": label,
                "avg_temp_c": avg_temp_7d,
                "radiation_mj_day": round(daily_rad, 1),
                "rain_7d_mm": total_rain_7d,
            },
            "forecast_7d": forecast,
        }

        _weather_cache[key] = (now, result)
        # Keep cache small — max 50 locations
        if len(_weather_cache) > 50:
            oldest = min(_weather_cache.keys(), key=lambda k: _weather_cache[k][0])
            del _weather_cache[oldest]

        # Fetch soil temps separately (default model — UK model lacks soil data)
        try:
            soil_r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lng,
                        "current": "soil_temperature_0cm,soil_temperature_6cm,soil_temperature_18cm"},
                timeout=5
            )
            soil_current = soil_r.json().get("current", {})
            if result.get("current"):
                result["current"]["soil_temp_6cm"] = soil_current.get("soil_temperature_6cm")
                result["current"]["soil_temp_18cm"] = soil_current.get("soil_temperature_18cm")
                result["current"]["soil_temp_0cm"] = soil_current.get("soil_temperature_0cm")
        except Exception:
            pass

        return result

    except Exception as e:
        return {"available": False, "error": str(e)}
