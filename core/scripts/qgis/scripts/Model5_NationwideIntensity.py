# -*- coding: utf-8 -*-
"""Nationwide intensity estimation and intra-municipal centrality point selection.

Uses Model 2 central place points as seeds and applies a cKDTree greedy exclusion
algorithm on nationwide OSM POI intensity maxima, incorporating exact Model 5
weight-to-radius mapping (Weight 4 -> 500m, Weight 3 -> 250m, Weight 2 -> 175m, Weight 1 -> 100m)
and the Model 5 saturation formula (5 * (1 - exp(-0.2773 * A))).
Generates individual and overlaid sector POI point layers and intensity heatmaps
with full QGIS QML styling, exact Model 5 POI classification, 250m duplicate resolution,
and metric coordinate projections.
"""

from __future__ import annotations

import gc
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence


def group() -> str:
    return 'PANDO'


def groupId() -> str:
    return 'pando'

try:
    import numpy as np
except ImportError:
    np = None

try:
    from scipy.spatial import cKDTree
except ImportError:
    cKDTree = None

try:
    from osgeo import gdal, osr
    gdal.SetConfigOption("OGR_GEOMETRY_ACCEPT_UNCLOSED_RING", "YES")
    gdal.SetConfigOption("CPL_LOG_ERRORS", "OFF")
    gdal.PushErrorHandler("CPLQuietErrorHandler")
except ImportError:
    gdal = None
    osr = None

try:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point
except ImportError:
    gpd = None
    pd = None
    Point = None


# --------------------------------------------------------------------------------------------------
# WEIGHT_RULES: Sortiert nach Gewicht absteigend (4 -> 3 -> 2 -> 1) für echtes Max-Weight
# --------------------------------------------------------------------------------------------------
# Bei Objekten mit mehreren zutreffenden OSM-Tags (z.B. "amenity=public_bath" [Gewicht 3] und
# "leisure=sports_centre" [Gewicht 2]) wird durch diese Sortierung garantiert immer das
# HÖCHSTMÖGLICHE GEWICHT (hier: 3) zugewiesen.
# --------------------------------------------------------------------------------------------------

WEIGHT_RULES = (
    # --- Gewicht 4 (Radius 500 m) ---
    ("amenity", "courthouse", 4),
    ("amenity", "university", 4),
    ("shop", "mall", 4),
    ("tourism", "aquarium", 4),
    ("amenity", "conference_centre", 4),
    ("amenity", "concert_hall", 4),
    ("tourism", "gallery", 4),
    ("amenity", "exhibition_centre", 4),
    ("tourism", "museum", 4),
    ("amenity", "theatre", 4),
    ("amenity", "planetarium", 4),
    ("leisure", "stadium", 4),
    ("tourism", "theme_park", 4),
    ("amenity", "events_venue", 4),
    ("tourism", "zoo", 4),

    # --- Gewicht 3 (Radius 250 m) ---
    ("office", "government", 3),
    ("office", "employment_agency", 3),
    ("amenity", "college", 3),
    ("amenity", "marketplace", 3),
    ("amenity", "library", 3),
    ("leisure", "water_park", 3),
    ("amenity", "cinema", 3),
    ("amenity", "music_school", 3),
    ("amenity", "public_bath", 3),
    ("amenity", "community_centre", 3),
    ("amenity", "clinic", 3),
    ("healthcare", "clinic", 3),
    ("amenity", "hospital", 3),
    ("healthcare", "hospital", 3),

    # --- Gewicht 2 (Radius 175 m) ---
    ("amenity", "townhall", 2),
    ("amenity", "school", 2),
    ("amenity", "kindergarten", 2),
    ("shop", "department_store", 2),
    ("shop", "chemist", 2),
    ("shop", "general", 2),
    ("shop", "beverages", 2),
    ("shop", "supermarket", 2),
    ("leisure", "sports_centre", 2),
    ("amenity", "place_of_worship", 2),
    ("leisure", "sports_hall", 2),
    ("amenity", "pharmacy", 2),
    ("healthcare", "pharmacy", 2),
    ("amenity", "doctors", 2),
    ("healthcare", "doctor", 2),
    ("amenity", "dentist", 2),
    ("healthcare", "dentist", 2),
    ("healthcare", "centre", 2),
    ("amenity", "social_facility", 2),
    ("amenity", "nursing_home", 2),
    ("amenity", "youth_centre", 2),
    ("amenity", "social_centre", 2),

    # --- Gewicht 1 (Radius 100 m) ---
    ("amenity", "bank", 1),
    ("amenity", "post_office", 1),
    ("shop", "bakery", 1),
    ("shop", "butcher", 1),
    ("shop", "convenience", 1),
    ("shop", "kiosk", 1),
)

WEIGHT_MAPPING = {(k, v): w for k, v, w in WEIGHT_RULES}

SECTOR_NAMES = ("GOVERNMENT", "EDUCATION", "RETAIL", "LEISURE", "HEALTHCARE")

# Model 5 exact Weight to Radius mapping in meters
WEIGHT_TO_RADIUS = {
    4: 500,
    3: 250,
    2: 175,
    1: 100,
    0: 0,
}


def get_radius_for_weight(weight: int) -> int:
    """Return Model 5 radius in meters corresponding to the given weight."""
    return WEIGHT_TO_RADIUS.get(int(weight), 0)


def get_sector(tags: Mapping[str, object]) -> Optional[str]:
    """Exact Model 5 Sector Field Calculator."""
    amenity = str(tags.get("amenity") or "").strip().lower()
    office = str(tags.get("office") or "").strip().lower()
    shop = str(tags.get("shop") or "").strip().lower()
    tourism = str(tags.get("tourism") or "").strip().lower()
    leisure = str(tags.get("leisure") or "").strip().lower()
    healthcare = str(tags.get("healthcare") or "").strip().lower()

    if amenity in ('courthouse', 'townhall', 'bank', 'post_office') or office in ('government', 'employment_agency'):
        return "GOVERNMENT"
    if amenity in ('university', 'college', 'school', 'kindergarten'):
        return "EDUCATION"
    if shop in ('mall', 'department_store', 'chemist', 'general', 'beverages', 'supermarket', 'bakery', 'butcher', 'convenience', 'kiosk') or amenity == 'marketplace':
        return "RETAIL"
    if tourism in ('aquarium', 'gallery', 'museum', 'theme_park', 'zoo') or leisure in ('stadium', 'water_park', 'sports_centre', 'sports_hall') or amenity in ('conference_centre', 'concert_hall', 'exhibition_centre', 'theatre', 'planetarium', 'events_venue', 'library', 'cinema', 'music_school', 'public_bath', 'community_centre', 'place_of_worship'):
        return "LEISURE"
    if amenity in ('clinic', 'hospital', 'pharmacy', 'doctors', 'dentist', 'social_facility', 'nursing_home', 'youth_centre', 'social_centre') or healthcare in ('clinic', 'hospital', 'pharmacy', 'doctor', 'dentist', 'centre'):
        return "HEALTHCARE"
    return None


