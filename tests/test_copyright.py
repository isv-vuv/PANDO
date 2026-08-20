import os
import unittest

from core.app.app_core.project import tool_root
from core.app.app_core.settings import AppSettings
from core.app.app_qt.dialogs.copyright_dialog import (
    COPYRIGHT_SOURCES,
    REQUIRED_SOURCE_IDS,
    format_source_text_to_html,
    load_source_content,
)


class CopyrightTests(unittest.TestCase):
    def test_copyright_sources_priority_order_and_file_existence(self):
        expected_ids = ["gadm", "ghs_pop", "osm", "osmium", "qgis", "visum"]
        self.assertEqual(REQUIRED_SOURCE_IDS, expected_ids)

        root = tool_root()
        for src in COPYRIGHT_SOURCES:
            rel_path = src["rel_path"]
            full_path = os.path.join(root, rel_path)
            self.assertTrue(os.path.isfile(full_path), f"Source file missing: {rel_path}")

            content = load_source_content(rel_path, root)
            self.assertFalse(content.startswith("Fehler"), f"Error loading source file: {rel_path}")
            self.assertGreater(len(content), 10, f"Source file content too short: {rel_path}")

    def test_format_source_text_to_html_converts_urls(self):
        sample = "OpenStreetMap\nhttps://www.openstreetmap.org/copyright\nTerms: test"
        html_out = format_source_text_to_html(sample)

        self.assertIn('<a href="https://www.openstreetmap.org/copyright"', html_out)
        self.assertIn("OpenStreetMap<br>", html_out)

    def test_app_settings_accepted_licenses_tracking(self):
        settings = AppSettings()
        self.assertFalse(settings.has_accepted_all_licenses(REQUIRED_SOURCE_IDS))

        # Accept partial
        settings.accepted_licenses["gadm"] = "2026-08-03T16:00:00"
        self.assertFalse(settings.has_accepted_all_licenses(REQUIRED_SOURCE_IDS))

        # Accept all 6
        for src_id in REQUIRED_SOURCE_IDS:
            settings.accepted_licenses[src_id] = "2026-08-03T16:00:00"

        self.assertTrue(settings.has_accepted_all_licenses(REQUIRED_SOURCE_IDS))

    def test_app_settings_serialization(self):
        data = {
            "last_workspace_path": "C:/Projects",
            "language": "de",
            "accepted_licenses": {"gadm": "2026-08-03T16:00:00", "osm": "2026-08-03T16:00:00"},
        }
        settings = AppSettings.from_dict(data)

        self.assertEqual(settings.last_workspace_path, "C:/Projects")
        self.assertEqual(settings.accepted_licenses, {"gadm": "2026-08-03T16:00:00", "osm": "2026-08-03T16:00:00"})


    def test_copyright_dialog_accept_all_and_unprechecked_defaults(self):
        from core.app.app_qt.qt_base import QtWidgets
        if QtWidgets is None:
            return
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from core.app.app_qt.dialogs.copyright_dialog import CopyrightDialog

        dialog = CopyrightDialog()
        for cb in dialog._checkboxes.values():
            self.assertFalse(cb.isChecked())

        dialog.accept_all_button.click()
        accepted_map = dialog.get_accepted_result()
        self.assertEqual(len(accepted_map), 6)
        for src_id in REQUIRED_SOURCE_IDS:
            self.assertIn(src_id, accepted_map)


if __name__ == "__main__":
    unittest.main()
