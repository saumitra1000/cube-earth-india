from datetime import datetime, timezone


def _age_days(date_str):
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None

def _age_penalty(age, thresholds):
    if age is None:
        return 2
    for days, penalty in thresholds:
        if age <= days:
            return penalty
    return 5

def _age_label(age):
    if age is None: return "unknown"
    if age == 0:    return "today"
    if age == 1:    return "1 day old"
    return f"{age} days old"


def s2_confidence(ndvi, ndre, cloud_cover, granule_date, obs_count=1):
    score = 10
    reasons = []
    cloud = float(cloud_cover or 100)
    if cloud > 50:
        score -= 4
        reasons.append(f"High cloud cover ({cloud}%)")
    elif cloud > 20:
        score -= 2
        reasons.append(f"Moderate cloud cover ({cloud}%)")
    age = _age_days(granule_date)
    penalty = _age_penalty(age, [(2,0),(7,0.5),(15,1),(30,2),(60,3),(90,4)])
    score -= penalty
    if penalty > 0:
        reasons.append(f"Granule {_age_label(age)} — age penalty -{penalty}")
    if ndvi is None:
        score -= 5
        reasons.append("No NDVI retrieved")
    if ndre is None:
        score -= 1
        reasons.append("No NDRE — chlorophyll confidence reduced")
    level = "high" if score >= 8 else "moderate" if score >= 5 else "low"
    return {"score": round(max(0,score),1), "level": level,
            "sensor": "HLS Sentinel-2", "resolution": "30m",
            "age_days": age, "age_label": _age_label(age),
            "reasons": reasons,
            "literature": "Ali 2016: NDRE r=0.93 with canopy chlorophyll"}


def smap_confidence(sm_surface, sm_rootzone, granule_date):
    score = 10
    reasons = []
    if sm_surface is None:
        score -= 6
        reasons.append("No SMAP surface moisture")
    elif sm_surface < 0 or sm_surface > 0.6:
        score -= 3
        reasons.append("SMAP value out of physical range")
    if sm_rootzone is None:
        score -= 2
        reasons.append("No SMAP rootzone")
    age = _age_days(granule_date)
    penalty = _age_penalty(age, [(2,0),(7,0.5),(14,1),(30,2)])
    score -= penalty
    if penalty > 0:
        reasons.append(f"Granule {_age_label(age)} — age penalty -{penalty}")
    score -= 2
    reasons.append("9km EASE-2 grid — NOT parcel precise")
    reasons.append("One pixel covers ~81km2 — regional indicator only")
    level = "high" if score >= 8 else "moderate" if score >= 5 else "low"
    return {"score": round(max(0,score),1), "level": level,
            "sensor": "SMAP L4", "resolution": "9km",
            "age_days": age, "age_label": _age_label(age),
            "reasons": reasons,
            "literature": "Direct L-band observation — most reliable regional soil moisture"}


def era5_confidence(surface_mean, obs_count):
    score = 10
    reasons = []
    if surface_mean is None:
        score -= 6
        reasons.append("ERA5 data unavailable")
    if obs_count and obs_count < 30:
        score -= 2
        reasons.append(f"Only {obs_count} observations — short season")
    score -= 1
    reasons.append("Model reanalysis — not direct observation")
    reasons.append("9km resolution — NOT parcel precise")
    level = "high" if score >= 8 else "moderate" if score >= 5 else "low"
    return {"score": round(max(0,score),1), "level": level,
            "sensor": "ERA5-Land", "resolution": "9km",
            "age_days": None, "age_label": "current trend",
            "reasons": reasons,
            "literature": "Green 2018: ERA5 used for seasonal moisture trend analysis"}


