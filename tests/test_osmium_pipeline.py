import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from core.app.app_core.osmium import (
    OsmiumCancelledError,
    OsmiumRuntime,
    normalize_architecture,
    normalize_platform,
    resolve_osmium,
    run_osmium,
)
from core.app.app_core.osm_pipeline import (
    OsmPhaseAConfig,
    OsmPhaseCConfig,
    OsmPipeline,
    canonical_osm_outputs,
)
try:
    from core.scripts.osm.export_network import calculate_dynamic_brackets, parse_net_link_types
except ImportError:
    calculate_dynamic_brackets, parse_net_link_types = None, None


class OsmiumResolverTests(unittest.TestCase):
    def test_platform_and_architecture_aliases(self):
        self.assertEqual(normalize_platform("Darwin"), "macos")
        self.assertEqual(normalize_platform("Windows"), "windows")
        self.assertEqual(normalize_architecture("AMD64"), "x86_64")
        self.assertEqual(normalize_architecture("aarch64"), "arm64")

    def test_explicit_executable_is_validated_with_required_commands(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "custom osmium"
            executable.touch(mode=0o755)

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                stdout = "osmium version 1.18.0" if command[-1] == "--version" else "help"
                return subprocess.CompletedProcess(command, 0, stdout, "")

            runtime = resolve_osmium(
                executable,
                tool_root=temp_dir,
                system="Linux",
                machine="x86_64",
                run=fake_run,
                which=lambda _name: None,
            )

        self.assertEqual(runtime.version, "1.18.0")
        self.assertFalse(runtime.bundled)
        self.assertEqual(
            [call[0][-1] for call in calls[1:]],
            ["cat", "merge", "time-filter", "extract", "tags-filter"],
        )
        self.assertTrue(all("creationflags" not in kwargs for _, kwargs in calls))

    def test_bundled_runtime_precedes_path_and_gets_library_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled = Path(temp_dir) / "core/scripts/osmium/osmium"
            bundled.parent.mkdir(parents=True)
            bundled.touch(mode=0o755)
            (bundled.parent / "lib").mkdir()

            def fake_run(command, **kwargs):
                stdout = "osmium version 1.18.0" if command[-1] == "--version" else ""
                return subprocess.CompletedProcess(command, 0, stdout, "")

            runtime = resolve_osmium(
                tool_root=temp_dir,
                system="Darwin",
                machine="arm64",
                run=fake_run,
                which=lambda _name: "/usr/local/bin/osmium",
            )

        self.assertTrue(runtime.bundled)
        self.assertEqual(runtime.executable, bundled.resolve())
        self.assertIn("DYLD_LIBRARY_PATH", runtime.environment)

    def test_windows_validation_alone_receives_creationflags(self):
        seen_kwargs = []
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "osmium.exe"
            executable.touch()

            def fake_run(command, **kwargs):
                seen_kwargs.append(kwargs)
                stdout = "osmium version 1.18.0" if command[-1] == "--version" else ""
                return subprocess.CompletedProcess(command, 0, stdout, "")

            resolve_osmium(
                executable,
                tool_root=temp_dir,
                system="Windows",
                machine="AMD64",
                run=fake_run,
                which=lambda _name: None,
            )

        self.assertTrue(all(kwargs.get("creationflags") for kwargs in seen_kwargs))

    def test_master_network_analysis_does_not_require_arrow_regex_compute(self):
        if calculate_dynamic_brackets is None:
            self.skipTest("pandas required for network analysis test")
        master_network = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "scripts"
            / "visum"
            / "helper_files"
            / "master_linktypes.net"
        )

        brackets = calculate_dynamic_brackets(parse_net_link_types(master_network))

        self.assertIn("Motorway", brackets)


