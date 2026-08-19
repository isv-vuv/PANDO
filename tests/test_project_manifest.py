import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

from core.app.app_core.project import (
    PIPELINE_MANIFEST_FILENAME,
    build_pipeline_manifest,
    canonical_project_paths,
    create_project,
    dated_pbf_filename,
    load_pipeline_manifest,
    manifest_outputs_valid,
    mark_downstream_phases_stale,
    mark_phases_stale,
    pbf_age_days,
    pbf_is_stale,
    pbf_metadata,
    phase_can_be_reused,
    save_pipeline_manifest,
    update_manifest_phase,
)


class ProjectManifestTests(unittest.TestCase):
    def test_project_is_created_below_fixed_projects_dir_with_canonical_tree(self):
        location = SimpleNamespace(
            address="Stuttgart, Deutschland",
            raw={"address": {"country_code": "de"}},
        )
        with tempfile.TemporaryDirectory() as root:
            first = create_project(root, location, created_at=datetime(2026, 7, 29))
            second = create_project(root, location, created_at=datetime(2026, 7, 29))
            paths = canonical_project_paths(first)

            self.assertEqual(first, os.path.join(root, "projects", "20260729_stuttgart"))
            self.assertEqual(second, os.path.join(root, "projects", "20260729_stuttgart_2"))
            for key in ("input", "temp", "osm", "qgis_output", "visum"):
                self.assertTrue(os.path.isdir(paths[key]), key)
            self.assertTrue(os.path.isfile(paths["config"]))
            self.assertTrue(os.path.isfile(paths["manifest"]))
            self.assertTrue(os.path.isdir(os.path.join(paths["osm"], "04_network")))
            self.assertTrue(os.path.isdir(os.path.join(paths["qgis_output"], "model6_ZoneAssembler")))
            self.assertTrue(os.path.isdir(os.path.join(paths["visum"], "shapefile", "Zones")))

    def test_manifest_roundtrip_tracks_status_parameters_outputs_and_model_source(self):
        with tempfile.TemporaryDirectory() as project:
            output = os.path.join(project, "processed", "result.gpkg")
            os.makedirs(os.path.dirname(output))
            open(output, "wb").close()
            manifest = build_pipeline_manifest(
                project,
                active_model_source="/models/only-source",
                phases=["phase_a", "model_1"],
            )
            update_manifest_phase(
                manifest,
                "phase_a",
                "done",
                parameters={"radius_km": 25},
                outputs={"cities": output},
                logs=["complete"],
                now=datetime(2026, 7, 29, 12),
            )

            path = save_pipeline_manifest(project, manifest)
            loaded = load_pipeline_manifest(project)

            self.assertEqual(path, os.path.join(project, PIPELINE_MANIFEST_FILENAME))
            self.assertEqual(
                loaded["active_model_source"],
                os.path.abspath("/models/only-source"),
            )
            self.assertEqual(loaded["phases"]["phase_a"]["parameters"], {"radius_km": 25})
            self.assertTrue(manifest_outputs_valid(loaded["phases"]["phase_a"]))

    def test_stale_propagation_marks_only_named_dependants(self):
        manifest = build_pipeline_manifest("/tmp/example", phases=["phase_a", "model_1", "model_2"])
        for phase in manifest["phases"].values():
            phase["status"] = "done"

        mark_phases_stale(manifest, ["model_1", "model_2"], reason="radius changed")

        self.assertEqual(manifest["phases"]["phase_a"]["status"], "done")
        self.assertEqual(manifest["phases"]["model_1"]["status"], "stale")
        self.assertEqual(manifest["phases"]["model_2"]["stale_reason"], "radius changed")

    def test_downstream_stale_propagation_follows_explicit_phase_order(self):
        manifest = build_pipeline_manifest("/tmp/example", phases=["a", "b", "c"])
        for phase in manifest["phases"].values():
            phase["status"] = "done"

        mark_downstream_phases_stale(
            manifest,
            "b",
            phase_order=("a", "b", "c"),
            reason="input changed",
        )

        self.assertEqual(manifest["phases"]["a"]["status"], "done")
        self.assertEqual(manifest["phases"]["b"]["status"], "stale")
        self.assertEqual(manifest["phases"]["c"]["status"], "stale")

    def test_pbf_metadata_and_thirty_day_age_check(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "2026-07-01_germany.osm.pbf")
            with open(path, "wb") as pbf:
                pbf.write(b"osm")

            metadata = pbf_metadata(path, region="germany", checksum=True)

            self.assertEqual(metadata["size_bytes"], 3)
            self.assertEqual(metadata["downloaded_at"][:10], "2026-07-01")
            self.assertEqual(metadata["checksum"]["value"], hashlib.sha256(b"osm").hexdigest())
            self.assertEqual(pbf_age_days(metadata, now=datetime(2026, 7, 31)), 30)
            self.assertFalse(pbf_is_stale(metadata, now=datetime(2026, 7, 31)))
            self.assertTrue(pbf_is_stale(metadata, now=datetime(2026, 8, 1)))

    def test_done_phase_is_reusable_only_with_valid_outputs_and_current_pbfs(self):
        with tempfile.TemporaryDirectory() as project:
            pbf_path = os.path.join(project, "2026-07-01_germany.osm.pbf")
            output = os.path.join(project, "merged.osm.pbf")
            open(pbf_path, "wb").close()
            open(output, "wb").close()
            manifest = build_pipeline_manifest(
                project,
                input_pbfs=[pbf_path],
                phases=["phase_a"],
            )
            update_manifest_phase(manifest, "phase_a", "done", outputs={"merged": output})

            self.assertTrue(phase_can_be_reused(manifest, "phase_a", now=datetime(2026, 7, 31)))
            os.remove(output)
            self.assertFalse(phase_can_be_reused(manifest, "phase_a", now=datetime(2026, 7, 31)))

    def test_download_filename_uses_iso_date_prefix(self):
        self.assertEqual(
            dated_pbf_filename("germany-latest.osm.pbf", datetime(2026, 7, 29)),
            "2026-07-29_germany.osm.pbf",
        )


if __name__ == "__main__":
    unittest.main()
