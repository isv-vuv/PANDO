import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.app.app_qt.qt_base import (
    configure_qgis_environment,
    find_gdal_data_path,
    find_qgis_prefix_path,
    is_valid_qgis_prefix,
    normalize_qgis_prefix_path,
)


class QtBootstrapPathTests(unittest.TestCase):
    def _create_qgis_app(self, root: Path, name: str = "QGIS-final-4_2_0.app") -> Path:
        app_path = root / name
        resources = app_path / "Contents" / "Resources" / "qgis"
        plugins = app_path / "Contents" / "PlugIns" / "qgis"
        (resources / "proj").mkdir(parents=True)
        (resources / "resources").mkdir()
        (resources / "gdal").mkdir()
        plugins.mkdir(parents=True)
        (resources / "proj" / "proj.db").touch()
        (resources / "resources" / "qgis.db").touch()
        (resources / "gdal" / "osmconf.ini").touch()
        (plugins / "libprovider_wms.so").touch()
        return app_path

    def test_normalizes_bundle_subpaths_to_app_root(self):
        with tempfile.TemporaryDirectory() as directory:
            app_path = self._create_qgis_app(Path(directory))

            self.assertEqual(
                normalize_qgis_prefix_path(str(app_path / "Contents" / "MacOS")),
                os.path.realpath(app_path),
            )
            self.assertEqual(
                normalize_qgis_prefix_path(str(app_path / "Contents" / "Resources" / "qgis")),
                os.path.realpath(app_path),
            )

    def test_rejects_existing_directory_without_qgis_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(is_valid_qgis_prefix(directory))

    def test_accepts_bundle_with_resources_and_wms_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            app_path = self._create_qgis_app(Path(directory))
            self.assertTrue(is_valid_qgis_prefix(str(app_path)))

    def test_invalid_environment_prefix_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_path = root / "not-qgis"
            invalid_path.mkdir()
            app_path = self._create_qgis_app(root)

            with mock.patch.dict(os.environ, {"QGIS_PREFIX_PATH": str(invalid_path)}):
                with mock.patch(
                    "core.app.app_qt.qt_base.discover_qgis_prefix_candidates",
                    return_value=[str(app_path)],
                ):
                    self.assertEqual(find_qgis_prefix_path(), os.path.realpath(app_path))

    def test_finds_gdal_data_inside_qgis_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            app_path = self._create_qgis_app(Path(directory))

            self.assertEqual(
                find_gdal_data_path(str(app_path)),
                str(app_path / "Contents" / "Resources" / "qgis" / "gdal"),
            )

    def test_configures_gdal_data_for_pyogrio_osm_import(self):
        with tempfile.TemporaryDirectory() as directory:
            app_path = self._create_qgis_app(Path(directory))

            with mock.patch.dict(os.environ, {}, clear=True):
                configure_qgis_environment(str(app_path))
                self.assertEqual(
                    os.environ["GDAL_DATA"],
                    str(app_path / "Contents" / "Resources" / "qgis" / "gdal"),
                )


if __name__ == "__main__":
    unittest.main()
