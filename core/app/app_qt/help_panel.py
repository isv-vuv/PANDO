"""Clean, standard Qt side help panel component for PANDO step dialogs, integrated with localizer."""

from __future__ import annotations

from typing import Optional

from core.app.app_qt.app_state import StepId
from core.app.app_qt.qt_base import (
    Qt,
    QtWidgets,
    pyqtSignal,
    qt_enum,
    require_qgis_qt,
)

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object

STANDARD_QT_HELP_CSS = """
<style>
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        font-size: 12px;
        line-height: 1.55;
        color: #202124;
        margin: 14px 16px;
    }
    p {
        margin-top: 0;
        margin-bottom: 10px;
    }
    b, strong {
        color: #1a1a1a;
    }
    ul, ol {
        margin-top: 4px;
        margin-bottom: 12px;
        padding-left: 20px;
    }
    li {
        margin-bottom: 6px;
    }
    code {
        font-family: Consolas, "Courier New", monospace;
        font-size: 11px;
        background-color: #f1f3f4;
        padding: 2px 4px;
        border-radius: 3px;
    }
    a {
        color: #1a73e8;
        text-decoration: none;
    }
    a:hover {
        text-decoration: underline;
    }
</style>
"""


class HelpPanelWidget(_Q_WIDGET_BASE):
    """Clean, standard Qt side help panel displaying contextual step guidance from localizer."""

    close_requested = pyqtSignal() if pyqtSignal is not None else None

    def __init__(self, parent: Optional[object] = None):
        require_qgis_qt()
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setMaximumWidth(600)
        self._current_step_id: Optional[StepId] = None
        self._localizer = None
        self._build_ui()

    def _build_ui(self) -> None:
        outer_layout = QtWidgets.QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Straight 1px vertical separator line
        self._line = QtWidgets.QFrame(self)
        self._line.setFrameShape(qt_enum(QtWidgets.QFrame, "VLine", "Shape"))
        self._line.setFrameShadow(qt_enum(QtWidgets.QFrame, "Plain", "Shadow"))
        self._line.setLineWidth(1)
        self._line.setMidLineWidth(0)
        self._line.setStyleSheet(
            "QFrame { background-color: #d0d7de; border: none; max-width: 1px; min-width: 1px; }"
        )
        outer_layout.addWidget(self._line)

        # Container for header and content
        container = QtWidgets.QWidget(self)
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Header bar
        header_bar = QtWidgets.QWidget(container)
        header_bar.setStyleSheet(
            "QWidget { background-color: #f6f8fa; border-bottom: 1px solid #d0d7de; }"
        )
        header_layout = QtWidgets.QHBoxLayout(header_bar)
        header_layout.setContentsMargins(14, 8, 10, 8)
        header_layout.setSpacing(8)

        self.lbl_title = QtWidgets.QLabel("Hilfe", header_bar)
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #24292f; border: none; background: transparent;")
        header_layout.addWidget(self.lbl_title)
        header_layout.addStretch(1)

        self.btn_close = QtWidgets.QToolButton(header_bar)
        self.btn_close.setText("✕")
        self.btn_close.setToolTip("Hilfe schließen")
        self.btn_close.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        self.btn_close.setStyleSheet(
            "QToolButton { border: none; background: transparent; font-size: 13px; font-weight: bold; color: #57606a; padding: 2px 6px; border-radius: 4px; } "
            "QToolButton:hover { background-color: #e1e4e8; color: #24292f; }"
        )
        self.btn_close.clicked.connect(self._on_close_clicked)
        header_layout.addWidget(self.btn_close)

        container_layout.addWidget(header_bar)

        # Standard Qt Text Browser for HTML help content
        self.browser = QtWidgets.QTextBrowser(container)
        self.browser.setOpenExternalLinks(True)
        self.browser.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))
        self.browser.setVerticalScrollBarPolicy(qt_enum(Qt, "ScrollBarAsNeeded", "ScrollBarPolicy"))
        self.browser.setHorizontalScrollBarPolicy(qt_enum(Qt, "ScrollBarAlwaysOff", "ScrollBarPolicy"))
        container_layout.addWidget(self.browser, 1)

        outer_layout.addWidget(container, 1)

    def _on_close_clicked(self) -> None:
        if self.close_requested is not None:
            self.close_requested.emit()

    def update_help(self, step_id: StepId | int, localizer=None, project_path: Optional[str] = None) -> None:
        self._current_step_id = step_id
        self._localizer = localizer

        if localizer:
            title_text = localizer.get_string("help_title", default="Hilfe")
            self.lbl_title.setText(title_text)
            self.btn_close.setToolTip(localizer.get_string("button_close", default="Schließen"))

        help_key_by_step = {
            StepId.WELCOME: "help_step0_body",
            StepId.SEARCH: "help_step1_body",
            StepId.CITY_SELECTION: "help_step1_body",
            StepId.PROJECT_PBF: "help_step2_body",
            StepId.GRID_AREA: "help_step3_body",
            StepId.PROCESSING: "help_step4_body",
            StepId.VISUM: "help_step5_body",
            StepId.RESULTS: "help_step6_body",
        }
        key = help_key_by_step.get(step_id, f"help_step{int(step_id)}_body")

        if project_path:
            import os
            visum_folder = os.path.normpath(os.path.join(project_path, "processed", "visum"))
        else:
            visum_folder = "/projects/XXX/processed/visum"

        if localizer:
            html_body = localizer.get_string(key, visum_path=visum_folder, project_path=project_path or "")
        else:
            html_body = "<p>Keine Hilfe verfügbar.</p>"

        full_html = f"<!DOCTYPE html><html><head>{STANDARD_QT_HELP_CSS}</head><body>{html_body}</body></html>"
        self.browser.setHtml(full_html)
