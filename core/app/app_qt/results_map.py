"""Embedded, independently owned QGIS project for the results page."""

from __future__ import annotations

import logging

from core.app.app_qt.map_preview import OsmAttributionOverlay, setup_map_canvas
from core.app.app_qt.qt_base import Qt, QtCore, QtGui, QtWidgets, qt_enum, require_qgis_qt, pyqtSignal
from core.app.app_qt.map_controls import FloatingMapControls, Step3PanMapTool, Step3MeasureMapTool, get_app_icon

try:
    from qgis.core import (
        QgsCoordinateTransform,
        QgsLayerTreeModel,
        QgsProject,
        QgsRectangle,
    )
    from qgis.gui import QgsLayerTreeView, QgsMapCanvas, QgsMapToolPan
except ImportError:  # Allows importing the application in non-QGIS test environments.
    QgsProject = None

logger = logging.getLogger(__name__)
_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
CENTRALITY_CLICK_RADIUS_PX = 16


class ResultsMapWidget(_Q_WIDGET_BASE):
    """Display exported result projects without modifying the global QGIS project."""

    edits_changed = pyqtSignal() if pyqtSignal else None

    def __init__(self, localizer, parent=None, edit_session=None):
        super().__init__(parent)
        require_qgis_qt()
        self.localizer = localizer
        self.project = QgsProject(self)
        self._extent = None
        self._loading = False
        self.edit_session = edit_session
        self.editor = None
        self._base_crs = None
        self._planning_view = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QtWidgets.QLabel(self._text("step6_map_title"), self)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        toolbar = QtWidgets.QHBoxLayout()
        self.zoom_in_button = QtWidgets.QPushButton("+", self)
        self.zoom_in_button.setToolTip(self._text("step6_map_zoom_in"))
        self.zoom_out_button = QtWidgets.QPushButton("−", self)
        self.zoom_out_button.setToolTip(self._text("step6_map_zoom_out"))
        self.zoom_results_button = QtWidgets.QPushButton(self._text("step6_map_zoom_results"), self)
        for button in (self.zoom_in_button, self.zoom_out_button, self.zoom_results_button):
            toolbar.addWidget(button)
        self.zoom_in_button.hide()
        self.zoom_out_button.hide()
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.edit_bar = QtWidgets.QWidget(self)
        edit_layout = QtWidgets.QGridLayout(self.edit_bar)
        edit_layout.setContentsMargins(3, 4, 3, 4)
        edit_layout.setSpacing(2)
        self.target_layer = QtWidgets.QComboBox(self.edit_bar)
        self.target_layer.setToolTip(self._text('centres_target'))
        self.target_layer.hide()
        self.target_button = QtWidgets.QToolButton(self.edit_bar)
        self.target_button.setIcon(get_app_icon('mActionLayers.svg'))
        self.target_button.setIconSize(QtCore.QSize(20, 20))
        self.target_button.setFixedSize(34, 34)
        self.target_button.setAutoRaise(True)
        self.target_button.setToolTip(self._text('centres_target'))
        self.target_menu = QtWidgets.QMenu(self.target_button)
        self.target_menu.setStyleSheet(
            'QMenu { background-color: #ffffff; color: #111827; border: 1px solid #cbd5e1; padding: 4px; }'
            'QMenu::item { background-color: #ffffff; color: #111827; padding: 6px 24px; }'
            'QMenu::item:selected { background-color: #dbeafe; color: #111827; }'
            'QMenu::item:disabled { color: #64748b; }'
        )
        self.target_button.setMenu(self.target_menu)
        self.target_button.setPopupMode(qt_enum(QtWidgets.QToolButton, 'InstantPopup', 'ToolButtonPopupMode'))
        edit_layout.addWidget(self.target_button, 0, 0, 1, 2, qt_enum(Qt, 'AlignHCenter', 'AlignmentFlag'))
        self.edit_buttons = {}
        for mode, icon in (('add', 'mActionAdd.svg'), ('level', 'mActionIdentify.svg'), ('move', 'mActionMoveFeature.svg'), ('delete', 'mActionDeleteSelected.svg')):
            button = QtWidgets.QToolButton(self.edit_bar)
            button.setIcon(get_app_icon(icon))
            button.setText(self._text('centres_' + mode))
            button.setToolTip(button.text())
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, m=mode: self._edit_mode(m))
            button.setIconSize(QtCore.QSize(20, 20))
            button.setFixedSize(34, 34)
            button.setAutoRaise(True)
            index = len(self.edit_buttons)
            edit_layout.addWidget(button, 1 + index // 2, index % 2)
            self.edit_buttons[mode] = button
        self.history_buttons = []
        for action, icon, shortcut in (('undo', 'mActionUndo.svg', 'Ctrl+Z'), ('redo', 'mActionRedo.svg', 'Ctrl+Shift+Z')):
            button = QtWidgets.QToolButton(self.edit_bar)
            button.setIcon(get_app_icon(icon))
            button.setText(self._text('centres_' + action))
            button.setToolTip(button.text())
            button.setShortcut(QtGui.QKeySequence(shortcut))
            button.clicked.connect(lambda checked=False, a=action: self._history(a))
            button.setIconSize(QtCore.QSize(20, 20))
            button.setFixedSize(34, 34)
            button.setAutoRaise(True)
            edit_layout.addWidget(button, 4, len(self.history_buttons))
            self.history_buttons.append(button)
        divider = QtWidgets.QFrame(self.edit_bar)
        divider.setFixedHeight(1)
        divider.setStyleSheet('background: #cbd5e1; margin: 3px 4px;')
        edit_layout.addWidget(divider, 3, 0, 1, 2)
        self.edit_bar.hide()

        self.canvas = QgsMapCanvas(self)
        # The canvas stops its render jobs before QObject destroys its project.
        self.project.setParent(self.canvas)
        self.canvas.setProject(self.project)
        setup_map_canvas(self.canvas)
        self.canvas.setMinimumSize(280, 240)
        self.canvas.setRenderFlag(False)
        self.pan_tool = Step3PanMapTool(self.canvas)
        self.measure_tool = Step3MeasureMapTool(self.canvas, localizer)
        self.measure_tool.project = self.project
        self.canvas.setMapTool(self.pan_tool)
        self.map_controls = FloatingMapControls(self.canvas, on_mode_changed=self._navigation_mode,
            on_layer_selected=self._basemap, on_crs_selected=self._crs, localizer=localizer, navigation_only=True)
        self.map_controls.add_tool_column(self.edit_bar)
        self.map_controls.btn_select.setChecked(False)
        self.map_controls.btn_pan.setChecked(True)
        from core.app.app_qt.centrality_map_tool import CentralityMapTool
        self.edit_tool = CentralityMapTool(self)
        self.target_layer.currentIndexChanged.connect(self._target_changed)
        self.attribution = OsmAttributionOverlay(self.canvas, localizer)
        layout.addWidget(self.canvas, 1)

        self.layer_view = QgsLayerTreeView(self)
        self.layer_model = QgsLayerTreeModel(self.project.layerTreeRoot(), self)
        self.layer_model.setFlag(QgsLayerTreeModel.Flag.AllowNodeChangeVisibility, True)
        self.layer_view.setModel(self.layer_model)
        self.layer_view.setHeaderHidden(True)
        self.layer_view.setMaximumHeight(190)
        layout.addWidget(self.layer_view)

        self.status = QtWidgets.QLabel(self._text("step6_map_hint"), self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.zoom_in_button.clicked.connect(self.canvas.zoomIn)
        self.zoom_out_button.clicked.connect(self.canvas.zoomOut)
        self.zoom_results_button.clicked.connect(self.zoom_to_results)
        self.project.layerTreeRoot().visibilityChanged.connect(self._sync_layers)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.clear)
        self._set_controls_enabled(False)

    def _text(self, key):
        return self.localizer.get_string(key)

    def retranslate_ui(self):
        self.zoom_results_button.setText(self._text('step6_map_zoom_planning' if self._planning_view else 'step6_map_zoom_results'))
        if self._planning_view:
            self.heading.setText(self._text('step6_map_planning'))
        self.target_layer.setToolTip(self._text('centres_target'))
        self._update_target_menu()
        for mode, button in self.edit_buttons.items():
            button.setText(self._text('centres_' + mode))
            button.setToolTip(button.text())
        for action, button in zip(('undo', 'redo'), self.history_buttons):
            button.setText(self._text('centres_' + action))
        self._update_history()

    def _set_controls_enabled(self, enabled):
        for widget in (self.zoom_in_button, self.zoom_out_button, self.zoom_results_button, self.layer_view):
            widget.setEnabled(enabled)
        self.map_controls.setEnabled(enabled)

    def clear(self):
        """Stop rendering before releasing providers and their file handles."""
        self._loading = True
        self.edit_tool.reset()
        self.measure_tool.reset()
        self.canvas.setMapTool(self.pan_tool)
        self.editor = None
        self.map_controls.btn_group.setExclusive(False)
        for button in self.map_controls.btn_group.buttons():
            button.setChecked(False)
        self.map_controls.btn_pan.setChecked(True)
        self.map_controls.btn_group.setExclusive(True)
        for button in self.edit_buttons.values():
            button.setChecked(False)
        self.target_layer.clear()
        self.edit_bar.hide()
        self.map_controls.update_position()
        self.canvas.setRenderFlag(False)
        self.canvas.stopRendering()
        self.canvas.setLayers([])
        self.canvas.clearCache()
        self.project.clear()
        self._extent = None
        self._planning_view = False
        self.zoom_results_button.setText(self._text('step6_map_zoom_results'))
        self._loading = False
        self.heading.setText(self._text("step6_map_title"))
        self.status.setText(self._text("step6_map_hint"))
        self._set_controls_enabled(False)

    def show_planning_area(self, project_path):
        """Show only a basemap, fitted to the PA input; never register result layers."""
        from pathlib import Path
        from qgis.core import QgsVectorLayer, QgsCoordinateReferenceSystem
        self.clear()
        root = Path(project_path)
        candidates = (
            (root / 'input/zone_type_selected.gpkg', True),
            (root / 'processed/qgis_output/model4_TierAssign/grid_zone_pa_ia1_ia2.gpkg', True),
            (root / 'processed/qgis_output/model4_TierAssign/extent_paia1.gpkg', False),
            (root / 'input/center_point.gpkg', False),
        )
        bounds = None
        source_crs = None
        for path, pa_only in candidates:
            if not path.is_file():
                continue
            layer = QgsVectorLayer(str(path), 'Planning extent', 'ogr')
            if not layer.isValid() or not layer.crs().isValid():
                continue
            if pa_only:
                zone_field = next((field.name() for field in layer.fields()
                                   if field.name().lower().replace('_', '').replace(' ', '') == 'zonetype'), None)
                if zone_field is None:
                    continue
                layer.setSubsetString('"' + zone_field.replace('"', '""') + '" = \'PA\'')
            for feature in layer.getFeatures():
                geometry = feature.geometry()
                if geometry.isNull() or geometry.isEmpty():
                    continue
                if bounds is None:
                    bounds = QgsRectangle(geometry.boundingBox())
                else:
                    bounds.combineExtentWith(geometry.boundingBox())
            if bounds is not None:
                source_crs = layer.crs()
                break
        if bounds is None:
            return False
        crs = source_crs
        crs_file = root / 'input/local_crs.txt'
        if crs_file.is_file():
            try:
                local_crs = QgsCoordinateReferenceSystem(crs_file.read_text(encoding='utf-8').strip())
                if local_crs.isValid():
                    crs = local_crs
            except OSError:
                logger.warning('Could not read planning CRS from %s', crs_file)
        self.project.setCrs(crs)
        self.canvas.setDestinationCrs(crs)
        self._base_crs = crs
        if source_crs != crs:
            bounds = QgsCoordinateTransform(source_crs, crs, self.project).transformBoundingBox(bounds)
        if bounds.width() <= 0 or bounds.height() <= 0:
            # Projects with only a saved centre still get a useful town-scale view.
            bounds.grow(0.1 if crs.isGeographic() else 15000)
        bounds.scale(1.08)
        self._extent = bounds
        self._planning_view = True
        self.heading.setText(self._text('step6_map_planning'))
        self.zoom_results_button.setText(self._text('step6_map_zoom_planning'))
        self.map_controls._current_crs = 'mercator' if crs.authid() == 'EPSG:3857' else 'utm'
        self.map_controls._update_crs_card_styles()
        self._basemap(self.map_controls._current_layer)
        self._set_controls_enabled(True)
        self.zoom_to_results()
        self.canvas.setRenderFlag(True)
        self.canvas.refresh()
        return True

    def show_project(self, path, title):
        self.clear()
        self._loading = True
        try:
            if not self.project.read(str(path)):
                raise ValueError("Could not read result project")
            result_layers = [layer for layer in self.project.mapLayers().values()
                             if layer.isValid() and layer.providerType() != "wms"]
            if not result_layers:
                raise ValueError("Result project contains no valid data layers")
            self.canvas.setDestinationCrs(self.project.crs())
            self._base_crs = self.project.crs()
            self._extent = self._result_extent(result_layers)
            if self._extent and (self._base_crs.isGeographic() or self._base_crs.authid() == 'EPSG:3857'):
                from qgis.core import QgsCoordinateReferenceSystem
                from core.app.app_core.geo import get_utm_epsg
                centre = QgsCoordinateTransform(self.project.crs(), QgsCoordinateReferenceSystem('EPSG:4326'), self.project).transform(self._extent.center())
                utm = get_utm_epsg(centre.y(), centre.x())
                if utm:
                    self._base_crs = QgsCoordinateReferenceSystem(utm)
            self.map_controls._current_crs = 'mercator' if self.project.crs().authid() == 'EPSG:3857' else 'utm'
            self.map_controls._update_crs_card_styles()
            self.heading.setText(title)
            invalid_count = sum(not layer.isValid() for layer in self.project.mapLayers().values())
            self.status.setText(self._text("step6_map_partial") if invalid_count else self._text("step6_map_navigation"))
            self.layer_view.expandAll()
            self._set_controls_enabled(True)
        except Exception:
            logger.exception("Could not display result project %s", path)
            self.clear()
            self.status.setText(self._text("step6_map_error"))
            return False
        finally:
            self._loading = False
        self.attribution.set_mode(self.map_controls._current_layer)
        if self.map_controls._current_layer == 'satellite':
            self._basemap('satellite')
        self._sync_layers()
        self.zoom_to_results()
        self.canvas.setRenderFlag(True)
        self.canvas.refresh()
        self._refresh_targets()
        return True

    def _result_extent(self, layers):
        extent = None
        for layer in layers:
            try:
                if not layer.crs().isValid():
                    continue
                bounds = layer.extent()
                if bounds.isNull() and getattr(layer, "featureCount", lambda: 0)() == 0:
                    continue
                if layer.crs() != self.project.crs():
                    transform = QgsCoordinateTransform(layer.crs(), self.project.crs(), self.project)
                    bounds = transform.transformBoundingBox(bounds)
                if extent is None:
                    extent = QgsRectangle(bounds)
                else:
                    extent.combineExtentWith(bounds)
            except Exception:
                logger.warning("Could not transform result extent for %s", layer.name(), exc_info=True)
        if extent is not None:
            # Single points and horizontal/vertical lines also need a visible extent.
            minimum = 0.01 if self.project.crs().isGeographic() else 100.0
            if extent.width() <= 0 or extent.height() <= 0:
                extent.grow(minimum)
            extent.scale(1.08)
        return extent

    def _sync_layers(self, *args):
        if self._loading:
            return
        layers = [node.layer() for node in self.project.layerTreeRoot().findLayers()
                  if node.isVisible() and node.layer() is not None and node.layer().isValid()]
        self.canvas.setLayers(layers)
        self.canvas.refresh()
        self._refresh_targets()

    def zoom_to_results(self):
        if self._extent is not None:
            bounds = self._extent
            if self.canvas.mapSettings().destinationCrs() != self.project.crs():
                bounds = QgsCoordinateTransform(self.project.crs(), self.canvas.mapSettings().destinationCrs(), self.project).transformBoundingBox(bounds)
            self.canvas.setExtent(bounds)
            self.canvas.refresh()

    def _navigation_mode(self, button):
        self.edit_tool.reset()
        for item in self.edit_buttons.values():
            item.setChecked(False)
        self.canvas.setMapTool(self.measure_tool if button.property('map_mode') == 'measure' else self.pan_tool)

    def _basemap(self, mode):
        from core.app.app_qt.map_preview import create_high_quality_osm_layer, create_satellite_layer
        self._loading = True
        for layer in list(self.project.mapLayers().values()):
            if layer.providerType() == 'wms':
                self.project.removeMapLayer(layer)
        layer = create_satellite_layer() if mode == 'satellite' else create_high_quality_osm_layer()
        if layer and layer.isValid():
            self.project.addMapLayer(layer, False)
            self.project.layerTreeRoot().addLayer(layer)
        self._loading = False
        self.attribution.set_mode(mode)
        self._sync_layers()

    def _crs(self, mode):
        from qgis.core import QgsCoordinateReferenceSystem
        self.edit_tool.reset()
        self.measure_tool.reset()
        target = QgsCoordinateReferenceSystem('EPSG:3857') if mode == 'mercator' else self._base_crs
        if target and target.isValid():
            bounds = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), target, self.project).transformBoundingBox(self.canvas.extent())
            self.canvas.setDestinationCrs(target)
            self.canvas.setExtent(bounds)
            self.canvas.refresh()

    def _refresh_targets(self):
        if self._loading or not self.edit_session:
            return
        from pathlib import Path
        from core.app.app_qt.centrality_editor import ROLES
        current = self.target_layer.currentData()
        self.target_layer.blockSignals(True)
        self.target_layer.clear()
        for layer in self.canvas.layers():
            if Path(layer.source().split('|')[0]).stem.lower() in ROLES:
                try:
                    from core.app.app_qt.centrality_editor import CentralityEditor
                    CentralityEditor(self.edit_session.root, layer)
                except (ValueError, KeyError, OSError):
                    continue
                self.target_layer.addItem(layer.name(), layer.id())
        index = self.target_layer.findData(current)
        self.target_layer.setCurrentIndex(max(index, 0))
        self.target_layer.blockSignals(False)
        self.edit_bar.setVisible(self.target_layer.count() > 0 or bool(self.edit_session.commands))
        self._update_target_menu()
        self.map_controls.update_position()
        self._target_changed()
        self._update_history()

    def _target_changed(self, *args):
        self.edit_tool.reset()
        self.editor = None
        layer = self.project.mapLayer(self.target_layer.currentData())
        if layer:
            from core.app.app_qt.centrality_editor import CentralityEditor
            self.editor = CentralityEditor(self.edit_session.root, layer)
        for button in self.edit_buttons.values():
            button.setEnabled(self.editor is not None and not self.edit_session.busy)
        self._update_target_menu()

    def _update_target_menu(self):
        self.target_button.setVisible(self.target_layer.count() > 1)
        self.target_button.setEnabled(self.target_layer.count() > 0)
        label = self._text('centres_target')
        if self.target_layer.currentText():
            label += ': ' + self.target_layer.currentText()
        self.target_button.setToolTip(label)
        labels = [self.target_layer.itemText(index) for index in range(self.target_layer.count())]
        if [action.text() for action in self.target_menu.actions()] == labels:
            for index, action in enumerate(self.target_menu.actions()):
                action.setChecked(index == self.target_layer.currentIndex())
            return
        self.target_menu.clear()
        for index in range(self.target_layer.count()):
            action = self.target_menu.addAction(self.target_layer.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == self.target_layer.currentIndex())
            action.triggered.connect(lambda checked=False, i=index: self.target_layer.setCurrentIndex(i))

    def _edit_mode(self, mode):
        if not self.editor or self.edit_session.busy:
            return
        self.measure_tool.reset()
        self.edit_tool.reset()
        self.edit_tool.mode = mode
        self.edit_tool.operation_cursor()
        self.map_controls.btn_group.setExclusive(False)
        for button in self.map_controls.btn_group.buttons():
            button.setChecked(False)
        self.map_controls.btn_group.setExclusive(True)
        for name, button in self.edit_buttons.items():
            button.setChecked(name == mode)
        self.canvas.setMapTool(self.edit_tool)
        self.status.setText(self._text('centres_hint_' + mode))

    def _update_history(self):
        if not self.edit_session:
            return
        session = self.edit_session
        for button, available, index in ((self.history_buttons[0], session.position > 0, session.position - 1),
                                         (self.history_buttons[1], session.position < len(session.commands), session.position)):
            button.setEnabled(available and not session.busy)
            label = session.commands[index].label if available else ''
            button.setToolTip(button.text() + (': ' + self._text('centres_' + label) if label else ''))

    def _reload_edits(self):
        self.edit_tool.reset()
        for layer in self.project.mapLayers().values():
            if layer.providerType() == 'ogr':
                layer.dataProvider().reloadData()
                layer.updateExtents()
                layer.triggerRepaint()
        self._extent = self._result_extent([layer for layer in self.project.mapLayers().values()
                                           if layer.isValid() and layer.providerType() != 'wms'])
        self.canvas.clearCache()
        self.canvas.refresh()
        self._update_history()
        if self.canvas.mapTool() is self.edit_tool:
            self.status.setText(self._text('centres_hint_' + self.edit_tool.mode))
        self.edits_changed.emit()

    def _history(self, action):
        try:
            getattr(self.edit_session, action)()
            self._reload_edits()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, self._text('centres_error'), str(exc))

    def _choose(self, rows, label):
        if len(rows) == 1:
            return rows[0]
        if not rows:
            return None
        choices = [label(row) for row in rows]
        text, ok = QtWidgets.QInputDialog.getItem(self, self._text('centres_choose'), self._text('centres_choose'), choices, 0, False)
        return rows[choices.index(text)] if ok else None

    def edit_click(self, tool, point, screen):
        if not self.editor or self.edit_session.busy:
            return
        from core.app.app_qt.centrality_editor import geometry
        from core.app.app_core.centrality_edits import centrality_level
        editor = self.editor
        crs = self.canvas.mapSettings().destinationCrs()
        try:
            row = tool.selected
            if tool.mode != 'add' and row is None:
                hits = []
                for candidate in editor.points.rows():
                    if not candidate[editor.points.geometry] or centrality_level(candidate[editor.points.field('TypeNo')]) not in (0, 1, 2, 3, 4):
                        continue
                    local = editor.transform(geometry(candidate[editor.points.geometry]).asPoint(), editor.layer.crs(), crs)
                    pixel = self.canvas.getCoordinateTransform().transform(local)
                    if (pixel.x() - screen.x()) ** 2 + (pixel.y() - screen.y()) ** 2 <= CENTRALITY_CLICK_RADIUS_PX ** 2:
                        hits.append(candidate)
                row = self._choose(hits, editor.label)
                if row is None:
                    return
                if tool.mode == 'move':
                    tool.selected = row
                    selected = editor.transform(geometry(row[editor.points.geometry]).asPoint(), editor.layer.crs(), crs)
                    tool.marker.setCenter(selected)
                    tool.marker.show()
                    self.status.setText(self._text('centres_move_target'))
                    return
            zone = None
            replaced = []
            if tool.mode in ('add', 'move'):
                zone = self._choose(editor.zones_at(point, crs), lambda z: editor.label(z, editor.zones))
                if zone is None:
                    self.status.setText(self._text('centres_no_zone'))
                    return
                replaced = editor.occupied(zone, row[editor.points.key] if row else None)
                central_replaced = [r for r in replaced if centrality_level(r[editor.points.field('TypeNo')]) in (0, 1, 2, 3, 4)]
                if central_replaced:
                    reply = QtWidgets.QMessageBox.question(self, self._text('centres_replace'),
                        self._text('centres_replace_question') + '\n' + '\n'.join(editor.label(r) for r in central_replaced),
                        qt_enum(QtWidgets.QMessageBox, 'Yes', 'StandardButton') | qt_enum(QtWidgets.QMessageBox, 'No', 'StandardButton'),
                        qt_enum(QtWidgets.QMessageBox, 'No', 'StandardButton'))
                    if reply != qt_enum(QtWidgets.QMessageBox, 'Yes', 'StandardButton'):
                        tool.reset()
                        return
            level = None
            if tool.mode in ('add', 'level'):
                labels = [self._text('centres_level_' + str(i)) for i in range(5)]
                value = row[editor.points.field('TypeNo')] if row else 4
                choice, ok = QtWidgets.QInputDialog.getItem(self, self._text('centres_level'), self._text('centres_level'), labels, int(value or 0), False)
                if not ok:
                    tool.reset()
                    return
                level = labels.index(choice)
            command = editor.command(tool.mode, row=row, point=point if tool.mode in ('add', 'move') else None,
                                     canvas_crs=crs, level=level, zone=zone, replaced=replaced)
            self.canvas.stopRendering()
            self.edit_session.execute(command)
            self._reload_edits()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, self._text('centres_error'), str(exc))

    def event(self, event):
        if event.type() == qt_enum(QtCore.QEvent, "DeferredDelete", "Type"):
            self.clear()
        return super().event(event)

    def closeEvent(self, event):
        self.clear()
        super().closeEvent(event)
