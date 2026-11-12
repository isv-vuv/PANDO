"""Native Visum report selection, layer/legend switches, and cleanup."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
from core.app.app_core.report_visum import (FIGURES, configure_network_view, export_visum_maps, find_final_version, resolve_network_gpa, report_frame, report_graphic_parameters, LEGEND_SPACE)


class VisumReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.versions = self.root / 'processed/visum/ver'
        self.versions.mkdir(parents=True)

    def final(self):
        path = self.versions / '07_Test_Model.ver'
        path.write_bytes(b'version')
        return path

    def gpa(self):
        path = self.root / 'processed/visum/gpa/Zentrale_Orte_Luftlinienverbindungen_VFS.gpax'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<graphic-parameters><legendParameters><legendGeneralParameters><labelTextParameters textSize="3.5"/></legendGeneralParameters><legendElementsParameters><legendElementParameter symbolHeight="5" symbolWidth="10"/></legendElementsParameters></legendParameters></graphic-parameters>')
        return path

    def test_frame_reserves_right_side_without_cropping_region(self):
        for region in ((0, 0, 300, 200), (10, 20, 30, 420)):
            xmin, ymin, xmax, ymax = region
            left, bottom, right, top = report_frame(*region)
            self.assertLessEqual(left, xmin)
            self.assertLessEqual(bottom, ymin)
            self.assertGreaterEqual(top, ymax)
            self.assertLessEqual(xmax, right - (right - left) * LEGEND_SPACE + 1e-9)
            self.assertAlmostEqual((right - left) / (top - bottom), 1.5)
            self.assertAlmostEqual(((xmin + xmax) / 2 - left) / (right - left), (1 - LEGEND_SPACE) / 2)

    def test_report_legend_is_smaller_and_source_is_unchanged(self):
        import xml.etree.ElementTree as ET
        source = self.gpa()
        original = source.read_bytes()
        output = report_graphic_parameters(source, self.root / 'report.gpax')
        root = ET.parse(output).getroot()
        self.assertAlmostEqual(float(root.find('.//labelTextParameters').get('textSize')), 2.45)
        self.assertEqual(root.find('.//legendGeneralParameters').get('legendAlignment'), 'TOPRIGHTALIGNMENT')
        self.assertEqual(float(root.find('.//legendElementParameter').get('symbolHeight')), 5)
        self.assertEqual(float(root.find('.//legendElementParameter').get('symbolWidth')), 7)
        self.assertGreaterEqual(float(root.find('.//legendGeneralParameters').get('gap_Symbols')), 2)
        self.assertEqual(source.read_bytes(), original)

    def test_intermediate_versions_do_not_count_as_completed_import(self):
        (self.versions / '06_DirectLineMatrices_Added.ver').touch()
        result = export_visum_maps(self.root, self.root / 'images', {}, MagicMock())
        self.assertEqual(result['status'], 'skipped')
        self.assertIn('Schritt 5', result['message'])
        self.assertEqual(result['figures'], [])
        self.assertFalse((self.root / 'images').exists())

    def test_only_final_model_is_selected(self):
        final = self.final()
        (self.versions / '99_Debug.ver').touch()
        self.assertEqual(find_final_version(self.root), final)

    def test_existing_model_on_non_windows_has_accurate_explanation(self):
        self.final()
        with patch('core.app.app_core.report_visum.platform.system', return_value='Linux'):
            result = export_visum_maps(self.root, self.root / 'images', {}, MagicMock())
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('Windows', result['message'])
        self.assertNotIn('nicht durchgeführt', result['message'])

    def test_project_graphic_parameters_take_precedence(self):
        gpa = self.gpa()
        self.assertEqual(resolve_network_gpa(self.root), gpa)

    def test_desire_lines_and_roads_have_matching_native_legend(self):
        visum = MagicMock()
        entries = {key: MagicMock() for key in ('LINKS', 'ZONES', 'DESIRELINESZONES')}
        visum.Net.LegendParameters.LegendElementsParameters.ItemByKey.side_effect = entries.__getitem__
        for lines in (True, False):
            configure_network_view(visum, desire_lines=lines)
            gp = visum.Net.GraphicParameters
            gp.Links.SetAttValue.assert_called_with('DRAW', not lines)
            gp.DesireLinesZones.SetAttValue.assert_called_with('DRAW', lines)
            gp.DesireLinesMainZones.SetAttValue.assert_called_with('DRAW', False)
            visum.Net.LegendParameters.LegendGeneralParameters.SetAttValue.assert_called_with('DRAWLEGEND', True)
            self.assertEqual(entries['LINKS'].Enabled, not lines)
            self.assertTrue(entries['ZONES'].Enabled)
            self.assertEqual(entries['DESIRELINESZONES'].Enabled, lines)

    def test_exports_network_and_accessibility_views_and_detail_gpa(self):
        final = self.final()
        gpa = self.gpa()
        visum, job, pythoncom = MagicMock(), MagicMock(), MagicMock()
        client = types.ModuleType('win32com.client')
        client.DispatchEx = MagicMock(return_value=visum)
        package = types.ModuleType('win32com'); package.client = client
        states = []
        def export(path, *arguments):
            states.append((visum.Net.GraphicParameters.Links.SetAttValue.call_args.args[1],
                           visum.Net.GraphicParameters.DesireLinesZones.SetAttValue.call_args.args[1]))
            Path(path).write_bytes(b'PNG')
        visum.Graphic.ExportNetworkImageFile.side_effect = export
        extents = {'outer': (0,0,300,200), 'study': (10,10,40,30), 'planning': (20,20,23,22), 'detail': (21,21,22,22)}
        with patch.dict(sys.modules, {'pythoncom':pythoncom, 'win32com':package, 'win32com.client':client}), \
             patch('core.app.app_core.report_visum.platform.system', return_value='Windows'), \
             patch('core.scripts.visum.scripts.visum_com.get_visum_progid', return_value='Visum.Visum.2601'), \
             patch('core.app.app_core.report_visum.own_visum_lifetime', return_value=job), \
             patch('core.app.app_core.report_visum.report_extents', return_value=extents):
            result = export_visum_maps(self.root, self.root / 'images', {}, MagicMock())
        self.assertEqual(result['status'], 'done')
        self.assertEqual(len(result['figures']), 6)
        self.assertEqual(len(result['accessibility_figures']), 2)
        visum.Net.GraphicParameters.Save.assert_called_once_with(str((self.root / 'processed/visum/gpa/_Ausschnitt_Inneroertliche_Intensitaeten.gpa').resolve()))
        self.assertEqual(states[:6], [(False,True),(False,True),(True,False),(True,False),(True,False),(True,False)])
        self.assertEqual([c.args[1:5] for c in visum.Graphic.ExportNetworkImageFile.call_args_list], [extents[k] for k in ('outer','study','outer','study','planning','detail','planning','study')])
        self.assertTrue(all(c.args[5:] == (2400,300,100) for c in visum.Graphic.ExportNetworkImageFile.call_args_list))
        visum.LoadVersion.assert_called_once_with(str(final.resolve()))
        paths = [call.args[0] for call in visum.Net.GraphicParameters.OpenXml.call_args_list]
        self.assertEqual(len(paths), 8)
        self.assertTrue(paths[1].endswith('_report_graphic_parameters_study.gpax'))
        self.assertTrue(all(path.endswith('_report_graphic_parameters.gpax') for path in paths[:1] + paths[2:6]))
        self.assertTrue(all(path.endswith('_report_accessibility.gpax') for path in paths[6:]))
        visum.Graphic.ShowMaximized.assert_called_once()
        visum.Graphic.MaximizeNetWindow.assert_called_once()
        visum.SaveVersion.assert_not_called()
        job.Close.assert_called_once()
        pythoncom.CoUninitialize.assert_called_once()

    def test_outer_views_hide_central_places(self):
        visum = MagicMock()
        entries = {key: MagicMock() for key in ('LINKS', 'ZONES', 'DESIRELINESZONES')}
        visum.Net.LegendParameters.LegendElementsParameters.ItemByKey.side_effect = entries.__getitem__
        configure_network_view(visum, desire_lines=True, central_places=False)
        visum.Net.GraphicParameters.Zones.SetAttValue.assert_called_with('DRAW', False)
        self.assertFalse(visum.Net.LegendParameters.LegendElementsParameters.ItemByKey('ZONES').Enabled)

    def test_desire_line_levels_are_selected_for_each_extent(self):
        import xml.etree.ElementTree as ET
        source = Path('core/scripts/visum/helper_files/gpa/DE_Zentrale_Orte_Luftlinienverbindungen_VFS.gpax')
        original = source.read_bytes()
        for levels in ((0, 1), (1, 2, 3)):
            result = report_graphic_parameters(source, self.root / 'levels.gpax', levels=levels)
            root = ET.parse(result).getroot()
            enabled = [item.find('standardBar').get('scaleAttrID')
                       for item in root.findall('.//desireLinesZones/items/item') if item.get('draw') == 'true']
            self.assertEqual(enabled, [f'MATVALUE({i+1})' for i in levels])
        self.assertEqual(source.read_bytes(), original)

    def test_com_failure_is_not_misreported_as_skipped_import(self):
        self.final(); self.gpa()
        client = types.ModuleType('win32com.client')
        client.DispatchEx = MagicMock(side_effect=RuntimeError('license unavailable'))
        package = types.ModuleType('win32com'); package.client = client
        pythoncom = MagicMock()
        with patch.dict(sys.modules, {'pythoncom':pythoncom, 'win32com':package, 'win32com.client':client}), \
             patch('core.app.app_core.report_visum.platform.system', return_value='Windows'), \
             patch('core.scripts.visum.scripts.visum_com.get_visum_progid', return_value='Visum.Visum.2601'):
            with self.assertRaisesRegex(RuntimeError, 'Visum-Kartenexport fehlgeschlagen.*license unavailable'):
                export_visum_maps(self.root, self.root / 'images', {}, MagicMock())
        pythoncom.CoUninitialize.assert_called_once()


if __name__ == '__main__':
    unittest.main()
