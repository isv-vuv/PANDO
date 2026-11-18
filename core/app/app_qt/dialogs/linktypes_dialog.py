"""Dialog for viewing and customizing PANDO Visum link types."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from core.locales import localizer
from core.app.app_core.linktypes_config import (
    generate_linktypes_rows,
    is_default_linktypes_config,
)
from core.app.app_qt.qt_base import (
    Dialogs,
    QtWidgets,
    app_font,
    qfont_bold,
)
from core.app.app_qt.steps.step5_linktypes import LinkTypesEditorWidget

_Q_DIALOG_BASE = QtWidgets.QDialog if QtWidgets is not None else object

_DIALOG_STYLE = """
    QDialog {
        background-color: #ffffff;
    }
"""


class LinkTypesConfigDialog(_Q_DIALOG_BASE):
    """Modal configuration dialog wrapping the 5-sub-page link-type editor."""

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        parent: Optional[object] = None,
        localizer_obj: Optional[object] = None,
        project_path: Optional[str] = None,
    ):
        if QtWidgets is None:
            return
        super().__init__(parent)
        self.loc = localizer_obj or localizer

        self.setWindowTitle(
            self.loc.get_string(
                "linktypes_dialog_title",
                default="Streckentypen & Fährparameter konfigurieren",
            )
        )
        self.setMinimumSize(960, 680)
        self.resize(1120, 740)
        self.setStyleSheet(_DIALOG_STYLE)

        self._build_ui(config, project_path)

    def get_config(self) -> dict[str, Any]:
        """Return normalized copy of the currently edited configuration."""
        return self.editor.get_config()

    def _build_ui(self, config: Optional[Mapping[str, Any]], project_path: Optional[str]) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(18, 14, 18, 14)
        main_layout.setSpacing(10)

        title_lbl = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_dialog_header",
                default="Visum-Streckentypen, Kapazitäten & Fährparameter konfigurieren",
            ),
            self,
        )
        title_lbl.setFont(app_font(12, qfont_bold()))
        title_lbl.setStyleSheet("color: #0f172a;")
        main_layout.addWidget(title_lbl)

        desc_lbl = QtWidgets.QLabel(
            self.loc.get_string(
                "linktypes_dialog_desc",
                default="Konfiguration der PANDO-Streckentypen-Methodik in fünf strukturierten Unterseiten. Änderungen steuern das OSM-Bracketing sowie die Visum-Neustrukturierung.",
            ),
            self,
        )
        desc_lbl.setFont(app_font(9))
        desc_lbl.setStyleSheet("color: #475569;")
        desc_lbl.setWordWrap(True)
        main_layout.addWidget(desc_lbl)

        self.editor = LinkTypesEditorWidget(
            config=config,
            project_path=project_path,
            localizer_obj=self.loc,
            parent=self,
            show_footer=False,
        )
        self.editor.config_changed.connect(self._sync_footer)
        main_layout.addWidget(self.editor, 1)

        # Delegate public attributes used by callers & tests
        self.tab_widget = self.editor.tab_widget
        self._speed_checks = self.editor._speed_checks
        self._lane_checks = self.editor._lane_checks
        self.cap_table = self.editor.cap_table
        self.imp_table = self.editor.imp_table
        self.special_table = self.editor.special_table
        self.preview_table = self.editor.preview_table
        self.modes_table = self.editor.modes_table
        self.category_list = self.editor.category_list
        self.ferry_wait_spin = self.editor.ferry_wait_spin

        # Footer
        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(10)

        self.summary_label = QtWidgets.QLabel("", self)
        self.summary_label.setFont(app_font(9))
        self.summary_label.setStyleSheet("color: #475569;")
        footer.addWidget(self.summary_label, 1)

        self.reset_button = QtWidgets.QPushButton(
            self.loc.get_string("linktypes_btn_reset", default="Auf Standard zurücksetzen"),
            self,
        )
        self.reset_button.setFont(app_font(9))
        self.reset_button.setStyleSheet(
            "QPushButton:enabled { background-color: #ffffff; color: #334155; border: 1px solid #cbd5e1; border-radius: 4px; padding: 6px 14px; } "
            "QPushButton:enabled:hover { background-color: #f1f5f9; border-color: #94a3b8; } "
            "QPushButton:disabled { background-color: #f8fafc; color: #94a3b8; border: 1px solid #e2e8f0; border-radius: 4px; padding: 6px 14px; }"
        )
        self.reset_button.clicked.connect(self._on_reset_defaults)
        footer.addWidget(self.reset_button)

        self.cancel_button = QtWidgets.QPushButton(
            self.loc.get_string("button_cancel", default="Abbrechen"),
            self,
        )
        self.cancel_button.setFont(app_font(9))
        self.cancel_button.setStyleSheet(
            "QPushButton { background-color: #ffffff; color: #334155; border: 1px solid #cbd5e1; border-radius: 4px; padding: 6px 16px; } "
            "QPushButton:hover { background-color: #f1f5f9; border-color: #94a3b8; }"
        )
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)

        self.save_button = QtWidgets.QPushButton(
            self.loc.get_string("linktypes_btn_save", default="Speichern & Übernehmen"),
            self,
        )
        self.save_button.setFont(app_font(9, qfont_bold()))
        self.save_button.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; font-weight: bold; border-radius: 4px; padding: 6px 18px; border: 1px solid #1d4ed8; } "
            "QPushButton:hover { background-color: #1d4ed8; } "
            "QPushButton:pressed { background-color: #1e40af; }"
        )
        self.save_button.clicked.connect(self._on_accept)
        footer.addWidget(self.save_button)
        main_layout.addLayout(footer)

        self._sync_footer()

    def _sync_footer(self) -> None:
        self.summary_label.setText(self.editor.summary_label.text())
        self.reset_button.setEnabled(not is_default_linktypes_config(self.editor._config))

    def _on_reset_defaults(self) -> None:
        self.editor.reset_defaults()
        self._sync_footer()

    def _on_accept(self) -> None:
        rows = generate_linktypes_rows(self.get_config())
        if not rows:
            Dialogs.warning(
                self,
                self.loc.get_string("linktypes_err_empty_title", default="Keine Streckentypen"),
                self.loc.get_string("linktypes_err_empty_body", default="Bitte mindestens eine Streckentyp-Klasse aktivieren."),
            )
            return
        self.accept()
