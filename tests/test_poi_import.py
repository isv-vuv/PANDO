import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.scripts.visum.scripts.poi_import import (
    VISUM_VALUETYPE_BOOL,
    VISUM_VALUETYPE_INT,
    VISUM_VALUETYPE_REAL,
    VISUM_VALUETYPE_STRING,
    category_for_file,
    ensure_import_categories,
    import_project_pois,
)

try:
    # Match the application startup order before loading geospatial DLLs.
    from core.app.app_qt import qt_base  # noqa: F401
    import geopandas as gpd
    from pyproj import CRS
    from shapely.geometry import Point, Polygon
except ImportError:
    gpd = None


class POIImportTests(unittest.TestCase):
    def test_categories_can_be_prepared_again_without_duplicates(self):
        visum = MagicMock()
        created = []

        def add_category():
            category = MagicMock()
            attributes = {}
            category.SetAttValue.side_effect = attributes.__setitem__
            category.AttValue.side_effect = attributes.__getitem__
            created.append(category)
            return category

        visum.Net.AddPOICategory.side_effect = add_category
        visum.Net.POICategories.GetAll = created
        ensure_import_categories(visum)
        self.assertEqual(len(created), 12)
        ensure_import_categories(visum)
        self.assertEqual(len(created), 12)

    def test_import_normalizes_name_whitespace_before_visum_assignment(self):
        names = [
            'Ministerium der Justiz und für Migration\r\n Baden-Württemberg',
            'Name\nmit\rZeilenumbrüchen',
            'Name\u0085mit\u2028Unicode\u2029Umbrüchen',
            '  Name\t mit  Leerzeichen  ',
            'Universität Tübingen',
            'Dr. Ralph Kaufmann; Dr. Eva Hammen-SchÃ¶ppe; Dr. Michael Theurer',
            'Praxis;\r\nÄrzte',
            None,
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'shapefile' / 'POI'
            folder.mkdir(parents=True)
            (folder / 'sector_government_points.shp').touch()
            point = types.SimpleNamespace(is_empty=False, geom_type='Point', x=1, y=2, is_valid=True, has_z=False)
            rows = []
            for name in names:
                row = MagicMock(geometry=point)
                row.__getitem__.side_effect = {'name': name}.__getitem__
                rows.append(row)
            frame = MagicMock(crs='source')
            frame.columns = ['geometry', 'name']
            frame.to_crs.return_value = frame
            frame.iterrows.return_value = list(enumerate(rows))
            visum = MagicMock()
            visum.Net.POICategories.ItemByKey.return_value.POIs.GetMultiAttValues.return_value = []
            with patch.dict('sys.modules', {
                'geopandas': types.SimpleNamespace(read_file=MagicMock(return_value=frame)),
                'pandas': types.SimpleNamespace(notna=lambda value: value is not None),
                'pyproj': types.SimpleNamespace(CRS=MagicMock()),
            }):
                self.assertEqual(import_project_pois(visum, root, log=MagicMock()), len(names))
            self.assertEqual(
                [call.args for call in visum.Net.AddPOI.return_value.SetAttValue.call_args_list],
                [('NAME', name) for name in [
                    'Ministerium der Justiz und für Migration Baden-Württemberg',
                    'Name mit Zeilenumbrüchen',
                    'Name mit Unicode Umbrüchen',
                    'Name mit Leerzeichen',
                    'Universität Tübingen',
                    'Dr. Ralph Kaufmann, Dr. Eva Hammen-Schöppe, Dr. Michael Theurer',
                    'Praxis, Ärzte',
                ]],
            )

    def test_category_mapping_excludes_combined_and_traffic_zone_files(self):
        for name, number in [('healthcare', 21), ('leisure', 22), ('retail', 23),
                             ('education', 24), ('government', 25)]:
            self.assertEqual(category_for_file(f'sector_{name}_points.shp'), number)
        self.assertIsNone(category_for_file('sector_all_points.shp'))
        for level in range(4):
            self.assertEqual(category_for_file(f'adm{level}.shp', True), 10 + level)
        for name in ('zones.shp', 'mainzones.shp', 'adm4.shp'):
            self.assertIsNone(category_for_file(name, True))

    def test_import_uses_model_crs_category_object_and_admin_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder, name in [('POI', 'sector_education_points'), ('POI', 'sector_all_points'),
                                 ('Zones', 'adm2'), ('Zones', 'zones')]:
                (root / 'shapefile' / folder).mkdir(parents=True, exist_ok=True)
                (root / 'shapefile' / folder / (name + '.shp')).touch()
            point = types.SimpleNamespace(is_empty=False, geom_type='Point', x=123, y=456, is_valid=True, has_z=False)
            polygon = MagicMock(is_empty=False, wkt='MULTIPOLYGON (...)', geom_type='MultiPolygon', is_valid=True, has_z=False)
            polygon.representative_point.return_value = point
            frames = []
            for geometry in (point, polygon):
                frame = MagicMock(crs='source')
                frame.columns = ['geometry']
                frame.to_crs.return_value = frame
                frame.iterrows.return_value = [(0, types.SimpleNamespace(geometry=geometry))]
                frames.append(frame)
            gpd = types.SimpleNamespace(read_file=MagicMock(side_effect=frames))
            crs = MagicMock()
            visum = MagicMock()
            category = visum.Net.POICategories.ItemByKey.return_value
            category.POIs.GetMultiAttValues.return_value = [(1, 8)]
            with patch.dict('sys.modules', {'geopandas': gpd, 'pandas': MagicMock(),
                                           'pyproj': types.SimpleNamespace(CRS=crs)}):
                self.assertEqual(import_project_pois(visum, root, log=MagicMock()), 2)
            self.assertEqual([c.args[0] for c in visum.Net.POICategories.ItemByKey.call_args_list], [24, 12])
            for frame in frames:
                frame.to_crs.assert_called_once_with(crs.from_wkt.return_value)
            self.assertEqual(visum.Net.AddPOI.call_count, 2)
            visum.Net.AddPOI.assert_called_with(9, category, 123.0, 456.0)
            visum.Net.AddPOI.return_value.SetAttValue.assert_called_once_with('WKTSURFACE', polygon.wkt)

    @unittest.skipIf(gpd is None, 'Requires the QGIS geospatial Python environment')
    def test_creates_poi_udas_from_geodata_dtypes_and_uses_native_shapefile_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            poi_dir = root / 'shapefile' / 'POI'
            zones_dir = root / 'shapefile' / 'Zones'
            poi_dir.mkdir(parents=True)
            zones_dir.mkdir(parents=True)

            pts = gpd.GeoDataFrame(
                {
                    'name': ['Klinik A', 'Klinik B'],
                    'Weight': [3, 1],
                    'fid': [1.5, 2.5],
                    'is_polygon': [False, True],
                    'sector': ['HEALTHCARE', 'HEALTHCARE'],
                },
                geometry=[Point(10, 20), Point(30, 40)],
                crs='EPSG:3857',
            )
            pts_path = poi_dir / 'sector_healthcare_points.shp'
            pts.to_file(pts_path, encoding='utf-8')

            adm = gpd.GeoDataFrame(
                {'NAME_2': ['Bezirk X'], 'POP': [12500.0]},
                geometry=[Polygon([(0, 0), (10, 0), (10, 10), (0, 0)])],
                crs='EPSG:3857',
            )
            adm_path = zones_dir / 'adm2.shp'
            adm.to_file(adm_path, encoding='utf-8')

            visum = MagicMock()
            visum._use_native_shapefile_import = True
            visum.Net.AttValue.return_value = CRS.from_epsg(3857).to_wkt()

            cat21 = MagicMock()
            cat21.POIs.AttrExists.return_value = False
            cat21.POIs.GetMultiAttValues.return_value = []

            cat12 = MagicMock()
            cat12.POIs.AttrExists.return_value = False

            def get_multi(attr_id, OnlyActive=False):
                if attr_id == 'NAME_2':
                    return [(1, 'Bezirk X')]
                return []

            cat12.POIs.GetMultiAttValues.side_effect = get_multi
            visum.Net.POICategories.ItemByKey.side_effect = lambda key: {21: cat21, 12: cat12}[key]

            para_pt = MagicMock()
            para_adm = MagicMock()
            visum.IO.CreateImportShapeFilePara.side_effect = [para_pt, para_adm]

            count = import_project_pois(visum, root, log=MagicMock())
            self.assertEqual(count, 3)
            visum.Net.AddPOI.assert_not_called()
            self.assertEqual(visum.IO.ImportShapefile.call_count, 2)

            created_pt_udas = {
                call.args[0]: call.args[3]
                for call in cat21.POIs.AddUserDefinedAttribute.call_args_list
            }
            self.assertEqual(
                created_pt_udas,
                {
                    'WEIGHT': VISUM_VALUETYPE_INT,
                    'FID': VISUM_VALUETYPE_REAL,
                    'IS_POLYGON': VISUM_VALUETYPE_BOOL,
                    'SECTOR': VISUM_VALUETYPE_STRING,
                },
            )
            self.assertEqual(para_pt.ObjectType, 59)
            self.assertTrue(para_pt.CreateUserDefinedAttributes)
            self.assertTrue(para_pt.SetAttributeAllocationsByIDs)
            para_pt.SetAttValue.assert_any_call('POIKey', 21)
            para_pt.SetAttValue.assert_any_call('ReadAdditive', True)
            para_pt.AddAttributeAllocation.assert_any_call('Weight', 'WEIGHT')
            para_pt.AddAttributeAllocation.assert_any_call('name', 'NAME')

            self.assertEqual(para_adm.ObjectType, 9)
            para_adm.SetAttValue.assert_any_call('POIKey', 12)
            cat12.POIs.SetMultiAttValues.assert_called_once_with('NAME', [(1, 'Bezirk X')], False)

    @unittest.skipIf(gpd is None, 'Requires the QGIS geospatial Python environment')
    def test_native_geojson_import_uses_create_import_geojson_para(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            poi_dir = root / 'shapefile' / 'POI'
            poi_dir.mkdir(parents=True)

            pts = gpd.GeoDataFrame(
                {'name': ['Schule 1'], 'Radius': [200]},
                geometry=[Point(122.95, 10.67)],
                crs='EPSG:4326',
            )
            gj_path = poi_dir / 'sector_education_points.geojson'
            pts.to_file(gj_path, driver='GeoJSON')

            visum = MagicMock()
            visum._use_native_shapefile_import = True
            visum.Net.AttValue.return_value = CRS.from_epsg(4326).to_wkt()

            cat24 = MagicMock()
            cat24.POIs.AttrExists.return_value = False
            cat24.POIs.GetMultiAttValues.return_value = []
            visum.Net.POICategories.ItemByKey.return_value = cat24

            para_gj = MagicMock()
            visum.IO.CreateImportGeoJSONPara.return_value = para_gj

            count = import_project_pois(visum, root, log=MagicMock())
            self.assertEqual(count, 1)
            visum.IO.CreateImportGeoJSONPara.assert_called_once()
            visum.IO.ImportGeoJSON.assert_called_once()
            imported_file, used_para = visum.IO.ImportGeoJSON.call_args.args
            self.assertTrue(imported_file.endswith('.geojson'))
            self.assertIs(used_para, para_gj)
            self.assertEqual(para_gj.ObjectType, 59)
            para_gj.SetAttValue.assert_any_call('POIKey', 24)
            para_gj.AddAttributeAllocation.assert_any_call('Radius', 'RADIUS')
            cat24.POIs.AddUserDefinedAttribute.assert_called_once_with(
                'RADIUS', 'Radius', 'Radius', VISUM_VALUETYPE_INT, 0
            )


if __name__ == '__main__':
    unittest.main()
