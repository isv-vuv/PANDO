import json
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace

from core.app.app_core.exports import export_center_point_geojson
from core.app.app_core.processing import (
    PbfPrepareState,
    QgisModelRunConfig,
    build_qgis_model_command,
    formatted_pbf_names,
    inspect_pbf_preparation,
    merge_pbf_files,
    run_qgis_model,
)
import core.app.app_core.processing as processing_core
from core.app.app_core.project import PROJECT_INPUT_DIR


def _shapely_available():
    try:
        import shapely  # noqa: F401
    except ImportError:
        return False
    return True


requires_shapely = unittest.skipUnless(_shapely_available(), "shapely not installed")


class Step4ProcessingCoreTests(unittest.TestCase):
    def test_center_point_export_writes_geojson(self):
        loc = SimpleNamespace(latitude=48.78, longitude=9.18)
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "center.geojson")

            export_center_point_geojson(loc, output_path)

            with open(output_path, "r", encoding="utf-8") as geojson_file:
                data = json.load(geojson_file)
            self.assertEqual(data["type"], "FeatureCollection")
            self.assertEqual(data["features"][0]["geometry"]["type"], "Point")
            self.assertEqual(data["features"][0]["geometry"]["coordinates"], [9.18, 48.78])

    @requires_shapely
    def test_area_export_combines_selected_cells_by_area_type(self):
        from shapely.geometry import Polygon

        from core.app.app_core.exports import export_area_geojson

        grid = [
            {"id": 1, "shapely_poly_wgs84": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])},
            {"id": 2, "shapely_poly_wgs84": Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])},
            {"id": 3, "shapely_poly_wgs84": Polygon([(3, 0), (4, 0), (4, 1), (3, 1)])},
        ]
        selected = {"PA": {1, 2}, "IA1": {3}, "IA2": set()}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "areas.geojson")

            result = export_area_geojson(grid, selected, output_path)

            self.assertEqual(result.exported_areas, ["PA", "IA1"])
            with open(output_path, "r", encoding="utf-8") as geojson_file:
                data = json.load(geojson_file)
            self.assertEqual([f["properties"]["Bereich"] for f in data["features"]], ["PA", "IA1"])

    def test_pbf_status_detects_single_file_as_directly_usable(self):
        loc = SimpleNamespace(raw={"address": {"country_code": "de"}})
        with tempfile.TemporaryDirectory() as project_path:
            input_dir = os.path.join(project_path, PROJECT_INPUT_DIR)
            os.makedirs(input_dir)
            raw_pbf = os.path.join(input_dir, "baden-wuerttemberg-260702.osm.pbf")
            open(raw_pbf, "wb").close()

            status = inspect_pbf_preparation(project_path, loc)

            self.assertEqual(status.state, PbfPrepareState.READY_SINGLE)
            self.assertEqual(status.selected_pbf_path, raw_pbf)
            self.assertTrue(status.is_usable)

    def test_pbf_status_detects_multiple_files_as_needing_merge(self):
        loc = SimpleNamespace(raw={"address": {"country_code": "de"}})
        with tempfile.TemporaryDirectory() as project_path:
            input_dir = os.path.join(project_path, PROJECT_INPUT_DIR)
            os.makedirs(input_dir)
            open(os.path.join(input_dir, "a.osm.pbf"), "wb").close()
            open(os.path.join(input_dir, "b.osm.pbf"), "wb").close()

            status = inspect_pbf_preparation(project_path, loc)

            self.assertEqual(status.state, PbfPrepareState.NEEDS_MERGE)
            self.assertIsNone(status.selected_pbf_path)
            self.assertTrue(status.needs_merge)

    def test_pbf_status_prefers_final_country_file(self):
        loc = SimpleNamespace(raw={"address": {"country_code": "de"}})
        with tempfile.TemporaryDirectory() as project_path:
            input_dir = os.path.join(project_path, PROJECT_INPUT_DIR)
            os.makedirs(input_dir)
            final_pbf = os.path.join(input_dir, "DE.osm.pbf")
            open(final_pbf, "wb").close()
            open(os.path.join(input_dir, "a.osm.pbf"), "wb").close()

            status = inspect_pbf_preparation(project_path, loc)

            self.assertEqual(status.state, PbfPrepareState.READY_FINAL)
            self.assertEqual(status.selected_pbf_path, final_pbf)

    def test_pbf_status_uses_data_path_and_references(self):
        loc = SimpleNamespace(raw={"address": {"country_code": "de"}})
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = os.path.join(temp_dir, "project")
            data_path = os.path.join(temp_dir, "data")
            os.makedirs(project_path)
            os.makedirs(data_path)
            referenced_pbf = os.path.join(data_path, "hamburg-260702.osm.pbf")
            open(referenced_pbf, "wb").close()
            open(os.path.join(data_path, "unrelated.osm.pbf"), "wb").close()

            status = inspect_pbf_preparation(
                project_path,
                loc,
                data_path=data_path,
                pbf_references=[{"filename": "hamburg-260702.osm.pbf", "path": referenced_pbf}],
            )

            self.assertEqual(status.input_dir, data_path)
            self.assertEqual(status.input_files, [referenced_pbf])
            self.assertEqual(status.state, PbfPrepareState.READY_SINGLE)
            self.assertEqual(status.selected_pbf_path, referenced_pbf)

    def test_qgis_model_command_builds_sorted_parameter_args(self):
        command = build_qgis_model_command(
            QgisModelRunConfig(
                model_path="/models/model.py",
                qgis_interpreter="/qgis/python",
                parameters={"OUTPUT": "/tmp/out.gpkg", "INPUT": "/tmp/in.gpkg", "EMPTY": ""},
            )
        )

        self.assertEqual(
            command,
            ["/qgis/python", "/models/model.py", "INPUT=/tmp/in.gpkg", "OUTPUT=/tmp/out.gpkg"],
        )

    def test_qgis_model_command_defaults_to_python3(self):
        command = build_qgis_model_command(QgisModelRunConfig(model_path="/models/model.py", parameters={}))

        self.assertEqual(command, ["python3", "/models/model.py"])

    def test_model3_dispatch_uses_central_processing_adapter(self):
        calls = []
        original_processing_adapter = processing_core.run_qgis_model_processing
        original_subprocess_adapter = processing_core.run_qgis_model_subprocess

        def fake_processing(config, **kwargs):
            calls.append(("processing", config.model_path, kwargs))
            return "processing-result"

        def fake_subprocess(config, **kwargs):
            calls.append(("subprocess", config.model_path, kwargs))
            return "subprocess-result"

        try:
            processing_core.run_qgis_model_processing = fake_processing
            processing_core.run_qgis_model_subprocess = fake_subprocess

            result = run_qgis_model(QgisModelRunConfig(model_path="/models/a.model3", parameters={}))

            self.assertEqual(result, "processing-result")
            self.assertEqual(calls[0][0], "processing")
        finally:
            processing_core.run_qgis_model_processing = original_processing_adapter
            processing_core.run_qgis_model_subprocess = original_subprocess_adapter

    def test_non_model3_dispatch_uses_central_subprocess_adapter(self):
        calls = []
        original_processing_adapter = processing_core.run_qgis_model_processing
        original_subprocess_adapter = processing_core.run_qgis_model_subprocess

        def fake_processing(config, **kwargs):
            calls.append(("processing", config.model_path, kwargs))
            return "processing-result"

        def fake_subprocess(config, **kwargs):
            calls.append(("subprocess", config.model_path, kwargs))
            return "subprocess-result"

        try:
            processing_core.run_qgis_model_processing = fake_processing
            processing_core.run_qgis_model_subprocess = fake_subprocess

            result = run_qgis_model(QgisModelRunConfig(model_path="/models/a.py", parameters={}))

            self.assertEqual(result, "subprocess-result")
            self.assertEqual(calls[0][0], "subprocess")
        finally:
            processing_core.run_qgis_model_processing = original_processing_adapter
            processing_core.run_qgis_model_subprocess = original_subprocess_adapter

    def test_processing_adapter_runs_model3_instance_without_registry_mutation(self):
        calls = []

        class FakeFeedback:
            def pushInfo(self, info):
                calls.append(("info", info))

            def reportError(self, error, fatalError=False):
                calls.append(("error", error, fatalError))

            def setProgressText(self, text):
                calls.append(("progress", text))

            def setProgress(self, progress):
                calls.append(("progress_value", progress))

            def isCanceled(self):
                return False

        class FakeAlgorithm:
            def fromFile(self, model_path):
                calls.append(("from_file", model_path))
                return True

            def id(self):
                return "model:test"

        def fake_processing_run(algorithm, parameters, feedback=None):
            calls.append(("run", algorithm.id(), parameters))
            print("python console output")
            feedback.pushInfo("started")
            feedback.setProgressText("Preparing algorithm: Raster pixels to polygons")
            feedback.setProgress(37.4)
            return {"OUTPUT": "/tmp/out.gpkg"}

        old_modules = {
            name: sys.modules.get(name)
            for name in ("processing", "qgis", "qgis.core")
        }
        processing_module = types.ModuleType("processing")
        processing_module.run = fake_processing_run
        qgis_module = types.ModuleType("qgis")
        qgis_core_module = types.ModuleType("qgis.core")
        qgis_core_module.QgsProcessingFeedback = FakeFeedback
        qgis_core_module.QgsProcessingModelAlgorithm = FakeAlgorithm

        try:
            sys.modules["processing"] = processing_module
            sys.modules["qgis"] = qgis_module
            sys.modules["qgis.core"] = qgis_core_module

            progress = []
            progress_text = []
            logs = []
            result = processing_core.run_qgis_model_processing(
                QgisModelRunConfig(model_path="/models/test.model3", parameters={"INPUT": "/tmp/in.gpkg"}),
                on_progress=progress.append,
                on_progress_text=progress_text.append,
                on_log=logs.append,
            )

            self.assertEqual(result.return_code, 0)
            self.assertIn(("run", "model:test", {"INPUT": "/tmp/in.gpkg"}), calls)
            self.assertEqual(progress, [37, 100])
            self.assertEqual(
                progress_text,
                ["Preparing algorithm: Raster pixels to polygons"],
            )
            self.assertIn("python console output", logs)
            self.assertFalse(any(call[0] in {"add", "remove"} for call in calls))
        finally:
            for name, module in old_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_merge_pbf_reports_missing_osmium_dependency_cleanly(self):
        try:
            import osmium  # noqa: F401
        except ImportError:
            with tempfile.TemporaryDirectory() as temp_dir:
                output_path = os.path.join(temp_dir, "merged.osm.pbf")
                with self.assertRaisesRegex(RuntimeError, "pyosmium/osmium is required"):
                    merge_pbf_files(["/tmp/a.osm.pbf", "/tmp/b.osm.pbf"], output_path)
        else:
            self.skipTest("osmium installed")

    def test_merge_pbf_single_file_copies_directly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            src_path = os.path.join(temp_dir, "input.osm.pbf")
            dest_path = os.path.join(temp_dir, "output.osm.pbf")
            with open(src_path, "wb") as f:
                f.write(b"dummy pbf data")
            logs = []
            res = merge_pbf_files([src_path], dest_path, on_log=logs.append)
            self.assertTrue(os.path.exists(dest_path))
            self.assertEqual(res.count, 1)
            self.assertTrue(any("Einzelne PBF-Datei" in log for log in logs))

    def test_get_dynamic_gdal_cache_mb_returns_at_least_min_cache(self):
        from core.app.app_core.processing import _get_dynamic_gdal_cache_mb
        cache_mb = _get_dynamic_gdal_cache_mb()
        self.assertGreaterEqual(cache_mb, 2048)

    def test_formatted_pbf_names_normalizes_geofabrik_suffixes(self):
        self.assertEqual(
            formatted_pbf_names(["/tmp/berlin-latest.osm.pbf", "/tmp/bayern-260702.osm.pbf"]),
            ["berlin", "bayern"],
        )


if __name__ == "__main__":
    unittest.main()