def s1_confidence(granule_count, latest_date=None, sar_extracted=False):
    score = 7
    reasons = []
    if not granule_count or granule_count == 0:
        score = 0
        reasons.append("No Sentinel-1 granules found")
    elif granule_count < 3:
        score -= 2
        reasons.append(f"Only {granule_count} SAR granules")
    age = _age_days(latest_date)
    penalty = _age_penalty(age, [(2,0),(7,0.5),(15,1),(30,2)])
    score -= penalty
    if penalty > 0:
        reasons.append(f"Latest granule {_age_label(age)}")
    reasons.append("Granule presence confirmed — VV/VH backscatter not yet extracted")
    reasons.append("C-band only — cannot distinguish management intensities")
    reasons.append("Barrett 2014: C+L kappa=0.98 vs C alone kappa=0.87")
    reasons.append("Score capped until pixel extraction implemented")
    score = min(score, 6)  # cap until actual signal extracted
    if sar_extracted:
        score = min(score + 2, 8)  # reward actual extraction
        reasons.insert(0, "VV/VH backscatter extracted via CDSE — signal confirmed")
    level = "high" if score >= 8 else "moderate" if score >= 5 else "low"
    return {"score": round(max(0,score),1), "level": level,
            "sensor": "Sentinel-1 C-band", "resolution": "20m",
            "age_days": age, "age_label": _age_label(age),
            "reasons": reasons,
            "literature": "Barrett 2014: C-band alone kappa=0.87 for grassland classification"}


def ecostress_confidence(celsius, granule_time, landsat_lst=None, landsat_age=None):
    age = _age_days(granule_time)
    if celsius is None:
        # Try Landsat thermal fallback
        if landsat_lst is not None:
            score = 5  # Partial credit for Landsat thermal
            penalty = _age_penalty(landsat_age or 30, [(7,0),(14,0.5),(30,1),(60,2)])
            score -= penalty
            return {"score": round(max(0,score),1), "level": "moderate",
                    "sensor": "Landsat Thermal", "resolution": "30m",
                    "age_days": landsat_age, "age_label": _age_label(landsat_age),
                    "reasons": [f"Landsat LST {landsat_lst}°C — partial thermal coverage",
                                "ECOSTRESS unavailable — Landsat 30m fallback"],
                    "literature": "Hayes 2025: Thermal data limited by cloud in maritime climates"}
        return {"score": 0, "level": "unavailable",
                "sensor": "ECOSTRESS", "resolution": "70m",
                "age_days": None, "age_label": "no data",
                "reasons": ["No valid LST pixel — cloud or fill value",
                            "Ireland Atlantic cloud limits ECOSTRESS availability"],
                "literature": "Hayes 2025: Thermal data limited by cloud in maritime climates"}
    score = 8
    reasons = []
    penalty = _age_penalty(age, [(5,0),(15,1),(30,2),(60,3),(90,4)])
    score -= penalty
    if penalty > 0:
        reasons.append(f"Granule {_age_label(age)} — age penalty -{penalty}")
    level = "high" if score >= 8 else "moderate" if score >= 5 else "low"
    return {"score": round(max(0,score),1), "level": level,
            "sensor": "ECOSTRESS", "resolution": "70m",
            "age_days": age, "age_label": _age_label(age),
            "reasons": reasons,
            "literature": "Hayes 2025: Thermal ET confirms moisture stress independently"}


def parcel_confidence(area_ha, crop, match_distance_m=None, match_quality=None):
    score = 10
    reasons = []

    # Distance penalty — distant parcel match reduces reliability
    if match_distance_m is not None:
        if match_distance_m > 1000:
            score -= 5
            reasons.append(f"Parcel match {match_distance_m}m away — results likely reflect different field")
        elif match_distance_m > 500:
            score -= 3
            reasons.append(f"Parcel match {match_distance_m}m away — spatial uncertainty high")
        elif match_distance_m > 200:
            score -= 1
            reasons.append(f"Parcel match {match_distance_m}m away — minor spatial offset")
        else:
            reasons.append(f"Parcel match {match_distance_m}m — good spatial accuracy")

    if area_ha is None:
        score -= 4
        reasons.append("No parcel area — size penalty unknown")
    elif area_ha < 0.5:
        score -= 6
        reasons.append(f"Micro parcel ({area_ha}ha) — below TaLAM 0.5ha minimum")
        reasons.append("Boundary contamination affects ALL satellite readings")
    elif area_ha < 2.0:
        score -= 3
        reasons.append(f"Small parcel ({area_ha}ha) — boundary contamination likely")
    elif area_ha < 5.0:
        score -= 1
        reasons.append(f"Medium parcel ({area_ha}ha) — minor edge effects")
    if crop is None:
        score -= 2
        reasons.append("No crop declaration in LPIS")
    level = "high" if score >= 8 else "moderate" if score >= 5 else "low"
    return {"score": round(max(0,score),1), "level": level,
            "sensor": "LPIS Parcel", "area_ha": area_ha,
            "reasons": reasons,
            "literature": "TaLAM 2018: Minimum mapping unit 0.5ha for satellite reliability"}


