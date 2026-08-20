"""Qt implementation of step 4: project folder and geodata setup (GADM, POP & OSM PBF)."""

from __future__ import annotations

import os
import time
import webbrowser
from pathlib import Path
from threading import Event
from typing import Optional
from urllib.parse import quote

from core.locales import localizer
from core.app.app_core.formatting import format_bytes, format_eta
from core.app.app_core.geo import create_circle_polygon_coords
from core.app.app_core.geofabrik import (
    find_pbf_details,
    download_geofabrik_index,
    get_geofabrik_index_info,
    get_cached_pbf_details,
    clear_pbf_details_cache,
)
from core.app.app_core.download_files import check_global_datasets, DEFAULT_BASE_URL
from core.app.app_core.project import (
    DEFAULT_PBF_MAX_AGE_DAYS,
    USER_AGENT,
    add_download_job,
    build_project_metadata,
    build_pbf_references,
    build_step4_payload,
    download_jobs_from_pbf_references,
    download_pbf_jobs,
    normalize_pbf_name,
    pending_pbf_download_jobs,
    pbf_age_days,
    remove_download_job,
    save_project_metadata,
    total_download_size,
    verify_pbf_files,
    workspace_data_dir,
    tool_root,
)
from core.app.app_qt.map_preview import OsmAttributionOverlay, create_high_quality_osm_layer, setup_map_canvas
from core.app.app_qt.qt_base import (
    AnimatedProgressBar,
    Dialogs,
    Qt,
    QtCore,
    QtGui,
    QtWidgets,
    app_font,
    create_step_header,
    escape_mnemonic,
    pyqtSignal,
    qfont_bold,
    qt_enum,
    require_qgis_qt,
)


try:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsFillSymbol,
        QgsGeometry,
        QgsPointXY,
        QgsProject,
        QgsRasterLayer,
        QgsRectangle,
        QgsVectorLayer,
    )
    from qgis.gui import QgsMapCanvas, QgsVertexMarker

    QGIS_STEP2_MAP_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only hit outside QGIS Python.
    QgsCoordinateReferenceSystem = QgsFeature = QgsFillSymbol = QgsGeometry = None
    QgsPointXY = QgsProject = QgsRasterLayer = QgsRectangle = QgsVectorLayer = None
    QgsMapCanvas = QgsVertexMarker = None
    QGIS_STEP2_MAP_IMPORT_ERROR = exc


_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
_Q_OBJECT_BASE = QtCore.QObject if QtCore is not None else object

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def format_gb(size_bytes) -> str:
    """Always formats a size as XX,XX (GB value, comma decimal, no unit suffix for table cells)."""
    if size_bytes is None or size_bytes == 0:
        return "0,00"
    gb = size_bytes / (1024 ** 3)
    return f"{gb:,.2f}".replace(".", "X").replace(",", ".").replace("X", ",")


def format_gb_label(size_bytes) -> str:
    """Formats total size as 'XX,XX GB' for the footer label."""
    return format_gb(size_bytes) + " GB"


def availability_for_job(job: dict, verification, *, now=None) -> str:
    """Return a stable availability state for one selected PBF job."""
    if verification is None:
        return "checking"
    normalized_name = normalize_pbf_name(job.get("filename", ""))
    found_names = {
        normalize_pbf_name(name): name
        for name in verification.found_names
    }
    found_name = found_names.get(normalized_name)
    if found_name is None:
        return "download"
    age_days = pbf_age_days(found_name, now=now)
    if age_days is not None and age_days > DEFAULT_PBF_MAX_AGE_DAYS:
        return "stale"
    return "offline"


def geojson_bounds(geometry: Optional[dict]) -> Optional[tuple[float, float, float, float]]:
    """Return ``(min_lon, min_lat, max_lon, max_lat)`` for GeoJSON geometry."""
    if not geometry:
        return None
    points = []

    def collect(value) -> None:
        if not isinstance(value, (list, tuple)):
            return
        if len(value) >= 2 and all(isinstance(coordinate, (int, float)) for coordinate in value[:2]):
            points.append((float(value[0]), float(value[1])))
            return
        for child in value:
            collect(child)

    collect(geometry.get("coordinates"))
    if not points:
        return None
    longitudes, latitudes = zip(*points)
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