def compute_classification(sector: str, tags: Mapping[str, object], search_string: str) -> Optional[str]:
    """Exact Model 5 classification (tiers) with search string regex and ISCED levels."""
    amenity = str(tags.get("amenity") or "").strip().lower()
    office = str(tags.get("office") or "").strip().lower()
    shop = str(tags.get("shop") or "").strip().lower()
    tourism = str(tags.get("tourism") or "").strip().lower()
    leisure = str(tags.get("leisure") or "").strip().lower()
    healthcare = str(tags.get("healthcare") or "").strip().lower()
    isced = str(tags.get("isced:level") or "").strip().lower()

    if sector == "EDUCATION":
        if re.search(r'[5-8]', isced) or amenity in ('university', 'college', 'research_institute') or re.search(r'(universit|hochschule|akademie|fakult|college|academy|faculty|polytechnic|seminary)', search_string):
            return "F"
        if re.match(r'^[01;\-]+$', isced) or amenity == 'kindergarten' or re.search(r'(grundschule|primarschule|kindergarten|kita|vorschule|primary|elementary|nursery|maternelle|preescolar)', search_string):
            return "D"
        return "E"

    if sector == "GOVERNMENT":
        if amenity == 'courthouse' or re.search(r'(gericht|justiz|court|tribunal|juzgado|суд)', search_string):
            return "C"
        if amenity == 'social_facility' or office in ('government', 'employment_agency', 'lawyer', 'notary') or re.search(r'(amt|behörde|bureau|administration|администрация)', search_string):
            return "B"
        return "A"

    if sector == "HEALTHCARE":
        if amenity == 'hospital' or healthcare in ('hospital', 'hospice', 'rehabilitation') or re.search(r'(krankenhaus|klinikum|spital|hospital|hôpital|больница|医院|病院)', search_string):
            return "O"
        if amenity == 'pharmacy' or healthcare == 'pharmacy' or shop in ('chemist', 'drugstore') or re.search(r'(apotheke|pharmacy|chemist|drugstore|pharmacie|farmacia|аптека|药店|薬局)', search_string):
            return "M"
        return "N"

    if sector == "LEISURE":
        if leisure in ('stadium', 'water_park') or tourism in ('theme_park', 'zoo', 'aquarium', 'attraction') or amenity in ('conference_centre', 'exhibition_centre', 'events_venue') or re.search(r'(stadion|stadium|zoo|freizeitpark|theme park|messe|exhibition|parc d\'attraction)', search_string):
            return "L"
        if leisure in ('sports_centre', 'sports_hall') or amenity in ('cinema', 'theatre', 'public_bath', 'arts_centre', 'concert_hall', 'planetarium', 'music_school') or tourism == 'museum' or re.search(r'(kino|cinema|theater|theatre|museum|musée|sporthalle|sports centre|体育中心)', search_string):
            return "K"
        return "J"

    if sector == "RETAIL":
        if shop in ('mall', 'department_store') or re.search(r'(einkaufszentrum|kaufhaus|shopping cent|department store|mall|commercial|торговый центр)', search_string):
            return "T"
        if shop in ('clothes', 'electronics', 'furniture', 'doityourself', 'books', 'shoes', 'sports', 'general') or re.search(r'(baumarkt|möbelhaus|fachmarkt|diy|bricolage|furniture|electronics|fashion|одежда)', search_string):
            return "S"
        return "R"

    return None


def get_description(tags: Mapping[str, object]) -> str:
    """Exact Model 5 Description Field Calculator."""
    amenity = str(tags.get("amenity") or "").strip().lower()
    office = str(tags.get("office") or "").strip().lower()
    shop = str(tags.get("shop") or "").strip().lower()
    tourism = str(tags.get("tourism") or "").strip().lower()
    leisure = str(tags.get("leisure") or "").strip().lower()
    healthcare = str(tags.get("healthcare") or "").strip().lower()

    if amenity == 'courthouse': return 'Court of law for legal proceedings'
    if office == 'government': return 'Government office or public administration'
    if office == 'employment_agency': return 'Employment or job center'
    if office == 'lawyer': return 'Lawyer or legal office'
    if amenity == 'bank': return 'Bank or financial institution'
    if office == 'notary': return 'Notary public office'
    if amenity == 'post_office': return 'Post office for mail services'
    if amenity == 'townhall': return 'Town hall or city administration'
    if amenity == 'social_facility': return 'Social or care facility'
    if amenity == 'university': return 'University or higher education'
    if amenity == 'college': return 'College or vocational school'
    if amenity == 'kindergarten': return 'Kindergarten or preschool'
    if amenity == 'school': return 'Primary or secondary school'
    if shop == 'mall': return 'Shopping mall with many stores'
    if shop == 'department_store': return 'Large department store'
    if amenity == 'marketplace': return 'Marketplace or weekly market'
    if shop == 'chemist': return 'Chemist or drugstore'
    if shop == 'general': return 'General or mixed goods store'
    if shop == 'beverages': return 'Beverage or drinks shop'
    if shop == 'supermarket': return 'Supermarket for groceries'
    if shop == 'bakery': return 'Bakery for bread and pastries'
    if shop == 'butcher': return 'Butcher shop for meat'
    if shop == 'convenience': return 'Convenience store for daily needs'
    if shop == 'kiosk': return 'Kiosk or small booth'
    if tourism == 'aquarium': return 'Aquarium for aquatic life'
    if amenity == 'conference_centre': return 'Conference or convention center'
    if amenity == 'concert_hall': return 'Concert hall for live music'
    if amenity == 'arts_centre': return 'Arts or cultural center'
    if tourism == 'gallery': return 'Art gallery for exhibitions'
    if amenity == 'exhibition_centre': return 'Exhibition or trade fair center'
    if tourism == 'museum': return 'Museum for history or art'
    if amenity == 'theatre': return 'Theatre or opera house'
    if amenity == 'planetarium': return 'Planetarium for astronomy shows'
    if tourism == 'attraction': return 'Tourist attraction or landmark'
    if leisure == 'stadium': return 'Stadium for sports and events'
    if tourism == 'theme_park': return 'Theme park or amusement park'
    if amenity == 'events_venue': return 'Events or performance venue'
    if tourism == 'zoo': return 'Zoo for animals'
    if amenity == 'library': return 'Library for books and study'
    if leisure == 'water_park': return 'Water park or outdoor pool'
    if amenity == 'cinema': return 'Cinema or movie theater'
    if amenity == 'music_school': return 'Music school for lessons'
    if leisure == 'sports_centre': return 'Sports center or gym'
    if amenity == 'public_bath': return 'Public bath or spa'
    if amenity == 'community_centre': return 'Community or activity center'
    if amenity == 'youth_centre': return 'Youth or children’s center'
    if amenity == 'place_of_worship': return 'Religious building or church'
    if leisure == 'sports_hall': return 'Indoor sports hall'
    if leisure == 'pitch': return 'Sports pitch or field'
    if amenity == 'social_centre': return 'Social meeting center'
    if amenity == 'clinic' or healthcare == 'clinic': return 'Clinic for outpatient care'
    if amenity == 'hospital' or healthcare == 'hospital': return 'Hospital for medical care'
    if amenity == 'pharmacy' or healthcare == 'pharmacy': return 'Pharmacy for medicine'
    if amenity == 'doctors' or healthcare == 'doctor': return 'Doctors’ office or practice'
    if amenity == 'dentist' or healthcare == 'dentist': return 'Dentist office'
    if healthcare == 'centre': return 'Medical center'
    return 'Other'


def get_sector_for_tag(k: str, v: str) -> Optional[str]:
    """Map primary tag (key, value) directly to functional sector."""
    if (k == 'amenity' and v in ('courthouse', 'townhall', 'bank', 'post_office')) or (k == 'office' and v in ('government', 'employment_agency')):
        return "GOVERNMENT"
    if k == 'amenity' and v in ('university', 'college', 'school', 'kindergarten'):
        return "EDUCATION"
    if (k == 'shop' and v in ('mall', 'department_store', 'chemist', 'general', 'beverages', 'supermarket', 'bakery', 'butcher', 'convenience', 'kiosk')) or (k == 'amenity' and v == 'marketplace'):
        return "RETAIL"
    if (k == 'tourism' and v in ('aquarium', 'gallery', 'museum', 'theme_park', 'zoo')) or (k == 'leisure' and v in ('stadium', 'water_park', 'sports_centre', 'sports_hall')) or (k == 'amenity' and v in ('conference_centre', 'concert_hall', 'exhibition_centre', 'theatre', 'planetarium', 'events_venue', 'library', 'cinema', 'music_school', 'public_bath', 'community_centre', 'place_of_worship')):
        return "LEISURE"
    if (k == 'amenity' and v in ('clinic', 'hospital', 'pharmacy', 'doctors', 'dentist', 'social_facility', 'nursing_home', 'youth_centre', 'social_centre')) or (k == 'healthcare' and v in ('clinic', 'hospital', 'pharmacy', 'doctor', 'dentist', 'centre')):
        return "HEALTHCARE"
    return None