def cross_sensor_agreement(ndvi, smap_surf, era5_surf, s1_granules, rvi=None, vv_db=None, vh_db=None,
                           s2_age=None, ecostress_available=False):
    # Start at 8.0 — sensors measure different things by design
    # 10/10 reserved for all sensors fresh, all consistent
    score = 8.0
    flags = []
    agreements = []

    # Structural penalties — missing or stale sensors reduce max agreement
    if not ecostress_available:
        score -= 0.3
        flags.append("ECOSTRESS unavailable — thermal dimension absent from agreement")
    if s2_age is not None and s2_age > 20:
        penalty = round(min((s2_age - 20) / 50, 0.4), 2)
        score -= penalty
        flags.append(f"Optical data {s2_age} days old — vegetation state may have changed")

    # SMAP vs ERA5 moisture
    if smap_surf is not None and era5_surf is not None:
        diff = abs(smap_surf - era5_surf)
        if diff > 0.15:
            score -= 2
            flags.append(f"SMAP ({smap_surf:.3f}) and ERA5 ({era5_surf:.3f}) disagree by {diff:.3f} m3/m3")
        elif diff > 0.08:
            score -= 0.5
            flags.append(f"Minor moisture disagreement SMAP vs ERA5 ({diff:.3f} m3/m3)")
        else:
            score += 0.5  # active evidence of consistency
            agreements.append(f"SMAP and ERA5 moisture consistent (diff {diff:.3f} m3/m3)")
    else:
        score -= 1
        flags.append("Cannot compare SMAP vs ERA5 — one unavailable")

    # NDVI vs moisture
    if ndvi is not None and smap_surf is not None:
        if ndvi > 0.75 and smap_surf < 0.18:
            score -= 2
            flags.append("High NDVI but low soil moisture — possible deep-root access")
        elif ndvi < 0.35 and smap_surf > 0.38:
            score -= 2
            flags.append("Low vegetation but high moisture — possible waterlogging or recent cut")
        else:
            score += 0.5  # active evidence
            agreements.append(
                    f"Optical signal (NDVI {ndvi:.2f}) and soil moisture "
                    f"({smap_surf:.3f} m3/m3) consistent with current vegetation condition"
                )
    else:
        score -= 0.5
        flags.append("Cannot cross-check NDVI vs moisture — data missing")

    # SAR — granule existence only, no pixel extraction yet
    if not s1_granules or s1_granules == 0:
        score -= 1
        flags.append("No SAR granules found — optical-only assessment")
    else:
        # Granules confirmed but VV/VH backscatter not extracted
        # SAR backscatter via CDSE — use actual values
        if rvi is not None and vv_db is not None and vh_db is not None:
            score += 0.5
            agreements.append(
                f"SAR signal confirmed — VV {vv_db:.1f} dB  VH {vh_db:.1f} dB  RVI {rvi:.2f}"
            )
            if ndvi is not None and abs(rvi - ndvi) < 0.20:
                score += 0.5
                agreements.append(
                    (f"SAR RVI {rvi:.2f} and optical NDVI {ndvi:.2f} — both independently support high vegetation condition"
                     if ndvi > 0.65 else
                     f"SAR RVI {rvi:.2f} and optical NDVI {ndvi:.2f} — both independently support moderate vegetation condition")
                    if not (s2_age and s2_age > 20) else
                    f"SAR RVI {rvi:.2f} and optical NDVI {ndvi:.2f} — broadly consistent, though optical data {s2_age} days old increases uncertainty"
                )
        else:
            agreements.append(f"SAR granules available ({s1_granules}) — backscatter pending")

    score = min(10, max(0, score))
    level = "high" if score >= 8 else "moderate" if score >= 5 else "low"
    note = (flags[0] if flags
            else f"Sensors consistent — {len(agreements)} agreement(s) confirmed")
    return {"score": round(score, 1), "level": level,
            "agreements": agreements, "flags": flags, "note": note}


