import heapq
import math
import os
import subprocess
import re
import shutil
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np


# ==============================================================================
# 1. STRUKTURANALYSE DES MASTER-NETZES (Für die Bracket-Berechnung)
# ==============================================================================

def parse_net_link_types(master_net_path, log=print):
    """Liest die $LINKTYPE-Tabelle direkt aus der Master-.net-Textdatei."""
    log(f"Analysiere Master-Netzdatei für Klassen-Bracketing: {master_net_path}")
    with open(master_net_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    link_type_lines = []
    headers = []
    is_table = False
    for line in lines:
        line_str = line.strip()
        if line_str.startswith('$LINKTYPE:'):
            is_table = True
            headers = [h.strip() for h in line_str.replace('$LINKTYPE:', '').split(';')]
            continue
        if is_table:
            if line_str.startswith('$') or line_str.startswith('*') or not line_str:
                if link_type_lines: break
                continue
            link_type_lines.append([v.strip() for v in line_str.split(';')])

    df = pd.DataFrame(link_type_lines, columns=headers[:len(link_type_lines[0])])
    df['TYPENO'] = pd.to_numeric(df['NO'], errors='coerce').fillna(0).astype(int)
    df['NUMLANES'] = pd.to_numeric(df['NUMLANES'], errors='coerce').fillna(1).astype(int)
    df['V0PRT'] = pd.to_numeric(df['V0PRT'], errors='coerce').fillna(30).astype(float)
    return df[['TYPENO', 'NAME', 'NUMLANES', 'V0PRT']]


def calculate_dynamic_brackets(df_all_types):
    """Berechnet die mathematischen Klassengrenzen (Ober-/Untergrenzen) der Geschwindigkeiten."""
    df_new_types = df_all_types[df_all_types['TYPENO'] >= 100].copy()
    # Avoid pandas' Arrow string regex backend here. Some supported QGIS
    # installations bundle pandas with an older PyArrow whose compute module
    # does not yet provide match_substring_regex.
    non_bus_rows = ["bus" not in str(value).casefold() for value in df_new_types["NAME"]]
    df_new_types = df_new_types.loc[non_bus_rows].copy()

    cat_map_abbr = {
        "Mo": "Motorway", "Tr": "Trunk", "Pr": "Primary", "Se": "Secondary", "Te": "Tertiary",
        "Un": "Unclassified", "Re": "Residential", "Link Mo": "Motorway_link", "Link Tr": "Trunk_link",
        "Link Pr": "Primary_link", "Link Se": "Secondary_link", "Link Te": "Tertiary_link"
    }

    def get_base_name(name):
        parts = name.split(" ")
        if len(parts) > 1 and f"{parts[0]} {parts[1]}" in cat_map_abbr:
            return cat_map_abbr[f"{parts[0]} {parts[1]}"]
        if parts[0] in cat_map_abbr:
            return cat_map_abbr[parts[0]]
        return name

    df_new_types["OSM_NAME"] = df_new_types["NAME"].apply(get_base_name)
    brackets_db = {}

    for category_name in df_new_types['OSM_NAME'].unique():
        category_df = df_new_types[df_new_types['OSM_NAME'] == category_name]
        brackets_db[category_name] = {}

        for num_lanes in category_df['NUMLANES'].unique():
            lane_specific_types = category_df[category_df['NUMLANES'] == num_lanes].sort_values('V0PRT',
                                                                                                ascending=False)
            speed_steps = lane_specific_types['V0PRT'].tolist()
            brackets_db[category_name][num_lanes] = []

            for j, v0prt in enumerate(speed_steps):
                upper_bound = float('inf') if j == 0 else (v0prt + speed_steps[j - 1]) / 2
                lower_bound = 0 if j == len(speed_steps) - 1 else (v0prt + speed_steps[j + 1]) / 2

                brackets_db[category_name][num_lanes].append({
                    'target_speed': int(v0prt),
                    'target_lanes': int(num_lanes),
                    'lower_bound': lower_bound,
                    'upper_bound': upper_bound
                })

    return brackets_db


# ==============================================================================
# 2. TOPOLOGISCHE KONTEXT-IMPUTATION & STRUKTURIERTE LOG-GENERIERUNG
# ==============================================================================

def impute_and_classify_osm(input_xml_path, output_xml_path, brackets_db, log=print):
    """Berechnet Mittelwerte, analysiert Nachbarschaften topologisch und harmonisiert das Netz."""
    log("Analysiere Netzwerktopologie und berechne regionale Kennwerte...")
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    osm_speeds, osm_lanes = {}, {}
    node_to_ways = {}  # Intersection dictionary: node_id -> list of way_ids
    original_ways_data = {}  # Unaltered memory snapshot of the raw OSM network

    # Pass 1: Build topological network map and gather global type stats
    for way in root.findall('way'):
        way_id = way.get('id')
        tags = {tag.get('k'): tag.get('v') for tag in way.findall('tag')}
        nodes = [nd.get('ref') for nd in way.findall('nd')]

        if 'highway' not in tags: continue
        hw = tags['highway']

        original_ways_data[way_id] = {'tags': tags, 'nodes': nodes, 'hw': hw}

        # Connect nodes to way elements
        for node_ref in nodes:
            if node_ref not in node_to_ways:
                node_to_ways[node_ref] = []
            node_to_ways[node_ref].append(way_id)

        # Collect values for arithmetic regional average
        if hw not in osm_speeds: osm_speeds[hw], osm_lanes[hw] = [], []
        if 'maxspeed' in tags:
            digits = re.findall(r'\d+', tags['maxspeed'])
            if digits: osm_speeds[hw].append(int(digits[0]))
        if 'lanes' in tags:
            digits = re.findall(r'\d+', tags['lanes'])
            if digits: osm_lanes[hw].append(int(digits[0]))

    # Compute static regional average thresholds
    global_fallbacks = {'motorway': (130, 2), 'trunk': (100, 2), 'primary': (100, 2), 'secondary': (80, 2),
                        'tertiary': (70, 1), 'unclassified': (50, 1), 'residential': (30, 1)}
    mean_rules = {}
    for hw in set(osm_speeds.keys()).union(global_fallbacks.keys()):
        speeds, lanes = osm_speeds.get(hw, []), osm_lanes.get(hw, [])
        avg_speed = float(np.mean(speeds)) if speeds else float(global_fallbacks.get(hw, (130, 2))[0])
        avg_lanes = float(np.mean(lanes)) if lanes else float(global_fallbacks.get(hw, (130, 2))[1])
        mean_rules[hw] = {'speed': avg_speed, 'lanes': max(1.0, avg_lanes)}

    osm_to_cat_map = {'motorway': 'Motorway', 'motorway_link': 'Motorway_link', 'trunk': 'Trunk',
                      'trunk_link': 'Trunk_link', 'primary': 'Primary', 'primary_link': 'Primary_link',
                      'secondary': 'Secondary', 'secondary_link': 'Secondary_link', 'tertiary': 'Tertiary',
                      'tertiary_link': 'Tertiary_link', 'unclassified': 'Unclassified', 'residential': 'Residential'}

    # Pass 2: Neighborhood network scanning, outlier filtration & structured logging
    modified_ways = 0
    for way in root.findall('way'):
        way_id = way.get('id')
        if way_id not in original_ways_data: continue

        w_data = original_ways_data[way_id]
        hw = w_data['hw']
        orig_tags = w_data['tags']
        nodes = w_data['nodes']

        # Promote drivable service roads (e.g. ferry terminal pier approaches) to residential
        # so the Visum importer assigns CAR+WALK instead of non-motorized Service (type 73).
        if hw == 'service':
            service_sub = str(orig_tags.get('service', '')).strip().lower()
            access_val = str(orig_tags.get('access', '')).strip().lower()
            mv_val = str(orig_tags.get('motor_vehicle', '')).strip().lower()
            if (
                service_sub not in {'parking_aisle', 'driveway', 'alley', 'emergency_access'}
                and access_val not in {'private', 'no', 'customers', 'agricultural', 'forestry'}
                and mv_val not in {'private', 'no'}
            ):
                hw = 'residential'
                tags_dict_pre = {tag.get('k'): tag for tag in way.findall('tag')}
                if 'highway_original' not in tags_dict_pre:
                    way.append(ET.Element('tag', k='highway_original', v='service'))
                if 'highway' in tags_dict_pre:
                    tags_dict_pre['highway'].set('v', 'residential')

        fallback = mean_rules.get(hw, {'speed': 30.0, 'lanes': 1.0})
        cat_name = osm_to_cat_map.get(hw, hw.capitalize())

        orig_speed_str = orig_tags.get('maxspeed', 'missing')
        orig_lanes_str = orig_tags.get('lanes', 'missing')

        # ----------------------------------------------------------------------
        # MAXSPEED PROCESSING ENGINE
        # ----------------------------------------------------------------------
        if orig_speed_str != 'missing':
            try:
                raw_speed = int(re.findall(r'\d+', str(orig_speed_str))[0])
                if raw_speed > 250:  # Unplausible threshold
                    current_speed = int(fallback['speed'])
                    speed_log = f"[OSM-Unplausible] Raw: {orig_speed_str} km/h -> Capped to Template-Max:"
                else:
                    current_speed = raw_speed
                    speed_log = f"[OSM-Original] {orig_speed_str} km/h -> Template-Matched:"
            except:
                current_speed = int(fallback['speed'])
                speed_log = f"[OSM-Original] Invalid ({orig_speed_str}) -> Fallback to Regional Avg ({fallback['speed']:.1f} km/h) -> Template-Matched:"
        else:
            # Spatial Neighborhood Query
            neighbor_speed = None
            chosen_neighbor_id = None
            if nodes:
                endpoints = [nodes[0], nodes[-1]] if len(nodes) >= 2 else nodes
                connected_ways = set()
                for ep in endpoints:
                    if ep in node_to_ways: connected_ways.update(node_to_ways[ep])
                connected_ways.discard(way_id)

                for n_id in connected_ways:
                    n_data = original_ways_data.get(n_id)
                    if n_data and osm_to_cat_map.get(n_data['hw']) == cat_name:
                        n_speed_str = n_data['tags'].get('maxspeed', 'missing')
                        if n_speed_str != 'missing':
                            try:
                                neighbor_speed = int(re.findall(r'\d+', str(n_speed_str))[0])
                                chosen_neighbor_id = n_id
                                break
                            except:
                                pass

            if neighbor_speed is not None:
                # Plausibility check against regional mean (Delta max 30 km/h)
                if abs(neighbor_speed - fallback['speed']) <= 30:
                    current_speed = neighbor_speed
                    speed_log = f"[Neighbor-Match] From Neighbor ID {chosen_neighbor_id} ({neighbor_speed} km/h) -> Template-Matched:"
                else:
                    current_speed = int(fallback['speed'])
                    speed_log = f"[Neighbor-No-Match] Neighbor ID {chosen_neighbor_id} ({neighbor_speed} km/h) rejected vs. Regional Avg ({fallback['speed']:.1f} km/h) -> Regional Fallback applied -> Template-Matched:"
            else:
                current_speed = int(fallback['speed'])
                speed_log = f"[Regional-Average] Type '{hw}' regional mean = {fallback['speed']:.1f} km/h -> Template-Matched:"

        # ----------------------------------------------------------------------
        # LANES PROCESSING ENGINE
        # ----------------------------------------------------------------------
        if orig_lanes_str != 'missing':
            try:
                raw_lanes = int(re.findall(r'\d+', str(orig_lanes_str))[0])
                if raw_lanes > 8:  # Unplausible threshold
                    current_lanes = int(np.round(fallback['lanes']))
                    lanes_log = f"[OSM-Unplausible] Raw: {orig_lanes_str} lanes -> Capped to Template-Max:"
                else:
                    current_lanes = raw_lanes
                    lanes_log = f"[OSM-Original] {orig_lanes_str} lanes -> Template-Matched:"
            except:
                current_lanes = int(np.round(fallback['lanes']))
                lanes_log = f"[OSM-Original] Invalid ({orig_lanes_str}) -> Fallback to Regional Avg ({fallback['lanes']:.1f} lanes) -> Template-Matched:"
        else:
            # Spatial Neighborhood Query
            neighbor_lanes = None
            chosen_neighbor_id = None
            if nodes:
                endpoints = [nodes[0], nodes[-1]] if len(nodes) >= 2 else nodes
                connected_ways = set()
                for ep in endpoints:
                    if ep in node_to_ways: connected_ways.update(node_to_ways[ep])
                connected_ways.discard(way_id)

                for n_id in connected_ways:
                    n_data = original_ways_data.get(n_id)
                    if n_data and osm_to_cat_map.get(n_data['hw']) == cat_name:
                        n_lanes_str = n_data['tags'].get('lanes', 'missing')
                        if n_lanes_str != 'missing':
                            try:
                                neighbor_lanes = int(re.findall(r'\d+', str(n_lanes_str))[0])
                                chosen_neighbor_id = n_id
                                break
                            except:
                                pass

            if neighbor_lanes is not None:
                # Plausibility check against regional mean (Delta max 2 lanes)
                if abs(neighbor_lanes - fallback['lanes']) <= 2:
                    current_lanes = neighbor_lanes
                    lanes_log = f"[Neighbor-Match] From Neighbor ID {chosen_neighbor_id} ({neighbor_lanes} lanes) -> Template-Matched:"
                else:
                    current_lanes = int(np.round(fallback['lanes']))
                    lanes_log = f"[Neighbor-No-Match] Neighbor ID {chosen_neighbor_id} ({neighbor_lanes} lanes) rejected vs. Regional Avg ({fallback['lanes']:.1f} lanes) -> Regional Fallback applied -> Template-Matched:"
            else:
                rounded_lanes = int(np.round(fallback['lanes']))
                current_lanes = rounded_lanes
                lanes_log = f"[Regional-Average] Type '{hw}' regional mean = {fallback['lanes']:.1f} lanes (rounded: {rounded_lanes}) -> Template-Matched:"

        # ----------------------------------------------------------------------
        # BRACKET MATCHING & ADJUSTMENT TO FIXED TEMPLATE LINK TYPES
        # ----------------------------------------------------------------------
        target_speed, target_lanes = current_speed, current_lanes
        if cat_name in brackets_db:
            available_lanes = brackets_db[cat_name]
            chosen_lanes = current_lanes if current_lanes in available_lanes else max(available_lanes.keys(),
                                                                                      default=current_lanes)
            if chosen_lanes in available_lanes:
                for bracket in available_lanes[chosen_lanes]:
                    if bracket['lower_bound'] <= current_speed < bracket['upper_bound']:
                        target_speed = bracket['target_speed']
                        target_lanes = bracket['target_lanes']
                        break

        # Append structured resolution text string
        maxspeed_source = f"{speed_log} {target_speed} km/h"
        lanes_source = f"{lanes_log} {target_lanes} lanes"

        # Update XML elements
        tags_dict = {tag.get('k'): tag for tag in way.findall('tag')}
        tags_to_write = [
            ('maxspeed', target_speed),
            ('lanes', target_lanes),
            ('maxspeed_original', orig_speed_str),
            ('lanes_original', orig_lanes_str),
            ('maxspeed_source', maxspeed_source),
            ('lanes_source', lanes_source)
        ]

        for key, final_val in tags_to_write:
            if key in tags_dict:
                tags_dict[key].set('v', str(final_val))
            else:
                way.append(ET.Element('tag', k=key, v=str(final_val)))
            modified_ways += 1

    tree.write(output_xml_path, encoding='utf-8', xml_declaration=True)
    log(f"Bearbeitete Netzdatei erfolgreich aktualisiert: {output_xml_path}")


_DRIVABLE_HIGHWAYS = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "road",
})

