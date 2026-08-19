"""Reusable QGIS map preview widget with high-quality OSM rendering."""

from __future__ import annotations

import math
from typing import Optional
from urllib.parse import quote

from core.app.app_qt.qt_base import QColor, QCursor, Qt, QtCore, QtWidgets, qt_enum, require_qgis_qt


try:
    from qgis.core import (
        QgsApplication,
        QgsCoordinateReferenceSystem,
        QgsPointXY,
        QgsProject,
        QgsProviderRegistry,
        QgsRasterLayer,
        QgsRectangle,
    )
    from qgis.gui import QgsMapCanvas, QgsVertexMarker

    QGIS_MAP_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only hit outside QGIS Python.
    QgsApplication = QgsCoordinateReferenceSystem = QgsPointXY = QgsProject = None
    QgsProviderRegistry = QgsRasterLayer = QgsRectangle = None
    QgsMapCanvas = QgsVertexMarker = None
    QGIS_MAP_IMPORT_ERROR = exc


_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
APP_USER_AGENT = "PANDO V1.0 (Urban-Act Tool)"

# Central Europe / Germany overview extent in EPSG:3857 (fills 16:9 and 4:3 previews without white bars)
CENTRAL_EUROPE_OVERVIEW = (330000.0, 5780000.0, 1900000.0, 7560000.0)


def create_high_quality_osm_layer(name: str = "OpenStreetMap") -> Optional[QgsRasterLayer]:
    """Creates a high quality OpenStreetMap XYZ raster layer with bilinear resampling."""
    if QgsRasterLayer is None:
        return None
    encoded_url = quote(OSM_TILE_URL, safe=":/")
    encoded_ua = quote(APP_USER_AGENT)
    uri = f"type=xyz&url={encoded_url}&zmax=19&zmin=0&crs=EPSG3857&http-header:User-Agent={encoded_ua}"
    layer = QgsRasterLayer(uri, name, "wms")
    if layer.isValid():
        try:
            from qgis.core import QgsBilinearRasterResampler
            resampler = QgsBilinearRasterResampler()
            if hasattr(layer, "resampleFilter") and layer.resampleFilter():
                layer.resampleFilter().setZoomedInResampler(resampler)
                layer.resampleFilter().setZoomedOutResampler(resampler)
        except Exception:
            pass
    return layer


def setup_map_canvas(canvas: QgsMapCanvas) -> None:
    """Configures a QgsMapCanvas for high quality, anti-aliased rendering."""
    if canvas is None:
        return
    canvas.setCanvasColor(QColor("#f7f8fa"))
    if hasattr(canvas, "setDestinationCrs") and QgsCoordinateReferenceSystem is not None:
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    if hasattr(canvas, "enableAntiAliasing"):
        canvas.enableAntiAliasing(True)
    if hasattr(canvas, "setCachingEnabled"):
        canvas.setCachingEnabled(True)


