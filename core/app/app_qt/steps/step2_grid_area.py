"""Qt/PyQGIS implementation of step 3: grid and planning area selection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional
from urllib.parse import quote

from core.app.app_core.geo import calc_utm_zone_str
from core.app.app_core.grid import (
    AREA_MODES,
    assign_cell_at_lonlat,
    assign_cells_along_lonlat_path,
    build_step3_payload,
    create_selected_cells,
    estimate_grid_cell_count,
    generate_grid_map_data,
    grid_params_are_dirty,
    has_selected_cells,
    restore_selected_cells,
    selection_is_exclusive,
    selected_cell_counts,
    subgrid_division_for_area,
    subgrid_map_coords_for_cell,
    toggle_cell_at_lonlat,
)
from core.app.app_qt.map_preview import OsmAttributionOverlay, lonlat_to_web_mercator, create_high_quality_osm_layer, setup_map_canvas
from core.app.app_qt.qt_base import QColor, Dialogs, Qt, QtCore, QtWidgets, app_font, create_step_header, pyqtSignal, qfont_bold, qt_enum, require_qgis_qt


try:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsFeature,
        QgsField,
        QgsFillSymbol,
        QgsGeometry,
        QgsPointXY,
        QgsProject,
        QgsRasterLayer,
        QgsRectangle,
        QgsRendererCategory,
        QgsCategorizedSymbolRenderer,
        QgsVectorLayer,
    )
    from qgis.gui import QgsMapCanvas, QgsMapToolEmitPoint, QgsMapToolPan, QgsVertexMarker
    from qgis.PyQt.QtCore import QVariant

    QGIS_STEP3_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only hit outside QGIS Python.
    QgsCoordinateReferenceSystem = QgsCoordinateTransform = QgsFeature = QgsField = None
    QgsFillSymbol = QgsGeometry = QgsPointXY = QgsProject = QgsRasterLayer = QgsRectangle = None
    QgsRendererCategory = QgsCategorizedSymbolRenderer = QgsVectorLayer = None
    QgsMapCanvas = QgsMapToolEmitPoint = QgsMapToolPan = QgsVertexMarker = QVariant = None
    QGIS_STEP3_IMPORT_ERROR = exc


AREA_COLORS = {
    "PA": "#008080",
    "IA1": "#FF8C00",
    "IA2": "#8A2BE2",
    "DEFAULT_FILL": "",
    "DEFAULT_OUTLINE": "gray",
}
MAX_CELL_LIMIT = 2000
MAX_CELL_WARN = 15000
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
_MAP_TOOL_BASE = QgsMapToolEmitPoint if QgsMapToolEmitPoint is not None else object


class Step3SelectionMapTool(_MAP_TOOL_BASE):
    """Map tool that keeps click toggling separate from drag assignment."""

    def __init__(self, canvas, click_callback, drag_callback, drag_finished_callback):
        if QgsMapToolEmitPoint is not None:
            super().__init__(canvas)
        self.canvas = canvas
        self.click_callback = click_callback
        self.drag_callback = drag_callback
        self.drag_finished_callback = drag_finished_callback
        self._press_point = None
        self._press_pixel = None
        self._dragging = False

    def canvasPressEvent(self, event):
        self._press_point = self._event_map_point(event)
        self._press_pixel = event.pos() if hasattr(event, "pos") else None
        self._dragging = False

    def canvasMoveEvent(self, event):
        if self._press_point is None:
            return
        if not self._dragging and not self._drag_threshold_reached(event):
            return
        current_point = self._event_map_point(event)
        if current_point is None:
            return
        if not self._dragging:
            self._dragging = True
            if self._press_point is not None:
                self.drag_callback(self._press_point)
        self.drag_callback(current_point)

    def canvasReleaseEvent(self, event):
        release_point = self._event_map_point(event)
        if not self._dragging and release_point is not None:
            self.click_callback(release_point)
        elif self._dragging:
            self.drag_finished_callback()
        self._press_point = None
        self._press_pixel = None
        self._dragging = False

    def _event_map_point(self, event):
        if hasattr(event, "mapPoint"):
            return event.mapPoint()
        if hasattr(event, "pos") and self.canvas is not None:
            pos = event.pos()
            return self.canvas.getCoordinateTransform().toMapCoordinates(pos.x(), pos.y())
        return None

    def _drag_threshold_reached(self, event) -> bool:
        if self._press_pixel is None or not hasattr(event, "pos"):
            return True
        current_pixel = event.pos()
        return abs(current_pixel.x() - self._press_pixel.x()) + abs(current_pixel.y() - self._press_pixel.y()) >= 4


class Step2GridAreaWidget(_Q_WIDGET_BASE):
    """Generates a UTM-based grid and lets the user classify cells on a QGIS canvas."""

    status_changed = pyqtSignal(str) if pyqtSignal is not None else None
    position_changed = pyqtSignal(object) if pyqtSignal is not None else None
    confirmed = pyqtSignal(dict) if pyqtSignal is not None else None

    def __init__(
        self,
        localizer,
        selected_location,
        parent: Optional[object] = None,
        step3_data: Optional[dict] = None,
    ):
        require_qgis_qt()
        if QGIS_STEP3_IMPORT_ERROR is not None:
            raise RuntimeError(f"QGIS step 3 imports failed: {QGIS_STEP3_IMPORT_ERROR}")
        super().__init__(parent)
        self.localizer = localizer
        self.selected_location = selected_location
        self._saved_step3_data = dict(step3_data or {})
        self.grid_map_data = []
        self.selected_cells = create_selected_cells()
        self.grid_generated = False
        self.generated_grid_params: Optional[tuple[int, int]] = None
        self.grid_dirty = True
        self.current_mode = AREA_MODES[0]
        self.osm_layer = None
        self.grid_layer = None
        self.subgrid_layer = None
        self.splitter = None
        self.map_host = None
        self.canvas = None
        self.marker = None
        self.selection_map_tool = None
        self.pan_map_tool = None
        self.cell_size_edit = None
        self.radius_edit = None
        self.grid_params_label = None
        self.generate_button = None
        self.confirm_button = None
        self.show_subgrid_checkbox = None
        self.mode_group = None
        self.map_mode_group = None
        self.select_map_button = None
        self.pan_map_button = None
        self.adjust_position_button = None
        self.map_interaction_mode = "select"
        self._last_drag_lonlat = None
        self._build_ui()
        self._center_map_on_location()
        if self._restore_saved_grid():
            self._emit_status(self.localizer.get_string("step3_status_grid_redrawn"))
        else:
            self._emit_status(self.localizer.get_string("step3_status_define_grid"))
            self.generate_grid()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(16, 10, 16, 10)
        root_layout.setSpacing(8)

        # Step Header spanning full window width
        header, _title_label, _step_label = create_step_header(
            self.localizer.get_string("step3_title"),
            current_step=2,
            total_steps=6,
            localizer=self.localizer,
            parent=self,
        )
        root_layout.addWidget(header)

        self.splitter = QtWidgets.QSplitter(qt_enum(Qt, "Horizontal", "Orientation"), self)
        self.splitter.setChildrenCollapsible(False)
        root_layout.addWidget(self.splitter)

        left_pane = QtWidgets.QWidget(self.splitter)
        left_layout = QtWidgets.QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)

        scroll = QtWidgets.QScrollArea(left_pane)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))
        form_host = QtWidgets.QWidget(scroll)
        form_layout = QtWidgets.QVBoxLayout(form_host)
        form_layout.setContentsMargins(4, 4, 12, 4)
        form_layout.setSpacing(14)

        utm_zone_name = calc_utm_zone_str(
            self._location_lat(),
            self._location_lon(),
            self.localizer.get_string("step5_qgis_params_na"),
        )
        projection_text = self.localizer.get_string("step3_info_map_projection", local_utm_zone=utm_zone_name)
        projection = QtWidgets.QLabel(projection_text, form_host)
        projection.setWordWrap(True)
        form_layout.addWidget(projection)

        params_box = QtWidgets.QGroupBox(self.localizer.get_string("step3_label_current_grid_params"), form_host)
        params_layout = QtWidgets.QVBoxLayout(params_box)
        self.grid_params_label = QtWidgets.QLabel("", params_box)
        params_layout.addWidget(self.grid_params_label)
        form_layout.addWidget(params_box)

        grid_box = QtWidgets.QGroupBox(self.localizer.get_string("step3_label_grid_parameters"), form_host)
        grid_layout = QtWidgets.QGridLayout(grid_box)
        grid_layout.setColumnStretch(1, 1)
        grid_layout.addWidget(QtWidgets.QLabel(self.localizer.get_string("step3_label_cell_size"), grid_box), 0, 0)
        self.cell_size_edit = QtWidgets.QLineEdit("4500", grid_box)
        self.cell_size_edit.setMaximumWidth(140)
        self.cell_size_edit.textChanged.connect(self._mark_grid_params_dirty)
        grid_layout.addWidget(self.cell_size_edit, 0, 1)
        grid_layout.addWidget(QtWidgets.QLabel(self.localizer.get_string("step3_label_radius"), grid_box), 1, 0)
        self.radius_edit = QtWidgets.QLineEdit("30", grid_box)
        self.radius_edit.setMaximumWidth(140)
        self.radius_edit.textChanged.connect(self._mark_grid_params_dirty)
        grid_layout.addWidget(self.radius_edit, 1, 1)
        self.generate_button = QtWidgets.QPushButton(self.localizer.get_string("step3_button_generate_grid"), grid_box)
        self.generate_button.clicked.connect(self.generate_grid)
        grid_layout.addWidget(self.generate_button, 2, 1, qt_enum(Qt, "AlignRight", "AlignmentFlag"))
        form_layout.addWidget(grid_box)

        map_mode_box = QtWidgets.QGroupBox(self.localizer.get_string("step3_label_map_interaction"), form_host)
        map_mode_layout = QtWidgets.QHBoxLayout(map_mode_box)
        self.map_mode_group = QtWidgets.QButtonGroup(self)
        self.map_mode_group.setExclusive(True)

        self.select_map_button = QtWidgets.QPushButton(self.localizer.get_string("step3_button_select_cells"), map_mode_box)
        self.select_map_button.setCheckable(True)
        self.select_map_button.setChecked(True)
        self.select_map_button.setProperty("map_mode", "select")
        self.map_mode_group.addButton(self.select_map_button)
        map_mode_layout.addWidget(self.select_map_button)

        self.pan_map_button = QtWidgets.QPushButton(self.localizer.get_string("step3_button_pan_map"), map_mode_box)
        self.pan_map_button.setCheckable(True)
        self.pan_map_button.setProperty("map_mode", "pan")
        self.map_mode_group.addButton(self.pan_map_button)
        map_mode_layout.addWidget(self.pan_map_button)

        self.adjust_position_button = QtWidgets.QPushButton(
            self.localizer.get_string("step2_button_adjust_position"),
            map_mode_box,
        )
        self.adjust_position_button.setCheckable(True)
        self.adjust_position_button.setProperty("map_mode", "adjust_position")
        self.map_mode_group.addButton(self.adjust_position_button)
        map_mode_layout.addWidget(self.adjust_position_button)

        self.map_mode_group.buttonClicked.connect(self._map_mode_changed)
        map_mode_layout.addStretch(1)
        form_layout.addWidget(map_mode_box)

        area_box = QtWidgets.QGroupBox(self.localizer.get_string("step3_label_area_selection"), form_host)
        area_layout = QtWidgets.QVBoxLayout(area_box)
        area_layout.setSpacing(6)
        mode_label = QtWidgets.QLabel(self.localizer.get_string("step3_label_selection_mode"), area_box)
        mode_label.setFont(app_font(10, qfont_bold()))
        area_layout.addWidget(mode_label)

        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.setExclusive(True)
        mode_labels = {
            "PA": self.localizer.get_string("step3_mode_pa", default="Planungsraum (PA)"),
            "IA1": self.localizer.get_string("step3_mode_ia1", default="Einflussraum 1 (IA1)"),
            "IA2": self.localizer.get_string("step3_mode_ia2", default="Einflussraum 2 (IA2)"),
        }
        for mode in AREA_MODES:
            radio = QtWidgets.QRadioButton(mode_labels.get(mode, mode), area_box)
            radio.setProperty("area_mode", mode)
            radio.setChecked(mode == self.current_mode)
            self.mode_group.addButton(radio)
            area_layout.addWidget(radio)

        self.mode_group.buttonClicked.connect(self._mode_changed)
        form_layout.addWidget(area_box)

        counter_box = QtWidgets.QGroupBox(self.localizer.get_string("step3_label_traffic_cells_counter"), form_host)
        counter_layout = QtWidgets.QVBoxLayout(counter_box)
        counter_layout.setSpacing(6)

        self.show_subgrid_checkbox = QtWidgets.QCheckBox(
            self.localizer.get_string("step3_checkbox_show_subdivision"),
            counter_box,
        )
        self.show_subgrid_checkbox.stateChanged.connect(lambda _state: self.redraw_grid())
        counter_layout.addWidget(self.show_subgrid_checkbox)

        self.pa_count_label = QtWidgets.QLabel("", counter_box)
        self.ia1_count_label = QtWidgets.QLabel("", counter_box)
        self.ia2_count_label = QtWidgets.QLabel("", counter_box)

        self.total_traffic_cells_label = QtWidgets.QLabel("", counter_box)
        self.total_traffic_cells_label.setFont(app_font(10, qfont_bold()))

        counter_layout.addWidget(self.pa_count_label)
        counter_layout.addWidget(self.ia1_count_label)
        counter_layout.addWidget(self.ia2_count_label)
        counter_layout.addWidget(self.total_traffic_cells_label)
        form_layout.addWidget(counter_box)

        form_layout.addStretch(1)

        scroll.setWidget(form_host)
        left_layout.addWidget(scroll, 1)
        self.confirm_button = None

        self.map_host = QtWidgets.QWidget(self.splitter)
        map_layout = QtWidgets.QVBoxLayout(self.map_host)
        map_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = QgsMapCanvas(self.map_host)
        setup_map_canvas(self.canvas)
        self.osm_layer = self._create_osm_layer()
        if self.osm_layer and self.osm_layer.isValid():
            QgsProject.instance().addMapLayer(self.osm_layer, False)
            self.canvas.setLayers([self.osm_layer])
        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setColor(QColor("#2563eb"))
        self.marker.setIconSize(14)
        self.marker.setPenWidth(3)
        if hasattr(QgsVertexMarker, "ICON_CIRCLE"):
            self.marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
        self.selection_map_tool = Step3SelectionMapTool(
            self.canvas,
            self._handle_canvas_click,
            self._handle_canvas_drag,
            self._handle_canvas_drag_finished,
        )
        self.pan_map_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.selection_map_tool)
        map_layout.addWidget(self.canvas)
        self.attribution_overlay = OsmAttributionOverlay(self.canvas)

        self.splitter.addWidget(left_pane)
        self.splitter.addWidget(self.map_host)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        QtCore.QTimer.singleShot(0, self._apply_map_width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_map_width()

    def _apply_map_width(self) -> None:
        """Keep the interactive map at least half as wide as the splitter."""
        if self.splitter is None or self.map_host is None:
            return
        available = max(0, self.splitter.width() - self.splitter.handleWidth())
        map_width = available // 2
        self.map_host.setMinimumWidth(map_width)
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sizes[1] < map_width:
            self.splitter.setSizes([available - map_width, map_width])

    def _create_osm_layer(self):
        return create_high_quality_osm_layer("OpenStreetMap")

    def _location_lat(self) -> Optional[float]:
        lat = getattr(self.selected_location, "latitude", None)
        return float(lat) if lat is not None else None

    def _location_lon(self) -> Optional[float]:
        lon = getattr(self.selected_location, "longitude", None)
        return float(lon) if lon is not None else None

    def _center_map_on_location(self) -> None:
        lat = self._location_lat()
        lon = self._location_lon()
        if lat is None or lon is None:
            return
        x, y = lonlat_to_web_mercator(lon, lat)
        point = QgsPointXY(x, y)
        self.marker.setCenter(point)
        self.marker.show()
        self.canvas.setCenter(point)
        self.canvas.setExtent(QgsRectangle(x - 35000, y - 35000, x + 35000, y + 35000))
        self.canvas.refresh()

    def generate_grid(self) -> None:
        params = self._read_grid_params()
        if params is None:
            return
        cell_size_m, radius_km = params
        approx_num_cells = estimate_grid_cell_count(cell_size_m, radius_km)
        if approx_num_cells > MAX_CELL_LIMIT:
            Dialogs.error(
                self,
                self.localizer.get_string("step3_error_cell_limit_title"),
                self.localizer.get_string(
                    "step3_error_cell_limit_message",
                    approx_num_cells=int(approx_num_cells),
                    max_cells=MAX_CELL_LIMIT,
                ),
            )
            return

        lat = self._location_lat()
        lon = self._location_lon()
        try:
            self.grid_map_data = generate_grid_map_data(lat, lon, cell_size_m, radius_km)
            self.selected_cells = create_selected_cells()
        except ValueError:
            Dialogs.error(
                self,
                self.localizer.get_string("message_general_error_title"),
                self.localizer.get_string("message_grid_error_utm_zone_unavailable", lat=lat, lon=lon),
            )
            return
        except Exception as exc:
            Dialogs.error(
                self,
                self.localizer.get_string("message_general_error_title"),
                self.localizer.get_string("message_grid_error_generation", error_details=exc),
            )
            self._emit_status(self.localizer.get_string("message_grid_error_generation", error_details=exc))
            return

        self.grid_generated = True
        self.generated_grid_params = params
        self.grid_dirty = False
        self.generate_button.setText(self.localizer.get_string("step3_button_update_grid"))
        self.grid_params_label.setText(
            f"{self.localizer.get_string('grid_cell_size_display', cell_size=cell_size_m)}, "
            f"{self.localizer.get_string('grid_radius_display', radius_km=radius_km)}"
        )
        self.redraw_grid()
        self._emit_status(self.localizer.get_string("message_grid_status_generating", count=len(self.grid_map_data)))
        self._update_confirm_button_state()

    def _restore_saved_grid(self) -> bool:
        saved_grid = self._saved_step3_data.get("grid_map_data")
        if not saved_grid:
            return False
        try:
            cell_size_m = int(self._saved_step3_data["cell_size_m"])
            radius_km = int(self._saved_step3_data["radius_km"])
        except (KeyError, TypeError, ValueError):
            return False

        self.cell_size_edit.setText(str(cell_size_m))
        self.radius_edit.setText(str(radius_km))
        self.grid_map_data = [dict(cell) for cell in saved_grid]
        self.selected_cells = restore_selected_cells(
            self.grid_map_data,
            self._saved_step3_data.get("selected_cells"),
        )
        self.grid_generated = True
        self.generated_grid_params = (cell_size_m, radius_km)
        self.grid_dirty = False
        self.generate_button.setText(self.localizer.get_string("step3_button_update_grid"))
        self.grid_params_label.setText(
            f"{self.localizer.get_string('grid_cell_size_display', cell_size=cell_size_m)}, "
            f"{self.localizer.get_string('grid_radius_display', radius_km=radius_km)}"
        )
        self.redraw_grid()
        self._update_confirm_button_state()
        return True

    def _parse_radius_km(self, text: str) -> Optional[float]:
        try:
            val = float(text.replace(",", "."))
            if val <= 0:
                return None
            return val / 1000.0 if val >= 100 else val
        except (TypeError, ValueError):
            return None

    def _current_grid_params_silent(self) -> Optional[tuple[int, float]]:
        try:
            cell_size_m = int(self.cell_size_edit.text())
        except (TypeError, ValueError):
            return None
        radius_km = self._parse_radius_km(self.radius_edit.text())
        if cell_size_m <= 0 or radius_km is None:
            return None
        return cell_size_m, radius_km

    def _mark_grid_params_dirty(self) -> None:
        current_params = self._current_grid_params_silent()
        self.grid_dirty = grid_params_are_dirty(current_params, self.generated_grid_params)
        if self.grid_dirty:
            self._emit_status(self.localizer.get_string("step3_status_define_grid"))
        self._update_confirm_button_state()

    def _read_grid_params(self) -> Optional[tuple[int, float]]:
        try:
            cell_size_m = int(self.cell_size_edit.text())
        except ValueError:
            Dialogs.error(
                self,
                self.localizer.get_string("step3_error_invalid_params_title"),
                self.localizer.get_string("step3_error_invalid_params_message_integer"),
            )
            return None
        radius_km = self._parse_radius_km(self.radius_edit.text())
        if cell_size_m <= 0 or radius_km is None:
            Dialogs.error(
                self,
                self.localizer.get_string("step3_error_invalid_params_title"),
                self.localizer.get_string("step3_error_invalid_params_message_positive"),
            )
            return None
        return cell_size_m, radius_km

    def redraw_grid(self) -> None:
        if not self.canvas:
            return
        self.grid_layer = self._build_grid_layer()
        self.subgrid_layer = self._build_subgrid_layer()
        layers = [layer for layer in (self.subgrid_layer, self.grid_layer, self.osm_layer) if layer and layer.isValid()]
        self.canvas.setLayers(layers)
        self.canvas.refresh()
        if self.grid_generated:
            self._emit_status(self.localizer.get_string("step3_status_grid_redrawn"))

    def _build_grid_layer(self):
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Planning Grid", "memory")
        provider = layer.dataProvider()
        provider.addAttributes([QgsField("cell_id", QVariant.Int), QgsField("area_type", QVariant.String)])
        layer.updateFields()
        provider.addFeatures([self._feature_for_cell(cell, layer.fields()) for cell in self.grid_map_data])
        layer.updateExtents()
        layer.setRenderer(self._grid_renderer())
        return layer

    def _grid_renderer(self):
        show_subgrid = self.show_subgrid_checkbox is not None and self.show_subgrid_checkbox.isChecked()
        categories = []
        if show_subgrid:
            style_by_area = {
                "": (QColor("#00000000"), "#00000000"),
                "PA": (QColor("#00000000"), "#00000000"),
                "IA1": (QColor("#00000000"), "#00000000"),
                "IA2": (QColor("#00000000"), "#00000000"),
            }
        else:
            style_by_area = {
                "": (QColor("#00000000"), AREA_COLORS["DEFAULT_OUTLINE"]),
                "PA": (QColor(0, 128, 128, 90), AREA_COLORS["PA"]),
                "IA1": (QColor(255, 140, 0, 90), AREA_COLORS["IA1"]),
                "IA2": (QColor(138, 43, 226, 90), AREA_COLORS["IA2"]),
            }
        for value, (fill_color, outline_color) in style_by_area.items():
            symbol = QgsFillSymbol.createSimple(
                {
                    "outline_color": outline_color,
                    "outline_width": "0.35" if outline_color != "#00000000" else "0",
                }
            )
            symbol.setColor(fill_color)
            try:
                symbol.symbolLayer(0).setFillColor(fill_color)
            except Exception:
                pass
            categories.append(QgsRendererCategory(value, symbol, value or "unselected"))
        return QgsCategorizedSymbolRenderer("area_type", categories)

    def _feature_for_cell(self, cell: dict, fields):
        feature = QgsFeature()
        feature.setFields(fields)
        points = [QgsPointXY(lon, lat) for lat, lon in cell["wgs84_coords_map"]]
        points.append(points[0])
        feature.setGeometry(QgsGeometry.fromPolygonXY([points]))
        feature.setAttributes([cell["id"], cell.get("area_type") or ""])
        return feature

    def _build_subgrid_layer(self):
        if not self.show_subgrid_checkbox or not self.show_subgrid_checkbox.isChecked():
            return None

        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Planning Grid Subdivision", "memory")
        provider = layer.dataProvider()
        provider.addAttributes([QgsField("area_type", QVariant.String)])
        layer.updateFields()
        features = []
        for cell in self.grid_map_data:
            area_type = cell.get("area_type")
            division = subgrid_division_for_area(area_type)
            if division:
                features.extend(self._subgrid_features(cell, division, layer.fields()))
        provider.addFeatures(features)
        layer.updateExtents()
        layer.setRenderer(self._subgrid_renderer())
        return layer

    def _subgrid_features(self, cell: dict, division: int, fields=None) -> list:
        features = []
        area_type = cell.get("area_type") or ""
        for polygon_coords in subgrid_map_coords_for_cell(cell, division):
            points = [QgsPointXY(lon, lat) for lat, lon in polygon_coords]
            points.append(points[0])
            feature = QgsFeature()
            if fields:
                feature.setFields(fields)
            feature.setGeometry(QgsGeometry.fromPolygonXY([points]))
            feature.setAttributes([area_type])
            features.append(feature)
        return features

    def _subgrid_renderer(self):
        categories = []
        dark_gray = "#383838"
        for value in ("PA", "IA1", "IA2"):
            symbol = QgsFillSymbol.createSimple(
                {
                    "outline_color": dark_gray,
                    "outline_width": "0.35",
                }
            )
            transparent = QColor("#00000000")
            symbol.setColor(transparent)
            try:
                symbol.symbolLayer(0).setFillColor(transparent)
            except Exception:
                pass
            categories.append(QgsRendererCategory(value, symbol, value))
        return QgsCategorizedSymbolRenderer("area_type", categories)

    def _mode_changed(self, button) -> None:
        if not hasattr(button, "property") and self.mode_group:
            button = self.mode_group.checkedButton()
        mode = button.property("area_mode") if button is not None else None
        if mode:
            self.current_mode = mode

    def _map_mode_changed(self, button) -> None:
        if not hasattr(button, "property") and self.map_mode_group:
            button = self.map_mode_group.checkedButton()
        mode = button.property("map_mode") if button is not None else None
        if not mode:
            return
        self.map_interaction_mode = mode
        if mode == "pan":
            self.canvas.setMapTool(self.pan_map_tool)
            self._emit_status(self.localizer.get_string("step3_status_pan_mode"))
        else:
            self.canvas.setMapTool(self.selection_map_tool)
            if mode == "adjust_position":
                self._emit_status(self.localizer.get_string("step3_status_adjust_position_mode"))
            else:
                self._emit_status(self.localizer.get_string("step3_status_select_mode_prompt"))

    def _handle_canvas_click(self, point) -> None:
        self._last_drag_lonlat = None
        if self.map_interaction_mode == "adjust_position":
            self._handle_position_adjustment(point)
            return
        if self.map_interaction_mode != "select":
            return
        if not self.grid_map_data:
            self._emit_status(self.localizer.get_string("step3_status_define_grid"))
            return
        if self.grid_dirty:
            self._emit_status(self.localizer.get_string("step3_status_define_grid"))
            return
        lon, lat = self._map_point_to_lonlat(point)
        toggle_result = toggle_cell_at_lonlat(self.grid_map_data, self.selected_cells, self.current_mode, lon, lat)
        if toggle_result:
            self._handle_toggle_result(toggle_result)

    def _handle_canvas_drag(self, point) -> None:
        if self.map_interaction_mode != "select":
            return
        if not self.grid_map_data or self.grid_dirty:
            return
        lon, lat = self._map_point_to_lonlat(point)
        previous_lonlat = self._last_drag_lonlat
        self._last_drag_lonlat = (lon, lat)
        if previous_lonlat is None:
            assign_result = assign_cell_at_lonlat(self.grid_map_data, self.selected_cells, self.current_mode, lon, lat)
            if assign_result:
                self._handle_toggle_result(assign_result)
            return

        results = assign_cells_along_lonlat_path(
            self.grid_map_data,
            self.selected_cells,
            self.current_mode,
            previous_lonlat[0],
            previous_lonlat[1],
            lon,
            lat,
        )
        if results:
            self._handle_toggle_result(results[-1])

    def _handle_canvas_drag_finished(self) -> None:
        self._last_drag_lonlat = None

    def _handle_position_adjustment(self, point) -> None:
        lon, lat = self._map_point_to_lonlat(point)
        self.selected_location = self._location_with_new_position(lat, lon)
        self.position_changed.emit(self.selected_location)
        x, y = lonlat_to_web_mercator(lon, lat)
        self.marker.setCenter(QgsPointXY(x, y))
        self.marker.show()
        self.canvas.setCenter(QgsPointXY(x, y))
        self.generate_grid()
        if self.select_map_button is not None:
            self.select_map_button.setChecked(True)
        self.map_interaction_mode = "select"
        self.canvas.setMapTool(self.selection_map_tool)
        self._emit_status(self.localizer.get_string("step3_status_position_updated", lat=f"{lat:.6f}", lon=f"{lon:.6f}"))

    def _location_with_new_position(self, lat: float, lon: float):
        raw = getattr(self.selected_location, "raw", None)
        if not isinstance(raw, dict):
            raw = {}
        return SimpleNamespace(
            address=getattr(self.selected_location, "address", ""),
            latitude=lat,
            longitude=lon,
            raw=dict(raw),
        )

    def _map_point_to_lonlat(self, point) -> tuple[float, float]:
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:3857"),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsProject.instance(),
        )
        wgs_point = transform.transform(point)
        return wgs_point.x(), wgs_point.y()

    def _handle_toggle_result(self, toggle_result) -> None:
        self.redraw_grid()
        status_parts = selected_cell_counts(self.selected_cells)
        localized_action = self.localizer.get_string("action_selected" if toggle_result.selected else "action_deselected")
        self._emit_status(
            self.localizer.get_string(
                "step3_status_cell_action",
                cell_id=toggle_result.cell_id,
                action=localized_action,
                current_mode=self.current_mode,
                area_counts=status_parts,
            )
        )
        self._update_confirm_button_state()

    def _update_traffic_cell_counter(self) -> None:
        if not hasattr(self, "pa_count_label") or self.pa_count_label is None:
            return

        params = self._current_grid_params_silent() or self.generated_grid_params
        cell_size_m = params[0] if params else 4500

        sub_e2 = cell_size_m // 9
        sub_e1 = cell_size_m // 3
        sub_e0 = cell_size_m

        count_pa = len(self.selected_cells.get("PA", set()))
        count_ia1 = len(self.selected_cells.get("IA1", set()))
        count_ia2 = len(self.selected_cells.get("IA2", set()))

        cells_pa = count_pa * 81
        cells_ia1 = count_ia1 * 9
        cells_ia2 = count_ia2 * 1
        total_cells = cells_pa + cells_ia1 + cells_ia2

        self.pa_count_label.setText(
            self.localizer.get_string(
                "step3_traffic_cells_pa",
                sub_size=sub_e2,
                count=count_pa,
                cells=f"{cells_pa:,}".replace(",", "."),
            )
        )
        self.ia1_count_label.setText(
            self.localizer.get_string(
                "step3_traffic_cells_ia1",
                sub_size=sub_e1,
                count=count_ia1,
                cells=f"{cells_ia1:,}".replace(",", "."),
            )
        )
        self.ia2_count_label.setText(
            self.localizer.get_string(
                "step3_traffic_cells_ia2",
                sub_size=sub_e0,
                count=count_ia2,
                cells=f"{cells_ia2:,}".replace(",", "."),
            )
        )
        self.total_traffic_cells_label.setText(
            self.localizer.get_string(
                "step3_traffic_cells_total",
                total=f"{total_cells:,}".replace(",", "."),
            )
        )

    def _update_confirm_button_state(self) -> None:
        self._update_traffic_cell_counter()
        if self.confirm_button is not None:
            self.confirm_button.setEnabled(
                self.grid_generated
                and has_selected_cells(self.selected_cells)
                and selection_is_exclusive(self.selected_cells)
            )

    def _confirm_selection(self) -> None:
        self._confirm()

    def _confirm(self) -> None:
        params = self._read_grid_params()
        if params is None:
            return
        cell_size_m, radius_km = params
        approx_num_cells = estimate_grid_cell_count(cell_size_m, radius_km)
        if approx_num_cells > MAX_CELL_WARN and not Dialogs.confirm(
            self,
            self.localizer.get_string("step3_warn_performance_title"),
            self.localizer.get_string("step3_warn_performance_message", count=int(approx_num_cells)),
        ):
            return
        if grid_params_are_dirty(params, self.generated_grid_params):
            Dialogs.warning(
                self,
                self.localizer.get_string("step3_label_grid_parameters"),
                self.localizer.get_string("step3_warn_grid_params_changed"),
            )
            self.grid_dirty = True
            self._update_confirm_button_state()
            return
        if not selection_is_exclusive(self.selected_cells):
            Dialogs.warning(
                self,
                self.localizer.get_string("step3_error_no_areas_defined_title"),
                self.localizer.get_string("step3_error_exclusive_assignments"),
            )
            self._update_confirm_button_state()
            return
        if not has_selected_cells(self.selected_cells):
            Dialogs.warning(
                self,
                self.localizer.get_string("step3_error_no_areas_defined_title"),
                self.localizer.get_string("step3_error_no_areas_defined_message"),
            )
            return
        self.confirmed.emit(
            build_step3_payload(
                self.selected_location,
                cell_size_m,
                radius_km,
                self.selected_cells,
                self.grid_map_data,
            )
        )

    def _emit_status(self, message: str) -> None:
        self.status_changed.emit(message)
