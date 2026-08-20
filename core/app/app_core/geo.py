import math


def calc_utm_zone_str(lat, lon, na_label="N/A"):
    if lat is None or lon is None:
        return f"{na_label} (Coords missing)"
    try:
        if not -80 <= lat <= 84:
            return f"{na_label} (Out of UTM latitude range)"
        zone_num = math.floor((lon + 180) / 6) + 1
        hemi = "N" if lat >= 0 else "S"
        return f"{zone_num}{hemi}"
    except Exception as exc:
        print(f"Error calculating UTM zone string: {exc}")
        return f"{na_label} (Calc error)"


def create_circle_polygon_coords(lat, lon, radius_km, num_segments=36):
    from geopy.distance import great_circle

    center_point = (lat, lon)
    circle_coords = []
    for i in range(num_segments + 1):
        angle = i * (360 / num_segments)
        dest_point = great_circle(kilometers=radius_km).destination(center_point, angle)
        circle_coords.append((dest_point.latitude, dest_point.longitude))
    return circle_coords


def get_utm_epsg(lat, lon):
    if not (-80 <= lat <= 84):
        print(f"WARNING: Latitude {lat} out of standard UTM range (-80 to 84).")
        return None
    zone = math.floor((lon + 180) / 6) + 1
    return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"
