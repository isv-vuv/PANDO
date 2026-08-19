import os
import subprocess
import geopandas as gpd
import math
import re


def get_safe_string(val):
    if val is None: return ""
    if isinstance(val, float) and math.isnan(val): return ""
    return str(val).strip()


def parse_other_tags(hstore_str):
    if not isinstance(hstore_str, str): return {}
    return dict(re.findall(r'"(.*?)"=>"(.*?)"', hstore_str))


def save_safely(gdf, filepath, layer_name="osm_cities"):
    """Write GeoDataFrame safely to file format with layer and spatial indexing options."""
    if os.path.exists(filepath):
        os.remove(filepath)
    driver = "GPKG" if filepath.lower().endswith(".gpkg") else "GeoJSON"
    kwargs = {"driver": driver}
    if driver == "GPKG":
        kwargs["layer"] = layer_name
        kwargs["layer_options"] = {"SPATIAL_INDEX": "YES"}
    else:
        kwargs["coordinate_precision"] = 5
    gdf.to_file(filepath, **kwargs)
    return filepath


def export_cities(
    merged_pbf, osm_dir, osmium_exe, bin_dir, *, output_path=None,
    run_command=None, log=print
):
    log("--- Module: Exporting 'cities' layer (Full Country/Merged Area) started ---")

    filtered_pbf = os.path.join(osm_dir, "temp_cities_filtered.osm.pbf")
    output_geojson = output_path or os.path.join(osm_dir, "osm_cities.gpkg")

    env = os.environ.copy()
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")

    try:
        log("Filtering nodes for cities and towns in the entire merged file...")
        cmd_filter = [osmium_exe, "tags-filter", merged_pbf, "n/place=city,town", "-o", filtered_pbf, "--overwrite"]
        if run_command:
            run_command(cmd_filter[1:])
        else:
            kwargs = {"env": env, "check": True}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.run(cmd_filter, **kwargs)

        try:
            features = []
            try:
                gdf_points = gpd.read_file(filtered_pbf, layer='points')
                if not gdf_points.empty:
                    for _, row in gdf_points.iterrows():
                        tags = row.to_dict()
                        if row['geometry'] is None: continue

                        tags.update(parse_other_tags(tags.get('other_tags')))

                        raw_id = tags.get('osm_id') or tags.get('id')
                        osm_id_str = get_safe_string(raw_id).split('.')[0]
                        if not osm_id_str: osm_id_str = "unknown"

                        features.append({
                            "geometry": row['geometry'],
                            "osm_id": osm_id_str,
                            "name": get_safe_string(tags.get('name')),
                            "name_en": get_safe_string(tags.get('name:en')),
                            "place_type": get_safe_string(tags.get('place'))
                        })
            except Exception as e:
                log(f"Notice/Error parsing city nodes: {e}")

            if features:
                gdf_out = gpd.GeoDataFrame(features, crs="EPSG:4326")
            else:
                log("Notice: No cities or towns found. Creating empty GeoDataFrame...")
                gdf_out = gpd.GeoDataFrame(columns=["geometry", "osm_id", "name", "name_en", "place_type"], crs="EPSG:4326")

            save_safely(gdf_out, output_geojson)
            log(f"OSM-Städte gespeichert: {output_geojson}")
            return output_geojson
        except Exception as exc:
            log(f"Error in export_cities: {exc}")

    finally:
        if os.path.exists(filtered_pbf): os.remove(filtered_pbf)
    return None
