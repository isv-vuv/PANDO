import os
import geopandas as gpd
from shapely.geometry import Point


def save_safely(gdf, filepath):
    if os.path.exists(filepath):
        os.remove(filepath)
    driver = "GPKG" if filepath.lower().endswith(".gpkg") else "GeoJSON"
    kwargs = {"driver": driver}
    if driver == "GPKG":
        kwargs["layer"] = "center_point"
    else:
        kwargs["coordinate_precision"] = 5
    gdf.to_file(filepath, **kwargs)
    return filepath


def export_center_point(coordinates_string, osm_dir, *, output_path=None):
    print("\n--- Module: Exporting 'center point' layer started ---")

    output_geojson = output_path or os.path.join(osm_dir, "center_point.gpkg")

    try:
        # Koordinaten aus dem String splitten (lat, lon)
        lat_raw, lon_raw = coordinates_string.split(",")
        lat = float(lat_raw.strip())
        lon = float(lon_raw.strip())

        # Wichtig: Shapely Points werden als (X, Y), d. h. (Längengrad, Breitengrad), definiert.
        geometry = Point(lon, lat)

        # Ein simples Feature erstellen
        features = [{
            "geometry": geometry,
            "name": "Center Point",
            "category": "center"
        }]

        # In GeoPandas laden und strikt als WGS84 (EPSG:4326) definieren
        gdf = gpd.GeoDataFrame(features, crs="EPSG:4326")

        # Sicher speichern
        save_safely(gdf, output_geojson)

    except Exception as e:
        print(f"Fehler beim Erstellen des Mittelpunkts: {e}")
