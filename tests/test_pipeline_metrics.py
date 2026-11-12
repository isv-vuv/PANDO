import csv
import json
import tempfile
import unittest
from pathlib import Path

from core.app.app_core.pipeline_metrics import RunMetrics


class RunMetricsTests(unittest.TestCase):
    def test_deleted_intermediates_remain_in_peak_and_deltas_telescope(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "input").write_bytes(b"123")
            metrics = RunMetrics(project)
            intermediate = project / "intermediate"
            intermediate.write_bytes(b"x" * 100)
            metrics.sample()
            intermediate.unlink()
            metrics.transition("second")
            (project / "output").write_bytes(b"12345")
            metrics.finish("completed", {})
            with (metrics.directory / "steps.csv").open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(sum(int(r["delta_bytes"]) for r in rows), 5)
            self.assertEqual(int(rows[0]["additional_peak_bytes"]), 100)
            data = json.loads((metrics.directory / "run.json").read_text())
            self.assertEqual(data["peak_project_bytes"], 103)
            self.assertTrue(all(float(r["duration_seconds"]) >= 0 for r in rows))

    def test_failed_step_and_reused_marker_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics = RunMetrics(directory)
            metrics.transition("model1 (wiederverwendet)")
            metrics.finish("failed", {"error": "test"})
            self.assertTrue(metrics.rows[-1]["reused"])
            self.assertEqual(metrics.rows[-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
