"""Ferry node classification, automatic road-network connection, and turn activation for PTV Visum."""

from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString

FERRY_TERMINAL_TAG_VALUES = {
    "amenity": {"ferry_terminal"},
    "ferry": {"yes"},
    "mooring": {"ferry", "yes"},
    "man_made": {"pier"},
    "waterway": {"dock"},
}


def is_osm_ferry_terminal_tags(tags: dict[str, str]) -> bool:
    """Return True if an OSM tag dictionary marks a ferry terminal / pier node."""
    if not tags:
        return False
    for key, allowed_values in FERRY_TERMINAL_TAG_VALUES.items():
        val = str(tags.get(key, "")).strip().lower()
        if val in allowed_values:
            return True
    return False


def _normalize_osm_id(value) -> str:
    """Normalize numeric/string OSM node IDs to clean integer strings."""
    if value is None:
        return ""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        if math.isnan(float(value)):
            return ""
        return str(int(value))
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return ""
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


def _parse_tsys_tokens(tsysset: str) -> set[str]:
    if not tsysset:
        return set()
    return {tok.strip().upper() for tok in str(tsysset).split(",") if tok.strip()}


def _has_car(tsysset: str) -> bool:
    tokens = _parse_tsys_tokens(tsysset)
    return "CAR" in tokens


def extract_osm_ferry_terminal_info(
    osm_path: str | Path,
) -> tuple[set[str], dict[str, tuple[float, float]], set[str]]:
    """Parse an .osm XML file for tagged ferry terminal nodes and mid-only ferry nodes.

    Returns:
        terminal_ids: Set of OSM node IDs carrying ferry terminal tags.
        terminal_lonlats: Mapping of terminal OSM node ID -> (lon, lat) in WGS84 degrees.
        mid_only_ferry_ids: Set of OSM node IDs that appear only as intermediate vertices
            of `route=ferry` ways (never as way endpoints).
    """
    osm_path = Path(osm_path)
    if not osm_path.exists() or osm_path.suffix.lower() != ".osm":
        return set(), {}, set()

    terminal_ids: set[str] = set()
    terminal_lonlats: dict[str, tuple[float, float]] = {}
    ferry_endpoints: set[str] = set()
    ferry_midnodes: set[str] = set()

    for _event, elem in ET.iterparse(str(osm_path), events=("end",)):
        tag_name = elem.tag
        if tag_name == "node":
            tags = {t.attrib.get("k", ""): t.attrib.get("v", "") for t in elem.findall("tag")}
            if is_osm_ferry_terminal_tags(tags):
                nid = _normalize_osm_id(elem.attrib.get("id"))
                if nid:
                    terminal_ids.add(nid)
                    try:
                        lon = float(elem.attrib["lon"])
                        lat = float(elem.attrib["lat"])
                        terminal_lonlats[nid] = (lon, lat)
                    except (KeyError, ValueError, TypeError):
                        pass
            elem.clear()
        elif tag_name == "way":
            tags = {t.attrib.get("k", ""): t.attrib.get("v", "") for t in elem.findall("tag")}
            if tags.get("route") == "ferry":
                nds = [_normalize_osm_id(nd.attrib.get("ref")) for nd in elem.findall("nd")]
                nds = [nid for nid in nds if nid]
                if len(nds) >= 2:
                    ferry_endpoints.add(nds[0])
                    ferry_endpoints.add(nds[-1])
                    ferry_midnodes.update(nds[1:-1])
            elem.clear()

    mid_only_ferry_ids = ferry_midnodes - ferry_endpoints
    return terminal_ids, terminal_lonlats, mid_only_ferry_ids


def _wgs84_to_transverse_mercator(
    lons: Iterable[float],
    lats: Iterable[float],
    central_meridian: float,
    false_easting: float = 500000.0,
    false_northing: float = 0.0,
    scale_factor: float = 0.9996,
) -> list[tuple[float, float]]:
    """Convert WGS84 lon/lat coordinates to Transverse Mercator (e.g. UTM) in meters."""
    a = 6378137.0
    f = 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    ep2 = e2 / (1.0 - e2)
    lon0 = math.radians(central_meridian)

    results: list[tuple[float, float]] = []
    for lon_deg, lat_deg in zip(lons, lats):
        lat = math.radians(lat_deg)
        lon = math.radians(lon_deg)
        sin_lat = math.sin(lat)
        cos_lat = math.cos(lat)
        tan_lat = math.tan(lat)

        n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        t = tan_lat * tan_lat
        c = ep2 * cos_lat * cos_lat
        aa = cos_lat * (lon - lon0)

        m = a * (
            (1.0 - e2 / 4.0 - 3.0 * e2 * e2 / 64.0 - 5.0 * e2 * e2 * e2 / 256.0) * lat
            - (3.0 * e2 / 8.0 + 3.0 * e2 * e2 / 32.0 + 45.0 * e2 * e2 * e2 / 1024.0) * math.sin(2.0 * lat)
            + (15.0 * e2 * e2 / 256.0 + 45.0 * e2 * e2 * e2 / 1024.0) * math.sin(4.0 * lat)
            - (35.0 * e2 * e2 * e2 / 3072.0) * math.sin(6.0 * lat)
        )

        x = false_easting + scale_factor * n * (
            aa
            + (1.0 - t + c) * (aa ** 3) / 6.0
            + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * ep2) * (aa ** 5) / 120.0
        )
        y = false_northing + scale_factor * (
            m
            + n
            * tan_lat
            * (
                (aa ** 2) / 2.0
                + (5.0 - t + 9.0 * c + 4.0 * c * c) * (aa ** 4) / 24.0
                + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * ep2) * (aa ** 6) / 720.0
            )
        )
        results.append((x, y))
    return results


def _parse_tm_params_from_wkt(proj_wkt: str) -> tuple[float, float, float, float] | None:
    """Extract (central_meridian, false_easting, false_northing, scale_factor) from WKT if projected."""
    if not proj_wkt or "PROJCS" not in proj_wkt.upper():
        return None
    cm_match = re.search(r'PARAMETER\["central_meridian"\s*,\s*([-+]?\d+(?:\.\d+)?)\]', proj_wkt, re.IGNORECASE)
    if not cm_match:
        return None
    cm = float(cm_match.group(1))
    fe_match = re.search(r'PARAMETER\["false_easting"\s*,\s*([-+]?\d+(?:\.\d+)?)\]', proj_wkt, re.IGNORECASE)
    fn_match = re.search(r'PARAMETER\["false_northing"\s*,\s*([-+]?\d+(?:\.\d+)?)\]', proj_wkt, re.IGNORECASE)
    sf_match = re.search(r'PARAMETER\["scale_factor"\s*,\s*([-+]?\d+(?:\.\d+)?)\]', proj_wkt, re.IGNORECASE)
    fe = float(fe_match.group(1)) if fe_match else 500000.0
    fn = float(fn_match.group(1)) if fn_match else 0.0
    sf = float(sf_match.group(1)) if sf_match else 0.9996
    return cm, fe, fn, sf


