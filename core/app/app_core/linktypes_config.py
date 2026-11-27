"""Configuration model, defaults, and Visum .net generator for PANDO link types.

Provides PANDO's master link-type taxonomy with curated defaults that match
`core/scripts/visum/helper_files/master_linktypes.net` out-of-the-box while
allowing users to customize active road categories, lane classes, speed
brackets, capacities, and impedance factors directly in the GUI before Visum
runs.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from core.app.app_core.project import tool_root

CONFIG_FILENAME = "config.json"
LEGACY_CONFIG_FILENAME = "linktypes_config.json"
CUSTOM_NET_FILENAME = "net/custom_linktypes.net"
LEGACY_CUSTOM_NET_FILENAME = "custom_export_excel_linktypes.net"

ALL_SPEEDS: tuple[int, ...] = (130, 120, 110, 100, 90, 80, 70, 60, 50, 40, 30, 20)
ALL_LANES: tuple[int, ...] = (4, 3, 2, 1)
ALL_TSYS: tuple[str, ...] = ("CAR", "HGV", "BUS", "MINIBUS", "PUTCAR", "BIKE", "WALK", "PUTW")

DEFAULT_BASE_CAPACITY_1_LANE: dict[int, int] = {
    130: 1500,
    120: 1400,
    110: 1300,
    100: 1200,
    90: 1100,
    80: 1000,
    70: 800,
    60: 600,
    50: 400,
    40: 300,
    30: 200,
    20: 100,
}

# Exact default velocity ceiling/default vector (VMINPRT, VMAX_PRTSYS(*), VDEF_PUTSYS(*))
_STD_SPEED_VECTOR = ("1", "20", "130", "100", "4", "80", "50", "4", "50", "50", "50")
_FERRY_SPEED_VECTOR = ("20", "20", "130", "100", "20", "80", "50", "20", "50", "50", "50")

LINKTYPE_NET_HEADER = (
    "$LINKTYPE:NO;GTYPE;NAME;STRICT;RANK;TSYSSET;NUMLANES;CAPPRT;V0PRT;VMINPRT;"
    "VMAX_PRTSYS(BIKE);VMAX_PRTSYS(CAR);VMAX_PRTSYS(HGV);VMAX_PRTSYS(WALK);"
    "VDEF_PUTSYS(BUS);VDEF_PUTSYS(LRT);VDEF_PUTSYS(PUTW);VDEF_PUTSYS(SUBWAY);"
    "VDEF_PUTSYS(TRAIN);VDEF_PUTSYS(TRAM);LINKTYPEIMPEDANCEFACTOR_BIKE;"
    "LINKTYPEIMPEDANCEFACTOR_CAR;LINKTYPEIMPEDANCEFACTOR_PEDESTRIAN;OSM_WIKI_REFERENCE"
)


@dataclass(frozen=True)
class StructuredCategorySpec:
    key: str
    abbr: str
    gtype: int
    rank: int
    block_base: int
    block_size: int
    is_ramp: bool
    default_lanes: tuple[int, ...]
    default_speeds: tuple[int, ...]
    default_tsys: tuple[str, ...]
    imp_car: float
    imp_bike: float
    imp_ped: float
    osm_ref: str
    lane_slot_starts: tuple[tuple[int, int], ...]


STRUCTURED_CATEGORIES: tuple[StructuredCategorySpec, ...] = (
    StructuredCategorySpec(
        key="Motorway",
        abbr="Mo",
        gtype=10,
        rank=1,
        block_base=100,
        block_size=100,
        is_ramp=False,
        default_lanes=(4, 3, 2, 1),
        default_speeds=(130, 120, 110, 100, 90, 80, 70, 60, 50),
        default_tsys=("BUS", "CAR", "HGV"),
        imp_car=0.8,
        imp_bike=10.0,
        imp_ped=10.0,
        osm_ref='Motorway: High-capacity, controlled-access road, physically separated lanes, special traffic restrictions ("Autobahn")',
        lane_slot_starts=((4, 100), (3, 120), (2, 140), (1, 160)),
    ),
    StructuredCategorySpec(
        key="Motorway_link",
        abbr="Link Mo",
        gtype=11,
        rank=1,
        block_base=800,
        block_size=10,
        is_ramp=True,
        default_lanes=(3, 2, 1),
        default_speeds=(80, 40),
        default_tsys=("BUS", "CAR", "HGV"),
        imp_car=0.8,
        imp_bike=10.0,
        imp_ped=10.0,
        osm_ref='Motorway Link: Slip road (ramp) connecting to a motorway ("Autobahn")',
        lane_slot_starts=((3, 800), (2, 801), (1, 802), (4, 803)),
    ),
    StructuredCategorySpec(
        key="Trunk",
        abbr="Tr",
        gtype=20,
        rank=2,
        block_base=200,
        block_size=100,
        is_ramp=False,
        default_lanes=(3, 2, 1),
        default_speeds=(130, 120, 110, 100, 90, 80, 70, 60, 50, 40),
        default_tsys=("BUS", "MINIBUS", "PUTCAR", "CAR", "HGV"),
        imp_car=0.8,
        imp_bike=10.0,
        imp_ped=10.0,
        osm_ref='Trunk: Major high-speed road, not a motorway, often excludes slow traffic ("Kraftfahrstraße")',
        lane_slot_starts=((3, 200), (2, 220), (1, 240), (4, 260)),
    ),
    StructuredCategorySpec(
        key="Trunk_link",
        abbr="Link Tr",
        gtype=21,
        rank=2,
        block_base=810,
        block_size=10,
        is_ramp=True,
        default_lanes=(3, 2, 1),
        default_speeds=(80, 40),
        default_tsys=("BUS", "MINIBUS", "PUTCAR", "CAR", "HGV"),
        imp_car=0.8,
        imp_bike=10.0,
        imp_ped=10.0,
        osm_ref='Trunk Link: Slip road (ramp) connecting to a trunk road ("Kraftfahrstraße")',
        lane_slot_starts=((3, 810), (2, 811), (1, 812), (4, 813)),
    ),
    StructuredCategorySpec(
        key="Primary",
        abbr="Pr",
        gtype=30,
        rank=3,
        block_base=300,
        block_size=100,
        is_ramp=False,
        default_lanes=(3, 2, 1),
        default_speeds=(130, 120, 110, 100, 90, 80, 70, 60, 50, 40),
        default_tsys=("BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"),
        imp_car=1.0,
        imp_bike=1.3,
        imp_ped=1.3,
        osm_ref='Primary: Major road under national administration, connects large cities, significant interurban traffic (e.g. 10.000 veh / day, "Bundesstraße")',
        lane_slot_starts=((3, 300), (2, 320), (1, 340), (4, 384)),
    ),
    StructuredCategorySpec(
        key="Primary_link",
        abbr="Link Pr",
        gtype=31,
        rank=3,
        block_base=820,
        block_size=10,
        is_ramp=True,
        default_lanes=(3, 2, 1),
        default_speeds=(80, 40),
        default_tsys=("BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"),
        imp_car=1.0,
        imp_bike=1.3,
        imp_ped=1.3,
        osm_ref='Primary Link: Slip road (ramp) connecting to a primary road ("Bundesstaße")',
        lane_slot_starts=((3, 820), (2, 821), (1, 822), (4, 823)),
    ),
    StructuredCategorySpec(
        key="Secondary",
        abbr="Se",
        gtype=40,
        rank=4,
        block_base=400,
        block_size=100,
        is_ramp=False,
        default_lanes=(2, 1),
        default_speeds=(100, 90, 80, 70, 60, 50, 40),
        default_tsys=("BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"),
        imp_car=1.2,
        imp_bike=1.0,
        imp_ped=1.0,
        osm_ref='Secondary: Important regional road, connects smaller towns and centers ("Landesstraße")',
        lane_slot_starts=((2, 400), (1, 420), (3, 460), (4, 480)),
    ),
    StructuredCategorySpec(
        key="Secondary_link",
        abbr="Link Se",
        gtype=41,
        rank=4,
        block_base=830,
        block_size=10,
        is_ramp=True,
        default_lanes=(2, 1),
        default_speeds=(80, 40),
        default_tsys=("BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"),
        imp_car=1.2,
        imp_bike=1.0,
        imp_ped=1.0,
        osm_ref='Secondary Link: Slip road (ramp) connecting to a secondary road ("Landesstraße")',
        lane_slot_starts=((2, 830), (1, 831), (3, 832), (4, 833)),
    ),
    StructuredCategorySpec(
        key="Tertiary",
        abbr="Te",
        gtype=50,
        rank=5,
        block_base=500,
        block_size=100,
        is_ramp=False,
        default_lanes=(2, 1),
        default_speeds=(100, 90, 80, 70, 60, 50, 40),
        default_tsys=("BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"),
        imp_car=1.2,
        imp_bike=1.0,
        imp_ped=1.0,
        osm_ref='Tertiary: Connects villages or serves as a major street within towns ("Kreisstraße")',
        lane_slot_starts=((2, 500), (1, 520), (3, 560), (4, 580)),
    ),
    StructuredCategorySpec(
        key="Tertiary_link",
        abbr="Link Te",
        gtype=51,
        rank=5,
        block_base=840,
        block_size=10,
        is_ramp=True,
        default_lanes=(2, 1),
        default_speeds=(80, 40),
        default_tsys=("BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"),
        imp_car=1.2,
        imp_bike=1.0,
        imp_ped=1.0,
        osm_ref='Tertiary Link: Slip road (ramp) connecting to a tertiary road ("Kreisstraße")',
        lane_slot_starts=((2, 840), (1, 841), (3, 842), (4, 843)),
    ),
    StructuredCategorySpec(
        key="Unclassified",
        abbr="Un",
        gtype=60,
        rank=6,
        block_base=600,
        block_size=100,
        is_ramp=False,
        default_lanes=(1,),
        default_speeds=(100, 90, 80, 70, 60, 50, 40, 30),
        default_tsys=("BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"),
        imp_car=1.2,
        imp_bike=1.0,
        imp_ped=1.0,
        osm_ref='Unclassified: Minor public road, often rural, lowest standard, typically no center line ("Gemeindestraße")',
        lane_slot_starts=((1, 600), (2, 640), (3, 660), (4, 680)),
    ),
    StructuredCategorySpec(
        key="Residential",
        abbr="Re",
        gtype=70,
        rank=7,
        block_base=700,
        block_size=100,
        is_ramp=False,
        default_lanes=(1,),
        default_speeds=(50, 40, 30, 20),
        default_tsys=("BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"),
        imp_car=1.4,
        imp_bike=1.0,
        imp_ped=1.0,
        osm_ref="Residential: Street in residential areas, mainly for local access",
        lane_slot_starts=((1, 700), (2, 720), (3, 740), (4, 760)),
    ),
)

STRUCTURED_BY_KEY: dict[str, StructuredCategorySpec] = {c.key: c for c in STRUCTURED_CATEGORIES}

# Special/secondary Visum link types (900..994)
DEFAULT_SPECIAL_TYPES: tuple[dict[str, Any], ...] = (
    {
        "no": 900, "gtype": 0, "name": "Blocked Oneway", "rank": 999,
        "tsys": ["BIKE", "PUTW", "WALK"], "lanes": 1, "cap": 0, "v0": 4,
        "imp_bike": 1.0, "imp_car": 10.0, "imp_ped": 1.0,
        "osm_ref": "Blocked opposite direction",
    },
    {
        "no": 901, "gtype": 0, "name": "Construction", "rank": 999,
        "tsys": ["PUTW", "WALK"], "lanes": 1, "cap": 0, "v0": 4,
        "imp_bike": 10.0, "imp_car": 10.0, "imp_ped": 10.0,
        "osm_ref": "Road under construction",
    },
    {
        "no": 909, "gtype": 1, "name": "Road", "rank": 999,
        "tsys": ["BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"], "lanes": 1, "cap": 200, "v0": 30,
        "imp_bike": 1.0, "imp_car": 1.4, "imp_ped": 1.0,
        "osm_ref": "Classification not determined",
    },
    {
        "no": 910, "gtype": 1, "name": "Living_Street", "rank": 999,
        "tsys": ["BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"], "lanes": 1, "cap": 200, "v0": 30,
        "imp_bike": 1.0, "imp_car": 1.4, "imp_ped": 1.0,
        "osm_ref": 'Street with pedestrian priority, traffic-calmed, very low speed ("Verkehrsberuhigter Bereich")',
    },
    {
        "no": 911, "gtype": 1, "name": "Service", "rank": 999,
        "tsys": ["BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"], "lanes": 1, "cap": 200, "v0": 30,
        "imp_bike": 1.3, "imp_car": 1.5, "imp_ped": 1.0,
        "osm_ref": 'Minor road for access to properties, parking, or industrial sites ("Erschließungsweg")',
    },
    {
        "no": 919, "gtype": 2, "name": "Footway/Pedestrian", "rank": 999,
        "tsys": ["BIKE", "PUTW", "WALK"], "lanes": 1, "cap": 9999, "v0": 4,
        "imp_bike": 1.3, "imp_car": 10.0, "imp_ped": 1.0,
        "osm_ref": "Path designated for pedestrians only",
    },
    {
        "no": 920, "gtype": 2, "name": "Steps", "rank": 999,
        "tsys": ["BIKE", "PUTW", "WALK"], "lanes": 1, "cap": 9999, "v0": 4,
        "imp_bike": 10.0, "imp_car": 10.0, "imp_ped": 1.0,
        "osm_ref": "Staircase for pedestrians",
    },
    {
        "no": 921, "gtype": 2, "name": "Path", "rank": 999,
        "tsys": ["BIKE", "PUTW", "WALK"], "lanes": 1, "cap": 9999, "v0": 4,
        "imp_bike": 1.0, "imp_car": 10.0, "imp_ped": 1.0,
        "osm_ref": "Generic path for non-motorized use",
    },
    {
        "no": 922, "gtype": 2, "name": "Cycleway", "rank": 999,
        "tsys": ["BIKE", "PUTW", "WALK"], "lanes": 1, "cap": 9999, "v0": 20,
        "imp_bike": 1.0, "imp_car": 10.0, "imp_ped": 1.3,
        "osm_ref": "Path designated for bicycles",
    },
    {
        "no": 923, "gtype": 2, "name": "Track", "rank": 999,
        "tsys": ["BIKE", "PUTW", "WALK"], "lanes": 1, "cap": 9999, "v0": 20,
        "imp_bike": 1.3, "imp_car": 10.0, "imp_ped": 1.0,
        "osm_ref": "Road for agricultural or forestry use",
    },
    {
        "no": 929, "gtype": 3, "name": "General rail", "rank": 999,
        "tsys": ["LRT", "SUBWAY", "TRAIN", "TRAM"], "lanes": 1, "cap": 0, "v0": 20,
        "imp_bike": 10.0, "imp_car": 10.0, "imp_ped": 10.0,
        "osm_ref": "General railway line",
    },
    {
        "no": 930, "gtype": 3, "name": "Rail", "rank": 999,
        "tsys": ["LRT", "TRAIN"], "lanes": 1, "cap": 0, "v0": 20,
        "imp_bike": 10.0, "imp_car": 10.0, "imp_ped": 10.0,
        "osm_ref": "Railway line, type not specified",
    },
    {
        "no": 931, "gtype": 3, "name": "Light rail", "rank": 999,
        "tsys": ["LRT", "TRAM"], "lanes": 1, "cap": 0, "v0": 20,
        "imp_bike": 10.0, "imp_car": 10.0, "imp_ped": 10.0,
        "osm_ref": "LRT, urban rail",
    },
    {
        "no": 932, "gtype": 3, "name": "Subway", "rank": 999,
        "tsys": ["SUBWAY"], "lanes": 1, "cap": 0, "v0": 20,
        "imp_bike": 10.0, "imp_car": 10.0, "imp_ped": 10.0,
        "osm_ref": "SUBWAY, underground urban railway",
    },
    {
        "no": 933, "gtype": 3, "name": "Tram", "rank": 999,
        "tsys": ["TRAM"], "lanes": 1, "cap": 0, "v0": 20,
        "imp_bike": 10.0, "imp_car": 10.0, "imp_ped": 10.0,
        "osm_ref": "Tram, street-level urban rail",
    },
    {
        "no": 939, "gtype": 100, "name": "Ferry", "rank": 999,
        "tsys": ["BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"], "lanes": 1, "cap": 1000, "v0": 20,
        "imp_bike": 1.3, "imp_car": 1.3, "imp_ped": 1.3,
        "osm_ref": "Route for vehicles or pedestrians using a boat or ferry service",
        "is_ferry": True,
    },
    {
        "no": 940, "gtype": 100, "name": "Ferry_Access", "rank": 999,
        "tsys": ["BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"], "lanes": 1, "cap": 1000, "v0": 20,
        "imp_bike": 1.3, "imp_car": 1.3, "imp_ped": 1.3,
        "osm_ref": "Access link to a boat or ferry service (20 km/h for all modes)",
        "is_ferry": True,
    },
    {
        "no": 990, "gtype": 90, "name": "ZK_Hilfsstrecken", "rank": 999,
        "tsys": ["BUS", "CAR", "HGV"], "lanes": 1, "cap": 9999, "v0": 20,
        "imp_bike": 10.0, "imp_car": 0.8, "imp_ped": 10.0,
        "osm_ref": "Used for connector nodes",
    },
    {
        "no": 994, "gtype": 90, "name": "UnknownByImporter(Default)", "rank": 1,
        "tsys": ["BIKE", "BUS", "MINIBUS", "PUTCAR", "CAR", "HGV", "PUTW", "WALK"], "lanes": 0, "cap": 0, "v0": 0,
        "imp_bike": 1.0, "imp_car": 1.2, "imp_ped": 1.0,
        "osm_ref": "Used when highway/construction tag is unknown to the importer",
    },
)

# Optional Bus-lane template definitions matching master_linktypes.net (skipped by OSM assignment)
_BUS_LANE_DEFS: tuple[dict[str, Any], ...] = (
    # Primary bus-lane variants
    {"no_start": 360, "cat": "Primary", "prefix": "Pr L2+1Bus", "lanes": 2, "tsys_override": None, "speeds": (70, 60, 50, 40)},
    {"no_start": 370, "cat": "Primary", "prefix": "Pr L1+1Bus", "lanes": 1, "tsys_override": None, "speeds": (70, 60, 50, 40)},
    {"no_start": 380, "cat": "Primary", "prefix": "Pr L1Bus", "lanes": 0, "tsys_override": ["BUS", "WALK", "BIKE", "PUTW"], "speeds": (70, 60, 50, 40)},
    # Secondary bus-lane variants
    {"no_start": 438, "cat": "Secondary", "prefix": "Se L1+1Bus", "lanes": 1, "tsys_override": None, "speeds": (70, 60, 50, 40)},
    {"no_start": 448, "cat": "Secondary", "prefix": "Se L1Bus", "lanes": 0, "tsys_override": ["BUS", "WALK", "BIKE", "PUTW"], "speeds": (70, 60, 50, 40)},
    # Tertiary bus-lane variants
    {"no_start": 540, "cat": "Tertiary", "prefix": "Te L1+1Bus", "lanes": 1, "tsys_override": None, "speeds": (70, 60, 50, 40)},
    {"no_start": 550, "cat": "Tertiary", "prefix": "Te L1Bus", "lanes": 0, "tsys_override": ["BUS", "WALK", "BIKE", "PUTW"], "speeds": (70, 60, 50, 40)},
    # Unclassified bus-lane variants
    {"no_start": 620, "cat": "Unclassified", "prefix": "Un L1Bus", "lanes": 0, "tsys_override": ["BUS", "WALK", "BIKE", "PUTW"], "speeds": (70, 60, 50, 40, 30)},
)


def _format_decimal_comma(value: float) -> str:
    val = float(value)
    if val.is_integer():
        return str(int(val))
    text = f"{val:g}"
    return text.replace(".", ",")


def default_capacities() -> dict[str, dict[str, int]]:
    """Return capacity lookup ``{str(speed): {str(lanes): cap_prt}}`` for the master methodology."""
    return {
        str(speed): {str(lanes): int(base * lanes) for lanes in (1, 2, 3, 4)}
        for speed, base in DEFAULT_BASE_CAPACITY_1_LANE.items()
    }


def default_linktypes_config() -> dict[str, Any]:
    """Return default link-type configuration producing `master_linktypes.net`."""
    categories: dict[str, dict[str, Any]] = {}
    for spec in STRUCTURED_CATEGORIES:
        categories[spec.key] = {
            "enabled": True,
            "lanes": list(spec.default_lanes),
            "speeds": list(spec.default_speeds),
            "tsys": list(spec.default_tsys),
            "imp_car": spec.imp_car,
            "imp_bike": spec.imp_bike,
            "imp_ped": spec.imp_ped,
        }
    return {
        "version": 1,
        "include_bus_lanes": True,
        "scale_lanes_from_single": True,
        "capacities": default_capacities(),
        "categories": categories,
        "special": copy.deepcopy(list(DEFAULT_SPECIAL_TYPES)),
    }


def normalize_linktypes_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge user-supplied config onto complete validated defaults."""
    cfg = default_linktypes_config()
    if not isinstance(raw, Mapping):
        return cfg

    if "include_bus_lanes" in raw:
        cfg["include_bus_lanes"] = bool(raw["include_bus_lanes"])

    raw_caps = raw.get("capacities")
    if isinstance(raw_caps, Mapping):
        for speed in ALL_SPEEDS:
            sk = str(speed)
            row_in = raw_caps.get(sk)
            if isinstance(row_in, Mapping):
                for lanes in (1, 2, 3, 4):
                    lk = str(lanes)
                    if lk in row_in:
                        try:
                            cfg["capacities"][sk][lk] = max(0, int(row_in[lk]))
                        except (TypeError, ValueError):
                            pass
    if "scale_lanes_from_single" in raw:
        cfg["scale_lanes_from_single"] = bool(raw["scale_lanes_from_single"])
    elif isinstance(raw_caps, Mapping):
        cfg["scale_lanes_from_single"] = all(
            cfg["capacities"][str(spd)][str(lanes)] == cfg["capacities"][str(spd)]["1"] * lanes
            for spd in ALL_SPEEDS
            for lanes in (2, 3, 4)
        )

    raw_cats = raw.get("categories")
    if isinstance(raw_cats, Mapping):
        for spec in STRUCTURED_CATEGORIES:
            cat_in = raw_cats.get(spec.key)
            if not isinstance(cat_in, Mapping):
                continue
            target = cfg["categories"][spec.key]
            if "enabled" in cat_in:
                target["enabled"] = bool(cat_in["enabled"])
            if "lanes" in cat_in and isinstance(cat_in["lanes"], (list, tuple, set)):
                cleaned_lanes = sorted(
                    {int(x) for x in cat_in["lanes"] if int(x) in ALL_LANES},
                    reverse=True,
                )
                target["lanes"] = cleaned_lanes
            if "speeds" in cat_in and isinstance(cat_in["speeds"], (list, tuple, set)):
                cleaned_speeds = sorted(
                    {int(x) for x in cat_in["speeds"] if int(x) in ALL_SPEEDS},
                    reverse=True,
                )
                target["speeds"] = cleaned_speeds
            if "tsys" in cat_in:
                raw_tsys = cat_in["tsys"]
                if isinstance(raw_tsys, str):
                    tokens = [t.strip().upper() for t in raw_tsys.split(",") if t.strip()]
                elif isinstance(raw_tsys, Iterable):
                    tokens = [str(t).strip().upper() for t in raw_tsys if str(t).strip()]
                else:
                    tokens = list(target["tsys"])
                target["tsys"] = tokens
            for imp_key in ("imp_car", "imp_bike", "imp_ped"):
                if imp_key in cat_in:
                    try:
                        target[imp_key] = max(0.01, float(cat_in[imp_key]))
                    except (TypeError, ValueError):
                        pass

    raw_special = raw.get("special")
    if isinstance(raw_special, list):
        by_no: dict[int, dict[str, Any]] = {}
        for item in raw_special:
            if isinstance(item, Mapping) and "no" in item:
                try:
                    by_no[int(item["no"])] = dict(item)
                except (TypeError, ValueError):
                    pass
        for target_item in cfg["special"]:
            override = by_no.get(int(target_item["no"]))
            if not override:
                continue
            for int_key in ("v0", "cap", "lanes", "rank", "gtype"):
                if int_key in override:
                    try:
                        target_item[int_key] = max(0, int(override[int_key]))
                    except (TypeError, ValueError):
                        pass
            for imp_key in ("imp_car", "imp_bike", "imp_ped"):
                if imp_key in override:
                    try:
                        target_item[imp_key] = max(0.01, float(override[imp_key]))
                    except (TypeError, ValueError):
                        pass
            if "tsys" in override:
                rt = override["tsys"]
                if isinstance(rt, str):
                    target_item["tsys"] = [t.strip().upper() for t in rt.split(",") if t.strip()]
                elif isinstance(rt, Iterable):
                    target_item["tsys"] = [str(t).strip().upper() for t in rt if str(t).strip()]
    return cfg


