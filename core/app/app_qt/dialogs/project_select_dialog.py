"""Modal dialog for selecting and opening existing PANDO projects."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from core.app.app_core.project import (
    is_project_folder,
    load_project_metadata,
)
from core.app.app_qt.qt_base import (
    Dialogs,
    Qt,
    QtCore,
    QtWidgets,
    app_font,
    qfont_bold,
    qt_enum,
    require_qgis_qt,
)

_Q_DIALOG_BASE = QtWidgets.QDialog if QtWidgets is not None else object


def _format_datetime(iso_or_ts: Any) -> tuple[str, float]:
    """Returns (formatted_str, sortable_timestamp)."""
    if isinstance(iso_or_ts, (int, float)):
        try:
            dt = datetime.fromtimestamp(iso_or_ts)
            return dt.strftime("%d.%m.%Y %H:%M"), float(iso_or_ts)
        except Exception:
            return str(iso_or_ts), float(iso_or_ts)
    if isinstance(iso_or_ts, str) and iso_or_ts.strip():
        try:
            dt = datetime.fromisoformat(iso_or_ts)
            return dt.strftime("%d.%m.%Y %H:%M"), dt.timestamp()
        except Exception:
            return iso_or_ts, 0.0
    return "-", 0.0


def _extract_location_str(metadata: dict[str, Any]) -> str:
    loc = metadata.get("selected_location")
    if isinstance(loc, dict):
        addr = loc.get("address") or loc.get("display_name") or loc.get("name") or ""
        if not addr and isinstance(loc.get("raw"), dict):
            addr = loc["raw"].get("display_name") or loc["raw"].get("name") or ""
        if addr:
            parts = [p.strip() for p in addr.split(",") if p.strip()]
            if len(parts) > 2:
                return f"{parts[0]}, {parts[-1]}"
            return addr
    return "-"


def _format_step_label(step_num: Any) -> str:
    try:
        s = int(step_num)
        step_names = {
            0: "Schritt 0: Start",
            1: "Schritt 1: Ortssuche",
            2: "Schritt 2: PBF-Daten",
            3: "Schritt 3: Modellraum",
            4: "Schritt 4: QGIS-Modell",
            5: "Schritt 5: Visum-Import",
            6: "Schritt 6: Ergebnisse",
        }
        return step_names.get(s, f"Schritt {s}")
    except (TypeError, ValueError):
        return str(step_num or "-")


def scan_and_sort_projects(projects_dir: str) -> list[dict[str, Any]]:
    """Discovers and parses projects inside projects_dir, sorted descending by last modification."""
    projects: list[dict[str, Any]] = []
    if projects_dir and os.path.isdir(projects_dir):
        search_path = os.path.abspath(projects_dir)
        try:
            entries = sorted(os.listdir(search_path))
        except OSError:
            entries = []

        for entry in entries:
            full_path = os.path.join(search_path, entry)
            if not os.path.isdir(full_path):
                continue
            if is_project_folder(full_path):
                try:
                    metadata = load_project_metadata(full_path)
                except Exception:
                    metadata = {
                        "project_path": full_path,
                        "last_step": 1,
                        "updated_at": None,
                    }

                # Sort timestamp
                mtime = os.path.getmtime(full_path)
                date_str, sort_ts = _format_datetime(metadata.get("updated_at") or mtime)
                if sort_ts == 0.0:
                    sort_ts = mtime

                proj_item = {
                    "path": full_path,
                    "name": os.path.basename(full_path),
                    "location": _extract_location_str(metadata),
                    "date_str": date_str,
                    "sort_ts": sort_ts,
                    "step_str": _format_step_label(metadata.get("last_step", 1)),
                }
                projects.append(proj_item)

        # Sort descending: newest projects on top
        projects.sort(key=lambda p: p["sort_ts"], reverse=True)

    return projects


def filter_projects(projects: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Filters project items by case-insensitive matching across name, location, date, and step."""
    clean_query = (query or "").strip().lower()
    if not clean_query:
        return list(projects)
    return [
        p for p in projects
        if clean_query in p["name"].lower()
        or clean_query in p["location"].lower()
        or clean_query in p["date_str"].lower()
        or clean_query in p["step_str"].lower()
    ]


