"""Shared map controls, cursors, navigation and distance measurement."""
from __future__ import annotations
import math
import os
from typing import Optional
from core.app.app_core.project import tool_root
from core.app.app_qt.qt_base import QColor, Qt, QtCore, QtGui, QtWidgets, app_font, qfont_bold, qt_enum
try:
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea, QgsPointXY, QgsProject, QgsWkbTypes
    from qgis.gui import QgsMapToolEmitPoint, QgsMapToolPan, QgsRubberBand, QgsVertexMarker
except ImportError:
    QgsCoordinateReferenceSystem = QgsCoordinateTransform = QgsDistanceArea = QgsPointXY = QgsProject = QgsWkbTypes = None
    QgsMapToolEmitPoint = QgsMapToolPan = QgsRubberBand = QgsVertexMarker = None
_MAP_TOOL_BASE = QgsMapToolEmitPoint if QgsMapToolEmitPoint is not None else object
def get_app_icon(name: str) -> QtGui.QIcon:
    path = os.path.join(tool_root(), "core", "app", "icons", name)
    if not os.path.isfile(path):
        path = os.path.join(tool_root(), "core", "app", name)
    if os.path.isfile(path) and QtGui is not None and hasattr(QtGui, "QIcon"):
        icon = QtGui.QIcon(path)
        if not icon.isNull():
            return icon
    try:
        from qgis.core import QgsApplication
        icon = QgsApplication.getThemeIcon('/' + name)
        if not icon.isNull():
            return icon
    except ImportError:
        pass
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