_DENY_ACCESS_VALUES = frozenset({"no", "private"})
_ALLOW_ACCESS_VALUES = frozenset({"yes", "permissive", "destination", "customers"})


def _is_car_drivable_way(tags: dict) -> bool:
    """Return True if an OSM way's tags permit motorized car routing."""
    if tags.get("highway") not in _DRIVABLE_HIGHWAYS:
        return False
    if tags.get("motor_vehicle") in _DENY_ACCESS_VALUES:
        return False
    if tags.get("motorcar") in _DENY_ACCESS_VALUES:
        return False
    if tags.get("vehicle") in _DENY_ACCESS_VALUES:
        return False
    if tags.get("access") in _DENY_ACCESS_VALUES:
        if (
            tags.get("motor_vehicle") not in _ALLOW_ACCESS_VALUES
            and tags.get("motorcar") not in _ALLOW_ACCESS_VALUES
        ):
            return False
    return True


def _way_direction(tags: dict) -> int:
    """Return 1 for forward-only, -1 for reverse-only, or 0 for bidirectional."""
    oneway = (tags.get("oneway") or "").strip().lower()
    if oneway in {"-1", "reverse"}:
        return -1
    if oneway in {"yes", "1", "true"}:
        return 1
    if oneway in {"no", "0", "false"}:
        return 0
    if (tags.get("junction") or "").strip().lower() in {"roundabout", "circular"}:
        return 1
    if tags.get("highway") in {"motorway", "motorway_link"}:
        return 1
    return 0


