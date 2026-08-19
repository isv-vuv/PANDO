import os
import tempfile
import unittest

from core.app.app_qt.app_state import AppState, LocationAdapter, StepId, previous_step_id, progress_percent_for_step
from core.app.app_core.settings import AppSettings, load_app_settings, save_app_settings


def step4_payload(**overrides):
    payload = {
        "selected_loc": "loc",
        "cell_size_m": 3500,
        "radius_km": 20,
        "selected_cells": {"PA": {1}, "IA1": set(), "IA2": set()},
        "grid_map_data": [{"id": 1}],
        "workspace_path": "/tmp/workspace",
        "project_path": "/tmp/workspace/project",
        "data_path": "/tmp/workspace/data",
        "download_jobs": [{"filename": "berlin-latest.osm.pbf"}],
        "user_pbf_path": "/tmp/workspace/data/berlin.osm.pbf",
        "pbf_references": [{"filename": "berlin-latest.osm.pbf", "path": "/tmp/workspace/data/berlin.osm.pbf"}],
    }
    payload.update(overrides)
    return payload


class AppStateTests(unittest.TestCase):
    def test_step_progress_maps_current_step_to_percent(self):
        self.assertEqual(progress_percent_for_step(StepId.WELCOME), 0)
        self.assertEqual(progress_percent_for_step(StepId.SEARCH), 17)
        self.assertEqual(progress_percent_for_step(StepId.CITY_SELECTION), 17)
        self.assertEqual(progress_percent_for_step(StepId.GRID_AREA), 33)
        self.assertEqual(progress_percent_for_step(StepId.PROJECT_PBF), 50)
        self.assertEqual(progress_percent_for_step(StepId.PROCESSING), 67)
        self.assertEqual(progress_percent_for_step(StepId.VISUM), 83)
        self.assertEqual(progress_percent_for_step(StepId.RESULTS), 100)

    def test_previous_step_id_walks_back_without_mutating_state(self):
        state = AppState(current_step=StepId.PROJECT_PBF, search_city="Berlin")

        self.assertEqual(previous_step_id(StepId.RESULTS), StepId.VISUM)
        self.assertEqual(previous_step_id(StepId.VISUM), StepId.PROCESSING)
        self.assertEqual(previous_step_id(state.current_step), StepId.GRID_AREA)
        self.assertEqual(previous_step_id(StepId.GRID_AREA), StepId.SEARCH)
        self.assertEqual(previous_step_id(StepId.SEARCH), StepId.WELCOME)
        self.assertIsNone(previous_step_id(StepId.WELCOME))
        self.assertEqual(state.current_step, StepId.PROJECT_PBF)
        self.assertEqual(state.search_city, "Berlin")

    def test_begin_search_resets_downstream_state_and_starts_a_new_location_project(self):
        state = AppState(
            current_step=StepId.PROCESSING,
            search_city="Old",
            geocode_results=["old"],
            selected_location="loc",
            workspace_path="/tmp/workspace",
            data_path="/tmp/workspace/data",
            project_path="/tmp/workspace/project",
            pbf_references=[{"filename": "old.osm.pbf"}],
            step3_data={"selected_loc": "loc"},
            step4_data={"project_path": "/tmp/project"},
        )

        state.begin_search("Stuttgart")

        self.assertEqual(state.current_step, StepId.SEARCH)
        self.assertEqual(state.search_city, "Stuttgart")
        self.assertEqual(state.geocode_results, [])
        self.assertIsNone(state.selected_location)
        self.assertEqual(state.workspace_path, "/tmp/workspace")
        self.assertEqual(state.data_path, "/tmp/workspace/data")
        self.assertEqual(state.project_path, "")
        self.assertEqual(state.pbf_references, [])
        self.assertEqual(state.step3_data, {})
        self.assertEqual(state.step4_data, {})

    def test_create_new_project_sets_context_and_writes_metadata(self):
        with tempfile.TemporaryDirectory() as workspace_path:
            state = AppState(language="de")

            project_path = state.create_new_project(workspace_path, "Unbekannter Ort")

            self.assertTrue(state.has_project_context())
            self.assertEqual(state.workspace_path, workspace_path)
            self.assertEqual(state.data_path, os.path.join(workspace_path, "core", "data", "osm"))
            self.assertEqual(state.project_path, project_path)
            self.assertTrue(os.path.isdir(state.data_path))
            self.assertTrue(os.path.isfile(os.path.join(project_path, "config.json")))
            self.assertEqual(state.load_project(project_path), StepId.SEARCH)
            self.assertEqual(state.language, "de")

    def test_step3_payload_contract_is_validated_and_copied(self):
        state = AppState()
        payload = {
            "selected_loc": "loc",
            "cell_size_m": 3500,
            "radius_km": 20,
            "selected_cells": {"PA": {1}, "IA1": set(), "IA2": set()},
            "grid_map_data": [{"id": 1}],
        }

        state.set_step3_data(payload)
        payload["cell_size_m"] = 1

        self.assertEqual(state.current_step, StepId.PROJECT_PBF)
        self.assertEqual(state.payload_for_step4()["cell_size_m"], 3500)
        self.assertEqual(state.selected_location, "loc")

    def test_step4_payload_contract_preserves_step3_subset(self):
        state = AppState()
        payload = step4_payload()

        state.set_step4_data(payload)

        self.assertEqual(state.current_step, StepId.PROCESSING)
        self.assertEqual(state.payload_for_step5()["project_path"], "/tmp/workspace/project")
        self.assertEqual(set(state.step3_data), {"selected_loc", "cell_size_m", "radius_km", "selected_cells", "grid_map_data"})
        self.assertEqual(state.workspace_path, "/tmp/workspace")
        self.assertEqual(state.data_path, "/tmp/workspace/data")
        self.assertEqual(state.pbf_references, [{"filename": "berlin-latest.osm.pbf", "path": "/tmp/workspace/data/berlin.osm.pbf"}])

    def test_project_context_is_added_to_step4_payload_when_available(self):
        state = AppState()
        state.set_project_context("/tmp/workspace", "/tmp/workspace/project")
        state.set_step3_data(
            {
                "selected_loc": "loc",
                "cell_size_m": 3500,
                "radius_km": 20,
                "selected_cells": {"PA": {1}, "IA1": set(), "IA2": set()},
                "grid_map_data": [{"id": 1}],
            }
        )

        payload = state.payload_for_step4()

        expected_workspace = os.path.abspath("/tmp/workspace")
        self.assertEqual(payload["workspace_path"], "/tmp/workspace")
        self.assertEqual(
            payload["data_path"],
            os.path.join(expected_workspace, "core", "data", "osm"),
        )
        self.assertEqual(payload["project_path"], "/tmp/workspace/project")

    def test_project_metadata_resume_restores_saved_step(self):
        with tempfile.TemporaryDirectory() as workspace_path:
            project_path = os.path.join(workspace_path, "20260702_DE_Berlin_Model")
            data_path = os.path.join(workspace_path, "data")
            pbf_path = os.path.join(data_path, "berlin.osm.pbf")
            state = AppState()
            state.set_step4_data(
                step4_payload(
                    workspace_path=workspace_path,
                    project_path=project_path,
                    data_path=data_path,
                    user_pbf_path=pbf_path,
                    pbf_references=[{"filename": "berlin.osm.pbf", "path": pbf_path}],
                )
            )
            metadata_path = state.save_project_metadata()

            resumed = AppState()
            step = resumed.load_project(metadata_path)

            self.assertEqual(step, StepId.PROCESSING)
            self.assertEqual(resumed.current_step, StepId.PROCESSING)
            self.assertEqual(resumed.workspace_path, workspace_path)
            self.assertEqual(resumed.project_path, project_path)
            self.assertEqual(resumed.payload_for_step5()["data_path"], data_path)

    def test_app_settings_roundtrip_persists_workspace_and_language(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = os.path.join(temp_dir, "settings.json")
            settings = AppSettings(last_workspace_path="/tmp/workspace", language="de")

            saved_path = save_app_settings(settings, settings_path)
            loaded = load_app_settings(saved_path)

            self.assertEqual(saved_path, settings_path)
            self.assertEqual(loaded.last_workspace_path, "/tmp/workspace")
            self.assertEqual(loaded.language, "de")

    def test_project_metadata_resume_adapts_location_dicts_to_attributes(self):
        state = AppState()
        metadata = {
            "schema_version": 1,
            "workspace_path": "/tmp/workspace",
            "project_path": "/tmp/workspace/project",
            "data_path": "/tmp/workspace/data",
            "last_step": 4,
            "pbf_references": [],
            "selected_location": {
                "address": "Berlin, Deutschland",
                "latitude": 52.5,
                "longitude": 13.4,
                "raw": {"address": {"country_code": "de"}},
            },
            "step3_data": {
                "selected_loc": {
                    "address": "Berlin, Deutschland",
                    "latitude": 52.5,
                    "longitude": 13.4,
                    "raw": {"address": {"country_code": "de"}},
                },
                "cell_size_m": 3500,
                "radius_km": 20,
                "selected_cells": {"PA": [1], "IA1": [], "IA2": []},
                "grid_map_data": [{"id": 1}],
            },
        }

        state.restore_project_metadata(metadata)

        loc = state.selected_location
        self.assertEqual(loc.address, "Berlin, Deutschland")
        self.assertEqual(loc.latitude, 52.5)
        self.assertEqual(loc.longitude, 13.4)
        self.assertEqual(loc.raw["address"]["country_code"], "de")
        self.assertIs(state.step3_data["selected_loc"], loc)
        self.assertEqual(state.geocode_results, [loc])

    def test_project_metadata_roundtrip_preserves_step_location_coordinates(self):
        with tempfile.TemporaryDirectory() as workspace_path:
            project_path = os.path.join(workspace_path, "project")
            state = AppState(
                selected_location=LocationAdapter(
                    address="Stuttgart, Deutschland",
                    latitude=48.7784,
                    longitude=9.1800,
                    raw={"address": {"country_code": "de"}},
                ),
                workspace_path=workspace_path,
                data_path=os.path.join(workspace_path, "data"),
                project_path=project_path,
                current_step=StepId.PROJECT_PBF,
            )
            state.step3_data = {
                "selected_loc": state.selected_location,
                "cell_size_m": 3500,
                "radius_km": 20,
                "selected_cells": {"PA": [1], "IA1": [], "IA2": []},
                "grid_map_data": [{"id": 1}],
            }

            metadata_path = state.save_project_metadata()
            resumed = AppState()
            resumed.load_project(metadata_path)

            self.assertEqual(resumed.selected_location.address, "Stuttgart, Deutschland")
            self.assertEqual(resumed.selected_location.latitude, 48.7784)
            self.assertEqual(resumed.selected_location.longitude, 9.1800)
            self.assertEqual(resumed.selected_location.raw["address"]["country_code"], "de")
            self.assertIs(resumed.step3_data["selected_loc"], resumed.selected_location)

    def test_project_metadata_roundtrip_rehydrates_grid_polygon(self):
        try:
            from shapely.geometry import Polygon
        except ImportError:
            self.skipTest("shapely not installed")

        with tempfile.TemporaryDirectory() as workspace_path:
            project_path = os.path.join(workspace_path, "project")
            location = LocationAdapter(
                address="Stuttgart, Deutschland",
                latitude=48.7784,
                longitude=9.1800,
                raw={"address": {"country_code": "de"}},
            )
            state = AppState(
                selected_location=location,
                workspace_path=workspace_path,
                project_path=project_path,
                current_step=StepId.PROCESSING,
            )
            polygon = Polygon([(9.0, 48.0), (10.0, 48.0), (10.0, 49.0), (9.0, 49.0)])
            state.step4_data = step4_payload(
                selected_loc=location,
                workspace_path=workspace_path,
                project_path=project_path,
                grid_map_data=[{"id": 1, "shapely_poly_wgs84": polygon}],
            )

            metadata_path = state.save_project_metadata()
            resumed = AppState()
            resumed.load_project(metadata_path)

            restored = resumed.payload_for_step5()["grid_map_data"][0]["shapely_poly_wgs84"]
            self.assertEqual(restored.geom_type, "Polygon")
            self.assertFalse(restored.is_empty)

    def test_legacy_string_location_is_recovered_from_saved_grid(self):
        state = AppState()
        metadata = {
            "schema_version": 1,
            "workspace_path": "/tmp/workspace",
            "project_path": "/tmp/workspace/project",
            "data_path": "/tmp/workspace/data",
            "last_step": 4,
            "pbf_references": [],
            "selected_location": {
                "address": None,
                "latitude": None,
                "longitude": None,
                "raw": None,
            },
            "step3_data": {
                "selected_loc": "Stuttgart, Baden-Württemberg, Germany",
                "cell_size_m": 3500,
                "radius_km": 20,
                "selected_cells": {"PA": [1], "IA1": [], "IA2": []},
                "grid_map_data": [
                    {
                        "id": 1,
                        "wgs84_coords_map": [
                            [48.7, 9.0],
                            [48.7, 9.4],
                            [48.9, 9.4],
                            [48.9, 9.0],
                        ],
                    }
                ],
            },
        }

        state.restore_project_metadata(metadata)

        self.assertEqual(state.selected_location.address, "Stuttgart, Baden-Württemberg, Germany")
        self.assertAlmostEqual(state.selected_location.latitude, 48.8)
        self.assertAlmostEqual(state.selected_location.longitude, 9.2)
        self.assertIs(state.step3_data["selected_loc"], state.selected_location)

    def test_missing_payload_keys_raise_clear_error(self):
        state = AppState()

        with self.assertRaisesRegex(ValueError, "radius_km"):
            state.set_step3_data({"selected_loc": "loc", "cell_size_m": 3500})


if __name__ == "__main__":
    unittest.main()
