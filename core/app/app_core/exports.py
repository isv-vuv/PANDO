"""Core export helpers for step 5.

The functions in this module avoid UI toolkit imports so they can be
unit-tested in a regular Python environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable

from core.app.app_core.grid import AREA_MODES


@dataclass(frozen=True)
class AreaExportResult:
    output_path: str
    exported_areas: list[str]
    feature_count: int


def export_area_geojson(
    grid_map_data: Iterable[dict],
    selected_cells: dict[str, set[int] | list[int] | tuple[int, ...]],
    output_path: str,
    area_modes: Iterable[str] = AREA_MODES,
) -> AreaExportResult:
    """Export selected PA/IA geometries as one combined GeoJSON file."""
    if not any(selected_cells.get(mode) for mode in area_modes):
        raise ValueError("No selected cells available for area export")

    unary_union = _require_shapely_unary_union()
    cell_polygons = _valid_cell_polygons_by_id(grid_map_data)
    if not cell_polygons:
        raise ValueError("Grid data does not contain valid shapely polygons")

    features = []
    for area_name in area_modes:
        polygons = [
            cell_polygons[cell_id]
            for cell_id in selected_cells.get(area_name, set())
            if cell_id in cell_polygons
        ]
        if not polygons:
            continue
        geometry = unary_union(polygons)
        geometry = _clean_geometry(geometry)
        if geometry.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"id": len(features) + 1, "Bereich": area_name},
                "geometry": geometry.__geo_interface__,
            }
        )

    if not features:
        raise ValueError("Selected cells did not produce exportable geometries")

    _write_geojson(output_path, features)
    return AreaExportResult(
        output_path=output_path,
        exported_areas=[feature["properties"]["Bereich"] for feature in features],
        feature_count=len(features),
    )


def export_center_point_geojson(selected_location, output_path: str, name: str = "CenterPoint") -> str:
    """Export the selected location as a single point GeoJSON feature."""
    lat = getattr(selected_location, "latitude", None)
    lon = getattr(selected_location, "longitude", None)
    if lat is None or lon is None:
        raise ValueError("Selected location must provide latitude and longitude")

    feature = {
        "type": "Feature",
        "properties": {"id": 1, "name": name},
        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
    }
    _write_geojson(output_path, [feature])
    return output_path


def default_area_export_filename(selected_location, extension: str = ".geojson") -> str:
    from core.app.app_core.location import get_alpha2_code, get_clean_filename_city

    return f"{get_alpha2_code(selected_location)}_{get_clean_filename_city(selected_location)}_PlanningAreas_Combined{extension}"


def default_center_point_filename(selected_location, extension: str = ".geojson") -> str:
    from core.app.app_core.location import get_alpha2_code, get_clean_filename_city

    return f"{get_alpha2_code(selected_location)}_{get_clean_filename_city(selected_location)}_CenterPoint{extension}"


def _valid_cell_polygons_by_id(grid_map_data: Iterable[dict]) -> dict[int, object]:
    polygons = {}
    for cell in grid_map_data:
        if "id" not in cell:
            continue
        polygon = cell.get("shapely_poly_wgs84")
        if polygon is None or not getattr(polygon, "is_valid", False):
            continue
        polygons[cell["id"]] = polygon
    return polygons


def _clean_geometry(geometry):
    if not getattr(geometry, "is_valid", False):
        geometry = geometry.buffer(0)
    if not getattr(geometry, "is_valid", False):
        geometry = geometry.buffer(1e-9).buffer(-1e-9)
    return geometry


def _write_geojson(output_path: str, features: list[dict]) -> None:
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    collection = {"type": "FeatureCollection", "features": features}
    with open(output_path, "w", encoding="utf-8") as geojson_file:
        json.dump(collection, geojson_file, ensure_ascii=False)


def _require_shapely_unary_union():
    try:
        from shapely.ops import unary_union
    except ImportError as exc:
        raise RuntimeError("shapely is required for area GeoJSON export") from exc
    return unary_union
