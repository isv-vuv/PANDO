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


def save_safely(gdf, filepath, layer_name="pop_zero_osm"):
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


def export_uninhabited(
    clipped_pbf, osm_dir, osmium_exe, bin_dir, west=None, south=None, east=None, north=None,
    *, output_path=None, run_command=None, log=print
):
    log("--- Module: Exporting 'uninhabited' layer started ---")

    filtered_pbf = os.path.join(osm_dir, "temp_uninhabited_filtered.osm.pbf")
    output_geojson = output_path or os.path.join(osm_dir, "osm_pop_0.gpkg")

    env = os.environ.copy()
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")

    try:
        log("Filtering tags for uninhabited infrastructures and landuse...")
        uninhabited_filter = [
            "w/highway=motorway,trunk,primary,secondary,tertiary",
            "wr/shop=mall",
            "wr/building=commercial,retail,school,construction,university",
            "wr/amenity=hospital,bus_station,school,police,parking",
            "wr/landuse=cemetery,forest,military,allotments,recreation_ground,plant_nursery,village_green",
            "wr/leisure=golf_course,park,garden,playground,pitch",
            "wr/boundary=protected_area",
            "wr/aeroway=aerodrome",
            "wr/natural=wood,scrub,grassland,wetland,water"
        ]

        cmd_filter = [osmium_exe, "tags-filter", clipped_pbf] + uninhabited_filter + ["-o", filtered_pbf, "--overwrite"]
        if run_command:
            run_command(cmd_filter[1:])
        else:
            kwargs = {"env": env, "check": True}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.run(cmd_filter, **kwargs)

        features = []
        for layer_name in ['lines', 'multipolygons']:
            try:
                gdf_layer = gpd.read_file(filtered_pbf, layer=layer_name)
                if gdf_layer.empty: continue

                for _, row in gdf_layer.iterrows():
                    tags = row.to_dict()
                    geom = row['geometry']
                    if geom is None or geom.is_empty: continue

                    tags.update(parse_other_tags(tags.get('other_tags')))

                    val_highway = get_safe_string(tags.get("highway"))
                    val_building = get_safe_string(tags.get("building"))
                    val_landuse = get_safe_string(tags.get("landuse"))
                    val_natural = get_safe_string(tags.get("natural"))
                    val_leisure = get_safe_string(tags.get("leisure"))
                    val_amenity = get_safe_string(tags.get("amenity"))
                    val_shop = get_safe_string(tags.get("shop"))
                    val_aeroway = get_safe_string(tags.get("aeroway"))
                    val_boundary = get_safe_string(tags.get("boundary"))

                    category = "other"
                    if val_highway:
                        category = "street"
                    elif val_building:
                        category = "building"
                    elif val_landuse == "forest" or val_natural == "wood":
                        category = "forest"
                    elif val_leisure in ("park", "playground", "pitch", "garden", "golf_course") or val_landuse in (
                        "recreation_ground", "village_green"
                    ):
                        category = "park_recreation"
                    elif val_natural == "water":
                        category = "water"
                    elif val_natural or val_landuse in ("cemetery", "military", "allotments", "plant_nursery") or val_leisure or val_boundary == "protected_area":
                        category = "green_area"
                    elif val_amenity or val_shop or val_aeroway:
                        category = "amenity_shop"

                    val_parking = get_safe_string(tags.get("parking"))
                    val_location = get_safe_string(tags.get("location"))
                    if val_amenity == "parking" and (val_parking == "underground" or val_location == "underground"):
                        continue

                    raw_id = tags.get('osm_id') or tags.get('osm_way_id') or tags.get('id')
                    osm_id_str = get_safe_string(raw_id).split('.')[0]
                    if not osm_id_str: osm_id_str = "unknown"

                    features.append({
                        "geometry": geom,
                        "osm_id": osm_id_str,
                        "name": get_safe_string(tags.get('name')),
                        "category": category
                    })
            except Exception as e:
                pass

        if features:
            gdf = gpd.GeoDataFrame(features, crs="EPSG:4326")

            log("Buffering road line networks into 0.5m polygon strips...")
            utm_crs = gdf.estimate_utm_crs()
            gdf = gdf.to_crs(utm_crs)
            is_line = gdf.geometry.type.isin(['LineString', 'MultiLineString'])
            gdf.loc[is_line, 'geometry'] = gdf[is_line].geometry.buffer(0.5, resolution=1, cap_style='square')
            gdf = gdf.to_crs("EPSG:4326")

            if None not in (west, south, east, north):
                gdf = gpd.clip(gdf, [west, south, east, north])
                gdf = gdf[~gdf.geometry.is_empty]
        else:
            log("Notice: No uninhabited geometries found in target area. Creating empty GeoDataFrame...")
            gdf = gpd.GeoDataFrame(columns=["geometry", "osm_id", "name", "category"], crs="EPSG:4326")

        save_safely(gdf, output_geojson)
        log(f"OSM-Population-0 gespeichert: {output_geojson}")
        return output_geojson
    finally:
        if os.path.exists(filtered_pbf): os.remove(filtered_pbf)
    return None
