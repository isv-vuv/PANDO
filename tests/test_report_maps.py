"""Shared report detail viewport selection in the project CRS."""
import unittest
from unittest.mock import patch
from core.app.app_qt.qt_base import QGIS_IMPORT_ERROR


@unittest.skipIf(QGIS_IMPORT_ERROR is not None, 'Requires QGIS Python')
class DetailViewportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.core import QgsApplication
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def test_top_three_urban_centres_inside_planning_area_define_extent(self):
        from qgis.core import (QgsProject, QgsCoordinateReferenceSystem, QgsVectorLayer,
                               QgsFeature, QgsGeometry, QgsPointXY, QgsRectangle)
        from core.app.app_core.report_maps import detail_geometry
        project = QgsProject()
        crs = QgsCoordinateReferenceSystem('EPSG:3857')
        project.setCrs(crs)
        layer = QgsVectorLayer('Point?crs=EPSG:3857&field=TypeNo:integer&field=Total_Intensity:double', 'centres', 'memory')
        for x, y, kind, intensity in [(1000,1000,3,30), (2000,1000,4,20),
                                      (3000,1000,3,10), (9000,1000,4,1),
                                      (8000,1000,9,100), (12000,1000,3,200)]:
            feature = QgsFeature(layer.fields())
            feature.setAttributes([kind, intensity])
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x,y)))
            layer.dataProvider().addFeatures([feature])
        planning = QgsGeometry.fromRect(QgsRectangle(0,0,10000,2000))
        with patch('core.app.app_core.report_maps.load_result_layer', return_value=layer):
            bounds = detail_geometry('.', project, crs, planning).boundingBox()
        for x in (1000,2000,3000):
            self.assertTrue(bounds.contains(QgsPointXY(x,1000)))
        self.assertLess(bounds.xMaximum(), 9000)
        self.assertAlmostEqual(bounds.width()/bounds.height(), 1.5)
        self.assertAlmostEqual(bounds.width(), 2400)

    def test_empty_selection_has_explicit_error(self):
        from qgis.core import QgsProject, QgsCoordinateReferenceSystem, QgsVectorLayer, QgsGeometry, QgsRectangle
        from core.app.app_core.report_maps import detail_geometry
        project = QgsProject()
        crs = QgsCoordinateReferenceSystem('EPSG:3857')
        layer = QgsVectorLayer('Point?crs=EPSG:3857&field=TypeNo:integer&field=Total_Intensity:double', 'empty', 'memory')
        with patch('core.app.app_core.report_maps.load_result_layer', return_value=layer):
            with self.assertRaisesRegex(ValueError, 'Keine innerörtlichen'):
                detail_geometry('.', project, crs, QgsGeometry.fromRect(QgsRectangle(0,0,10,10)))

    def test_population_uses_viridis_even_with_legacy_sidecar(self):
        import tempfile
        from pathlib import Path
        from osgeo import gdal, osr
        from core.app.app_core.result_maps import load_result_layer
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pop_raster_corr.tif'
            dataset = gdal.GetDriverByName('GTiff').Create(str(path), 2, 2, 1, gdal.GDT_Byte)
            dataset.SetGeoTransform([0,100,0,200,0,-100])
            crs = osr.SpatialReference()
            crs.ImportFromEPSG(3857)
            dataset.SetProjection(crs.ExportToWkt())
            dataset.GetRasterBand(1).Fill(20)
            dataset = None
            style = Path('core/scripts/qgis/styles/population_raster.qml').read_text()
            path.with_suffix('.qml').write_text(style.replace('#440154','#ff0000').replace('#fde725','#ff0000'))
            layer = load_result_layer(path)
            items = layer.renderer().shader().rasterShaderFunction().colorRampItemList()
            self.assertEqual(items[1].color.name(), '#440154')
            self.assertEqual(items[-1].color.name(), '#fde725')
            self.assertTrue(all('Einw./ha' not in item.label for item in items))
            layer = None

    def test_population_range_excludes_outer_area_zero_and_nodata(self):
        import tempfile
        import numpy as np
        from pathlib import Path
        from osgeo import gdal, osr
        from qgis.core import QgsProject, QgsCoordinateReferenceSystem, QgsGeometry, QgsRectangle
        from core.app.app_core.result_maps import load_result_layer
        from core.app.app_core.report_maps import population_report_range
        gdal.UseExceptions()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pop_raster_corr.tif'
            dataset = gdal.GetDriverByName('GTiff').Create(str(path), 4, 2, 1, gdal.GDT_Float32)
            dataset.SetGeoTransform([0,100,0,200,0,-100])
            spatial = osr.SpatialReference()
            spatial.ImportFromEPSG(3857)
            dataset.SetProjection(spatial.ExportToWkt())
            band = dataset.GetRasterBand(1)
            band.SetNoDataValue(-9999)
            band.WriteArray(np.array([[10,20,9999,9999],[0,-9999,9999,9999]], dtype=np.float32))
            band = None
            dataset = None
            layer = load_result_layer(path)
            project = QgsProject()
            crs = QgsCoordinateReferenceSystem('EPSG:3857')
            project.setCrs(crs)
            limits = population_report_range(layer, QgsGeometry.fromRect(QgsRectangle(0,0,200,200)), crs, project)
            self.assertEqual(limits, (10,20))
            renderer = layer.renderer()
            self.assertEqual(renderer.classificationMin(),10)
            self.assertEqual(renderer.classificationMax(),20)
            items = renderer.shader().rasterShaderFunction().colorRampItemList()
            self.assertEqual(items[0].color.alpha(),0)
            self.assertEqual(items[1].value,10)
            self.assertEqual(items[-1].value,20)
            self.assertEqual(items[-1].color.name(),'#fde725')
            layer = None

    def test_urban_category_nine_is_invisible_with_legacy_style(self):
        import tempfile
        from pathlib import Path
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsVectorFileWriter, QgsProject
        from core.app.app_core.result_maps import load_result_layer
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'zone_pa_ia1_ia2.gpkg'
            layer = QgsVectorLayer('Polygon?crs=EPSG:3857&field=TypeNo:integer', 'cells', 'memory')
            feature = QgsFeature(layer.fields())
            feature.setAttributes([9])
            feature.setGeometry(QgsGeometry.fromWkt('POLYGON((0 0,100 0,100 100,0 100,0 0))'))
            layer.dataProvider().addFeatures([feature])
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(path), QgsProject.instance().transformContext(), options)
            style = Path('core/scripts/qgis/styles/centrality_polygons.qml').read_text()
            path.with_suffix('.qml').write_text(style.replace('name="5" alpha="0"','name="5" alpha="0.65"'))
            layer = load_result_layer(path)
            symbols = {str(category.value()): category.symbol() for category in layer.renderer().categories()}
            self.assertEqual(symbols['9'].opacity(),0)
            self.assertGreater(symbols['3'].opacity(),0)
            layer = None

    def test_legend_is_measured_from_text_after_moving_onto_page(self):
        from qgis.core import (QgsProject, QgsPrintLayout, QgsLayoutItemLegend,
                               QgsLayoutExporter, QgsLayoutPoint, QgsLayoutSize, QgsVectorLayer)
        from core.app.app_core.report_maps import measure_side_legend
        project = QgsProject()
        layer = QgsVectorLayer('Point?crs=EPSG:3857', 'A', 'memory')
        project.addMapLayer(layer, False)
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        legend = QgsLayoutItemLegend(layout)
        layout.addLayoutItem(legend)
        legend.setAutoUpdateModel(False)
        node = legend.model().rootGroup().addLayer(layer)
        legend.attemptMove(QgsLayoutPoint(5,500))
        legend.attemptResize(QgsLayoutSize(180,50))
        exporter = QgsLayoutExporter(layout)
        short_width, _ = measure_side_legend(legend, exporter)
        node.setName('Legendentext mit deutlich mehr Platzbedarf')
        legend.attemptMove(QgsLayoutPoint(5,500))
        long_width, _ = measure_side_legend(legend, exporter)
        self.assertGreater(short_width,10)
        self.assertLess(short_width,60)
        self.assertGreater(long_width,short_width)



