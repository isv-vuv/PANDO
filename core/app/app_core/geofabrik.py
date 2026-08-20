import datetime
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen

GEOFABRIK_INDEX_URL = "https://download.geofabrik.de/index-v1.json"


def get_geofabrik_index_info(index_path: str | Path) -> dict:
    """Returns metadata about the local Geofabrik index (exists, age_days, mtime_str)."""
    path = Path(index_path)
    if not path.exists() or path.stat().st_size == 0:
        return {"exists": False, "age_days": None, "mtime_str": None}

    mtime = path.stat().st_mtime
    age_days = round((time.time() - mtime) / (24 * 3600), 1)
    mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%d.%m.%Y")
    return {"exists": True, "age_days": age_days, "mtime_str": mtime_str}


def download_geofabrik_index(target_path: str | Path, user_agent: str = "PANDO V1.0 (Urban-Act Tool)") -> dict:
    """Downloads the latest geofabrik-index.json directly from Geofabrik."""
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(".tmp")

    req = Request(GEOFABRIK_INDEX_URL, headers={"User-Agent": user_agent})
    with urlopen(req, timeout=15) as resp:
        content = resp.read()
        index_data = json.loads(content.decode("utf-8"))
        with open(temp_target, "w", encoding="utf-8") as out:
            json.dump(index_data, out, ensure_ascii=False, indent=2)

    temp_target.replace(target)
    return index_data



def clean_geofabrik_name(name):
    if not isinstance(name, str):
        return "unbekannter_name"

    cleaned_name = re.sub(r"<br\s*/?>", " ", name, flags=re.IGNORECASE)
    cleaned_name = re.sub(r"^[a-z]{2,3}/", "", cleaned_name)
    cleaned_name = cleaned_name.replace("-", " ")
    cleaned_name = re.sub(r"[^\w\s]", " ", cleaned_name)
    cleaned_name = re.sub(r"\s+", " ", cleaned_name.title()).strip()
    return cleaned_name


def get_feature_by_name(name, all_features):
    for feat in all_features:
        if feat.get("properties", {}).get("name") == name:
            return feat
    return None


def get_feature_hierarchy(feature, id_map):
    hierarchy = []
    current = feature
    while current:
        hierarchy.append(current)
        parent_id = current.get("properties", {}).get("parent")
        current = id_map.get(parent_id)
    return hierarchy


def get_top_level_country(feature, id_map):
    if not feature:
        return None
    hierarchy = get_feature_hierarchy(feature, id_map)
    for feat in reversed(hierarchy):
        if "iso3166-1:alpha2" in feat.get("properties", {}):
            return feat
    return None


_PBF_DETAILS_CACHE: dict[tuple, dict] = {}


def get_cached_pbf_details(loc, radius_km: int) -> Optional[dict]:
    """Return cached PBF search results for a location and radius, if available."""
    lat = getattr(loc, "latitude", None)
    lon = getattr(loc, "longitude", None)
    if lat is None or lon is None:
        return None
    key = (round(float(lat), 5), round(float(lon), 5), int(radius_km))
    return _PBF_DETAILS_CACHE.get(key)


def clear_pbf_details_cache() -> None:
    """Clear all cached PBF search results."""
    _PBF_DETAILS_CACHE.clear()


