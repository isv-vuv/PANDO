from pathlib import Path
import re
import unittest


class BoundaryTests(unittest.TestCase):
    def test_app_core_and_app_qt_are_free_of_legacy_tk_imports(self):
        repo_root = Path(__file__).resolve().parents[1]
        banned = re.compile(r"\b(tkinter|customtkinter|tkintermapview|ttk|messagebox|filedialog)\b")
        offenders = []

        app_root = repo_root / "core" / "app"
        for package_name in ("app_core", "app_qt"):
            for path in (app_root / package_name).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if banned.search(text):
                    offenders.append(str(path.relative_to(repo_root)))

        self.assertEqual(offenders, [])

    def test_qt_base_has_no_qgis3_prefix_candidates(self):
        repo_root = Path(__file__).resolve().parents[1]
        qt_base = (
            repo_root / "core" / "app" / "app_qt" / "qt_base.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("QGIS 3.", qt_base)
        self.assertNotIn("QGIS-LTR", qt_base)


if __name__ == "__main__":
    unittest.main()
