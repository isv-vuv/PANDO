import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.app.app_core.project import tool_root


class NationwideIntensityTests(unittest.TestCase):
    def test_nationwide_intensity(self):
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            from scipy.spatial import cKDTree
        except ImportError:
            raise unittest.SkipTest("geopandas, shapely or scipy not installed in test environment")

        from core.scripts.qgis.scripts.Model5_NationwideIntensity import run_nationwide_intensity_estimation

        tmp = Path(tempfile.mkdtemp())
        try:
            seeds = gpd.GeoDataFrame([
                {'fid': 1, 'name': 'City A', 'TypeNo': 0, 'POP': 500000, 'geometry': Point(500000, 1000000)}
            ], geometry='geometry', crs='EPSG:32651')
            seeds_file = tmp / 'central_place_points.gpkg'
            seeds.to_file(str(seeds_file), driver='GPKG', layer='central_place_points')

            pois_data = [
                {'name': 'Shop near seed', 'sector': 'RETAIL', 'weight': 4, 'geometry': Point(500100, 1000100)},
                {'name': 'Townhall B', 'sector': 'GOVERNMENT', 'weight': 4, 'geometry': Point(505000, 1005000)},
                {'name': 'Hospital B', 'sector': 'HEALTHCARE', 'weight': 3, 'geometry': Point(505050, 1005050)},
                {'name': 'Mall B', 'sector': 'RETAIL', 'weight': 4, 'geometry': Point(505020, 1005020)},
                {'name': 'Bakery B', 'sector': 'RETAIL', 'weight': 1, 'geometry': Point(505100, 1005100)},
                {'name': 'School C', 'sector': 'EDUCATION', 'weight': 2, 'geometry': Point(510000, 1010000)},
            ]
            pois_gdf = gpd.GeoDataFrame(pois_data, geometry='geometry', crs='EPSG:32651')
            pois_file = tmp / 'pois.gpkg'
            pois_gdf.to_file(str(pois_file), driver='GPKG', layer='pois')

            out_dir = tmp / 'output'
            out_file = out_dir / 'nationwide_centrality_points.gpkg'
            styles_dir = Path(tool_root()) / 'core' / 'scripts' / 'qgis' / 'styles'

            res = run_nationwide_intensity_estimation(
                seeds_gpkg=seeds_file,
                pois_or_pbf=pois_file,
                output_gpkg=out_file,
                local_crs='EPSG:32651',
                radius=500.0,
                min_intensity=0.5,
                min_intensity_level_3=5.0,
                styles_dir=styles_dir,
            )
            res_gdf = gpd.read_file(str(out_file))
            self.assertGreater(len(res_gdf), 0)

            # Verify all sector files and debug layers were created
            expected_files = [
                'central_place_points_nationwide.gpkg',
                'inner_urban_central_points_nationwide.gpkg',
                'pois_before_deduplication.gpkg',
                'pois_after_deduplication.gpkg',
                'sector_all_points.gpkg',
                'sector_all_intensity.tif',
            ]
            for ef in expected_files:
                fpath = out_dir / ef
                self.assertTrue(fpath.is_file(), f"Missing expected output file: {ef}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_poi_classification_precedence(self):
        from core.scripts.qgis.scripts.Model5_NationwideIntensity import classify_poi_feature

        # Max-weight test case: "Schwimmbad Obfelden" with leisure=sports_centre (2) and amenity=public_bath (3)
        # Under max-weight ordering, public_bath (weight 3, radius 250m) takes precedence over sports_centre (weight 2, 175m)
        tags = {
            "name": "Schwimmbad Obfelden",
            "amenity": "public_bath",
            "leisure": "sports_centre",
        }
        sector, weight, radius, primary_tag, secondary_tag, classification, description = classify_poi_feature(tags)
        self.assertEqual(sector, "LEISURE")
        self.assertEqual(weight, 3)
        self.assertEqual(radius, 250)
        self.assertEqual(primary_tag, "amenity=public_bath")
        self.assertEqual(secondary_tag, "leisure=sports_centre")
        self.assertEqual(classification, "K")
        self.assertEqual(description, "Public bath or spa")

        # Test case: pure public bath
        tags_bath = {
            "name": "Stadtbad",
            "amenity": "public_bath",
        }
        sec2, w2, rad2, tag2, sec_tag2, class2, desc2 = classify_poi_feature(tags_bath)
        self.assertEqual(sec2, "LEISURE")
        self.assertEqual(w2, 3)
        self.assertEqual(rad2, 250)
        self.assertEqual(tag2, "amenity=public_bath")
        self.assertEqual(sec_tag2, "")
        self.assertEqual(class2, "K")
        self.assertEqual(desc2, "Public bath or spa")

    def test_raster_generation_quartic_kernel(self):
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            from osgeo import gdal
        except ImportError:
            raise unittest.SkipTest("geopandas, shapely or osgeo.gdal not installed in test environment")

        from core.scripts.qgis.scripts.Model5_NationwideIntensity import generate_sector_rasters

        tmp = Path(tempfile.mkdtemp())
        try:
            # Create sample POIs
            pois = [
                {"geometry": Point(500, 500), "sector": "LEISURE", "Weight": 3, "Radius": 250},
                {"geometry": Point(700, 700), "sector": "EDUCATION", "Weight": 2, "Radius": 175},
            ]
            pois_gdf = gpd.GeoDataFrame(pois, crs="EPSG:32632")

            raster_paths = generate_sector_rasters(
                pois_gdf=pois_gdf,
                output_dir=tmp,
                local_crs="EPSG:32632",
                cell_size=25.0,
            )
            self.assertIn("leisure", raster_paths)
            self.assertIn("all", raster_paths)
            self.assertTrue(raster_paths["all"].is_file())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_prepare_model5_poi_datasets(self):
        try:
            import geopandas as gpd
            from shapely.geometry import Point, Polygon
        except ImportError:
            raise unittest.SkipTest("geopandas or shapely not installed in test environment")

        from core.scripts.qgis.scripts.Model5_NationwideIntensity import prepare_model5_poi_datasets

        tmp = Path(tempfile.mkdtemp())
        try:
            # Create a sample POI GPKG to simulate input (including duplicate points with same name/sector within 250m)
            pois_in = [
                {"name": "Town Hall", "amenity": "townhall", "geometry": Point(8.5, 47.3)},
                {"name": "Town Hall", "amenity": "townhall", "geometry": Point(8.5001, 47.3001)},
                {"name": "Library", "amenity": "library", "geometry": Point(8.501, 47.301)},
                {"name": "Far Away Clinic", "amenity": "clinic", "geometry": Point(9.5, 48.0)},
            ]
            pois_gdf = gpd.GeoDataFrame(pois_in, crs="EPSG:4326")
            in_gpkg = tmp / "sample_pois.gpkg"
            pois_gdf.to_file(str(in_gpkg), layer="sample_pois", driver="GPKG")

            # Create a sample .poly file enclosing the first 3 points
            poly_file = tmp / "bound_pa_ia1_ia2.poly"
            poly_content = """study_area
1
    8.40 47.20
    8.60 47.20
    8.60 47.40
    8.40 47.40
    8.40 47.20
END
END
"""
            poly_file.write_text(poly_content, encoding="utf-8")

            out_features = tmp / "03_features"
            res = prepare_model5_poi_datasets(
                merged_pbf=in_gpkg,
                poly_study_area=poly_file,
                features_dir=out_features,
                local_crs="EPSG:32632",
            )

            self.assertTrue(res["model5_pois_nationwide_all"].is_file())
            self.assertTrue(res["model5_pois_nationwide_cleaned"].is_file())
            self.assertTrue(res["model5_pois_study_area_cleaned"].is_file())
            self.assertTrue((out_features / "poi_points.gpkg").is_file())

            # Read and assert counts
            gdf_all = gpd.read_file(str(res["model5_pois_nationwide_all"]))
            gdf_clean = gpd.read_file(str(res["model5_pois_nationwide_cleaned"]))
            gdf_study = gpd.read_file(str(res["model5_pois_study_area_cleaned"]))

            self.assertEqual(len(gdf_all), 4)
            self.assertEqual(len(gdf_clean), 3)  # Duplicate removed!
            self.assertEqual(len(gdf_study), 2)  # Clipped to study area (excluding Far Away Clinic)
            self.assertIn("Town Hall", gdf_study["name"].values)
            self.assertIn("Library", gdf_study["name"].values)
            self.assertNotIn("Far Away Clinic", gdf_study["name"].values)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