class Step3MeasureMapTool(_MAP_TOOL_BASE):
    """Interactive measurement tool displaying segment and total distances on the canvas."""

    def __init__(self, canvas, localizer=None):
        if QgsMapToolEmitPoint is not None:
            super().__init__(canvas)
        self.canvas = canvas
        self.localizer = localizer
        self.points: list = []
        self.rubber_band = None
        self.vertex_markers: list = []
        self.segment_labels: list = []
        self.cursor_badge = None
        self._init_ui()

    def _init_ui(self):
        if QtWidgets is None or self.canvas is None:
            return
        self.cursor_badge = QtWidgets.QLabel(self.canvas)
        self.cursor_badge.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(15, 23, 42, 0.90);"
            "  color: #ffffff;"
            "  font-size: 11px;"
            "  font-weight: bold;"
            "  border-radius: 4px;"
            "  padding: 3px 6px;"
            "}"
        )
        self.cursor_badge.setAttribute(qt_enum(Qt, "WA_TransparentForMouseEvents", "WidgetAttribute"), True)
        self.cursor_badge.hide()

    def _apply_cursor(self):
        if hasattr(self, "setCursor") and Qt is not None:
            try:
                self.setCursor(QtGui.QCursor(qt_enum(Qt, "CrossCursor", "CursorShape")))
            except Exception:
                pass

    def activate(self):
        if hasattr(super(), "activate"):
            super().activate()
        self._apply_cursor()
        self._ensure_rubber_band()
        if self.canvas and hasattr(self.canvas, "extentsChanged"):
            try:
                self.canvas.extentsChanged.connect(self._update_label_positions)
            except Exception:
                pass

    def deactivate(self):
        self.reset()
        if self.cursor_badge:
            self.cursor_badge.hide()
        if self.canvas and hasattr(self.canvas, "extentsChanged"):
            try:
                self.canvas.extentsChanged.disconnect(self._update_label_positions)
            except Exception:
                pass
        if hasattr(super(), "deactivate"):
            super().deactivate()

    def _ensure_rubber_band(self):
        if self.rubber_band is None and self.canvas is not None and QgsRubberBand is not None:
            geom_type = getattr(QgsWkbTypes, "LineGeometry", 1) if QgsWkbTypes is not None else 1
            self.rubber_band = QgsRubberBand(self.canvas, geom_type)
            self.rubber_band.setColor(QColor("#e11d48"))
            self.rubber_band.setWidth(2.5)

    def reset(self):
        self.points.clear()
        if self.rubber_band is not None:
            self.rubber_band.reset()
        for marker in self.vertex_markers:
            if hasattr(marker, "hide"):
                marker.hide()
        self.vertex_markers.clear()
        for _, label in self.segment_labels:
            if hasattr(label, "deleteLater"):
                label.deleteLater()
            elif hasattr(label, "hide"):
                label.hide()
        self.segment_labels.clear()
        if self.cursor_badge:
            self.cursor_badge.hide()

    def canvasPressEvent(self, event):
        button = event.button() if hasattr(event, "button") else None
        is_right = (button == qt_enum(Qt, "RightButton", "MouseButton"))
        if is_right:
            self.reset()
            return

        pt = self._event_map_point(event)
        if pt is None:
            return

        self._ensure_rubber_band()

        # If previous points exist, record segment label
        if len(self.points) >= 1:
            prev_pt = self.points[-1]
            seg_dist = self._calculate_distance(prev_pt, pt)
            mid_pt = QgsPointXY((prev_pt.x() + pt.x()) / 2.0, (prev_pt.y() + pt.y()) / 2.0)
            lbl = QtWidgets.QLabel(self._format_distance(seg_dist), self.canvas)
            lbl.setStyleSheet(
                "QLabel {"
                "  background-color: rgba(255, 255, 255, 0.94);"
                "  color: #0f172a;"
                "  font-size: 11px;"
                "  font-weight: bold;"
                "  border: 1px solid #94a3b8;"
                "  border-radius: 4px;"
                "  padding: 2px 5px;"
                "}"
            )
            lbl.setAttribute(qt_enum(Qt, "WA_TransparentForMouseEvents", "WidgetAttribute"), True)
            self.segment_labels.append((mid_pt, lbl))
            self._update_label_position(mid_pt, lbl)
            lbl.show()

        self.points.append(pt)

        # Add vertex marker
        if QgsVertexMarker is not None and self.canvas is not None:
            marker = QgsVertexMarker(self.canvas)
            marker.setColor(QColor("#e11d48"))
            marker.setFillColor(QColor("#ffffff"))
            marker.setIconSize(8)
            marker.setPenWidth(2)
            if hasattr(QgsVertexMarker, "ICON_CIRCLE"):
                marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
            marker.setCenter(pt)
            marker.show()
            self.vertex_markers.append(marker)

        self._update_rubber_band()

    def canvasMoveEvent(self, event):
        curr_pt = self._event_map_point(event)
        m_pos = event.pos() if hasattr(event, "pos") else None
        if curr_pt is None or m_pos is None:
            return

        if len(self.points) == 0:
            if self.cursor_badge:
                hint = self.localizer.get_string("map_measure_click_start", default="Klicken um zu messen") if self.localizer else "Klicken um zu messen"
                self.cursor_badge.setText(hint)
                self.cursor_badge.adjustSize()
                self.cursor_badge.move(m_pos.x() + 14, m_pos.y() + 14)
                self.cursor_badge.show()
                self.cursor_badge.raise_()
            return

        last_pt = self.points[-1]
        seg_dist = self._calculate_distance(last_pt, curr_pt)
        total_dist = self._total_committed_distance() + seg_dist

        if self.cursor_badge:
            tot_label = self.localizer.get_string("map_measure_total", default="Gesamt") if self.localizer else "Gesamt"
            if len(self.points) == 1:
                self.cursor_badge.setText(self._format_distance(seg_dist))
            else:
                self.cursor_badge.setText(f"{self._format_distance(seg_dist)} ({tot_label}: {self._format_distance(total_dist)})")
            self.cursor_badge.adjustSize()
            self.cursor_badge.move(m_pos.x() + 14, m_pos.y() + 14)
            self.cursor_badge.show()
            self.cursor_badge.raise_()

        # Dynamic rubber band to current mouse pos
        self._update_rubber_band(temp_pt=curr_pt)

    def keyPressEvent(self, event):
        if hasattr(event, "key") and event.key() == qt_enum(Qt, "Key_Escape", "Key"):
            self.reset()
        elif hasattr(super(), "keyPressEvent"):
            super().keyPressEvent(event)

    def _update_rubber_band(self, temp_pt=None):
        if self.rubber_band is None:
            return
        pts = list(self.points)
        if temp_pt is not None:
            pts.append(temp_pt)
        self.rubber_band.reset()
        for i, p in enumerate(pts):
            self.rubber_band.addPoint(p, i == len(pts) - 1)
        self.rubber_band.show()

    def _total_committed_distance(self) -> float:
        total = 0.0
        for i in range(len(self.points) - 1):
            total += self._calculate_distance(self.points[i], self.points[i + 1])
        return total

    def _calculate_distance(self, p1, p2) -> float:
        if QgsDistanceArea is not None:
            try:
                da = QgsDistanceArea()
                if hasattr(self.canvas, "mapSettings"):
                    dest_crs = self.canvas.mapSettings().destinationCrs()
                else:
                    dest_crs = getattr(self.canvas, "destinationCrs", lambda: None)()
                if dest_crs and dest_crs.isValid():
                    project = getattr(self, 'project', None) or QgsProject.instance()
                    ctx = project.transformContext()
                    da.setSourceCrs(dest_crs, ctx)
                if hasattr(da, "setEllipsoid"):
                    da.setEllipsoid("WGS84")
                return float(da.measureLine(p1, p2))
            except Exception:
                pass
        return math.hypot(p2.x() - p1.x(), p2.y() - p1.y())

    def _format_distance(self, meters: float) -> str:
        if meters < 1000.0:
            return f"{meters:.1f} m"
        return f"{meters / 1000.0:.2f} km"

    def _update_label_positions(self):
        for mid_pt, lbl in self.segment_labels:
            self._update_label_position(mid_pt, lbl)

    def _update_label_position(self, mid_pt, lbl):
        if self.canvas is None:
            return
        try:
            pix = self.canvas.getCoordinateTransform().transform(mid_pt)
            lbl.adjustSize()
            lbl.move(int(pix.x() - lbl.width() / 2), int(pix.y() - lbl.height() / 2))
            lbl.raise_()
        except Exception:
            pass

    def _event_map_point(self, event):
        if hasattr(event, "mapPoint"):
            return event.mapPoint()
        if hasattr(event, "pos") and self.canvas is not None:
            pos = event.pos()
            return self.canvas.getCoordinateTransform().toMapCoordinates(pos.x(), pos.y())
        return None


