"""Qt implementation of Step 1: Place search and location selection combined."""

from __future__ import annotations

from typing import Optional

from core.app.app_qt.map_preview import MapPreviewWidget
from core.app.app_qt.qt_base import (
    Dialogs,
    Qt,
    QtCore,
    QtWidgets,
    app_font,
    create_step_header,
    pyqtSignal,
    qfont_bold,
    qt_enum,
    require_qgis_qt,
)

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object


def has_confirmable_selection(location) -> bool:
    return location is not None


class Step1SearchWidget(_Q_WIDGET_BASE):
    """Combined Step 1 widget: Search bar, geocoding results list, and map preview.

    The left pane contains the search box and the results list with radio buttons.
    The right pane displays an interactive map preview of the selected location.
    """

    search_requested = pyqtSignal(str) if pyqtSignal is not None else None
    selection_changed = pyqtSignal(object) if pyqtSignal is not None else None
    open_osm_requested = pyqtSignal(object) if pyqtSignal is not None else None
    selection_confirmed = pyqtSignal(object) if pyqtSignal is not None else None
    project_folder_open_requested = pyqtSignal() if pyqtSignal is not None else None
    project_file_open_requested = pyqtSignal() if pyqtSignal is not None else None
    language_switch_requested = pyqtSignal() if pyqtSignal is not None else None

    def __init__(
        self,
        localizer,
        parent: Optional[object] = None,
        project_path: str = "",
        workspace_path: str = "",
        locations: Optional[list] = None,
    ):
        require_qgis_qt()
        super().__init__(parent)
        self.localizer = localizer
        self.project_path = project_path
        self.workspace_path = workspace_path
        self.locations = []
        self.location_by_id = {}
        self.selected_location = None

        self.city_input = None
        self.search_button = None
        self.button_group = None
        self.results_layout = None
        self.empty_label = None
        self.open_osm_button = None
        self.confirm_button = None
        self.map_preview = None
        self.splitter = None

        self._build_ui()
        if locations:
            self.set_locations(locations)
        self._install_return_key_filter()

    def focus_city_input(self) -> None:
        if self.city_input is not None:
            self.city_input.setFocus(qt_enum(Qt, "OtherFocusReason", "FocusReason"))

    def city_name(self) -> str:
        if self.city_input is None:
            return ""
        return self.city_input.text().strip()

    def set_city_name(self, value: str) -> None:
        if self.city_input is not None:
            self.city_input.setText(value)

    def set_search_enabled(self, enabled: bool) -> None:
        if self.city_input is not None:
            self.city_input.setEnabled(enabled)
        if self.search_button is not None:
            self.search_button.setEnabled(enabled)

    def set_locations(self, locations: list) -> None:
        self.locations = [loc for loc in (locations or []) if hasattr(loc, "address")]
        self.location_by_id.clear()
        self.selected_location = None
        self._populate_results()

    def selected(self):
        return self.selected_location

    def _build_ui(self) -> None:
        self.setFocusPolicy(qt_enum(Qt, "StrongFocus", "FocusPolicy"))

        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(16, 10, 16, 10)
        root_layout.setSpacing(8)

        # Step Header spanning full window width
        header, _title_label, _step_label = create_step_header(
            self.localizer.get_string("step1_title"),
            current_step=1,
            total_steps=6,
            localizer=self.localizer,
            parent=self,
        )
        root_layout.addWidget(header)

        self.splitter = QtWidgets.QSplitter(qt_enum(Qt, "Horizontal", "Orientation"), self)
        self.splitter.setChildrenCollapsible(False)
        root_layout.addWidget(self.splitter)

        # Left Pane: Search + Results List + Actions
        left_pane = QtWidgets.QWidget(self.splitter)
        left_pane.setFocusPolicy(qt_enum(Qt, "StrongFocus", "FocusPolicy"))
        left_layout = QtWidgets.QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # Active project path indicator (if any)
        if self.project_path:
            project_text = self.localizer.get_string(
                "step1_active_project",
                workspace_path=self.workspace_path,
                project_path=self.project_path,
            )
            project_label = QtWidgets.QLabel(project_text, left_pane)
            project_label.setFont(app_font(9))
            project_label.setWordWrap(True)
            left_layout.addWidget(project_label)

        # Search Input Group
        search_box = QtWidgets.QGroupBox(self.localizer.get_string("step1_search_box", default="Ort suchen"), left_pane)
        search_box.setFont(app_font(10))
        search_box_layout = QtWidgets.QVBoxLayout(search_box)
        search_box_layout.setContentsMargins(12, 10, 12, 10)
        search_box_layout.setSpacing(8)

        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(8)

        self.city_input = QtWidgets.QLineEdit(search_box)
        self.city_input.setFont(app_font(10))
        self.city_input.setPlaceholderText(self.localizer.get_string("step1_placeholder", default="z. B. Stuttgart, Manila, Bandung..."))
        self.city_input.returnPressed.connect(self._request_search)
        input_row.addWidget(self.city_input, 1)

        self.search_button = QtWidgets.QPushButton(self.localizer.get_string("step1_search_button", default="Suchen"), search_box)
        self.search_button.setFont(app_font(10))
        self.search_button.clicked.connect(self._request_search)
        input_row.addWidget(self.search_button)

        search_box_layout.addLayout(input_row)
        left_layout.addWidget(search_box)

        # Results Label
        results_hdr = QtWidgets.QLabel(self.localizer.get_string("step1_results_found", default="Gefundene Orte:"), left_pane)
        results_hdr.setFont(app_font(10))
        left_layout.addWidget(results_hdr)

        # Scrollable Results List
        scroll_area = QtWidgets.QScrollArea(left_pane)
        scroll_area.setFocusPolicy(qt_enum(Qt, "StrongFocus", "FocusPolicy"))
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumWidth(0)
        scroll_area.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))

        results_host = QtWidgets.QWidget(scroll_area)
        results_host.setFocusPolicy(qt_enum(Qt, "StrongFocus", "FocusPolicy"))
        self.results_layout = QtWidgets.QVBoxLayout(results_host)
        self.results_layout.setContentsMargins(4, 4, 4, 4)
        self.results_layout.setSpacing(6)
        self.results_layout.addStretch(1)
        scroll_area.setWidget(results_host)
        left_layout.addWidget(scroll_area, 1)

        # OSM Link Button (Global next button in footer navigation bar will be used for Weiter)
        action_layout = QtWidgets.QVBoxLayout()
        action_layout.setSpacing(8)

        self.open_osm_button = QtWidgets.QPushButton(self.localizer.get_string("step2_button_check_osm"), left_pane)
        self.open_osm_button.setFont(app_font(10))
        self.open_osm_button.setEnabled(False)
        self.open_osm_button.clicked.connect(self._open_osm)
        action_layout.addWidget(self.open_osm_button)

        left_layout.addLayout(action_layout)

        # Right Pane: Interactive Map Preview
        self.map_preview = MapPreviewWidget(self.splitter)
        self.splitter.addWidget(left_pane)
        self.splitter.addWidget(self.map_preview)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        QtCore.QTimer.singleShot(0, self._apply_map_width)

        self._populate_results()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_map_width()

    def _apply_map_width(self) -> None:
        """Set equal 50-50 (1:1) split ratio between left pane and map preview."""
        if self.splitter is None or self.map_preview is None:
            return
        available = max(0, self.splitter.width() - self.splitter.handleWidth())
        half = max(0, available // 2)
        self.splitter.setSizes([half, half])

    def _request_search(self) -> None:
        city = self.city_name()
        if not city:
            Dialogs.warning(
                self,
                self.localizer.get_string("step1_warning_empty_city_title"),
                self.localizer.get_string("step1_warning_empty_city_message"),
            )
            return
        if self.search_requested is not None:
            self.search_requested.emit(city)

    def _populate_results(self) -> None:
        self._clear_results()
        self.button_group = QtWidgets.QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.button_group.idClicked.connect(self._select_location_by_id)

        if not self.locations:
            self.empty_label = QtWidgets.QLabel(
                self.localizer.get_string("step1_search_hint_empty", default="Geben Sie oben einen Ort ein und klicken Sie auf Suchen."), self
            )
            self.empty_label.setFont(app_font(10))
            self.empty_label.setWordWrap(True)
            self.results_layout.insertWidget(0, self.empty_label)
            if self.open_osm_button is not None:
                self.open_osm_button.setEnabled(False)
            if self.map_preview is not None:
                self.map_preview.show_world()
            return

        for index, loc in enumerate(self.locations):
            radio = QtWidgets.QRadioButton(loc.address, self)
            radio.setFont(app_font(10))
            radio.setToolTip(loc.address)
            radio.setSizePolicy(
                qt_enum(QtWidgets.QSizePolicy, "Ignored", "Policy"),
                qt_enum(QtWidgets.QSizePolicy, "Preferred", "Policy"),
            )
            radio.setMinimumHeight(28)
            radio.installEventFilter(self)
            self.location_by_id[index] = loc
            self.button_group.addButton(radio, index)
            self.results_layout.insertWidget(index, radio)

        first_button = self.button_group.button(0)
        if first_button is not None:
            first_button.setChecked(True)
            self._select_location_by_id(0)

    def _clear_results(self) -> None:
        if self.results_layout is None:
            return
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _select_location_by_id(self, location_id: int) -> None:
        self.selected_location = self.location_by_id.get(location_id)
        has_sel = self.selected_location is not None
        if self.open_osm_button is not None:
            self.open_osm_button.setEnabled(has_sel)
        if has_sel:
            if self.map_preview is not None:
                self.map_preview.show_location(self.selected_location)
            if self.selection_changed is not None:
                self.selection_changed.emit(self.selected_location)

    def _open_osm(self) -> None:
        if self.selected_location is not None and self.open_osm_requested is not None:
            self.open_osm_requested.emit(self.selected_location)

    def _confirm_selection(self) -> None:
        if has_confirmable_selection(self.selected_location) and self.selection_confirmed is not None:
            self.selection_confirmed.emit(self.selected_location)

    def _install_return_key_filter(self) -> None:
        self.installEventFilter(self)
        for child in self.findChildren(QtWidgets.QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == qt_enum(QtCore.QEvent, "KeyPress", "Type") and event.key() in (
            qt_enum(Qt, "Key_Return", "Key"),
            qt_enum(Qt, "Key_Enter", "Key"),
        ):
            # If focus is on city_input, Return triggers search; otherwise triggers confirm if a location is selected
            if self.city_input is not None and self.city_input.hasFocus():
                self._request_search()
                return True
            elif has_confirmable_selection(self.selected_location):
                self._confirm_selection()
                return True
        return super().eventFilter(watched, event)
