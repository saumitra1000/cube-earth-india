def bbox(lat, lng, km=0.5):
    lat_d = km / 111.32
    lng_d = km / (111.32 * __import__("math").cos(
        lat * __import__("math").pi / 180
    ))
    return {
        "min_lng": lng - lng_d,
        "min_lat": lat - lat_d,
        "max_lng": lng + lng_d,
        "max_lat": lat + lat_d,
    }

def bbox_str(lat, lng, km=0.5):
    b = bbox(lat, lng, km)
    return (
        f"{b['min_lng']:.6f},{b['min_lat']:.6f},"
        f"{b['max_lng']:.6f},{b['max_lat']:.6f}"
    )

def parcel_size_class(area_ha):
    if area_ha is None:
        return "unknown"
    if area_ha < 0.10:
        return "micro"
    if area_ha < 0.50:
        return "small"
    if area_ha < 2.0:
        return "medium_small"
    if area_ha < 10.0:
        return "medium"
    return "large"

def confidence_penalty(size_class):
    return {
        "micro":        0.50,
        "small":        0.68,
        "medium_small": 0.84,
        "medium":       0.95,
        "large":        1.00,
        "unknown":      0.90,
    }.get(size_class, 0.90)