class ProjectSelectDialog(_Q_DIALOG_BASE):
    """Clean, searchable project picker displaying detected PANDO projects sorted by date."""

    def __init__(
        self,
        parent: Optional[object] = None,
        projects_dir: str = "",
        localizer: Optional[object] = None,
        dialog_title: Optional[str] = None,
        accept_button_text: Optional[str] = None,
    ):
        require_qgis_qt()
        super().__init__(parent)
        self.projects_dir = projects_dir
        self.localizer = localizer
        self.dialog_title = dialog_title or self._tr("dialog_project_select_title", "Projekt auswählen")
        self.accept_button_text = accept_button_text or self._tr("dialog_project_select_btn_open", "Projekt öffnen")
        self.selected_project_path: Optional[str] = None
        self._all_projects: list[dict[str, Any]] = []
        self._filtered_projects: list[dict[str, Any]] = []

        self.setWindowTitle(self.dialog_title)
        self.resize(760, 480)
        self.setMinimumSize(560, 360)

        self._build_ui()
        self._load_projects()

    def _tr(self, key: str, default: str) -> str:
        if self.localizer is not None and hasattr(self.localizer, "get_string"):
            return self.localizer.get_string(key, default=default)
        return default

    def _build_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # Header section
        header_layout = QtWidgets.QVBoxLayout()
        header_layout.setSpacing(4)

        title_label = QtWidgets.QLabel(self.dialog_title, self)
        title_label.setFont(app_font(13, qfont_bold()))
        header_layout.addWidget(title_label)

        sub_text = self._tr("dialog_project_select_subtitle", "Wählen Sie ein bestehendes PANDO-Projekt aus der Liste:")
        if self.projects_dir:
            sub_text += f" ({os.path.normpath(self.projects_dir)})"
        sub_label = QtWidgets.QLabel(sub_text, self)
        sub_label.setFont(app_font(9))
        sub_label.setStyleSheet("color: #64748b;")
        sub_label.setWordWrap(True)
        header_layout.addWidget(sub_label)

        main_layout.addLayout(header_layout)

        # Search filter bar
        self.search_input = QtWidgets.QLineEdit(self)
        self.search_input.setFont(app_font(10))
        self.search_input.setPlaceholderText(
            self._tr("dialog_project_select_search_placeholder", "Nach Projektname oder Ort filtern...")
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._apply_filter)
        main_layout.addWidget(self.search_input)

        # Table of projects
        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            self._tr("dialog_project_select_col_name", "Projektname"),
            self._tr("dialog_project_select_col_location", "Ort / Region"),
            self._tr("dialog_project_select_col_date", "Zuletzt geändert"),
            self._tr("dialog_project_select_col_step", "Status / Schritt"),
        ])
        self.table.setFont(app_font(9))
        self.table.setSelectionBehavior(qt_enum(QtWidgets.QAbstractItemView, "SelectRows", "SelectionBehavior"))
        self.table.setSelectionMode(qt_enum(QtWidgets.QAbstractItemView, "SingleSelection", "SelectionMode"))
        self.table.setEditTriggers(qt_enum(QtWidgets.QAbstractItemView, "NoEditTriggers", "EditTrigger"))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(
            "QTableWidget { border: 1px solid #cbd5e1; border-radius: 4px; background-color: #ffffff; }"
            "QTableWidget::item { padding: 6px 8px; }"
            "QTableWidget::item:selected { background-color: #dbeafe; color: #1e3a8a; }"
            "QHeaderView::section { background-color: #f1f5f9; padding: 6px 8px; font-weight: bold; border: none; border-bottom: 1px solid #cbd5e1; }"
        )

        h_header = self.table.horizontalHeader()
        h_header.setStretchLastSection(False)
        h_header.setSectionResizeMode(0, qt_enum(QtWidgets.QHeaderView, "Stretch", "ResizeMode"))
        h_header.setSectionResizeMode(1, qt_enum(QtWidgets.QHeaderView, "Stretch", "ResizeMode"))
        h_header.setSectionResizeMode(2, qt_enum(QtWidgets.QHeaderView, "ResizeToContents", "ResizeMode"))
        h_header.setSectionResizeMode(3, qt_enum(QtWidgets.QHeaderView, "ResizeToContents", "ResizeMode"))

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        main_layout.addWidget(self.table, 1)

        # Empty / No result message label (hidden by default)
        self.empty_label = QtWidgets.QLabel(self)
        self.empty_label.setFont(app_font(10))
        self.empty_label.setAlignment(qt_enum(Qt, "AlignCenter", "AlignmentFlag"))
        self.empty_label.setStyleSheet("color: #64748b; padding: 20px;")
        self.empty_label.setVisible(False)
        main_layout.addWidget(self.empty_label)

        # Action Buttons Row
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(8)

        self.browse_btn = QtWidgets.QPushButton(
            self._tr("dialog_project_select_btn_browse", "Anderen Ordner durchsuchen..."), self
        )
        self.browse_btn.setFont(app_font(9))
        self.browse_btn.setFixedHeight(34)
        self.browse_btn.clicked.connect(self._handle_browse_other)
        button_layout.addWidget(self.browse_btn)

        button_layout.addStretch(1)

        self.cancel_btn = QtWidgets.QPushButton(self._tr("dialog_project_select_btn_cancel", "Abbrechen"), self)
        self.cancel_btn.setFont(app_font(9))
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.open_btn = QtWidgets.QPushButton(self.accept_button_text, self)
        self.open_btn.setFont(app_font(9, qfont_bold()))
        self.open_btn.setFixedHeight(34)
        self.open_btn.setDefault(True)
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._handle_open)
        button_layout.addWidget(self.open_btn)

        main_layout.addLayout(button_layout)

    def _load_projects(self) -> None:
        self._all_projects = scan_and_sort_projects(self.projects_dir)
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self.search_input.text()
        self._filtered_projects = filter_projects(self._all_projects, query)
        self._render_table()

    def _render_table(self) -> None:
        self.table.setRowCount(0)
        has_items = len(self._filtered_projects) > 0

        if not self._all_projects:
            self.empty_label.setText(
                self._tr("dialog_project_select_empty", "Keine bestehenden PANDO-Projekte im Projektordner gefunden.")
            )
            self.empty_label.setVisible(True)
            self.table.setVisible(False)
            self.open_btn.setEnabled(False)
            return

        if not has_items:
            self.empty_label.setText(
                self._tr("dialog_project_select_no_match", "Keine Projekte gefunden, die dem Suchfilter entsprechen.")
            )
            self.empty_label.setVisible(True)
            self.table.setVisible(False)
            self.open_btn.setEnabled(False)
            return

        self.empty_label.setVisible(False)
        self.table.setVisible(True)
        self.table.setRowCount(len(self._filtered_projects))

        for row, proj in enumerate(self._filtered_projects):
            name_item = QtWidgets.QTableWidgetItem(proj["name"])
            name_item.setData(qt_enum(Qt, "UserRole", "ItemDataRole"), proj["path"])

            loc_item = QtWidgets.QTableWidgetItem(proj["location"])
            date_item = QtWidgets.QTableWidgetItem(proj["date_str"])
            step_item = QtWidgets.QTableWidgetItem(proj["step_str"])

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, loc_item)
            self.table.setItem(row, 2, date_item)
            self.table.setItem(row, 3, step_item)

        # Select first row by default
        if self.table.rowCount() > 0:
            self.table.selectRow(0)
            self.open_btn.setEnabled(True)

    def _on_selection_changed(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if selected_rows:
            row = selected_rows[0].row()
            item = self.table.item(row, 0)
            if item:
                self.selected_project_path = item.data(qt_enum(Qt, "UserRole", "ItemDataRole"))
                self.open_btn.setEnabled(bool(self.selected_project_path))
                return
        self.selected_project_path = None
        self.open_btn.setEnabled(False)

    def _on_item_double_clicked(self, _item: QtWidgets.QTableWidgetItem) -> None:
        self._handle_open()

    def _handle_open(self) -> None:
        if self.selected_project_path and os.path.exists(self.selected_project_path):
            self.accept()

    def _handle_browse_other(self) -> None:
        """Fallback to native directory chooser if user wants to select a project elsewhere."""
        title = self._tr("step1_dialog_open_project_folder_title", "Projektordner auswählen")
        chosen_dir = Dialogs.select_directory(self, title, self.projects_dir or "")
        if chosen_dir and os.path.isdir(chosen_dir):
            self.selected_project_path = chosen_dir
            self.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (qt_enum(Qt, "Key_Return", "Key"), qt_enum(Qt, "Key_Enter", "Key")):
            if self.selected_project_path and self.open_btn.isEnabled():
                self._handle_open()
                return
        super().keyPressEvent(event)