def freshness_summary(s2_date, smap_date, s1_date, eco_date, landsat_date=None, landsat_age_days=None,
                      sar_latest=None, sar_acquisitions=None):
    def entry(name, date, resolution):
        age = _age_days(date)
        return {"sensor": name,
                "date": str(date)[:10] if date else None,
                "age_days": age, "age_label": _age_label(age),
                "resolution": resolution,
                "freshness": ("current" if age is not None and age <= 3
                              else "recent" if age is not None and age <= 14
                              else "moderate" if age is not None and age <= 30
                              else "stale" if age is not None else "no_data")}

    sar_age = _age_days(sar_latest)
    sar_entry = {
        "sensor":       "Sentinel-1 SAR",
        "date":         str(sar_latest)[:10] if sar_latest else None,
        "age_days":     sar_age,
        "age_label":    _age_label(sar_age),
        "resolution":   "20m",
        "acquisitions": sar_acquisitions,
        "freshness":    ("current" if sar_age is not None and sar_age <= 3
                         else "recent" if sar_age is not None and sar_age <= 14
                         else "moderate" if sar_age is not None and sar_age <= 30
                         else "stale" if sar_age is not None else "no_data"),
        "note": (f"{sar_acquisitions} acquisitions averaged — "
                 "temporal speckle reduction applied"
                 if sar_acquisitions else "no SAR data"),
    }

    return {
        "sentinel2":  entry("Sentinel-2", s2_date, "10m" if s2_date and len(str(s2_date))==10 else "30m"),
        "smap":       entry("SMAP L4",    smap_date, "9km"),
        "sentinel1":  sar_entry,
        "ecostress":  entry("ECOSTRESS", eco_date, "70m") if eco_date else (
            {"sensor": "Landsat Thermal", "date": str(landsat_date)[:10] if landsat_date else None,
             "age_days": landsat_age_days, "age_label": _age_label(landsat_age_days),
             "resolution": "30m",
             "freshness": ("current" if landsat_age_days and landsat_age_days<=3
                          else "recent" if landsat_age_days and landsat_age_days<=14
                          else "stale" if landsat_age_days else "no_data")}
            if landsat_date else entry("ECOSTRESS", None, "70m")),
        "era5":       {"sensor": "ERA5-Land", "age_label": "seasonal context",
                       "resolution": "9km", "freshness": "seasonal_context",
                       "note": "ERA5 provides seasonal trend — not parcel current moisture"},
    }


