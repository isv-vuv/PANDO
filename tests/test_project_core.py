import os
import tempfile
import unittest
from datetime import date, datetime
from types import SimpleNamespace

from core.app.app_core.project import (
    PROJECT_INPUT_DIR,
    PROJECT_METADATA_FILENAME,
    PROJECT_PROCESSED_DIR,
    PROJECT_VISUM_DIR,
    PbfVerification,
    add_download_job,
    build_project_folder_name,
    build_project_metadata,
    build_pbf_references,
    build_step4_payload,
    create_project,
    dated_pbf_filename,
    download_jobs_from_pbf_references,
    pending_pbf_download_jobs,
    ensure_workspace_structure,
    list_projects,
    load_project_metadata,
    normalize_pbf_name,
    project_input_dir,
    project_metadata_path,
    remove_download_job,
    save_project_metadata,
    total_download_size,
    verify_pbf_files,
    workspace_data_dir,
)


class ProjectCoreTests(unittest.TestCase):
    def test_project_folder_name_uses_date_country_and_clean_city(self):
        loc = SimpleNamespace(address="München, Bayern, Deutschland", raw={"address": {"country_code": "de"}})

        folder_name = build_project_folder_name(loc, datetime(2026, 7, 2))

        self.assertEqual(folder_name, "20260702_muenchen")

    def test_create_project_builds_expected_structure(self):
        loc = SimpleNamespace(address="Stuttgart, Deutschland", raw={"address": {"country_code": "de"}})
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = create_project(temp_dir, loc, created_at=datetime(2026, 7, 2))

            self.assertTrue(os.path.isdir(os.path.join(project_path, PROJECT_INPUT_DIR)))
            self.assertTrue(os.path.isdir(os.path.join(project_path, PROJECT_PROCESSED_DIR)))
            self.assertTrue(os.path.isdir(os.path.join(project_path, PROJECT_VISUM_DIR, "shapefile")))
            self.assertTrue(os.path.isdir(workspace_data_dir(temp_dir)))
            self.assertTrue(os.path.isfile(os.path.join(project_path, PROJECT_METADATA_FILENAME)))
            self.assertEqual(load_project_metadata(project_path)["last_step"], 1)

    def test_create_project_uses_unique_folder_when_name_exists(self):
        loc = SimpleNamespace(address="Stuttgart, Deutschland", raw={"address": {"country_code": "de"}})
        with tempfile.TemporaryDirectory() as temp_dir:
            first_project_path = create_project(temp_dir, loc, created_at=datetime(2026, 7, 2))
            second_project_path = create_project(temp_dir, loc, created_at=datetime(2026, 7, 2))

            self.assertEqual(os.path.basename(first_project_path), "20260702_stuttgart")
            self.assertEqual(os.path.basename(second_project_path), "20260702_stuttgart_2")

    def test_workspace_structure_uses_fixed_data_directory(self):
        with tempfile.TemporaryDirectory() as workspace_path:
            paths = ensure_workspace_structure(workspace_path)

            self.assertEqual(paths["workspace_path"], workspace_path)
            self.assertEqual(paths["data_path"], workspace_data_dir(workspace_path))
            self.assertTrue(os.path.isdir(paths["data_path"]))

    def test_project_metadata_roundtrip_and_project_listing(self):
        loc = SimpleNamespace(
            address="Berlin, Deutschland",
            latitude=52.5,
            longitude=13.4,
            raw={"address": {"country_code": "de"}},
        )
        with tempfile.TemporaryDirectory() as workspace_path:
            project_path = create_project(workspace_path, loc, created_at=datetime(2026, 7, 2))
            metadata = build_project_metadata(
                workspace_path=workspace_path,
                project_path=project_path,
                selected_location=loc,
                last_step=5,
                step3_data={"selected_loc": "Berlin", "selected_cells": {"PA": {1}}},
                pbf_references=[{"filename": "berlin-latest.osm.pbf", "path": os.path.join(workspace_path, "data", "berlin-latest.osm.pbf")}],
            )

            metadata_path = save_project_metadata(project_path, metadata)
            loaded = load_project_metadata(project_path)
            projects = list_projects(workspace_path)

            self.assertEqual(metadata_path, project_metadata_path(project_path))
            self.assertEqual(loaded["last_step"], 5)
            self.assertEqual(loaded["data_path"], workspace_data_dir(workspace_path))
            self.assertEqual(loaded["language"], "de")
            self.assertEqual(loaded["selected_location"]["address"], "Berlin, Deutschland")
            self.assertEqual(loaded["step3_data"]["selected_cells"]["PA"], [1])
            self.assertEqual([project["project_path"] for project in projects], [project_path])

    def test_download_job_helpers_preserve_contract_and_prevent_duplicates(self):
        jobs = []
        region = {
            "id": "germany/baden-wuerttemberg",
            "name": "Baden Wuerttemberg",
            "pbf_url": "https://example.test/baden-wuerttemberg-latest.osm.pbf",
            "size_bytes": 42,
        }

        self.assertTrue(add_download_job(jobs, region))
        self.assertFalse(add_download_job(jobs, region))
        self.assertEqual(
            jobs[0],
            {
                "name": "Baden Wuerttemberg",
                "url": "https://example.test/baden-wuerttemberg-latest.osm.pbf",
                "osm_id": "germany/baden-wuerttemberg",
                "bytes": 42,
                "filename": "baden-wuerttemberg-latest.osm.pbf",
            },
        )
        self.assertEqual(total_download_size(jobs), 42)
        self.assertEqual(remove_download_job(jobs, "germany/baden-wuerttemberg"), [])

    def test_pbf_verification_normalizes_latest_and_dated_names(self):
        jobs = [
            {"filename": "baden-wuerttemberg-latest.osm.pbf"},
            {"filename": "bayern-260701.osm.pbf"},
        ]
        with tempfile.TemporaryDirectory() as project_path:
            os.makedirs(project_input_dir(project_path))
            open(os.path.join(project_input_dir(project_path), "baden-wuerttemberg-260702.osm.pbf"), "wb").close()

            verification = verify_pbf_files(project_path, jobs)

            self.assertFalse(verification.all_found)
            self.assertEqual(verification.found_names, ["baden-wuerttemberg-260702.osm.pbf"])
            self.assertEqual(verification.missing_basenames, ["bayern"])
            self.assertIsNone(verification.user_pbf_path)

    def test_dated_pbf_filename_replaces_latest_suffix_and_remains_verifiable(self):
        filename = dated_pbf_filename(
            "baden-wuerttemberg-latest.osm.pbf",
            downloaded_at=datetime(2026, 7, 29),
        )

        self.assertEqual(filename, "2026-07-29_baden-wuerttemberg.osm.pbf")
        self.assertEqual(normalize_pbf_name(filename), "baden-wuerttemberg")
        self.assertEqual(normalize_pbf_name("baden-wuerttemberg-latest.osm.pbf"), "baden-wuerttemberg")

    def test_single_verified_pbf_sets_user_pbf_path(self):
        jobs = [{"filename": "berlin-latest.osm.pbf"}]
        with tempfile.TemporaryDirectory() as project_path:
            os.makedirs(project_input_dir(project_path))
            pbf_path = os.path.join(project_input_dir(project_path), "berlin-260702.osm.pbf")
            open(pbf_path, "wb").close()

            verification = verify_pbf_files(project_path, jobs)

            self.assertTrue(verification.all_found)
            self.assertEqual(verification.user_pbf_path, pbf_path)

    def test_pbf_verification_can_use_workspace_data_path(self):
        jobs = [{"filename": "hamburg-latest.osm.pbf"}]
        with tempfile.TemporaryDirectory() as workspace_path:
            project_path = os.path.join(workspace_path, "project")
            data_path = workspace_data_dir(workspace_path)
            os.makedirs(data_path)
            pbf_path = os.path.join(data_path, "hamburg-260702.osm.pbf")
            open(pbf_path, "wb").close()

            verification = verify_pbf_files(project_path, jobs, data_path=data_path)

            self.assertTrue(verification.all_found)
            self.assertEqual(verification.found_files, [pbf_path])

    def test_workspace_data_verification_reuses_existing_pbf_and_builds_references(self):
        jobs = [
            {
                "osm_id": "germany/berlin",
                "name": "Berlin",
                "filename": "berlin-latest.osm.pbf",
                "url": "https://example.test/berlin-latest.osm.pbf",
            },
            {
                "osm_id": "germany/brandenburg",
                "name": "Brandenburg",
                "filename": "brandenburg-latest.osm.pbf",
                "url": "https://example.test/brandenburg-latest.osm.pbf",
            },
        ]
        with tempfile.TemporaryDirectory() as workspace_path:
            project_path = os.path.join(workspace_path, "20260702_DE_Berlin_Model")
            data_path = workspace_data_dir(workspace_path)
            os.makedirs(data_path)
            berlin_path = os.path.join(data_path, "berlin-260702.osm.pbf")
            brandenburg_path = os.path.join(data_path, "brandenburg-latest.osm.pbf")
            open(berlin_path, "wb").close()
            open(brandenburg_path, "wb").close()

            verification = verify_pbf_files(project_path, jobs, data_path=data_path)
            references = build_pbf_references(jobs, verification.found_files)

            self.assertTrue(verification.all_found)
            self.assertEqual(verification.found_files, [berlin_path, brandenburg_path])
            self.assertEqual(
                references,
                [
                    {
                        "osm_id": "germany/berlin",
                        "name": "Berlin",
                        "filename": "berlin-latest.osm.pbf",
                        "path": berlin_path,
                        "url": "https://example.test/berlin-latest.osm.pbf",
                    },
                    {
                        "osm_id": "germany/brandenburg",
                        "name": "Brandenburg",
                        "filename": "brandenburg-latest.osm.pbf",
                        "path": brandenburg_path,
                        "url": "https://example.test/brandenburg-latest.osm.pbf",
                    },
                ],
            )

    def test_pending_download_jobs_skip_current_offline_regions(self):
        jobs = [
            {"filename": "berlin-latest.osm.pbf"},
            {"filename": "brandenburg-latest.osm.pbf"},
            {"filename": "sachsen-latest.osm.pbf"},
        ]
        verification = PbfVerification(
            all_found=False,
            found_files=[
                "/tmp/2026-07-29_berlin.osm.pbf",
                "/tmp/2026-06-01_sachsen.osm.pbf",
            ],
            found_names=[
                "2026-07-29_berlin.osm.pbf",
                "2026-06-01_sachsen.osm.pbf",
            ],
            missing_basenames=["brandenburg"],
            user_pbf_path=None,
        )

        pending = pending_pbf_download_jobs(
            jobs,
            verification,
            now=date(2026, 7, 30),
        )

        self.assertEqual(
            [job["filename"] for job in pending],
            [
                "brandenburg-latest.osm.pbf",
                "sachsen-latest.osm.pbf",
            ],
        )

    def test_download_jobs_can_be_restored_from_pbf_references(self):
        references = [
            {
                "osm_id": "germany/berlin",
                "name": "Berlin",
                "filename": "berlin-latest.osm.pbf",
                "path": "/tmp/workspace/data/berlin-latest.osm.pbf",
                "url": "https://example.test/berlin-latest.osm.pbf",
            },
            {
                "path": "/tmp/workspace/data/brandenburg-260702.osm.pbf",
            },
        ]

        jobs = download_jobs_from_pbf_references(references)

        self.assertEqual(
            jobs,
            [
                {
                    "name": "Berlin",
                    "url": "https://example.test/berlin-latest.osm.pbf",
                    "osm_id": "germany/berlin",
                    "bytes": 0,
                    "filename": "berlin-latest.osm.pbf",
                },
                {
                    "name": "brandenburg",
                    "url": None,
                    "osm_id": "brandenburg",
                    "bytes": 0,
                    "filename": "brandenburg-260702.osm.pbf",
                },
            ],
        )

    def test_build_step4_payload_keeps_step3_data_and_public_keys(self):
        step3_data = {"selected_loc": "loc", "grid_map_data": []}
        jobs = [{"filename": "berlin-latest.osm.pbf"}]

        payload = build_step4_payload(step3_data, "/tmp/workspace/project", jobs, "/tmp/workspace/data/berlin.osm.pbf")

        self.assertEqual(payload["selected_loc"], "loc")
        self.assertIs(payload["download_jobs"], jobs)
        expected_workspace = os.path.abspath("/tmp/workspace")
        self.assertEqual(payload["workspace_path"], "/tmp/workspace")
        self.assertEqual(payload["project_path"], "/tmp/workspace/project")
        self.assertEqual(
            payload["data_path"],
            os.path.join(expected_workspace, "core", "data", "osm"),
        )
        self.assertEqual(payload["user_pbf_path"], "/tmp/workspace/data/berlin.osm.pbf")
        self.assertEqual(payload["pbf_references"][0]["filename"], "berlin-latest.osm.pbf")

    def test_normalize_pbf_name_removes_geofabrik_suffix(self):
        self.assertEqual(normalize_pbf_name("berlin-latest.osm.pbf"), "berlin")
        self.assertEqual(normalize_pbf_name("berlin-260702.osm.pbf"), "berlin")


if __name__ == "__main__":
    unittest.main()
