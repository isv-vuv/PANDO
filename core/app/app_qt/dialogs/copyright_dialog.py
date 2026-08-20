"""Dialog for displaying and accepting copyright and license terms for all data and tool sources."""

from __future__ import annotations

import html
import os
import re
from datetime import datetime
from typing import Optional, Sequence

from core.locales import localizer
from core.app.app_core.project import tool_root
from core.app.app_qt.qt_base import (
    Qt,
    QtCore,
    QtWidgets,
    app_font,
    qfont_bold,
    qt_enum,
)


COPYRIGHT_SOURCES = [
    {
        "id": "gadm",
        "title": "1. GADM",
        "sub_title": "Global Administrative Areas",
        "rel_path": os.path.join("core", "data", "gadm", "_source.txt"),
    },
    {
        "id": "ghs_pop",
        "title": "2. GHS-POP",
        "sub_title": "Global Human Settlement Layer - Population Grid",
        "rel_path": os.path.join("core", "data", "ghs_pop", "_source.txt"),
    },
    {
        "id": "osm",
        "title": "3. OSM",
        "sub_title": "OSM Data, Geofabrik Extracts & Nominatim Geocoding API",
        "rel_path": os.path.join("core", "data", "osm", "_source.txt"),
    },
    {
        "id": "osmium",
        "title": "4. Osmium",
        "sub_title": "OSM Command Line Processing Utility",
        "rel_path": os.path.join("core", "scripts", "osmium", "_source.txt"),
    },
    {
        "id": "qgis",
        "title": "5. QGIS",
        "sub_title": "Model 5 / CentralityMapper",
        "rel_path": os.path.join("core", "scripts", "qgis", "_source.txt"),
    },
    {
        "id": "visum",
        "title": "6. Visum",
        "sub_title": "Access Node Deriver, RIN-Tool, Luftlinientool & Visum Importer",
        "rel_path": os.path.join("core", "scripts", "visum", "_source.txt"),
    },
]

REQUIRED_SOURCE_IDS = [src["id"] for src in COPYRIGHT_SOURCES]

_URL_PATTERN = re.compile(r"(https?://[^\s<]+)")
_Q_DIALOG_BASE = QtWidgets.QDialog if QtWidgets is not None else object


def format_source_text_to_html(raw_text: str) -> str:
    escaped = html.escape(raw_text)
    linked = _URL_PATTERN.sub(r'<a href="\1">\1</a>', escaped)
    formatted_paragraphs = linked.replace("\n", "<br>")
    return f"<html><body>{formatted_paragraphs}</body></html>"


def load_source_content(rel_path: str, base_dir: Optional[str] = None) -> str:
    """Reads license text file from project root or custom base_dir."""
    root = base_dir or tool_root()
    full_path = os.path.normpath(os.path.join(root, rel_path))
    if not os.path.isfile(full_path):
        return localizer.get_string("dialog_copyright_warn_not_found", path=rel_path)
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as exc:
        return localizer.get_string("dialog_copyright_warn_load_error", path=rel_path, error=exc)


