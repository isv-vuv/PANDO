import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.scripts.visum.scripts.poi_import import (
    clean_poi_name, import_project_pois, POIValidationError,
)

try:
    # Match the application startup order before loading geospatial DLLs.
    from core.app.app_qt import qt_base
    import pyproj
    import geopandas as gpd
    from shapely.geometry import Point, Polygon, LineString
except ImportError:
    gpd = None


class POINameTests(unittest.TestCase):
    def test_repairs_common_source_names_without_losing_unicode(self):
        for source, expected in [
            ('Praxis;\r\nÄrzte\x00\x07', 'Praxis, Ärzte'),
            ('SchÃ¶ppe', 'Schöppe'), ('fÃ¼r', 'für'),
            ('東京 – François Müller', '東京 – François Müller'),
            ('Ãngela', 'Ãngela'), ('A\u2028B\u2029C', 'A B C'),
            ('A' * 10000, 'A' * 10000),
            ('Bad\ufffdName', 'BadName'), ('\ufffd\ufffd', ''),
        ]:
            with self.subTest(source=source[:30]):
                self.assertEqual(clean_poi_name(source), expected)


@unittest.skipIf(gpd is None, 'Requires the QGIS geospatial Python environment')
class POIPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.visum = MagicMock()
        self.visum.Net.AttValue.return_value = 'EPSG:3857'
        self.visum.Net.POICategories.ItemByKey.return_value.POIs.GetMultiAttValues.return_value = []
        self.frames = {}

    def add_source(self, filename, geometries, names=None, administrative=False, crs='EPSG:3857'):
        folder = self.root / 'shapefile' / ('Zones' if administrative else 'POI')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        path.touch()
        frame = gpd.GeoDataFrame({'name': names or ['Name'] * len(geometries)},
                                 geometry=geometries, crs=crs)
        self.frames[path] = frame
        return frame

    def run_import(self):
        # Real geometry operations and reprojection, mocked file I/O and Visum.
        from pyproj import CRS
        self.visum.Net.AttValue.return_value = CRS.from_epsg(3857).to_wkt()
        with patch.object(gpd, 'read_file', side_effect=lambda path: self.frames[path]):
            return import_project_pois(self.visum, self.root, MagicMock())

    def report(self):
        return json.loads((self.root / 'poi_validation_report.json').read_text(encoding='utf-8'))

    def test_collects_errors_across_files_before_any_visum_write(self):
        self.add_source('sector_education_points.shp', [Point(1, 2), LineString([(0, 0), (1, 1)])])
        self.add_source('sector_healthcare_points.shp', [Point(2, 3)], crs=None)
        with self.assertRaises(POIValidationError) as caught:
            self.run_import()
        self.assertIn('Datensatz 2', str(caught.exception))
        self.assertIn('Koordinatensystem', str(caught.exception))
        self.assertEqual(len(self.report()['errors']), 2)
        self.visum.Net.AddPOI.assert_not_called()
        self.visum.Net.AddPOICategory.assert_not_called()

    def test_repairs_names_empty_and_3d_points_and_keeps_sources_intact(self):
        frame = self.add_source('sector_education_points.shp', [Point(1, 2, 3), None],
                                ['SchÃ¶ppe;\nÄrzte\x00', 'Ohne Ort'])
        self.assertEqual(self.run_import(), 1)
        self.visum.Net.AddPOI.assert_called_once()
        self.visum.Net.AddPOI.return_value.SetAttValue.assert_called_once_with('NAME', 'Schöppe, Ärzte')
        self.assertTrue(frame.geometry.iloc[0].has_z)
        self.assertEqual(frame['name'].iloc[0], 'SchÃ¶ppe;\nÄrzte\x00')
        self.assertEqual(len(self.report()['corrections']), 3)

    def test_repairs_self_intersecting_administrative_surface(self):
        polygon = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
        self.add_source('adm2.shp', [polygon], administrative=True)
        self.assertEqual(self.run_import(), 1)
        calls = self.visum.Net.AddPOI.return_value.SetAttValue.call_args_list
        from shapely import wkt
        surface = next(call.args[1] for call in calls if call.args[0] == 'WKTSURFACE')
        self.assertTrue(wkt.loads(surface).is_valid)
        self.assertTrue(self.report()['corrections'])

    def test_rejects_nonfinite_coordinates(self):
        self.add_source('sector_education_points.shp', [Point(float('inf'), 2), Point(1, 2)],
                        ['Name', 'Beschädigt\ufffd'])
        with self.assertRaises(POIValidationError):
            self.run_import()
        self.assertEqual(len(self.report()['errors']), 1)
        self.visum.Net.AddPOI.assert_not_called()

    def test_removes_replacement_characters_and_reports_correction(self):
        self.add_source('adm3.shp', [Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])],
                        ['Bad\ufffdName'], administrative=True)
        self.assertEqual(self.run_import(), 1)
        self.visum.Net.AddPOI.return_value.SetAttValue.assert_any_call('NAME', 'BadName')
        report = self.report()
        self.assertEqual(report['errors'], [])
        self.assertTrue(any('BadName' in correction for correction in report['corrections']))

    def test_rejects_administrative_line_and_empty_only_dataset(self):
        self.add_source('adm2.shp', [LineString([(0, 0), (1, 1)])], administrative=True)
        self.add_source('sector_education_points.shp', [None])
        with self.assertRaises(POIValidationError):
            self.run_import()
        self.visum.Net.AddPOI.assert_not_called()

    def test_missing_files_are_reported(self):
        with self.assertRaises(POIValidationError):
            self.run_import()
        self.assertIn('Keine passenden', self.report()['errors'][0])

    def test_read_errors_are_collected_without_writing_to_visum(self):
        self.add_source('sector_education_points.shp', [Point(1, 2)])
        self.add_source('sector_healthcare_points.shp', [Point(2, 3)])
        self.visum.Net.AttValue.return_value = pyproj.CRS.from_epsg(3857).to_wkt()
        with patch.object(gpd, 'read_file', side_effect=OSError('Shapefile unlesbar')):
            with self.assertRaises(POIValidationError):
                import_project_pois(self.visum, self.root, MagicMock())
        self.assertEqual(sum('Shapefile unlesbar' in error for error in self.report()['errors']), 2)
        self.visum.Net.AddPOI.assert_not_called()

    def test_reprojects_before_import(self):
        self.add_source('sector_education_points.shp', [Point(9, 48)], crs='EPSG:4326')
        self.assertEqual(self.run_import(), 1)
        args = self.visum.Net.AddPOI.call_args.args
        self.assertGreater(args[2], 1000000)
        self.assertGreater(args[3], 6000000)

    def test_unexpected_com_error_identifies_source_record(self):
        self.add_source('sector_education_points.shp', [Point(1, 2)])
        self.visum.Net.AddPOI.return_value.SetAttValue.side_effect = RuntimeError('Visum attribute rejected')
        with self.assertRaisesRegex(RuntimeError, r'sector_education_points.shp, Datensatz 1.*POI-Nr. 1'):
            self.run_import()


class POIErrorDialogTests(unittest.TestCase):
    def test_pipeline_preserves_validation_details_for_worker(self):
        import importlib
        chain = importlib.import_module('core.scripts.visum.scripts.00_run_all_visum_scripts')
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(chain, 'deploy_visum_importer_to_appdata',
                              side_effect=POIValidationError('POI-Testfehler: Datensatz 2')):
                self.assertFalse(chain.run_visum_postprocessing_chain(directory, log=MagicMock()))
            self.assertEqual(chain.LAST_ERROR, 'POI-Testfehler: Datensatz 2')

    def test_validation_details_are_displayed_by_finish_handler(self):
        from core.app.app_qt.steps import step5_visum as ui
        page = MagicMock(_visum_timer=None, _thread=None, selective_update=False,
                         running_state_changed=None)
        message = 'POI-Vorprüfung fehlgeschlagen.\nsector_education_points.shp, Datensatz 2\nBericht: test.json'
        with patch.object(ui.Dialogs, 'error') as dialog:
            ui.Step5VisumWidget._on_finished(page, False, message)
            self.assertEqual(dialog.call_args.args[2], message)
