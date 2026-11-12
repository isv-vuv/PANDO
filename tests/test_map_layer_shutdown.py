"""Exercise native raster ownership in a subprocess: a double free kills Python."""

import os
from pathlib import Path
import subprocess
import sys
import unittest

from core.app.app_qt.qt_base import QGIS_IMPORT_ERROR


@unittest.skipIf(QGIS_IMPORT_ERROR is not None, "Requires QGIS Python")
class MapLayerShutdownTests(unittest.TestCase):
    def test_raster_layers_can_be_removed_and_qgis_shutdown(self):
        for factory in ("create_high_quality_osm_layer", "create_satellite_layer"):
            with self.subTest(factory=factory):
                result = subprocess.run(
                    [sys.executable, "-c", f"""
from core.app.app_qt.qt_base import ensure_qgis_application
from core.app.app_qt.map_preview import {factory}
from qgis.core import QgsProject

context = ensure_qgis_application([])
for index in range(3):
    layer = {factory}()
    assert layer.isValid(), 'Raster provider failed to initialize'
    resampling = layer.resampleFilter()
    assert resampling.zoomedInResampler() is not resampling.zoomedOutResampler()
    QgsProject.instance().addMapLayer(layer)
    QgsProject.instance().removeAllMapLayers()
layer = {factory}()
QgsProject.instance().addMapLayer(layer)
context.shutdown()
print('clean shutdown')
"""],
                    cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("clean shutdown", result.stdout)