class CopyrightDialog(_Q_DIALOG_BASE):
    """Modal dialog displaying all 6 source texts with individual & master acceptance checkboxes."""

    def __init__(
        self,
        parent: Optional[object] = None,
        already_accepted: Optional[dict[str, str]] = None,
        base_dir: Optional[str] = None,
    ):
        if QtWidgets is None:
            return
        super().__init__(parent)
        self.setWindowTitle(localizer.get_string("dialog_copyright_window_title"))
        self.setMinimumSize(780, 560)
        self.resize(840, 600)
        self.already_accepted = dict(already_accepted or {})
        self.base_dir = base_dir
        self._checkboxes: dict[str, QtWidgets.QCheckBox] = {}
        self._updating_master = False

        self._build_ui()
        self._sync_checkbox_states()

    def _build_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # Header section
        header_title = QtWidgets.QLabel(localizer.get_string("dialog_copyright_header"), self)
        header_title.setFont(app_font(14))
        main_layout.addWidget(header_title)

        header_info = QtWidgets.QLabel(localizer.get_string("dialog_copyright_header_info"), self)
        header_info.setFont(app_font(10))
        header_info.setWordWrap(True)
        main_layout.addWidget(header_info)

        # Master Checkbox
        self.master_checkbox = QtWidgets.QCheckBox(localizer.get_string("dialog_copyright_master_checkbox"), self)
        self.master_checkbox.setFont(app_font(10))
        self.master_checkbox.stateChanged.connect(self._on_master_checkbox_changed)
        main_layout.addWidget(self.master_checkbox)

        # Tab Widget for the 6 sources in exact priority order
        self.tab_widget = QtWidgets.QTabWidget(self)
        if hasattr(self.tab_widget, "tabBar"):
            bar = self.tab_widget.tabBar()
            if hasattr(bar, "setExpanding"):
                bar.setExpanding(True)

        for src in COPYRIGHT_SOURCES:
            page = self._create_source_page(src)
            tab_title = localizer.get_string(f"dialog_copyright_src_{src['id']}_title", default=src["title"])
            self.tab_widget.addTab(page, tab_title)

        main_layout.addWidget(self.tab_widget, 1)

        # Footer Action Row
        footer_layout = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("", self)
        self.status_label.setFont(app_font(10))
        footer_layout.addWidget(self.status_label)

        footer_layout.addStretch(1)

        self.cancel_button = QtWidgets.QPushButton(localizer.get_string("button_cancel"), self)
        self.cancel_button.setFont(app_font(10))
        self.cancel_button.clicked.connect(self.reject)
        footer_layout.addWidget(self.cancel_button)

        self.accept_all_button = QtWidgets.QPushButton(localizer.get_string("dialog_copyright_button_accept_all"), self)
        self.accept_all_button.setFont(app_font(10))
        self.accept_all_button.clicked.connect(self._handle_accept_all_click)
        footer_layout.addWidget(self.accept_all_button)

        main_layout.addLayout(footer_layout)

    def _create_source_page(self, src: dict) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self.tab_widget)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Title & Subtitle
        sub_title = localizer.get_string(f"dialog_copyright_src_{src['id']}_sub", default=src["sub_title"])
        title_label = QtWidgets.QLabel(sub_title, page)
        title_label.setFont(app_font(10))
        layout.addWidget(title_label)

        # Text Viewer
        text_browser = QtWidgets.QTextBrowser(page)
        text_browser.setOpenExternalLinks(True)
        raw_text = load_source_content(src["rel_path"], self.base_dir)
        text_browser.setHtml(format_source_text_to_html(raw_text))
        layout.addWidget(text_browser, 1)

        # Checkbox for this source
        src_title = localizer.get_string(f"dialog_copyright_src_{src['id']}_title", default=src["title"])
        cb_label = localizer.get_string("dialog_copyright_individual_checkbox", title=src_title)
        checkbox = QtWidgets.QCheckBox(cb_label, page)
        checkbox.setProperty("source_id", src["id"])
        checkbox.stateChanged.connect(self._on_individual_checkbox_changed)
        self._checkboxes[src["id"]] = checkbox
        layout.addWidget(checkbox)

        return page

    def _handle_accept_all_click(self) -> None:
        """Immediately check all source checkboxes and accept dialog."""
        self._updating_master = True
        for cb in self._checkboxes.values():
            cb.setChecked(True)
        self._updating_master = False
        self._update_master_and_accept_button_state()
        self.accept()

    def _sync_checkbox_states(self) -> None:
        """Pre-checks boxes if already accepted in user settings."""
        for src_id, cb in self._checkboxes.items():
            if src_id in self.already_accepted and bool(self.already_accepted[src_id]):
                cb.setChecked(True)
            else:
                cb.setChecked(False)
        self._update_master_and_accept_button_state()

    def _on_master_checkbox_changed(self, state: int) -> None:
        if self._updating_master:
            return
        is_checked = self.master_checkbox.isChecked()
        self._updating_master = True
        for cb in self._checkboxes.values():
            cb.setChecked(is_checked)
        self._updating_master = False
        self._update_master_and_accept_button_state()

    def _on_individual_checkbox_changed(self, _state: int) -> None:
        if self._updating_master:
            return
        self._update_master_and_accept_button_state()

    def _update_master_and_accept_button_state(self) -> None:
        accepted_count = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        total_count = len(self._checkboxes)
        all_accepted = accepted_count == total_count

        self._updating_master = True
        self.master_checkbox.setChecked(all_accepted)
        self._updating_master = False

        if all_accepted:
            self.status_label.setText(localizer.get_string("dialog_copyright_status_accepted_all", total=total_count))
        else:
            self.status_label.setText(
                localizer.get_string("dialog_copyright_status_accepted_partial", count=accepted_count, total=total_count)
            )

    def get_accepted_result(self) -> dict[str, str]:
        """Returns a dict mapping source_id -> timestamp ISO string for all accepted sources."""
        now_str = datetime.now().isoformat()
        result = dict(self.already_accepted)
        for src_id, cb in self._checkboxes.items():
            if cb.isChecked():
                if src_id not in result or not result[src_id]:
                    result[src_id] = now_str
        return result
