import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.app.app_core.model_pipeline import load_parameter_defaults
from core.app.app_core.pipeline import PipelineCallbacks, UrbanActPipeline, pipeline_readiness
from core.app.app_core.project import (
    build_pipeline_manifest,
    pbf_metadata,
    save_pipeline_manifest,
    update_manifest_phase,
)


class PipelineConfigurationTests(unittest.TestCase):
    def test_model2_defaults_are_loaded_from_shipped_json(self):
        defaults = load_parameter_defaults("model2")
        self.assertEqual(defaults["minimum_population_level_0"], 500000)
        self.assertEqual(defaults["dual_centres_search_radius_km"], 5)

    def test_readiness_reports_distribution_resources_individually(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "projects/20260729_stuttgart"
            project.mkdir(parents=True)
            pbf = root / "source.osm.pbf"
            pbf.touch()
            step_data = {
                "pbf_references": [{"path": str(pbf)}],
                "selected_cells": {"PA": {1}, "IA1": set(), "IA2": set()},
            }

            blockers = pipeline_readiness(str(project), step_data, root=str(root))

            self.assertTrue(any(item.startswith("GADM ADM0 fehlt:") for item in blockers))
            self.assertTrue(any(item.startswith("GHS-POP fehlt:") for item in blockers))
            self.assertFalse(any(item.startswith("Keine PBF") for item in blockers))

    def test_readiness_accepts_complete_file_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "projects/20260729_stuttgart"
            project.mkdir(parents=True)
            required = [
                root / f"core/data/gadm/gadm_adm{level}.gpkg" for level in range(4)
            ] + [
                root / "core/data/ghs_pop/ghs_pop_global.tif",
                root / "core/scripts/visum/helper_files/master_linktypes.net",
            ]
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            pbf = root / "core/data/osm/DEU/2026-07-29_germany.osm.pbf"
            pbf.parent.mkdir(parents=True)
            pbf.touch()

            blockers = pipeline_readiness(
                str(project),
                {
                    "pbf_references": [{"path": os.fspath(pbf)}],
                    "selected_cells": {"PA": {1}},
                },
                root=str(root),
            )

            self.assertEqual(blockers, [])

    def test_phase_a_reuse_returns_existing_outputs_for_unchanged_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            pbf = Path(temp_dir) / "2026-07-29_germany.osm.pbf"
            pbf.write_bytes(b"osm")
            outputs = {}
            for key, relative in {
                "merged_pbf": "processed/osm/01_input/merged.osm.pbf",
                "bound_pa": "processed/osm/02_filter_bounds/bound_pa_ia1.poly",
                "osm_cities": "processed/osm/03_features/osm_cities.gpkg",
                "osm_pop_0": "processed/osm/03_features/pop_zero_osm.gpkg",
            }.items():
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                outputs[key] = str(path)
            manifest = build_pipeline_manifest(
                str(project), input_pbfs=[pbf], phases=["osm_phase_a"]
            )
            update_manifest_phase(manifest, "osm_phase_a", "done", outputs=outputs)
            save_pipeline_manifest(str(project), manifest)
            pipeline = UrbanActPipeline(
                str(project),
                {"pbf_references": [{"path": str(pbf)}], "selected_cells": {"PA": [1]}},
            )

            self.assertEqual(pipeline.reusable_phase_a_outputs(), outputs)

    def test_phase_a_reuse_is_rejected_after_pbf_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            pbf = Path(temp_dir) / "2026-07-29_germany.osm.pbf"
            pbf.write_bytes(b"osm")
            output = project / "processed/osm/01_input/merged.osm.pbf"
            output.parent.mkdir(parents=True)
            output.touch()
            manifest = build_pipeline_manifest(
                str(project), input_pbfs=[pbf], phases=["osm_phase_a"]
            )
            update_manifest_phase(
                manifest,
                "osm_phase_a",
                "done",
                outputs={
                    "merged_pbf": str(output),
                    "bound_pa": str(output),
                    "osm_cities": str(output),
                    "osm_pop_0": str(output),
                },
            )
            save_pipeline_manifest(str(project), manifest)
            pipeline = UrbanActPipeline(
                str(project), {"pbf_references": [{"path": str(pbf)}]}
            )
            pbf.write_bytes(b"changed")

            self.assertIsNone(pipeline.reusable_phase_a_outputs())

    def test_phase_a_only_skips_preparation_and_osmium_when_reusable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            pbf = Path(temp_dir) / "2026-07-29_germany.osm.pbf"
            pbf.write_bytes(b"osm")
            outputs = {}
            for key in ("merged_pbf", "bound_pa", "osm_cities", "osm_pop_0"):
                path = project / f"{key}.dat"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                outputs[key] = str(path)
            manifest = build_pipeline_manifest(
                str(project), input_pbfs=[pbf], phases=["osm_phase_a"]
            )
            update_manifest_phase(manifest, "osm_phase_a", "done", outputs=outputs)
            save_pipeline_manifest(str(project), manifest)
            logs = []
            pipeline = UrbanActPipeline(
                str(project), {"pbf_references": [{"path": str(pbf)}]}
            )

            with mock.patch(
                "core.app.app_core.pipeline.prepare_project_inputs",
                side_effect=AssertionError("must not prepare"),
            ), mock.patch(
                "core.app.app_core.pipeline.resolve_osmium",
                side_effect=AssertionError("must not resolve osmium"),
            ):
                result = pipeline.run_phase_a_only(
                    callbacks=PipelineCallbacks(log=logs.append)
                )

            self.assertEqual(result, outputs)
            self.assertTrue(any("wiederverwendet" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