def is_default_linktypes_config(config: Mapping[str, Any]) -> bool:
    """Return True if ``config`` matches `default_linktypes_config()`."""
    return normalize_linktypes_config(config) == default_linktypes_config()


def lookup_capacity(config: Mapping[str, Any], speed: int, lanes: int) -> int:
    """Resolve capacity (`CAPPRT`) from the capacity table (fallback to scaling 1-lane cap)."""
    if lanes <= 0:
        return 0
    caps = config.get("capacities") or {}
    speed_map = caps.get(str(int(speed)))
    if isinstance(speed_map, Mapping):
        if str(int(lanes)) in speed_map:
            return int(speed_map[str(int(lanes))])
        if "1" in speed_map:
            return int(speed_map["1"]) * int(lanes)
    base = DEFAULT_BASE_CAPACITY_1_LANE.get(int(speed), max(100, int(speed) * 10))
    return int(base * int(lanes))


def _gtype_for_slot(typeno: int) -> int:
    if 100 <= typeno <= 199:
        return 10
    if 200 <= typeno <= 299:
        return 20
    if 300 <= typeno <= 399:
        return 30
    if 400 <= typeno <= 499:
        return 40
    if 500 <= typeno <= 599:
        return 50
    if 600 <= typeno <= 699:
        return 60
    if 700 <= typeno <= 799:
        return 70
    if 800 <= typeno <= 809:
        return 11
    if 810 <= typeno <= 819:
        return 21
    if 820 <= typeno <= 829:
        return 31
    if 830 <= typeno <= 839:
        return 41
    if 840 <= typeno <= 849:
        return 51
    if 850 <= typeno <= 898:
        return 80
    if typeno == 899:
        return 90
    if 900 <= typeno <= 908:
        return 0
    if 909 <= typeno <= 918:
        return 1
    if 919 <= typeno <= 928:
        return 2
    if 929 <= typeno <= 938:
        return 3
    if 939 <= typeno <= 949:
        return 100
    if 950 <= typeno <= 989:
        return 5
    return 90


