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


def export_study_area(
    merged_pbf, osm_dir, osmium_exe, bin_dir, poly_ia2, *,
    output_points=None, output_polygons=None, run_command=None, log=print, progress=None
):
    progress = progress or (lambda _name, _index, _total: None)
    total_steps = 3
    path_poly_ia2 = poly_ia2 if os.path.isabs(poly_ia2) else os.path.join(osm_dir, poly_ia2)
    if not os.path.exists(path_poly_ia2):
        log(f"Fehler: {poly_ia2} fehlt.")
        return False

    desired_tags = [
        "amenity", "building", "isced:level", "name", "name:en", "name:de",
        "name:es", "name:fr", "name:pt", "name:ru", "name:ar", "name:zh",
        "name:ja", "name:hi", "alt_name", "official_name", "ref", "shop",
        "leisure", "tourism", "office", "healthcare", "healthcare:speciality",
        "sport", "railway", "waterway", "aerialway", "type",
        "admin_level", "barrier", "boundary", "craft", "geological", "historic",
        "land_area", "landuse", "man_made", "military", "natural", "place",
        "addr:street", "addr:housenumber", "addr:postcode", "addr:city", "addr:country"
    ]

    abs_temp_pbf = os.path.join(osm_dir, "Study_Area_PA_IA1_IA2.osm.pbf")

    output_points = output_points or os.path.join(osm_dir, "Study_Area_Points.gpkg")
    output_polygons = output_polygons or os.path.join(osm_dir, "Study_Area_Polygons.gpkg")

    env = os.environ.copy()
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")

    try:
        progress("study_area_extract", 0, total_steps)
        log("Study Area: OSM-Daten auf PA + IA1 + IA2 zuschneiden ...")
        cmd_extract = [osmium_exe, "extract", "-p", path_poly_ia2, merged_pbf, "-o", abs_temp_pbf, "--overwrite"]
        if run_command:
            run_command(cmd_extract[1:], cwd=osm_dir)
        else:
            kwargs = {"env": env, "capture_output": True, "text": True, "cwd": osm_dir}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            res = subprocess.run(cmd_extract, **kwargs)
            if res.returncode != 0:
                log(res.stderr.strip())
                return False

        for step_index, (source_layer, dest_file, dest_layer) in enumerate([
            ("points", output_points, "poi_points"),
            ("multipolygons", output_polygons, "poi_polygons"),
        ], start=1):
            progress(f"study_area_{source_layer}", step_index, total_steps)
            log(f"Study Area: Layer {source_layer} verarbeiten ...")
            try:
                gdf_layer = gpd.read_file(abs_temp_pbf, layer=source_layer)
                if gdf_layer.empty: continue

                features = []
                for _, row in gdf_layer.iterrows():
                    geom = row['geometry']
                    if geom is None or geom.is_empty: continue

                    tags = row.to_dict()
                    tags.update(parse_other_tags(tags.get('other_tags')))

                    raw_id = tags.get('osm_id') or tags.get('id')
                    osm_id_str = get_safe_string(raw_id).split('.')[0]
                    if not osm_id_str: osm_id_str = "unknown"

                    feature_dict = {"osm_id": osm_id_str, "geometry": geom}

                    has_any_data = False
                    for dt in desired_tags:
                        val = get_safe_string(tags.get(dt))
                        feature_dict[dt] = val
                        if val: has_any_data = True

                    if has_any_data:
                        features.append(feature_dict)

                if features:
                    final_gdf = gpd.GeoDataFrame(features, crs="EPSG:4326")
                    save_gpkg_safely(final_gdf, dest_file, dest_layer)
                    log(f"Study-Area-Layer gespeichert: {dest_file}")
            except Exception as e:
                log(f"Fehler bei Layer {source_layer}: {e}")

        if os.path.exists(abs_temp_pbf):
            os.remove(abs_temp_pbf)
        return {"study_area_points": output_points, "study_area_polygons": output_polygons}

    except Exception as e:
        log(f"Fehler: {e}")
        return False