def project_lonlats_to_visum_xy(
    lonlats: dict[str, tuple[float, float]],
    proj_wkt: str = "",
) -> dict[str, tuple[float, float]]:
    """Project WGS84 (lon, lat) coordinates into the Visum network CRS."""
    if not lonlats:
        return {}

    ids = list(lonlats.keys())
    lons = [lonlats[k][0] for k in ids]
    lats = [lonlats[k][1] for k in ids]

    if proj_wkt and "PROJCS" in proj_wkt.upper():
        try:
            from pyproj import Transformer

            transformer = Transformer.from_crs("EPSG:4326", proj_wkt, always_xy=True)
            xs, ys = transformer.transform(lons, lats)
            return {nid: (float(x), float(y)) for nid, x, y in zip(ids, xs, ys)}
        except Exception:
            pass

        tm_params = _parse_tm_params_from_wkt(proj_wkt)
        if tm_params is not None:
            cm, fe, fn, sf = tm_params
            coords = _wgs84_to_transverse_mercator(lons, lats, cm, fe, fn, sf)
            return {nid: xy for nid, xy in zip(ids, coords)}

    # Geographic / unknown WKT fallback: compute local UTM zone from median coordinates
    med_lon = float(np.median(lons))
    med_lat = float(np.median(lats))
    zone = int((med_lon + 180.0) / 6.0) + 1
    cm = (zone - 1) * 6.0 - 180.0 + 3.0
    fn = 0.0 if med_lat >= 0.0 else 10000000.0
    coords = _wgs84_to_transverse_mercator(lons, lats, cm, 500000.0, fn, 0.9996)
    return {nid: xy for nid, xy in zip(ids, coords)}


