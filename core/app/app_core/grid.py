"""Core logic for step 3 grid generation and area selection."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

from core.app.app_core.geo import get_utm_epsg


AREA_MODES = ("PA", "IA1", "IA2")
SUBGRID_DIVISIONS = {"PA": 9, "IA1": 3, "IA2": 1}


@dataclass(frozen=True)
class ToggleResult:
    cell_id: int
    area_type: Optional[str]
    selected: bool
    selected_cells: dict[str, set[int]]


GridParams = tuple[int, int]


def create_selected_cells(area_modes: Iterable[str] = AREA_MODES) -> dict[str, set[int]]:
    return {mode: set() for mode in area_modes}


def restore_selected_cells(
    grid_map_data: list[dict],
    saved_selected_cells: Optional[dict[str, Iterable[int]]] = None,
) -> dict[str, set[int]]:
    """Restore persisted selections and synchronize each cell's display type."""
    selected_cells = create_selected_cells()
    valid_cell_ids = {cell.get("id") for cell in grid_map_data}
    for mode in AREA_MODES:
        selected_cells[mode] = {
            cell_id
            for cell_id in saved_selected_cells.get(mode, []) if cell_id in valid_cell_ids
        } if saved_selected_cells else set()

    selected_by_id = {
        cell_id: mode
        for mode in AREA_MODES
        for cell_id in selected_cells[mode]
    }
    for cell in grid_map_data:
        cell["area_type"] = selected_by_id.get(cell.get("id"))
    return selected_cells


def restore_grid_geometries(grid_map_data: list[dict]) -> list[dict]:
    """Rehydrate persisted WKT polygons, including metadata from older projects."""
    polygon_cls = None
    wkt_loads = None
    for cell in grid_map_data:
        if not isinstance(cell, dict):
            continue
        geometry = cell.get("shapely_poly_wgs84")
        if geometry is not None and not isinstance(geometry, str):
            continue

        restored = None
        if isinstance(geometry, str) and geometry.strip():
            if wkt_loads is None:
                try:
                    from shapely.wkt import loads as wkt_loads
                except ImportError:
                    wkt_loads = False
            if wkt_loads:
                try:
                    restored = wkt_loads(geometry)
                except Exception:
                    restored = None

        if restored is None:
            coordinates = cell.get("wgs84_coords_map") or []
            try:
                lon_lat = [(float(item[1]), float(item[0])) for item in coordinates]
            except (IndexError, TypeError, ValueError):
                lon_lat = []
            if len(lon_lat) >= 3:
                if polygon_cls is None:
                    try:
                        _point_cls, polygon_cls = _require_shapely_geometry()
                    except RuntimeError:
                        polygon_cls = False
                if polygon_cls:
                    restored = polygon_cls(lon_lat)

        if (
            restored is not None
            and getattr(restored, "geom_type", None) in ("Polygon", "MultiPolygon")
            and not restored.is_empty
        ):
            cell["shapely_poly_wgs84"] = restored
        else:
            cell["shapely_poly_wgs84"] = None
    return grid_map_data


def grid_params_are_dirty(current_params: Optional[GridParams], generated_params: Optional[GridParams]) -> bool:
    return generated_params is None or current_params != generated_params


def estimate_grid_cell_count(cell_size_m: int, radius_km: int | float) -> float:
    return math.pi * (float(radius_km) * 1000 / int(cell_size_m)) ** 2


