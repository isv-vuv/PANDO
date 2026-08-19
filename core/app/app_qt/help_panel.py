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
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Straight 1px vertical separator line
        self._line = QtWidgets.QFrame(self)
        self._line.setFrameShape(qt_enum(QtWidgets.QFrame, "VLine", "Shape"))
        self._line.setFrameShadow(qt_enum(QtWidgets.QFrame, "Plain", "Shadow"))
        self._line.setLineWidth(1)
        self._line.setMidLineWidth(0)
        self._line.setStyleSheet(
            "QFrame { background-color: #d0d7de; border: none; max-width: 1px; min-width: 1px; }"
        )
        layout.addWidget(self._line)

        # Standard Qt Text Browser for HTML help content
        self.browser = QtWidgets.QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))
        self.browser.setVerticalScrollBarPolicy(qt_enum(Qt, "ScrollBarAsNeeded", "ScrollBarPolicy"))
        self.browser.setHorizontalScrollBarPolicy(qt_enum(Qt, "ScrollBarAlwaysOff", "ScrollBarPolicy"))
        layout.addWidget(self.browser, 1)

    def update_help(self, step_id: StepId | int, localizer=None, project_path: Optional[str] = None) -> None:
        self._current_step_id = step_id

        step_num = int(step_id)
        key = f"help_step{step_num}_body"

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
