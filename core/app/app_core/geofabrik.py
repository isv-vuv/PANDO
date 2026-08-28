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


def download_geofabrik_index(
    target_path: str | Path,
    user_agent: str = "PANDO V1.0 (Urban-Act Tool)",
    *,
    update_sizes: bool = True,
) -> dict:
    """Downloads the latest geofabrik-index.json directly from Geofabrik and updates geofabrik_sizes.json."""
    global _URL_SIZE_CACHE
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

    if update_sizes:
        try:
            from core.scripts.osm.update_geofabrik_index_and_sizes import crawl_geofabrik_sizes
            sizes_path = target.parent / "geofabrik_sizes.json"
            crawled_sizes = crawl_geofabrik_sizes(user_agent=user_agent)
            if crawled_sizes:
                final_dict = {}
                for f in index_data.get("features", []):
                    pbf = f.get("properties", {}).get("urls", {}).get("pbf")
                    if not pbf:
                        continue
                    fn = os.path.basename(pbf).lower()
                    if pbf in crawled_sizes:
                        final_dict[pbf] = crawled_sizes[pbf]
                    else:
                        for k, v in crawled_sizes.items():
                            if os.path.basename(k).lower() == fn:
                                final_dict[pbf] = v
                                break
                if final_dict:
                    with open(sizes_path, "w", encoding="utf-8") as out:
                        json.dump(final_dict, out, ensure_ascii=False, indent=2)
                    _URL_SIZE_CACHE = _load_sizes_index()
        except Exception:
            pass

    return index_data



def normalize_pbf_name(filename: str) -> str:
    stem = str(filename).lower().removesuffix(".osm.pbf")
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", stem)
    while re.search(r"-(latest|\d{6}|\d{8})$", stem):
        stem = re.sub(r"-(latest|\d{6}|\d{8})$", "", stem)
    return stem


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


CONTINENT_IDS = {
    "africa", "antarctica", "asia", "australia-oceania", "central-america",
    "europe", "north-america", "south-america", "russia"
}


def get_top_level_country(feature, id_map):
    if not feature:
        return None
    hierarchy = get_feature_hierarchy(feature, id_map)
    for feat in reversed(hierarchy):
        if "iso3166-1:alpha2" in feat.get("properties", {}):
            return feat
    # If no ISO alpha-2 found (e.g. Kosovo, Isle of Man), pick the topmost non-continent entity
    for feat in reversed(hierarchy):
        props = feat.get("properties", {})
        fid = props.get("id")
        parent = props.get("parent")
        if fid not in CONTINENT_IDS and (parent in CONTINENT_IDS or parent is None):
            return feat
    return None


def parse_size_str_to_bytes(size_str: str) -> Optional[int]:
    """Converts strings like '(51 MB)', '(51&nbsp;MB)', '3.8 GB', '82 MB', '520 KB' into integer bytes."""
    clean = str(size_str).replace("&nbsp;", " ").replace("\xa0", " ").strip()
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*([kKMmGgTt]?[bB])", clean)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2).upper()
    if "G" in unit:
        return int(val * (1024 ** 3))
    elif "M" in unit:
        return int(val * (1024 ** 2))
    elif "K" in unit:
        return int(val * 1024)
    elif "T" in unit:
        return int(val * (1024 ** 4))
    return int(val)


def scrape_sizes_from_geofabrik_html(html_text: str, page_url: str) -> dict[str, int]:
    """Extracts .osm.pbf URLs and their file sizes from Geofabrik HTML tables (e.g. td[3])."""
    from urllib.parse import urljoin
    from core.app.app_core.project import normalize_pbf_name

    found = {}
    normalized_html = str(html_text).replace("&nbsp;", " ").replace("\xa0", " ")

    # 1. Row-by-row table parsing
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", normalized_html, re.IGNORECASE | re.DOTALL)
    for row in rows:
        m_pbf = re.search(r'href=["\']([^"\']+\.osm\.pbf)["\']', row, re.IGNORECASE)
        if not m_pbf:
            continue
        pbf_href = m_pbf.group(1).strip()
        full_pbf_url = urljoin(page_url, pbf_href)

        # Look for size pattern in the row: e.g. '(51 MB)', '450 MB', '3.8 GB'
        m_size = re.search(r'\(?\b([0-9]+(?:[.,][0-9]+)?\s*[kKMmGgTt]?[bB])\b\)?', row, re.IGNORECASE)
        if m_size:
            size_str = m_size.group(1)
            size_bytes = parse_size_str_to_bytes(size_str)
            if size_bytes and size_bytes > 0:
                found[full_pbf_url.lower()] = size_bytes
                fn = os.path.basename(pbf_href).lower()
                norm = normalize_pbf_name(fn)
                found[fn] = size_bytes
                found[norm] = size_bytes
                found[pbf_href.lower()] = size_bytes

    return found


