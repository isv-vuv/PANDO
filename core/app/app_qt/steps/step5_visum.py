"""Qt page for Step 6: PTV Visum Import & Post-Processing."""

from __future__ import annotations

import os
import sys
import logging
from typing import Optional
from pathlib import Path

from core.locales import localizer
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
)

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
_Q_OBJECT_BASE = QtCore.QObject if QtCore is not None else object


class VisumWorker(_Q_OBJECT_BASE):
    """Background worker for Visum COM automation pipeline."""

    log_ready = pyqtSignal(str) if pyqtSignal is not None else None
    finished = pyqtSignal(bool, str) if pyqtSignal is not None else None

    def __init__(self, project_path: str, start_step: int = 1):
        super().__init__()
        self.project_path = project_path
        self.start_step = start_step

    def run(self) -> None:
        import importlib
        import importlib.util

        try:
            mod = importlib.import_module("core.scripts.visum.scripts.00_run_all_visum_scripts")
            run_visum_postprocessing_chain = mod.run_visum_postprocessing_chain
        except Exception:
            script_file = Path(__file__).resolve().parents[2] / "scripts" / "visum" / "scripts" / "00_run_all_visum_scripts.py"
            spec = importlib.util.spec_from_file_location("run_all_visum", script_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load 00_run_all_visum_scripts.py from {script_file}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            run_visum_postprocessing_chain = mod.run_visum_postprocessing_chain

        def log_adapter(msg: str) -> None:
            if self.log_ready:
                from core.app.app_core.logging import format_pando_log, should_ignore_log_message
                if not should_ignore_log_message(msg):
                    self.log_ready.emit(format_pando_log(msg))

        from core.app.app_core.logging import setup_pando_logger
        setup_pando_logger(name="", gui_callback=log_adapter, project_path=self.project_path)

        try:
            log_adapter(localizer.get_string("step5_log_start_visum", step=self.start_step, default=f"Starte Visum 2025 Import und Nachverarbeitung (ab Schritt {self.start_step})"))
            success = run_visum_postprocessing_chain(self.project_path, start_step=self.start_step, log=log_adapter)
            self._active_visum = getattr(mod, "_ACTIVE_VISUM_INSTANCE", None)
            if self.finished:
                if success:
                    self.finished.emit(True, localizer.get_string("step5_status_visum_success", default="Visum-Verarbeitung erfolgreich abgeschlossen."))
                else:
                    self.finished.emit(False, localizer.get_string("step5_status_visum_failed", default="Visum-Verarbeitung abgebrochen oder fehlerhaft."))
        except Exception as exc:
            self._active_visum = getattr(mod, "_ACTIVE_VISUM_INSTANCE", None)
            if self.log_ready:
                from core.app.app_core.logging import format_pando_log
                self.log_ready.emit(format_pando_log(localizer.get_string("step5_log_severe_error", error=str(exc), default=f"Schwerwiegender Fehler: {exc}"), level="ERROR"))
            if self.finished:
                self.finished.emit(False, str(exc))


class Step5VisumWidget(_Q_WIDGET_BASE):
    """Step 5 Widget for executing and watching Visum post-processing."""

    run_requested = pyqtSignal() if pyqtSignal is not None else None
    finished = pyqtSignal() if pyqtSignal is not None else None

    def __init__(self, localizer_obj, parent: Optional[object] = None, project_path: str = ""):
        super().__init__(parent)
        self.localizer = localizer_obj
        self.project_path = project_path
        self._thread = None
        self._worker = None
        self._visum_seconds = 0
        self._visum_timer = None
        if QtCore is not None:
            self._visum_timer = QtCore.QTimer(self)
            self._visum_timer.setInterval(1000)
            self._visum_timer.timeout.connect(self._update_visum_status_timer)

        self._setup_ui()

    def _setup_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 10, 16, 10)
        main_layout.setSpacing(8)

        self.header, _title_label, _step_label = create_step_header(
            self.localizer.get_string("step5_title", default="Schritt 5: Visum-Import und Verarbeitung"),
            current_step=5,
            total_steps=6,
            localizer=self.localizer,
            parent=self,
        )
        main_layout.addWidget(self.header)

        # Header card
        self.header_group = QtWidgets.QGroupBox(self.localizer.get_string("step5_group_visum_automation"), self)
        header_layout = QtWidgets.QVBoxLayout(self.header_group)

        self.desc_label = QtWidgets.QLabel(
            self.localizer.get_string("step5_desc", default="Automatisiertes Einlesen des Straßennetzes und der Verkehrszellen in PTV Visum sowie anschließende Anbindungserzeugung, Netzkategorisierung und Erreichbarkeitsanalysen."),
            self.header_group
        )
        self.desc_label.setWordWrap(True)
        header_layout.addWidget(self.desc_label)
        main_layout.addWidget(self.header_group)

        # Step selection row
        step_row = QtWidgets.QHBoxLayout()
        self.step_label = QtWidgets.QLabel(self.localizer.get_string("step5_label_start_from_step"), self)
        self.step_label.setFont(app_font(10, qfont_bold()))
        self.step_combo = QtWidgets.QComboBox(self)
        self._populate_combo_items()

        step_row.addWidget(self.step_label)
        step_row.addWidget(self.step_combo, stretch=1)
        main_layout.addLayout(step_row)

        # Status & Control Row
        control_row = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel(self.localizer.get_string("step5_status_ready"), self)
        self.status_label.setFont(app_font(10, qfont_bold()))

        self.start_button = QtWidgets.QPushButton(self.localizer.get_string("step5_button_start_visum"), self)
        self.start_button.clicked.connect(self.start_visum_pipeline)

        control_row.addWidget(self.status_label)
        control_row.addStretch()
        control_row.addWidget(self.start_button)
        main_layout.addLayout(control_row)

        # Log Display Window
        self.log_group = QtWidgets.QGroupBox(self.localizer.get_string("step5_group_log"), self)
        log_layout = QtWidgets.QVBoxLayout(self.log_group)

        self.log_edit = QtWidgets.QTextEdit(self.log_group)
        self.log_edit.setReadOnly(True)
        log_font = app_font(9)
        if hasattr(log_font, "setFamily"):
            log_font.setFamily("Consolas")
        self.log_edit.setFont(log_font)
        log_layout.addWidget(self.log_edit)

        main_layout.addWidget(self.log_group, stretch=1)

    def _populate_combo_items(self) -> None:
        curr_idx = self.step_combo.currentIndex() if self.step_combo.count() > 0 else 0
        self.step_combo.clear()
        self.step_combo.addItems([
            self.localizer.get_string("step5_combo_step1", default="Schritt 1: Komplett (Strecken & Bezirke importieren)"),
            self.localizer.get_string("step5_combo_step2", default="Schritt 2: Zugangsknoten ableiten (ab 02_Zones_Imported.ver)"),
            self.localizer.get_string("step5_combo_step3", default="Schritt 3: Streckentypen neu strukturieren (ab 03_AccessNodes_Derived.ver)"),
            self.localizer.get_string("step5_combo_step4", default="Schritt 4: Anbindungen erzeugen (ab 04_LinkTypes_Restructured.ver)"),
            self.localizer.get_string("step5_combo_step5", default="Schritt 5: Luftlinien-Matrizen erzeugen (ab 05_Connectors_Generated.ver)"),
            self.localizer.get_string("step5_combo_step6", default="Schritt 6: Verfahrensablauf ausführen (ab 06_DirectLineMatrices_Added.ver)"),
            self.localizer.get_string("step5_combo_step7", default="Schritt 7: Grafikparameter anwenden (ab 07_XXX_Model.ver)"),
        ])
        if 0 <= curr_idx < self.step_combo.count():
            self.step_combo.setCurrentIndex(curr_idx)

    def retranslate_ui(self) -> None:
        self.header_group.setTitle(self.localizer.get_string("step5_group_visum_automation"))
        self.desc_label.setText(self.localizer.get_string("step5_desc"))
        self.step_label.setText(self.localizer.get_string("step5_label_start_from_step"))
        self._populate_combo_items()
        self.start_button.setText(self.localizer.get_string("step5_button_start_visum"))
        self.log_group.setTitle(self.localizer.get_string("step5_group_log"))
        if self._thread is None:
            self.status_label.setText(self.localizer.get_string("step5_status_ready"))

    def set_project_path(self, path: str) -> None:
        self.project_path = path

    def start_visum_pipeline(self) -> None:
        if not self.project_path:
            Dialogs.warning(
                self,
                self.localizer.get_string("step5_err_no_project_title", default="Fehler"),
                self.localizer.get_string("step5_err_no_project_msg", default="Kein gültiger Projektpfad angegeben.")
            )
            return

        start_step = self.step_combo.currentIndex() + 1

        self.start_button.setEnabled(False)
        self._visum_seconds = 0
        if self._visum_timer is not None:
            self._visum_timer.start()
        base_status = self.localizer.get_string("step5_status_running", default="Status: Visum-Verarbeitung läuft...")
        self.status_label.setText(f"{base_status} (0s)")
        self.log_edit.clear()
        self.append_log(self.localizer.get_string("step5_log_starting", project_path=self.project_path, start_step=start_step))

        self._thread = QtCore.QThread()
        self._worker = VisumWorker(self.project_path, start_step=start_step)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log_ready.connect(self.append_log)
        self._worker.finished.connect(self._on_finished)

        self._thread.start()

    def _update_visum_status_timer(self) -> None:
        self._visum_seconds += 1
        mins, secs = divmod(self._visum_seconds, 60)
        base = self.localizer.get_string("step5_status_running", default="Status: Visum-Verarbeitung läuft...")
        time_text = f"{self._visum_seconds}s" if mins == 0 else f"{mins}m {secs:02d}s"
        self.status_label.setText(f"{base} ({time_text})")

    def append_log(self, text: str) -> None:
        self.log_edit.append(text)
        sb = self.log_edit.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_finished(self, success: bool, message: str) -> None:
        if self._visum_timer is not None and self._visum_timer.isActive():
            self._visum_timer.stop()

        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None

        self.start_button.setEnabled(True)
        if success:
            title = self.localizer.get_string("step5_title_success", default="Erfolg")
            msg = self.localizer.get_string("step5_msg_success", default=message)
            self.status_label.setText(self.localizer.get_string("step5_status_completed", default="Status: Erfolgreich abgeschlossen"))
            Dialogs.info(self, title, msg)
        else:
            title = self.localizer.get_string("step5_title_error", default="Fehler")
            self.status_label.setText(self.localizer.get_string("step5_status_error", default="Status: Abgebrochen / Fehler"))
            Dialogs.error(self, title, message)