def classify_ferry_nodes(
    nodes_df: pd.DataFrame,
    links_df: pd.DataFrame,
    *,
    ferry_typenos: Iterable[int] = (94,),
    ferry_access_typenos: Iterable[int] = (95,),
    osm_terminal_ids: Iterable[str] | None = None,
    osm_terminal_xy: dict[str, tuple[float, float]] | None = None,
    mid_only_osm_ids: Iterable[str] | None = None,
    snap_distance_m: float = 200.0,
) -> pd.DataFrame:
    """Classify nodes into ferry nodes, terminals, isolated ferry origins, and road targets.

    Returns a copy of `nodes_df` enriched with boolean/int columns:
        `Ferry_Node`, `Ferry_Degree`, `OSM_FerryTerminal`, `Isolated_Ferry`, `Road_Target_Node`.
    """
    result = nodes_df.copy()
    if result.empty:
        result["Ferry_Node"] = pd.Series(dtype=bool)
        result["Ferry_Degree"] = pd.Series(dtype=int)
        result["OSM_FerryTerminal"] = pd.Series(dtype=bool)
        result["Isolated_Ferry"] = pd.Series(dtype=bool)
        result["Road_Target_Node"] = pd.Series(dtype=bool)
        return result

    result["NO"] = result["NO"].astype(int)
    if "OSM_NODE_ID" in result.columns:
        result["_OSM_ID_NORM"] = result["OSM_NODE_ID"].map(_normalize_osm_id)
    else:
        result["_OSM_ID_NORM"] = ""

    existing_terminal = (
        result["OSM_FerryTerminal"].fillna(False).astype(bool)
        if "OSM_FerryTerminal" in result.columns
        else pd.Series(False, index=result.index)
    )

    ferry_type_set = {int(t) for t in ferry_typenos}
    access_type_set = {int(t) for t in ferry_access_typenos}
    excluded_road_types = ferry_type_set | access_type_set

    ferry_neighbors: dict[int, set[int]] = {}
    road_in_nodes: set[int] = set()
    road_out_nodes: set[int] = set()
    parent: dict[int, int] = {}

    def _uf_find(x: int) -> int:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def _uf_union(a: int, b: int) -> None:
        ra, rb = _uf_find(a), _uf_find(b)
        if ra != rb:
            parent[rb] = ra

    if not links_df.empty:
        typenos = links_df["TYPENO"].astype(int)
        from_nos = links_df["FROMNODENO"].astype(int)
        to_nos = links_df["TONODENO"].astype(int)

        ferry_mask = typenos.isin(ferry_type_set)
        for u, v in zip(from_nos[ferry_mask], to_nos[ferry_mask]):
            if u != v:
                ferry_neighbors.setdefault(int(u), set()).add(int(v))
                ferry_neighbors.setdefault(int(v), set()).add(int(u))

        road_mask = (~typenos.isin(excluded_road_types)) & links_df["TSYSSET"].map(_has_car)
        road_from = from_nos[road_mask]
        road_to = to_nos[road_mask]
        road_in_nodes = set(int(x) for x in road_to)
        road_out_nodes = set(int(x) for x in road_from)
        for u, v in zip(road_from, road_to):
            iu, iv = int(u), int(v)
            parent.setdefault(iu, iu)
            parent.setdefault(iv, iv)
            _uf_union(iu, iv)

    comp_counts: dict[int, int] = {}
    node_comp_id: dict[int, int] = {}
    for n in list(parent):
        root_id = _uf_find(n)
        node_comp_id[n] = root_id
        comp_counts[root_id] = comp_counts.get(root_id, 0) + 1
    max_comp_size = max(comp_counts.values(), default=0)
    min_target_comp_size = min(50, max_comp_size) if max_comp_size > 0 else 0

    road_comp_id_series = result["NO"].map(lambda n: node_comp_id.get(int(n), -1)).astype(int)
    road_comp_size_series = result["NO"].map(
        lambda n: comp_counts.get(node_comp_id.get(int(n), -1), 0)
    ).astype(int)

    ferry_degree = result["NO"].map(lambda n: len(ferry_neighbors.get(int(n), ()))).astype(int)
    ferry_node = ferry_degree > 0
    has_road_io = result["NO"].isin(road_in_nodes & road_out_nodes)

    osm_term_set = {_normalize_osm_id(x) for x in (osm_terminal_ids or ()) if _normalize_osm_id(x)}
    direct_terminal = existing_terminal | result["_OSM_ID_NORM"].isin(osm_term_set)

    # 1:1 nearest-Ferry_Node snap (<= snap_distance_m) for offset or road-side terminal nodes
    snapped_terminal = direct_terminal.copy()
    if snap_distance_m > 0 and bool(ferry_node.any()):
        ferry_subset = result.loc[ferry_node, ["NO", "XCOORD", "YCOORD"]].reset_index()
        ferry_tree = cKDTree(ferry_subset[["XCOORD", "YCOORD"]].to_numpy(dtype=float))

        # 1) Unmatched terminal points from Visum nodes not directly on a ferry link
        unmatched_coords: list[tuple[float, float]] = []
        off_ferry_term_mask = direct_terminal & (~ferry_node)
        if bool(off_ferry_term_mask.any()):
            for x, y in result.loc[off_ferry_term_mask, ["XCOORD", "YCOORD"]].to_numpy(dtype=float):
                if math.isfinite(x) and math.isfinite(y):
                    unmatched_coords.append((float(x), float(y)))

        # 2) Unmatched terminal points from raw OSM terminal coordinates
        if osm_terminal_xy:
            direct_ferry_osm_ids = set(result.loc[ferry_node & direct_terminal, "_OSM_ID_NORM"])
            for oid, (tx, ty) in osm_terminal_xy.items():
                if _normalize_osm_id(oid) not in direct_ferry_osm_ids and math.isfinite(tx) and math.isfinite(ty):
                    unmatched_coords.append((float(tx), float(ty)))

        if unmatched_coords:
            dists, idxs = ferry_tree.query(
                np.asarray(unmatched_coords, dtype=float),
                distance_upper_bound=float(snap_distance_m),
            )
            num_ferry = len(ferry_subset)
            for dist, idx in zip(dists, idxs):
                if math.isfinite(dist) and dist <= snap_distance_m and idx < num_ferry:
                    orig_df_idx = ferry_subset.loc[int(idx), "index"]
                    snapped_terminal.loc[orig_df_idx] = True

    mid_only_set = {_normalize_osm_id(x) for x in (mid_only_osm_ids or ()) if _normalize_osm_id(x)}
    is_mid_only = result["_OSM_ID_NORM"].isin(mid_only_set) if mid_only_set else pd.Series(False, index=result.index)

    is_terminal = ferry_node & (snapped_terminal | ((ferry_degree == 1) & (~is_mid_only)))
    road_target_node = has_road_io & (~ferry_node) & (road_comp_size_series >= min_target_comp_size)
    isolated_ferry = ferry_node & is_terminal & (~has_road_io)

    # Also treat terminals on tiny disconnected buffer road fragments (<50 nodes) as isolated
    # when a strictly larger road component exists nearby (<= 2000 m), e.g. Victorias port.
    small_frag_terminals = (
        ferry_node
        & is_terminal
        & has_road_io
        & (road_comp_size_series > 0)
        & (road_comp_size_series < min_target_comp_size)
    )
    if bool(small_frag_terminals.any()) and bool(road_target_node.any()):
        tgt_sub = result.loc[road_target_node, ["XCOORD", "YCOORD"]].to_numpy(dtype=float)
        tgt_comp_ids = road_comp_id_series.loc[road_target_node].to_numpy(dtype=int)
        tgt_comp_sizes = road_comp_size_series.loc[road_target_node].to_numpy(dtype=int)
        tgt_tree = cKDTree(tgt_sub)
        for df_idx in result.index[small_frag_terminals]:
            tx = float(result.at[df_idx, "XCOORD"])
            ty = float(result.at[df_idx, "YCOORD"])
            c_id = int(road_comp_id_series.at[df_idx])
            c_sz = int(road_comp_size_series.at[df_idx])
            if not (math.isfinite(tx) and math.isfinite(ty)):
                continue
            cand_idxs = tgt_tree.query_ball_point([tx, ty], r=2000.0)
            if any(
                tgt_comp_ids[i] != c_id and tgt_comp_sizes[i] > c_sz * 2
                for i in cand_idxs
            ):
                isolated_ferry.at[df_idx] = True

    result["Ferry_Node"] = ferry_node.astype(bool)
    result["Ferry_Degree"] = ferry_degree.astype(int)
    result["OSM_FerryTerminal"] = snapped_terminal.astype(bool)
    result["Isolated_Ferry"] = isolated_ferry.astype(bool)
    result["Road_Target_Node"] = road_target_node.astype(bool)
    result["Road_Comp_ID"] = road_comp_id_series
    result["Road_Comp_Size"] = road_comp_size_series
    return result.drop(columns=["_OSM_ID_NORM"])


def _extract_water_barriers_from_osm(
    osm_path: Path | None,
    proj_wkt: str = "",
) -> tuple[list[LineString], list[LineString]]:
    """Parse river/canal centerlines and coastlines from OSM XML into Visum XY LineStrings."""
    if osm_path is None or not osm_path.is_file() or osm_path.suffix.lower() != ".osm":
        return [], []
    raw_nodes: dict[str, tuple[float, float]] = {}
    waterway_refs: list[list[str]] = []
    coastline_refs: list[list[str]] = []
    waterway_values = {"river", "canal", "tidal_channel", "fairway"}
    for _ev, elem in ET.iterparse(str(osm_path), events=("end",)):
        if elem.tag == "node":
            nid = _normalize_osm_id(elem.attrib.get("id"))
            if nid and "lon" in elem.attrib and "lat" in elem.attrib:
                try:
                    raw_nodes[nid] = (float(elem.attrib["lon"]), float(elem.attrib["lat"]))
                except (TypeError, ValueError):
                    pass
            elem.clear()
        elif elem.tag == "way":
            tags = {t.attrib.get("k", ""): t.attrib.get("v", "").strip().lower() for t in elem.findall("tag")}
            is_ww = tags.get("waterway") in waterway_values
            is_coast = tags.get("natural") == "coastline"
            if is_ww or is_coast:
                nds = [_normalize_osm_id(nd.attrib.get("ref")) for nd in elem.findall("nd")]
                nds = [n for n in nds if n]
                if len(nds) >= 2:
                    if is_ww:
                        waterway_refs.append(nds)
                    if is_coast:
                        coastline_refs.append(nds)
            elem.clear()

    if not waterway_refs and not coastline_refs:
        return [], []
    used_ids = {
        nid
        for refs in (*waterway_refs, *coastline_refs)
        for nid in refs
        if nid in raw_nodes
    }
    proj_map = project_lonlats_to_visum_xy({nid: raw_nodes[nid] for nid in used_ids}, proj_wkt)
    ww_lines: list[LineString] = []
    for refs in waterway_refs:
        coords = [proj_map[n] for n in refs if n in proj_map]
        if len(coords) >= 2:
            ww_lines.append(LineString(coords))
    coast_lines: list[LineString] = []
    for refs in coastline_refs:
        coords = [proj_map[n] for n in refs if n in proj_map]
        if len(coords) >= 2:
            coast_lines.append(LineString(coords))
    return ww_lines, coast_lines