def explainability(grazing, traffic, slurry, drought, waterlog,
                   ndvi, gcap, surf_use, root_use, slope, drainage,
                   s2_age=None, smap_age=None, era5_age=None):

    def age_note(age):
        if age is None: return ""
        if age <= 1: return " (1 day old)"
        if age <= 7: return f" ({age} days old)"
        return f" ({age} days old — verify current conditions)"

    def build(label, score, reasons):
        return {"label": label, "score": score, "because": reasons}

    grazing_reasons = []
    if grazing is not None:
        if ndvi is not None:
            grazing_reasons.append(
                f"NDVI {ndvi:.2f}{age_note(s2_age)} — "
                f"{'good' if ndvi > 0.65 else 'moderate' if ndvi > 0.45 else 'low'} grass cover")
        if surf_use is not None:
            grazing_reasons.append(
                f"Soil moisture {surf_use:.3f} m3/m3{age_note(smap_age)} — "
                f"{'wet, poaching risk' if surf_use > 0.38 else 'adequate' if surf_use > 0.25 else 'dry'}")
        if slope is not None:
            grazing_reasons.append(
                f"Slope {slope:.1f}deg — "
                f"{'flat, easy access' if slope < 3 else 'moderate slope' if slope < 8 else 'steep'}")
        if waterlog["probability"] != "low":
            grazing_reasons.append(f"Waterlogging {waterlog['probability']} — limits grazing days")

    traffic_reasons = []
    if surf_use is not None:
        traffic_reasons.append(
            f"Surface moisture {surf_use:.3f}{age_note(smap_age)} — "
            f"{'excellent' if surf_use < 0.25 else 'good' if surf_use < 0.30 else 'moderate' if surf_use < 0.35 else 'poor, rutting risk'}")
    if root_use is not None:
        traffic_reasons.append(
            f"Rootzone {root_use:.3f} — "
            f"{'firm' if root_use < 0.28 else 'adequate' if root_use < 0.35 else 'soft'}")
    if slope is not None:
        traffic_reasons.append(
            f"Slope {slope:.1f}deg — "
            f"{'flat' if slope < 2 else 'gentle' if slope < 6 else 'challenging'}")

    slurry_reasons = []
    if surf_use is not None:
        slurry_reasons.append(
            f"Soil moisture {surf_use:.3f}{age_note(smap_age)} — "
            f"{'too wet, leaching risk' if surf_use > 0.40 else 'near capacity, caution' if surf_use > 0.35 else 'acceptable'}")
    if slope is not None and slope > 5:
        slurry_reasons.append(f"Slope {slope:.1f}deg — runoff risk elevated")
    if drainage != "good":
        slurry_reasons.append(f"Drainage {drainage} — increases runoff risk")

    drought_reasons = []
    if surf_use is not None:
        drought_reasons.append(f"Surface moisture {surf_use:.3f} m3/m3{age_note(smap_age)}")
    if root_use is not None:
        drought_reasons.append(f"Rootzone moisture {root_use:.3f} m3/m3")
    if ndvi is not None:
        drought_reasons.append(f"NDVI {ndvi:.2f}{age_note(s2_age)} — vegetation response")

    return {
        "grazing":   build(grazing["label"], grazing["score"], grazing_reasons) if grazing else None,
        "machinery": build(traffic["label"], traffic["score"], traffic_reasons),
        "slurry":    build(slurry["suitable"], None, slurry_reasons) if slurry else None,
        "drought":   build(drought["label"], drought["score"], drought_reasons),
    }


