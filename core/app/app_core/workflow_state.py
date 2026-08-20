"""Shared workflow state used by the Qt and QML presentation layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from core.app.app_core.grid import restore_grid_geometries
from core.app.app_core.project import (
    build_project_metadata,
    create_project,
    load_project_metadata,
    save_project_metadata,
    tool_root,
    workspace_data_dir,
)


STEP3_REQUIRED_KEYS = frozenset(
    {
        "selected_loc",
        "cell_size_m",
        "radius_km",
        "selected_cells",
        "grid_map_data",
    }
)
STEP4_REQUIRED_KEYS = STEP3_REQUIRED_KEYS | frozenset(
    {
        "workspace_path",
        "project_path",
        "data_path",
        "download_jobs",
        "user_pbf_path",
        "pbf_references",
    }
)


@dataclass(frozen=True)
class LocationAdapter:
    """Minimal location object for project metadata restored from JSON."""

    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class StepId(IntEnum):
    WELCOME = 0
    SEARCH = 1
    CITY_SELECTION = 2
    PROJECT_PBF = 3
    GRID_AREA = 4
    PROCESSING = 5
    VISUM = 6
    RESULTS = 7


STEP_COUNT = 6  # Active process steps (1 to 6)


def coerce_step_id(value: StepId | int) -> StepId:
    return value if isinstance(value, StepId) else StepId(int(value))


def previous_step_id(step_id: StepId | int) -> StepId | None:
    step = coerce_step_id(step_id)
    if step == StepId.WELCOME:
        return None
    if step == StepId.SEARCH or step == StepId.CITY_SELECTION:
        return StepId.WELCOME
    if step == StepId.PROJECT_PBF:
        return StepId.SEARCH
    if step == StepId.GRID_AREA:
        return StepId.PROJECT_PBF
    return StepId(int(step) - 1)


def progress_percent_for_step(step_id: StepId | int) -> int:
    step = coerce_step_id(step_id)
    if step == StepId.WELCOME:
        return 0
    step_num = int(step)
    if step_num >= 2:
        step_num -= 1
    return round(min(6, step_num) / STEP_COUNT * 100)


@dataclass
class AppState:
    """Mutable state passed between migrated Qt steps.

    Step widgets still communicate through Qt signals. The main window owns this
    object so each confirmed step has one canonical payload for the next step.
    """

    current_step: StepId = StepId.WELCOME
    search_city: str = ""
    geocode_results: list[Any] = field(default_factory=list)
    selected_location: Any = None
    workspace_path: str = ""
    data_path: str = ""
    project_path: str = ""
    language: str = "de"
    pbf_references: list[dict[str, Any]] = field(default_factory=list)
    last_saved_step: StepId = StepId.SEARCH
    step3_data: dict[str, Any] = field(default_factory=dict)
    step4_data: dict[str, Any] = field(default_factory=dict)

    def begin_search(self, city_name: str) -> None:
        self.current_step = StepId.SEARCH
        self.search_city = city_name
        self.geocode_results = []
        self.selected_location = None
        self.project_path = ""
        self.pbf_references = []
        self.step3_data = {}
        self.step4_data = {}

    def set_geocode_results(self, locations: list[Any]) -> None:
        self.geocode_results = list(locations)
        self.current_step = StepId.CITY_SELECTION

    def set_selected_location(self, location: Any) -> None:
        self.selected_location = location

    def confirm_location(self, location: Any) -> None:
        self.set_selected_location(location)
        self.current_step = StepId.PROJECT_PBF

    def set_project_context(self, workspace_path: str, project_path: str = "", data_path: str = "") -> None:
        self.workspace_path = workspace_path
        self.data_path = data_path or workspace_data_dir(workspace_path)
        self.project_path = project_path

    def create_new_project(
        self,
        workspace_path: str | None = None,
        unknown_place: str = "unknown_place",
        selected_location: Any = None,
    ) -> str:
        workspace_path = workspace_path or self.workspace_path or tool_root()
        selected_location = selected_location or self.selected_location
        project_path = create_project(workspace_path, selected_location, unknown_place)
        self.set_project_context(workspace_path, project_path)
        self.current_step = StepId.PROJECT_PBF if selected_location is not None else StepId.SEARCH
        self.last_saved_step = StepId.PROJECT_PBF if selected_location is not None else StepId.SEARCH
        self.save_project_metadata()
        return project_path

    def has_project_context(self) -> bool:
        return bool(self.workspace_path and self.data_path and self.project_path)

    def has_tool_context(self) -> bool:
        return bool(self.workspace_path and self.data_path)

    def set_language(self, language: str) -> None:
        self.language = language

    def set_step3_data(self, data: dict[str, Any]) -> None:
        self._require_keys(data, STEP3_REQUIRED_KEYS, "Step 3 (Grid)")
        self.step3_data = dict(data)
        self._sync_workspace_fields(self.step3_data)
        if "selected_loc" in self.step3_data and self.step3_data["selected_loc"]:
            self.selected_location = self.step3_data["selected_loc"]
        self.current_step = StepId.PROCESSING
        self.last_saved_step = StepId.GRID_AREA

    def set_step4_data(self, data: dict[str, Any]) -> None:
        self.step4_data = dict(data)
        self._sync_workspace_fields(self.step4_data)
        if "selected_loc" in self.step4_data and self.step4_data["selected_loc"]:
            self.selected_location = self.step4_data["selected_loc"]
        self.current_step = StepId.GRID_AREA
        self.last_saved_step = StepId.PROJECT_PBF

    def payload_for_step4(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.step4_data:
            payload.update(self.step4_data)
        if self.step3_data:
            payload.update(self.step3_data)
        if self.selected_location:
            payload["selected_loc"] = self.selected_location
        if self.workspace_path:
            payload["workspace_path"] = self.workspace_path
        if self.data_path:
            payload["data_path"] = self.data_path
        if self.project_path:
            payload["project_path"] = self.project_path
        if self.pbf_references:
            payload["pbf_references"] = list(self.pbf_references)
        return payload

    def payload_for_step5(self) -> dict[str, Any]:
        payload = dict(self.step4_data)
        payload.update(self.step3_data)
        if self.workspace_path:
            payload["workspace_path"] = self.workspace_path
        if self.data_path:
            payload["data_path"] = self.data_path
        if self.project_path:
            payload["project_path"] = self.project_path
        if self.pbf_references:
            payload["pbf_references"] = list(self.pbf_references)
        return payload

    def to_project_metadata(self) -> dict[str, Any]:
        if not self.project_path:
            raise ValueError("Cannot build project metadata without project_path")
        if not self.workspace_path:
            raise ValueError("Cannot build project metadata without workspace_path")
        return build_project_metadata(
            workspace_path=self.workspace_path,
            project_path=self.project_path,
            data_path=self.data_path or workspace_data_dir(self.workspace_path),
            selected_location=self.selected_location,
            last_step=int(self.current_step),
            step3_data=self.step3_data or None,
            step4_data=self.step4_data or None,
            pbf_references=self.pbf_references,
            language=self.language,
        )

    def save_project_metadata(self) -> str:
        return save_project_metadata(self.project_path, self.to_project_metadata())

    def load_project(self, project_path_or_file: str) -> StepId:
        metadata = load_project_metadata(project_path_or_file)
        self.restore_project_metadata(metadata)
        return self.current_step

    def restore_project_metadata(self, metadata: dict[str, Any]) -> None:
        self.workspace_path = metadata["workspace_path"]
        self.data_path = metadata["data_path"]
        self.project_path = metadata["project_path"]
        self.language = metadata.get("language") or self.language
        self.pbf_references = list(metadata.get("pbf_references") or [])
        self.step3_data = self._adapt_payload_locations(dict(metadata.get("step3_data") or {}))
        self.step4_data = self._adapt_payload_locations(dict(metadata.get("step4_data") or {}))
        metadata_location = self._location_from_metadata(metadata.get("selected_location"))
        if self.step4_data:
            self._sync_workspace_fields(self.step4_data)
            payload_location = self.step4_data.get("selected_loc")
            grid_data = self.step4_data.get("grid_map_data")
        elif self.step3_data:
            self._sync_workspace_fields(self.step3_data)
            payload_location = self.step3_data.get("selected_loc")
            grid_data = self.step3_data.get("grid_map_data")
        else:
            payload_location = None
            grid_data = None
        self.selected_location = self._restore_location(payload_location, metadata_location, grid_data)
        if self.selected_location is not None:
            if self.step3_data:
                self.step3_data["selected_loc"] = self.selected_location
            if self.step4_data:
                self.step4_data["selected_loc"] = self.selected_location
        self.geocode_results = [self.selected_location] if self.selected_location is not None else []
        self.current_step = StepId(int(metadata.get("last_step") or StepId.SEARCH))
        self.last_saved_step = self.current_step

    def _sync_workspace_fields(self, data: dict[str, Any]) -> None:
        workspace_path = data.get("workspace_path")
        data_path = data.get("data_path")
        project_path = data.get("project_path")
        if workspace_path:
            self.workspace_path = workspace_path
        if data_path:
            self.data_path = data_path
        elif self.workspace_path:
            self.data_path = workspace_data_dir(self.workspace_path)
        if project_path:
            self.project_path = project_path
        if "pbf_references" in data:
            self.pbf_references = list(data.get("pbf_references") or [])

    @staticmethod
    def _require_keys(data: dict[str, Any], required_keys: frozenset[str], label: str) -> None:
        missing = sorted(required_keys.difference(data))
        if missing:
            raise ValueError(f"{label} payload is missing required keys: {', '.join(missing)}")

    @classmethod
    def _adapt_payload_locations(cls, data: dict[str, Any]) -> dict[str, Any]:
        if "selected_loc" in data:
            data["selected_loc"] = cls._location_from_metadata(data["selected_loc"])
        grid_map_data = data.get("grid_map_data")
        if isinstance(grid_map_data, list):
            data["grid_map_data"] = restore_grid_geometries(grid_map_data)
        return data

    @staticmethod
    def _location_from_metadata(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return LocationAdapter(
            address=value.get("address") or "",
            latitude=value.get("latitude"),
            longitude=value.get("longitude"),
            raw=value.get("raw") if isinstance(value.get("raw"), dict) else {},
        )

    @classmethod
    def _restore_location(cls, payload_location: Any, metadata_location: Any, grid_data: Any) -> Any:
        if cls._location_has_coordinates(payload_location):
            return payload_location
        if cls._location_has_coordinates(metadata_location):
            return metadata_location

        recovered = cls._location_from_grid(
            grid_data,
            address=payload_location if isinstance(payload_location, str) else getattr(metadata_location, "address", ""),
            raw=getattr(metadata_location, "raw", {}),
        )
        return recovered or payload_location or metadata_location

    @staticmethod
    def _location_has_coordinates(location: Any) -> bool:
        return (
            location is not None
            and getattr(location, "latitude", None) is not None
            and getattr(location, "longitude", None) is not None
        )

    @staticmethod
    def _location_from_grid(grid_data: Any, *, address: str = "", raw: Any = None) -> LocationAdapter | None:
        latitudes = []
        longitudes = []
        for cell in grid_data or []:
            for coordinate in cell.get("wgs84_coords_map") or []:
                if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                    continue
                try:
                    latitudes.append(float(coordinate[0]))
                    longitudes.append(float(coordinate[1]))
                except (TypeError, ValueError):
                    continue
        if not latitudes or not longitudes:
            return None
        return LocationAdapter(
            address=address,
            latitude=(min(latitudes) + max(latitudes)) / 2,
            longitude=(min(longitudes) + max(longitudes)) / 2,
            raw=raw if isinstance(raw, dict) else {},
        )