class FakeProcess:
    def __init__(self, running=False):
        self.returncode = None if running else 0
        self.running = running
        self.terminated = False

    def poll(self):
        return None if self.running and not self.terminated else self.returncode or 0

    def communicate(self, timeout=None):
        self.returncode = -15 if self.terminated else 0
        return ("out\n", "err\n")

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class OsmiumRunnerTests(unittest.TestCase):
    def _runtime(self, os_name):
        return OsmiumRuntime(
            executable=Path("/runtime/osmium.exe" if os_name == "windows" else "/runtime/osmium"),
            version="1.18.0",
            platform=os_name,
            architecture="x86_64",
            bundled=True,
            environment={"PATH": "/runtime"},
        )

    def test_posix_process_has_no_windows_creationflags(self):
        captured = {}

        def fake_popen(command, **kwargs):
            captured.update(kwargs)
            return FakeProcess()

        run_osmium(self._runtime("linux"), ["cat", "input with spaces.pbf"], popen=fake_popen)
        self.assertNotIn("creationflags", captured)
        self.assertFalse(captured["shell"])

    def test_cancel_terminates_running_process(self):
        process = FakeProcess(running=True)
        stop_event = threading.Event()
        stop_event.set()
        with self.assertRaises(OsmiumCancelledError):
            run_osmium(
                self._runtime("linux"),
                ["merge"],
                stop_event=stop_event,
                popen=lambda *_args, **_kwargs: process,
                poll_interval=0,
            )
        self.assertTrue(process.terminated)

    def test_windows_extract_uses_relative_polygon_path(self):
        captured = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            working_directory = Path(temp_dir)
            polygon = working_directory / "bounds" / "bound_pa.poly"
            polygon.parent.mkdir()
            polygon.touch()

            def fake_popen(command, **kwargs):
                captured["command"] = command
                captured["kwargs"] = kwargs
                return FakeProcess()

            run_osmium(
                self._runtime("windows"),
                ["extract", "-p", polygon, "input.osm.pbf"],
                cwd=working_directory,
                popen=fake_popen,
            )

        polygon_argument = captured["command"][3]
        self.assertFalse(Path(polygon_argument).is_absolute())
        self.assertEqual(
            (Path(captured["kwargs"]["cwd"]) / polygon_argument).resolve(),
            polygon.resolve(),
        )


