import os
import subprocess
import geopandas as gpd
import pandas as pd
import re
import math


def get_safe_string(val):
    if val is None: return ""
    if isinstance(val, float) and math.isnan(val): return ""
    return str(val).strip()


def parse_other_tags(hstore_str):
    if not isinstance(hstore_str, str): return {}
    return dict(re.findall(r'"(.*?)"=>"(.*?)"', hstore_str))


def save_gpkg_safely(gdf, filepath, layer_name):
    if os.path.exists(filepath):
        os.remove(filepath)
    gdf.to_file(filepath, driver="GPKG", layer=layer_name, layer_options={"SPATIAL_INDEX": "YES"})
    return filepath


def extract_polygonal_geometry(geom):
    """Ensure geometry is pure Polygon or MultiPolygon, extracting polygon components from GeometryCollections."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if geom.geom_type == "GeometryCollection":
        from shapely.geometry import Polygon, MultiPolygon
        polys = []
        for part in geom.geoms:
            if isinstance(part, Polygon):
                polys.append(part)
            elif isinstance(part, MultiPolygon):
                polys.extend(part.geoms)
        if not polys:
            return None
        if len(polys) == 1:
            return polys[0]
        return MultiPolygon(polys)
    return None


def extract_point_geometry(geom):
    """Ensure geometry is pure Point or MultiPoint, extracting point components from GeometryCollections."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Point", "MultiPoint"):
        return geom
    if geom.geom_type == "GeometryCollection":
        from shapely.geometry import Point, MultiPoint
        pts = []
        for part in geom.geoms:
            if isinstance(part, Point):
                pts.append(part)
            elif isinstance(part, MultiPoint):
                pts.extend(part.geoms)
        if not pts:
            return None
        if len(pts) == 1:
            return pts[0]
        return MultiPoint(pts)
    return None


def export_study_area(
    merged_pbf, osm_dir, osmium_exe, bin_dir, poly_ia2, *,
    output_points=None, output_polygons=None, run_command=None, log=print, progress=None,
    local_crs="EPSG:3857"
):
    from core.scripts.qgis.scripts.Model5_NationwideIntensity import prepare_model5_poi_datasets

    path_poly_ia2 = poly_ia2 if os.path.isabs(poly_ia2) else os.path.join(osm_dir, poly_ia2)
    if not os.path.exists(path_poly_ia2):
        log(f"Fehler: {poly_ia2} fehlt.")
        return False

    features_dir = os.path.dirname(output_points) if output_points else os.path.join(osm_dir, "03_features")
    os.makedirs(features_dir, exist_ok=True)

    try:
        log("Study Area: Landesweite POIs extrahieren, bereinigen und zuschneiden (Max-Weight) ...")
        prepare_model5_poi_datasets(
            merged_pbf=merged_pbf,
            poly_study_area=path_poly_ia2,
            features_dir=features_dir,
            local_crs=local_crs,
            osmium_exe=osmium_exe,
            log=log,
            progress=lambda p: progress("study_area_pois", p, 100) if progress else None,
        )
        return True
    except Exception as exc:
        log(f"Fehler bei POI-Aufbereitung: {exc}")
        return False

        if os.path.exists(abs_temp_pbf):
            try:
                os.remove(abs_temp_pbf)
            except Exception:
                pass
        return {"study_area_points": output_points, "study_area_polygons": output_polygons}

    except Exception as e:
        log(f"Fehler: {e}")
        return False