def _extract_waterway_lines_from_osm(
    osm_path: Path | None,
    proj_wkt: str = "",
) -> list[LineString]:
    """Parse river/canal centerlines from OSM XML into Visum XY LineStrings (optional barrier)."""
    ww_lines, _coast_lines = _extract_water_barriers_from_osm(osm_path, proj_wkt)
    return ww_lines


def _iter_intersection_points(geom) -> list[tuple[float, float]]:
    """Extract representative (x, y) crossing points from any Shapely intersection geometry."""
    if geom is None or geom.is_empty:
        return []
    gtype = geom.geom_type
    if gtype == "Point":
        return [(float(geom.x), float(geom.y))]
    if gtype == "MultiPoint":
        return [(float(p.x), float(p.y)) for p in geom.geoms]
    if gtype == "LineString":
        coords = list(geom.coords)
        return [(float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))] if coords else []
    if hasattr(geom, "geoms"):
        pts: list[tuple[float, float]] = []
        for sub in geom.geoms:
            pts.extend(_iter_intersection_points(sub))
        return pts
    return []


def _crosses_open_water_coastlines(
    seg: LineString,
    s_minx: float,
    s_miny: float,
    s_maxx: float,
    s_maxy: float,
    local_coastlines: list[tuple[LineString, float, float, float, float]],
    dedup_tol_m: float = 10.0,
) -> bool:
    """Return True if `seg` crosses coastlines at >= 2 distinct points (leaving one landmass and entering another)."""
    distinct_pts: list[tuple[float, float]] = []
    tol_sq = dedup_tol_m * dedup_tol_m
    for c_ls, c_minx, c_miny, c_maxx, c_maxy in local_coastlines:
        if c_maxx < s_minx or c_minx > s_maxx or c_maxy < s_miny or c_miny > s_maxy:
            continue
        inter = seg.intersection(c_ls)
        if inter.is_empty:
            continue
        for px, py in _iter_intersection_points(inter):
            if not any((px - qx) ** 2 + (py - qy) ** 2 <= tol_sq for qx, qy in distinct_pts):
                distinct_pts.append((px, py))
                if len(distinct_pts) >= 2:
                    return True
    return False