class FloatingMapControls(QtWidgets.QWidget if QtWidgets is not None else object):
    """Unified vertical map control overlay with tools, zoom, layer switcher, and CRS switcher."""

    def __init__(
        self,
        parent_canvas: Optional[object],
        on_mode_changed=None,
        on_layer_selected=None,
        on_crs_selected=None,
        localizer=None,
        navigation_only=False,
    ):
        if QtWidgets is None or parent_canvas is None:
            return
        super().__init__(parent_canvas)
        self.parent_canvas = parent_canvas
        self.on_mode_changed = on_mode_changed
        self.on_layer_selected = on_layer_selected
        self.on_crs_selected = on_crs_selected
        self.localizer = localizer
        self._current_layer = "osm"
        self._current_crs = "utm"

        self.setAttribute(qt_enum(Qt, "WA_TranslucentBackground", "WidgetAttribute"), True)
        self.setStyleSheet("background: transparent; border: none;")

        root_layout = QtWidgets.QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(6)

        # Popup Panels Container (Aligned at bottom next to switcher buttons)
        self.popup_container = QtWidgets.QWidget(self)
        self.popup_container.setAttribute(qt_enum(Qt, "WA_TranslucentBackground", "WidgetAttribute"), True)
        self.popup_container.setStyleSheet("background: transparent; border: none;")
        self.popup_container.setVisible(False)

        popup_layout = QtWidgets.QVBoxLayout(self.popup_container)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        popup_layout.setSpacing(4)
        popup_layout.setAlignment(qt_enum(Qt, "AlignBottom", "AlignmentFlag"))

        # 1. Popup Layer Selection Card
        self.layer_panel = QtWidgets.QFrame(self.popup_container)
        self.layer_panel.setFixedHeight(34)
        self.layer_panel.setStyleSheet(
            "QFrame {"
            "  background-color: rgba(255, 255, 255, 0.98);"
            "  border: 1px solid #cbd5e1;"
            "  border-radius: 6px;"
            "}"
        )
        layer_panel_layout = QtWidgets.QHBoxLayout(self.layer_panel)
        layer_panel_layout.setContentsMargins(3, 3, 3, 3)
        layer_panel_layout.setSpacing(4)

        osm_title = self.localizer.get_string("map_basemap_standard", default="Standard") if self.localizer else "Standard"
        sat_title = self.localizer.get_string("map_basemap_satellite", default="Satellit") if self.localizer else "Satellit"

        self.btn_layer_osm = self._create_card_button(self.layer_panel, "osm", osm_title, self._select_layer)
        self.btn_layer_sat = self._create_card_button(self.layer_panel, "satellite", sat_title, self._select_layer)

        layer_panel_layout.addWidget(self.btn_layer_osm)
        layer_panel_layout.addWidget(self.btn_layer_sat)
        self.layer_panel.setVisible(False)
        popup_layout.addWidget(self.layer_panel)

        # 2. Popup CRS Selection Card
        self.crs_panel = QtWidgets.QFrame(self.popup_container)
        self.crs_panel.setFixedHeight(34)
        self.crs_panel.setStyleSheet(
            "QFrame {"
            "  background-color: rgba(255, 255, 255, 0.98);"
            "  border: 1px solid #cbd5e1;"
            "  border-radius: 6px;"
            "}"
        )
        crs_panel_layout = QtWidgets.QHBoxLayout(self.crs_panel)
        crs_panel_layout.setContentsMargins(3, 3, 3, 3)
        crs_panel_layout.setSpacing(4)

        utm_title = self.localizer.get_string("map_crs_local_utm", default="Lokale UTM-Zone") if self.localizer else "Lokale UTM-Zone"
        merc_title = self.localizer.get_string("map_crs_webmercator", default="Web Mercator") if self.localizer else "Web Mercator"

        self.btn_crs_utm = self._create_card_button(self.crs_panel, "utm", utm_title, self._select_crs)
        self.btn_crs_merc = self._create_card_button(self.crs_panel, "mercator", merc_title, self._select_crs)

        crs_panel_layout.addWidget(self.btn_crs_utm)
        crs_panel_layout.addWidget(self.btn_crs_merc)
        self.crs_panel.setVisible(False)
        popup_layout.addWidget(self.crs_panel)

        root_layout.addWidget(self.popup_container, 0, qt_enum(Qt, "AlignBottom", "AlignmentFlag"))

        # Main Vertical Control Stack
        self.control_bar = QtWidgets.QFrame(self)
        self.control_bar.setObjectName("mapControlBar")
        self.control_bar.setStyleSheet(
            "#mapControlBar {"
            "  background-color: rgba(255, 255, 255, 0.96);"
            "  border: 1px solid #cbd5e1;"
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
            "QToolTip {"
            "  background-color: #ffffff;"
            "  color: #333333;"
            "  border: 1px solid #d0d5dd;"
            "  border-radius: 2px;"
            "  padding: 4px 7px;"
            "  font-size: 11px;"
            "}"
        )
        self.columns_layout = QtWidgets.QHBoxLayout(self.control_bar)
        self.columns_layout.setContentsMargins(0, 0, 0, 0)
        self.columns_layout.setSpacing(0)
        self.navigation_column = QtWidgets.QWidget(self.control_bar)
        self.columns_layout.addWidget(self.navigation_column)
        bar_layout = QtWidgets.QVBoxLayout(self.navigation_column)
        bar_layout.setContentsMargins(3, 4, 3, 4)
        bar_layout.setSpacing(2)

        self.btn_group = QtWidgets.QButtonGroup(self)
        self.btn_group.setExclusive(True)

        select_title = localizer.get_string("step3_button_select_cells", default="Zellen auswählen") if localizer else "Zellen auswählen"
        pan_title = localizer.get_string("step3_button_pan_map", default="Karte verschieben") if localizer else "Karte verschieben"
        measure_title = localizer.get_string("map_button_measure", default="Strecke messen") if localizer else "Strecke messen"
        adjust_title = localizer.get_string("step2_button_adjust_position", default="Position verschieben") if localizer else "Position verschieben"
        zoom_in_title = localizer.get_string("step3_button_zoom_in", default="Hineinzoomen (+)") if localizer else "Hineinzoomen (+)"
        zoom_out_title = localizer.get_string("step3_button_zoom_out", default="Herauszoomen (-)") if localizer else "Herauszoomen (-)"
        layer_title = localizer.get_string("map_button_layers", default="Hintergrundkarte wählen") if localizer else "Hintergrundkarte wählen"
        crs_title = localizer.get_string("map_button_crs", default="Koordinatensystem (KBS) wählen") if localizer else "Koordinatensystem (KBS) wählen"

        select_icon = get_app_icon("mActionSelectRectangle.svg") or self._create_tool_icon("select")
        pan_icon = get_app_icon("mActionPan.svg") or self._create_tool_icon("pan")
        measure_icon = get_app_icon("mActionMeasure.svg") or QtGui.QIcon()
        adjust_icon = get_app_icon("mCapturePoint.svg") or self._create_tool_icon("adjust_position")
        zoom_in_icon = self._create_tool_icon("zoom_in")
        zoom_out_icon = self._create_tool_icon("zoom_out")
        layers_icon = get_app_icon("mActionLayers.svg") or QtGui.QIcon()
        crs_icon = get_app_icon("CRS.svg") or QtGui.QIcon()

        self.btn_select = self._create_mode_button("select", select_icon, select_title)
        self.btn_pan = self._create_mode_button("pan", pan_icon, pan_title)
        self.btn_measure = self._create_mode_button("measure", measure_icon, measure_title)
        self.btn_adjust = self._create_mode_button("adjust_position", adjust_icon, adjust_title)

        self.btn_zoom_in = self._create_action_button(zoom_in_icon, zoom_in_title, self._zoom_in)
        self.btn_zoom_out = self._create_action_button(zoom_out_icon, zoom_out_title, self._zoom_out)
        self.btn_layers = self._create_action_button(layers_icon, layer_title, self._toggle_layer_panel)
        self.btn_crs = self._create_action_button(crs_icon, crs_title, self._toggle_crs_panel)

        bar_layout.addWidget(self.btn_select)
        bar_layout.addWidget(self.btn_pan)
        bar_layout.addWidget(self.btn_measure)
        bar_layout.addWidget(self.btn_adjust)
        bar_layout.addWidget(self._create_divider())  # Trennstrich vor dem +/- Block
        bar_layout.addWidget(self.btn_zoom_in)
        bar_layout.addWidget(self.btn_zoom_out)
        bar_layout.addWidget(self._create_divider())  # Trennstrich nach dem +/- Block
        bar_layout.addWidget(self.btn_layers)
        bar_layout.addWidget(self.btn_crs)
        if navigation_only:
            self.btn_select.hide()
            self.btn_adjust.hide()
            self.btn_pan.setChecked(True)

        root_layout.addWidget(self.control_bar)

        self.btn_select.setChecked(True)
        self.btn_group.buttonClicked.connect(self._on_mode_button_clicked)
        self._update_layer_card_styles()
        self._update_crs_card_styles()

        parent_canvas.installEventFilter(self)
        self.adjustSize()
        self.update_position()

    def _create_divider(self) -> QtWidgets.QFrame:
        """Erstellt einen feinen 1px-Trennstrich zwischen den Funktionsblöcken der Werkzeugleiste."""
        sep = QtWidgets.QFrame(self.control_bar)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #cbd5e1; margin: 3px 4px; border: none;")
        return sep

    def _create_mode_button(self, mode: str, icon: QtGui.QIcon, tooltip: str) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton(self.control_bar)
        btn.setIcon(icon)
        btn.setIconSize(QtCore.QSize(20, 20))
        btn.setFixedSize(34, 34)
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        btn.setToolTip(tooltip)
        btn.setProperty("map_mode", mode)
        self.btn_group.addButton(btn)
        return btn

    def _create_action_button(self, icon: QtGui.QIcon, tooltip: str, on_click) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton(self.control_bar)
        btn.setIcon(icon)
        btn.setIconSize(QtCore.QSize(20, 20))
        btn.setFixedSize(34, 34)
        btn.setCheckable(False)
        btn.setAutoRaise(True)
        btn.setToolTip(tooltip)
        btn.clicked.connect(on_click)
        return btn

    def _create_card_button(self, parent: QtWidgets.QWidget, item_id: str, label_text: str, handler) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(label_text, parent)
        btn.setFont(app_font(9, qfont_bold()))
        btn.setFixedHeight(26)
        btn.setProperty("item_id", item_id)
        btn.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        btn.clicked.connect(lambda: handler(item_id))
        return btn

    def _update_layer_card_styles(self):
        for btn in (self.btn_layer_osm, self.btn_layer_sat):
            lid = btn.property("item_id")
            if lid == self._current_layer:
                btn.setStyleSheet(
                    "QPushButton {"
                    "  background-color: #eff6ff;"
                    "  color: #1d4ed8;"
                    "  border: 2px solid #2563eb;"
                    "  border-radius: 4px;"
                    "  padding: 0px 10px;"
                    "  font-weight: bold;"
                    "}"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton {"
                    "  background-color: #ffffff;"
                    "  color: #334155;"
                    "  border: 1px solid #cbd5e1;"
                    "  border-radius: 4px;"
                    "  padding: 0px 10px;"
                    "}"
                    "QPushButton:hover {"
                    "  background-color: #f1f5f9;"
                    "  color: #0f172a;"
                    "  border-color: #94a3b8;"
                    "}"
                )

    def _update_crs_card_styles(self):
        for btn in (self.btn_crs_utm, self.btn_crs_merc):
            cid = btn.property("item_id")
            if cid == self._current_crs:
                btn.setStyleSheet(
                    "QPushButton {"
                    "  background-color: #eff6ff;"
                    "  color: #1d4ed8;"
                    "  border: 2px solid #2563eb;"
                    "  border-radius: 4px;"
                    "  padding: 0px 10px;"
                    "  font-weight: bold;"
                    "}"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton {"
                    "  background-color: #ffffff;"
                    "  color: #334155;"
                    "  border: 1px solid #cbd5e1;"
                    "  border-radius: 4px;"
                    "  padding: 0px 10px;"
                    "}"
                    "QPushButton:hover {"
                    "  background-color: #f1f5f9;"
                    "  color: #0f172a;"
                    "  border-color: #94a3b8;"
                    "}"
                )

    def _toggle_layer_panel(self):
        self.crs_panel.setVisible(False)
        new_vis = not self.layer_panel.isVisible()
        self.layer_panel.setVisible(new_vis)
        self.popup_container.setVisible(new_vis)
        self.adjustSize()
        self.update_position()

    def _toggle_crs_panel(self):
        self.layer_panel.setVisible(False)
        new_vis = not self.crs_panel.isVisible()
        self.crs_panel.setVisible(new_vis)
        self.popup_container.setVisible(new_vis)
        self.adjustSize()
        self.update_position()

    def _select_layer(self, layer_id: str):
        self._current_layer = layer_id
        self._update_layer_card_styles()
        self.layer_panel.setVisible(False)
        self.popup_container.setVisible(False)
        self.adjustSize()
        self.update_position()
        if self.on_layer_selected:
            self.on_layer_selected(layer_id)

    def _select_crs(self, crs_id: str):
        self._current_crs = crs_id
        self._update_crs_card_styles()
        self.crs_panel.setVisible(False)
        self.popup_container.setVisible(False)
        self.adjustSize()
        self.update_position()
        if self.on_crs_selected:
            self.on_crs_selected(crs_id)

    def _zoom_in(self):
        """+ zooms in (smaller geographic extent)."""
        if self.parent_canvas:
            self.parent_canvas.zoomByFactor(1.0 / 1.3)

    def _zoom_out(self):
        """- zooms out (larger geographic extent)."""
        if self.parent_canvas:
            self.parent_canvas.zoomByFactor(1.3)

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
                pen = QtGui.QPen(color, 2.0)
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
                    # Clean bold + symbol
                    painter.setPen(QtGui.QPen(color, 2.4, qt_enum(Qt, "SolidLine", "PenStyle"), qt_enum(Qt, "RoundCap", "PenCapStyle")))
                    painter.drawLine(5, 12, 19, 12)
                    painter.drawLine(12, 5, 12, 19)
                elif icon_type == "zoom_out":
                    # Clean bold - symbol
                    painter.setPen(QtGui.QPen(color, 2.4, qt_enum(Qt, "SolidLine", "PenStyle"), qt_enum(Qt, "RoundCap", "PenCapStyle")))
                    painter.drawLine(5, 12, 19, 12)
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

    def add_tool_column(self, widget):
        """Extend the same floating frame without moving the navigation tools."""
        self.columns_layout.insertWidget(0, widget, 0, qt_enum(Qt, 'AlignTop', 'AlignmentFlag'))
        self.update_position()

    def _on_mode_button_clicked(self, button):
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
            x = max(0, parent.width() - self.width() - 14)
            self.move(x, min(14, max(0, parent.height() - self.height())))
            self.raise_()


