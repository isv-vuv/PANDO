"""Native QGIS coverage for embedded result layers, extents and ownership."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.app.app_qt.qt_base import QGIS_IMPORT_ERROR

_qgis_context = None
_qgis_profile = None


@unittest.skipIf(QGIS_IMPORT_ERROR is not None, "Requires QGIS Python")
class EmbeddedResultsMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _qgis_context, _qgis_profile
        from types import SimpleNamespace
        from qgis.core import QgsApplication
        if _qgis_context is None:
            import atexit
            _qgis_profile = tempfile.TemporaryDirectory()
            QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", ""), True)
            _qgis_context = SimpleNamespace(app=QgsApplication([], True, _qgis_profile.name))
            QgsApplication.initQgis()
            def shutdown():
                QgsApplication.exitQgis()
                _qgis_profile.cleanup()
            atexit.register(shutdown)
        cls.context = _qgis_context

    @classmethod
    def tearDownClass(cls):
        # Native QGIS registries and QApplication live once per test process.
        pass

    def setUp(self):
        from core.app.app_qt.steps.step6_results import Step6ResultsWidget
        from core.locales import localizer
        from qgis.core import QgsProject
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "input").mkdir()
        (self.root / "input" / "local_crs.txt").write_text("EPSG:3857", encoding="utf-8")
        self.points = self._make_points("zone_centroids")
        self.other_points = self._make_points("central_place_points", x=14)
        self.raster = self._make_raster()
        self.global_layers = set(QgsProject.instance().mapLayers())
        self.page = Step6ResultsWidget(localizer, project_path=str(self.root))
        self.page.resize(1400, 850)
        self.page.show()

    def tearDown(self):
        from qgis.core import QgsProject
        from qgis.PyQt import QtCore
        self.page.results_map.clear()
        self.page.deleteLater()
        self.context.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.context.app.processEvents()
        self.assertEqual(set(QgsProject.instance().mapLayers()), self.global_layers)
        self.temporary.cleanup()

    def _make_points(self, name, x=13):
        from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
                               QgsVectorFileWriter, QgsVectorLayer)
        layer = QgsVectorLayer("Point?crs=EPSG:4326&field=id:integer", name, "memory")
        feature = QgsFeature(layer.fields())
        feature.setAttributes([1])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, 52)))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        path = self.root / "processed" / "qgis_output" / "model6_ZoneAssembler" / (name + ".gpkg")
        path.parent.mkdir(parents=True, exist_ok=True)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(path), QgsProject.instance().transformContext(), options)
        self.assertEqual(result[0], QgsVectorFileWriter.WriterError.NoError, result)
        layer.renderer().symbol().setSize(7.0)
        layer.saveNamedStyle(str(path.with_suffix(".qml")))
        return path

    def _make_raster(self):
        from osgeo import gdal, osr
        path = self.root / "population.tif"
        gdal.UseExceptions()
        dataset = gdal.GetDriverByName("GTiff").Create(str(path), 2, 2, 1, gdal.GDT_Byte)
        dataset.SetGeoTransform([1440000, 1000, 0, 6810000, 0, -1000])
        crs = osr.SpatialReference()
        crs.ImportFromEPSG(3857)
        dataset.SetProjection(crs.ExportToWkt())
        dataset.GetRasterBand(1).Fill(100)
        dataset = None
        return path

    def _hide_background(self):
        preview = self.page.results_map
        for node in preview.project.layerTreeRoot().findLayers():
            if node.layer().providerType() == "wms":
                node.setItemVisibilityChecked(False)

    def test_mixed_crs_styles_visibility_and_group_switch(self):
        preview = self.page.results_map
        with mock.patch("core.app.app_qt.steps.step6_results.subprocess.Popen") as popen:
            self.page._show_layers_in_map([self.points, self.raster], "Mixed results")
            self._hide_background()
            self.assertEqual(preview.heading.text(), "Mixed results")
            self.assertEqual(preview.canvas.mapSettings().destinationCrs().authid(), "EPSG:3857")
            self.assertEqual(len(preview.canvas.layers()), 2)
            nodes = [n for n in preview.project.layerTreeRoot().findLayers() if n.layer().providerType() != "wms"]
            self.assertEqual(nodes[0].layer().name(), "zone_centroids")
            self.assertAlmostEqual(nodes[0].layer().renderer().symbol().size(), 7.0)
            self.assertGreater(preview._extent.xMinimum(), 1000000)
            self.assertLess(preview._extent.xMaximum(), 2000000)
            nodes[0].setItemVisibilityChecked(False)
            self.assertEqual(len(preview.canvas.layers()), 1)
            nodes[0].setItemVisibilityChecked(True)
            self.assertEqual(len(preview.canvas.layers()), 2)
            self.page._show_layers_in_map([self.other_points], "Other group")
            self._hide_background()
            self.assertEqual([layer.name() for layer in preview.canvas.layers()], ["central_place_points"])
            self.assertFalse(preview._extent.isEmpty())
            popen.assert_not_called()

    def test_card_preview_and_external_qgis_are_separate_actions(self):
        from core.app.app_qt.steps.step6_results import ResultCardWidget
        card = next(card for card in self.page.findChildren(ResultCardWidget) if str(self.points) in card.layer_paths)
        self.assertTrue(card.btn_preview.isEnabled())
        with mock.patch("core.app.app_qt.steps.step6_results.open_in_qgis") as external:
            card.btn_preview.click()
            self._hide_background()
            self.assertEqual(self.page.results_map.heading.text(), card.title)
            external.assert_not_called()
            card.btn_qgis.click()
            external.assert_called_once()

    def test_all_layers_and_project_change_release_files(self):
        self.page.btn_show_all_map.click()
        self._hide_background()
        self.assertIn("zone_centroids", [layer.name() for layer in self.page.results_map.canvas.layers()])
        empty = self.root / "empty_project"
        empty.mkdir()
        self.page.set_project_path(str(empty))
        self.assertEqual(self.page.results_map.canvas.layers(), [])
        self.assertEqual(self.page.results_map.project.mapLayers(), {})
        self.assertFalse(self.page.btn_show_all_map.isEnabled())
        self.points.unlink()  # Windows would reject this if the GPKG provider was still open.

    def test_release_while_rendering_and_reload(self):
        self.page._show_layers_in_map([self.points, self.raster], "Rendering")
        self._hide_background()
        self.context.app.processEvents()
        self.page.results_map.canvas.refresh()
        self.context.app.processEvents()
        self.page.release_resources()
        self.assertFalse(self.page.results_map.canvas.isDrawing())
        self.assertEqual(self.page.results_map.project.mapLayers(), {})
        self.points.unlink()
        self.page._show_layers_in_map([self.other_points], "Reloaded")
        self._hide_background()
        self.assertEqual(self.page.results_map.heading.text(), "Reloaded")

    def test_bad_project_and_missing_layers_show_error(self):
        preview = self.page.results_map
        self.assertFalse(preview.show_project(self.root / "missing.qgs", "Missing"))
        self.assertFalse(preview.zoom_results_button.isEnabled())
        self.page._show_layers_in_map([self.root / "missing.gpkg"], "Missing")
        self.assertEqual(preview.canvas.layers(), [])
        self.assertEqual(preview.status.text(), self.page.localizer.get_string("step6_map_error"))

    def test_opening_results_shows_pa_basemap_without_restoring_results(self):
        from core.app.app_qt.steps.step6_results import Step6ResultsWidget
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsVectorFileWriter, QgsProject
        from qgis.PyQt import QtWidgets, QtCore
        layer = QgsVectorLayer('Polygon?crs=EPSG:3857&field=ZoneType:string', 'Selected areas', 'memory')
        for zone, wkt in (
            ('PA', 'POLYGON ((100 100, 200 100, 200 200, 100 200, 100 100))'),
            ('IA2', 'POLYGON ((10000 10000, 20000 10000, 20000 20000, 10000 20000, 10000 10000))'),
        ):
            feature = QgsFeature(layer.fields())
            feature.setAttributes([zone])
            feature.setGeometry(QgsGeometry.fromWkt(wkt))
            layer.dataProvider().addFeatures([feature])
        path = self.root / 'input/zone_type_selected.gpkg'
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'GPKG'
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(path), QgsProject.instance().transformContext(), options)
        self.assertEqual(result[0], QgsVectorFileWriter.NoError, result)
        owner = QtWidgets.QWidget()
        owner._centrality_edit_session = self.page.edit_session
        self.page.edit_session.last_preview = ([self.points], 'Previous results')
        reopened = Step6ResultsWidget(self.page.localizer, owner, project_path=str(self.root))
        try:
            preview = reopened.results_map
            self.assertTrue(preview._planning_view)
            self.assertAlmostEqual(preview._extent.center().x(), 150)
            self.assertLess(preview._extent.width(), 1000)
            self.assertEqual(len(preview.canvas.layers()), 1)
            self.assertTrue(all(item.providerType() == 'wms' for item in preview.project.mapLayers().values()))
            self.assertEqual(preview.target_layer.count(), 0)
            self.assertTrue(preview.map_controls.isEnabled())
            self.assertEqual(preview.zoom_results_button.text(), self.page.localizer.get_string('step6_map_zoom_planning'))
        finally:
            reopened.release_resources()
            owner.deleteLater()
            self.context.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