def select_same_bank_target_nodes(
    classified_df: pd.DataFrame,
    links_df: pd.DataFrame,
    *,
    ferry_typenos: Iterable[int] = (94,),
    ferry_wkt_rows: list[tuple[int, int, str]] | None = None,
    waterway_lines: list[LineString] | None = None,
    coastline_lines: list[LineString] | None = None,
    max_distance_m: float = 5000.0,
    max_water_cone_cos: float = 0.20,
) -> dict[int, int]:
    """Select one same-bank `Road_Target_Node` for each `Isolated_Ferry` terminal.

    Prevents `Ferry_Access` links from cutting across a river, harbor, or open sea
    (e.g. from a roadless small island back to the mainland) by combining:
    1) A geometric barrier check against non-incident ferry channel polylines
       and `waterway=river/canal` centerlines,
    2) A coastline crossing check (`natural=coastline`: <= 1 crossing allowed for
       pier-to-land, >= 2 crossings rejected as open-water jump), and
    3) A water-departure half-space / cone filter (`cos_water <= max_water_cone_cos`)
       derived from the outgoing `route=ferry` tangents at each terminal.

    Terminals with no barrier-free target on their own landmass are intentionally
    omitted so their ferry remains cleanly open-ended.
    """
    if classified_df.empty:
        return {}

    iso_df = classified_df.loc[classified_df["Isolated_Ferry"].astype(bool), ["NO", "XCOORD", "YCOORD"]]
    tgt_df = classified_df.loc[classified_df["Road_Target_Node"].astype(bool), ["NO", "XCOORD", "YCOORD"]]
    if iso_df.empty or tgt_df.empty:
        return {}

    ferry_type_set = {int(t) for t in ferry_typenos}
    node_xy: dict[int, tuple[float, float]] = {
        int(row.NO): (float(row.XCOORD), float(row.YCOORD))
        for row in classified_df[["NO", "XCOORD", "YCOORD"]].itertuples(index=False)
        if math.isfinite(float(row.XCOORD)) and math.isfinite(float(row.YCOORD))
    }

    # Build pre-computed barrier geometries + bounding boxes once before looping over terminals
    ferry_segments: list[tuple[int, int, list[tuple[float, float]]]] = []
    if ferry_wkt_rows:
        for fn, tn, wkt_str in ferry_wkt_rows:
            try:
                geom = shapely_wkt.loads(wkt_str)
                coords = list(geom.coords)
                if len(coords) >= 2:
                    ferry_segments.append((int(fn), int(tn), [(float(x), float(y)) for x, y in coords]))
            except Exception:
                continue
    elif not links_df.empty:
        ferry_mask = links_df["TYPENO"].astype(int).isin(ferry_type_set)
        for row in links_df.loc[ferry_mask, ["FROMNODENO", "TONODENO"]].itertuples(index=False):
            u, v = int(row.FROMNODENO), int(row.TONODENO)
            if u != v and u in node_xy and v in node_xy:
                ferry_segments.append((u, v, [node_xy[u], node_xy[v]]))

    water_vectors: dict[int, list[tuple[float, float]]] = {}
    pre_ferry_barriers: list[
        tuple[
            int,
            int,
            tuple[LineString, float, float, float, float],
            tuple[LineString, float, float, float, float] | None,
            tuple[LineString, float, float, float, float] | None,
        ]
    ] = []
    for u, v, coords in ferry_segments:
        dux, duy = coords[1][0] - coords[0][0], coords[1][1] - coords[0][1]
        lu = math.hypot(dux, duy)
        if lu > 1e-6:
            water_vectors.setdefault(u, []).append((dux / lu, duy / lu))
        dvx, dvy = coords[-2][0] - coords[-1][0], coords[-2][1] - coords[-1][1]
        lv = math.hypot(dvx, dvy)
        if lv > 1e-6:
            water_vectors.setdefault(v, []).append((dvx / lv, dvy / lv))

        full_ls = LineString(coords)
        tail_u = None
        tail_v = None
        if len(coords) >= 3:
            lu_tail = LineString(coords[1:])
            tail_u = (lu_tail, *lu_tail.bounds)
            lv_tail = LineString(coords[:-1])
            tail_v = (lv_tail, *lv_tail.bounds)
        pre_ferry_barriers.append((u, v, (full_ls, *full_ls.bounds), tail_u, tail_v))

    pre_waterway_barriers: list[tuple[LineString, float, float, float, float]] = [
        (w_ls, *w_ls.bounds) for w_ls in (waterway_lines or ())
    ]
    pre_coastline_barriers: list[tuple[LineString, float, float, float, float]] = [
        (c_ls, *c_ls.bounds) for c_ls in (coastline_lines or ())
    ]

    tgt_nos = tgt_df["NO"].astype(int).to_numpy()
    tgt_coords = tgt_df[["XCOORD", "YCOORD"]].to_numpy(dtype=float)
    tgt_comp_ids = tgt_df["Road_Comp_ID"].astype(int).to_numpy() if "Road_Comp_ID" in tgt_df.columns else None
    tgt_tree = cKDTree(tgt_coords)
    bound_pad = float(max_distance_m)

    assignments: dict[int, int] = {}
    k_query = min(len(tgt_nos), 64)

    for iso_row in iso_df.itertuples(index=False):
        t_no = int(iso_row.NO)
        tx, ty = float(iso_row.XCOORD), float(iso_row.YCOORD)
        if not (math.isfinite(tx) and math.isfinite(ty)):
            continue

        # Average water departure unit vector at terminal t_no
        vecs = water_vectors.get(t_no, [])
        ux = sum(v[0] for v in vecs)
        uy = sum(v[1] for v in vecs)
        u_len = math.hypot(ux, uy)
        has_water_dir = u_len > 1e-6
        if has_water_dir:
            ux /= u_len
            uy /= u_len

        # Collect only local barriers whose bounding box intersects [tx +/- max_distance_m]
        t_minx, t_miny, t_maxx, t_maxy = tx - bound_pad, ty - bound_pad, tx + bound_pad, ty + bound_pad
        local_barriers: list[tuple[LineString, float, float, float, float]] = []
        for u, v, full_b, tail_u_b, tail_v_b in pre_ferry_barriers:
            if u != t_no and v != t_no:
                b = full_b
            elif u == t_no:
                b = tail_u_b
            else:
                b = tail_v_b
            if b is not None and b[3] >= t_minx and b[1] <= t_maxx and b[4] >= t_miny and b[2] <= t_maxy:
                local_barriers.append(b)
        for wb in pre_waterway_barriers:
            if wb[3] >= t_minx and wb[1] <= t_maxx and wb[4] >= t_miny and wb[2] <= t_maxy:
                local_barriers.append(wb)
        local_coastlines: list[tuple[LineString, float, float, float, float]] = [
            cb for cb in pre_coastline_barriers
            if cb[3] >= t_minx and cb[1] <= t_maxx and cb[4] >= t_miny and cb[2] <= t_maxy
        ]

        dists, idxs = tgt_tree.query([tx, ty], k=k_query, distance_upper_bound=bound_pad)
        if np.ndim(dists) == 0:
            dists, idxs = np.asarray([dists], dtype=float), np.asarray([idxs], dtype=int)

        best_both = None
        best_clear = None

        iso_comp_id = int(getattr(iso_row, "Road_Comp_ID", -1))
        for dist, idx in zip(dists, idxs):
            if not math.isfinite(dist) or dist <= 0 or idx >= len(tgt_nos):
                continue
            r_no = int(tgt_nos[int(idx)])
            if tgt_comp_ids is not None and iso_comp_id >= 0 and int(tgt_comp_ids[int(idx)]) == iso_comp_id:
                continue
            rx, ry = float(tgt_coords[int(idx), 0]), float(tgt_coords[int(idx), 1])

            s_minx = tx if tx < rx else rx
            s_maxx = rx if tx < rx else tx
            s_miny = ty if ty < ry else ry
            s_maxy = ry if ty < ry else ty
            seg = LineString([(tx, ty), (rx, ry)])
            crosses = False
            for b_ls, b_minx, b_miny, b_maxx, b_maxy in local_barriers:
                if b_maxx >= s_minx and b_minx <= s_maxx and b_maxy >= s_miny and b_miny <= s_maxy:
                    if seg.intersects(b_ls):
                        crosses = True
                        break
            if not crosses and local_coastlines:
                if _crosses_open_water_coastlines(seg, s_minx, s_miny, s_maxx, s_maxy, local_coastlines):
                    crosses = True
            if not crosses and best_clear is None:
                best_clear = r_no

            if has_water_dir:
                cos_w = ((rx - tx) * ux + (ry - ty) * uy) / dist
                landward = cos_w <= float(max_water_cone_cos)
            else:
                landward = True

            if not crosses and landward:
                best_both = r_no
                break

        chosen = best_both if best_both is not None else best_clear
        if chosen is not None:
            assignments[t_no] = int(chosen)

    return assignments


def _ensure_node_uda(nodes_obj, uda_name: str, value_type: int, def_val, comment: str) -> None:
    """Ensure a node UDA exists without overwriting existing values."""
    if nodes_obj.AttrExists(uda_name):
        return
    nodes_obj.AddUserDefinedAttribute(uda_name, uda_name, uda_name, value_type, DefVal=def_val)
    nodes_obj.SetAllAttValues(uda_name, def_val, OnlyActive=False)
    try:
        uda = nodes_obj.Attributes.ItemByKey(uda_name)
        uda.Comment = comment
        if value_type != 5:
            uda.ValueDefault = def_val
        else:
            uda.StringValueDefault = def_val
    except Exception:
        pass