SIZES_CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "osm" / "geofabrik_sizes.json"


def _load_sizes_index() -> dict[str, int]:
    if SIZES_CACHE_FILE.exists():
        try:
            with open(SIZES_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                res = {}
                for k, v in data.items():
                    res[k.lower()] = int(v)
                    res[os.path.basename(k).lower()] = int(v)
                return res
        except Exception:
            pass
    return {}


_PBF_DETAILS_CACHE: dict[tuple, dict] = {}
_URL_SIZE_CACHE: dict[str, int] = _load_sizes_index()


def _extract_lat_lon(loc) -> tuple[Optional[float], Optional[float]]:
    if loc is None:
        return None, None
    if isinstance(loc, dict):
        lat = loc.get("latitude") if loc.get("latitude") is not None else loc.get("lat")
        lon = loc.get("longitude") if loc.get("longitude") is not None else loc.get("lon")
    else:
        lat = getattr(loc, "latitude", None)
        if lat is None:
            lat = getattr(loc, "lat", None)
        lon = getattr(loc, "longitude", None)
        if lon is None:
            lon = getattr(loc, "lon", None)
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            return None, None
    return None, None


def get_cached_pbf_details(loc, radius_km: int) -> Optional[dict]:
    """Return cached PBF search results for a location and radius, if available."""
    lat, lon = _extract_lat_lon(loc)
    if lat is None or lon is None:
        return None
    key = (round(float(lat), 5), round(float(lon), 5), int(radius_km))
    return _PBF_DETAILS_CACHE.get(key)


def clear_pbf_details_cache() -> None:
    """Clear all cached PBF search results."""
    _PBF_DETAILS_CACHE.clear()
    _URL_SIZE_CACHE.clear()


US_SUBGROUPS = {
    "us-midwest": [
        "us/illinois", "us/indiana", "us/iowa", "us/kansas", "us/michigan",
        "us/minnesota", "us/missouri", "us/nebraska", "us/north-dakota",
        "us/ohio", "us/south-dakota", "us/wisconsin"
    ],
    "us-northeast": [
        "us/connecticut", "us/delaware", "us/district-of-columbia", "us/maine",
        "us/maryland", "us/massachusetts", "us/new-hampshire", "us/new-jersey",
        "us/new-york", "us/pennsylvania", "us/rhode-island", "us/vermont"
    ],
    "us-pacific": [
        "us/alaska", "us/hawaii", "us/washington"
    ],
    "us-south": [
        "us/alabama", "us/arkansas", "us/florida", "us/georgia", "us/kentucky",
        "us/louisiana", "us/mississippi", "us/north-carolina", "us/oklahoma",
        "us/south-carolina", "us/tennessee", "us/texas", "us/virginia",
        "us/west-virginia"
    ],
    "us-west": [
        "us/arizona", "us/california", "us/colorado", "us/idaho", "us/montana",
        "us/nevada", "us/new-mexico", "us/oregon", "us/utah", "us/washington",
        "us/wyoming"
    ],
}
STATE_TO_US_SUBGROUP = {st: sg for sg, states in US_SUBGROUPS.items() for st in states}


_SHAPES_INDEX_CACHE = {}


def _get_or_build_shapes_cache(all_features: list) -> list:
    """Builds and caches Shapely geometry objects, bounding boxes, and pre-resolved countries."""
    cache_id = id(all_features)
    if cache_id in _SHAPES_INDEX_CACHE:
        return _SHAPES_INDEX_CACHE[cache_id]

    from shapely.geometry import shape

    id_to_feature_map = {
        feat["properties"]["id"]: feat
        for feat in all_features
        if "id" in feat.get("properties", {})
    }
    country_by_id = {
        feat["properties"]["id"]: feat
        for feat in all_features
        if feat.get("properties", {}).get("iso3166-1:alpha2")
    }

    def _resolve_country(feature):
        fid = feature.get("properties", {}).get("id", "")
        if fid == "us" or fid in US_SUBGROUPS or fid.startswith("us/"):
            return id_to_feature_map.get("us")
        result = get_top_level_country(feature, id_to_feature_map)
        if result:
            return result
        if "/" in fid:
            prefix = fid.split("/")[0]
            if prefix in country_by_id:
                return country_by_id[prefix]
        return None

    built = []
    for feat in all_features:
        props = feat.get("properties", {})
        geom_data = feat.get("geometry")
        if not (props.get("urls", {}).get("pbf") and geom_data and props.get("id") != "europe"):
            continue
        try:
            geom = shape(geom_data)
            c_feat = _resolve_country(feat)
            built.append((feat, geom, geom.bounds, c_feat))
        except Exception:
            continue

    _SHAPES_INDEX_CACHE[cache_id] = built
    return built


def find_pbf_details(
    loc,
    radius_km,
    geofabrik_index,
    user_agent,
    *,
    force_refresh: bool = False,
    is_cancelled: Optional[Callable[[], bool]] = None,
    log: Optional[Callable[[str], None]] = None,
    on_regions_found: Optional[Callable[[dict], None]] = None,
    on_size_updated: Optional[Callable[[str, int], None]] = None,
):
    import math
    from geopy.distance import great_circle
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    lat, lon = _extract_lat_lon(loc)
    if lat is None or lon is None:
        if log:
            log("Fehler: Ungültiger Standort angegeben.")
        return {"error": "Ungültiger Standort angegeben."}

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
    selected_point = Point(lon, lat)
    search_radius_km = float(radius_km)
    
    # Calculate bounding box buffer in degrees for fast candidate pre-filtering
    lat_deg = search_radius_km / 111.0
    lon_deg = search_radius_km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    min_x, max_x = lon - lon_deg, lon + lon_deg
    min_y, max_y = lat - lat_deg, lat + lat_deg

    candidate_regions = []
    shapes_list = _get_or_build_shapes_cache(all_features)

    for feat, feature_shape, (s_min_x, s_min_y, s_max_x, s_max_y), country_feature in shapes_list:
        if is_cancelled and is_cancelled():
            return {"pbfs": []}
        try:
            # 1. Instant check: If the point is directly inside the polygon (e.g. current country / state)
            if feature_shape.contains(selected_point):
                candidate_regions.append({"feature": feat, "distance": 0.0, "country": country_feature})
                if log:
                    reg_name = clean_geofabrik_name(feat.get("properties", {}).get("name"))
                    log(f"Geofabrik-Region gefunden: {reg_name} (Standort liegt innerhalb)")
                continue

            # 2. Fast Bounding-Box overlap check
            if max_x < s_min_x or min_x > s_max_x or max_y < s_min_y or min_y > s_max_y:
                continue

            # 3. Exact distance check for nearby regions
            p1, p2 = nearest_points(selected_point, feature_shape)
            distance = great_circle((p1.y, p1.x), (p2.y, p2.x)).km

            if distance <= search_radius_km:
                candidate_regions.append({"feature": feat, "distance": distance, "country": country_feature})
                if log:
                    reg_name = clean_geofabrik_name(feat.get("properties", {}).get("name"))
                    log(f"Geofabrik-Region gefunden: {reg_name} (Entfernung: {distance:.1f} km)")
        except Exception:
            continue

    countries = {}
    id_to_feature_map = {
        feat["properties"]["id"]: feat
        for feat in all_features
        if "id" in feat.get("properties", {})
    }
    for item in candidate_regions:
        if is_cancelled and is_cancelled():
            return {"pbfs": []}
        feature = item["feature"]
        distance = item["distance"]
        region_props = feature.get("properties", {})
        country_feature = item.get("country")
        if not country_feature:
            continue

        country_props = country_feature.get("properties", {})
        country_id = country_props.get("id")

        if country_id not in countries:
            loc_country_code = loc.raw.get("address", {}).get("country_code", "xx").lower() if hasattr(loc, "raw") and isinstance(loc.raw, dict) else "xx"
            index_country_codes = [c.lower() for c in country_props.get("iso3166-1:alpha2", [])] if country_props.get("iso3166-1:alpha2") else []
            if country_id == "kosovo":
                index_country_codes.append("xk")
            is_primary = (loc_country_code in index_country_codes) or (loc_country_code == country_id)
            countries[country_id] = {
                "id": country_id,
                "name": clean_geofabrik_name(country_props.get("name")),
                "pbf_url": country_props.get("urls", {}).get("pbf"),
                "osm_id": country_id,
                "is_primary": is_primary,
                "min_distance": float("inf"),
                "sub_regions": [],
            }

        if distance <= search_radius_km:
            countries[country_id]["min_distance"] = min(countries[country_id]["min_distance"], distance)

        region_id = region_props.get("id")
        is_country_itself = region_id == country_id

        if country_id == "us":
            if is_country_itself:
                continue
            if region_id in US_SUBGROUPS:
                # Subgroup entry (e.g. us-south)
                sg_entry = next((s for s in countries["us"]["sub_regions"] if s["id"] == region_id), None)
                if not sg_entry:
                    sg_entry = {
                        "id": region_id,
                        "name": clean_geofabrik_name(region_props.get("name")),
                        "pbf_url": region_props.get("urls", {}).get("pbf"),
                        "osm_id": region_id,
                        "distance": distance,
                        "sub_regions": [],
                    }
                    countries["us"]["sub_regions"].append(sg_entry)
                else:
                    sg_entry["distance"] = min(sg_entry["distance"], distance)
                    if not sg_entry.get("pbf_url"):
                        sg_entry["pbf_url"] = region_props.get("urls", {}).get("pbf")
            elif region_id.startswith("us/"):
                sg_id = STATE_TO_US_SUBGROUP.get(region_id)
                if sg_id:
                    sg_entry = next((s for s in countries["us"]["sub_regions"] if s["id"] == sg_id), None)
                    if not sg_entry:
                        sg_feat = id_to_feature_map.get(sg_id, {})
                        sg_props = sg_feat.get("properties", {})
                        sg_entry = {
                            "id": sg_id,
                            "name": clean_geofabrik_name(sg_props.get("name", sg_id)),
                            "pbf_url": sg_props.get("urls", {}).get("pbf"),
                            "osm_id": sg_id,
                            "distance": distance,
                            "sub_regions": [],
                        }
                        countries["us"]["sub_regions"].append(sg_entry)
                    else:
                        sg_entry["distance"] = min(sg_entry["distance"], distance)

                    state_entry = {
                        "id": region_id,
                        "name": clean_geofabrik_name(region_props.get("name")),
                        "pbf_url": region_props.get("urls", {}).get("pbf"),
                        "osm_id": region_id,
                        "distance": distance,
                    }
                    if not any(s["id"] == region_id for s in sg_entry["sub_regions"]):
                        sg_entry["sub_regions"].append(state_entry)
                else:
                    # Territory directly under US (e.g. Puerto Rico)
                    territory_entry = {
                        "id": region_id,
                        "name": clean_geofabrik_name(region_props.get("name")),
                        "pbf_url": region_props.get("urls", {}).get("pbf"),
                        "osm_id": region_id,
                        "distance": distance,
                    }
                    if not any(s["id"] == region_id for s in countries["us"]["sub_regions"]):
                        countries["us"]["sub_regions"].append(territory_entry)
        else:
            # Standard country (e.g. Germany, France, Canada)
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
            for sub in country_data["sub_regions"]:
                if sub.get("sub_regions"):
                    sub["sub_regions"].sort(key=lambda item: item.get("distance", float("inf")))

    # Pre-populate known sizes from cache
    def _collect_all_regions(items: list) -> list:
        res = []
        for r in items:
            res.append(r)
            if r.get("sub_regions"):
                res.extend(_collect_all_regions(r["sub_regions"]))
        return res

    all_target_regions = _collect_all_regions(final_list)
    for r in all_target_regions:
        r_url = str(r.get("pbf_url") or "").lower()
        fn = os.path.basename(r_url).lower()
        norm = normalize_pbf_name(fn)
        oid = str(r.get("osm_id") or r.get("id") or "").lower()
        r["size_bytes"] = _URL_SIZE_CACHE.get(r_url) or _URL_SIZE_CACHE.get(fn) or _URL_SIZE_CACHE.get(norm) or _URL_SIZE_CACHE.get(oid) or 0

    # Yield candidate regions immediately
    if on_regions_found:
        on_regions_found({"pbfs": final_list})

    result = {"pbfs": final_list}
    if cache_key is not None:
        _PBF_DETAILS_CACHE[cache_key] = result

    if log:
        log(f"PBF-Suche erfolgreich abgeschlossen ({len(final_list)} Geofabrik-Treffer).")

    return result