def generate_linktypes_rows(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build all active Visum link-type entries (`TYPENO >= 100`) from ``config``."""
    cfg = normalize_linktypes_config(config)
    assigned: dict[int, dict[str, Any]] = {}
    overflow_pool = iter([n for n in list(range(850, 899)) + list(range(950, 989))])

    # 1. Fixed special/secondary categories (900..994)
    for sp in cfg["special"]:
        no = int(sp["no"])
        assigned[no] = {
            "no": no,
            "gtype": int(sp["gtype"]),
            "name": str(sp["name"]),
            "strict": 1,
            "rank": int(sp["rank"]),
            "tsys": ",".join(sp["tsys"]),
            "lanes": int(sp["lanes"]),
            "cap": int(sp["cap"]),
            "v0": int(sp["v0"]),
            "imp_bike": float(sp["imp_bike"]),
            "imp_car": float(sp["imp_car"]),
            "imp_ped": float(sp["imp_ped"]),
            "osm_ref": str(sp.get("osm_ref", "")),
            "is_ferry": bool(sp.get("is_ferry", False)),
            "category": "Special",
        }

    # 2. Reserve Bus-lane rows first when enabled (preserving exact master TypeNos)
    if cfg.get("include_bus_lanes", True):
        for bus_def in _BUS_LANE_DEFS:
            cat_cfg = cfg["categories"][bus_def["cat"]]
            if not cat_cfg.get("enabled", True):
                continue
            spec = STRUCTURED_BY_KEY[bus_def["cat"]]
            tsys_str = ",".join(bus_def["tsys_override"] or cat_cfg["tsys"])
            for offset, spd in enumerate(bus_def["speeds"]):
                no = int(bus_def["no_start"]) + offset
                lanes = int(bus_def["lanes"])
                cap = 0 if lanes == 0 else lookup_capacity(cfg, spd, lanes)
                assigned[no] = {
                    "no": no,
                    "gtype": spec.gtype,
                    "name": f"{bus_def['prefix']} V{spd}",
                    "strict": 1,
                    "rank": spec.rank,
                    "tsys": tsys_str,
                    "lanes": lanes,
                    "cap": cap,
                    "v0": int(spd),
                    "imp_bike": float(cat_cfg["imp_bike"]),
                    "imp_car": float(cat_cfg["imp_car"]),
                    "imp_ped": float(cat_cfg["imp_ped"]),
                    "osm_ref": spec.osm_ref,
                    "is_ferry": False,
                    "category": spec.key,
                }

    # 3. Structured road & ramp categories
    for spec in STRUCTURED_CATEGORIES:
        cat_cfg = cfg["categories"][spec.key]
        if not cat_cfg.get("enabled", True):
            continue
        lanes_list = sorted({int(l) for l in cat_cfg["lanes"] if int(l) in ALL_LANES}, reverse=True)
        speeds_list = sorted({int(s) for s in cat_cfg["speeds"] if int(s) in ALL_SPEEDS}, reverse=True)
        if not lanes_list or not speeds_list:
            continue

        tsys_str = ",".join(cat_cfg["tsys"])
        slot_starts = dict(spec.lane_slot_starts)
        block_end = spec.block_base + spec.block_size - 1

        if spec.is_ramp:
            # Match exact master_linktypes slots when default ramp layout fits,
            # otherwise allocate sequentially in the 10-slot block / overflow pool.
            base_lane_offsets = {lanes: offset for offset, (lanes, _) in enumerate(spec.lane_slot_starts)}
            for s_idx, spd in enumerate(speeds_list):
                if spd == 80:
                    speed_base = spec.block_base
                elif spd == 40:
                    speed_base = spec.block_base + 5
                else:
                    speed_base = None
                for lanes in lanes_list:
                    slot = None
                    if speed_base is not None and lanes in base_lane_offsets and len(lanes_list) <= 3:
                        # Exact master_linktypes layout (supports 1..3 active lane classes cleanly)
                        candidate = speed_base + base_lane_offsets[lanes]
                        if candidate <= block_end and candidate not in assigned:
                            slot = candidate
                    if slot is None:
                        for cand in range(spec.block_base, block_end + 1):
                            if cand not in assigned:
                                slot = cand
                                break
                    if slot is None:
                        slot = next(overflow_pool)
                    assigned[slot] = {
                        "no": slot,
                        "gtype": spec.gtype,
                        "name": f"{spec.abbr} L{lanes} V{spd}",
                        "strict": 1,
                        "rank": spec.rank,
                        "tsys": tsys_str,
                        "lanes": lanes,
                        "cap": lookup_capacity(cfg, spd, lanes),
                        "v0": spd,
                        "imp_bike": float(cat_cfg["imp_bike"]),
                        "imp_car": float(cat_cfg["imp_car"]),
                        "imp_ped": float(cat_cfg["imp_ped"]),
                        "osm_ref": spec.osm_ref,
                        "is_ferry": False,
                        "category": spec.key,
                    }
        else:
            for lanes in lanes_list:
                start_no = slot_starts.get(lanes, spec.block_base)
                for s_idx, spd in enumerate(speeds_list):
                    candidate = start_no + s_idx
                    slot = None
                    if spec.block_base <= candidate <= block_end and candidate not in assigned:
                        slot = candidate
                    else:
                        for cand in range(spec.block_base, block_end + 1):
                            if cand not in assigned:
                                slot = cand
                                break
                    if slot is None:
                        slot = next(overflow_pool)
                    assigned[slot] = {
                        "no": slot,
                        "gtype": spec.gtype,
                        "name": f"{spec.abbr} L{lanes} V{spd}",
                        "strict": 1,
                        "rank": spec.rank,
                        "tsys": tsys_str,
                        "lanes": lanes,
                        "cap": lookup_capacity(cfg, spd, lanes),
                        "v0": spd,
                        "imp_bike": float(cat_cfg["imp_bike"]),
                        "imp_car": float(cat_cfg["imp_car"]),
                        "imp_ped": float(cat_cfg["imp_ped"]),
                        "osm_ref": spec.osm_ref,
                        "is_ferry": False,
                        "category": spec.key,
                    }

    return [assigned[k] for k in sorted(assigned)]


def _read_net_preamble(template_path: Optional[Path] = None) -> list[str]:
    """Read lines up to and including `$LINKTYPE:...` header from `master_linktypes.net`."""
    tpl = template_path or (
        Path(tool_root()) / "core" / "scripts" / "visum" / "helper_files" / "master_linktypes.net"
    )
    if tpl.is_file():
        lines = tpl.read_bytes().decode("cp1252", errors="replace").splitlines()
        out: list[str] = []
        for line in lines:
            out.append(line)
            if line.startswith("$LINKTYPE:"):
                return out
    return [
        "$VISION",
        "* PANDO Generated Link Types",
        "* ",
        "* Table: Version block",
        "* ",
        "$VERSION:VERSNR;FILETYPE;LANGUAGE;UNIT",
        "15;Net;ENG;KM",
        "* ",
        "* Table: User-defined attributes",
        "* ",
        "$USERATTDEF:OBJID;ATTID;CODE;NAME;VALUETYPE;MINVALUE;MAXVALUE;DEFAULTVALUE;DEFAULTSTRINGVALUE;COMMENT;MAXSTRINGLENGTH;NUMDECPLACES;DATASOURCETYPE;FORMULA;SCALEDBYLENGTH;CROSSSECTIONLOGIC;CSLIGNORECLOSED;SUBATTRS;CANBEEMPTY;USERDEFINEDGROUPNAME;OPERATIONREFERENCE;EXISTSINNET",
        "LINKTYPE;LINKTYPEIMPEDANCEFACTOR_BIKE;LinkTypeImpedanceFactor_Bike;LinkTypeImpedanceFactor_Bike;Double;MIN;MAX;1000;Type-dependent impedance factor for TSys bike (BASt-GiV);0;2;Data;0;SUM;0;0;1",
        "LINKTYPE;LINKTYPEIMPEDANCEFACTOR_CAR;LinkTypeImpedanceFactor_Car;LinkTypeImpedanceFactor_Car;Double;MIN;MAX;1000;Type-dependent impedance factor for TSys car (BASt-GiV);0;2;Data;0;SUM;0;0;1",
        "LINKTYPE;LINKTYPEIMPEDANCEFACTOR_PEDESTRIAN;LinkTypeImpedanceFactor_Pedestrian;LinkTypeImpedanceFactor_Pedestrian;Double;MIN;MAX;1000;Type-dependent impedance factor for TSys pedestrian (BASt-GiV);0;2;Data;0;SUM;0;0;1",
        "LINKTYPE;OSM_WIKI_REFERENCE;OSM_Wiki_Reference;OSM_Wiki_Reference;LongText;0.000;0.000;Reference to meaning in the OSM Wiki;2147483647;0;Data;0;SUM;0;0;1",
        "* ",
        "* Table: Link types",
        "* ",
        LINKTYPE_NET_HEADER,
    ]


def export_linktypes_net(
    config: Mapping[str, Any],
    output_net_path: str | Path,
    template_net_path: Optional[Path] = None,
) -> Path:
    """Export configured link types (`100..999`) to a Visum `.net` file (`cp1252` + CRLF)."""
    rows = {r["no"]: r for r in generate_linktypes_rows(config)}
    preamble = _read_net_preamble(template_net_path)
    out_lines = list(preamble)

    for no in range(100, 1000):
        row = rows.get(no)
        if row is None:
            gtype = _gtype_for_slot(no)
            out_lines.append(
                f"{no};{gtype};free;1;1000;;0;0;0;0;0;0;0;0;0;0;0;0;0;0;10;10;10;"
            )
            continue

        speeds = _FERRY_SPEED_VECTOR if row.get("is_ferry") else _STD_SPEED_VECTOR
        imp_bike = _format_decimal_comma(row["imp_bike"])
        imp_car = _format_decimal_comma(row["imp_car"])
        imp_ped = _format_decimal_comma(row["imp_ped"])
        osm_ref = str(row.get("osm_ref", "")).replace(";", ",")
        fields = [
            str(no),
            str(row["gtype"]),
            str(row["name"]),
            str(row.get("strict", 1)),
            str(row["rank"]),
            str(row["tsys"]),
            str(row["lanes"]),
            str(row["cap"]),
            str(row["v0"]),
            *speeds,
            imp_bike,
            imp_car,
            imp_ped,
            osm_ref,
        ]
        out_lines.append(";".join(fields))

    out_path = Path(output_net_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\r\n".join(out_lines) + "\r\n"
    out_path.write_bytes(text.encode("cp1252", errors="replace"))
    return out_path


def _read_project_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return dict(loaded) if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _has_saved_linktypes_config(base: Path) -> bool:
    cfg = _read_project_json(base / CONFIG_FILENAME)
    visum_cfg = cfg.get("visum")
    if isinstance(visum_cfg, Mapping) and isinstance(visum_cfg.get("linktypes"), Mapping):
        return True
    return (base / LEGACY_CONFIG_FILENAME).is_file()


def load_linktypes_config(project_dir: str | Path | None) -> dict[str, Any]:
    """Load project-specific link-type configuration from config.json (or legacy linktypes_config.json)."""
    if not project_dir:
        return default_linktypes_config()
    base = Path(project_dir)
    cfg = _read_project_json(base / CONFIG_FILENAME)
    visum_cfg = cfg.get("visum")
    if isinstance(visum_cfg, Mapping) and isinstance(visum_cfg.get("linktypes"), Mapping):
        return normalize_linktypes_config(visum_cfg["linktypes"])
    legacy_path = base / LEGACY_CONFIG_FILENAME
    if legacy_path.is_file():
        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
            return normalize_linktypes_config(raw)
        except Exception:
            pass
    return default_linktypes_config()


def save_linktypes_config(
    project_dir: str | Path,
    config: Mapping[str, Any],
) -> tuple[Path, Path]:
    """Persist link-type configuration into config.json['visum']['linktypes'] and export Visum `.net`."""
    base = Path(project_dir)
    norm = normalize_linktypes_config(config)

    cfg_path = base / CONFIG_FILENAME
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    root_cfg = _read_project_json(cfg_path)
    visum_cfg = root_cfg.get("visum")
    if not isinstance(visum_cfg, dict):
        visum_cfg = {}
        root_cfg["visum"] = visum_cfg
    visum_cfg["linktypes"] = norm

    tmp_cfg = cfg_path.with_suffix(".json.tmp")
    tmp_cfg.write_text(json.dumps(root_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_cfg.replace(cfg_path)

    visum_dir = base / "processed" / "visum"
    for stale in (base / LEGACY_CONFIG_FILENAME, visum_dir / LEGACY_CUSTOM_NET_FILENAME):
        if stale.is_file():
            try:
                stale.unlink()
            except OSError:
                pass

    net_path = visum_dir / CUSTOM_NET_FILENAME
    export_linktypes_net(norm, net_path)
    return cfg_path, net_path


def reset_linktypes_config(project_dir: str | Path) -> dict[str, Any]:
    """Reset project link-type configuration back to defaults and remove custom `.net`."""
    base = Path(project_dir)
    cfg_path = base / CONFIG_FILENAME
    if cfg_path.is_file():
        root_cfg = _read_project_json(cfg_path)
        visum_cfg = root_cfg.get("visum")
        if isinstance(visum_cfg, dict) and "linktypes" in visum_cfg:
            visum_cfg.pop("linktypes", None)
            if not visum_cfg:
                root_cfg.pop("visum", None)
            tmp_cfg = cfg_path.with_suffix(".json.tmp")
            tmp_cfg.write_text(json.dumps(root_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_cfg.replace(cfg_path)

    for path in (
        base / LEGACY_CONFIG_FILENAME,
        base / "processed" / "visum" / CUSTOM_NET_FILENAME,
        base / "processed" / "visum" / LEGACY_CUSTOM_NET_FILENAME,
    ):
        if path.is_file():
            try:
                path.unlink()
            except Exception:
                pass
    return default_linktypes_config()


def resolve_project_master_net(
    project_dir: str | Path | None,
    fallback_master_net: str | Path,
) -> Path:
    """Return custom generated `.net` if configured for `project_dir`, else fallback."""
    fallback = Path(fallback_master_net)
    if not project_dir:
        return fallback
    base = Path(project_dir)
    visum_dir = base / "processed" / "visum"
    custom_net = visum_dir / CUSTOM_NET_FILENAME
    if _has_saved_linktypes_config(base):
        cfg = load_linktypes_config(base)
        src_cfg = base / CONFIG_FILENAME if (base / CONFIG_FILENAME).is_file() else (base / LEGACY_CONFIG_FILENAME)
        if not custom_net.is_file() or custom_net.stat().st_mtime < src_cfg.stat().st_mtime:
            export_linktypes_net(cfg, custom_net, template_net_path=fallback if fallback.is_file() else None)
        return custom_net

    legacy_exports = list(visum_dir.glob("*_export_excel_linktypes.net")) if visum_dir.is_dir() else []
    if legacy_exports:
        return max(legacy_exports, key=lambda p: p.stat().st_mtime)
    return fallback


def apply_linktypes_to_modified_osm(
    project_dir: str | Path | None,
    *,
    force: bool = False,
    log=print,
) -> bool:
    """Re-classify ``road_network_hierarchical_modified.osm`` using the active master `.net` brackets.

    Only rewrites the modified OSM file when ``road_network_hierarchical_original.osm``
    exists and either ``force=True`` or ``custom_export_excel_linktypes.net`` is newer
    than ``road_network_hierarchical_modified.osm``.
    """
    if not project_dir:
        return False
    base = Path(project_dir)
    net_dir = base / "processed" / "osm" / "04_network"
    orig_osm = net_dir / "road_network_hierarchical_original.osm"
    mod_osm = net_dir / "road_network_hierarchical_modified.osm"
    if not orig_osm.is_file():
        return False

    fallback = (
        Path(tool_root()) / "core" / "scripts" / "visum" / "helper_files" / "master_linktypes.net"
    )
    active_net = resolve_project_master_net(base, fallback)
    if not active_net.is_file():
        return False

    if not force:
        cfg_file = base / CONFIG_FILENAME
        if not cfg_file.is_file() and active_net == fallback:
            return False
        if mod_osm.is_file() and active_net.stat().st_mtime <= mod_osm.stat().st_mtime:
            return False

    from core.scripts.osm.export_network import (
        calculate_dynamic_brackets,
        impute_and_classify_osm,
        parse_net_link_types,
    )

    df_types = parse_net_link_types(str(active_net), log=log)
    brackets_db = calculate_dynamic_brackets(df_types)
    tmp_out = mod_osm.with_suffix(".osm.tmp")
    impute_and_classify_osm(str(orig_osm), str(tmp_out), brackets_db, log=log)
    tmp_out.replace(mod_osm)
    return True