def generate_grid_map_data(center_lat: float, center_lon: float, cell_size_m: int, radius_km: int | float) -> list[dict]:
    transformer = _require_pyproj_transformer()
    _point_cls, polygon_cls = _require_shapely_geometry()
    utm_crs = get_utm_epsg(center_lat, center_lon)
    if not utm_crs:
        raise ValueError(f"UTM CRS unavailable for lat={center_lat}, lon={center_lon}")

    to_utm = transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
    from_utm = transformer.from_crs(utm_crs, "EPSG:4326", always_xy=True)
    cs = int(cell_size_m)
    radius_m = float(radius_km) * 1000
    half_cell = cs / 2.0
    center_x, center_y = to_utm.transform(center_lon, center_lat)
    num_half_cells = math.ceil(radius_m / cs)

    x_coords = _meter_steps(center_x - num_half_cells * cs - half_cell, center_x + (num_half_cells + 1) * cs, cs)
    y_coords = _meter_steps(center_y - num_half_cells * cs - half_cell, center_y + (num_half_cells + 1) * cs, cs)

    grid_map_data = []
    cell_id = 0
    for i in range(len(x_coords) - 1):
        for j in range(len(y_coords) - 1):
            xs_utm, xe_utm = x_coords[i], x_coords[i + 1]
            ys_utm, ye_utm = y_coords[j], y_coords[j + 1]
            if math.hypot((xs_utm + half_cell) - center_x, (ys_utm + half_cell) - center_y) > radius_m:
                continue

            wgs_map_coords = []
            wgs_shapely_coords = []
            for xp, yp in ((xs_utm, ys_utm), (xe_utm, ys_utm), (xe_utm, ye_utm), (xs_utm, ye_utm)):
                lon_c, lat_c = from_utm.transform(xp, yp)
                wgs_map_coords.append((lat_c, lon_c))
                wgs_shapely_coords.append((lon_c, lat_c))

            grid_map_data.append(
                {
                    "id": cell_id,
                    "wgs84_coords_map": wgs_map_coords,
                    "shapely_poly_wgs84": polygon_cls(wgs_shapely_coords),
                    "area_type": None,
                    "utm_coords": (xs_utm, xe_utm, ys_utm, ye_utm),
                    "utm_crs": utm_crs,
                }
            )
            cell_id += 1
    return grid_map_data


def find_cell_at_lonlat(grid_map_data: Iterable[dict], lon: float, lat: float) -> Optional[dict]:
    point_cls, _polygon_cls = _require_shapely_geometry()
    click_point = point_cls(lon, lat)
    for cell in grid_map_data:
        polygon = cell.get("shapely_poly_wgs84")
        if polygon and (polygon.contains(click_point) or polygon.touches(click_point)):
            return cell
    return None


def toggle_cell(cell: dict, selected_cells: dict[str, set[int]], current_mode: str) -> ToggleResult:
    if current_mode not in selected_cells:
        raise ValueError(f"Unknown area mode: {current_mode}")

    cell_id = cell["id"]
    selected_in_current_mode = cell_id in selected_cells[current_mode]
    for cells_for_mode in selected_cells.values():
        cells_for_mode.discard(cell_id)

    new_area_type = None
    selected = False
    if not selected_in_current_mode:
        selected_cells[current_mode].add(cell_id)
        new_area_type = current_mode
        selected = True
    cell["area_type"] = new_area_type

    return ToggleResult(cell_id=cell_id, area_type=new_area_type, selected=selected, selected_cells=selected_cells)


def assign_cell(cell: dict, selected_cells: dict[str, set[int]], current_mode: str) -> ToggleResult:
    if current_mode not in selected_cells:
        raise ValueError(f"Unknown area mode: {current_mode}")

    cell_id = cell["id"]
    for cells_for_mode in selected_cells.values():
        cells_for_mode.discard(cell_id)

    selected_cells[current_mode].add(cell_id)
    cell["area_type"] = current_mode
    return ToggleResult(cell_id=cell_id, area_type=current_mode, selected=True, selected_cells=selected_cells)


def toggle_cell_at_lonlat(
    grid_map_data: Iterable[dict],
    selected_cells: dict[str, set[int]],
    current_mode: str,
    lon: float,
    lat: float,
) -> Optional[ToggleResult]:
    cell = find_cell_at_lonlat(grid_map_data, lon, lat)
    if cell is None:
        return None
    return toggle_cell(cell, selected_cells, current_mode)


def assign_cell_at_lonlat(
    grid_map_data: Iterable[dict],
    selected_cells: dict[str, set[int]],
    current_mode: str,
    lon: float,
    lat: float,
) -> Optional[ToggleResult]:
    cell = find_cell_at_lonlat(grid_map_data, lon, lat)
    if cell is None:
        return None
    return assign_cell(cell, selected_cells, current_mode)


