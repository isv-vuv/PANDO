"""Qt/PyQGIS implementation of step 3: grid and planning area selection."""

from __future__ import annotations

import math
import os
from pathlib import Path
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
    transfer_selected_cells,
)
from core.app.app_core.project import tool_root
from core.app.app_qt.map_preview import OsmAttributionOverlay, lonlat_to_web_mercator, create_high_quality_osm_layer, setup_map_canvas
from core.app.app_qt.qt_base import QColor, Dialogs, Qt, QtCore, QtGui, QtWidgets, app_font, create_step_header, pyqtSignal, qfont_bold, qt_enum, require_qgis_qt


try:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsFeature,
        QgsFeatureRequest,
        QgsField,
        QgsFillSymbol,
        QgsGeometry,
        QgsPointXY,
        QgsProject,
        QgsRasterLayer,
        QgsRectangle,
        QgsRendererCategory,
        QgsCategorizedSymbolRenderer,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
    )
    from qgis.gui import QgsMapCanvas, QgsMapToolEmitPoint, QgsMapToolPan, QgsVertexMarker
    from qgis.PyQt.QtCore import QVariant

    QGIS_STEP3_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only hit outside QGIS Python.
    QgsCoordinateReferenceSystem = QgsCoordinateTransform = QgsFeature = QgsField = None
    QgsFeatureRequest = QgsSingleSymbolRenderer = None
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


def get_app_icon(name: str) -> QtGui.QIcon:
    path = os.path.join(tool_root(), "core", "app", "icons", name)
    if not os.path.isfile(path):
        path = os.path.join(tool_root(), "core", "app", name)
    if os.path.isfile(path) and QtGui is not None and hasattr(QtGui, "QIcon"):
        icon = QtGui.QIcon(path)
        if not icon.isNull():
            return icon
    return QtGui.QIcon() if QtGui and hasattr(QtGui, "QIcon") else None


def get_custom_cursor(name: str, hot_x: int = -1, hot_y: int = -1) -> Optional[QtGui.QCursor]:
    path = os.path.join(tool_root(), "core", "app", "icons", name)
    if not os.path.isfile(path):
        path = os.path.join(tool_root(), "core", "app", name)
    if os.path.isfile(path) and QtGui is not None and hasattr(QtGui, "QCursor") and hasattr(QtGui, "QPixmap"):
        pixmap = QtGui.QPixmap(path)
        if not pixmap.isNull():
            hx = pixmap.width() // 2 if hot_x == -1 else hot_x
            hy = pixmap.height() // 2 if hot_y == -1 else hot_y
            return QtGui.QCursor(pixmap, hx, hy)
    cross = qt_enum(Qt, "CrossCursor", "CursorShape")
    return QtGui.QCursor(cross) if cross and hasattr(QtGui, "QCursor") else None


_PAN_TOOL_BASE = QgsMapToolPan if QgsMapToolPan is not None else object


class Step3PanMapTool(_PAN_TOOL_BASE):
    """Pan tool with interactive OpenHand / ClosedHand cursor on drag."""

    def __init__(self, canvas):
        if QgsMapToolPan is not None:
            super().__init__(canvas)
        self.canvas = canvas
        self._set_hand_cursor(False)

    def _set_hand_cursor(self, is_closed: bool) -> None:
        if QtCore is None or not hasattr(self, "setCursor"):
            return
        shape_name = "ClosedHandCursor" if is_closed else "OpenHandCursor"
        cursor_shape = qt_enum(Qt, shape_name, "CursorShape")
        if cursor_shape is not None:
            self.setCursor(cursor_shape)

    def activate(self) -> None:
        if hasattr(super(), "activate"):
            super().activate()
        self._set_hand_cursor(False)

    def canvasPressEvent(self, event) -> None:
        self._set_hand_cursor(True)
        if hasattr(super(), "canvasPressEvent"):
            super().canvasPressEvent(event)

    def canvasReleaseEvent(self, event) -> None:
        self._set_hand_cursor(False)
        if hasattr(super(), "canvasReleaseEvent"):
            super().canvasReleaseEvent(event)

    def deactivate(self) -> None:
        self._set_hand_cursor(False)
        if hasattr(super(), "deactivate"):
            super().deactivate()