def overall_confidence(s2c, smapc, era5c, s1c, ecoc, parcelc, agreement):
    weights = {"s2": 0.28, "smap": 0.22, "era5": 0.13, "s1": 0.10,
               "eco": 0.08, "parcel": 0.10, "agreement": 0.09}

    # Fix 2: age-weighted S2 score — penalise stale optical data
    s2_age = s2c.get("age_days") or 0
    s2_score = s2c["score"]
    if s2_age > 20:
        s2_score = max(s2_score - round((s2_age - 20) / 10, 1), 0)

    # Fix 3: ECOSTRESS contributes 0 if unavailable — not 5
    # But Landsat thermal partial credit if available
    eco_score = 0 if ecoc["level"] == "unavailable" else ecoc["score"]

    scores = {
        "s2":        s2_score,
        "smap":      smapc["score"],
        "era5":      era5c["score"],
        "s1":        s1c["score"],
        "eco":       eco_score,
        "parcel":    parcelc["score"],
        "agreement": agreement["score"],
    }

    weighted = sum(scores[k] * weights[k] for k in weights)
    if weighted >= 8.5:
        level = "high"
    elif weighted >= 6.5:
        level = "moderate-high"
    elif weighted >= 4.5:
        level = "moderate"
    else:
        level = "low"

    limiting = [k for k, v in {"Sentinel-2": s2c, "SMAP": smapc, "ERA5": era5c,
                                "Sentinel-1": s1c, "ECOSTRESS": ecoc,
                                "Parcel": parcelc}.items()
                if v["level"] in ("low", "unavailable")]
    level_label = level.replace("-", " ").capitalize()
    explanation = (f"{level_label} confidence"
                   + (f" — limiting: {', '.join(limiting)}" if limiting
                      else " — all sensors reliable"))

    # Fix 4: contributions as percentages
    raw_contribs = {k: scores[k] * weights[k] for k in weights}
    total = sum(raw_contribs.values()) or 1
    contributions = {
        "Sentinel-2": {
            "score": round(s2_score, 1),
            "weighted": round(raw_contribs["s2"], 2),
            "percent": round(raw_contribs["s2"] / total * 100, 1),
            "age_days": s2c.get("age_days"),
        },
        "SMAP": {
            "score": round(scores["smap"], 1),
            "weighted": round(raw_contribs["smap"], 2),
            "percent": round(raw_contribs["smap"] / total * 100, 1),
            "age_days": smapc.get("age_days"),
        },
        "ERA5": {
            "score": round(scores["era5"], 1),
            "weighted": round(raw_contribs["era5"], 2),
            "percent": round(raw_contribs["era5"] / total * 100, 1),
        },
        "Sentinel-1": {
            "score": round(scores["s1"], 1),
            "weighted": round(raw_contribs["s1"], 2),
            "percent": round(raw_contribs["s1"] / total * 100, 1),
            "age_days": s1c.get("age_days"),
        },
        "ECOSTRESS": {
            "score": round(eco_score, 1),
            "weighted": round(raw_contribs["eco"], 2),
            "percent": round(raw_contribs["eco"] / total * 100, 1),
            "note": "unavailable" if ecoc["level"] == "unavailable" else None,
        },
        "Parcel": {
            "score": round(scores["parcel"], 1),
            "weighted": round(raw_contribs["parcel"], 2),
            "percent": round(raw_contribs["parcel"] / total * 100, 1),
        },
        "Agreement": {
            "score": round(scores["agreement"], 1),
            "weighted": round(raw_contribs["agreement"], 2),
            "percent": round(raw_contribs["agreement"] / total * 100, 1),
        },
    }

    # Uncertainty breakdown
    uncertainty = []
    if ecoc["level"] == "unavailable":
        uncertainty.append("ECOSTRESS unavailable — thermal dimension absent")
    if s2c.get("age_days") and s2c["age_days"] > 20:
        uncertainty.append(f"Sentinel-2 {s2c['age_days']} days old — vegetation state may have changed")
    if s1c.get("age_days") and s1c["age_days"] > 7:
        uncertainty.append(f"Sentinel-1 {s1c['age_days']} days old — SAR signal temporal lag")
    if agreement["score"] < 8.5:
        uncertainty.append("Moderate sensor disagreement — cross-validate before decisions")

    # What reduced confidence
    reducers = []
    if ecoc["level"] == "unavailable":
        reducers.append({"factor": "ECOSTRESS unavailable", "impact": "thermal layer absent"})
    if s2c.get("age_days") and s2c["age_days"] > 20:
        reducers.append({"factor": f"Sentinel-2 {s2c['age_days']}d old", "impact": "age penalty applied"})
    if s1c.get("age_days") and s1c["age_days"] > 7:
        reducers.append({"factor": f"Sentinel-1 {s1c['age_days']}d old", "impact": "minor age penalty"})

    # What strengthened confidence
    boosters = []
    if agreement["score"] >= 9.0:
        boosters.append({"factor": "Strong sensor agreement", "impact": f"{agreement['score']}/10 cross-sensor consistency"})
    if smapc.get("age_days") is not None and smapc["age_days"] <= 2:
        boosters.append({"factor": "SMAP soil moisture current", "impact": f"{smapc['age_days']} day old L-band observation"})
    if s1c.get("age_days") is not None and s1c["age_days"] <= 3:
        boosters.append({"factor": "SAR signal recent", "impact": f"Sentinel-1 {s1c['age_days']} days old"})
    if s2c.get("age_days") is not None and s2c["age_days"] <= 7:
        boosters.append({"factor": "Optical data fresh", "impact": f"Sentinel-2 {s2c['age_days']} days old"})
    if parcelc.get("score", 0) >= 9:
        boosters.append({"factor": "LPIS parcel matched", "impact": "Official Irish parcel boundary confirmed"})

    return {"score": round(weighted, 1), "level": level, "explanation": explanation,
            "weights": weights, "sensor_scores": scores,
            "contributions": contributions,
            "uncertainty": uncertainty,
            "confidence_reducers": reducers,
            "confidence_boosters": boosters,
            "breakdown": {"sentinel2": s2c, "smap": smapc, "era5": era5c,
                          "sentinel1": s1c, "ecostress": ecoc,
                          "parcel": parcelc, "agreement": agreement}}