def _ensure_ferry_access_linktype(visum, linktype_no: int = 95):
    """Ensure LinkType 95 ('Ferry_Access') exists with 20 km/h for all modes (incl. Walk)."""
    try:
        lt = visum.Net.LinkTypes.ItemByKey(linktype_no)
    except Exception:
        lt = visum.Net.AddLinkType(linktype_no)
        lt.SetAttValue("NAME", "Ferry_Access")
        lt.SetAttValue("NUMLANES", 1)
        lt.SetAttValue("CAPPRT", 1000)

        existing_tsys = [
            (str(code).strip(), str(ttype).strip().upper())
            for code, ttype in visum.Net.TSystems.GetMultipleAttributes(["CODE", "TYPE"])
        ]
        preferred = {"BIKE", "BUS", "CAR", "HGV", "PUTW", "WALK"}
        selected = [code for code, _ in existing_tsys if code.upper() in preferred]
        if not selected:
            selected = [code for code, ttype in existing_tsys if ttype in {"PRT", "PUTWALK"}]
        if selected:
            lt.SetAttValue("TSYSSET", ",".join(selected))

    # Treat Ferry_Access identically to Ferry: 20 km/h for all modes (including Walk / PuTW)
    for attr_name, val in (
        ("V0PRT", 20),
        ("VMINPRT", 1),
        ("VMAX_PRTSYS(WALK)", 20),
        ("VDEF_PUTSYS(PUTW)", 20),
    ):
        try:
            lt.SetAttValue(attr_name, val)
        except Exception:
            pass
    return lt