def assign_cells_along_lonlat_path(
    grid_map_data: Iterable[dict],
    selected_cells: dict[str, set[int]],
    current_mode: str,
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
) -> list[ToggleResult]:
    line_string_cls = _require_shapely_linestring()
    drag_line = line_string_cls([(start_lon, start_lat), (end_lon, end_lat)])
    results = []
    assigned_ids: set[int] = set()
    for cell in grid_map_data:
        polygon = cell.get("shapely_poly_wgs84")
        cell_id = cell.get("id")
        if polygon and cell_id not in assigned_ids and polygon.intersects(drag_line):
            results.append(assign_cell(cell, selected_cells, current_mode))
            assigned_ids.add(cell_id)
    return results


def selected_cell_counts(selected_cells: dict[str, set[int]], area_modes: Iterable[str] = AREA_MODES) -> str:
    return ", ".join(f"{mode}:{len(selected_cells.get(mode, set()))}" for mode in area_modes)


def has_selected_cells(selected_cells: dict[str, set[int]]) -> bool:
    return any(selected_cells.values())


def selection_is_exclusive(selected_cells: dict[str, set[int]]) -> bool:
    seen: set[int] = set()
    for cells_for_mode in selected_cells.values():
        overlap = seen.intersection(cells_for_mode)
        if overlap:
            return False
        seen.update(cells_for_mode)
    return True


def subgrid_division_for_area(area_type: Optional[str]) -> int:
    return SUBGRID_DIVISIONS.get(area_type or "", 0)


def subgrid_map_coords_for_cell(cell: dict, division: int) -> list[list[tuple[float, float]]]:
    if division <= 0 or not cell.get("utm_coords") or not cell.get("utm_crs"):
        return []

    transformer = _require_pyproj_transformer()
    from_utm = transformer.from_crs(cell["utm_crs"], "EPSG:4326", always_xy=True)
    xs_utm, xe_utm, ys_utm, ye_utm = cell["utm_coords"]
    x_steps = _linear_steps(xs_utm, xe_utm, division)
    y_steps = _linear_steps(ys_utm, ye_utm, division)

    subgrid_polygons = []
    for i in range(division):
        for j in range(division):
            polygon_coords = []
            for xp, yp in (
                (x_steps[i], y_steps[j]),
                (x_steps[i + 1], y_steps[j]),
                (x_steps[i + 1], y_steps[j + 1]),
                (x_steps[i], y_steps[j + 1]),
            ):
                lon_c, lat_c = from_utm.transform(xp, yp)
                polygon_coords.append((lat_c, lon_c))
            subgrid_polygons.append(polygon_coords)
    return subgrid_polygons


def build_step3_payload(
    selected_loc,
    cell_size_m: int,
    radius_km: int,
    selected_cells: dict[str, set[int]],
    grid_map_data: list[dict],
) -> dict:
    return {
        "selected_loc": selected_loc,
        "cell_size_m": cell_size_m,
        "radius_km": radius_km,
        "selected_cells": selected_cells,
        "grid_map_data": grid_map_data,
    }


def _meter_steps(start: float, stop: float, step: int) -> list[float]:
    values = []
    current = start
    while current < stop:
        values.append(current)
        current += step
    return values


def _linear_steps(start: float, stop: float, divisions: int) -> list[float]:
    step = (stop - start) / divisions
    return [start + step * index for index in range(divisions + 1)]


def _require_pyproj_transformer():
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError("pyproj is required for grid coordinate transformations") from exc
    return Transformer


def _require_shapely_geometry():
    try:
        from shapely.geometry import Point, Polygon
    except ImportError as exc:
        raise RuntimeError("shapely is required for grid geometry operations") from exc
    return Point, Polygon


def _require_shapely_linestring():
    try:
        from shapely.geometry import LineString
    except ImportError as exc:
        raise RuntimeError("shapely is required for grid geometry operations") from exc
    return LineString
