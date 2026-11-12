"""Report input provenance, readiness, and safe PDF replacement."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

from core.app.app_core.report_data import PARAMETERS, build_report_inputs, report_blockers
from core.app.app_core.result_maps import result_paths
from core.app.app_core.report_worker import generate_report


class ReportDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = {'phases': {name: {'status': 'done', 'parameters': {}}
                                  for name in ('model1', 'model2', 'model3', 'model4', 'model5', 'model6')},
                         'input_pbfs': [{'region': 'bw', 'path': 'bw.pbf', 'downloaded_at': '2026-10-29T12:34:56'},
                                        {'region': 'bayern', 'filename': 'bayern.pbf'}]}
        for model, mapping in PARAMETERS.items():
            self.manifest['phases'][model]['parameters'] = {key: index + 13 for index, key in enumerate(mapping.values())}
        self.metadata = {'selected_location': {'raw': {'address': {'city': 'München & Umgebung'}}},
                         'pbf_references': [{'osm_id': 'bw', 'name': 'Baden-Württemberg', 'path': 'bw.pbf'}]}
        paths = [self.root / 'input/zone_type_selected.gpkg']
        for group in ('population', 'central_places', 'urban', 'zones'):
            paths.extend(result_paths(self.root, group))
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True); path.touch()

    def build(self):
        return build_report_inputs(self.root, metadata=self.metadata, manifest=self.manifest)

    def test_uses_executed_parameters_and_actual_pbf_dates(self):
        data = self.build()
        self.assertEqual(data['munincipality'], 'München & Umgebung')
        self.assertEqual(data['min_population_MR'], 13)
        self.assertEqual(data['min_distance_3-4'], 13)
        self.assertEqual(data['used_osm_area_with_date'], [['Baden-Württemberg', '29.10.2026'], ['bayern', 'nicht dokumentiert']])
        self.assertEqual(json.loads(json.dumps(data, ensure_ascii=False)), data)

    def test_english_inputs_override_project_language(self):
        self.metadata['language'] = 'de'
        self.manifest['phases']['model1']['parameters'] = {'custom_census': 'census.tif', 'pop_local': 'reference.gpkg'}
        data = build_report_inputs(self.root, metadata=self.metadata, manifest=self.manifest, language='en')
        self.assertEqual(data['language'], 'en')
        self.assertIn('Automatically generated', data['title'])
        self.assertEqual(data['used_osm_area_with_date'][0][1], '2026-10-29')
        self.assertEqual(data['used_osm_area_with_date'][1][1], 'not documented')
        self.assertIn('Additional census data:', data['pop_source'])
        self.assertNotIn('Lokale', data['pop_source'])

    def test_cell_edges_use_executed_grid_parameter_instead_of_current_selection(self):
        self.manifest['phases']['model3']['parameters']['grid_size_e0_m'] = 4500
        self.metadata['step3_data'] = {'cell_size_m': 9000}
        data = self.build()
        self.assertEqual(data['cell_size_pa_m'],500)
        self.assertEqual(data['cell_size_ia1_m'],1500)
        self.assertEqual(data['cell_size_ia2_m'],4500)
        self.manifest['phases']['model3']['parameters']['grid_size_e0_m'] = 4501
        self.assertAlmostEqual(self.build()['cell_size_pa_m'],4501/9)

    def test_missing_or_invalid_grid_parameter_blocks_report(self):
        parameters = self.manifest['phases']['model3']['parameters']
        for value in (None, 0, -1, float('nan'), True):
            parameters['grid_size_e0_m'] = value
            with self.assertRaisesRegex(ValueError,'grid_size_e0_m'):
                self.build()

    def test_local_population_and_census_sources_are_disclosed(self):
        self.manifest['phases']['model1']['parameters'] = {'custom_census': 'mein zensus.tif', 'pop_local': 'referenz.gpkg', 'pop_local_field': 'EW'}
        data = self.build()
        self.assertIn('mein zensus.tif', data['pop_source'])
        self.assertIn('referenz.gpkg', data['pop_source'])
        self.assertIn('EW', data['pop_source'])
        self.manifest['phases']['model1']['parameters']['no_local_reference'] = True
        self.assertNotIn('referenz.gpkg', self.build()['pop_source'])

    def test_missing_parameter_stale_model_and_file_block_generation(self):
        del self.manifest['phases']['model2']['parameters']['population_tolerance']
        self.manifest['phases']['model5']['status'] = 'stale'
        result_paths(self.root, 'population')[0].unlink()
        errors = report_blockers(self.root, self.manifest)
        self.assertEqual(len(errors), 3)
        with self.assertRaisesRegex(ValueError, 'population_tolerance'):
            self.build()

    def test_pending_edits_block_generation(self):
        self.manifest['centrality_edits'] = {'pending': ['assembly']}
        self.assertTrue(any('Zentralitätsänderungen' in e for e in report_blockers(self.root, self.manifest)))

    def test_nonfinite_parameters_are_not_passed_to_typst(self):
        self.manifest['phases']['model5']['parameters']['minimum_intensity_level_3'] = float('nan')
        with self.assertRaises(ValueError):
            self.build()


class ReportOutputTests(unittest.TestCase):
    def test_failed_compilation_preserves_previous_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'report').mkdir()
            pdf = root / 'report/report.pdf'; pdf.write_bytes(b'previous PDF')
            with patch('core.app.app_core.report_data.build_report_inputs', return_value={'munincipality':'Test'}), \
                 patch('core.app.app_core.report_maps.export_report_maps'), \
                 patch.dict('sys.modules', {'typst': types.SimpleNamespace(compile=lambda *a, **k: (_ for _ in ()).throw(ValueError('invalid template')))}):
                with self.assertRaisesRegex(ValueError, 'invalid template'):
                    generate_report(root, lambda *a: None)
            self.assertEqual(pdf.read_bytes(), b'previous PDF')
            self.assertTrue((root / 'report/fonts/Inter-Regular.ttf').is_file())
            self.assertEqual(json.loads((root / 'report/report_inputs.json').read_text(encoding='utf-8'))['munincipality'], 'Test')

    def test_language_selects_template_and_rejects_unknown_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('core.app.app_core.report_data.build_report_inputs', return_value={'language': 'en'}) as build, \
                 patch('core.app.app_core.report_data.report_blockers', return_value=[]), \
                 patch('core.app.app_core.report_maps.export_report_maps'), \
                 patch('core.app.app_core.report_visum.export_visum_maps', return_value={'status': 'skipped'}), \
                 patch.dict('sys.modules', {'typst': types.SimpleNamespace(compile=lambda *a, **k: b'%PDF-test')}):
                generate_report(root, lambda *a: None, language='en')
            build.assert_called_once_with(root.resolve(), language='en')
            self.assertIn('lang: "en"', (root / 'report/main.typ').read_text(encoding='utf-8'))
            self.assertTrue((root / 'report/fonts/OFL.txt').is_file())
            self.assertFalse(list((root / 'report/fonts').glob('ARIAL*')))
            with self.assertRaises(ValueError):
                generate_report(root, language='../invalid')

    def test_visum_failure_preserves_previous_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'report').mkdir()
            pdf = root / 'report/report.pdf'; pdf.write_bytes(b'previous PDF')
            with patch('core.app.app_core.report_data.build_report_inputs', return_value={}), \
                 patch('core.app.app_core.report_maps.export_report_maps'), \
                 patch('core.app.app_core.report_visum.export_visum_maps', side_effect=RuntimeError('Visum export failed')):
                with self.assertRaisesRegex(RuntimeError, 'Visum export failed'):
                    generate_report(root, lambda *a: None)
            self.assertEqual(pdf.read_bytes(), b'previous PDF')

    def test_success_replaces_previous_pdf_and_supplies_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'report/fonts').mkdir(parents=True)
            (root / 'report/fonts/ARIAL.TTF').write_bytes(b'legacy font')
            compiler = Mock(return_value=b'%PDF-new')
            with patch('core.app.app_core.report_data.build_report_inputs', return_value={'munincipality':'München'}), \
                 patch('core.app.app_core.report_data.report_blockers', return_value=[]), \
                 patch('core.app.app_core.report_maps.export_report_maps'), \
                 patch.dict('sys.modules', {'typst': types.SimpleNamespace(compile=compiler)}):
                output = generate_report(root, lambda *a: None)
            self.assertEqual(output.read_bytes(), b'%PDF-new')
            self.assertFalse((root / 'report/fonts/ARIAL.TTF').exists())
            arguments = compiler.call_args.kwargs
            self.assertEqual(json.loads(arguments['sys_inputs']['report'])['munincipality'], 'München')
            self.assertEqual(arguments['root'], str(root.resolve()))
            self.assertEqual(arguments['font_paths'], [str(root.resolve() / 'report/fonts')])
            self.assertFalse((root / 'report/report.pending.pdf').exists())


if __name__ == '__main__':
    unittest.main()