class OsmAttributionOverlay(QtWidgets.QLabel if QtWidgets is not None else object):
    """Clickable OpenStreetMap attribution overlay for QGIS map canvases."""

    def __init__(self, parent_canvas: Optional[object], localizer=None):
        if QtWidgets is None or parent_canvas is None:
            return
        super().__init__(parent_canvas)
        label_text = "© OpenStreetMap-Mitwirkende"
        if localizer:
            label_text = localizer.get_string("osm_copyright", default=label_text)
        elif hasattr(parent_canvas, "localizer") and parent_canvas.localizer:
            label_text = parent_canvas.localizer.get_string("osm_copyright", default=label_text)
        self.setText(f'<a href="https://www.openstreetmap.org/copyright" style="color: #0055aa; text-decoration: none;">{label_text}</a>')
        self.setOpenExternalLinks(True)
        self.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(255, 255, 255, 0.85);"
            "  color: #333333;"
            "  border-top-left-radius: 4px;"
            "  padding: 2px 6px;"
            "  font-size: 11px;"
            "}"
        )
        if Qt is not None and QCursor is not None:
            try:
                self.setCursor(QCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape")))
            except Exception:
                pass
        parent_canvas.installEventFilter(self)
        self.update_position()

    def eventFilter(self, obj, event):
        if obj == self.parent() and QtCore is not None and hasattr(event, "type") and event.type() == qt_enum(QtCore.QEvent, "Resize", "Type"):
            self.update_position()
        return super().eventFilter(obj, event)

    def update_position(self):
        parent = self.parent()
        if parent is None or not hasattr(parent, "width"):
            return
        self.adjustSize()
        margin = 2
        x = max(0, parent.width() - self.width() - margin)
        y = max(0, parent.height() - self.height() - margin)
        self.move(x, y)
        self.raise_()


class MapPreviewWidget(_Q_WIDGET_BASE):
    """Small map canvas centered on a selected WGS84 location with high quality OSM preview."""

    TILE_URL = OSM_TILE_URL

    def __init__(self, parent: Optional[object] = None):
        require_qgis_qt()
        if QGIS_MAP_IMPORT_ERROR is not None:
            raise RuntimeError(f"QGIS map canvas could not be imported: {QGIS_MAP_IMPORT_ERROR}")
        super().__init__(parent)
        self.canvas = None
        self.marker = None
        self.osm_layer = None
        self.error_label = None
        self.attribution_overlay = None
        self._build_ui()
        self.show_world()

    def show_location(self, location) -> None:
        lat = getattr(location, "latitude", None)
        lon = getattr(location, "longitude", None)
        if lat is None or lon is None:
            return
        self.set_marker(float(lat), float(lon))

    def show_world(self) -> None:
        """Shows a comfortable Central Europe overview that fills the canvas without white borders."""
        if self.marker is not None:
            self.marker.hide()
        if self.canvas is not None and QgsRectangle is not None:
            xmin, ymin, xmax, ymax = CENTRAL_EUROPE_OVERVIEW
            overview_extent = QgsRectangle(xmin, ymin, xmax, ymax)
            self.canvas.setExtent(overview_extent)
            self.canvas.setRenderFlag(True)
            self.canvas.refresh()

    def clear_marker(self) -> None:
        self.show_world()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.error_label = QtWidgets.QPlainTextEdit(self)
        self.error_label.setReadOnly(True)
        self.error_label.setMaximumHeight(180)
        self.error_label.setStyleSheet("color: #8a1f11; padding: 8px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.canvas = QgsMapCanvas(self)
        setup_map_canvas(self.canvas)
        self.canvas.setRenderFlag(False)

        self.osm_layer = create_high_quality_osm_layer("OpenStreetMap")
        if self.osm_layer and self.osm_layer.isValid():
            QgsProject.instance().addMapLayer(self.osm_layer, False)
            self.canvas.setLayers([self.osm_layer])
        else:
            self.canvas.setLayers([])
            self._show_layer_error()

        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setColor(QColor("#2563eb"))
        self.marker.setIconSize(14)
        self.marker.setPenWidth(3)
        if hasattr(QgsVertexMarker, "ICON_CIRCLE"):
            self.marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
        self.marker.hide()

        layout.addWidget(self.canvas)
        self.attribution_overlay = OsmAttributionOverlay(self.canvas)

    def _create_osm_layer(self):
        return create_high_quality_osm_layer("OpenStreetMap")

    def set_marker(self, lat: float, lon: float) -> None:
        x, y = lonlat_to_web_mercator(lon, lat)
        point = QgsPointXY(x, y)
        self.marker.setCenter(point)
        self.marker.show()
        self.canvas.setCenter(point)
        # Closer zoom on the searched city (approx. 18 km radius around location)
        self.canvas.setExtent(QgsRectangle(x - 18000, y - 18000, x + 18000, y + 18000))
        self.canvas.setRenderFlag(True)
        self.canvas.refresh()

    def _show_layer_error(self) -> None:
        if self.error_label is None or self.osm_layer is None:
            return
        message = "OpenStreetMap preview layer could not be loaded."
        try:
            error_summary = self.osm_layer.error().summary()
            if error_summary:
                message = f"{message}\n{error_summary}"
        except Exception:
            pass
        diagnostics = self._provider_diagnostics()
        if diagnostics:
            message = f"{message}\n\n{diagnostics}"
        self.error_label.setPlainText(message)
        self.error_label.show()

    def _provider_diagnostics(self) -> str:
        details = []
        try:
            providers = QgsProviderRegistry.instance().providerList()
            details.append(f"Loaded providers: {', '.join(providers)}")
        except Exception as exc:
            details.append(f"Could not read provider list: {exc}")
        try:
            details.append(QgsApplication.showSettings())
        except Exception:
            pass
        return "\n".join(details)


def lonlat_to_web_mercator(lon: float, lat: float) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * 20037508.34 / 180.0
    return x, y