class OsmPipelineTests(unittest.TestCase):
    def _runtime(self):
        return OsmiumRuntime(
            executable=Path("/mock/osmium"),
            version="1.18.0",
            platform="linux",
            architecture="x86_64",
            bundled=False,
            environment={},
        )

    def test_phase_a_uses_pa_poly_not_bbox_and_stable_outputs(self):
        commands = []
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            source = Path(temp_dir) / "source.osm.pbf"
            pa_poly = Path(temp_dir) / "pa.poly"
            source.touch()
            pa_poly.touch()

            def fake_osmium(_runtime, arguments, **_kwargs):
                commands.append([str(arg) for arg in arguments])
                output = Path(arguments[arguments.index("-o") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.touch()
                return subprocess.CompletedProcess(arguments, 0, "", "")

            def fake_cities(*_args, output_path, **_kwargs):
                Path(output_path).touch()
                return output_path

            def fake_uninhabited(*_args, output_path, **_kwargs):
                Path(output_path).touch()
                return output_path

            with patch("core.app.app_core.osm_pipeline.run_osmium", fake_osmium):
                result = OsmPipeline(self._runtime()).run_phase_a(
                    OsmPhaseAConfig(project, [source], pa_poly),
                    export_cities_fn=fake_cities,
                    export_uninhabited_fn=fake_uninhabited,
                )

            expected = canonical_osm_outputs(project)
            self.assertEqual(result["osm_pop_0"], expected["osm_pop_0"])
            self.assertTrue(result["merged_pbf"].is_file())
            self.assertEqual(commands[0][0:2], ["extract", "-p"])
            self.assertEqual(Path(commands[0][2]).name, "bound_pa_ia1.poly")
            self.assertNotIn("-b", commands[0])

    def test_phase_a_can_build_missing_pa_poly_from_selected_grid(self):
        build_calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            source = Path(temp_dir) / "source.osm.pbf"
            zones = Path(temp_dir) / "zones.gpkg"
            source.touch()
            zones.touch()

            def fake_builder(**kwargs):
                build_calls.append(kwargs)
                Path(kwargs["selected_pa_cells"]).touch()
                Path(kwargs["output_poly"]).touch()

            def fake_osmium(_runtime, arguments, **_kwargs):
                Path(arguments[arguments.index("-o") + 1]).touch()
                return subprocess.CompletedProcess(arguments, 0, "", "")

            def fake_export(*_args, output_path, **_kwargs):
                Path(output_path).touch()
                return output_path

            with patch("core.app.app_core.osm_pipeline.run_osmium", fake_osmium):
                OsmPipeline(self._runtime()).run_phase_a(
                    OsmPhaseAConfig(project, [source], zone_type_selected=zones),
                    build_pa_polygon_fn=fake_builder,
                    export_cities_fn=fake_export,
                    export_uninhabited_fn=fake_export,
                )

            self.assertEqual(build_calls[0]["target_crs"], "EPSG:4326")
            self.assertEqual(Path(build_calls[0]["output_poly"]).name, "bound_pa_ia1.poly")

    def test_phase_a_consolidates_overlapping_inputs_before_extract(self):
        commands = []
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            sources = [Path(temp_dir) / f"source_{index}.osm.pbf" for index in range(2)]
            pa_poly = Path(temp_dir) / "pa.poly"
            for source in sources:
                source.touch()
            pa_poly.touch()

            def fake_osmium(_runtime, arguments, **_kwargs):
                commands.append([str(arg) for arg in arguments])
                output = Path(arguments[arguments.index("-o") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.touch()
                return subprocess.CompletedProcess(arguments, 0, "", "")

            def fake_export(*_args, output_path, **_kwargs):
                Path(output_path).touch()
                return output_path

            with patch("core.app.app_core.osm_pipeline.run_osmium", fake_osmium):
                OsmPipeline(self._runtime()).run_phase_a(
                    OsmPhaseAConfig(project, sources, pa_poly),
                    export_cities_fn=fake_export,
                    export_uninhabited_fn=fake_export,
                )

            self.assertEqual(
                [command[0] for command in commands[:3]],
                ["merge", "time-filter", "extract"],
            )
            merged_versions = Path(commands[0][commands[0].index("-o") + 1])
            snapshot = Path(commands[1][commands[1].index("-o") + 1])
            extract_input = Path(commands[2][3])
            self.assertEqual(merged_versions.name, "merged_versions.osm.pbf")
            self.assertEqual(snapshot.name, "merged.osm.pbf")
            self.assertEqual(extract_input, snapshot)
            self.assertFalse(merged_versions.exists())

    def test_phase_c_returns_both_network_and_poi_contracts(self):
        logs = []
        progress_updates = []
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            inputs = [Path(temp_dir) / name for name in ("merged.pbf", "paia1.poly", "study.poly", "master.net")]
            for path in inputs:
                path.touch()

            def fake_network(
                *_args, output_original, output_modified, log, progress, **_kwargs
            ):
                log("network console message")
                progress("network_classification", 9, 11)
                Path(output_original).touch()
                Path(output_modified).touch()
                return True

            def fake_study(
                *_args, output_points, output_polygons, log, progress, **_kwargs
            ):
                log("study-area console message")
                progress("study_area_multipolygons", 2, 3)
                Path(output_points).touch()
                Path(output_polygons).touch()
                return True

            result = OsmPipeline(
                self._runtime(),
                log=logs.append,
                progress=lambda name, index, total: progress_updates.append(
                    (name, index, total)
                ),
            ).run_phase_c(
                OsmPhaseCConfig(project, *inputs),
                network_fn=fake_network,
                study_area_fn=fake_study,
            )

            self.assertEqual(
                set(result),
                {"network_original", "network_modified", "study_area_points", "study_area_polygons"},
            )
            self.assertTrue(all(path.exists() for path in result.values()))
            self.assertIn("network console message", logs)
            self.assertIn("study-area console message", logs)
            self.assertIn(("network_classification", 9, 14), progress_updates)
            self.assertIn(("study_area_multipolygons", 13, 14), progress_updates)
            self.assertEqual(progress_updates[-1], ("complete", 14, 14))

    def test_run_osmium_rewrites_cross_drive_polygon_paths_on_windows(self):
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append((cmd, kwargs))
            proc = subprocess.Popen(["python", "-c", "import sys; sys.exit(0)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return proc

        runtime = OsmiumRuntime(
            executable=Path("C:/tools/osmium.exe"),
            version="1.18.0",
            bundled=True,
            platform="windows",
            architecture="x86_64",
            environment={},
        )

        run_osmium(
            runtime,
            ["extract", "-p", "S:/NetworkDrive/project/bound.poly", "C:/data/in.pbf", "-o", "out.pbf"],
            cwd="C:/different_drive/work",
            popen=fake_popen,
        )

        self.assertEqual(len(calls), 1)
        cmd, kwargs = calls[0]
        # Polygon argument should be changed to the local filename and cwd switched to polygon parent
        self.assertIn("bound.poly", cmd)
        self.assertNotIn("S:/NetworkDrive/project/bound.poly", cmd)
        self.assertEqual(Path(kwargs["cwd"]), Path("S:/NetworkDrive/project"))


if __name__ == "__main__":
    unittest.main()
