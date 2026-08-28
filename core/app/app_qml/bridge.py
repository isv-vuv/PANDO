"""Backend boundary for the independent QML presentation layer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

from core.locales import localizer
from core.app.app_core.formatting import format_bytes
from core.app.app_core.geo import create_circle_polygon_coords
from core.app.app_core.grid import (
    AREA_MODES,
    build_step3_payload,
    create_selected_cells,
    estimate_grid_cell_count,
    generate_grid_map_data,
    has_selected_cells,
    restore_selected_cells,
    toggle_cell_at_lonlat,
)
from core.app.app_core.model_pipeline import load_parameter_defaults
from core.app.app_core.pipeline import UrbanActPipeline, pipeline_readiness
from core.app.app_core.project import (
    DEFAULT_PBF_MAX_AGE_DAYS,
    PROJECT_METADATA_FILENAME,
    add_download_job,
    build_pbf_references,
    normalize_pbf_name,
    pending_pbf_download_jobs,
    pbf_age_days,
    remove_download_job,
    tool_root,
    total_download_size,
    workspace_data_dir,
)
from core.app.app_core.settings import AppSettings, load_app_settings, save_app_settings
from core.app.app_core.workflow_state import AppState, STEP_COUNT, StepId, previous_step_id, progress_percent_for_step

try:
    from qgis.PyQt.QtCore import QObject, QThread, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

    QT_AVAILABLE = True
except Exception:  # pragma: no cover - non-QGIS unit-test fallback
    QObject = object
    QThread = QTimer = None
    pyqtProperty = None
    QT_AVAILABLE = False

    class _NoopSignal:
        def connect(self, _callback: Any) -> None:
            return None

        def emit(self, *_args: Any) -> None:
            return None

    def pyqtSignal(*_args: Any, **_kwargs: Any) -> _NoopSignal:
        return _NoopSignal()

    def pyqtSlot(*_args: Any, **_kwargs: Any):
        def _decorator(func):
            return func

        return _decorator


MAX_GRID_CELLS = 2000
MODEL2_FIELDS = (
    "minimum_population_level_0",
    "minimum_population_level_1",
    "minimum_population_level_2",
    "population_tolerance",
    "distance_tolerance",
    "dual_centres_search_radius_km",
    "dual_centres_population_tolerance",
)


def _qt_property(value_type: object, getter, *, notify=None):
    if pyqtProperty is None:
        return property(getter)
    return pyqtProperty(value_type, fget=getter, notify=notify)


def _local_path(value: str) -> str:
    """Convert a QML file URL or a plain path to a normalized local path."""
    value = str(value or "")
    if value.startswith("file:"):
        parsed = urlparse(value)
        path = unquote(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        value = path
    return os.path.normpath(value) if value else ""


def _location_map(location: Any) -> dict[str, Any]:
    if location is None:
        return {}
    return {
        "address": str(getattr(location, "address", "") or ""),
        "latitude": float(getattr(location, "latitude", 0.0)),
        "longitude": float(getattr(location, "longitude", 0.0)),
    }


def _coordinates(coords: Iterable[Iterable[float]]) -> list[dict[str, float]]:
    return [{"latitude": float(lat), "longitude": float(lon)} for lat, lon in coords]


class QmlAppBridge(QObject):
    """Expose workflow data and commands without importing QWidget code."""

    stateChanged = pyqtSignal()
    contentChanged = pyqtSignal()
    statusChanged = pyqtSignal(str)
    dialogRequested = pyqtSignal(str, str, str)

    def __init__(
        self,
        state: AppState | None = None,
        settings: AppSettings | None = None,
        *,
        qml_material_available: bool = False,
        parent: object | None = None,
    ):
        if QT_AVAILABLE:
            super().__init__(parent)
        else:
            super().__init__()
        localizer.load_translations(str(Path(tool_root()) / "core" / "locales"))
        self._settings = settings or load_app_settings()
        localizer.set_language(self._settings.language or "de")
        self._state = state or AppState(language=localizer.get_current_language())
        self._state.set_language(localizer.get_current_language())
        if not self._state.workspace_path:
            self._state.set_project_context(tool_root())
        self._qml_material_available = bool(qml_material_available)
        self._status_text = localizer.get_string("step1_status_ready")
        self._busy = False
        self._threads: dict[str, Any] = {}
        self._workers: dict[str, Any] = {}
        self._grid_cells: list[dict[str, Any]] = []
        self._selected_cells = create_selected_cells()
        self._grid_params: tuple[int, int] | None = None
        self._available_regions: list[dict[str, Any]] = []
        self._regions_by_id: dict[str, dict[str, Any]] = {}
        self._download_jobs: list[dict[str, Any]] = []
        self._verification = None
        self._region_radius_km = 250
        self._download_progress = 0
        self._active_download_count = 0
        self._pipeline_phase = "Bereit"
        self._pipeline_progress = 0
        self._pipeline_phase_progress = 0
        self._pipeline_log: list[str] = []
        self._pipeline_running = False
        self._elapsed_seconds = 0
        self._timer = QTimer(self) if QT_AVAILABLE else None
        if self._timer is not None:
            self._timer.timeout.connect(self._tick_elapsed)
        self._geofabrik_index = self._load_geofabrik_index()
        self._restore_view_models()

    @property
    def state(self) -> AppState:
        return self._state

    def _emit_changed(self, *, content: bool = False) -> None:
        self.stateChanged.emit()
        if content:
            self.contentChanged.emit()
        self.statusChanged.emit(self._status_text)

    def _set_status(self, text: str) -> None:
        self._status_text = str(text)
        self._emit_changed()

    def _show_error(self, title: str, message: str) -> None:
        self.dialogRequested.emit("error", str(title), str(message))

    def _show_info(self, title: str, message: str) -> None:
        self.dialogRequested.emit("info", str(title), str(message))

    def _load_geofabrik_index(self) -> dict:
        try:
            with open(Path(tool_root()) / "core" / "data" / "osm" / "geofabrik-index.json", encoding="utf-8") as stream:
                return json.load(stream)
        except Exception as exc:
            return {"error": str(exc)}

    def _restore_view_models(self) -> None:
        data = self._state.step3_data
        if data.get("grid_map_data"):
            self._grid_cells = [dict(cell) for cell in data["grid_map_data"]]
            self._selected_cells = restore_selected_cells(self._grid_cells, data.get("selected_cells"))
            self._grid_params = (int(data["cell_size_m"]), int(data["radius_km"]))
        data4 = self._state.step4_data
        self._download_jobs = [dict(job) for job in data4.get("download_jobs") or []]

    # --- general properties -------------------------------------------------
    language = _qt_property(str, lambda self: self._state.language, notify=stateChanged)
    currentStep = _qt_property(int, lambda self: int(self._state.current_step), notify=stateChanged)
    stepCount = _qt_property(int, lambda _self: STEP_COUNT, notify=stateChanged)
    progressPercent = _qt_property(
        int, lambda self: progress_percent_for_step(self._state.current_step), notify=stateChanged
    )
    backEnabled = _qt_property(
        bool, lambda self: previous_step_id(self._state.current_step) is not None, notify=stateChanged
    )
    workspacePath = _qt_property(str, lambda self: self._state.workspace_path, notify=stateChanged)
    projectPath = _qt_property(str, lambda self: self._state.project_path, notify=stateChanged)
    dataPath = _qt_property(str, lambda self: self._state.data_path, notify=stateChanged)
    qmlMaterialAvailable = _qt_property(
        bool, lambda self: self._qml_material_available, notify=stateChanged
    )
    statusText = _qt_property(str, lambda self: self._status_text, notify=stateChanged)
    busy = _qt_property(bool, lambda self: self._busy, notify=stateChanged)
    mapBridgeStatus = _qt_property(str, lambda _self: "QtLocation/QML map", notify=stateChanged)
    selectedLocation = _qt_property(object, lambda self: _location_map(self._state.selected_location), notify=contentChanged)
    geocodeResults = _qt_property(
        object,
        lambda self: [_location_map(location) | {"index": index} for index, location in enumerate(self._state.geocode_results)],
        notify=contentChanged,
    )
    gridCells = _qt_property(object, lambda self: self._qml_grid_cells(), notify=contentChanged)
    selectedCounts = _qt_property(
        object,
        lambda self: {mode: len(self._selected_cells.get(mode, set())) for mode in AREA_MODES},
        notify=contentChanged,
    )
    availableRegions = _qt_property(object, lambda self: list(self._available_regions), notify=contentChanged)
    selectedRegions = _qt_property(object, lambda self: self._selected_region_rows(), notify=contentChanged)
    regionPolygons = _qt_property(object, lambda self: self._region_polygons(), notify=contentChanged)
    downloadUrls = _qt_property(
        object,
        lambda self: [str(job.get("url")) for job in self._download_jobs if job.get("url")],
        notify=contentChanged,
    )
    downloadProgress = _qt_property(int, lambda self: self._download_progress, notify=contentChanged)
    pipelinePhase = _qt_property(str, lambda self: self._pipeline_phase, notify=contentChanged)
    pipelineProgress = _qt_property(int, lambda self: self._pipeline_progress, notify=contentChanged)
    pipelinePhaseProgress = _qt_property(int, lambda self: self._pipeline_phase_progress, notify=contentChanged)
    pipelineLog = _qt_property(str, lambda self: "\n".join(self._pipeline_log), notify=contentChanged)
    pipelineRunning = _qt_property(bool, lambda self: self._pipeline_running, notify=contentChanged)
    elapsedText = _qt_property(
        str,
        lambda self: f"{self._elapsed_seconds // 60:02d}:{self._elapsed_seconds % 60:02d}",
        notify=contentChanged,
    )
    model2Defaults = _qt_property(object, lambda _self: load_parameter_defaults("model2"), notify=contentChanged)
    processingSettings = _qt_property(object, lambda self: self._processing_settings(), notify=contentChanged)

    @pyqtSlot(str, result=str)
    def text(self, key: str) -> str:
        return localizer.get_string(key)

    @pyqtSlot(str, result=str)
    def displayPath(self, path: str) -> str:
        return os.path.normpath(path) if path else "–"

    @pyqtSlot(str, result=str)
    def fileUrl(self, path: str) -> str:
        try:
            return Path(path).resolve().as_uri()
        except (OSError, ValueError):
            return ""

    @pyqtSlot(int, result=str)
    def formatBytes(self, value: int) -> str:
        return format_bytes(value, localizer.get_string("message_size_unknown"))

    @pyqtSlot()
    def switchLanguage(self) -> None:
        next_language = "de" if self._state.language == "en" else "en"
        localizer.set_language(next_language)
        self._state.set_language(next_language)
        self._settings.language = next_language
        save_app_settings(self._settings)
        self._save_project()
        self._emit_changed(content=True)

    @pyqtSlot()
    def goBack(self) -> None:
        target = previous_step_id(self._state.current_step)
        if target is not None:
            self._state.current_step = target
            self._save_project()
            self._emit_changed(content=True)

    @pyqtSlot()
    def goNextPrototype(self) -> None:  # compatibility with the former prototype API
        if int(self._state.current_step) < len(StepId):
            self._state.current_step = StepId(int(self._state.current_step) + 1)
            self._emit_changed(content=True)

    @pyqtSlot(int)
    def setCurrentStepPrototype(self, step_number: int) -> None:
        self._state.current_step = StepId(step_number)
        self._emit_changed(content=True)

    @pyqtSlot(str)
    def setWorkspacePathPrototype(self, workspace_path: str) -> None:
        self._state.set_project_context(_local_path(workspace_path))
        self._emit_changed(content=True)

    # --- project and geocoding ---------------------------------------------
    @pyqtSlot(str)
    def createProject(self, workspace_path: str) -> None:
        workspace_path = _local_path(workspace_path)
        if not workspace_path:
            return
        try:
            self._state.create_new_project(
                workspace_path,
                localizer.get_string("message_unknown_place"),
            )
            self._settings.last_workspace_path = workspace_path
            save_app_settings(self._settings)
            self._state.current_step = StepId.SEARCH
            self._save_project()
            self._set_status(localizer.get_string("status_project_folder_created", folder_path=self._state.project_path))
        except Exception as exc:
            self._show_error(localizer.get_string("error_project_folder_title"), str(exc))

    @pyqtSlot(str)
    def openProject(self, path: str) -> None:
        path = _local_path(path)
        if os.path.isdir(path):
            metadata = os.path.join(path, PROJECT_METADATA_FILENAME)
            if os.path.isfile(metadata):
                path = metadata
        try:
            self._state.load_project(path)
            localizer.set_language(self._state.language)
            self._settings.last_workspace_path = self._state.workspace_path
            self._settings.language = self._state.language
            save_app_settings(self._settings)
            self._restore_view_models()
            self._set_status(
                localizer.get_string(
                    "step1_status_project_loaded",
                    project_path=self._state.project_path,
                    step=int(self._state.current_step),
                )
            )
            self._emit_changed(content=True)
        except Exception as exc:
            self._show_error(localizer.get_string("step1_error_open_project_title"), str(exc))

    @pyqtSlot(str)
    def searchCity(self, city_name: str) -> None:
        city_name = city_name.strip()
        if not city_name:
            self._show_error(
                localizer.get_string("step1_error_input_missing_title"),
                localizer.get_string("step1_error_input_missing_message"),
            )
            return
        if "geocode" in self._threads:
            return
        self._state.begin_search(city_name)
        self._save_project()
        self._set_busy(True)
        self._set_status(localizer.get_string("step1_status_searching", city_name=city_name))
        if not QT_AVAILABLE:
            return
        from core.app.app_qml.workers import GeocodeWorker

        worker = GeocodeWorker(city_name)
        self._start_worker(
            "geocode",
            worker,
            worker.resultsReady,
            self._geocode_ready,
            worker.errorReady,
            self._geocode_error,
        )

    def _geocode_ready(self, locations: list) -> None:
        if not locations:
            self._show_info(
                localizer.get_string("step1_info_no_results_title"),
                localizer.get_string("step1_info_no_results_message", city_name=self._state.search_city),
            )
            return
        self._state.set_geocode_results(locations)
        self._state.current_step = StepId.CITY_SELECTION
        self._save_project()
        self._emit_changed(content=True)

    def _geocode_error(self, message: str) -> None:
        self._show_error(
            localizer.get_string("step1_error_geocoding_title"),
            localizer.get_string("step1_error_geocoding_message", error_details=message),
        )

    @pyqtSlot(int)
    def selectLocation(self, index: int) -> None:
        if 0 <= index < len(self._state.geocode_results):
            self._state.set_selected_location(self._state.geocode_results[index])
            self._set_status(str(getattr(self._state.selected_location, "address", "")))
            self._emit_changed(content=True)

    @pyqtSlot()
    def confirmLocation(self) -> None:
        if self._state.selected_location is None:
            self._show_error(
                localizer.get_string("step2_error_selection_missing_title"),
                localizer.get_string("step2_error_selection_missing_message"),
            )
            return
        self._state.current_step = StepId.GRID_AREA
        self._save_project()
        self._emit_changed(content=True)

    @pyqtSlot(float, float)
    def adjustLocation(self, latitude: float, longitude: float) -> None:
        current = self._state.selected_location
        self._state.set_selected_location(
            SimpleNamespace(
                latitude=float(latitude),
                longitude=float(longitude),
                address=getattr(current, "address", ""),
                raw=getattr(current, "raw", {}),
            )
        )
        self._save_project()
        self._emit_changed(content=True)

    # --- grid ---------------------------------------------------------------
    @pyqtSlot(int, float)
    def generateGrid(self, cell_size_m: int, radius_km: float) -> None:
        if cell_size_m <= 0 or radius_km <= 0:
            self._show_error(
                localizer.get_string("step3_error_invalid_params_title"),
                localizer.get_string("step3_error_invalid_params_message_positive"),
            )
            return
        count = estimate_grid_cell_count(cell_size_m, radius_km)
        if count > MAX_GRID_CELLS:
            self._show_error(
                localizer.get_string("step3_error_cell_limit_title"),
                localizer.get_string(
                    "step3_error_cell_limit_message",
                    approx_num_cells=int(count),
                    max_cells=MAX_GRID_CELLS,
                ),
            )
            return
        location = self._state.selected_location
        try:
            self._grid_cells = generate_grid_map_data(
                float(location.latitude),
                float(location.longitude),
                int(cell_size_m),
                float(radius_km),
            )
            self._selected_cells = create_selected_cells()
            self._grid_params = (int(cell_size_m), float(radius_km))
            self._set_status(localizer.get_string("message_grid_status_generating", count=len(self._grid_cells)))
            self._emit_changed(content=True)
        except Exception as exc:
            self._show_error(localizer.get_string("message_general_error_title"), str(exc))

    @pyqtSlot(float, float, str)
    def toggleGridCell(self, longitude: float, latitude: float, area_mode: str) -> None:
        if area_mode not in AREA_MODES or not self._grid_cells:
            return
        result = toggle_cell_at_lonlat(
            self._grid_cells,
            self._selected_cells,
            area_mode,
            float(longitude),
            float(latitude),
        )
        if result:
            self._emit_changed(content=True)

    @pyqtSlot()
    def confirmGrid(self) -> None:
        if not self._grid_params or not has_selected_cells(self._selected_cells):
            self._show_error(
                localizer.get_string("step3_error_no_areas_defined_title"),
                localizer.get_string("step3_error_no_areas_defined_message"),
            )
            return
        payload = build_step3_payload(
            self._state.selected_location,
            self._grid_params[0],
            self._grid_params[1],
            self._selected_cells,
            self._grid_cells,
        )
        try:
            self._state.set_step3_data(payload)
            if not self._state.project_path:
                self._state.create_new_project(
                    selected_location=self._state.selected_location,
                    unknown_place=localizer.get_string("message_unknown_place"),
                )
            self._state.current_step = StepId.PROJECT_PBF
            self._save_project()
            self._emit_changed(content=True)
        except Exception as exc:
            self._show_error(localizer.get_string("message_general_error_title"), str(exc))

    def _qml_grid_cells(self) -> list[dict[str, Any]]:
        result = []
        for cell in self._grid_cells:
            result.append(
                {
                    "id": int(cell["id"]),
                    "areaType": str(cell.get("area_type") or ""),
                    "path": _coordinates(cell["wgs84_coords_map"]),
                }
            )
        return result

    # --- PBF selection ------------------------------------------------------
    @pyqtSlot(int)
    def searchRegions(self, radius_km: int) -> None:
        if "regions" in self._threads or self._state.selected_location is None:
            return
        self._region_radius_km = max(1, int(radius_km))
        self._set_busy(True)
        self._set_status(localizer.get_string("step4_status_searching_pbf"))
        if not QT_AVAILABLE:
            return
        from core.app.app_qml.workers import RegionSearchWorker

        worker = RegionSearchWorker(self._state.selected_location, self._region_radius_km, self._geofabrik_index)
        self._start_worker(
            "regions",
            worker,
            worker.resultsReady,
            self._regions_ready,
            worker.errorReady,
            self._generic_error,
        )

    def _regions_ready(self, details: dict) -> None:
        self._available_regions = []
        self._regions_by_id = {}
        for country in details.get("pbfs") or []:
            self._append_region(country, 0)
            for region in country.get("sub_regions") or []:
                self._append_region(region, 1)
        self._set_status(localizer.get_string("step4_status_project_dir_set"))
        self._emit_changed(content=True)

    def _append_region(self, region: dict, depth: int) -> None:
        region_id = str(region.get("osm_id") or region.get("id") or "")
        if not region_id:
            return
        row = {
            "id": region_id,
            "name": str(region.get("name") or region_id),
            "depth": depth,
            "sizeBytes": int(region.get("size_bytes") or 0),
            "distanceKm": round(float(region.get("distance", region.get("min_distance", 0)) or 0), 1),
        }
        self._available_regions.append(row)
        self._regions_by_id[region_id] = dict(region)

    @pyqtSlot(str)
    def addRegion(self, region_id: str) -> None:
        region = self._regions_by_id.get(region_id)
        if region and add_download_job(self._download_jobs, region):
            self._verification = None
            self._emit_changed(content=True)

    @pyqtSlot(str)
    def removeRegion(self, region_id: str) -> None:
        self._download_jobs = remove_download_job(self._download_jobs, region_id)
        self._verification = None
        self._emit_changed(content=True)

    @pyqtSlot()
    def clearRegions(self) -> None:
        self._download_jobs = []
        self._verification = None
        self._emit_changed(content=True)

    def _selected_region_rows(self) -> list[dict[str, Any]]:
        found = {
            normalize_pbf_name(name): name
            for name in (getattr(self._verification, "found_names", []) or [])
        }
        rows = []
        for job in self._download_jobs:
            normalized = normalize_pbf_name(job.get("filename", ""))
            state = "checking"
            if self._verification is not None:
                state = "download"
                if normalized in found:
                    age = pbf_age_days(found[normalized])
                    state = "stale" if age is not None and age > DEFAULT_PBF_MAX_AGE_DAYS else "offline"
            rows.append(
                {
                    "id": str(job.get("osm_id") or ""),
                    "name": str(job.get("name") or ""),
                    "sizeBytes": int(job.get("bytes") or 0),
                    "availability": state,
                }
            )
        return rows

    def _region_polygons(self) -> list[dict[str, Any]]:
        features = {
            str(feature.get("properties", {}).get("id")): feature
            for feature in self._geofabrik_index.get("features", [])
        }
        polygons = []
        for job in self._download_jobs:
            feature = features.get(str(job.get("osm_id")))
            geometry = feature.get("geometry") if feature else None
            if not geometry:
                continue
            coords = geometry.get("coordinates") or []
            rings = [coords[0]] if geometry.get("type") == "Polygon" and coords else []
            if geometry.get("type") == "MultiPolygon":
                rings = [polygon[0] for polygon in coords if polygon]
            for ring in rings:
                polygons.append(
                    {
                        "name": str(job.get("name") or ""),
                        "path": [{"latitude": float(lat), "longitude": float(lon)} for lon, lat in ring],
                    }
                )
        location = self._state.selected_location
        if location is not None:
            circle = create_circle_polygon_coords(location.latitude, location.longitude, self._region_radius_km)
            polygons.append({"name": "search-radius", "path": _coordinates(circle)})
        return polygons

    @pyqtSlot()
    def verifyPbfFiles(self) -> None:
        if "verify" in self._threads or not self._download_jobs:
            return
        self._set_busy(True)
        if not QT_AVAILABLE:
            return
        from core.app.app_qml.workers import PbfVerifyWorker

        worker = PbfVerifyWorker(self._state.project_path, self._download_jobs, self._state.data_path)
        self._start_worker(
            "verify",
            worker,
            worker.resultsReady,
            self._verification_ready,
            worker.errorReady,
            self._generic_error,
        )

    def _verification_ready(self, verification) -> None:
        self._verification = verification
        if verification.all_found:
            self._set_status(localizer.get_string("step4_status_pbf_ready"))
        self._emit_changed(content=True)

    @pyqtSlot()
    def downloadPbfs(self) -> None:
        if "download" in self._threads or not self._download_jobs:
            return
        pending_jobs = pending_pbf_download_jobs(self._download_jobs, self._verification)
        if not pending_jobs:
            self._download_progress = 100
            self._set_status(localizer.get_string("step4_status_pbf_ready"))
            return
        self._set_busy(True)
        self._download_progress = 0
        self._active_download_count = len(pending_jobs)
        if not QT_AVAILABLE:
            return
        from core.app.app_qml.workers import PbfDownloadWorker

        worker = PbfDownloadWorker(pending_jobs, self._state.project_path, self._state.data_path)
        thread = self._start_worker(
            "download",
            worker,
            worker.errorReady,
            self._generic_error,
        )
        worker.progressReady.connect(self._download_progress_ready)
        worker.fileReady.connect(lambda *_args: self._emit_changed(content=True))
        worker.finished.connect(self._download_finished)
        return thread

    def _download_progress_ready(self, downloaded: int, total: int, _file: int, _files: int) -> None:
        self._download_progress = int(downloaded / max(1, total) * 100)
        self._emit_changed(content=True)

    def _download_finished(self, success: bool) -> None:
        if success:
            self._download_progress = 100
            self._set_status(
                localizer.get_string(
                    "step4_status_all_downloads_complete",
                    total_files=self._active_download_count,
                )
            )
            self.verifyPbfFiles()
        else:
            self._set_status(localizer.get_string("step4_download_interrupted"))
        self._active_download_count = 0

    @pyqtSlot()
    def stopDownload(self) -> None:
        worker = self._workers.get("download")
        if worker is not None:
            worker.stop()

    @pyqtSlot()
    def confirmPbfSelection(self) -> None:
        if self._verification is None or not self._verification.all_found:
            self._show_error(
                localizer.get_string("step4_verify_files_missing_title"),
                localizer.get_string("step4_verify_files_missing_message"),
            )
            return
        pbf_references = build_pbf_references(self._download_jobs, self._verification.found_files)
        payload = dict(self._state.step3_data)
        payload.update(
            {
                "workspace_path": self._state.workspace_path,
                "project_path": self._state.project_path,
                "data_path": self._state.data_path or workspace_data_dir(self._state.workspace_path),
                "download_jobs": self._download_jobs,
                "user_pbf_path": self._verification.user_pbf_path,
                "pbf_references": pbf_references,
            }
        )
        try:
            self._state.set_step4_data(payload)
            self._state.current_step = StepId.PROCESSING
            self._save_project()
            self._emit_changed(content=True)
            self.startPreprocessing()
        except Exception as exc:
            self._show_error(localizer.get_string("message_general_error_title"), str(exc))

    # --- processing ---------------------------------------------------------
    @pyqtSlot(str, bool, str, str, object, bool, result=object)
    def processingBlockers(
        self,
        reference_path: str,
        no_reference: bool,
        reference_field: str,
        census_path: str,
        _parameters: object,
        _unused: bool = False,
    ) -> list[str]:
        blockers = pipeline_readiness(self._state.project_path, self._state.step4_data)
        reference_path = _local_path(reference_path)
        census_path = _local_path(census_path)
        if not no_reference:
            if not reference_path:
                blockers.append("Referenz-Polygonlayer wählen oder Verzicht aktivieren")
            elif not os.path.isfile(reference_path):
                blockers.append(f"Referenz-Polygonlayer fehlt: {reference_path}")
            if not reference_field.strip():
                blockers.append("Einwohnerfeld fehlt")
        if census_path and not os.path.isfile(census_path):
            blockers.append(f"Zensusraster fehlt: {census_path}")
        return blockers

    @pyqtSlot(str, str, str, bool, object, bool)
    def startPipeline(
        self,
        reference_path: str,
        reference_field: str,
        census_path: str,
        no_reference: bool,
        parameters: object,
        force_restart: bool,
    ) -> None:
        if self._pipeline_running:
            return
        blockers = self.processingBlockers(
            reference_path,
            no_reference,
            reference_field,
            census_path,
            parameters,
        )
        if blockers:
            self._show_error("Nicht bereit", "• " + "\n• ".join(blockers))
            return
        options = {
            "model2_parameters": {
                key: int(dict(parameters).get(key, load_parameter_defaults("model2")[key]))
                for key in MODEL2_FIELDS
            },
            "pop_local": _local_path(reference_path) or None,
            "pop_local_field": reference_field.strip() or "POP",
            "custom_census": _local_path(census_path) or None,
            "no_local_reference": bool(no_reference),
            "force_restart_models": bool(force_restart),
        }
        self._start_pipeline_worker(options, phase_a_only=False)

    def _processing_settings(self) -> dict[str, Any]:
        input_dir = Path(self._state.project_path) / "input"
        temp_dir = Path(self._state.project_path) / "temp"
        population = input_dir / "pop_local.gpkg"
        population_field = input_dir / "pop_local_fieldname.txt"
        census = input_dir / "custom_census.tif"
        dummy_population = temp_dir / "dummy_pop_local.gpkg"
        model2_params = dict(load_parameter_defaults("model2"))
        m2_path = input_dir / "Model2_params.json"
        if m2_path.is_file():
            try:
                model2_params.update(json.loads(m2_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass

        model3_params = dict(load_parameter_defaults("model3"))
        m3_path = input_dir / "Model3_params.json"
        if m3_path.is_file():
            try:
                model3_params.update(json.loads(m3_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass

        model5_params = dict(load_parameter_defaults("model5"))
        m5_path = input_dir / "Model5_params.json"
        if m5_path.is_file():
            try:
                model5_params.update(json.loads(m5_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass

        field_name = "POP"
        if population_field.is_file():
            try:
                field_name = population_field.read_text(encoding="utf-8").strip() or "POP"
            except OSError:
                pass
        return {
            "referencePath": str(population) if population.is_file() else "",
            "referenceField": field_name,
            "censusPath": str(census) if census.is_file() else "",
            "noReference": dummy_population.is_file() and not population.is_file(),
            "parameters": model2_params,
            "model2Parameters": model2_params,
            "model3Parameters": model3_params,
            "model5Parameters": model5_params,
        }

    @pyqtSlot()
    def startPreprocessing(self) -> None:
        if self._pipeline_running:
            return
        blockers = pipeline_readiness(self._state.project_path, self._state.step4_data)
        blockers = [
            item
            for item in blockers
            if item.startswith(("Projekt", "Keine PBF", "PBF fehlt", "Mindestens eine PA"))
        ]
        if blockers:
            self._append_log("OSM-Phase A wartet: " + "; ".join(blockers))
            return
        reusable = UrbanActPipeline(self._state.project_path, self._state.step4_data).reusable_phase_a_outputs()
        if reusable is not None:
            self._pipeline_phase = "OSM-Vorverarbeitung bereits vorhanden"
            self._pipeline_phase_progress = 100
            self._append_log("Vorhandene OSM-Phase-A-Dateien werden wiederverwendet.")
            return
        self._start_pipeline_worker({}, phase_a_only=True)

    def _start_pipeline_worker(self, options: dict, *, phase_a_only: bool) -> None:
        if not QT_AVAILABLE or "pipeline" in self._threads:
            return
        from core.app.app_qml.workers import PipelineWorker

        worker = PipelineWorker(
            UrbanActPipeline(self._state.project_path, self._state.step4_data),
            options,
            phase_a_only=phase_a_only,
        )
        self._pipeline_running = True
        self._elapsed_seconds = 0
        self._pipeline_phase_progress = 0
        if self._timer is not None:
            self._timer.start(1000)
        self._start_worker(
            "pipeline",
            worker,
            worker.errorReady,
            self._pipeline_error,
        )
        worker.phaseStarted.connect(self._pipeline_phase_started)
        worker.phaseProgress.connect(self._pipeline_phase_progress_ready)
        worker.phaseDetail.connect(lambda text: self._set_pipeline_detail(text))
        worker.logReady.connect(self._append_log)
        worker.outputReady.connect(lambda key, path: self._append_log(f"{key}: {path}"))
        worker.finished.connect(lambda _result: self._pipeline_finished(phase_a_only))
        self._emit_changed(content=True)

    @pyqtSlot()
    def stopPipeline(self) -> None:
        worker = self._workers.get("pipeline")
        if worker is not None:
            worker.stop()

    def _pipeline_phase_started(self, name: str, index: int, total: int) -> None:
        self._pipeline_phase = name
        self._pipeline_progress = int((index - 1) / max(1, total) * 100)
        self._pipeline_phase_progress = 0
        self._emit_changed(content=True)

    def _pipeline_phase_progress_ready(self, value: int) -> None:
        self._pipeline_phase_progress = int(value)
        self._emit_changed(content=True)

    def _set_pipeline_detail(self, detail: str) -> None:
        text = " ".join(str(detail).split())
        if text:
            self._pipeline_phase = f"{self._pipeline_phase} · {text}"
            self._emit_changed(content=True)

    def _append_log(self, text: str) -> None:
        self._pipeline_log.append(str(text))
        self._pipeline_log = self._pipeline_log[-2000:]
        self._emit_changed(content=True)

    def _pipeline_finished(self, phase_a_only: bool) -> None:
        self._pipeline_phase_progress = 100
        if phase_a_only:
            self._pipeline_phase = "OSM-Vorverarbeitung abgeschlossen"
        else:
            self._pipeline_progress = 100
            self._pipeline_phase = "Abgeschlossen"
        self._emit_changed(content=True)

    def _pipeline_error(self, phase: str, message: str) -> None:
        self._pipeline_phase = f"Fehler: {phase}"
        self._append_log(f"FEHLER [{phase}]: {message}")
        self._show_error(f"Fehler in {phase}", message)

    def _tick_elapsed(self) -> None:
        self._elapsed_seconds += 1
        self._emit_changed(content=True)

    # --- threading and persistence -----------------------------------------
    def _start_worker(self, name: str, worker, *connections):
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        for signal, callback in zip(connections[0::2], connections[1::2]):
            signal.connect(callback)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda name=name: self._worker_finished(name))
        self._threads[name] = thread
        self._workers[name] = worker
        thread.start()
        return thread

    def _worker_finished(self, name: str) -> None:
        self._threads.pop(name, None)
        self._workers.pop(name, None)
        if name == "pipeline":
            self._pipeline_running = False
            if self._timer is not None:
                self._timer.stop()
        self._set_busy(bool(self._threads))
        self._emit_changed(content=True)

    def _set_busy(self, value: bool) -> None:
        self._busy = bool(value)
        self._emit_changed()

    def _generic_error(self, message: str) -> None:
        self._show_error(localizer.get_string("message_general_error_title"), message)

    def _save_project(self) -> None:
        if self._state.has_project_context():
            try:
                self._state.save_project_metadata()
            except Exception:
                pass