def _metric_dist_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Fast local equirectangular distance in meters (accurate to <0.05% within 10 km)."""
    mid_lat = math.radians(0.5 * (lat1 + lat2))
    dx = (lon2 - lon1) * 111320.0 * math.cos(mid_lat)
    dy = (lat2 - lat1) * 111320.0
    return math.hypot(dx, dy)


def _dijkstra_to_targets(
    start_node: str,
    adj: dict,
    target_nodes: set,
    max_dist_m: float,
):
    """Shortest path from `start_node` to nearest node in `target_nodes` capped at `max_dist_m`.

    Returns `(reached_target, total_dist_m, set_of_way_ids)` or `(None, inf, set())`.
    """
    heap = [(0.0, start_node)]
    best = {start_node: 0.0}
    parent = {}  # node -> (prev_node, way_id)

    while heap:
        dist, u = heapq.heappop(heap)
        if dist > best.get(u, float("inf")):
            continue
        if u in target_nodes:
            used_ways = set()
            cur = u
            while cur != start_node:
                prev_node, wid = parent[cur]
                used_ways.add(wid)
                cur = prev_node
            return u, dist, used_ways
        for v, edge_len, wid in adj.get(u, ()):
            ndist = dist + edge_len
            if ndist <= max_dist_m and ndist < best.get(v, float("inf")):
                best[v] = ndist
                parent[v] = (u, wid)
                heapq.heappush(heap, (ndist, v))

    return None, float("inf"), set()


def _write_ferry_terminal_buffer_poly(ferry_osm_path: str, poly_out_path: str, radius_m: float = 5000.0) -> bool:
    """Create an Osmium .poly file buffering all route=ferry way endpoints by `radius_m` meters."""
    if not os.path.isfile(ferry_osm_path):
        return False
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError:
        return False

    node_coords: dict[str, tuple[float, float]] = {}
    endpoint_ids: set[str] = set()
    for _ev, elem in ET.iterparse(ferry_osm_path, events=("end",)):
        if elem.tag == "node":
            nid = elem.get("id")
            if nid and "lon" in elem.attrib and "lat" in elem.attrib:
                node_coords[nid] = (float(elem.attrib["lon"]), float(elem.attrib["lat"]))
            elem.clear()
        elif elem.tag == "way":
            tags = {t.get("k", ""): t.get("v", "") for t in elem.findall("tag")}
            if tags.get("route") == "ferry":
                nds = [nd.get("ref") for nd in elem.findall("nd") if nd.get("ref")]
                if nds:
                    endpoint_ids.add(nds[0])
                    endpoint_ids.add(nds[-1])
            elem.clear()

    polys = []
    n_verts = 16
    for nid in endpoint_ids:
        if nid not in node_coords:
            continue
        lon, lat = node_coords[nid]
        dlat = radius_m / 111320.0
        cos_lat = max(0.1, math.cos(math.radians(lat)))
        dlon = radius_m / (111320.0 * cos_lat)
        pts = [
            (
                lon + dlon * math.cos(2.0 * math.pi * k / n_verts),
                lat + dlat * math.sin(2.0 * math.pi * k / n_verts),
            )
            for k in range(n_verts)
        ]
        polys.append(Polygon(pts))

    if not polys:
        return False

    merged = unary_union(polys)
    geoms = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    lines = ["ferry_terminal_buffer"]
    poly_idx = 0
    for poly in geoms:
        if poly.is_empty:
            continue
        poly_idx += 1
        lines.append(str(poly_idx))
        for x, y in poly.exterior.coords:
            lines.append(f"   {x:.6f}   {y:.6f}")
        lines.append("END")
    lines.append("END")
    with open(poly_out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return poly_idx > 0


def _route_ferry_access_ways(
    ferry_osm_path: str,
    candidates_osm_path: str,
    target_roads_osm_path: str,
    output_osm_path: str,
    *,
    max_route_m: float = 5000.0,
    start_snap_m: float = 200.0,
    max_detour_ratio: float = 2.5,
    short_dist_m: float = 300.0,
    short_detour_ratio: float = 4.0,
    log=print,
) -> dict:
    """Select ONLY the OSM road ways on plausible car routes from isolated ferry endpoints
    to the hierarchical target road network (`PA/IA1 + IA2 + OA`).

    Terminals without a plausible bidirectional car route (or with an implausible detour
    compared to straight-line distance) receive no buffer ways so Visum's `Ferry_Access`
    fallback connects them cleanly without polluting the network with stray local streets.
    """
    stats = {
        "endpoints": 0,
        "already_connected": 0,
        "routed": 0,
        "fallback": 0,
        "candidate_ways": 0,
        "selected_ways": 0,
    }
    if not (os.path.isfile(ferry_osm_path) and os.path.isfile(candidates_osm_path)):
        return stats

    # 1. Read ferry way endpoints & coordinates
    ferry_node_coords: dict[str, tuple[float, float]] = {}
    endpoint_ids: set[str] = set()
    for _ev, elem in ET.iterparse(ferry_osm_path, events=("end",)):
        if elem.tag == "node":
            nid = elem.get("id")
            if nid and "lon" in elem.attrib and "lat" in elem.attrib:
                ferry_node_coords[nid] = (float(elem.attrib["lon"]), float(elem.attrib["lat"]))
            elem.clear()
        elif elem.tag == "way":
            tags = {t.get("k", ""): t.get("v", "") for t in elem.findall("tag")}
            if tags.get("route") == "ferry":
                nds = [nd.get("ref") for nd in elem.findall("nd") if nd.get("ref")]
                if nds:
                    endpoint_ids.add(nds[0])
                    endpoint_ids.add(nds[-1])
            elem.clear()

    stats["endpoints"] = len(endpoint_ids)
    if not endpoint_ids:
        return stats

    # 2. Read target road network nodes already present in hierarchical network (PA/IA1 + IA2 + OA)
    target_node_ids: set[str] = set()
    target_way_ids: set[str] = set()
    if os.path.isfile(target_roads_osm_path) and os.path.getsize(target_roads_osm_path) > 0:
        for _ev, elem in ET.iterparse(target_roads_osm_path, events=("end",)):
            if elem.tag == "way":
                tags = {t.get("k", ""): t.get("v", "") for t in elem.findall("tag")}
                if _is_car_drivable_way(tags):
                    wid = elem.get("id")
                    if wid:
                        target_way_ids.add(wid)
                    for nd in elem.findall("nd"):
                        ref = nd.get("ref")
                        if ref:
                            target_node_ids.add(ref)
                elem.clear()
            elif elem.tag == "node":
                elem.clear()

    if not target_node_ids:
        stats["fallback"] = len(endpoint_ids)
        return stats

    # 3. Build directed car routing graph from 5 km candidate buffer extract
    node_coords: dict[str, tuple[float, float]] = {}
    fwd_adj: dict[str, list[tuple[str, float, str]]] = {}
    rev_adj: dict[str, list[tuple[str, float, str]]] = {}
    way_nodes: dict[str, list[str]] = {}
    graph_node_ids: set[str] = set()

    for _ev, elem in ET.iterparse(candidates_osm_path, events=("end",)):
        if elem.tag == "node":
            nid = elem.get("id")
            if nid and "lon" in elem.attrib and "lat" in elem.attrib:
                node_coords[nid] = (float(elem.attrib["lon"]), float(elem.attrib["lat"]))
            elem.clear()
        elif elem.tag == "way":
            wid = elem.get("id")
            tags = {t.get("k", ""): t.get("v", "") for t in elem.findall("tag")}
            if wid and _is_car_drivable_way(tags):
                stats["candidate_ways"] += 1
                nds = [nd.get("ref") for nd in elem.findall("nd") if nd.get("ref")]
                if len(nds) >= 2:
                    way_nodes[wid] = nds
                    direction = _way_direction(tags)
                    for u, v in zip(nds, nds[1:]):
                        if u not in node_coords or v not in node_coords:
                            continue
                        lon1, lat1 = node_coords[u]
                        lon2, lat2 = node_coords[v]
                        seg_m = _metric_dist_m(lon1, lat1, lon2, lat2)
                        graph_node_ids.add(u)
                        graph_node_ids.add(v)
                        if direction in (0, 1):
                            fwd_adj.setdefault(u, []).append((v, seg_m, wid))
                            rev_adj.setdefault(v, []).append((u, seg_m, wid))
                        if direction in (0, -1):
                            fwd_adj.setdefault(v, []).append((u, seg_m, wid))
                            rev_adj.setdefault(u, []).append((v, seg_m, wid))
            elem.clear()

    if not graph_node_ids:
        stats["fallback"] = len(endpoint_ids)
        return stats

    # Optional spatial index for fast <= start_snap_m snapping of offset ferry endpoints
    graph_ids_list = list(graph_node_ids)
    mean_lat = sum(node_coords[n][1] for n in graph_ids_list) / len(graph_ids_list)
    cos_mean = max(0.1, math.cos(math.radians(mean_lat)))
    kdtree = None
    try:
        from scipy.spatial import cKDTree
        pts_m = np.array(
            [(node_coords[n][0] * 111320.0 * cos_mean, node_coords[n][1] * 111320.0) for n in graph_ids_list],
            dtype=float,
        )
        kdtree = cKDTree(pts_m)
    except Exception:
        kdtree = None

    def _find_start_node(fid: str):
        if fid in graph_node_ids:
            return fid
        if fid not in ferry_node_coords:
            return None
        flon, flat = ferry_node_coords[fid]
        if kdtree is not None:
            dist, idx = kdtree.query([flon * 111320.0 * cos_mean, flat * 111320.0], distance_upper_bound=start_snap_m * 1.05)
            if math.isfinite(dist) and idx < len(graph_ids_list):
                cand = graph_ids_list[int(idx)]
                if _metric_dist_m(flon, flat, *node_coords[cand]) <= start_snap_m:
                    return cand
            return None
        best_nid = None
        best_d = start_snap_m
        dlat_lim = start_snap_m / 111320.0 * 1.1
        dlon_lim = dlat_lim / cos_mean
        for nid in graph_ids_list:
            nlon, nlat = node_coords[nid]
            if abs(nlat - flat) > dlat_lim or abs(nlon - flon) > dlon_lim:
                continue
            d = _metric_dist_m(flon, flat, nlon, nlat)
            if d <= best_d:
                best_d = d
                best_nid = nid
        return best_nid

    def _is_plausible(start_nid: str, target_nid, route_len: float) -> bool:
        if target_nid is None or route_len > max_route_m:
            return False
        air_dist = _metric_dist_m(*node_coords[start_nid], *node_coords[target_nid])
        limit_ratio = short_detour_ratio if air_dist < short_dist_m else max_detour_ratio
        return (route_len / max(air_dist, 50.0)) <= limit_ratio

    # 4. Route each isolated ferry endpoint (forward & reverse) to the target road network
    selected_way_ids: set[str] = set()
    for fid in sorted(endpoint_ids):
        if fid in target_node_ids:
            stats["already_connected"] += 1
            continue
        start_nid = _find_start_node(fid)
        if start_nid is None:
            stats["fallback"] += 1
            continue
        if start_nid in target_node_ids:
            stats["already_connected"] += 1
            continue

        t_fwd, dist_fwd, ways_fwd = _dijkstra_to_targets(start_nid, fwd_adj, target_node_ids, max_route_m)
        t_rev, dist_rev, ways_rev = _dijkstra_to_targets(start_nid, rev_adj, target_node_ids, max_route_m)

        if _is_plausible(start_nid, t_fwd, dist_fwd) and _is_plausible(start_nid, t_rev, dist_rev):
            selected_way_ids.update(ways_fwd)
            selected_way_ids.update(ways_rev)
            stats["routed"] += 1
        else:
            stats["fallback"] += 1

    # Exclude ways already present in the hierarchical base network
    selected_way_ids -= target_way_ids
    stats["selected_ways"] = len(selected_way_ids)
    if not selected_way_ids:
        log(
            f"D2) Fähr-Routing: {stats['endpoints']} Endpunkte geprüft "
            f"({stats['already_connected']} bereits angebunden, {stats['routed']} geroutet, "
            f"{stats['fallback']} via Ferry_Access) -> 0 zusätzliche Zubringer-Ways."
        )
        return stats

    needed_node_ids: set[str] = set()
    for wid in selected_way_ids:
        needed_node_ids.update(way_nodes.get(wid, ()))

    # 5. Stream-write minimal sorted .osm XML containing ONLY selected ways & their nodes
    out_root = ET.Element("osm", {"version": "0.6", "generator": "PANDO_FerryAccessRouter"})
    for _ev, elem in ET.iterparse(candidates_osm_path, events=("end",)):
        if elem.tag == "node":
            if elem.get("id") in needed_node_ids:
                out_root.append(elem)
            else:
                elem.clear()
        elif elem.tag == "way":
            if elem.get("id") in selected_way_ids:
                out_root.append(elem)
            else:
                elem.clear()

    ET.ElementTree(out_root).write(output_osm_path, encoding="utf-8", xml_declaration=True)
    log(
        f"D2) Fähr-Routing: {stats['endpoints']} Endpunkte geprüft "
        f"({stats['already_connected']} bereits angebunden, {stats['routed']} über OSM-Netz geroutet, "
        f"{stats['fallback']} via Ferry_Access) -> {stats['selected_ways']} von {stats['candidate_ways']} "
        f"Puffer-Ways übernommen."
    )
    return stats


# ==============================================================================
# 3. OSMIUM PIPELINE EXECUTIVE
# ==============================================================================

def build_hierarchical_network(merged_pbf, osm_dir, osmium_exe, bin_dir, poly_pa_ia1, poly_ia2, master_net_path,
                               template_cfg_path=None, template_xml_path=None, *, output_original=None,
                               output_modified=None, run_command=None, log=print, progress=None):
    progress = progress or (lambda _name, _index, _total: None)
    total_steps = 11
    path_poly_pa_ia1 = poly_pa_ia1 if os.path.isabs(poly_pa_ia1) else os.path.join(osm_dir, poly_pa_ia1)
    path_poly_ia2 = poly_ia2 if os.path.isabs(poly_ia2) else os.path.join(osm_dir, poly_ia2)

    def has_poly_rings(filepath: str) -> bool:
        if not os.path.isfile(filepath):
            return False
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = [line.strip() for line in f if line.strip()]
            coord_lines = [line for line in lines if re.match(r"^-?\d+\.?\d*\s+-?\d+\.?\d*", line)]
            return len(coord_lines) >= 3
        except Exception:
            return False

    if not os.path.exists(path_poly_pa_ia1) or not os.path.exists(path_poly_ia2):
        log("Fehler: .poly-Dateien fehlen.")
        return None

    if not has_poly_rings(path_poly_pa_ia1):
        if has_poly_rings(path_poly_ia2):
            log("Hinweis: Kernzone (PA+IA1) .poly enthält keine Ringe; verwende Study-Area-Polygon für den Zuschnitt.")
            path_poly_pa_ia1 = path_poly_ia2
        else:
            log("Fehler: Die .poly-Dateien enthalten keine gültigen Polygon-Koordinaten.")
            return None

    try:
        progress("master_network", 0, total_steps)
        df_types = parse_net_link_types(master_net_path, log=log)
        brackets_db = calculate_dynamic_brackets(df_types)
    except Exception as e:
        raise RuntimeError(f"Master-Netz-Analyse fehlgeschlagen: {e}") from e

    rel_temp_pa_ia1 = "temp_net_pa_ia1_extract.osm.pbf"
    rel_net_pa_ia1 = "temp_net_1_pa_ia1.osm.pbf"
    rel_temp_ia2 = "temp_net_ia2_extract.osm.pbf"
    rel_net_ia2 = "temp_net_2_ia2.osm.pbf"
    rel_net_oa = "temp_net_3_oa.osm.pbf"
    rel_net_ferry = "temp_net_4_ferry.osm.pbf"
    rel_ferry_osm = "temp_net_4_ferry.osm"
    rel_ferry_poly = "temp_ferry_buffer.poly"
    rel_ferry_buf_extract = "temp_ferry_buf_extract.osm.pbf"
    rel_ferry_candidates_osm = "temp_ferry_candidates.osm"
    rel_temp_base_roads = "temp_net_base_roads.osm.pbf"
    rel_ferry_target_osm = "temp_ferry_target_roads.osm"
    rel_net_ferry_access_routed = "temp_net_5_ferry_access_routed.osm"
    rel_ferry_waterways = "temp_ferry_waterways.osm"

    rel_final = "hierarchical_network.osm"
    abs_final_osm = os.path.join(osm_dir, rel_final)
    abs_final_orig_xml = os.path.join(osm_dir, "hierarchical_network_original.osm")
    abs_final_mod_xml = os.path.join(osm_dir, "hierarchical_network_modified.osm")
    output_original = output_original or os.path.join(osm_dir, "road_network_hierarchical_original.osm")
    output_modified = output_modified or os.path.join(osm_dir, "road_network_hierarchical_modified.osm")

    env = os.environ.copy()
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")

    def run_osmium(cmd):
        if run_command:
            return run_command(cmd[1:], cwd=osm_dir)
        kwargs = {"env": env, "capture_output": True, "text": True, "cwd": osm_dir}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        res = subprocess.run(cmd, **kwargs)
        if res.returncode != 0:
            raise Exception(f"Osmium Fehler: {res.stderr.strip()}")
        return res

    try:
        progress("network_pa_extract", 1, total_steps)
        log("A) Verarbeite Kernzone (PA + IA1): Gebiet ausschneiden ...")
        run_osmium([osmium_exe, "extract", "-p", path_poly_pa_ia1, merged_pbf, "-o", rel_temp_pa_ia1, "--overwrite"])
        progress("network_pa_filter", 2, total_steps)
        log("A) Verarbeite Kernzone (PA + IA1): Straßentypen filtern ...")
        filter_pa_ia1 = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified,residential"
        run_osmium([osmium_exe, "tags-filter", rel_temp_pa_ia1, filter_pa_ia1, "-o", rel_net_pa_ia1, "--overwrite"])

        progress("network_ia2_extract", 3, total_steps)
        log("B) Verarbeite Einflusszone (IA2): Gebiet ausschneiden ...")
        run_osmium([osmium_exe, "extract", "-p", path_poly_ia2, merged_pbf, "-o", rel_temp_ia2, "--overwrite"])
        progress("network_ia2_filter", 4, total_steps)
        log("B) Verarbeite Einflusszone (IA2): Straßentypen filtern ...")
        filter_ia2 = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified"
        run_osmium([osmium_exe, "tags-filter", rel_temp_ia2, filter_ia2, "-o", rel_net_ia2, "--overwrite"])

        progress("network_oa_filter", 5, total_steps)
        log("C) Verarbeite Außenraum (OA) ...")
        filter_oa = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link"
        run_osmium([osmium_exe, "tags-filter", merged_pbf, filter_oa, "-o", rel_net_oa, "--overwrite"])

        progress("network_ferry_filter", 6, total_steps)
        log("D) Extrahiere Fährverbindungen ...")
        run_osmium([osmium_exe, "tags-filter", merged_pbf, "w/route=ferry", "-o", rel_net_ferry, "--overwrite"])

        merge_inputs = [rel_net_pa_ia1, rel_net_ia2, rel_net_oa, rel_net_ferry]
        abs_net_ferry = os.path.join(osm_dir, rel_net_ferry)
        if os.path.isfile(abs_net_ferry) and os.path.getsize(abs_net_ferry) > 0:
            try:
                run_osmium([osmium_exe, "cat", rel_net_ferry, "-o", rel_ferry_osm, "--overwrite"])
                abs_ferry_osm = os.path.join(osm_dir, rel_ferry_osm)
                abs_ferry_poly = os.path.join(osm_dir, rel_ferry_poly)
                if _write_ferry_terminal_buffer_poly(abs_ferry_osm, abs_ferry_poly, radius_m=5000.0):
                    log("D2) Route isolierte Fähranleger im 5000 m-Suchraum ins hierarchische Straßennetz ...")
                    run_osmium([osmium_exe, "extract", "-p", rel_ferry_poly, merged_pbf, "-o", rel_ferry_buf_extract, "--overwrite"])
                    filter_ferry_access = (
                        "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,"
                        "secondary,secondary_link,tertiary,tertiary_link,unclassified,residential,"
                        "living_street,service,road"
                    )
                    run_osmium([osmium_exe, "tags-filter", rel_ferry_buf_extract, filter_ferry_access, "-o", rel_ferry_candidates_osm, "--overwrite"])
                    run_osmium([osmium_exe, "merge", rel_net_pa_ia1, rel_net_ia2, rel_net_oa, "-o", rel_temp_base_roads, "--overwrite"])
                    run_osmium([osmium_exe, "extract", "-p", rel_ferry_poly, rel_temp_base_roads, "-o", rel_ferry_target_osm, "--overwrite"])

                    abs_ferry_candidates = os.path.join(osm_dir, rel_ferry_candidates_osm)
                    abs_ferry_target = os.path.join(osm_dir, rel_ferry_target_osm)
                    abs_ferry_routed = os.path.join(osm_dir, rel_net_ferry_access_routed)
                    route_stats = _route_ferry_access_ways(
                        abs_ferry_osm,
                        abs_ferry_candidates,
                        abs_ferry_target,
                        abs_ferry_routed,
                        max_route_m=5000.0,
                        start_snap_m=200.0,
                        max_detour_ratio=2.5,
                        short_dist_m=300.0,
                        short_detour_ratio=4.0,
                        log=log,
                    )
                    merge_inputs = [rel_temp_base_roads, rel_net_ferry]
                    if route_stats.get("selected_ways", 0) > 0 and os.path.isfile(abs_ferry_routed):
                        merge_inputs.append(rel_net_ferry_access_routed)

                    # Extract river/canal centerlines separately (NOT merged into road network so PANDO_Importer never imports waterways as roads)
                    filter_waterways = "w/waterway=river,canal,tidal_channel,fairway"
                    run_osmium([osmium_exe, "tags-filter", rel_ferry_buf_extract, filter_waterways, "-o", rel_ferry_waterways, "--overwrite"])
                    abs_temp_waterways = os.path.join(osm_dir, rel_ferry_waterways)
                    if os.path.isfile(abs_temp_waterways) and os.path.getsize(abs_temp_waterways) > 0:
                        target_dir = os.path.dirname(output_modified) or osm_dir
                        os.makedirs(target_dir, exist_ok=True)
                        shutil.copyfile(abs_temp_waterways, os.path.join(target_dir, "ferry_waterways.osm"))
            except Exception as buf_exc:
                log(f"Hinweis: Fähranleger-Routing konnte nicht durchgeführt werden ({buf_exc}).")

        progress("network_merge", 7, total_steps)
        log("E) Führe alle Netz-Schichten zusammen ...")
        run_osmium([osmium_exe, "merge", *merge_inputs, "-o", rel_final, "--overwrite"])

        # 1. Save out raw unaltered baseline copy
        shutil.copyfile(abs_final_osm, abs_final_orig_xml)
        progress("network_original", 8, total_steps)
        if output_original.endswith(".osm"):
            shutil.copyfile(abs_final_orig_xml, output_original)
        else:
            run_osmium([osmium_exe, "cat", abs_final_orig_xml, "-o", output_original, "--overwrite"])
        log(f"Unbearbeitetes Straßennetz gespeichert: {output_original}")

        # 2. Run neighborhood topological imputation engine and output full modified copy
        progress("network_classification", 9, total_steps)
        impute_and_classify_osm(abs_final_osm, abs_final_mod_xml, brackets_db, log=log)
        progress("network_modified", 10, total_steps)
        if output_modified.endswith(".osm"):
            shutil.copyfile(abs_final_mod_xml, output_modified)
        else:
            run_osmium([osmium_exe, "cat", abs_final_mod_xml, "-o", output_modified, "--overwrite"])
        log(f"Bearbeitetes Straßennetz gespeichert: {output_modified}")

        # Cleanup transient working file
        if os.path.exists(abs_final_osm):
            os.remove(abs_final_osm)

        return {"network_original": output_original, "network_modified": output_modified}
    except Exception as e:
        log(f"Fehler in der Pipeline: {e}")
        return None
    finally:
        for tmp in [
            rel_temp_pa_ia1, rel_net_pa_ia1, rel_temp_ia2, rel_net_ia2,
            rel_net_oa, rel_net_ferry, rel_ferry_osm, rel_ferry_poly,
            rel_ferry_buf_extract, rel_ferry_candidates_osm, rel_temp_base_roads,
            rel_ferry_target_osm, rel_net_ferry_access_routed, rel_ferry_waterways,
            abs_final_osm, abs_final_orig_xml, abs_final_mod_xml
        ]:
            abs_tmp = tmp if os.path.isabs(tmp) else os.path.join(osm_dir, tmp)
            if os.path.exists(abs_tmp): os.remove(abs_tmp)