class ReportLayoutTests(unittest.TestCase):
    def test_landscape_map_uses_full_width_with_its_geographic_aspect(self):
        from core.app.app_core.report_maps import report_map_dimensions
        width, height = report_map_dimensions(300,200,200)
        self.assertEqual(width,180)
        self.assertEqual(height,120)

    def test_portrait_map_preserves_full_extent_with_a_side_legend(self):
        from core.app.app_core.report_maps import report_map_dimensions
        width, height = report_map_dimensions(100,160,200,legend_width=65)
        self.assertEqual(width,110)
        self.assertAlmostEqual(height,176)
        self.assertAlmostEqual(width/height,100/160)

    def test_height_limit_does_not_crop_tall_regions(self):
        from core.app.app_core.report_maps import report_map_dimensions, MAX_FIGURE_HEIGHT_MM
        width, height = report_map_dimensions(100,400,200,legend_width=65)
        self.assertEqual(height,200)
        self.assertAlmostEqual(width/height,100/400)
        self.assertAlmostEqual(MAX_FIGURE_HEIGHT_MM,237.6)

    def test_narrower_legend_gives_its_saved_width_to_the_map(self):
        from core.app.app_core.report_maps import report_map_dimensions
        narrow = report_map_dimensions(100,120,220,legend_width=35)
        wide = report_map_dimensions(100,120,220,legend_width=65)
        self.assertEqual(narrow[0],140)
        self.assertEqual(wide[0],110)
        self.assertAlmostEqual(narrow[0] + 5 + 35,180)
        self.assertGreater(narrow[1],wide[1])