class Step3SelectionMapTool(_MAP_TOOL_BASE):
    """Map tool that keeps click toggling separate from drag assignment with custom SVG cursors."""

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
        self.mode = "select"
        self._cursor_select = get_custom_cursor("mSelect.svg", 0, 0)
        self._cursor_adjust = get_custom_cursor("mCapturePoint.svg", -1, -1)
        self._apply_cursor()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._apply_cursor()

    def _apply_cursor(self) -> None:
        if not hasattr(self, "setCursor"):
            return
        if self.mode == "adjust_position":
            if self._cursor_adjust:
                self.setCursor(self._cursor_adjust)
        else:
            if self._cursor_select:
                self.setCursor(self._cursor_select)

    def activate(self) -> None:
        if hasattr(super(), "activate"):
            super().activate()
        self._apply_cursor()

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


class FloatingMapToolbar(QtWidgets.QFrame if QtWidgets is not None else object):
    """Floating overlay toolbar directly on the QGIS canvas for map interaction tools."""

    def __init__(self, parent_canvas: Optional[object], on_mode_changed=None, localizer=None):
        if QtWidgets is None or parent_canvas is None:
            return
        super().__init__(parent_canvas)
        self.localizer = localizer
        self.on_mode_changed = on_mode_changed
        self.setObjectName("floatingMapToolbar")
        self.setStyleSheet(
            "#floatingMapToolbar {"
            "  background-color: rgba(255, 255, 255, 0.96);"
            "  border: 1px solid #94a3b8;"
            "  border-radius: 6px;"
            "}"
            "QToolButton {"
            "  border: 1px solid transparent;"
            "  border-radius: 4px;"
            "  background-color: transparent;"
            "}"
            "QToolButton:hover:!checked {"
            "  background-color: #f1f5f9;"
            "  border-color: #cbd5e1;"
            "}"
            "QToolButton:checked {"
            "  background-color: #dbeafe;"
            "  border-color: #2563eb;"
            "}"
        )
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.btn_group = QtWidgets.QButtonGroup(self)
        self.btn_group.setExclusive(True)

        select_title = localizer.get_string("step3_button_select_cells", default="Zellen auswählen") if localizer else "Zellen auswählen"
        pan_title = localizer.get_string("step3_button_pan_map", default="Karte verschieben") if localizer else "Karte verschieben"
        adjust_title = localizer.get_string("step2_button_adjust_position", default="Position verschieben") if localizer else "Position verschieben"
        zoom_in_title = localizer.get_string("step3_button_zoom_in", default="Hineinzoomen (+)") if localizer else "Hineinzoomen (+)"
        zoom_out_title = localizer.get_string("step3_button_zoom_out", default="Herauszoomen (-)") if localizer else "Herauszoomen (-)"

        select_icon = get_app_icon("mActionSelectRectangle.svg") or self._create_tool_icon("select")
        pan_icon = get_app_icon("mActionPan.svg") or self._create_tool_icon("pan")
        adjust_icon = get_app_icon("mCapturePoint.svg") or self._create_tool_icon("adjust_position")
        zoom_in_icon = self._create_tool_icon("zoom_in")
        zoom_out_icon = self._create_tool_icon("zoom_out")

        self.btn_select = self._create_button("select", select_icon, select_title)
        self.btn_pan = self._create_button("pan", pan_icon, pan_title)
        self.btn_adjust = self._create_button("adjust_position", adjust_icon, adjust_title)

        separator = QtWidgets.QFrame(self)
        separator.setFrameShape(qt_enum(QtWidgets.QFrame, "VLine", "Shape") if QtWidgets is not None else 5)
        separator.setStyleSheet("color: #cbd5e1; margin: 4px 2px;")

        self.btn_zoom_in = self._create_action_button(zoom_in_icon, zoom_in_title, self._zoom_in)
        self.btn_zoom_out = self._create_action_button(zoom_out_icon, zoom_out_title, self._zoom_out)

        layout.addWidget(self.btn_select)
        layout.addWidget(self.btn_pan)
        layout.addWidget(self.btn_adjust)
        layout.addWidget(separator)
        layout.addWidget(self.btn_zoom_in)
        layout.addWidget(self.btn_zoom_out)

        self.btn_select.setChecked(True)
        self.btn_group.buttonClicked.connect(self._on_button_clicked)

        parent_canvas.installEventFilter(self)
        self.adjustSize()
        self.update_position()

    def _create_button(self, mode: str, icon: QtGui.QIcon, tooltip: str) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton(self)
        btn.setIcon(icon)
        btn.setIconSize(QtCore.QSize(22, 22))
        btn.setFixedSize(36, 36)
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        btn.setToolTip(tooltip)
        btn.setProperty("map_mode", mode)
        self.btn_group.addButton(btn)
        return btn

    def _create_action_button(self, icon: QtGui.QIcon, tooltip: str, on_click) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton(self)
        btn.setIcon(icon)
        btn.setIconSize(QtCore.QSize(22, 22))
        btn.setFixedSize(36, 36)
        btn.setCheckable(False)
        btn.setAutoRaise(True)
        btn.setToolTip(tooltip)
        btn.clicked.connect(on_click)
        return btn

    def _zoom_in(self):
        if self.parent_canvas:
            self.parent_canvas.zoomByFactor(1.3)

    def _zoom_out(self):
        if self.parent_canvas:
            self.parent_canvas.zoomByFactor(1.0 / 1.3)

    def _create_tool_icon(self, icon_type: str) -> QtGui.QIcon:
        icon = QtGui.QIcon()
        if QtGui is None or not hasattr(QtGui, "QPixmap"):
            return icon
        for is_checked in (False, True):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QColor(0, 0, 0, 0))
            painter = QtGui.QPainter(pixmap)
            try:
                painter.setRenderHint(qt_enum(QtGui.QPainter, "Antialiasing", "RenderHint"))
                color = QColor("#1d4ed8") if is_checked else QColor("#1e293b")
                pen = QtGui.QPen(color, 1.8)
                pen.setCapStyle(qt_enum(Qt, "RoundCap", "PenCapStyle"))
                pen.setJoinStyle(qt_enum(Qt, "RoundJoin", "PenJoinStyle"))
                painter.setPen(pen)

                if icon_type == "select":
                    pen_dash = QtGui.QPen(color, 1.5, qt_enum(Qt, "DashLine", "PenStyle"))
                    painter.setPen(pen_dash)
                    painter.drawRect(3, 3, 17, 17)
                    painter.setPen(QtGui.QPen(color, 1.5))
                    painter.fillRect(8, 8, 8, 8, color)
                elif icon_type == "pan":
                    painter.setPen(QtGui.QPen(color, 1.5))
                    painter.setBrush(color if is_checked else QColor(30, 41, 59, 40))
                    path = QtGui.QPainterPath()
                    path.moveTo(7, 12)
                    path.lineTo(7, 7.5)
                    path.arcTo(7, 6, 2.5, 2.5, 180, -180)
                    path.lineTo(9.5, 10.5)
                    path.lineTo(9.5, 5.5)
                    path.arcTo(9.5, 4, 2.5, 2.5, 180, -180)
                    path.lineTo(12, 10.5)
                    path.lineTo(12, 6.5)
                    path.arcTo(12, 5, 2.5, 2.5, 180, -180)
                    path.lineTo(14.5, 10.5)
                    path.lineTo(14.5, 8.5)
                    path.arcTo(14.5, 7, 2.5, 2.5, 180, -180)
                    path.lineTo(17, 13.5)
                    path.arcTo(12, 12, 5, 5, 0, -90)
                    path.lineTo(9, 18)
                    path.arcTo(5, 14, 4, 4, -90, -90)
                    path.closeSubpath()
                    painter.drawPath(path)
                elif icon_type == "adjust_position":
                    painter.setPen(QtGui.QPen(color, 1.6))
                    painter.drawEllipse(5, 5, 14, 14)
                    painter.drawLine(12, 1, 12, 5)
                    painter.drawLine(12, 19, 12, 23)
                    painter.drawLine(1, 12, 5, 12)
                    painter.drawLine(19, 12, 23, 12)
                    painter.setBrush(color)
                    painter.drawEllipse(10, 10, 4, 4)
                elif icon_type == "zoom_in":
                    painter.setPen(QtGui.QPen(color, 1.8))
                    painter.drawEllipse(3, 3, 12, 12)
                    painter.drawLine(12, 12, 19, 19)
                    painter.drawLine(6, 9, 12, 9)
                    painter.drawLine(9, 6, 9, 12)
                elif icon_type == "zoom_out":
                    painter.setPen(QtGui.QPen(color, 1.8))
                    painter.drawEllipse(3, 3, 12, 12)
                    painter.drawLine(12, 12, 19, 19)
                    painter.drawLine(6, 9, 12, 9)
            finally:
                painter.end()
            mode = qt_enum(QtGui.QIcon, "Normal", "Mode")
            state = qt_enum(QtGui.QIcon, "On", "State") if is_checked else qt_enum(QtGui.QIcon, "Off", "State")
            icon.addPixmap(pixmap, mode, state)
        return icon

    def set_mode(self, mode: str):
        for btn in (self.btn_select, self.btn_pan, self.btn_adjust):
            if btn.property("map_mode") == mode:
                btn.setChecked(True)
                break

    def _on_button_clicked(self, button):
        if self.on_mode_changed:
            self.on_mode_changed(button)

    def eventFilter(self, obj, event):
        if obj == self.parent() and QtCore is not None and hasattr(event, "type") and event.type() == qt_enum(QtCore.QEvent, "Resize", "Type"):
            self.update_position()
        return super().eventFilter(obj, event)

    def update_position(self):
        parent = self.parent()
        if parent is not None:
            self.adjustSize()
            self.move(14, 14)
            self.raise_()


