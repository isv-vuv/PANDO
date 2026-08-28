import os
import tempfile
import unittest
from datetime import datetime

from core.app.app_core.project import (
    build_project_metadata,
    save_project_metadata,
)
from core.app.app_qt.dialogs.project_select_dialog import (
    _extract_location_str,
    _format_datetime,
    _format_step_label,
    filter_projects,
    scan_and_sort_projects,
)


class ProjectSelectDialogTests(unittest.TestCase):
    def test_format_helpers(self):
        # DateTime formatting
        dt = datetime(2026, 8, 28, 10, 15, 0)
        formatted, sort_ts = _format_datetime(dt.isoformat())
        self.assertEqual(formatted, "28.08.2026 10:15")
        self.assertGreater(sort_ts, 0.0)

        # Fallback for empty
        empty_fmt, empty_ts = _format_datetime(None)
        self.assertEqual(empty_fmt, "-")
        self.assertEqual(empty_ts, 0.0)

        # Step label formatting
        self.assertEqual(_format_step_label(1), "Schritt 1: Ortssuche")
        self.assertEqual(_format_step_label(4), "Schritt 4: QGIS-Modell")
        self.assertEqual(_format_step_label(6), "Schritt 6: Ergebnisse")

        # Location extraction
        meta = {
            "selected_location": {
                "display_name": "Berlin, Deutschland",
                "name": "Berlin",
            }
        }
        self.assertEqual(_extract_location_str(meta), "Berlin, Deutschland")

        meta_long = {
            "selected_location": {
                "display_name": "Stuttgart, Baden-Württemberg, 70174, Deutschland",
            }
        }
        self.assertEqual(_extract_location_str(meta_long), "Stuttgart, Deutschland")

    def test_project_discovery_sorting_and_filtering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proj_a = os.path.join(temp_dir, "Project_A_Old")
            proj_b = os.path.join(temp_dir, "Project_B_New")
            proj_c = os.path.join(temp_dir, "Project_C_Middle")

            t_old = datetime(2026, 8, 10, 12, 0, 0)
            t_mid = datetime(2026, 8, 20, 12, 0, 0)
            t_new = datetime(2026, 8, 28, 12, 0, 0)

            # Build metadata for each
            meta_a = build_project_metadata(
                workspace_path=temp_dir,
                project_path=proj_a,
                last_step=2,
                updated_at=t_old,
                selected_location={"display_name": "Hamburg, Deutschland"},
            )
            meta_b = build_project_metadata(
                workspace_path=temp_dir,
                project_path=proj_b,
                last_step=5,
                updated_at=t_new,
                selected_location={"display_name": "München, Deutschland"},
            )
            meta_c = build_project_metadata(
                workspace_path=temp_dir,
                project_path=proj_c,
                last_step=3,
                updated_at=t_mid,
                selected_location={"display_name": "Köln, Deutschland"},
            )

            save_project_metadata(proj_a, meta_a)
            save_project_metadata(proj_b, meta_b)
            save_project_metadata(proj_c, meta_c)

            projects = scan_and_sort_projects(temp_dir)

            # Verify sorting: newest first (B, C, A)
            self.assertEqual(len(projects), 3)
            self.assertEqual(projects[0]["name"], "Project_B_New")
            self.assertEqual(projects[1]["name"], "Project_C_Middle")
            self.assertEqual(projects[2]["name"], "Project_A_Old")

            # Verify filtering
            filtered_munich = filter_projects(projects, "München")
            self.assertEqual(len(filtered_munich), 1)
            self.assertEqual(filtered_munich[0]["name"], "Project_B_New")

            filtered_hamburg = filter_projects(projects, "hamburg")
            self.assertEqual(len(filtered_hamburg), 1)
            self.assertEqual(filtered_hamburg[0]["name"], "Project_A_Old")

            filtered_none = filter_projects(projects, "NonExistentCity")
            self.assertEqual(len(filtered_none), 0)

            filtered_all = filter_projects(projects, "")
            self.assertEqual(len(filtered_all), 3)

    def test_dialog_instantiation_with_custom_titles(self):
        from core.app.app_qt.qt_base import QtWidgets
        if QtWidgets is None:
            self.skipTest("PyQt/QGIS environment not available")
        from core.app.app_qt.dialogs.project_select_dialog import ProjectSelectDialog
        with tempfile.TemporaryDirectory() as temp_dir:
            dialog = ProjectSelectDialog(
                projects_dir=temp_dir,
                dialog_title="Custom Title",
                accept_button_text="Custom Accept",
            )
            self.assertEqual(dialog.dialog_title, "Custom Title")
            self.assertEqual(dialog.accept_button_text, "Custom Accept")


if __name__ == "__main__":
    unittest.main()
