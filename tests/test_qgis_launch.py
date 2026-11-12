"""Keep result launches tied to the QGIS runtime that started PANDO."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.app.app_qt.steps import step6_results as results


class QgisLaunchTests(unittest.TestCase):
    def test_runtime_prefix_wins_over_other_installation_hints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "QGIS 4" / "apps" / "qgis"
            prefix.mkdir(parents=True)
            executable = root / "QGIS 4" / "bin" / "qgis-bin.exe"
            executable.parent.mkdir()
            executable.touch()
            old_executable = root / "QGIS 3" / "bin" / "qgis-bin.exe"
            old_executable.parent.mkdir(parents=True)
            old_executable.touch()
            with mock.patch.object(results, "QgsApplication") as app, mock.patch.object(
                results.platform, "system", return_value="Windows"
            ), mock.patch.dict(results.os.environ, {
                "QGIS_PREFIX_PATH": str(old_executable.parent.parent / "apps" / "qgis"),
                "QGIS_BAT": str(old_executable),
            }):
                app.prefixPath.return_value = str(prefix)
                self.assertEqual(results.find_qgis_executable(), str(executable))
                executable.unlink()
                self.assertIsNone(results.find_qgis_executable())

    def test_ltr_runtime_prefers_ltr_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "apps" / "qgis-ltr"
            prefix.mkdir(parents=True)
            (root / "bin").mkdir()
            for name in ("qgis-bin.exe", "qgis-ltr-bin.exe"):
                (root / "bin" / name).touch()
            with mock.patch.object(results, "QgsApplication") as app, mock.patch.object(
                results.platform, "system", return_value="Windows"
            ):
                app.prefixPath.return_value = str(prefix)
                self.assertEqual(results.find_qgis_executable(), str(root / "bin" / "qgis-ltr-bin.exe"))

    def test_launch_passes_target_to_resolved_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            layer = Path(directory) / "results.gpkg"
            layer.touch()
            with mock.patch.object(results, "find_qgis_executable", return_value="QGIS 4/qgis-bin.exe"), mock.patch.object(
                results.subprocess, "Popen"
            ) as popen, mock.patch.object(results, "_BACKGROUND_PROCESSES", []):
                self.assertTrue(results.open_in_qgis([layer]))
                self.assertEqual(popen.call_args.args[0], ["QGIS 4/qgis-bin.exe", str(layer)])

    def test_missing_or_failed_launcher_does_not_use_file_association(self):
        with tempfile.TemporaryDirectory() as directory:
            layer = Path(directory) / "results.gpkg"
            layer.touch()
            for executable in (None, "missing-qgis.exe"):
                with self.subTest(executable=executable), mock.patch.object(
                    results, "find_qgis_executable", return_value=executable
                ), mock.patch.object(results.subprocess, "Popen", side_effect=OSError("failed")), mock.patch.object(
                    results, "open_path_in_explorer"
                ) as explorer:
                    self.assertFalse(results.open_in_qgis([layer]))
                    explorer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