class Step3GridAreaWidget(_Q_WIDGET_BASE):
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
        step2_data: Optional[dict] = None,
    ):
        require_qgis_qt()
        if QGIS_STEP3_IMPORT_ERROR is not None:
            raise RuntimeError(f"QGIS step 3 imports failed: {QGIS_STEP3_IMPORT_ERROR}")
        super().__init__(parent)
        self.localizer = localizer
        self.selected_location = selected_location
        self._saved_step3_data = dict(step3_data or {})
        self._saved_step2_data = dict(step2_data or {})
        self.grid_map_data = []
        self.selected_cells = create_selected_cells()
        self.grid_generated = False
        self.generated_grid_params: Optional[tuple[int, int]] = None
        self.grid_dirty = True
        self.current_mode = AREA_MODES[0]
        self.osm_layer = None
        self.grid_layer = None
        self.subgrid_layer = None
        self.adm2_layer = None
        self.oa_help_label = None
        self.splitter = None
        self.map_host = None
        self.canvas = None
        self.marker = None
        self.selection_map_tool = None
        self.pan_map_tool = None
        self.map_toolbar = None
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
        self._cached_adm2_key = None
        self._cached_adm2_features = None
        self._oa_cell_count = self._compute_oa_cell_count()
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
            current_step=3,
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
            "OA": self.localizer.get_string("step3_mode_oa", default="Außenraum (OA)"),
        }
        all_modes = list(AREA_MODES) + ["OA"]
        for mode in all_modes:
            radio = QtWidgets.QRadioButton(mode_labels.get(mode, mode), area_box)
            radio.setProperty("area_mode", mode)
            radio.setChecked(mode == self.current_mode)
            self.mode_group.addButton(radio)
            area_layout.addWidget(radio)

        self.oa_help_label = QtWidgets.QLabel(
            self.localizer.get_string(
                "step3_oa_help_text",
                default=(
                    "Der Außenraum wird basierend auf der im nächsten Schritt definierten "
                    "Auswahl der OSM-Daten festgelegt und besitzt Verkehrszellen in Form von "
                    "ADM2-Zellen. Eine Auswahl ist hier nicht erforderlich."
                ),
            ),
            area_box,
        )
        self.oa_help_label.setWordWrap(True)
        self.oa_help_label.setStyleSheet("color: #475569; font-size: 11px; line-height: 1.4; padding: 2px 4px 6px 22px;")
        area_layout.addWidget(self.oa_help_label)
        self.oa_help_label.setVisible(self.current_mode == "OA")

        self.mode_group.buttonClicked.connect(self._mode_changed)
        form_layout.addWidget(area_box)

        counter_box = QtWidgets.QGroupBox(self.localizer.get_string("step3_label_traffic_cells_counter"), form_host)
        counter_layout = QtWidgets.QVBoxLayout(counter_box)
        counter_layout.setSpacing(6)

        self.show_subgrid_checkbox = QtWidgets.QCheckBox(
            self.localizer.get_string("step3_checkbox_show_subdivision"),
            counter_box,
        )
        self.show_subgrid_checkbox.stateChanged.connect(self._on_subgrid_toggled)
        counter_layout.addWidget(self.show_subgrid_checkbox)

        self.pa_count_label = QtWidgets.QLabel("", counter_box)
        self.ia1_count_label = QtWidgets.QLabel("", counter_box)
        self.ia2_count_label = QtWidgets.QLabel("", counter_box)
        self.oa_count_label = QtWidgets.QLabel("", counter_box)

        self.total_traffic_cells_label = QtWidgets.QLabel("", counter_box)
        self.total_traffic_cells_label.setFont(app_font(10, qfont_bold()))

        counter_layout.addWidget(self.pa_count_label)
        counter_layout.addWidget(self.ia1_count_label)
        counter_layout.addWidget(self.ia2_count_label)
        counter_layout.addWidget(self.oa_count_label)
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
        self.pan_map_tool = Step3PanMapTool(self.canvas)
        self.canvas.setMapTool(self.selection_map_tool)
        if hasattr(self.canvas, "setWheelFactor"):
            self.canvas.setWheelFactor(1.15)
        if hasattr(self.canvas, "extentsChanged"):
            self.canvas.extentsChanged.connect(self._handle_canvas_extents_changed)
        map_layout.addWidget(self.canvas)
        self.attribution_overlay = OsmAttributionOverlay(self.canvas)
        self.map_toolbar = FloatingMapToolbar(self.canvas, self._map_mode_changed, self.localizer)

        self.splitter.addWidget(left_pane)
        self.splitter.addWidget(self.map_host)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        QtCore.QTimer.singleShot(0, self._apply_map_width)

    def _handle_canvas_extents_changed(self) -> None:
        """Smoothly clamp zoom-out and pan so the user cannot zoom beyond the 200km preview buffer."""
        if getattr(self, "_is_clamping_extent", False) or not self.canvas:
            return

        lat = self._location_lat()
        lon = self._location_lon()
        if lat is None or lon is None:
            return

        orig_extent = self.canvas.extent()
        if orig_extent.isEmpty():
            return

        center_x, center_y = lonlat_to_web_mercator(lon, lat)
        max_radius_m = 200_000.0  # 200 km maximum radius from center point

        w = orig_extent.width()
        h = orig_extent.height()
        aspect = w / max(1.0, h)

        needs_clamp = False
        clamped_w = w
        clamped_h = h

        # 1. Hard stop on zoom-out: maximum width / height = 2 * max_radius_m (400 km box)
        if w > 2 * max_radius_m or h > 2 * max_radius_m:
            needs_clamp = True
            if aspect >= 1.0:
                clamped_w = 2 * max_radius_m
                clamped_h = clamped_w / aspect
            else:
                clamped_h = 2 * max_radius_m
                clamped_w = clamped_h * aspect

        # 2. Hard stop on pan: keep center from drifting far outside the 200km radius box
        curr_cx = orig_extent.center().x()
        curr_cy = orig_extent.center().y()
        max_pan_dx = max(0.0, max_radius_m - clamped_w / 4.0)
        max_pan_dy = max(0.0, max_radius_m - clamped_h / 4.0)

        clamped_cx = min(max(curr_cx, center_x - max_pan_dx), center_x + max_pan_dx)
        clamped_cy = min(max(curr_cy, center_y - max_pan_dy), center_y + max_pan_dy)

        if abs(clamped_cx - curr_cx) > 100.0 or abs(clamped_cy - curr_cy) > 100.0:
            needs_clamp = True

        if needs_clamp:
            new_extent = QgsRectangle(
                clamped_cx - clamped_w / 2.0,
                clamped_cy - clamped_h / 2.0,
                clamped_cx + clamped_w / 2.0,
                clamped_cy + clamped_h / 2.0,
            )
            self._is_clamping_extent = True
            try:
                self.canvas.setExtent(new_extent)
                self.canvas.refresh()
            finally:
                self._is_clamping_extent = False

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
        params = self._current_grid_params_silent() or self.generated_grid_params
        radius_km = params[1] if params else 30.0
        # Comfortable zoom level showing the inner grid area with ~35% context margin
        extent_m = max(35000.0, float(radius_km) * 1000.0 * 1.35)
        self.canvas.setExtent(QgsRectangle(x - extent_m, y - extent_m, x + extent_m, y + extent_m))
    def generate_grid(self, preserve_selection: bool = True) -> None:
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
        old_grid = list(self.grid_map_data) if self.grid_map_data else None
        try:
            self.grid_map_data = generate_grid_map_data(lat, lon, cell_size_m, radius_km)
            if preserve_selection and old_grid:
                self.selected_cells = transfer_selected_cells(old_grid, self.grid_map_data)
            else:
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
            val = float(text.replace(",", ".").strip())
            if val <= 0:
                return None
            return val
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

    def _on_subgrid_toggled(self, _state: int) -> None:
        if self.show_subgrid_checkbox and self.show_subgrid_checkbox.isChecked():
            self.adm2_layer = self._build_adm2_layer()
        self.redraw_grid()

    def redraw_grid(self) -> None:
        if not self.canvas:
            return
        self.grid_layer = self._build_grid_layer()
        self.subgrid_layer = self._build_subgrid_layer()
        
        is_oa_visible = (self.current_mode == "OA") or (
            self.show_subgrid_checkbox is not None and self.show_subgrid_checkbox.isChecked()
        )
        if is_oa_visible and self.adm2_layer is None:
            self.adm2_layer = self._build_adm2_layer()
            
        active_adm2 = self.adm2_layer if is_oa_visible else None
        layers = [layer for layer in (self.subgrid_layer, self.grid_layer, active_adm2, self.osm_layer) if layer and layer.isValid()]
        self.canvas.setLayers(layers)
        self.canvas.refresh()
        if self.grid_generated:
            self._emit_status(self.localizer.get_string("step3_status_grid_redrawn"))

    def _build_adm2_layer(self):
        if QgsVectorLayer is None:
            return None

        lat = self._location_lat()
        lon = self._location_lon()
        if lat is None or lon is None:
            return None

        gpkg_path = Path(tool_root()) / "core" / "data" / "gadm" / "gadm_adm2.gpkg"
        if not gpkg_path.exists():
            return None

        adm2_src = QgsVectorLayer(f"{gpkg_path}|layername=gadm_adm2", "ADM2_Src", "ogr")
        if not adm2_src.isValid():
            return None

        country_codes = self._get_selected_country_codes()
        field_names = [f.name() for f in adm2_src.fields()]
        gid_field = next((f for f in ("GID_0", "ISO", "ADM0_A3", "iso_a3") if f in field_names), None)

        # 200 km buffer around center point for OA preview
        d_lat = 200.0 / 111.32
        cos_lat = max(0.2, math.cos(math.radians(lat)))
        d_lon = 200.0 / (111.32 * cos_lat)
        filter_rect = QgsRectangle(lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat)
        req = QgsFeatureRequest().setFilterRect(filter_rect)
        if country_codes and gid_field:
            in_clause = ", ".join(f"'{c}'" for c in country_codes)
            req.setFilterExpression(f'"{gid_field}" IN ({in_clause})')

        # Build union of selected cells (PA, IA1, IA2) to cut out of ADM2
        selected_geoms = []
        for cell in self.grid_map_data:
            if cell.get("area_type"):
                coords = cell.get("wgs84_coords_map")
                if coords and len(coords) >= 3:
                    pts = [QgsPointXY(c_lon, c_lat) for c_lat, c_lon in coords]
                    pts.append(pts[0])
                    selected_geoms.append(QgsGeometry.fromPolygonXY([pts]))

        selected_union = None
        if selected_geoms and QgsGeometry is not None:
            selected_union = QgsGeometry.unaryUnion(selected_geoms)

        adm2_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Außenraum (ADM2)", "memory")
        provider = adm2_layer.dataProvider()
        provider.addAttributes([QgsField("NAME_2", QVariant.String)])
        adm2_layer.updateFields()

        out_features = []
        for feat in adm2_src.getFeatures(req):
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue
            if selected_union and not selected_union.isEmpty() and geom.intersects(selected_union):
                geom = geom.difference(selected_union)
                if not geom or geom.isEmpty():
                    continue

            new_feat = QgsFeature(adm2_layer.fields())
            new_feat.setGeometry(geom)
            name_val = feat.attribute("NAME_2") if feat.fields().indexFromName("NAME_2") != -1 else ""
            new_feat.setAttributes([name_val])
            out_features.append(new_feat)

        provider.addFeatures(out_features)
        adm2_layer.updateExtents()
        adm2_layer.setRenderer(self._adm2_renderer())
        return adm2_layer

    def _adm2_renderer(self):
        if QgsFillSymbol is None:
            return None
        symbol = QgsFillSymbol.createSimple(
            {
                "outline_color": "#475569",
                "outline_width": "0.75",
                "outline_style": "dash",
            }
        )
        fill_color = QColor(71, 85, 105, 30)
        symbol.setColor(fill_color)
        try:
            symbol.symbolLayer(0).setFillColor(fill_color)
        except Exception:
            pass
        if QgsSingleSymbolRenderer is not None:
            return QgsSingleSymbolRenderer(symbol)
        return None

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
            if hasattr(self, "oa_help_label") and self.oa_help_label:
                self.oa_help_label.setVisible(mode == "OA")
            if mode == "OA":
                self.adm2_layer = self._build_adm2_layer()
                self._emit_status(
                    self.localizer.get_string(
                        "step3_oa_help_text",
                        default="Der Außenraum wird basierend auf der im vorherigen Schritt definierten Auswahl der OSM-Daten festgelegt und besitzt Verkehrszellen in Form von ADM2-Zellen. Eine Auswahl ist hier nicht erforderlich.",
                    )
                )
            else:
                self._emit_status(self.localizer.get_string("step3_status_select_mode_prompt"))
            self.redraw_grid()

    def _map_mode_changed(self, button=None) -> None:
        if button is None or not hasattr(button, "property"):
            if hasattr(self, "map_toolbar") and self.map_toolbar and hasattr(self.map_toolbar, "btn_group"):
                button = self.map_toolbar.btn_group.checkedButton()
            elif hasattr(self, "map_mode_group") and self.map_mode_group:
                button = self.map_mode_group.checkedButton()
        mode = button.property("map_mode") if button is not None else None
        if not mode:
            return
        self.map_interaction_mode = mode
        if mode == "pan":
            self.canvas.setMapTool(self.pan_map_tool)
            self._emit_status(self.localizer.get_string("step3_status_pan_mode"))
        elif mode == "adjust_position":
            self.selection_map_tool.set_mode("adjust_position")
            self.canvas.setMapTool(self.selection_map_tool)
            self._emit_status(self.localizer.get_string("step3_status_adjust_position_mode"))
        else:
            self.selection_map_tool.set_mode("select")
            self.canvas.setMapTool(self.selection_map_tool)
            self._emit_status(self.localizer.get_string("step3_status_select_mode_prompt"))

    def _handle_canvas_click(self, point) -> None:
        self._last_drag_lonlat = None
        if self.map_interaction_mode == "adjust_position":
            self._handle_position_adjustment(point)
            return
        if self.map_interaction_mode != "select":
            return
        if self.current_mode == "OA":
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
        if self.current_mode == "OA":
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
        self.adm2_layer = None
        self._oa_cell_count = self._compute_oa_cell_count()
        self.generate_grid(preserve_selection=True)
        if hasattr(self, "map_toolbar") and self.map_toolbar:
            self.map_toolbar.set_mode("select")
        elif hasattr(self, "select_map_button") and self.select_map_button is not None:
            self.select_map_button.setChecked(True)
        self.map_interaction_mode = "select"
        self.selection_map_tool.set_mode("select")
        self.canvas.setMapTool(self.selection_map_tool)
        self._emit_status(self.localizer.get_string("step3_status_position_updated", lat=float(lat), lon=float(lon)))

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

    def _get_selected_country_codes(self) -> list[str]:
        from core.app.app_core.pipeline import resolve_all_iso3_country_codes
        step_data = self._saved_step2_data or self._saved_step3_data or {}
        raw_codes = resolve_all_iso3_country_codes(self.selected_location, step_data)
        return [c.strip().upper() for c in raw_codes.split(",") if c.strip()]

    def _compute_oa_cell_count(self) -> int:
        lat = self._location_lat()
        lon = self._location_lon()
        if lat is None or lon is None:
            return 0
        country_codes = self._get_selected_country_codes()

        gpkg_path = Path(tool_root()) / "core" / "data" / "gadm" / "gadm_adm2.gpkg"
        if not gpkg_path.exists() or QgsVectorLayer is None:
            return 0

        adm2_src = QgsVectorLayer(f"{gpkg_path}|layername=gadm_adm2", "ADM2_Src", "ogr")
        if not adm2_src.isValid():
            return 0

        field_names = [f.name() for f in adm2_src.fields()]
        gid_field = next((f for f in ("GID_0", "ISO", "ADM0_A3", "iso_a3") if f in field_names), None)

        count = 0
        if country_codes and gid_field:
            in_clause = ", ".join(f"'{c}'" for c in country_codes)
            req = QgsFeatureRequest().setFilterExpression(f'"{gid_field}" IN ({in_clause})')
            if hasattr(QgsFeatureRequest, "NoGeometry"):
                req.setFlags(QgsFeatureRequest.NoGeometry)
            for _ in adm2_src.getFeatures(req):
                count += 1
        else:
            d_deg = 3.5
            req = QgsFeatureRequest().setFilterRect(QgsRectangle(lon - d_deg, lat - d_deg, lon + d_deg, lat + d_deg))
            if hasattr(QgsFeatureRequest, "NoGeometry"):
                req.setFlags(QgsFeatureRequest.NoGeometry)
            for _ in adm2_src.getFeatures(req):
                count += 1

        return count

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
        count_oa = getattr(self, "_oa_cell_count", 0)

        cells_pa = count_pa * 81
        cells_ia1 = count_ia1 * 9
        cells_ia2 = count_ia2 * 1
        total_cells = cells_pa + cells_ia1 + cells_ia2 + count_oa

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
        if hasattr(self, "oa_count_label") and self.oa_count_label is not None:
            self.oa_count_label.setText(
                self.localizer.get_string(
                    "step3_traffic_cells_oa",
                    count=f"{count_oa:,}".replace(",", "."),
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


# Backward compatibility alias
Step2GridAreaWidget = Step3GridAreaWidget