class BusySpinner(_Q_WIDGET_BASE):
    """Small native Qt spinner used while Geofabrik data is fetched."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(22, 22)
        self.hide()

    def start(self) -> None:
        self.show()
        self._timer.start(80)

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(qt_enum(QtGui.QPainter, "Antialiasing", "RenderHint"))
            painter.translate(self.width() / 2, self.height() / 2)
            painter.rotate(self._angle)
            for index in range(12):
                color = QtGui.QColor("#2563eb")
                color.setAlpha(40 + index * 18)
                pen = QtGui.QPen(color, 2.2)
                pen.setCapStyle(qt_enum(Qt, "RoundCap", "PenCapStyle"))
                painter.setPen(pen)
                painter.drawLine(0, -5, 0, -9)
                painter.rotate(30)
        finally:
            painter.end()


class Step4MapPreview(_Q_WIDGET_BASE):
    """QGIS preview of the search radius and selected Geofabrik regions."""

    def __init__(self, localizer, location, geofabrik_index: dict, parent=None):
        if QGIS_STEP2_MAP_IMPORT_ERROR is not None:
            raise RuntimeError(f"QGIS step 4 map imports failed: {QGIS_STEP2_MAP_IMPORT_ERROR}")
        super().__init__(parent)
        self.localizer = localizer
        self.location = location
        self.geofabrik_index = geofabrik_index or {}
        self.osm_layer = None
        self.radius_layer = None
        self.regions_layer = None
        self.canvas = None
        self.marker = None
        self.radius_km = 250
        self._build_ui()
        self.update_preview([], self.radius_km)

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Title is rendered by the outer panel - no duplicate label here

        self.canvas = QgsMapCanvas(self)
        self.canvas.setMinimumWidth(260)
        setup_map_canvas(self.canvas)
        self.osm_layer = create_high_quality_osm_layer("OpenStreetMap")
        if self.osm_layer and self.osm_layer.isValid():
            QgsProject.instance().addMapLayer(self.osm_layer, False)

        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setColor(QtGui.QColor("#dc2626"))
        self.marker.setIconSize(14)
        self.marker.setPenWidth(3)
        if hasattr(QgsVertexMarker, "ICON_CIRCLE"):
            self.marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
        layout.addWidget(self.canvas, 1)
        self.attribution_overlay = OsmAttributionOverlay(self.canvas)

    def update_preview(self, jobs: list[dict], radius_km: int) -> None:
        self.radius_km = radius_km
        lat = getattr(self.location, "latitude", None)
        lon = getattr(self.location, "longitude", None)
        if lat is None or lon is None:
            return
        lat, lon = float(lat), float(lon)
        circle = create_circle_polygon_coords(lat, lon, radius_km, num_segments=72)
        self.radius_layer = self._build_radius_layer(circle)

        features_by_id = {
            feature.get("properties", {}).get("id"): feature
            for feature in self.geofabrik_index.get("features", [])
            if feature.get("properties", {}).get("id")
        }
        selected_geometries = [
            features_by_id[job.get("osm_id")].get("geometry")
            for job in jobs
            if job.get("osm_id") in features_by_id and features_by_id[job.get("osm_id")].get("geometry")
        ]
        self.regions_layer = self._build_regions_layer(selected_geometries)
        layers = [
            layer
            for layer in (self.regions_layer, self.radius_layer, self.osm_layer)
            if layer is not None and layer.isValid()
        ]
        self.canvas.setLayers(layers)

        x, y = self._web_mercator(lon, lat)
        self.marker.setCenter(QgsPointXY(x, y))
        self.marker.show()
        bounds = self._combined_bounds(selected_geometries, circle)
        self.canvas.setExtent(self._mercator_extent(bounds))
        self.canvas.refresh()

    def _build_radius_layer(self, circle: list[tuple[float, float]]):
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Search radius", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(lon, lat) for lat, lon in circle]]))
        layer.dataProvider().addFeature(feature)
        layer.updateExtents()
        symbol = QgsFillSymbol.createSimple(
            {"color": "37,99,235,15", "outline_color": "37,99,235,220", "outline_width": "0.8"}
        )
        layer.renderer().setSymbol(symbol)
        return layer

    def _build_regions_layer(self, geometries: list[dict]):
        layer = QgsVectorLayer("MultiPolygon?crs=EPSG:4326", "Selected OSM regions", "memory")
        features = []
        for geometry in geometries:
            feature = QgsFeature()
            qgis_geometry = self._qgis_geometry(geometry)
            if qgis_geometry is None:
                continue
            feature.setGeometry(qgis_geometry)
            features.append(feature)
        if features:
            layer.dataProvider().addFeatures(features)
        layer.updateExtents()
        symbol = QgsFillSymbol.createSimple(
            {"color": "51,102,204,70", "outline_color": "20,41,82,240", "outline_width": "0.8"}
        )
        layer.renderer().setSymbol(symbol)
        return layer

    @staticmethod
    def _qgis_geometry(geometry: dict):
        def ring_points(ring):
            return [QgsPointXY(float(lon), float(lat)) for lon, lat, *_rest in ring]

        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates") or []
        if geometry_type == "Polygon":
            polygon = [ring_points(ring) for ring in coordinates if ring]
            return QgsGeometry.fromMultiPolygonXY([polygon]) if polygon else None
        if geometry_type == "MultiPolygon":
            polygons = [
                [ring_points(ring) for ring in polygon if ring]
                for polygon in coordinates
                if polygon
            ]
            return QgsGeometry.fromMultiPolygonXY(polygons) if polygons else None
        return None

    @staticmethod
    def _combined_bounds(geometries: list[dict], circle: list[tuple[float, float]]):
        bounds = [geojson_bounds(geometry) for geometry in geometries]
        bounds = [bound for bound in bounds if bound is not None]
        circle_lons = [lon for lat, lon in circle]
        circle_lats = [lat for lat, lon in circle]
        bounds.append((min(circle_lons), min(circle_lats), max(circle_lons), max(circle_lats)))
        return (
            min(bound[0] for bound in bounds),
            min(bound[1] for bound in bounds),
            max(bound[2] for bound in bounds),
            max(bound[3] for bound in bounds),
        )

    @classmethod
    def _mercator_extent(cls, bounds):
        min_lon, min_lat, max_lon, max_lat = bounds
        min_x, min_y = cls._web_mercator(min_lon, min_lat)
        max_x, max_y = cls._web_mercator(max_lon, max_lat)
        padding_x = max((max_x - min_x) * 0.08, 1000)
        padding_y = max((max_y - min_y) * 0.08, 1000)
        return QgsRectangle(min_x - padding_x, min_y - padding_y, max_x + padding_x, max_y + padding_y)

    @staticmethod
    def _web_mercator(lon: float, lat: float) -> tuple[float, float]:
        from core.app.app_qt.map_preview import lonlat_to_web_mercator

        return lonlat_to_web_mercator(lon, lat)


class PbfSearchWorker(_Q_OBJECT_BASE):
    log_ready = pyqtSignal(str) if pyqtSignal is not None else None
    results_ready = pyqtSignal(object) if pyqtSignal is not None else None
    error_ready = pyqtSignal(str) if pyqtSignal is not None else None
    finished = pyqtSignal() if pyqtSignal is not None else None

    def __init__(self, location, radius_km: int, geofabrik_index: dict, force_refresh: bool = False):
        super().__init__()
        self.location = location
        self.radius_km = radius_km
        self.geofabrik_index = geofabrik_index
        self.force_refresh = force_refresh
        self._cancel_event = Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        def log_adapter(msg: str) -> None:
            if self.log_ready:
                from core.app.app_core.logging import format_pando_log
                self.log_ready.emit(format_pando_log(msg))

        try:
            details = find_pbf_details(
                self.location,
                self.radius_km,
                self.geofabrik_index,
                USER_AGENT,
                force_refresh=self.force_refresh,
                is_cancelled=self._cancel_event.is_set,
                log=log_adapter,
            )
            if self._cancel_event.is_set():
                return
            if details and "error" in details:
                self.error_ready.emit(str(details["error"]))
            else:
                self.results_ready.emit(details or {"pbfs": []})
        except Exception as exc:
            self.error_ready.emit(str(exc))
        finally:
            self.finished.emit()


class PbfDownloadWorker(_Q_OBJECT_BASE):
    # (filename, file_dl, file_total, file_idx, total_files, cum_dl, cum_total)
    # Byte counts use 'object' to avoid 32-bit C++ int overflow for files > 2 GB
    progress_ready = pyqtSignal(str, object, object, int, int, object, object) if pyqtSignal is not None else None
    file_ready = pyqtSignal(str, int, int) if pyqtSignal is not None else None
    error_ready = pyqtSignal(str) if pyqtSignal is not None else None
    finished = pyqtSignal(bool) if pyqtSignal is not None else None

    def __init__(self, jobs: list[dict], project_path: str, data_path: str):
        super().__init__()
        self.jobs = jobs
        self.project_path = project_path
        self.data_path = data_path
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def run(self) -> None:
        try:
            success = download_pbf_jobs(
                self.jobs,
                self.project_path,
                data_path=self.data_path,
                is_stopped=lambda: self._stopped,
                on_progress=self.progress_ready.emit,
                on_file_ready=self.file_ready.emit,
            )
            self.finished.emit(success)
        except Exception as exc:
            self.error_ready.emit(str(exc))
            self.finished.emit(False)


class PbfVerifyWorker(_Q_OBJECT_BASE):
    results_ready = pyqtSignal(object) if pyqtSignal is not None else None
    error_ready = pyqtSignal(str) if pyqtSignal is not None else None
    finished = pyqtSignal() if pyqtSignal is not None else None

    def __init__(self, project_path: str, jobs: list[dict], data_path: str):
        super().__init__()
        self.project_path = project_path
        self.jobs = jobs
        self.data_path = data_path

    def run(self) -> None:
        try:
            self.results_ready.emit(verify_pbf_files(self.project_path, self.jobs, data_path=self.data_path))
        except Exception as exc:
            self.error_ready.emit(str(exc))
        finally:
            self.finished.emit()


class GlobalDataDownloadWorker(_Q_OBJECT_BASE):
    """Background worker for downloading GADM & GHS-POP global datasets."""

    progress_updated = pyqtSignal(str, int) if pyqtSignal is not None else None
    # (filename, file_dl, file_total, file_idx, total_files, cum_dl, cum_total)
    # Byte counts use 'object' to avoid 32-bit C++ int overflow for files > 2 GB
    detail_progress = pyqtSignal(str, object, object, int, int, object, object) if pyqtSignal is not None else None
    finished = pyqtSignal(bool, str) if pyqtSignal is not None else None

    def __init__(self, base_url: str, data_root: Path):
        super().__init__()
        self.base_url = base_url
        self.data_root = data_root
        self._cancelled = False

    def stop(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        from core.app.app_core.download_files import process_downloads

        def progress_cb(filename: str, downloaded: int, total: int, percent: float) -> None:
            if self.progress_updated:
                if total > 0:
                    mb_dl = downloaded / (1024 * 1024)
                    mb_tot = total / (1024 * 1024)
                    msg = localizer.get_string(
                        "step2_status_downloading_file_progress",
                        filename=filename,
                        percent=f"{percent:.1f}",
                        mb_dl=f"{mb_dl:.1f}",
                        mb_tot=f"{mb_tot:.1f}",
                        default=f"Lade {filename}: {percent:.1f}% ({mb_dl:.1f}/{mb_tot:.1f} MB)"
                    )
                else:
                    msg = localizer.get_string(
                        "step2_status_downloading_file",
                        filename=filename,
                        default=f"Lade {filename}..."
                    )
                self.progress_updated.emit(msg, int(percent))

        def detail_cb(filename, file_dl, file_total, file_idx, total_files, cum_dl, cum_total):
            if self.detail_progress:
                self.detail_progress.emit(filename, file_dl, file_total, file_idx, total_files, cum_dl, cum_total)

        try:
            process_downloads(
                base_url=self.base_url,
                output_dir=self.data_root,
                progress_callback=progress_cb,
                cancel_check=lambda: self._cancelled,
                detail_progress_callback=detail_cb,
            )
            if self.finished:
                if self._cancelled:
                    self.finished.emit(False, localizer.get_string("step2_status_download_cancelled", default="Download abgebrochen."))
                else:
                    self.finished.emit(True, localizer.get_string("step2_status_global_data_downloaded", default="Alle POP & GADM Daten erfolgreich heruntergeladen."))
        except Exception as exc:
            if self.finished:
                self.finished.emit(False, str(exc))


class IndexUpdateWorker(_Q_OBJECT_BASE):
    """Worker for fetching updated Geofabrik index in background."""

    finished = pyqtSignal(bool, object) if pyqtSignal is not None else None

    def __init__(self, target_path: Path):
        super().__init__()
        self.target_path = target_path

    def run(self) -> None:
        try:
            index_data = download_geofabrik_index(self.target_path)
            if self.finished:
                self.finished.emit(True, index_data)
        except Exception as exc:
            if self.finished:
                self.finished.emit(False, str(exc))


class Step2DataWidget(_Q_WIDGET_BASE):
    """Creates the project folder and prepares geodata inputs (GADM, POP & OSM PBF)."""

    status_changed = pyqtSignal(str) if pyqtSignal is not None else None
    confirmed = pyqtSignal(dict) if pyqtSignal is not None else None

    def __init__(
        self,
        localizer,
        step3_data: dict,
        geofabrik_index: Optional[dict] = None,
        parent: Optional[object] = None,
    ):
        require_qgis_qt()
        if QGIS_STEP2_MAP_IMPORT_ERROR is not None:
            raise RuntimeError(f"QGIS step 4 map imports failed: {QGIS_STEP2_MAP_IMPORT_ERROR}")
        super().__init__(parent)
        self.localizer = localizer
        self.step3_data = step3_data
        self.selected_location = step3_data.get("selected_loc")
        self.geofabrik_index = geofabrik_index or {}
        self.project_path = step3_data.get("project_path")
        self.workspace_path = step3_data.get("workspace_path") or (os.path.dirname(self.project_path) if self.project_path else None)
        self.data_path = step3_data.get("data_path") or (workspace_data_dir(self.workspace_path) if self.workspace_path else None)
        self.download_jobs = list(step3_data.get("download_jobs", []))
        self.user_pbf_path = step3_data.get("user_pbf_path")
        self.pbf_references = list(step3_data.get("pbf_references", []))
        if not self.download_jobs and self.pbf_references:
            self.download_jobs = download_jobs_from_pbf_references(self.pbf_references)
        self.pbf_details = None
        self._last_verification = None
        self._last_verified_job_key = None
        self._verify_after_finish = False
        self._verification_job_key = None
        self._verify_restart_requested = False
        self._verify_thread = None
        self._verify_worker = None
        self._search_thread = None
        self._search_worker = None
        self._search_cancelled = False
        self._download_thread = None
        self._download_worker = None
        self._download_active = False
        self._active_download_count = 0
        self._global_download_thread = None
        self._global_download_worker = None
        self._index_update_thread = None
        self._index_update_worker = None

        self.project_label = None
        self.create_project_button = None
        self.open_project_button = None
        self.content_splitter = None
        self.splitter = None
        self.move_layout = None
        self.add_button = None
        self.remove_button = None
        self.radius_edit = None
        self.search_button = None
        self.search_spinner = None
        self.search_spinner_label = None
        self.available_tree = None
        self.selected_tree = None
        self.map_preview = None
        self.total_size_label = None
        self.download_button = None
        self.stop_button = None
        self.browser_button = None
        self.check_button = None
        self.progress_bar = None
        self.pbf_file_progress_bar = None
        self.pbf_file_progress_label = None
        self.pbf_overall_progress_label = None
        self.global_download_button = None
        self.global_stop_button = None
        self.global_browser_button = None
        self.global_check_button = None
        self.global_progress_bar = None
        self.global_file_progress_bar = None
        self.global_file_progress_label = None
        self.global_overall_progress_label = None
        self.global_status_label = None
        self.index_status_label = None
        self.index_update_button = None
        self.global_box = None
        self.global_container = None
        self.global_toggle_btn = None
        self.pbf_box = None
        self.pbf_container = None
        self.pbf_toggle_btn = None
        self.next_button = None
        self.interactive_widgets = []

        # Download timing state for ETA calculations
        self._global_download_start_time = None
        self._pbf_download_start_time = None

        self._build_ui()
        self._set_project_ready(bool(self.project_path))
        self.check_global_datasets_status(show_feedback=False)
        self.check_geofabrik_index_freshness()
        if self.project_path:
            self._refresh_selected_tree()
            self.start_pbf_search()
            self.check_pbf_files(show_feedback=False)

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(16, 10, 16, 10)
        root_layout.setSpacing(8)

        # Title: Top Left (Fixed position)
        header, _title_label, _step_label = create_step_header(
            self.localizer.get_string("step2_title", default="Schritt 2: Projektordner und Geodaten-Setup"),
            current_step=2,
            total_steps=6,
            localizer=self.localizer,
            parent=self,
        )
        root_layout.addWidget(header)

        # Header Box: Project Information
        dir_box = QtWidgets.QGroupBox(self.localizer.get_string("step2_header_project_info", default="Projekt-Informationen"), self)
        dir_layout = QtWidgets.QGridLayout(dir_box)

        location_name = getattr(self.selected_location, "address", "")
        if location_name:
            location_label = QtWidgets.QLabel(
                self.localizer.get_string("step2_location_info", location_name=location_name),
                dir_box,
            )
            location_label.setWordWrap(True)
            dir_layout.addWidget(location_label, 0, 0, 1, 2)

        self.create_project_button = QtWidgets.QPushButton(self.localizer.get_string("step2_button_create_project_dir"))
        self.create_project_button.hide()

        self.open_project_button = QtWidgets.QPushButton(self.localizer.get_string("step2_button_open_folder"), dir_box)
        self.open_project_button.clicked.connect(self.open_project_folder)
        dir_layout.addWidget(self.open_project_button, 1, 0)

        self.project_label = QtWidgets.QLabel(self.localizer.get_string("step2_label_project_dir_not_set"), dir_box)
        self.project_label.setWordWrap(True)
        dir_layout.addWidget(self.project_label, 1, 1)
        dir_layout.setColumnStretch(1, 1)
        root_layout.addWidget(dir_box)

        # Scroll Area for Accordion Box 1 & Box 2
        scroll_area = QtWidgets.QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))

        scroll_widget = QtWidgets.QWidget(scroll_area)
        controls_layout = QtWidgets.QVBoxLayout(scroll_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(12)

        # -------------------------------------------------------------
        # Box 1: Globale Geodaten (POP & GADM) - Accordion (1 line collapsed)
        # -------------------------------------------------------------
        self.global_box = QtWidgets.QWidget(scroll_widget)
        global_box_layout = QtWidgets.QVBoxLayout(self.global_box)
        global_box_layout.setContentsMargins(0, 0, 0, 0)
        global_box_layout.setSpacing(4)

        self.global_toggle_btn = QtWidgets.QPushButton(
            escape_mnemonic("▼ " + self.localizer.get_string("step2_accordion_global")), self.global_box
        )
        self.global_toggle_btn.setFlat(False)
        self.global_toggle_btn.clicked.connect(self.toggle_global_box)
        global_box_layout.addWidget(self.global_toggle_btn)

        self.global_container = QtWidgets.QWidget(self.global_box)
        self.global_container.setMaximumHeight(16777215)
        global_layout = QtWidgets.QVBoxLayout(self.global_container)
        global_layout.setContentsMargins(4, 4, 4, 4)

        self.global_status_label = QtWidgets.QLabel(
            self.localizer.get_string("step2_status_checking_datasets"),
            self.global_container,
        )
        self.global_status_label.setWordWrap(True)
        global_layout.addWidget(self.global_status_label)

        global_button_row = QtWidgets.QHBoxLayout()
        self.global_download_button = QtWidgets.QPushButton(
            self.localizer.get_string("step2_button_download", default="Herunterladen"),
            self.global_container,
        )
        self.global_download_button.clicked.connect(self.start_global_download)
        global_button_row.addWidget(self.global_download_button)

        self.global_stop_button = QtWidgets.QPushButton(
            self.localizer.get_string("button_stop_download", default="Stopp"),
            self.global_container,
        )
        self.global_stop_button.clicked.connect(self.stop_global_download)
        self.global_stop_button.hide()
        global_button_row.addWidget(self.global_stop_button)

        self.global_browser_button = QtWidgets.QPushButton(
            self.localizer.get_string("step2_button_open_browser", default="Im Browser öffnen"),
            self.global_container,
        )
        self.global_browser_button.clicked.connect(self.open_global_browser_url)
        global_button_row.addWidget(self.global_browser_button)

        self.global_check_button = QtWidgets.QPushButton(
            self.localizer.get_string("step2_button_check_status", default="Status prüfen"),
            self.global_container,
        )
        self.global_check_button.clicked.connect(lambda: self.check_global_datasets_status(show_feedback=True))
        global_button_row.addWidget(self.global_check_button)
        global_button_row.addStretch(1)
        global_layout.addLayout(global_button_row)

        self.global_file_progress_label = QtWidgets.QLabel("", self.global_container)
        self.global_file_progress_label.setWordWrap(True)
        global_layout.addWidget(self.global_file_progress_label)

        self.global_file_progress_bar = AnimatedProgressBar(self.global_container)
        self.global_file_progress_bar.setRange(0, 100)
        self.global_file_progress_bar.setValue(0)
        global_layout.addWidget(self.global_file_progress_bar)

        self.global_overall_progress_label = QtWidgets.QLabel("", self.global_container)
        self.global_overall_progress_label.setWordWrap(True)
        global_layout.addWidget(self.global_overall_progress_label)

        self.global_progress_bar = AnimatedProgressBar(self.global_container)
        self.global_progress_bar.setRange(0, 100)
        self.global_progress_bar.setValue(0)
        global_layout.addWidget(self.global_progress_bar)

        global_box_layout.addWidget(self.global_container)
        controls_layout.addWidget(self.global_box)

        # -------------------------------------------------------------
        # Box 2: OSM-Regionen auswählen & herunterladen (Geofabrik) - Accordion
        # -------------------------------------------------------------
        self.pbf_box = QtWidgets.QWidget(scroll_widget)
        pbf_box_layout = QtWidgets.QVBoxLayout(self.pbf_box)
        pbf_box_layout.setContentsMargins(0, 0, 0, 0)
        pbf_box_layout.setSpacing(4)

        self.pbf_toggle_btn = QtWidgets.QPushButton(
            escape_mnemonic("▼ " + self.localizer.get_string("step2_accordion_osm")), self.pbf_box
        )
        self.pbf_toggle_btn.setFlat(False)
        self.pbf_toggle_btn.clicked.connect(self.toggle_pbf_box)
        pbf_box_layout.addWidget(self.pbf_toggle_btn)

        self.pbf_container = QtWidgets.QWidget(self.pbf_box)
        self.pbf_container.setMaximumHeight(16777215)
        pbf_layout = QtWidgets.QVBoxLayout(self.pbf_container)
        pbf_layout.setContentsMargins(4, 4, 4, 4)

        # Single-line control bar: Geofabrik Index controls AND PBF Search Radius controls on 1 line
        index_row = QtWidgets.QHBoxLayout()
        self.index_status_label = QtWidgets.QLabel(
            self.localizer.get_string("step2_status_checking_geofabrik"),
            self.pbf_container,
        )
        index_row.addWidget(self.index_status_label)
        self.index_update_button = QtWidgets.QPushButton(
            self.localizer.get_string("step2_button_update_index", default="Index aktualisieren"),
            self.pbf_container,
        )
        self.index_update_button.clicked.connect(self.update_geofabrik_index)
        index_row.addWidget(self.index_update_button)

        index_row.addSpacing(24)

        radius_label = QtWidgets.QLabel(self.localizer.get_string("step2_label_pbf_search_radius"), self.pbf_container)
        index_row.addWidget(radius_label)
        self.radius_edit = QtWidgets.QLineEdit("250", self.pbf_container)
        self.radius_edit.setMaximumWidth(80)
        index_row.addWidget(self.radius_edit)
        self.search_button = QtWidgets.QPushButton(self.localizer.get_string("button_update"), self.pbf_container)
        self.search_button.clicked.connect(lambda: self.start_pbf_search(force=True))
        index_row.addWidget(self.search_button)

        index_row.addStretch(1)
        pbf_layout.addLayout(index_row)

        # Main Box 2 Horizontal Splitter: Left 2/3 (Selection & Tables) | Right 1/3 (Map Preview)
        pbf_main_splitter = QtWidgets.QSplitter(qt_enum(Qt, "Horizontal", "Orientation"), self.pbf_container)

        left_pbf_panel = QtWidgets.QWidget(pbf_main_splitter)
        left_pbf_layout = QtWidgets.QVBoxLayout(left_pbf_panel)
        left_pbf_layout.setContentsMargins(0, 0, 0, 0)
        left_pbf_layout.setSpacing(8)

        # Region selection 3-column table panel (Available | Buttons | Selection)
        self.splitter = QtWidgets.QSplitter(qt_enum(Qt, "Horizontal", "Orientation"), left_pbf_panel)

        available_panel = QtWidgets.QWidget(self.splitter)
        available_layout = QtWidgets.QVBoxLayout(available_panel)
        available_layout.setContentsMargins(0, 0, 0, 0)
        available_layout.setSpacing(8)

        available_heading_row = QtWidgets.QHBoxLayout()
        av_label = QtWidgets.QLabel(self.localizer.get_string("step2_label_available_regions"), available_panel)
        av_label.setFont(app_font(10, qfont_bold()))
        available_heading_row.addWidget(av_label)
        available_heading_row.addStretch(1)
        self.search_spinner = BusySpinner(available_panel)
        available_heading_row.addWidget(self.search_spinner)
        self.search_spinner_label = QtWidgets.QLabel(
            self.localizer.get_string("step2_status_searching_pbf"),
            available_panel,
        )
        self.search_spinner_label.hide()
        available_heading_row.addWidget(self.search_spinner_label)
        available_layout.addLayout(available_heading_row)

        self.available_tree = QtWidgets.QTreeWidget(available_panel)
        self.available_tree.setColumnCount(2)
        self.available_tree.setHeaderLabels(
            [
                self.localizer.get_string("step2_label_column_region"),
                self.localizer.get_string("step2_label_column_size"),
            ]
        )
        self.available_tree.header().setStretchLastSection(False)
        self.available_tree.header().setSectionResizeMode(0, qt_enum(QtWidgets.QHeaderView, "Stretch", "ResizeMode"))
        self.available_tree.header().setSectionResizeMode(1, qt_enum(QtWidgets.QHeaderView, "ResizeToContents", "ResizeMode"))
        self.available_tree.itemDoubleClicked.connect(lambda _item, _column: self.add_selected_region())
        available_layout.addWidget(self.available_tree, 1)
        self._available_total_item = None

        move_panel = QtWidgets.QWidget(self.splitter)
        self.move_layout = QtWidgets.QBoxLayout(qt_enum(QtWidgets.QBoxLayout, "TopToBottom", "Direction"), move_panel)
        self.move_layout.setContentsMargins(4, 24, 4, 24)
        self.move_layout.addStretch(1)
        self.add_button = QtWidgets.QPushButton(">", move_panel)
        self.add_button.clicked.connect(self.add_selected_region)
        self.move_layout.addWidget(self.add_button)
        self.remove_button = QtWidgets.QPushButton("<", move_panel)
        self.remove_button.clicked.connect(self.remove_selected_region)
        self.move_layout.addWidget(self.remove_button)
        self.clear_button = QtWidgets.QPushButton("<<", move_panel)
        self.clear_button.setToolTip(self.localizer.get_string("step2_button_clear_list"))
        self.clear_button.clicked.connect(self.clear_selected_regions)
        self.move_layout.addWidget(self.clear_button)
        self.move_layout.addStretch(1)

        selected_panel = QtWidgets.QWidget(self.splitter)
        selected_layout = QtWidgets.QVBoxLayout(selected_panel)
        selected_layout.setContentsMargins(0, 0, 0, 0)
        selected_layout.setSpacing(8)
        sel_label = QtWidgets.QLabel(self.localizer.get_string("step2_label_selection_for_download"), selected_panel)
        sel_label.setFont(app_font(10, qfont_bold()))
        selected_layout.addWidget(sel_label)
        self.selected_tree = QtWidgets.QTreeWidget(selected_panel)
        self.selected_tree.setColumnCount(3)
        self.selected_tree.setHeaderLabels(
            [
                self.localizer.get_string("step2_label_column_region"),
                self.localizer.get_string("step2_label_column_size"),
                self.localizer.get_string("step2_label_column_availability"),
            ]
        )
        self.selected_tree.header().setSectionResizeMode(0, qt_enum(QtWidgets.QHeaderView, "Stretch", "ResizeMode"))
        self.selected_tree.header().setSectionResizeMode(1, qt_enum(QtWidgets.QHeaderView, "ResizeToContents", "ResizeMode"))
        self.selected_tree.header().setSectionResizeMode(2, qt_enum(QtWidgets.QHeaderView, "ResizeToContents", "ResizeMode"))
        selected_layout.addWidget(self.selected_tree, 1)

        # Total size footer: separator line + label below the tree, always visible at the bottom
        sep = QtWidgets.QFrame(selected_panel)
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine if hasattr(QtWidgets.QFrame, "Shape") else QtWidgets.QFrame.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken if hasattr(QtWidgets.QFrame, "Shadow") else QtWidgets.QFrame.Sunken)
        selected_layout.addWidget(sep)
        footer_row = QtWidgets.QHBoxLayout()
        self.total_size_footer_label = QtWidgets.QLabel("", selected_panel)
        footer_row.addWidget(self.total_size_footer_label, 1)
        self.total_size_value_label = QtWidgets.QLabel("", selected_panel)
        footer_row.addWidget(self.total_size_value_label)
        selected_layout.addLayout(footer_row)
        self._total_size_footer_item = None  # kept for compatibility, unused

        self.splitter.addWidget(available_panel)
        self.splitter.addWidget(move_panel)
        self.splitter.addWidget(selected_panel)
        self.splitter.setStretchFactor(0, 5)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 5)
        left_pbf_layout.addWidget(self.splitter, 1)

        # PBF Download Buttons Row
        pbf_button_row = QtWidgets.QHBoxLayout()
        self.download_button = QtWidgets.QPushButton(
            self.localizer.get_string("step2_button_download", default="Herunterladen"),
            left_pbf_panel,
        )
        self.download_button.clicked.connect(self.start_download)
        pbf_button_row.addWidget(self.download_button)

        self.stop_button = QtWidgets.QPushButton(
            self.localizer.get_string("button_stop_download", default="Stopp"),
            left_pbf_panel,
        )
        self.stop_button.clicked.connect(self.stop_download)
        self.stop_button.hide()
        pbf_button_row.addWidget(self.stop_button)

        self.browser_button = QtWidgets.QPushButton(
            self.localizer.get_string("step2_button_open_browser", default="Im Browser öffnen"),
            left_pbf_panel,
        )
        self.browser_button.clicked.connect(self.open_download_urls)
        pbf_button_row.addWidget(self.browser_button)

        self.check_button = QtWidgets.QPushButton(
            self.localizer.get_string("step2_button_check_status", default="Status prüfen"),
            left_pbf_panel,
        )
        self.check_button.clicked.connect(lambda: self.check_pbf_files(show_feedback=True, force=True))
        pbf_button_row.addWidget(self.check_button)
        pbf_button_row.addStretch(1)
        left_pbf_layout.addLayout(pbf_button_row)

        self.pbf_file_progress_label = QtWidgets.QLabel("", left_pbf_panel)
        self.pbf_file_progress_label.setWordWrap(True)
        left_pbf_layout.addWidget(self.pbf_file_progress_label)

        self.pbf_file_progress_bar = AnimatedProgressBar(left_pbf_panel)
        self.pbf_file_progress_bar.setRange(0, 100)
        self.pbf_file_progress_bar.setValue(0)
        left_pbf_layout.addWidget(self.pbf_file_progress_bar)

        self.pbf_overall_progress_label = QtWidgets.QLabel("", left_pbf_panel)
        self.pbf_overall_progress_label.setWordWrap(True)
        left_pbf_layout.addWidget(self.pbf_overall_progress_label)

        self.progress_bar = AnimatedProgressBar(left_pbf_panel)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        left_pbf_layout.addWidget(self.progress_bar)

        # Right Panel inside Box 2: Map Preview (~1/3 total width) with matching title styling
        right_map_panel = QtWidgets.QWidget(pbf_main_splitter)
        right_map_layout = QtWidgets.QVBoxLayout(right_map_panel)
        right_map_layout.setContentsMargins(0, 0, 0, 0)
        right_map_layout.setSpacing(8)

        map_title = QtWidgets.QLabel(self.localizer.get_string("step2_map_title"), right_map_panel)
        right_map_layout.addWidget(map_title)

        self.map_preview = Step4MapPreview(self.localizer, self.selected_location, self.geofabrik_index, right_map_panel)
        self.map_preview.setMinimumHeight(280)
        right_map_layout.addWidget(self.map_preview, 1)

        pbf_main_splitter.addWidget(left_pbf_panel)
        pbf_main_splitter.addWidget(right_map_panel)
        pbf_main_splitter.setStretchFactor(0, 67)
        pbf_main_splitter.setStretchFactor(1, 33)
        pbf_main_splitter.setSizes([670, 330])

        pbf_layout.addWidget(pbf_main_splitter)
        pbf_box_layout.addWidget(self.pbf_container)

        controls_layout.addWidget(self.pbf_box)
        controls_layout.addStretch(1)

        scroll_area.setWidget(scroll_widget)
        root_layout.addWidget(scroll_area, 1)

        self.next_button = QtWidgets.QPushButton(self.localizer.get_string("button_next"), self)
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.confirm)
        self.next_button.hide()

        self.interactive_widgets = [
            self.radius_edit,
            self.search_button,
            self.available_tree,
            self.add_button,
            self.remove_button,
            self.clear_button,
            self.selected_tree,
            self.download_button,
            self.browser_button,
            self.check_button,
            self.global_download_button,
            self.global_browser_button,
            self.global_check_button,
            self.index_update_button,
        ]
        self._update_total_size()

    def _set_accordion_collapsed(self, container, toggle_btn, locale_key) -> None:
        """Collapse one accordion section to zero height and update its button text."""
        container.setMaximumHeight(0)
        container.setVisible(False)
        toggle_btn.setText(escape_mnemonic("\u25b6 " + self.localizer.get_string(locale_key)))

    def toggle_global_box(self) -> None:
        visible = not self.global_container.isVisible()
        if visible:
            self.global_container.setMaximumHeight(16777215)
            self.global_container.setVisible(True)
        else:
            self.global_container.setMaximumHeight(0)
            self.global_container.setVisible(False)
        self.global_toggle_btn.setText(escape_mnemonic(("\u25bc " if visible else "\u25b6 ") + self.localizer.get_string("step2_accordion_global")))
        if visible:
            self._set_accordion_collapsed(self.pbf_container, self.pbf_toggle_btn, "step2_accordion_osm")
        self.global_box.adjustSize()
        self.pbf_box.adjustSize()

    def toggle_pbf_box(self) -> None:
        visible = not self.pbf_container.isVisible()
        if visible:
            self.pbf_container.setMaximumHeight(16777215)
            self.pbf_container.setVisible(True)
        else:
            self.pbf_container.setMaximumHeight(0)
            self.pbf_container.setVisible(False)
        self.pbf_toggle_btn.setText(escape_mnemonic(("\u25bc " if visible else "\u25b6 ") + self.localizer.get_string("step2_accordion_osm")))
        if visible:
            self._set_accordion_collapsed(self.global_container, self.global_toggle_btn, "step2_accordion_global")
        self.global_box.adjustSize()
        self.pbf_box.adjustSize()

    def create_project_directory(self) -> None:
        Dialogs.warning(
            self,
            self.localizer.get_string("error_project_dir_not_set_title"),
            self.localizer.get_string("step2_error_project_dir_first"),
        )

    def open_project_folder(self) -> None:
        if self.project_path and os.path.exists(self.project_path):
            webbrowser.open(os.path.normpath(self.project_path))
        else:
            Dialogs.warning(
                self,
                self.localizer.get_string("error_project_dir_not_set_title"),
                self.localizer.get_string("step2_error_project_dir_first"),
            )

    def _set_project_ready(self, ready: bool) -> None:
        self.create_project_button.setEnabled(False)
        self.open_project_button.setEnabled(ready)
        for widget in self.interactive_widgets:
            widget.setEnabled(ready)
        if self.global_download_button:
            self.global_download_button.setEnabled(True)
        if ready and self.project_path:
            self.project_label.setText(
                self.localizer.get_string("step2_label_project_dir_set", path=os.path.normpath(self.project_path))
            )
        else:
            self.project_label.setText(self.localizer.get_string("step2_label_project_dir_not_set"))



    def start_pbf_search(self, force: bool = False) -> None:
        if not self.project_path:
            self.create_project_directory()
            return
        if not self.selected_location:
            return
        try:
            radius_km = int(self.radius_edit.text().strip())
        except ValueError:
            Dialogs.error(
                self,
                self.localizer.get_string("step2_error_invalid_radius_title"),
                self.localizer.get_string("step2_error_invalid_radius_message"),
            )
            return

        if not force:
            cached = get_cached_pbf_details(self.selected_location, radius_km)
            if cached and "pbfs" in cached:
                self.pbf_details = cached
                self._refresh_available_tree()
                self._emit_status(self.localizer.get_string("step2_status_project_dir_set"))
                return

        if self._search_thread is not None:
            self._search_cancelled = True
            if self._search_worker:
                self._search_worker.cancel()

        self._search_cancelled = False
        self.search_spinner.start()
        self.search_spinner_label.show()
        self._set_busy(True)
        self._emit_status(self.localizer.get_string("step2_status_searching_pbf"))

        self._search_thread = QtCore.QThread(self)
        self._search_worker = PbfSearchWorker(self.selected_location, radius_km, self.geofabrik_index, force_refresh=force)
        self._search_worker.moveToThread(self._search_thread)
        self._search_thread.started.connect(self._search_worker.run)
        self._search_worker.results_ready.connect(self._handle_pbf_search_results)
        self._search_worker.error_ready.connect(self._handle_pbf_search_error)
        self._search_worker.finished.connect(self._search_thread.quit)
        self._search_worker.finished.connect(self._search_worker.deleteLater)
        self._search_thread.finished.connect(self._search_thread.deleteLater)
        self._search_thread.finished.connect(self._clear_search_worker)
        self._search_thread.start()

    def cancel_all_workers(self) -> None:
        """Safely stops, disconnects, and waits for all active background threads to terminate."""
        self._search_cancelled = True

        if self._search_worker is not None:
            try:
                self._search_worker.cancel()
                self._search_worker.results_ready.disconnect()
                self._search_worker.error_ready.disconnect()
            except Exception:
                pass
        if self._search_thread is not None:
            try:
                self._search_thread.quit()
                self._search_thread.wait(300)
            except Exception:
                pass
            self._search_thread = None
            self._search_worker = None

        if self._verify_worker is not None:
            try:
                self._verify_worker.results_ready.disconnect()
                self._verify_worker.error_ready.disconnect()
            except Exception:
                pass
        if self._verify_thread is not None:
            try:
                self._verify_thread.quit()
                self._verify_thread.wait(300)
            except Exception:
                pass
            self._verify_thread = None
            self._verify_worker = None

        if self._download_worker is not None:
            try:
                self._download_worker.stop()
                self._download_worker.progress_ready.disconnect()
                self._download_worker.file_ready.disconnect()
                self._download_worker.error_ready.disconnect()
            except Exception:
                pass
        if self._download_thread is not None:
            try:
                self._download_thread.quit()
                self._download_thread.wait(300)
            except Exception:
                pass
            self._download_thread = None
            self._download_worker = None

        if self._global_download_worker is not None:
            try:
                self._global_download_worker.stop()
                self._global_download_worker.progress_updated.disconnect()
                self._global_download_worker.detail_progress.disconnect()
            except Exception:
                pass
        if self._global_download_thread is not None:
            try:
                self._global_download_thread.quit()
                self._global_download_thread.wait(300)
            except Exception:
                pass
            self._global_download_thread = None
            self._global_download_worker = None

        if self._index_update_worker is not None:
            try:
                self._index_update_worker.finished.disconnect()
            except Exception:
                pass
        if self._index_update_thread is not None:
            try:
                self._index_update_thread.quit()
                self._index_update_thread.wait(300)
            except Exception:
                pass
            self._index_update_thread = None
            self._index_update_worker = None

        try:
            if hasattr(self, "search_spinner") and self.search_spinner:
                self.search_spinner.stop()
            if hasattr(self, "search_spinner_label") and self.search_spinner_label:
                self.search_spinner_label.hide()
        except Exception:
            pass

    def cancel_region_search(self) -> None:
        self.cancel_all_workers()

    def closeEvent(self, event) -> None:
        self.cancel_all_workers()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        self.cancel_all_workers()
        super().hideEvent(event)

    def _handle_pbf_search_results(self, details: dict) -> None:
        if self._search_cancelled:
            return
        self.pbf_details = details
        self._refresh_available_tree()

    def _handle_pbf_search_error(self, message: str) -> None:
        if self._search_cancelled:
            return
        self._refresh_available_tree()
        self._emit_status(self.localizer.get_string("step2_status_project_dir_set"))
        self._emit_status(self.localizer.get_string("step2_status_pbf_search_error", error=message))

    def _clear_search_worker(self) -> None:
        self.search_spinner.stop()
        self.search_spinner_label.hide()
        self._search_thread = None
        self._search_worker = None
        self._search_cancelled = False
        self._set_busy(False)

    def _refresh_available_tree(self) -> None:
        self.available_tree.clear()
        pbfs = (self.pbf_details or {}).get("pbfs", [])
        for pbf in pbfs:
            top_item = QtWidgets.QTreeWidgetItem(
                [
                    pbf.get("name", pbf.get("filename", "")),
                    format_gb(pbf.get("size_bytes", 0)),
                ]
            )
            top_item.setTextAlignment(1, 0x0002 | 0x0080)  # AlignRight | AlignVCenter
            top_item.setData(0, qt_enum(Qt, "UserRole", "ItemDataRole"), pbf)
            self.available_tree.addTopLevelItem(top_item)

            for sub in pbf.get("sub_regions", []):
                sub_item = QtWidgets.QTreeWidgetItem(
                    [
                        "  " + sub.get("name", sub.get("filename", "")),
                        format_gb(sub.get("size_bytes", 0)),
                    ]
                )
                sub_item.setTextAlignment(1, 0x0002 | 0x0080)  # AlignRight | AlignVCenter
                sub_item.setData(0, qt_enum(Qt, "UserRole", "ItemDataRole"), sub)
                top_item.addChild(sub_item)

            top_item.setExpanded(True)

    def _refresh_selected_tree(self) -> None:
        if not isinstance(self.download_jobs, list):
            self.download_jobs = []
        self.selected_tree.clear()
        self._total_size_footer_item = None
        for job in self.download_jobs:
            item = QtWidgets.QTreeWidgetItem(
                [
                    job.get("name", job.get("filename", "")),
                    format_gb(job.get("size_bytes") or job.get("bytes") or 0),
                    self._availability_label_text("checking"),
                ]
            )
            item.setTextAlignment(1, 0x0002 | 0x0080)  # AlignRight | AlignVCenter
            item.setData(0, qt_enum(Qt, "UserRole", "ItemDataRole"), job)
            self.selected_tree.addTopLevelItem(item)
        self._update_total_size()

    def add_selected_region(self) -> None:
        item = self.available_tree.currentItem()
        if not item:
            return
        pbf = item.data(0, qt_enum(Qt, "UserRole", "ItemDataRole"))
        if not pbf:
            return
        if not isinstance(self.download_jobs, list):
            self.download_jobs = []
        added = add_download_job(self.download_jobs, pbf)
        if added:
            self._refresh_selected_tree()
            self._update_map_preview()
            self.check_pbf_files(show_feedback=False)

    def remove_selected_region(self) -> None:
        item = self.selected_tree.currentItem()
        if not item:
            return
        job = item.data(0, qt_enum(Qt, "UserRole", "ItemDataRole"))
        if not job:
            return
        self.download_jobs = remove_download_job(self.download_jobs, job.get("osm_id"))
        self._refresh_selected_tree()
        self._update_map_preview()
        self.check_pbf_files(show_feedback=False)

    def clear_selected_regions(self) -> None:
        self.download_jobs = []
        self._refresh_selected_tree()
        self._update_map_preview()
        self.check_pbf_files(show_feedback=False)

    def _update_map_preview(self) -> None:
        if self.map_preview is not None:
            try:
                radius_km = int(self.radius_edit.text().strip())
            except ValueError:
                radius_km = 250
            self.map_preview.update_preview(self.download_jobs, radius_km)

    def start_download(self) -> None:
        if not self.download_jobs:
            Dialogs.warning(
                self,
                self.localizer.get_string("step2_error_no_download_title"),
                self.localizer.get_string("step2_error_no_regions_selected_message"),
            )
            return

        pending = pending_pbf_download_jobs(self.download_jobs, self._last_verification)
        if not pending:
            self.check_pbf_files(show_feedback=True, force=True)
            return

        self._download_active = True
        self._pbf_download_start_time = time.monotonic()
        self._set_busy(True)
        self.download_button.hide()
        self.stop_button.show()
        self.progress_bar.setValue(0)
        self.pbf_file_progress_bar.setValue(0)
        self.progress_bar.start_animation()
        self.pbf_file_progress_bar.start_animation()
        self.pbf_file_progress_label.setText("")
        self.pbf_overall_progress_label.setText("")
        self._emit_status(self.localizer.get_string("step2_status_preparing_download"))

        self._download_thread = QtCore.QThread(self)
        self._download_worker = PbfDownloadWorker(pending, self.project_path, self.data_path)
        self._download_worker.moveToThread(self._download_thread)
        self._download_thread.started.connect(self._download_worker.run)
        self._download_worker.progress_ready.connect(self._handle_download_progress)
        self._download_worker.file_ready.connect(self._handle_download_file_ready)
        self._download_worker.finished.connect(self._handle_download_finished)
        self._download_worker.finished.connect(self._download_thread.quit)
        self._download_worker.finished.connect(self._download_worker.deleteLater)
        self._download_thread.finished.connect(self._download_thread.deleteLater)
        self._download_thread.finished.connect(self._clear_download_worker)
        self._download_thread.start()

    def stop_download(self) -> None:
        if self._download_worker:
            self._download_worker.stop()
            self.progress_bar.stop_animation()
            self.pbf_file_progress_bar.stop_animation()
            self._emit_status(self.localizer.get_string("status_stopping_download", default="Breche Download ab..."))

    def _handle_download_progress(
        self, filename: str, file_dl: int, file_total: int,
        file_index: int, total_files: int, cum_dl: int, cum_total: int,
    ) -> None:
        # Per-file progress
        if file_total > 0:
            file_pct = min(100, max(0, int((file_dl / file_total) * 100)))
        else:
            file_pct = 0
        self.pbf_file_progress_bar.setValue(file_pct)

        file_dl_str = format_bytes(file_dl)
        file_total_str = format_bytes(file_total)
        self.pbf_file_progress_label.setText(
            f"Datei {file_index}/{total_files}: {filename}  —  {file_dl_str} / {file_total_str}"
        )

        # Overall progress
        if cum_total > 0:
            overall_pct = min(100, max(0, int((cum_dl / cum_total) * 100)))
        else:
            overall_pct = 0
        self.progress_bar.setValue(overall_pct)

        # ETA calculation
        elapsed = time.monotonic() - self._pbf_download_start_time if self._pbf_download_start_time else 0
        if elapsed > 0.5 and cum_dl > 0:
            speed = cum_dl / elapsed
            remaining = cum_total - cum_dl
            eta_sec = remaining / speed if speed > 0 else None
        else:
            eta_sec = None

        cum_dl_str = format_bytes(cum_dl)
        cum_total_str = format_bytes(cum_total)
        eta_str = format_eta(eta_sec)
        self.pbf_overall_progress_label.setText(
            f"Gesamt: {cum_dl_str} / {cum_total_str}  —  ETA: {eta_str}"
        )

        self._emit_status(
            f"Lade herunter… ({file_index}/{total_files} Dateien – {overall_pct}%) ETA: {eta_str}"
        )

    def _handle_download_file_ready(self, filename: str, file_index: int, total_files: int) -> None:
        self.pbf_file_progress_bar.setValue(100)
        self._emit_status(
            self.localizer.get_string(
                "step2_status_file_complete",
                filename=filename,
                files_completed=file_index,
                total_files=total_files,
                default=f"'{filename}' fertig. ({file_index}/{total_files} Dateien)",
            )
        )

    def _handle_download_finished(self, success: bool) -> None:
        self._download_active = False
        self._pbf_download_start_time = None
        self.progress_bar.stop_animation()
        self.pbf_file_progress_bar.stop_animation()
        self.stop_button.hide()
        self.download_button.show()
        if success:
            self.progress_bar.setValue(100)
            self.pbf_file_progress_bar.setValue(100)
            self.pbf_file_progress_label.setText("")
            total_files = len(self.download_jobs) if (hasattr(self, "download_jobs") and self.download_jobs) else 1
            self.pbf_overall_progress_label.setText(
                self.localizer.get_string(
                    "step2_status_all_downloads_complete",
                    total_files=total_files,
                    default=f"Alle {total_files} Downloads abgeschlossen.",
                )
            )
            self.check_pbf_files(show_feedback=False, force=True)
        else:
            self._emit_status(self.localizer.get_string("step2_status_download_interrupted", default="Download abgebrochen."))

    def _clear_download_worker(self) -> None:
        self.progress_bar.stop_animation()
        self.pbf_file_progress_bar.stop_animation()
        self._download_thread = None
        self._download_worker = None
        self._set_busy(False)

    def open_download_urls(self) -> None:
        if not self.download_jobs:
            webbrowser.open("https://download.geofabrik.de/")
            return
        for job in self.download_jobs:
            url = job.get("url")
            if url:
                webbrowser.open(url)

    def _update_total_size(self) -> None:
        """Update the footer label below the selected_tree with the total GB value."""
        size = total_download_size(self.download_jobs)
        total_label = self.localizer.get_string("step2_label_total_size")
        size_text = format_gb_label(size)
        if hasattr(self, "total_size_footer_label") and self.total_size_footer_label:
            self.total_size_footer_label.setText(total_label)
        if hasattr(self, "total_size_value_label") and self.total_size_value_label:
            self.total_size_value_label.setText(size_text)

    def _update_selected_availability(self, state_override: Optional[str] = None) -> None:
        try:
            selected_tree = object.__getattribute__(self, "selected_tree")
        except (AttributeError, RuntimeError):
            selected_tree = None
        if selected_tree is None:
            return
        footer = getattr(self, "_total_size_footer_item", None)
        for index in range(selected_tree.topLevelItemCount()):
            item = selected_tree.topLevelItem(index)
            if item is footer:
                continue  # skip the total size footer row
            job = item.data(0, qt_enum(Qt, "UserRole", "ItemDataRole"))
            if not job:
                continue
            state = state_override or availability_for_job(job, self._last_verification)
            item.setText(2, self._availability_label_text(state))


    def _availability_label_text(self, state: str) -> str:
        key_by_state = {
            "offline": "step2_availability_offline",
            "stale": "step2_availability_stale",
            "download": "step2_availability_download",
            "downloading": "step2_availability_downloading",
            "checking": "step2_availability_checking",
        }
        return self.localizer.get_string(key_by_state.get(state, "step2_availability_checking"))

    def _confirm_selection(self) -> None:
        self.confirm()

    def confirm(self) -> None:
        if not self.download_jobs:
            Dialogs.warning(
                self,
                self.localizer.get_string("step2_error_no_download_title"),
                self.localizer.get_string("step2_error_no_regions_selected_message"),
            )
            return
        self.check_pbf_files(show_feedback=True, confirm_after=True)

    def check_pbf_files(self, show_feedback: bool = False, confirm_after: bool = False, force: bool = False):
        if not self.download_jobs:
            self._invalidate_pbf_verification()
            self._update_next_button_state()
            return False
        if force:
            self._invalidate_pbf_verification()
        elif self._last_verification is not None and self._last_verified_job_key == self._job_key():
            self._verify_after_finish = confirm_after
            self._handle_verify_results(self._last_verification, show_feedback)
            return True
        self._start_verify_worker(show_feedback=show_feedback, confirm_after=confirm_after)
        return False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.splitter is None or self.move_layout is None:
            return
        use_vertical = self.width() < 760
        orientation = qt_enum(Qt, "Vertical" if use_vertical else "Horizontal", "Orientation")
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.move_layout.setDirection(
                qt_enum(QtWidgets.QBoxLayout, "LeftToRight", "Direction")
                if use_vertical
                else qt_enum(QtWidgets.QBoxLayout, "TopToBottom", "Direction")
            )
            self.move_layout.setContentsMargins(32 if use_vertical else 6, 6 if use_vertical else 32, 32 if use_vertical else 6, 6 if use_vertical else 32)
            self.add_button.setText("v" if use_vertical else ">")
            self.remove_button.setText("^" if use_vertical else "<")

    def _start_verify_worker(self, show_feedback: bool = False, confirm_after: bool = False) -> None:
        if self._verify_thread is not None:
            self._verify_after_finish = self._verify_after_finish or confirm_after
            if self._verification_job_key != self._job_key():
                self._verify_restart_requested = True
            return
        if not self.data_path:
            self.data_path = workspace_data_dir(self.workspace_path or os.path.dirname(self.project_path))
        self._verify_after_finish = confirm_after
        self._verification_job_key = self._job_key()
        self._verify_restart_requested = False
        self._set_busy(True)
        self._emit_status(self.localizer.get_string("step2_status_verifying_files"))
        self._verify_thread = QtCore.QThread(self)
        self._verify_worker = PbfVerifyWorker(self.project_path, list(self.download_jobs), self.data_path)
        self._verify_worker.moveToThread(self._verify_thread)
        self._verify_thread.started.connect(self._verify_worker.run)
        self._verify_worker.results_ready.connect(lambda verification: self._handle_verify_results(verification, show_feedback))
        self._verify_worker.error_ready.connect(lambda message: self._handle_verify_error(message, show_feedback))
        self._verify_worker.finished.connect(self._verify_thread.quit)
        self._verify_worker.finished.connect(self._verify_worker.deleteLater)
        self._verify_thread.finished.connect(self._verify_thread.deleteLater)
        self._verify_thread.finished.connect(self._clear_verify_worker)
        self._verify_thread.start()

    def _handle_verify_results(self, verification, show_feedback: bool) -> None:
        self._last_verification = verification
        self._last_verified_job_key = getattr(self, "_verification_job_key", None) or self._job_key()
        self._update_selected_availability()
        result_matches_selection = self._last_verified_job_key == self._job_key()
        if getattr(verification, "all_found", False) and result_matches_selection:
            self.user_pbf_path = getattr(verification, "user_pbf_path", None)
            found_files = getattr(verification, "found_files", [])
            self.pbf_references = build_pbf_references(self.download_jobs, found_files)
            if getattr(self, "progress_bar", None) is not None:
                self.progress_bar.setValue(100)
            if getattr(self, "_verify_after_finish", False):
                self._emit_confirmed_payload()
        elif result_matches_selection:
            self.user_pbf_path = None
            self.pbf_references = []
            if show_feedback or getattr(self, "_verify_after_finish", False):
                self._show_missing_files_warning(verification)
        self._update_next_button_state()

    def _handle_verify_error(self, message: str, show_feedback: bool) -> None:
        self._invalidate_pbf_verification()
        self._emit_status(message)
        if show_feedback or self._verify_after_finish:
            Dialogs.error(self, self.localizer.get_string("message_general_error_title"), message)

    def _clear_verify_worker(self) -> None:
        restart_requested = self._verify_restart_requested
        confirm_after = self._verify_after_finish
        self._verify_thread = None
        self._verify_worker = None
        self._verification_job_key = None
        self._verify_restart_requested = False
        self._verify_after_finish = False
        if restart_requested and self.download_jobs:
            self._start_verify_worker(show_feedback=False, confirm_after=confirm_after)
            return
        self._set_busy(False)

    def _show_missing_files_warning(self, verification) -> None:
        missing = "\n".join(f"- {name}*.osm.pbf" for name in verification.missing_basenames)
        Dialogs.warning(
            self,
            self.localizer.get_string("step2_verify_files_missing_title"),
            f"{self.localizer.get_string('step2_verify_files_missing_message')}\n\n{missing}",
        )

    def _emit_confirmed_payload(self) -> None:
        workspace_path = self.workspace_path or os.path.dirname(self.project_path)
        data_path = self.data_path or workspace_data_dir(workspace_path)
        payload = build_step4_payload(
            self.step3_data,
            self.project_path,
            self.download_jobs,
            self.user_pbf_path,
            workspace_path=workspace_path,
            data_path=data_path,
            pbf_references=self.pbf_references,
        )
        save_project_metadata(
            self.project_path,
            build_project_metadata(
                workspace_path=workspace_path,
                project_path=self.project_path,
                data_path=data_path,
                selected_location=self.selected_location,
                last_step=4,
                step3_data=self.step3_data,
                step4_data=payload,
                pbf_references=self.pbf_references,
            ),
        )
        self.confirmed.emit(payload)

    def _job_key(self) -> tuple:
        return tuple((job.get("osm_id"), job.get("filename")) for job in self.download_jobs)

    def _invalidate_pbf_verification(self) -> None:
        self._last_verification = None
        self._last_verified_job_key = None
        self.user_pbf_path = None
        self.pbf_references = []
        try:
            selected_tree = object.__getattribute__(self, "selected_tree")
        except (AttributeError, RuntimeError):
            selected_tree = None
        if selected_tree is not None:
            self._update_selected_availability("checking")

    def _set_busy(self, busy: bool) -> None:
        project_ready = bool(self.project_path)
        busy = busy or any(
            thread is not None
            for thread in (self._search_thread, self._verify_thread, self._download_thread)
        )
        for widget in self.interactive_widgets:
            widget.setEnabled(project_ready and not busy)
        self._update_next_button_state()
        if self._download_thread is not None:
            self.download_button.setEnabled(False)

    def _emit_status(self, message: str) -> None:
        if getattr(self, "status_changed", None) is not None:
            self.status_changed.emit(message)

    def check_global_datasets_status(self, show_feedback: bool = False) -> None:
        data_root = Path(tool_root()) / "core" / "data"
        status_dict = check_global_datasets(data_root)
        missing = [info["filename"] for info in status_dict.values() if not info["exists"]]

        if not missing:
            msg = self.localizer.get_string(
                "step2_status_global_data_all_present",
                default="Alle 5 POP & GADM Datensätze vorhanden (GADM ADM0..3 & GHS-POP)."
            )
            if self.global_status_label:
                self.global_status_label.setText(f"Status: {msg}")
            if self.global_progress_bar:
                self.global_progress_bar.setValue(100)
            if self.global_toggle_btn:
                self.global_toggle_btn.setText(escape_mnemonic(f"▶ {self.localizer.get_string('step2_accordion_global_full', default='1. Globale Geodaten (POP & GADM) [✓ 5/5 vorhanden]')}"))
            if self.global_container:
                self.global_container.hide()
            if self.pbf_container:
                self.pbf_container.show()
            if self.pbf_toggle_btn:
                self.pbf_toggle_btn.setText(escape_mnemonic(f"▼ {self.localizer.get_string('step2_accordion_osm', default='2. OSM-Regionen auswählen & herunterladen (Geofabrik)')}"))
            if show_feedback:
                Dialogs.info(self, self.localizer.get_string("step2_dialog_global_status_title", default="POP & GADM Status"), msg)
        else:
            present_count = len(status_dict) - len(missing)
            pct = int((present_count / float(len(status_dict))) * 100) if status_dict else 0
            if self.global_progress_bar:
                self.global_progress_bar.setValue(pct)
            missing_str = ", ".join(missing)
            msg = self.localizer.get_string(
                "step2_status_global_data_missing",
                missing=missing_str,
                default=f"{len(missing)} Datensätze fehlen ({missing_str})"
            )
            if self.global_status_label:
                self.global_status_label.setText(f"Status: {msg}")
            if self.global_toggle_btn:
                self.global_toggle_btn.setText(escape_mnemonic(f"▼ {self.localizer.get_string('step2_accordion_global_partial', present_count=present_count, default=f'1. Globale Geodaten (POP & GADM) [{present_count}/5 vorhanden]')}"))
            if self.global_container:
                self.global_container.show()
            if show_feedback:
                Dialogs.warning(self, self.localizer.get_string("step2_dialog_global_status_title", default="POP & GADM Status"), msg)
        self._update_next_button_state()

    def start_global_download(self) -> None:
        if self._global_download_thread is not None:
            return

        data_root = Path(tool_root()) / "core" / "data"
        self._global_download_start_time = time.monotonic()
        if self.global_download_button:
            self.global_download_button.setEnabled(False)
        if self.global_stop_button:
            self.global_stop_button.show()
        if self.global_progress_bar:
            self.global_progress_bar.setValue(0)
            self.global_progress_bar.start_animation()
        if self.global_file_progress_bar:
            self.global_file_progress_bar.setValue(0)
            self.global_file_progress_bar.start_animation()
        if self.global_file_progress_label:
            self.global_file_progress_label.setText("")
        if self.global_overall_progress_label:
            self.global_overall_progress_label.setText("")
        if self.global_status_label:
            self.global_status_label.setText(self.localizer.get_string("step2_status_preparing_download", default="Download von POP & GADM wird gestartet..."))

        self._global_download_thread = QtCore.QThread(self.parent())
        self._global_download_worker = GlobalDataDownloadWorker(DEFAULT_BASE_URL, data_root)
        self._global_download_worker.moveToThread(self._global_download_thread)
        self._global_download_thread.started.connect(self._global_download_worker.run)
        self._global_download_worker.progress_updated.connect(self._handle_global_download_progress)
        self._global_download_worker.detail_progress.connect(self._handle_global_detail_progress)
        self._global_download_worker.finished.connect(self._handle_global_download_finished)
        self._global_download_worker.finished.connect(self._global_download_thread.quit)
        self._global_download_worker.finished.connect(self._global_download_worker.deleteLater)
        self._global_download_thread.finished.connect(self._global_download_thread.deleteLater)
        self._global_download_thread.finished.connect(self._clear_global_download_worker)
        self._global_download_thread.start()

    def _handle_global_download_progress(self, msg: str, percent: int) -> None:
        if self.global_status_label:
            self.global_status_label.setText(msg)

    def _handle_global_detail_progress(
        self, filename: str, file_dl: int, file_total: int,
        file_index: int, total_files: int, cum_dl: int, cum_total: int,
    ) -> None:
        # Per-file progress
        if file_total > 0:
            file_pct = min(100, max(0, int((file_dl / file_total) * 100)))
        else:
            file_pct = 0
        if self.global_file_progress_bar:
            self.global_file_progress_bar.setValue(file_pct)

        file_dl_str = format_bytes(file_dl)
        file_total_str = format_bytes(file_total)
        if self.global_file_progress_label:
            self.global_file_progress_label.setText(
                f"Datei {file_index}/{total_files}: {filename}  —  {file_dl_str} / {file_total_str}"
            )

        # Overall progress
        if cum_total > 0:
            overall_pct = min(100, max(0, int((cum_dl / cum_total) * 100)))
        else:
            overall_pct = 0
        if self.global_progress_bar:
            self.global_progress_bar.setValue(overall_pct)

        # ETA calculation
        elapsed = time.monotonic() - self._global_download_start_time if self._global_download_start_time else 0
        if elapsed > 0.5 and cum_dl > 0:
            speed = cum_dl / elapsed
            remaining = cum_total - cum_dl
            eta_sec = remaining / speed if speed > 0 else None
        else:
            eta_sec = None

        cum_dl_str = format_bytes(cum_dl)
        cum_total_str = format_bytes(cum_total)
        eta_str = format_eta(eta_sec)
        if self.global_overall_progress_label:
            self.global_overall_progress_label.setText(
                f"Gesamt: {cum_dl_str} / {cum_total_str}  —  ETA: {eta_str}"
            )

    def _handle_global_download_finished(self, success: bool, message: str) -> None:
        self._global_download_start_time = None
        if self.global_progress_bar:
            self.global_progress_bar.stop_animation()
        if self.global_file_progress_bar:
            self.global_file_progress_bar.stop_animation()
        if self.global_download_button:
            self.global_download_button.setEnabled(True)
        if self.global_stop_button:
            self.global_stop_button.hide()

        if success:
            if self.global_progress_bar:
                self.global_progress_bar.setValue(100)
            if self.global_file_progress_bar:
                self.global_file_progress_bar.setValue(100)
            if self.global_file_progress_label:
                self.global_file_progress_label.setText("")
            if self.global_overall_progress_label:
                self.global_overall_progress_label.setText(
                    self.localizer.get_string(
                        "step2_status_all_downloads_complete",
                        total_files=5,
                        default="Alle 5 Downloads abgeschlossen.",
                    )
                )
            if self.global_status_label:
                self.global_status_label.setText(self.localizer.get_string("step2_status_global_complete", default="Download abgeschlossen: Alle POP & GADM Daten vorhanden."))
            Dialogs.info(self, self.localizer.get_string("step2_dialog_global_download_title", default="Download POP & GADM"), message)
        else:
            if self.global_status_label:
                self.global_status_label.setText(self.localizer.get_string("step2_status_download_finished_msg", message=message, default=f"Download beendet: {message}"))
            Dialogs.warning(self, self.localizer.get_string("step2_dialog_global_download_title", default="Download POP & GADM"), message)
        self.check_global_datasets_status(show_feedback=False)

    def stop_global_download(self) -> None:
        if self._global_download_worker:
            self._global_download_worker.stop()
            if self.global_progress_bar:
                self.global_progress_bar.stop_animation()
            if self.global_file_progress_bar:
                self.global_file_progress_bar.stop_animation()
            if self.global_status_label:
                self.global_status_label.setText(self.localizer.get_string("step2_status_cancelling", default="Breche Download ab..."))

    def _clear_global_download_worker(self) -> None:
        if self.global_progress_bar:
            self.global_progress_bar.stop_animation()
        if self.global_file_progress_bar:
            self.global_file_progress_bar.stop_animation()
        self._global_download_thread = None
        self._global_download_worker = None

    def open_global_browser_url(self) -> None:
        webbrowser.open(DEFAULT_BASE_URL)

    def _update_next_button_state(self) -> None:
        pbf_ready = bool(self._last_verification and getattr(self._last_verification, "all_found", False))

        data_root = Path(tool_root()) / "core" / "data"
        status_dict = check_global_datasets(data_root)
        global_ready = all(info["exists"] for info in status_dict.values())

        ready = pbf_ready and global_ready
        if self.next_button:
            self.next_button.setEnabled(ready)

        pbf_icon = "✓" if pbf_ready else "✗"
        global_icon = "✓" if global_ready else "✗"
        msg = self.localizer.get_string(
            "step2_status_footer_summary",
            global_icon=global_icon,
            pbf_icon=pbf_icon,
            default=f"Status: [{global_icon}] POP & GADM Geodaten   |   [{pbf_icon}] OSM PBF-Dateien"
        )
        self._emit_status(msg)

    def check_geofabrik_index_freshness(self) -> None:
        index_path = Path(tool_root()) / "core" / "data" / "osm" / "geofabrik-index.json"
        info = get_geofabrik_index_info(index_path)

        if not info["exists"]:
            if self.index_status_label:
                self.index_status_label.setText(self.localizer.get_string("step2_status_index_missing", default="Geofabrik-Index: Nicht vorhanden"))
            self.update_geofabrik_index()
        elif info["age_days"] is not None and info["age_days"] > 14:
            mtime_str = info.get("mtime_str", "")
            if self.index_status_label:
                self.index_status_label.setText(
                    self.localizer.get_string("step2_status_index_outdated", date=mtime_str, default=f"Geofabrik-Index: Veraltet (Stand: {mtime_str})")
                )
            self.update_geofabrik_index()
        else:
            mtime_str = info.get("mtime_str", "")
            if self.index_status_label:
                self.index_status_label.setText(
                    self.localizer.get_string("step2_status_index_up_to_date", date=mtime_str, default=f"Geofabrik-Index: Aktuell (Stand: {mtime_str})")
                )

    def update_geofabrik_index(self) -> None:
        index_path = Path(tool_root()) / "core" / "data" / "osm" / "geofabrik-index.json"
        if self._index_update_thread is not None:
            return

        if self.index_status_label:
            self.index_status_label.setText(self.localizer.get_string("step2_status_index_downloading", default="Geofabrik-Index wird heruntergeladen..."))
        if self.index_update_button:
            self.index_update_button.setEnabled(False)

        self._index_update_thread = QtCore.QThread(self.parent())
        self._index_update_worker = IndexUpdateWorker(index_path)
        self._index_update_worker.moveToThread(self._index_update_thread)
        self._index_update_thread.started.connect(self._index_update_worker.run)
        self._index_update_worker.finished.connect(self._handle_index_update_finished)
        self._index_update_worker.finished.connect(self._index_update_thread.quit)
        self._index_update_worker.finished.connect(self._index_update_worker.deleteLater)
        self._index_update_thread.finished.connect(self._index_update_thread.deleteLater)
        self._index_update_thread.finished.connect(self._clear_index_update_worker)
        self._index_update_thread.start()

    def _handle_index_update_finished(self, success: bool, index_data: object) -> None:
        if self.index_update_button:
            self.index_update_button.setEnabled(True)
        if success and isinstance(index_data, dict):
            clear_pbf_details_cache()
            self.geofabrik_index = index_data
            index_path = Path(tool_root()) / "core" / "data" / "osm" / "geofabrik-index.json"
            info = get_geofabrik_index_info(index_path)
            mtime_str = info.get("mtime_str", "heute")
            if self.index_status_label:
                self.index_status_label.setText(
                    self.localizer.get_string("step2_status_index_up_to_date", date=mtime_str, default=f"Geofabrik-Index: Aktuell (Stand: {mtime_str})")
                )
            self.start_pbf_search(force=True)
        else:
            if self.index_status_label:
                self.index_status_label.setText(f"Index-Aktualisierung fehlgeschlagen: {index_data}")

    def _clear_index_update_worker(self) -> None:
        self._index_update_thread = None
        self._index_update_worker = None


# Backward compatibility alias
Step3DataWidget = Step2DataWidget
