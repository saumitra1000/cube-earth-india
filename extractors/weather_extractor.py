"""
Weather Extractor for India — WeatherAPI.com
Free tier: 1M calls/month, no rate limiting
Covers India, global coverage
"""
import requests
import time

WEATHER_API_KEY = "77658e2037aa46e88cd34301260906"
WEATHER_API_URL = "http://api.weatherapi.com/v1"

# Cache — 1 hour TTL
_weather_cache = {}
CACHE_TTL = 3600

def get_weather_data(lat, lng):
    key = (round(lat, 2), round(lng, 2))
    now = time.time()

    if key in _weather_cache:
        ts, data = _weather_cache[key]
        if now - ts < CACHE_TTL:
            return data

    try:
        # Current + forecast
        r = requests.get(
            f"{WEATHER_API_URL}/forecast.json",
            params={
                "key": WEATHER_API_KEY,
                "q": f"{lat},{lng}",
                "days": 7,
                "aqi": "no",
                "alerts": "no"
            },
            timeout=10
        )
        r.raise_for_status()
        d = r.json()

        current = d.get("current", {})
        forecast_days = d.get("forecast", {}).get("forecastday", [])

        # Build forecast array
        forecast_7d = []
        for fd in forecast_days:
            day = fd.get("day", {})
            forecast_7d.append({
                "date": fd.get("date"),
                "temp_max": day.get("maxtemp_c"),
                "temp_min": day.get("mintemp_c"),
                "rain_mm": day.get("totalprecip_mm"),
                "radiation_mj": None,
                "et0_mm": day.get("uv", 0) * 0.5  # Approximate ET0 from UV
            })

        # Get soil temps from Open-Meteo (separate call, low rate)
        soil_temp_6cm = None
        soil_temp_0cm = None
        try:
            soil_r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "current": "soil_temperature_0cm,soil_temperature_6cm",
                    "forecast_days": 1
                },
                timeout=5
            )
            if soil_r.status_code == 200:
                soil_d = soil_r.json().get("current", {})
                soil_temp_0cm = soil_d.get("soil_temperature_0cm")
                soil_temp_6cm = soil_d.get("soil_temperature_6cm")
        except Exception:
            pass

        # Get ET0 from Open-Meteo separately
        et0_today = None
        try:
            et0_r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "daily": "et0_fao_evapotranspiration,shortwave_radiation_sum",
                    "timezone": "Asia/Kolkata",
                    "forecast_days": 7,
                    "models": "best_match"
                },
                timeout=5
            )
            if et0_r.status_code == 200:
                et0_data = et0_r.json().get("daily", {})
                et0_list = et0_data.get("et0_fao_evapotranspiration", [])
                rad_list = et0_data.get("shortwave_radiation_sum", [])
                for i, fd in enumerate(forecast_7d):
                    if i < len(et0_list):
                        fd["et0_mm"] = et0_list[i]
                    if i < len(rad_list):
                        fd["radiation_mj"] = rad_list[i]
                if et0_list:
                    et0_today = et0_list[0]
        except Exception:
            pass

        # Rain last 7 days
        rain_7d = sum([f.get("rain_mm", 0) or 0 for f in forecast_7d[:7]])
        avg_temp = sum([f.get("temp_max", 20) or 20 for f in forecast_7d[:7]]) / max(len(forecast_7d), 1)
        rad_avg = sum([f.get("radiation_mj", 0) or 0 for f in forecast_7d[:7]]) / max(len(forecast_7d), 1)

        # Growth conditions score
        temp_c = current.get("temp_c", 25)
        score = 5
        if 15 <= avg_temp <= 30:
            score += 2
        if rain_7d > 10:
            score += 1
        if rad_avg > 15:
            score += 2
        score = min(10, score)
        label = "Excellent" if score >= 8 else "Good" if score >= 6 else "Moderate" if score >= 4 else "Poor"

        result = {
            "available": True,
            "source": "WeatherAPI.com",
            "current": {
                "temp_c": temp_c,
                "rain": current.get("precip_mm"),
                "humidity": current.get("humidity"),
                "wind_kph": current.get("wind_kph"),
                "soil_temp_c": soil_temp_0cm,
                "soil_temp_0cm": soil_temp_0cm,
                "soil_temp_6cm": soil_temp_6cm,
            },
            "forecast_7d": forecast_7d,
            "growth_conditions": {
                "score": score,
                "label": label,
                "avg_temp_c": round(avg_temp, 1),
                "rain_7d_mm": round(rain_7d, 1),
                "radiation_mj_day": round(rad_avg, 1)
            }
        }

        # Cache
        _weather_cache[key] = (time.time(), result)
        if len(_weather_cache) > 50:
            oldest = min(_weather_cache, key=lambda k: _weather_cache[k][0])
            del _weather_cache[oldest]

        return result

    except Exception as e:
        return {"available": False, "error": str(e)}
