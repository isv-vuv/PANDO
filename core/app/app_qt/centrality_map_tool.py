"""Two-click centre editing with a fixed screen-space hit tolerance."""
from core.app.app_qt.qt_base import Qt, QtGui, QColor, qt_enum
try:
    from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
    from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes
except ImportError:
    QgsMapTool = object


class CentralityMapTool(QgsMapTool):
    def __init__(self, owner):
        super().__init__(owner.canvas)
        self.owner = owner
        self.mode = 'add'
        self.selected = None
        self.band = QgsRubberBand(owner.canvas, QgsWkbTypes.LineGeometry)
        self.band.setColor(QColor('#2563eb'))
        self.band.setWidth(2)
        self.marker = QgsVertexMarker(owner.canvas)
        self.marker.setColor(QColor('#2563eb'))
        self.marker.setIconSize(16)
        self.marker.setPenWidth(3)
        self.marker.hide()
        self.setCursor(QtGui.QCursor(qt_enum(Qt, 'CrossCursor', 'CursorShape')))

    def reset(self):
        self.selected = None
        self.band.reset(QgsWkbTypes.LineGeometry)
        self.marker.hide()
        self.owner.canvas.refresh()

    def operation_cursor(self):
        pixmap = QtGui.QPixmap(40, 40)
        pixmap.fill(qt_enum(Qt, 'transparent', 'GlobalColor'))
        painter = QtGui.QPainter(pixmap)
        painter.setPen(QtGui.QPen(QColor('#ffffff'), 3))
        painter.drawLine(1, 11, 21, 11)
        painter.drawLine(11, 1, 11, 21)
        painter.setPen(QtGui.QPen(QColor('#b91c1c' if self.mode == 'delete' else '#1d4ed8'), 1))
        painter.drawLine(1, 11, 21, 11)
        painter.drawLine(11, 1, 11, 21)
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(18)
        painter.setFont(font)
        painter.drawText(22, 31, {'add': '+', 'level': '≡', 'move': '↔', 'delete': '×'}[self.mode])
        painter.end()
        self.setCursor(QtGui.QCursor(pixmap, 11, 11))

    def deactivate(self):
        self.reset()
        super().deactivate()

    def keyPressEvent(self, event):
        if event.key() == qt_enum(Qt, 'Key_Escape', 'Key'):
            self.reset()

    def canvasMoveEvent(self, event):
        if self.selected:
            from core.app.app_qt.centrality_editor import geometry
            start = geometry(self.selected[self.owner.editor.points.geometry]).asPoint()
            start = self.owner.editor.transform(start, self.owner.editor.layer.crs(), self.owner.canvas.mapSettings().destinationCrs())
            self.band.setToGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(start), event.mapPoint()]), None)

    def canvasReleaseEvent(self, event):
        if event.button() == qt_enum(Qt, 'RightButton', 'MouseButton'):
            self.reset()
            return
        if event.button() != qt_enum(Qt, 'LeftButton', 'MouseButton'):
            return
        self.owner.edit_click(self, event.mapPoint(), event.pos())
