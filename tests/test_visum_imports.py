"""Check the Qt worker's package and standalone orchestration imports."""

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VisumImportTests(unittest.TestCase):
    def test_package_import_without_scripts_on_path(self):
        self.run_python(
            "import importlib; "
            "m = importlib.import_module('core.scripts.visum.scripts.00_run_all_visum_scripts'); "
            "assert callable(m.run_visum_postprocessing_chain)"
        )

    def test_standalone_import(self):
        self.run_python(
            "import importlib.util, sys; from pathlib import Path; "
            "p = Path('core/scripts/visum/scripts/00_run_all_visum_scripts.py').resolve(); "
            "sys.path.insert(0, str(p.parent)); "
            "s = importlib.util.spec_from_file_location('run_all_visum', p); "
            "m = importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "assert callable(m.run_visum_postprocessing_chain)"
        )

    def run_python(self, code):
        result = subprocess.run(
            [sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