def get_description_for_tag(k: str, v: str) -> Optional[str]:
    """Map primary tag (key, value) directly to description string."""
    mapping = {
        ("amenity", "courthouse"): "Court of law for legal proceedings",
        ("office", "government"): "Government office or public administration",
        ("office", "employment_agency"): "Employment or job center",
        ("amenity", "townhall"): "Town hall or city administration",
        ("amenity", "bank"): "Bank or financial institution",
        ("amenity", "post_office"): "Post office for mail services",
        ("amenity", "university"): "University or higher education",
        ("amenity", "college"): "College or vocational school",
        ("amenity", "school"): "Primary or secondary school",
        ("amenity", "kindergarten"): "Kindergarten or preschool",
        ("shop", "mall"): "Shopping mall with many stores",
        ("shop", "department_store"): "Large department store",
        ("amenity", "marketplace"): "Marketplace or weekly market",
        ("shop", "chemist"): "Chemist or drugstore",
        ("shop", "general"): "General or mixed goods store",
        ("shop", "beverages"): "Beverage or drinks shop",
        ("shop", "supermarket"): "Supermarket for groceries",
        ("shop", "bakery"): "Bakery for bread and pastries",
        ("shop", "butcher"): "Butcher shop for meat",
        ("shop", "convenience"): "Convenience store for daily needs",
        ("shop", "kiosk"): "Kiosk or small booth",
        ("tourism", "aquarium"): "Aquarium for aquatic life",
        ("amenity", "conference_centre"): "Conference or convention center",
        ("amenity", "concert_hall"): "Concert hall for live music",
        ("tourism", "gallery"): "Art gallery for exhibitions",
        ("amenity", "exhibition_centre"): "Exhibition or trade fair center",
        ("tourism", "museum"): "Museum for history or art",
        ("amenity", "theatre"): "Theatre or opera house",
        ("amenity", "planetarium"): "Planetarium for astronomy shows",
        ("leisure", "stadium"): "Stadium for sports and events",
        ("tourism", "theme_park"): "Theme park or amusement park",
        ("amenity", "events_venue"): "Events or performance venue",
        ("tourism", "zoo"): "Zoo for animals",
        ("amenity", "library"): "Library for books and study",
        ("leisure", "water_park"): "Water park or outdoor pool",
        ("amenity", "cinema"): "Cinema or movie theater",
        ("amenity", "music_school"): "Music school for lessons",
        ("leisure", "sports_centre"): "Sports center or gym",
        ("amenity", "public_bath"): "Public bath or spa",
        ("amenity", "community_centre"): "Community or activity center",
        ("amenity", "place_of_worship"): "Religious building or church",
        ("leisure", "sports_hall"): "Indoor sports hall",
        ("amenity", "clinic"): "Clinic for outpatient care",
        ("healthcare", "clinic"): "Clinic for outpatient care",
        ("amenity", "hospital"): "Hospital for medical care",
        ("healthcare", "hospital"): "Hospital for medical care",
        ("amenity", "pharmacy"): "Pharmacy for medicine",
        ("healthcare", "pharmacy"): "Pharmacy for medicine",
        ("amenity", "doctors"): "Doctors’ office or practice",
        ("healthcare", "doctor"): "Doctors’ office or practice",
        ("amenity", "dentist"): "Dentist office",
        ("healthcare", "dentist"): "Dentist office",
        ("healthcare", "centre"): "Medical center",
        ("amenity", "social_facility"): "Social or care facility",
        ("amenity", "nursing_home"): "Nursing home or elder care",
        ("amenity", "youth_centre"): "Youth or children’s center",
        ("amenity", "social_centre"): "Social meeting center",
    }
    return mapping.get((k, v))


def classify_poi_feature(tags: Mapping[str, object]) -> tuple[Optional[str], int, int, str, str, Optional[str], str]:
    """Return (sector, weight, radius, primary_tag, secondary_tag, classification, description) for a given OSM tag dictionary.
    
    Evaluates WEIGHT_RULES (sorted 4 -> 3 -> 2 -> 1) ensuring every entity receives the maximum possible weight.
    Also identifies secondary_tag if another valid POI tag exists.
    """
    weight = 0
    primary_tag = ""
    primary_k = ""
    primary_v = ""
    matching_tags = []

    for k, v, w in WEIGHT_RULES:
        val = str(tags.get(k) or "").strip().lower()
        if val == v:
            matching_tags.append(f"{k}={v}")
            if weight == 0:
                weight = w
                primary_tag = f"{k}={v}"
                primary_k = k
                primary_v = v

    if weight <= 0:
        return None, 0, 0, "", "", None, "Other"

    # Identify secondary tag (if present)
    secondary_tag = ""
    other_matches = [t for t in matching_tags if t != primary_tag]
    if other_matches:
        secondary_tag = other_matches[0]
    else:
        # Check other relevant sector tag keys
        for cat in ("amenity", "shop", "tourism", "leisure", "office", "healthcare"):
            val_cat = str(tags.get(cat) or "").strip().lower()
            if val_cat and f"{cat}={val_cat}" != primary_tag:
                secondary_tag = f"{cat}={val_cat}"
                break

    radius = get_radius_for_weight(weight)
    sector = get_sector_for_tag(primary_k, primary_v) or get_sector(tags)
    if not sector:
        return None, 0, 0, "", "", None, "Other"

    # Search string from multiple name fields
    search_parts = [
        str(tags.get("name") or ""),
        str(tags.get("name:en") or ""),
        str(tags.get("name:de") or ""),
        str(tags.get("name:es") or ""),
        str(tags.get("name:fr") or ""),
        str(tags.get("name:pt") or ""),
        str(tags.get("name:ru") or ""),
        str(tags.get("name:ar") or ""),
        str(tags.get("name:zh") or ""),
        str(tags.get("name:ja") or ""),
        str(tags.get("name:hi") or ""),
        str(tags.get("alt_name") or ""),
        str(tags.get("official_name") or ""),
    ]
    search_string = " ".join(p.strip().lower() for p in search_parts if p.strip())

    classification = compute_classification(sector, tags, search_string)
    description = get_description_for_tag(primary_k, primary_v) or get_description(tags)

    return sector, weight, radius, primary_tag, secondary_tag, classification, description


def extract_osm_id(tags: Mapping[str, object]) -> str:
    """Extract a clean OSM ID string checking osm_id, osm_way_id, osm_relation_id, @id, id."""
    raw_id = (
        tags.get("osm_id")
        or tags.get("osm_way_id")
        or tags.get("osm_relation_id")
        or tags.get("@id")
        or tags.get("id")
    )
    if raw_id is None or (pd is not None and pd.isna(raw_id)):
        return "unknown"
    val = str(raw_id).strip()
    if val.endswith(".0"):
        val = val[:-2]
    return val if val else "unknown"


