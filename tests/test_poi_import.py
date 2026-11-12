import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.scripts.visum.scripts.poi_import import category_for_file, ensure_import_categories, import_project_pois


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


if __name__ == '__main__':
    unittest.main()
