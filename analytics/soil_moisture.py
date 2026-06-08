def classify_surface(sm):
    if sm is None:
        return "unknown"
    if sm > 0.42:
        return "Above field capacity - waterlogging risk"
    if sm > 0.35:
        return "At field capacity - optimal"
    if sm > 0.28:
        return "Below field capacity - adequate"
    if sm > 0.20:
        return "Dry - N mineralisation reduced"
    return "Very dry - significant stress"

def classify_rootzone(sm):
    if sm is None:
        return "unknown"
    if sm > 0.38:
        return "High rootzone moisture - waterlogging risk"
    if sm > 0.30:
        return "Adequate rootzone moisture"
    if sm > 0.22:
        return "Low rootzone moisture - drought stress possible"
    return "Very low rootzone moisture - severe stress"

def classify_drainage(sm, slope):
    if sm is None:
        return "unknown"
    if sm > 0.38 and (slope or 0) < 2:
        return "poor"
    if sm > 0.28:
        return "moderate"
    return "good"

def n_mineralisation_risk(sm):
    if sm is None:
        return "unknown"
    if sm < 0.25:
        return "High risk - surface too dry for microbial activity"
    if sm < 0.32:
        return "Moderate risk - reduced N mineralisation"
    if sm > 0.42:
        return "Moderate risk - waterlogging suppressing N"
    return "Low risk - adequate moisture for N mineralisation"