def extract_clean_str(val: object) -> str:
    """Extract a clean string representation, returning empty string for nan/None/null."""
    if val is None or (pd is not None and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null", "<na>"):
        return ""
    return s


def clean_name_base(name_str: object) -> str:
    """Extract cleaned base name matching Model 5 duplicate filter regex (first 3 words without entrance/extension suffixes).

    HINWEIS / TODO (Option 4 - Zukünftige Verfeinerung):
    Für zukünftige Versionen kann hier eine sprachunabhängige Fuzzy-String-Ähnlichkeitsprüfung
    (z. B. Levenshtein / Token-Set-Ratio > 85-90 %) implementiert werden, um z. B. 'Produkte Mishi - X'
    und 'Produkte Mishi - Y' oder 'Klinike Dentare - A' und 'Klinike Dentare - B' nicht als Duplikat
    zu werten, wenn nur das generische Präfix übereinstimmt, und die Duplizierung primär auf
    Punkt-vs-Polygon-Verschneidungen zu beschränken.
    """
    cleaned = extract_clean_str(name_str)
    if not cleaned or len(cleaned) < 2:
        return ""
    name_clean = re.sub(r'\b(entrance|extention|extension|annex|main|campus)\b', '', cleaned.lower(), flags=re.IGNORECASE)
    name_clean = re.sub(r'\s(i{1,3}|iv|v|[0-9]+)$', '', name_clean.strip(), flags=re.IGNORECASE)
    words = [w for w in name_clean.strip().split() if w not in ("nan", "none", "null")]
    if not words:
        return ""
    base = " ".join(words[:3])
    return base if len(base) >= 2 else ""


def deduplicate_pois_250m(gdf: gpd.GeoDataFrame, log: Callable[[str], None] = print) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Deduplicate POIs within 250m in projected CRS having the same sector and base name, matching Model 5.
    Returns (gdf_annotated_before, gdf_clean_after).
    """
    if len(gdf) <= 1:
        gdf_annotated = gdf.copy()
        gdf_annotated["is_dropped"] = False
        gdf_annotated["drop_reason"] = ""
        gdf_annotated["dropped_by_osm_id"] = ""
        gdf_annotated["dropped_by_name"] = ""
        gdf_annotated["dropped_by_distance_m"] = 0.0
        return gdf_annotated, gdf

    coords = np.array([[p.x, p.y] for p in gdf.geometry]) if np is not None else [[p.x, p.y] for p in gdf.geometry]
    if cKDTree is not None and np is not None:
        tree = cKDTree(coords)
        def find_near(idx):
            return tree.query_ball_point(coords[idx], 250.0)
    else:
        from shapely import STRtree
        geoms = list(gdf.geometry)
        tree = STRtree(geoms)
        def find_near(idx):
            pt = geoms[idx]
            return tree.query(pt.buffer(250.0))

    to_drop: dict[int, tuple[int, str, float]] = {}

    is_poly = gdf.get("is_polygon", pd.Series(False, index=gdf.index)).values
    weights = gdf.get("Weight", pd.Series(1, index=gdf.index)).values
    name_bases = [clean_name_base(x) for x in gdf.get("name_base", pd.Series("", index=gdf.index)).values]
    sectors = gdf.get("sector", pd.Series("", index=gdf.index)).values

    for i in range(len(gdf)):
        if i in to_drop:
            continue
        n_base_i = name_bases[i]
        # Unnamed POIs (no name) must NEVER be deduplicated against each other!
        if not n_base_i or len(n_base_i) < 2:
            continue
        sector_i = sectors[i]
        near_indices = find_near(i)

        for j in near_indices:
            if i == j or j in to_drop:
                continue
            n_base_j = name_bases[j]
            if not n_base_j or len(n_base_j) < 2:
                continue
            if sectors[j] == sector_i and n_base_j == n_base_i:
                dist = float(np.hypot(coords[i][0] - coords[j][0], coords[i][1] - coords[j][1]))
                # Same name and sector within 250m: keep polygon / higher weight
                if is_poly[i] and not is_poly[j]:
                    to_drop[j] = (i, "Polygon bevorzugt vor Punkt (gleicher Name)", round(dist, 1))
                elif is_poly[j] and not is_poly[i]:
                    to_drop[i] = (j, "Polygon bevorzugt vor Punkt (gleicher Name)", round(dist, 1))
                    break
                elif weights[i] >= weights[j]:
                    to_drop[j] = (i, "Höheres oder gleiches Gewicht bevorzugt (gleicher Name)", round(dist, 1))
                else:
                    to_drop[i] = (j, "Höheres Gewicht bevorzugt (gleicher Name)", round(dist, 1))
                    break

    gdf_annotated = gdf.copy()
    is_dropped_col = np.zeros(len(gdf), dtype=bool) if np is not None else [False] * len(gdf)
    reasons = [""] * len(gdf)
    by_osm_ids = [""] * len(gdf)
    by_names = [""] * len(gdf)
    dists = [0.0] * len(gdf)

    osm_ids = [extract_clean_str(x) for x in gdf.get("osm_id", pd.Series("", index=gdf.index)).values]
    names = [extract_clean_str(x) for x in gdf.get("name", pd.Series("", index=gdf.index)).values]

    for dropped_idx, (winner_idx, reason, dist) in to_drop.items():
        is_dropped_col[dropped_idx] = True
        reasons[dropped_idx] = reason
        by_osm_ids[dropped_idx] = str(osm_ids[winner_idx])
        by_names[dropped_idx] = str(names[winner_idx])
        dists[dropped_idx] = dist

    gdf_annotated["is_dropped"] = is_dropped_col
    gdf_annotated["drop_reason"] = reasons
    gdf_annotated["dropped_by_osm_id"] = by_osm_ids
    gdf_annotated["dropped_by_name"] = by_names
    gdf_annotated["dropped_by_distance_m"] = dists

    if to_drop:
        log(f"  250 m Duplikatfilter (analog Modell 5): {len(to_drop):,} redundante Punkt/Polygon-Doppelungen entfernt.")
        gdf_clean = gdf_annotated[~gdf_annotated["is_dropped"]].drop(
            columns=["is_dropped", "drop_reason", "dropped_by_osm_id", "dropped_by_name", "dropped_by_distance_m"]
        ).reset_index(drop=True)
    else:
        gdf_clean = gdf.copy().reset_index(drop=True)

    return gdf_annotated, gdf_clean


def load_or_extract_nationwide_pois(
    pois_or_pbf: str | Path,
    *,
    poi_points_path: Optional[str | Path] = None,
    poi_polygons_path: Optional[str | Path] = None,
    osmium_exe: Optional[str | Path] = None,
    local_crs: str = "EPSG:3857",
    debug_dir: Optional[str | Path] = None,
    return_both: bool = False,
    log: Callable[[str], None] = print,
    progress: Optional[Callable[[int], None]] = None,
) -> gpd.GeoDataFrame | tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load POIs from 03_features GeoPackages or extract them from PBF, reprojecting to local_crs."""
    records = []
    source_crs = "EPSG:4326"

    # 1. First priority: Check if model5_pois_nationwide_cleaned.gpkg exists in 03_features
    if hasattr(pois_or_pbf, "parent"):
        f_dir = Path(pois_or_pbf).parent.parent / "03_features"
        cand_nationwide = f_dir / "model5_pois_nationwide_cleaned.gpkg"
        cand_all = f_dir / "model5_pois_nationwide_all.gpkg"
        if cand_nationwide.is_file():
            log(f"Lade vorverarbeitete landesweite POIs aus {cand_nationwide.name} ...")
            gdf_clean = gpd.read_file(str(cand_nationwide))
            if gdf_clean.crs is None or str(gdf_clean.crs).upper() != str(local_crs).upper():
                gdf_clean = gdf_clean.to_crs(local_crs)
            if return_both:
                gdf_all = gpd.read_file(str(cand_all)) if cand_all.is_file() else gdf_clean
                if gdf_all.crs is None or str(gdf_all.crs).upper() != str(local_crs).upper():
                    gdf_all = gdf_all.to_crs(local_crs)
                return gdf_all, gdf_clean
            return gdf_clean

    # 2. Check if direct already-cleaned GPKG path passed
    if Path(pois_or_pbf).is_file() and Path(pois_or_pbf).name == "model5_pois_nationwide_cleaned.gpkg":
        log(f"Lade vorverarbeitete landesweite POIs aus {Path(pois_or_pbf).name} ...")
        gdf_clean = gpd.read_file(str(pois_or_pbf))
        if gdf_clean.crs is None or str(gdf_clean.crs).upper() != str(local_crs).upper():
            gdf_clean = gdf_clean.to_crs(local_crs)
        if return_both:
            return gdf_clean, gdf_clean
        return gdf_clean

    # 3. Check legacy feature layers
    pts_file = Path(poi_points_path) if poi_points_path and Path(poi_points_path).is_file() else None
    poly_file = Path(poi_polygons_path) if poi_polygons_path and Path(poi_polygons_path).is_file() else None

    if pts_file and poly_file:
        log(f"Lade POIs aus vorhandenen Feature-Layern ({pts_file.name}, {poly_file.name}) ...")
        gdf_pts = gpd.read_file(str(pts_file))
        source_crs = gdf_pts.crs or "EPSG:4326"
        for _, r in gdf_pts.iterrows():
            tags = r.to_dict()
            sector, weight, radius, primary_tag, secondary_tag, classification, description = classify_poi_feature(tags)
            if sector and weight > 0:
                name_val = extract_clean_str(tags.get("name") or tags.get("name:en"))
                osm_id_val = extract_osm_id(tags)
                records.append({
                    "name": name_val,
                    "name_base": clean_name_base(name_val),
                    "sector": sector,
                    "classification": classification,
                    "description": description,
                    "primary_tag": primary_tag,
                    "secondary_tag": secondary_tag,
                    "Weight": weight,
                    "Radius": radius,
                    "osm_id": osm_id_val,
                    "is_polygon": False,
                    "geometry": r.geometry,
                })

        gdf_poly = gpd.read_file(str(poly_file))
        for _, r in gdf_poly.iterrows():
            tags = r.to_dict()
            sector, weight, radius, primary_tag, secondary_tag, classification, description = classify_poi_feature(tags)
            if sector and weight > 0:
                name_val = extract_clean_str(tags.get("name") or tags.get("name:en"))
                osm_id_val = extract_osm_id(tags)
                geom = r.geometry
                pt = geom.centroid if geom.geom_type != "Point" else geom
                records.append({
                    "name": name_val,
                    "name_base": clean_name_base(name_val),
                    "sector": sector,
                    "classification": classification,
                    "description": description,
                    "primary_tag": primary_tag,
                    "secondary_tag": secondary_tag,
                    "Weight": weight,
                    "Radius": radius,
                    "osm_id": osm_id_val,
                    "is_polygon": True,
                    "geometry": pt,
                })

    elif Path(pois_or_pbf).suffix.lower() == ".pbf":
        pbf_path = Path(pois_or_pbf)
        source_crs = "EPSG:4326"
        target_pbf = pbf_path
        temp_poi_pbf = None

        if osmium_exe and Path(osmium_exe).is_file():
            temp_poi_pbf = pbf_path.parent / f"temp_pois_filtered_{pbf_path.stem}.osm.pbf"
            log(f"Filtere relevante OSM-POIs schnell mit Osmium vor ({pbf_path.name}) ...")
            cmd = [
                str(osmium_exe), "tags-filter", str(pbf_path),
                "nwr/amenity=courthouse,townhall,bank,post_office,university,college,school,kindergarten,marketplace,conference_centre,concert_hall,exhibition_centre,theatre,planetarium,events_venue,library,cinema,music_school,public_bath,community_centre,place_of_worship,clinic,hospital,pharmacy,doctors,dentist,social_facility,nursing_home,youth_centre,social_centre",
                "nwr/office=government,employment_agency",
                "nwr/shop=mall,department_store,chemist,general,beverages,supermarket,bakery,butcher,convenience,kiosk",
                "nwr/tourism=aquarium,gallery,museum,theme_park,zoo",
                "nwr/leisure=stadium,water_park,sports_centre,sports_hall",
                "nwr/healthcare=clinic,hospital,pharmacy,doctor,dentist,centre",
                "-o", str(temp_poi_pbf), "--overwrite"
            ]
            try:
                env = os.environ.copy()
                env["PATH"] = str(Path(osmium_exe).parent) + os.pathsep + env.get("PATH", "")
                kwargs = {"env": env, "capture_output": True, "text": True}
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                res = subprocess.run(cmd, **kwargs)
                if res.returncode == 0 and temp_poi_pbf.is_file() and temp_poi_pbf.stat().st_size > 0:
                    target_pbf = temp_poi_pbf
                    log(f"Osmium-Vorfilterung abgeschlossen ({target_pbf.stat().st_size / (1024*1024):.1f} MB). Lese POI-Layer ...")
                else:
                    log(f"Lese landesweite POIs direkt aus OSM-PBF ({pbf_path.name}) ...")
            except Exception as exc:
                log(f"Hinweis: Osmium-Vorfilterung übersprungen ({exc}), lese PBF direkt ...")
        else:
            log(f"Lese landesweite POIs direkt aus OSM-PBF ({pbf_path.name}) ...")

        for layer_name in ("points", "multipolygons"):
            try:
                gdf_layer = gpd.read_file(str(target_pbf), layer=layer_name)
            except Exception as exc:
                log(f"Hinweis bei Layer {layer_name}: {exc}")
                continue

            if gdf_layer.empty:
                continue

            is_polygon_layer = (layer_name == "multipolygons")
            for _, row in gdf_layer.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                if geom.geom_type != "Point":
                    pt = geom.centroid
                else:
                    pt = geom

                tags = row.to_dict()
                other_tags = tags.get("other_tags")
                if isinstance(other_tags, str):
                    parsed = dict(re.findall(r'"(.*?)"=>"(.*?)"', other_tags))
                    tags.update(parsed)

                sector, weight, radius, primary_tag, secondary_tag, classification, description = classify_poi_feature(tags)
                if sector and weight > 0:
                    name_val = extract_clean_str(tags.get("name") or tags.get("name:en"))
                    osm_id_val = extract_osm_id(tags)
                    records.append({
                        "name": name_val,
                        "name_base": clean_name_base(name_val),
                        "sector": sector,
                        "classification": classification,
                        "description": description,
                        "primary_tag": primary_tag,
                        "secondary_tag": secondary_tag,
                        "Weight": weight,
                        "Radius": radius,
                        "osm_id": osm_id_val,
                        "is_polygon": bool(is_polygon_layer),
                        "geometry": pt,
                    })

        if temp_poi_pbf and temp_poi_pbf.is_file():
            try:
                temp_poi_pbf.unlink()
            except Exception:
                pass

    else:
        # Load from GPKG directly
        gdf_raw = gpd.read_file(str(pois_or_pbf))
        source_crs = gdf_raw.crs or "EPSG:4326"
        for _, r in gdf_raw.iterrows():
            tags = r.to_dict()
            sector, weight, radius, primary_tag, secondary_tag, classification, description = classify_poi_feature(tags)
            if not sector and "sector" in tags and tags["sector"]:
                sector = str(tags["sector"]).upper()
                weight = int(tags.get("Weight") or tags.get("weight") or 2)
                radius = int(tags.get("Radius") or tags.get("radius") or get_radius_for_weight(weight))
                primary_tag = tags.get("primary_tag") or f"sector={sector}"
            if sector and weight > 0:
                name_val = extract_clean_str(tags.get("name") or tags.get("name:en"))
                osm_id_val = extract_osm_id(tags)
                geom = r.geometry
                pt = geom.centroid if geom.geom_type != "Point" else geom
                records.append({
                    "name": name_val,
                    "name_base": clean_name_base(name_val),
                    "sector": sector,
                    "classification": classification,
                    "description": description,
                    "primary_tag": primary_tag,
                    "secondary_tag": secondary_tag,
                    "Weight": weight,
                    "Radius": radius,
                    "osm_id": osm_id_val,
                    "is_polygon": bool(geom.geom_type != "Point"),
                    "geometry": pt,
                })

    if not records:
        raise RuntimeError("Keine relevanten POIs in den Eingabedaten gefunden.")

    df_pois = gpd.GeoDataFrame(records, geometry="geometry", crs=source_crs)
    df_pois["fid"] = list(range(1, len(df_pois) + 1))

    # Reproject to local projected CRS before spatial deduplication
    if df_pois.crs is None or str(df_pois.crs).upper() != str(local_crs).upper():
        df_pois_proj = df_pois.to_crs(local_crs)
    else:
        df_pois_proj = df_pois

    count_pts = int((~df_pois_proj["is_polygon"]).sum())
    count_polys = int((df_pois_proj["is_polygon"]).sum())
    log(f"Extrahierte POIs vor Duplikatsbereinigung: {len(df_pois_proj):,} (Punkte: {count_pts:,}, Polygone: {count_polys:,})")

    df_annotated_before, df_clean = deduplicate_pois_250m(df_pois_proj, log=log)
    df_clean = df_clean.copy()
    df_clean["fid"] = list(range(1, len(df_clean) + 1))

    # Save debug layer before deduplication (with competitor fields: is_dropped, drop_reason, dropped_by_osm_id, etc.)
    if debug_dir:
        debug_before = Path(debug_dir) / "pois_before_deduplication.gpkg"
        safe_write_gpkg(df_annotated_before, debug_before, "pois_before_deduplication")
        log(f"Debug-Layer vor Duplikatsbereinigung gespeichert: {debug_before.name} (inkl. Konkurrenten-Referenz)")

    # Save debug layer after deduplication
    if debug_dir:
        debug_after = Path(debug_dir) / "pois_after_deduplication.gpkg"
        safe_write_gpkg(df_clean, debug_after, "pois_after_deduplication")
        log(f"Debug-Layer nach Duplikatsbereinigung gespeichert: {debug_after.name}")

    if return_both:
        return df_annotated_before, df_clean
    return df_clean


def parse_poly_file_to_polygon(poly_path: str | Path):
    """Parse Osmosis .poly file format into a Shapely Polygon or MultiPolygon."""
    from shapely.geometry import Polygon, MultiPolygon

    poly_path = Path(poly_path)
    if not poly_path.is_file():
        return None

    rings = []
    current_ring = []
    with open(poly_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.lower().startswith("none") or line.upper() == "END":
                if current_ring:
                    if len(current_ring) >= 3:
                        rings.append(current_ring)
                    current_ring = []
                continue
            parts = line.split()
            if len(parts) == 2:
                try:
                    lon, lat = float(parts[0]), float(parts[1])
                    current_ring.append((lon, lat))
                except ValueError:
                    pass

    if not rings:
        return None

    polygons = [Polygon(r) for r in rings if len(r) >= 3]
    if not polygons:
        return None
    if len(polygons) == 1:
        return polygons[0]
    return MultiPolygon(polygons)


def prepare_model5_poi_datasets(
    merged_pbf: str | Path,
    poly_study_area: Optional[str | Path] = None,
    features_dir: str | Path = None,
    local_crs: str = "EPSG:3857",
    osmium_exe: Optional[str | Path] = None,
    log: Callable[[str], None] = print,
    progress: Optional[Callable[[int], None]] = None,
) -> dict[str, Path]:
    """Extract all POIs landesweit, deduplicate, and clip to study area.
    
    Generates in features_dir (usually processed/osm/03_features):
      1. model5_pois_nationwide_all.gpkg (All POIs before deduplication)
      2. model5_pois_nationwide_cleaned.gpkg (POIs after KDTree deduplication)
      3. model5_pois_study_area_cleaned.gpkg (Study area POIs clipped to bound_pa_ia1_ia2.poly)
      4. poi_points.gpkg (alias for model5_pois_study_area_cleaned)
      5. poi_polygons.gpkg (empty placeholder for backward compatibility)
    """
    f_dir = Path(features_dir)
    f_dir.mkdir(parents=True, exist_ok=True)

    path_all = f_dir / "model5_pois_nationwide_all.gpkg"
    path_cleaned = f_dir / "model5_pois_nationwide_cleaned.gpkg"
    path_study = f_dir / "model5_pois_study_area_cleaned.gpkg"
    path_legacy_pts = f_dir / "poi_points.gpkg"
    path_legacy_poly = f_dir / "poi_polygons.gpkg"

    log("Starte landesweite POI-Extraktion und -Klassifizierung (Max-Weight) ...")
    if progress: progress(10)

    # 1. Extract and classify all POIs landesweit (both before and after deduplication)
    df_all, df_clean = load_or_extract_nationwide_pois(
        pois_or_pbf=merged_pbf,
        osmium_exe=osmium_exe,
        local_crs=local_crs,
        return_both=True,
        log=log,
        progress=progress,
    )

    # Save landesweit all (before deduplication)
    log(f"Speichere landesweite POIs (gesamt vor Bereinigung): {path_all.name} ({len(df_all):,} POIs) ...")
    safe_write_gpkg(df_all, path_all, "model5_pois_nationwide_all")

    # Save landesweit cleaned (after deduplication)
    log(f"Speichere landesweite POIs (bereinigt): {path_cleaned.name} ({len(df_clean):,} POIs) ...")
    safe_write_gpkg(df_clean, path_cleaned, "model5_pois_nationwide_cleaned")

    # 2. Clip to study area
    if poly_study_area and Path(poly_study_area).is_file():
        log(f"Schneide POIs auf Untersuchungsraum zu ({Path(poly_study_area).name}) ...")
        poly_geom = parse_poly_file_to_polygon(poly_study_area)
        if poly_geom is not None and not poly_geom.is_empty:
            poly_series = gpd.GeoSeries([poly_geom], crs="EPSG:4326").to_crs(local_crs)
            poly_proj = poly_series.iloc[0]
            mask = df_clean.geometry.intersects(poly_proj)
            df_study = df_clean[mask].copy()
            log(f"Untersuchungsraum-POIs: {len(df_study):,} (aus {len(df_clean):,} bereinigten landesweiten POIs)")
        else:
            log("Warnung: .poly-Datei konnte nicht als Polygon interpretiert werden; übernehme alle bereinigten POIs.")
            df_study = df_clean.copy()
    else:
        log("Hinweis: Keine Study-Area .poly übergeben; übernehme alle bereinigten POIs.")
        df_study = df_clean.copy()

    df_study["fid"] = list(range(1, len(df_study) + 1))

    safe_write_gpkg(df_study, path_study, "model5_pois_study_area_cleaned")
    safe_write_gpkg(df_study, path_legacy_pts, "poi_points")

    # Write empty polygon layer for backward compatibility
    schema_empty = {
        "osm_id": pd.Series(dtype="str"),
        "name": pd.Series(dtype="str"),
        "sector": pd.Series(dtype="str"),
        "classification": pd.Series(dtype="str"),
        "Weight": pd.Series(dtype="int32"),
        "Radius": pd.Series(dtype="int32"),
        "description": pd.Series(dtype="str"),
        "primary_tag": pd.Series(dtype="str"),
        "secondary_tag": pd.Series(dtype="str"),
        "is_polygon": pd.Series(dtype="bool"),
        "geometry": gpd.GeoSeries(dtype="geometry"),
    }
    df_empty_poly = gpd.GeoDataFrame(schema_empty, crs=local_crs)
    safe_write_gpkg(df_empty_poly, path_legacy_poly, "poi_polygons")

    log(f"POI-Datensätze erfolgreich in {f_dir.name} bereitgestellt:")
    log(f"  • {path_all.name}")
    log(f"  • {path_cleaned.name}")
    log(f"  • {path_study.name}")

    if progress: progress(30)

    return {
        "model5_pois_nationwide_all": path_all,
        "model5_pois_nationwide_cleaned": path_cleaned,
        "model5_pois_study_area_cleaned": path_study,
        "study_area_points": path_study,
        "study_area_polygons": path_legacy_poly,
    }


def compute_sector_intensities_and_entropy(
    candidate_coords: np.ndarray,
    poi_coords: dict[str, np.ndarray],
    poi_weights: dict[str, np.ndarray],
    poi_radii: dict[str, np.ndarray],
    *,
    log: Callable[[str], None] = print,
    progress: Optional[Callable[[int], None]] = None,
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
    """Calculate sector intensities using Model 5 quartic kernel and saturation formula: 5 * (1 - exp(-0.2773 * A))."""
    n_candidates = len(candidate_coords)
    sector_intensities = {s: np.zeros(n_candidates, dtype=np.float64) for s in SECTOR_NAMES}

    for idx_s, sector in enumerate(SECTOR_NAMES, start=1):
        coords_s = poi_coords.get(sector)
        weights_s = poi_weights.get(sector)
        radii_s = poi_radii.get(sector)
        if coords_s is None or len(coords_s) == 0:
            continue

        log(f"  [{idx_s}/5] Werte KDE-Intensitäten für Sektor {sector} an {n_candidates:,} Kandidatenorten aus ...")
        if progress:
            progress(60 + int((idx_s - 1) * 3))

        tree_s = cKDTree(coords_s)
        max_search_r = 500.0  # Max possible radius across all weights
        indices_list = tree_s.query_ball_point(candidate_coords, max_search_r)

        for i, idxs in enumerate(indices_list):
            if not idxs:
                continue
            idxs_arr = np.array(idxs)
            c_pts = coords_s[idxs_arr]
            dists = np.linalg.norm(c_pts - candidate_coords[i], axis=1)
            w = weights_s[idxs_arr]
            r = radii_s[idxs_arr]

            # Filter only POIs whose individual radius covers this candidate
            mask = (dists <= r) & (r > 0)
            if not np.any(mask):
                continue

            u = dists[mask] / r[mask]
            # QGIS exact Raw Quartic (biweight) kernel density: w * (1 - (d/r)^2)^2
            kernel = (1.0 - u ** 2) ** 2
            raw_a = np.sum(w[mask] * kernel)

            # Model 5 exact saturation formula: 5 * (1 - exp(-0.2773 * A))
            scaled = 5.0 * (1.0 - math.exp(-0.2773 * raw_a))
            sector_intensities[sector][i] = round(scaled, 2)

    total_intensity = np.zeros(n_candidates, dtype=np.float64)
    for sector in SECTOR_NAMES:
        total_intensity += sector_intensities[sector]
    total_intensity = np.round(total_intensity, 2)

    entropy = np.zeros(n_candidates, dtype=np.float64)
    entropy_index = np.zeros(n_candidates, dtype=np.float64)
    log_5 = math.log(5.0)

    for i in range(n_candidates):
        tot = total_intensity[i]
        if tot > 0:
            e = 0.0
            for sector in SECTOR_NAMES:
                p = sector_intensities[sector][i] / tot
                if p > 0:
                    e -= p * math.log(p)
            entropy[i] = round(e, 2)
            entropy_index[i] = round(e / log_5, 2)

    return sector_intensities, total_intensity, entropy, entropy_index


def generate_sector_rasters(
    pois_gdf: gpd.GeoDataFrame,
    output_dir: Path,
    local_crs: str,
    *,
    cell_size: float = 25.0,
    log: Callable[[str], None] = print,
    progress: Optional[Callable[[int], None]] = None,
) -> dict[str, Path]:
    """Generate GeoTIFF heatmaps using exact Model 5 weight-to-radius footprints and saturation formula."""
    if gdal is None:
        log("Hinweis: GDAL nicht verfügbar, Rastererzeugung wird übersprungen.")
        return {}

    bounds = pois_gdf.total_bounds  # minx, miny, maxx, maxy
    margin = 3000.0  # 3 km buffer
    # Snap extent to exact multiples of cell_size (25 m) to ensure 100% congruent grid alignment
    minx = math.floor((bounds[0] - margin) / cell_size) * cell_size
    miny = math.floor((bounds[1] - margin) / cell_size) * cell_size
    maxx = math.ceil((bounds[2] + margin) / cell_size) * cell_size
    maxy = math.ceil((bounds[3] + margin) / cell_size) * cell_size

    cols = max(10, int(round((maxx - minx) / cell_size)))
    rows = max(10, int(round((maxy - miny) / cell_size)))

    log(f"Erzeuge landesweite Intensitätsraster ({cols:,} × {rows:,} Pixel, Zellengröße: {cell_size} m, deckungsgleiches Gitter) ...")

    driver = gdal.GetDriverByName("GTiff")
    srs = osr.SpatialReference()
    srs.SetFromUserInput(local_crs)
    proj_wkt = srs.ExportToWkt()
    geotransform = (minx, cell_size, 0.0, maxy, 0.0, -cell_size)

    def write_tif(filename: str, array_data: np.ndarray) -> Path:
        out_path = output_dir / filename
        if out_path.exists():
            out_path.unlink()
        ds = driver.Create(
            str(out_path),
            cols,
            rows,
            1,
            gdal.GDT_Float32,
            options=["COMPRESS=DEFLATE", "TILED=YES", "NUM_THREADS=ALL_CPUS"],
        )
        ds.SetGeoTransform(geotransform)
        ds.SetProjection(proj_wkt)
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(-9999.0)
        band.WriteArray(array_data.astype(np.float32))
        band.FlushCache()
        ds = None
        return out_path

    raster_paths: dict[str, Path] = {}
    overlaid_grid = np.zeros((rows, cols), dtype=np.float32)

    for idx_s, sector in enumerate(SECTOR_NAMES, start=1):
        sub = pois_gdf[pois_gdf["sector"] == sector]
        raw_a_grid = np.zeros((rows, cols), dtype=np.float32)
        log(f"  [{idx_s}/5] Berechne Heatmap-Raster für Sektor {sector} ({len(sub):,} POIs) ...")
        if progress:
            progress(45 + int((idx_s - 1) * 3))

        if not sub.empty:
            xs = np.array([p.x for p in sub.geometry])
            ys = np.array([p.y for p in sub.geometry])
            weights = sub["Weight"].values.astype(np.float32)
            radii = sub["Radius"].values.astype(np.float32)

            for x_p, y_p, w_p, r_p in zip(xs, ys, weights, radii):
                if r_p <= 0 or w_p <= 0:
                    continue

                r_cells = int(math.ceil(r_p / cell_size))
                c_center = int((x_p - minx) / cell_size)
                r_center = int((maxy - y_p) / cell_size)

                c_min = max(0, c_center - r_cells)
                c_max = min(cols, c_center + r_cells + 1)
                r_min = max(0, r_center - r_cells)
                r_max = min(rows, r_center + r_cells + 1)

                if c_min >= c_max or r_min >= r_max:
                    continue

                grid_x = minx + (np.arange(c_min, c_max) + 0.5) * cell_size
                grid_y = maxy - (np.arange(r_min, r_max) + 0.5) * cell_size

                dx = grid_x - x_p
                dy = grid_y[:, np.newaxis] - y_p
                dist_sq = dx[np.newaxis, :] ** 2 + dy ** 2
                r_sq = r_p ** 2

                mask = dist_sq <= r_sq
                u_sq = np.where(mask, dist_sq / r_sq, 1.0)

                # QGIS exact Raw Quartic kernel: w * (1 - (d/r)^2)^2
                kernel = ((1.0 - u_sq) ** 2) * mask * w_p
                raw_a_grid[r_min:r_max, c_min:c_max] += kernel.astype(np.float32)

            # Apply Model 5 exact saturation formula: 5 * (1 - exp(-0.2773 * A))
            sector_grid = 5.0 * (1.0 - np.exp(-0.2773 * raw_a_grid))
            sector_grid = np.clip(sector_grid, 0.0, 5.0).astype(np.float32)
        else:
            sector_grid = np.zeros((rows, cols), dtype=np.float32)

        del raw_a_grid

        # Stream-add to overlaid sum
        overlaid_grid += sector_grid

        # Save individual sector raster immediately
        fname = f"sector_{sector.lower()}_intensity.tif"
        raster_paths[sector.lower()] = write_tif(fname, sector_grid)
        log(f"  Sektor {sector} GeoTIFF gespeichert: {fname}")

        del sector_grid
        gc.collect()

    # Save overlaid sum raster (values 0-25)
    log("  [6/6] Speichere überlagerte Gesamt-Heatmap: sector_all_intensity.tif ...")
    raster_paths["all"] = write_tif("sector_all_intensity.tif", overlaid_grid)
    del overlaid_grid
    gc.collect()

    log("Landesweite Intensitätsraster (GeoTIFF, 25 m Pixel) erfolgreich generiert.")
    return raster_paths


def copy_styles_for_nationwide_layers(output_dir: Path, styles_dir: Path, log: Callable[[str], None] = print) -> None:
    """Copy appropriate QML styles for all generated GPKG and TIF layers."""
    point_style_src = styles_dir / "centrality_points.qml"
    poi_style_src = styles_dir / "poi_points.qml"
    intensity_style_src = styles_dir / "intensity.qml"

    if point_style_src.is_file():
        shutil.copy2(point_style_src, output_dir / "inner_urban_central_points_nationwide.qml")
        shutil.copy2(point_style_src, output_dir / "central_place_points_nationwide.qml")
        shutil.copy2(point_style_src, output_dir / "nationwide_centrality_points.qml")

    for f in output_dir.glob("*.gpkg"):
        if f.stem.startswith("sector_") and poi_style_src.is_file():
            shutil.copy2(poi_style_src, output_dir / f"{f.stem}.qml")
        elif (
            f.stem in ("inner_urban_central_points_nationwide", "central_place_points_nationwide", "nationwide_centrality_points")
            and point_style_src.is_file()
        ):
            shutil.copy2(point_style_src, output_dir / f"{f.stem}.qml")

    for f in output_dir.glob("*.tif"):
        if intensity_style_src.is_file():
            shutil.copy2(intensity_style_src, output_dir / f"{f.stem}.qml")


def safe_write_gpkg(gdf: gpd.GeoDataFrame, path: Path, layer: str) -> None:
    """Safely write GeoDataFrame to GeoPackage, attempting unlink or using mode='w'."""
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass
    gdf_out = gdf.copy()
    # Explicitly cast standard integer columns to int32 (32-bit integer, type 2 in QGIS)
    for col in ("Weight", "Radius", "fid", "weight", "radius"):
        if col in gdf_out.columns:
            try:
                gdf_out[col] = gdf_out[col].fillna(0).astype("int32")
            except Exception:
                pass
    # Explicitly cast boolean columns to bool (BOOLEAN in GPKG / checkbox in QGIS)
    for col in ("is_polygon", "is_dropped"):
        if col in gdf_out.columns:
            try:
                gdf_out[col] = gdf_out[col].astype("bool")
            except Exception:
                pass
    gdf_out.to_file(str(path), layer=layer, driver="GPKG")


def run_nationwide_intensity_estimation(
    seeds_gpkg: str | Path,
    pois_or_pbf: str | Path,
    output_gpkg: str | Path,
    *,
    poi_points_path: Optional[str | Path] = None,
    poi_polygons_path: Optional[str | Path] = None,
    local_crs: str = "EPSG:3857",
    radius: float = 500.0,
    min_intensity: float = 7.0,
    min_intensity_level_3: float = 10.0,
    osmium_exe: Optional[str | Path] = None,
    styles_dir: Optional[str | Path] = None,
    log: Callable[[str], None] = print,
    progress: Optional[Callable[[int], None]] = None,
) -> Path:
    """Run nationwide intensity estimation using Model 2 seeds and cKDTree exclusion."""
    seeds_gpkg = Path(seeds_gpkg)
    output_gpkg = Path(output_gpkg)
    output_dir = output_gpkg.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        progress(5)

    # 1. Load Seeds from Model 2
    log(f"Lade zentrale Orte (Seeds) aus Modell 2: {seeds_gpkg.name} ...")
    if not seeds_gpkg.is_file():
        raise FileNotFoundError(f"Seed-Datei aus Modell 2 nicht gefunden: {seeds_gpkg}")

    seeds_gdf = gpd.read_file(str(seeds_gpkg))
    if seeds_gdf.crs is None or str(seeds_gdf.crs).upper() != str(local_crs).upper():
        seeds_gdf = seeds_gdf.to_crs(local_crs)

    seed_coords = np.array([[p.x, p.y] for p in seeds_gdf.geometry if p is not None and not p.is_empty])
    log(f"{len(seed_coords):,} Seed-Punkte geladen.")

    # Copy Model 2 seeds as central_place_points_nationwide.gpkg into output directory (clean overwrite)
    cp_nationwide_dest = output_dir / "central_place_points_nationwide.gpkg"
    safe_write_gpkg(seeds_gdf, cp_nationwide_dest, "central_place_points_nationwide")
    log(f"Zentrale Orte aus Modell 2 nach {cp_nationwide_dest.name} kopiert.")

    if progress:
        progress(15)

    # 2. Extract or Load POIs
    pois_gdf = load_or_extract_nationwide_pois(
        pois_or_pbf,
        poi_points_path=poi_points_path,
        poi_polygons_path=poi_polygons_path,
        osmium_exe=osmium_exe,
        local_crs=local_crs,
        debug_dir=output_dir,
        log=log,
        progress=progress,
    )

    log(f"{len(pois_gdf):,} bereinigte POIs im lokalen KBS ({local_crs}) bereitgestellt (mit Weight & Radius).")

    if progress:
        progress(30)

    # 3. Export Sector POI GPKGs (Individual and Overlaid All) with clean overwrite
    log("Speichere einzelne Sektor-POI-Punktlayer mit Weight und Radius ...")
    all_pts_dest = output_dir / "sector_all_points.gpkg"
    safe_write_gpkg(pois_gdf, all_pts_dest, "sector_all_points")

    for sector in SECTOR_NAMES:
        sub = pois_gdf[pois_gdf["sector"] == sector]
        sec_path = output_dir / f"sector_{sector.lower()}_points.gpkg"
        if not sub.empty:
            safe_write_gpkg(sub, sec_path, f"sector_{sector.lower()}_points")
        else:
            empty_gdf = gpd.GeoDataFrame(columns=list(pois_gdf.columns), crs=local_crs)
            safe_write_gpkg(empty_gdf, sec_path, f"sector_{sector.lower()}_points")

    if progress:
        progress(45)

    # 4. Generate Heatmap Rasters (.tif) for each sector and overlaid sum (25 m resolution as in Model 5)
    generate_sector_rasters(pois_gdf, output_dir, local_crs, cell_size=25.0, log=log, progress=progress)

    if progress:
        progress(60)

    # 5. Organize POIs by sector for fast spatial KDE at candidate points
    poi_coords_by_sector = {}
    poi_weights_by_sector = {}
    poi_radii_by_sector = {}
    for sector in SECTOR_NAMES:
        sub = pois_gdf[pois_gdf["sector"] == sector]
        if not sub.empty:
            coords = np.array([[p.x, p.y] for p in sub.geometry])
            weights = sub["Weight"].values.astype(np.float64)
            radii = sub["Radius"].values.astype(np.float64)
            poi_coords_by_sector[sector] = coords
            poi_weights_by_sector[sector] = weights
            poi_radii_by_sector[sector] = radii
        else:
            poi_coords_by_sector[sector] = np.empty((0, 2))
            poi_weights_by_sector[sector] = np.empty((0,))
            poi_radii_by_sector[sector] = np.empty((0,))

    # 6. Candidate Locations: Evaluate all deduplicated POI locations
    log("Berechne Intensitätswerte für Kandidatenorte ...")
    eval_coords = np.array([[p.x, p.y] for p in pois_gdf.geometry])
    log(f"{len(eval_coords):,} POI-Kandidatenorte werden analysiert ...")

    sector_ints, total_ints, entropies, entropy_idxs = compute_sector_intensities_and_entropy(
        eval_coords, poi_coords_by_sector, poi_weights_by_sector, poi_radii_by_sector, log=log, progress=progress
    )

    # Sort candidates descending by total_intensity
    sort_order = np.argsort(-total_ints)
    eval_coords = eval_coords[sort_order]
    total_ints = total_ints[sort_order]
    entropies = entropies[sort_order]
    entropy_idxs = entropy_idxs[sort_order]
    for sector in SECTOR_NAMES:
        sector_ints[sector] = sector_ints[sector][sort_order]

    if progress:
        progress(75)

    # 7. cKDTree Greedy Exclusion with Seeds (angelehnt an maximum_in_radius.py)
    log(f"Wende cKDTree-Greedy-Auswahl mit Sperrradius {radius} m auf die {len(seed_coords):,} Seeds an ...")
    tree = cKDTree(eval_coords)
    removed = np.zeros(len(eval_coords), dtype=bool)

    # 7.1 Seed Exclusion: Lock out candidate points in the vicinity of existing Model 2 seeds
    for s in seed_coords:
        neighbors = tree.query_ball_point(s, radius)
        removed[neighbors] = True

    if progress:
        progress(82)

    # 7.2 Greedy Selection of Intensity Maxima
    selected_indices = []
    for i, pt in enumerate(eval_coords):
        if not removed[i] and total_ints[i] >= min_intensity:
            selected_indices.append(i)
            neighbors = tree.query_ball_point(pt, radius)
            removed[neighbors] = True

    log(f"{len(selected_indices):,} neue innergemeindliche Zentralitäten identifiziert (Mindest-Intensität >= {min_intensity}).")

    if progress:
        progress(88)

    # 8. Concatenate contributing OSM POI names for each selected centrality point
    log(f"Verknüpfe OSM-Namen der beitragenden POIs für {len(selected_indices):,} Zentralitätspunkte ...")
    all_poi_coords = np.array([[p.x, p.y] for p in pois_gdf.geometry])
    all_poi_names = pois_gdf["name"].values
    full_poi_tree = cKDTree(all_poi_coords)

    records = []
    for idx in selected_indices:
        tot = float(total_ints[idx])
        typeno = 3 if tot >= min_intensity_level_3 else 4
        pt = Point(eval_coords[idx][0], eval_coords[idx][1])

        # Query all named POIs contributing to this centrality within radius
        contributing_idxs = full_poi_tree.query_ball_point(eval_coords[idx], radius)
        named_pois = [str(all_poi_names[k]).strip() for k in contributing_idxs if all_poi_names[k] and str(all_poi_names[k]).strip()]
        unique_names = list(dict.fromkeys(named_pois))
        concatenated_name = ", ".join(unique_names[:10]) if unique_names else ""

        rec = {
            "name": concatenated_name,
            "TypeNo": typeno,
            "Source": "Nationwide Intensity Estimation",
            "XCoord": eval_coords[idx][0],
            "YCoord": eval_coords[idx][1],
            "Total_Intensity": tot,
            "Healthcare": float(sector_ints["HEALTHCARE"][idx]),
            "Leisure": float(sector_ints["LEISURE"][idx]),
            "Retail": float(sector_ints["RETAIL"][idx]),
            "Education": float(sector_ints["EDUCATION"][idx]),
            "Government": float(sector_ints["GOVERNMENT"][idx]),
            "P_Healthcare": round(float(sector_ints["HEALTHCARE"][idx]) / max(0.01, tot), 2),
            "P_Leisure": round(float(sector_ints["LEISURE"][idx]) / max(0.01, tot), 2),
            "P_Retail": round(float(sector_ints["RETAIL"][idx]) / max(0.01, tot), 2),
            "P_Education": round(float(sector_ints["EDUCATION"][idx]) / max(0.01, tot), 2),
            "P_Government": round(float(sector_ints["GOVERNMENT"][idx]) / max(0.01, tot), 2),
            "Entropy": float(entropies[idx]),
            "Entropy_Index": float(entropy_idxs[idx]),
            "geometry": pt,
        }
        records.append(rec)

    if records:
        res_gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=local_crs)
    else:
        res_gdf = gpd.GeoDataFrame(
            columns=["name", "TypeNo", "Source", "XCoord", "YCoord", "Total_Intensity", "geometry"],
            crs=local_crs,
        )

    layer_name = output_gpkg.stem
    safe_write_gpkg(res_gdf, output_gpkg, layer_name)

    # Also ensure canonical inner_urban_central_points_nationwide.gpkg is written
    canonical_inner_urban = output_dir / "inner_urban_central_points_nationwide.gpkg"
    if output_gpkg.resolve() != canonical_inner_urban.resolve():
        safe_write_gpkg(res_gdf, canonical_inner_urban, "inner_urban_central_points_nationwide")

    log(f"Ergebnis erfolgreich gespeichert: {output_gpkg.name} ({len(res_gdf):,} Punkte).")

    # 9. Apply QML Styles for all generated layers
    if styles_dir:
        copy_styles_for_nationwide_layers(output_dir, Path(styles_dir), log=log)

    if progress:
        progress(100)

    return output_gpkg