def find_pbf_details(
    loc,
    radius_km,
    geofabrik_index,
    user_agent,
    *,
    force_refresh: bool = False,
    is_cancelled: Optional[Callable[[], bool]] = None,
    log: Optional[Callable[[str], None]] = None,
):
    import math
    import requests
    from geopy.distance import great_circle
    from shapely.geometry import Point, shape
    from shapely.ops import nearest_points

    lat = getattr(loc, "latitude", None)
    lon = getattr(loc, "longitude", None)
    cache_key = None
    if lat is not None and lon is not None:
        cache_key = (round(float(lat), 5), round(float(lon), 5), int(radius_km))
        if not force_refresh and cache_key in _PBF_DETAILS_CACHE:
            if log:
                log(f"Verwende zwischengespeicherte Geofabrik-Regionen für Radius {radius_km} km...")
            return _PBF_DETAILS_CACHE[cache_key]

    if log:
        log(f"Starte Geofabrik-PBF-Suche für Standort (Radius: {radius_km} km)...")

    if not geofabrik_index or "error" in geofabrik_index:
        if log:
            log("Fehler: Geofabrik-Index ist nicht geladen.")
        return {"error": "Geofabrik-Index ist nicht (oder fehlerhaft) geladen."}

    all_features = geofabrik_index.get("features", [])
    id_to_feature_map = {
        feat["properties"]["id"]: feat
        for feat in all_features
        if "id" in feat.get("properties", {})
    }
    selected_point = Point(loc.longitude, loc.latitude)
    search_radius_km = float(radius_km)
    
    # Calculate bounding box buffer in degrees for fast candidate pre-filtering
    lat_deg = search_radius_km / 111.0
    lon_deg = search_radius_km / (111.0 * max(0.1, math.cos(math.radians(loc.latitude))))
    min_x, max_x = loc.longitude - lon_deg, loc.longitude + lon_deg
    min_y, max_y = loc.latitude - lat_deg, loc.latitude + lat_deg

    candidate_regions = []

    for feat in all_features:
        if is_cancelled and is_cancelled():
            return {"pbfs": []}
        props = feat.get("properties", {})
        geom_data = feat.get("geometry")
        if not (props.get("urls", {}).get("pbf") and geom_data and props.get("id") != "europe"):
            continue
        try:
            # Fast Bounding-Box overlap check before creating expensive Shapely geometries
            bbox = geom_data.get("bbox")
            if bbox and len(bbox) == 4:
                b_min_x, b_min_y, b_max_x, b_max_y = bbox
                if max_x < b_min_x or min_x > b_max_x or max_y < b_min_y or min_y > b_max_y:
                    continue

            feature_shape = shape(geom_data)
            # Second Bounding-Box check on shape.bounds if GeoJSON bbox was missing
            s_min_x, s_min_y, s_max_x, s_max_y = feature_shape.bounds
            if max_x < s_min_x or min_x > s_max_x or max_y < s_min_y or min_y > s_max_y:
                continue

            distance = 0
            if not feature_shape.contains(selected_point):
                p1, p2 = nearest_points(selected_point, feature_shape)
                distance = great_circle((p1.y, p1.x), (p2.y, p2.x)).km

            if distance <= search_radius_km:
                candidate_regions.append({"feature": feat, "distance": distance})
                if log:
                    reg_name = clean_geofabrik_name(props.get("name"))
                    log(f"Geofabrik-Region gefunden: {reg_name} (Entfernung: {distance:.1f} km)")
        except Exception:
            continue
    # Build a fast lookup: country_id → country_entry for all countries with iso3166-1:alpha2
    country_by_id = {
        feat["properties"]["id"]: feat
        for feat in all_features
        if feat.get("properties", {}).get("iso3166-1:alpha2")
    }

    def resolve_country(feature):
        """Find the country for a feature via parent chain OR id-prefix (for US-style ids)."""
        # Standard walk: find ancestor with iso3166-1:alpha2
        result = get_top_level_country(feature, id_to_feature_map)
        if result:
            return result
        # Fallback: id-prefix match (e.g. 'us/california' → country 'us')
        fid = feature.get("properties", {}).get("id", "")
        if "/" in fid:
            prefix = fid.split("/")[0]
            if prefix in country_by_id:
                return country_by_id[prefix]
        return None

    countries = {}
    for item in candidate_regions:
        if is_cancelled and is_cancelled():
            return {"pbfs": []}
        feature = item["feature"]
        distance = item["distance"]
        region_props = feature.get("properties", {})

        country_feature = resolve_country(feature)
        if not country_feature:
            continue

        country_props = country_feature.get("properties", {})
        country_id = country_props.get("id")

        if country_id not in countries:
            loc_country_code = loc.raw.get("address", {}).get("country_code", "xx").lower() if hasattr(loc, "raw") and isinstance(loc.raw, dict) else "xx"
            index_country_code = country_props.get("iso3166-1:alpha2", [""])[0].lower() if country_props.get("iso3166-1:alpha2") else ""
            countries[country_id] = {
                "id": country_id,
                "name": clean_geofabrik_name(country_props.get("name")),
                "pbf_url": country_props.get("urls", {}).get("pbf"),
                "osm_id": country_id,
                "is_primary": loc_country_code == index_country_code,
                "min_distance": float("inf"),
                "sub_regions": [],
            }

        if distance <= search_radius_km:
            countries[country_id]["min_distance"] = min(countries[country_id]["min_distance"], distance)

        region_id = region_props.get("id")
        is_country_itself = region_id == country_id
        # Sub-region: direct parent (Germany-style) OR id-prefix (US-style: 'us/california')
        is_direct_child = region_props.get("parent") == country_id
        is_prefix_child = region_id.startswith(country_id + "/") if region_id and country_id else False
        if not is_country_itself and (is_direct_child or is_prefix_child) and distance <= search_radius_km:
            region_info = {
                "id": region_id,
                "name": clean_geofabrik_name(region_props.get("name")),
                "pbf_url": region_props.get("urls", {}).get("pbf"),
                "osm_id": region_id,
                "distance": distance,
            }
            if not any(sub["id"] == region_id for sub in countries[country_id]["sub_regions"]):
                countries[country_id]["sub_regions"].append(region_info)


    final_list = sorted(countries.values(), key=lambda item: (not item["is_primary"], item["min_distance"]))

    for country_data in final_list:
        if country_data["sub_regions"]:
            country_data["sub_regions"].sort(key=lambda item: item.get("distance", float("inf")))

    if log:
        log(f"Ermittle Dateigrößen von Geofabrik ({len(final_list)} Länder/Regionen)...")

    def fetch_sizes(regions):
        for region in regions:
            if is_cancelled and is_cancelled():
                return False
            size_bytes = 0
            if region.get("pbf_url"):
                try:
                    response = requests.head(
                        region["pbf_url"],
                        headers={"User-Agent": user_agent},
                        timeout=5,
                        allow_redirects=True,
                    )
                    size_bytes = int(response.headers.get("Content-Length", 0))
                except Exception:
                    pass
            region["size_bytes"] = size_bytes
            if region.get("sub_regions"):
                if not fetch_sizes(region["sub_regions"]):
                    return False
        return True

    fetch_sizes(final_list)

    result = {"pbfs": final_list}
    if cache_key is not None:
        _PBF_DETAILS_CACHE[cache_key] = result

    if log:
        log(f"PBF-Suche erfolgreich abgeschlossen ({len(final_list)} Geofabrik-Treffer).")

    return result