def _resolve_osm_file(osm_file: str | Path | None, working_dir: str | Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if osm_file:
        p = Path(osm_file)
        candidates.append(p)
        if p.suffix.lower() != ".osm":
            candidates.append(p.with_suffix(".osm"))
            if p.name.endswith(".osm.pbf"):
                candidates.append(p.with_name(p.name[:-4]))
    if working_dir:
        wdir = Path(working_dir)
        osm_base = wdir.parent / "osm"
        candidates.extend([
            osm_base / "04_network" / "road_network_hierarchical_modified.osm",
            osm_base / "road_network_hierarchical_modified.osm",
        ])
    for cand in candidates:
        if cand.exists() and cand.is_file() and cand.suffix.lower() == ".osm":
            return cand
    return None


def _resolve_waterway_osm_files(
    resolved_osm: Path | None,
    working_dir: str | Path | None = None,
) -> list[Path]:
    """Locate separate waterway sidecar (`ferry_waterways.osm`) plus fallback OSM."""
    candidates: list[Path] = []
    if resolved_osm is not None:
        candidates.append(resolved_osm.parent / "ferry_waterways.osm")
        candidates.append(resolved_osm)
    if working_dir:
        wdir = Path(working_dir)
        osm_base = wdir.parent / "osm"
        candidates.extend([
            osm_base / "04_network" / "ferry_waterways.osm",
            osm_base / "ferry_waterways.osm",
        ])
    seen: set[Path] = set()
    found: list[Path] = []
    for cand in candidates:
        resolved = cand.resolve() if cand.exists() else cand
        if cand.exists() and cand.is_file() and cand.suffix.lower() == ".osm" and resolved not in seen:
            seen.add(resolved)
            found.append(cand)
    return found


def connect_ferries_to_road_network(
    visum,
    *,
    osm_file: str | Path | None = None,
    working_dir: str | Path | None = None,
    max_distance_m: float = 5000.0,
    snap_distance_m: float = 200.0,
    ferry_access_type_no: int = 95,
    log=logging.info,
) -> dict[str, int]:
    """Qualify ferry nodes, connect isolated ferry terminals via Ferry_Access, and open turns."""
    nodes_obj = visum.Net.Nodes
    links_obj = visum.Net.Links

    _ensure_node_uda(nodes_obj, "Ferry_Node", 9, False, "1 if node is connected to a ferry link")
    _ensure_node_uda(nodes_obj, "Ferry_Degree", 1, 0, "Number of unique neighbor nodes connected via ferry links")
    _ensure_node_uda(nodes_obj, "OSM_FerryTerminal", 9, False, "1 if tagged as ferry terminal in OSM")
    _ensure_node_uda(nodes_obj, "Isolated_Ferry", 9, False, "1 if ferry terminal without CAR+WALK road access")
    _ensure_node_uda(nodes_obj, "Ferry_Origin", 9, False, "Compatibility alias for Isolated_Ferry")

    # Detect Ferry and Ferry_Access link type numbers dynamically (supports pre- and post-restructure nets)
    lt_rows = visum.Net.LinkTypes.GetMultipleAttributes(["NO", "NAME"])
    ferry_typenos = {94}
    ferry_access_typenos = {ferry_access_type_no}
    for lt_no, lt_name in lt_rows:
        name_l = str(lt_name or "").strip().lower()
        if name_l == "ferry":
            ferry_typenos.add(int(lt_no))
        elif name_l == "ferry_access":
            ferry_access_typenos.add(int(lt_no))

    node_attrs = ["NO", "XCOORD", "YCOORD"]
    has_osm_id = nodes_obj.AttrExists("OSM_NODE_ID")
    if has_osm_id:
        node_attrs.append("OSM_NODE_ID")
    node_attrs.append("OSM_FerryTerminal")

    raw_nodes = nodes_obj.GetMultipleAttributes(node_attrs, OnlyActive=False)
    visum_id_rows = nodes_obj.GetMultiAttValues("NO", OnlyActive=False)
    if not raw_nodes or not visum_id_rows:
        return {"ferry_nodes": 0, "osm_terminals": 0, "isolated_ferries": 0, "created_links": 0, "opened_turns": 0}

    nodes_df = pd.DataFrame(raw_nodes, columns=node_attrs)
    id_df = pd.DataFrame(visum_id_rows, columns=["VISUM_ID", "NO"]).astype({"VISUM_ID": int, "NO": int})
    nodes_df["NO"] = nodes_df["NO"].astype(int)
    nodes_df = pd.merge(id_df, nodes_df, on="NO", how="inner")

    raw_links = links_obj.GetMultipleAttributes(["FROMNODENO", "TONODENO", "TYPENO", "TSYSSET"], OnlyActive=False)
    links_df = (
        pd.DataFrame(raw_links, columns=["FROMNODENO", "TONODENO", "TYPENO", "TSYSSET"])
        if raw_links
        else pd.DataFrame(columns=["FROMNODENO", "TONODENO", "TYPENO", "TSYSSET"])
    )

    resolved_osm = _resolve_osm_file(osm_file, working_dir)
    osm_terminal_ids: set[str] = set()
    osm_terminal_xy: dict[str, tuple[float, float]] = {}
    mid_only_osm_ids: set[str] = set()
    if resolved_osm is not None:
        osm_terminal_ids, terminal_lonlats, mid_only_osm_ids = extract_osm_ferry_terminal_info(resolved_osm)
        try:
            proj_wkt = str(visum.Net.AttValue("PROJECTIONDEFINITION") or "")
        except Exception:
            proj_wkt = ""
        osm_terminal_xy = project_lonlats_to_visum_xy(terminal_lonlats, proj_wkt)
        # If the Visum network itself is still in geographic degrees, project node coords to local UTM for snapping
        if "PROJCS" not in proj_wkt.upper() and not nodes_df.empty:
            max_abs_x = float(nodes_df["XCOORD"].abs().max())
            max_abs_y = float(nodes_df["YCOORD"].abs().max())
            if max_abs_x <= 180.0 and max_abs_y <= 90.0:
                med_lon = float(nodes_df["XCOORD"].median())
                med_lat = float(nodes_df["YCOORD"].median())
                zone = int((med_lon + 180.0) / 6.0) + 1
                cm = (zone - 1) * 6.0 - 180.0 + 3.0
                fn = 0.0 if med_lat >= 0.0 else 10000000.0
                proj_nodes = _wgs84_to_transverse_mercator(
                    nodes_df["XCOORD"].astype(float),
                    nodes_df["YCOORD"].astype(float),
                    cm,
                    500000.0,
                    fn,
                    0.9996,
                )
                nodes_df = nodes_df.copy()
                nodes_df["XCOORD"] = [xy[0] for xy in proj_nodes]
                nodes_df["YCOORD"] = [xy[1] for xy in proj_nodes]

    classified = classify_ferry_nodes(
        nodes_df,
        links_df,
        ferry_typenos=ferry_typenos,
        ferry_access_typenos=ferry_access_typenos,
        osm_terminal_ids=osm_terminal_ids,
        osm_terminal_xy=osm_terminal_xy,
        mid_only_osm_ids=mid_only_osm_ids,
        snap_distance_m=snap_distance_m,
    )

    nodes_obj.SetMultiAttValues("Ferry_Node", classified[["VISUM_ID", "Ferry_Node"]].astype(int).to_numpy())
    nodes_obj.SetMultiAttValues("Ferry_Degree", classified[["VISUM_ID", "Ferry_Degree"]].astype(int).to_numpy())
    nodes_obj.SetMultiAttValues("OSM_FerryTerminal", classified[["VISUM_ID", "OSM_FerryTerminal"]].astype(int).to_numpy())
    nodes_obj.SetMultiAttValues("Isolated_Ferry", classified[["VISUM_ID", "Isolated_Ferry"]].astype(int).to_numpy())
    nodes_obj.SetMultiAttValues("Ferry_Origin", classified[["VISUM_ID", "Isolated_Ferry"]].astype(int).to_numpy())

    ferry_count = int(classified["Ferry_Node"].sum())
    terminal_count = int((classified["Ferry_Node"] & classified["OSM_FerryTerminal"]).sum())
    isolated_count = int(classified["Isolated_Ferry"].sum())
    target_count = int(classified["Road_Target_Node"].sum())

    log(
        f"Fährknoten klassifiziert: {ferry_count} Fährknoten, "
        f"{terminal_count} OSM_FerryTerminal, {isolated_count} isolierte Fähranleger (Isolated_Ferry=1)."
    )

    existing_access_links = int(links_df["TYPENO"].astype(int).isin(ferry_access_typenos).sum()) if not links_df.empty else 0
    created_links = 0
    opened_turns = 0

    if existing_access_links > 0:
        log(f"{existing_access_links} 'Ferry_Access'-Strecken bereits vorhanden – überspringe erneute Generierung.")
        return {
            "ferry_nodes": ferry_count,
            "osm_terminals": terminal_count,
            "isolated_ferries": isolated_count,
            "created_links": 0,
            "opened_turns": 0,
        }

    if isolated_count == 0 or target_count == 0:
        return {
            "ferry_nodes": ferry_count,
            "osm_terminals": terminal_count,
            "isolated_ferries": isolated_count,
            "created_links": 0,
            "opened_turns": 0,
        }

    # Same-bank refinement: prefer landward road targets on the terminal's side of the water
    nf = visum.Filters.NodeFilter()
    lf = visum.Filters.LinkFilter()
    tf = visum.Filters.TurnFilter()

    ferry_wkt_rows: list[tuple[int, int, str]] | None = None
    try:
        if links_obj.AttrExists("WKTPOLY") is True:
            visum.Filters.InitAll()
            lf.Init()
            lf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", ",".join(map(str, sorted(ferry_typenos))))
            raw_wkt = links_obj.GetMultipleAttributes(["FROMNODENO", "TONODENO", "WKTPOLY"], OnlyActive=True)
            lf.Init()
            if isinstance(raw_wkt, (list, tuple)) and raw_wkt:
                ferry_wkt_rows = [(int(fn), int(tn), str(w)) for fn, tn, w in raw_wkt if w]
    except Exception:
        ferry_wkt_rows = None

    waterway_lines: list[LineString] = []
    coastline_lines: list[LineString] = []
    waterway_files = _resolve_waterway_osm_files(resolved_osm, working_dir)
    if waterway_files:
        try:
            proj_wkt_cur = str(visum.Net.AttValue("PROJECTIONDEFINITION") or "")
        except Exception:
            proj_wkt_cur = ""
        for wf in waterway_files:
            ww, coast = _extract_water_barriers_from_osm(wf, proj_wkt_cur)
            waterway_lines.extend(ww)
            coastline_lines.extend(coast)

    same_bank_map = select_same_bank_target_nodes(
        classified,
        links_df,
        ferry_typenos=ferry_typenos,
        ferry_wkt_rows=ferry_wkt_rows,
        waterway_lines=waterway_lines,
        coastline_lines=coastline_lines,
        max_distance_m=max_distance_m,
    )
    skipped_open_end = max(0, isolated_count - len(same_bank_map))
    if skipped_open_end > 0:
        log(
            f"{skipped_open_end} isolierte Fähranleger ohne landseitiges Straßenziel "
            f"(z. B. straßenlose Insel) bleiben bewusst mit offenem Ende."
        )
    if same_bank_map:
        selected_targets = set(same_bank_map.values())
        classified = classified.copy()
        classified["Road_Target_Node"] = classified["Road_Target_Node"] & classified["NO"].isin(selected_targets)

    _ensure_node_uda(nodes_obj, "Road_Target_Node", 9, False, "Temporary target filter for ConnectNodes")
    nodes_obj.SetMultiAttValues("Road_Target_Node", classified[["VISUM_ID", "Road_Target_Node"]].astype(int).to_numpy())

    try:
        lt_access = _ensure_ferry_access_linktype(visum, ferry_access_type_no)

        paras = nodes_obj.CreateAutomaticNodeConnectionParas()
        paras.LinkType = lt_access
        paras.SetAttValue("MinNewLinksPerSourceNode", 0)
        paras.SetAttValue("MaxNewLinksPerSourceNode", 1)
        paras.SetAttValue("MaxDistanceEnabled", True)
        paras.SetAttValue("MaxDistance", f"{float(max_distance_m):g}m")

        # Partition isolated terminals into:
        # 1) batch_nos: Euclidean-nearest node among selected_targets IS their same-bank target -> single ConnectNodes call
        # 2) override_groups: rare terminals where another terminal's target in selected_targets is Euclidean-closer across water
        # 3) omitted (not in same_bank_map): roadless islands / barrier-blocked terminals left cleanly open-ended
        batch_nos: list[int] = []
        override_groups: dict[int, list[int]] = {}
        if same_bank_map:
            sel_df = classified.loc[classified["Road_Target_Node"].astype(bool), ["NO", "XCOORD", "YCOORD"]]
            sel_nos = sel_df["NO"].astype(int).to_numpy()
            if len(sel_nos) > 0:
                sel_tree = cKDTree(sel_df[["XCOORD", "YCOORD"]].to_numpy(dtype=float))
                iso_xy = {
                    int(r.NO): (float(r.XCOORD), float(r.YCOORD))
                    for r in classified.loc[
                        classified["Isolated_Ferry"].astype(bool), ["NO", "XCOORD", "YCOORD"]
                    ].itertuples(index=False)
                }
                for iso_no, tgt_no in same_bank_map.items():
                    xy = iso_xy.get(int(iso_no))
                    if xy is None:
                        continue
                    dist_n, idx_n = sel_tree.query(xy, distance_upper_bound=float(max_distance_m))
                    if math.isfinite(dist_n) and int(idx_n) < len(sel_nos) and int(sel_nos[int(idx_n)]) == int(tgt_no):
                        batch_nos.append(int(iso_no))
                    else:
                        override_groups.setdefault(int(tgt_no), []).append(int(iso_no))
            else:
                batch_nos = [int(k) for k in same_bank_map]

        batch_set = set(batch_nos)
        excluded_visum_rows = classified.loc[
            classified["Isolated_Ferry"].astype(bool) & (~classified["NO"].isin(batch_set)),
            "VISUM_ID",
        ].astype(int).tolist()

        # Pass 1: Batch-connect all non-conflicting isolated terminals in one ConnectNodes call
        if batch_nos:
            if excluded_visum_rows:
                nodes_obj.SetMultiAttValues("Isolated_Ferry", [(vid, 0) for vid in excluded_visum_rows])
            try:
                visum.Filters.InitAll()
                nf.Init()
                nf.AddCondition("OP_NONE", False, "Isolated_Ferry", "EqualVal", 1)
                source_nodes = visum.CreateNetElements()
                for node_obj in nodes_obj.GetAllActive:
                    source_nodes.Add(node_obj)

                nf.Init()
                nf.AddCondition("OP_NONE", False, "Road_Target_Node", "EqualVal", 1)
                if nodes_obj.CountActive > 0:
                    new_links = nodes_obj.ConnectNodes(source_nodes, paras)
                    try:
                        created_links += int(new_links.Count)
                    except Exception:
                        pass
            finally:
                if excluded_visum_rows:
                    nodes_obj.SetMultiAttValues("Isolated_Ferry", [(vid, 1) for vid in excluded_visum_rows])

        # Pass 2: Connect only the few across-water conflict terminals directly to their same-bank targets
        for tgt_no, src_nos in override_groups.items():
            visum.Filters.InitAll()
            source_nodes = visum.CreateNetElements()
            for s_no in sorted(set(src_nos)):
                try:
                    source_nodes.Add(nodes_obj.ItemByKey(int(s_no)))
                except Exception:
                    pass

            nf.Init()
            nf.AddCondition("OP_NONE", False, "Road_Target_Node", "EqualVal", 1)
            nf.AddCondition("OP_AND", False, "NO", "EqualVal", int(tgt_no))
            if nodes_obj.CountActive > 0:
                new_links = nodes_obj.ConnectNodes(source_nodes, paras)
                try:
                    created_links += int(new_links.Count)
                except Exception:
                    pass

        log(f"Fähr-Anbindungen ('Ferry_Access') über ConnectNodes generiert: {created_links} Streckenrichtungen.")

        # Open turns touching Ferry_Access links by intersecting allowed link modes (excluding PUTWALK)
        visum.Filters.InitAll()
        lf.Init()
        lf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", str(ferry_access_type_no))

        nf.Init()
        nf.AddCondition("OP_NONE", False, "COUNTACTIVE:INLINKS", "GreaterVal", 0)
        nf.AddCondition("OP_OR", False, "COUNTACTIVE:OUTLINKS", "GreaterVal", 0)

        tf.Init()
        tf.AddCondition("OP_NONE", False, "COUNTACTIVE:VIANODE", "GreaterVal", 0)

        if visum.Net.Turns.CountActive > 0:
            tsys_code_map = {
                str(code).strip().upper(): str(code).strip()
                for code, ttype in visum.Net.TSystems.GetMultipleAttributes(["CODE", "TYPE"])
                if str(ttype).strip().upper() != "PUTWALK"
            }
            turn_rows = visum.Net.Turns.GetMultipleAttributes(
                ["TSYSSET", r"FROMLINK\TSYSSET", r"TOLINK\TSYSSET", r"FROMLINK\TYPENO", r"TOLINK\TYPENO"],
                OnlyActive=True,
            )
            updated_vals = []
            for cur_tsys, fl_tsys, tl_tsys, fl_type, tl_type in turn_rows:
                if int(fl_type) == ferry_access_type_no or int(tl_type) == ferry_access_type_no:
                    common_tokens = _parse_tsys_tokens(fl_tsys) & _parse_tsys_tokens(tl_tsys)
                    inter = sorted(tsys_code_map[tok] for tok in common_tokens if tok in tsys_code_map)
                    updated_vals.append([",".join(inter)])
                    if inter:
                        opened_turns += 1
                else:
                    updated_vals.append([cur_tsys])
            visum.Net.Turns.SetMultipleAttributes(["TSYSSET"], updated_vals, OnlyActive=True)
            log(f"Abbieger für 'Ferry_Access'-Strecken freigegeben: {opened_turns} Abbieger geöffnet.")
    finally:
        visum.Filters.InitAll()
        try:
            if nodes_obj.AttrExists("Road_Target_Node"):
                nodes_obj.DeleteUserDefinedAttribute("Road_Target_Node")
        except Exception:
            pass

    return {
        "ferry_nodes": ferry_count,
        "osm_terminals": terminal_count,
        "isolated_ferries": isolated_count,
        "created_links": created_links,
        "opened_turns": opened_turns,
    }
