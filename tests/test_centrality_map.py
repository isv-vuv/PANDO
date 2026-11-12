"""Real GeoPackage/QGIS integration for centre edits and map controls."""
from pathlib import Path
import unittest

from tests import test_results_map


class CentralityMapTests(test_results_map.EmbeddedResultsMapTests):
    def make_layer(self, name, kind, features, folder='model2_ZoneClass', type_field='integer'):
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsVectorFileWriter, QgsProject
        layer = QgsVectorLayer(kind + '?crs=EPSG:3857&field=ZONENO:integer&field=TypeNo:' + type_field + '&field=XCoord:double&field=YCoord:double&field=name:string', name, 'memory')
        for attrs, wkt in features:
            feature = QgsFeature(layer.fields())
            feature.setAttributes(attrs)
            feature.setGeometry(QgsGeometry.fromWkt(wkt))
            layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        path = self.root / 'processed/qgis_output' / folder / (name + '.gpkg')
        path.parent.mkdir(parents=True, exist_ok=True)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'GPKG'
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(path), QgsProject.instance().transformContext(), options)
        self.assertEqual(result[0], QgsVectorFileWriter.NoError, result)
        return path

    def prepare_editor(self):
        self.zone_file = self.make_layer('zone_adm2_typeno', 'Polygon', [
            ([10, 2, 25., 25., 'West'], 'POLYGON ((0 0, 100 0, 100 100, 0 100, 0 0))'),
            ([20, 3, 125., 25., 'East'], 'POLYGON ((100 0, 200 0, 200 100, 100 100, 100 0))'),
        ])
        self.centre_file = self.make_layer('central_place_points', 'Point', [
            ([10, 2, 25., 25., 'West'], 'POINT (25 25)'),
            ([20, 3, 125., 25., 'East'], 'POINT (125 25)'),
        ])
        self.page._show_layers_in_map([self.zone_file, self.centre_file], 'Editable centres')
        self._hide_background()
        self.assertIsNotNone(self.page.results_map.editor)
        return self.page.results_map.editor

    def test_change_move_replace_delete_undo_redo_and_banner(self):
        from qgis.core import QgsPointXY
        editor = self.prepare_editor()
        session = self.page.edit_session
        before = editor.points.rows()[0]
        session.execute(editor.command('level', row=before, level=0))
        self.assertEqual(editor.zones.rows()[0]['TypeNo'], 0)
        self.page.results_map._reload_edits()
        self.assertTrue(self.page.stale_banner.isVisible())
        row = editor.points.rows()[0]
        target = editor.zones_at(QgsPointXY(150, 50), editor.layer.crs())[0]
        replaced = editor.occupied(target, row[editor.points.key])
        self.assertEqual(len(replaced), 1)
        session.execute(editor.command('move', row=row, point=QgsPointXY(150, 50), canvas_crs=editor.layer.crs(), zone=target, replaced=replaced))
        self.assertEqual(len(editor.points.rows()), 1)
        self.assertEqual(editor.points.rows()[0]['ZONENO'], 20)
        self.assertEqual(editor.zones.rows()[0]['TypeNo'], 9)
        self.assertIsNone(editor.zones.rows()[0]['XCoord'])
        session.undo()
        self.assertEqual(len(editor.points.rows()), 2)
        session.redo()
        row = editor.points.rows()[0]
        session.execute(editor.command('delete', row=row))
        self.assertEqual(editor.points.rows(), [])
        self.page.results_map._reload_edits()
        self.assertIsNotNone(self.page.results_map.editor)
        self.assertTrue(self.page.results_map.edit_buttons['add'].isEnabled())
        session.undo()
        self.assertEqual(editor.points.rows(), [row])

    def test_add_transforms_crs_and_spatial_index_then_undo(self):
        from qgis.core import QgsPointXY, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsVectorLayer, QgsFeatureRequest, QgsRectangle
        from core.app.app_core.centrality_edits import Change, Command
        editor = self.prepare_editor()
        session = self.page.edit_session
        for row in editor.points.rows():
            session.execute(editor.command('delete', row=row))
        crs = QgsCoordinateReferenceSystem('EPSG:4326')
        location = QgsCoordinateTransform(editor.layer.crs(), crs, QgsProject.instance()).transform(QgsPointXY(50, 50))
        zone = editor.zones_at(location, crs)[0]
        session.execute(editor.command('add', point=location, canvas_crs=crs, level=1, zone=zone))
        added = editor.points.rows()[0]
        self.assertAlmostEqual(added['XCoord'], 50, places=5)
        check = QgsVectorLayer(str(self.centre_file), 'check', 'ogr')
        self.assertEqual(len(list(check.getFeatures(QgsFeatureRequest().setFilterRect(QgsRectangle(49, 49, 51, 51))))), 1)
        del check
        session.undo()
        self.assertEqual(editor.points.rows(), [])

    def test_navigation_crs_layer_visibility_and_preparation(self):
        from core.app.app_qt.steps.step5_visum import Step5VisumWidget
        editor = self.prepare_editor()
        preview = self.page.results_map
        preview.map_controls.btn_pan.click()
        self.assertIs(preview.canvas.mapTool(), preview.pan_tool)
        preview.map_controls.btn_measure.click()
        self.assertIs(preview.canvas.mapTool(), preview.measure_tool)
        preview._crs('mercator')
        self.assertEqual(preview.canvas.mapSettings().destinationCrs().authid(), 'EPSG:3857')
        original = editor.points.rows()
        preview._crs('utm')
        self.assertEqual(preview.canvas.mapSettings().destinationCrs().authid(), 'EPSG:32631')
        self.assertEqual(editor.points.rows(), original)
        for button in list(preview.edit_buttons.values()) + preview.history_buttons:
            self.assertFalse(button.icon().isNull())
        self.page.edit_session.execute(editor.command('level', row=editor.points.rows()[0], level=1))
        preview._reload_edits()
        step = Step5VisumWidget(self.page.localizer, project_path=str(self.root))
        step.prepare_centrality_update()
        self.assertTrue(step.selective_update)
        self.assertFalse(step.step_combo.isEnabled())
        self.assertIn('Zonenassemblierung', step.update_summary.text())
        step.deleteLater()
        node = preview.project.layerTreeRoot().findLayer(editor.layer.id())
        node.setItemVisibilityChecked(False)
        self.assertIsNone(preview.editor)
        self.assertFalse(preview.edit_buttons['add'].isEnabled())

    def test_click_popup_saves_immediately_and_escape_cancels_move(self):
        from unittest.mock import patch
        from qgis.PyQt import QtCore, QtGui
        from qgis.core import QgsPointXY
        editor = self.prepare_editor()
        preview = self.page.results_map
        preview._edit_mode('level')
        location = QgsPointXY(25, 25)
        screen = preview.canvas.getCoordinateTransform().transform(location)
        with patch('core.app.app_qt.results_map.QtWidgets.QInputDialog.getItem', return_value=(self.page.localizer.get_string('centres_level_0'), True)):
            preview.edit_click(preview.edit_tool, location, screen)
        self.assertEqual(editor.points.rows()[0]['TypeNo'], 0)
        self.assertEqual(editor.zones.rows()[0]['TypeNo'], 0)
        preview._edit_mode('move')
        self.context.app.processEvents()
        screen = preview.canvas.getCoordinateTransform().transform(location)
        preview.edit_click(preview.edit_tool, location, QgsPointXY(screen.x() + 15, screen.y()))
        self.assertIsNotNone(preview.edit_tool.selected)
        event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Escape, QtCore.Qt.KeyboardModifier.NoModifier)
        preview.edit_tool.keyPressEvent(event)
        self.assertIsNone(preview.edit_tool.selected)
        self.assertEqual(self.page.edit_session.position, 1)

    def test_selective_assembly_preserves_manual_levels_and_skips_generators(self):
        from threading import Event
        from unittest.mock import patch
        from qgis.core import QgsVectorLayer
        from core.app.app_core.centrality_edits import Table
        from core.app.app_qt.centrality_editor import CentralityEditor
        from core.app.app_core.centrality_update import refresh_assembly
        editor = self.prepare_editor()
        folder = 'model6_ZoneAssembler'
        zones = self.make_layer('zones', 'Polygon', [
            ([10, '2', 25., 25., 'West'], 'POLYGON ((0 0, 100 0, 100 100, 0 100, 0 0))'),
            ([20, '3', 125., 25., 'East'], 'POLYGON ((100 0, 200 0, 200 100, 100 100, 100 0))'),
        ], folder, 'string')
        points = self.make_layer('zone_centroids', 'Point', [
            ([10, '2', 25., 25., 'West'], 'POINT (25 25)'),
            ([20, '3', 125., 25., 'East'], 'POINT (125 25)'),
        ], folder, 'string')
        session = self.page.edit_session
        original_geometry = Table.open(zones).rows()[0]['geom']
        session.execute(editor.command('level', row=editor.points.rows()[0], level=1))
        with patch('core.app.app_core.model_pipeline.ModelPipeline.run', side_effect=AssertionError('must not run centrality generators')):
            refresh_assembly(session, lambda _: None, Event())
        self.assertEqual(Table.open(zones).rows()[0]['TypeNo'], '1')
        self.assertEqual(Table.open(points).rows()[0]['TypeNo'], '1')
        self.assertEqual(Table.open(zones).rows()[0]['geom'], original_geometry)
        self.assertEqual(Table.open(zones).rows()[1]['TypeNo'], '3')
        layer = QgsVectorLayer(str(points), 'Final centres', 'ogr')
        final = CentralityEditor(self.root, layer)
        session.execute(final.command('level', row=final.points.rows()[0], level=0))
        session.undo()  # Text field affinity must not cause a false external conflict.
        self.assertEqual(final.points.rows()[0]['TypeNo'], '1')
        session.redo()
        session.execute(editor.command('level', row=editor.points.rows()[0], level=3))
        refresh_assembly(session, lambda _: None, Event())
        self.assertEqual(final.points.rows()[0]['TypeNo'], '0')
        self.assertEqual(final.zones.rows()[0]['TypeNo'], '0')
        del final, layer


if __name__ == '__main__':
    unittest.main()
