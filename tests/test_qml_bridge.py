import os
from types import SimpleNamespace
import tempfile
import unittest

from core.app.app_core.settings import AppSettings
from core.app.app_core.workflow_state import AppState, StepId
from core.app.app_qml.bridge import QmlAppBridge
from core.app.app_qml.main import import_qml_runtime, qml_module_available
from core.app.app_qml.main import qgis_qml_import_paths


class QmlBridgeTests(unittest.TestCase):
    def test_bridge_exposes_initial_app_state_and_progress(self):
        bridge = QmlAppBridge(
            state=AppState(current_step=StepId.PROJECT_PBF, language="de"),
            settings=AppSettings(language="de"),
            qml_material_available=False,
        )

        self.assertEqual(bridge.language, "de")
        self.assertEqual(bridge.currentStep, 3)
        self.assertEqual(bridge.stepCount, 6)
        self.assertEqual(bridge.progressPercent, 33)
        self.assertTrue(bridge.backEnabled)
        self.assertFalse(bridge.qmlMaterialAvailable)
        self.assertEqual(bridge.mapBridgeStatus, "QtLocation/QML map")

    def test_back_and_next_mutate_only_prototype_navigation_state(self):
        bridge = QmlAppBridge(
            state=AppState(current_step=StepId.PROJECT_PBF),
            settings=AppSettings(language="en"),
        )

        bridge.goBack()
        self.assertEqual(bridge.state.current_step, StepId.SEARCH)
        self.assertTrue(bridge.backEnabled)

        bridge.goNextPrototype()
        self.assertEqual(bridge.state.current_step, StepId.CITY_SELECTION)

    def test_qml_module_availability_checks_qmldir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_dir = os.path.join(temp_dir, "Qcm", "Material")
            os.makedirs(module_dir)
            with open(os.path.join(module_dir, "qmldir"), "w", encoding="utf-8") as qmldir:
                qmldir.write("module Qcm.Material\n")

            self.assertTrue(qml_module_available([temp_dir], "Qcm.Material"))
            self.assertFalse(qml_module_available([temp_dir], "Missing.Module"))

    def test_qgis_windows_prefix_adds_qt6_qml_import_root(self):
        prefix = os.path.join("C:\\", "QGIS", "apps", "qgis")
        expected = os.path.normpath(os.path.join("C:\\", "QGIS", "apps", "qt6", "qml"))
        original_isdir = os.path.isdir
        try:
            os.path.isdir = lambda path: os.path.normpath(path) == expected
            self.assertIn(expected, qgis_qml_import_paths(prefix))
        finally:
            os.path.isdir = original_isdir

    def test_qml_runtime_falls_back_to_pyqt6_qtqml(self):
        q_url = object()
        qml_engine = object()

        def fake_import_module(module_name):
            if module_name == "qgis.PyQt.QtCore":
                return SimpleNamespace(QUrl=q_url)
            if module_name == "qgis.PyQt.QtQml":
                raise ModuleNotFoundError("No module named 'qgis.PyQt.QtQml'")
            if module_name == "PyQt6.QtQml":
                return SimpleNamespace(QQmlApplicationEngine=qml_engine)
            raise AssertionError(f"Unexpected import: {module_name}")

        self.assertEqual(import_qml_runtime(fake_import_module), (q_url, qml_engine))


if __name__ == "__main__":
    unittest.main()
