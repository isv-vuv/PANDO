"""Final Qt page for parameter approval and the complete processing pipeline."""

from __future__ import annotations

import json
import os
from threading import Event
from typing import Optional

from core.locales import localizer
from core.app.app_core.geo import get_utm_epsg
from core.app.app_core.model_pipeline import load_parameter_defaults
from core.app.app_core.pipeline import PipelineCallbacks, UrbanActPipeline, pipeline_readiness
from core.app.app_qt.qt_base import (
    AnimatedProgressBar,
    Dialogs,
    QCursor,
    QtGui,
    Qt,
    QtCore,
    QtWidgets,
    app_font,
    create_step_header,
    escape_mnemonic,
    pyqtSignal,
    qfont_bold,
    qt_enum,
    require_qgis_qt,
)


_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
_Q_OBJECT_BASE = QtCore.QObject if QtCore is not None else object


class PipelineWorker(_Q_OBJECT_BASE):
    """Own the stop event used by QGIS feedback and every Osmium subprocess."""

    phase_started = pyqtSignal(str, int, int) if pyqtSignal is not None else None
    phase_progress = pyqtSignal(int) if pyqtSignal is not None else None
    phase_detail = pyqtSignal(str) if pyqtSignal is not None else None
    log_ready = pyqtSignal(str) if pyqtSignal is not None else None
    output_ready = pyqtSignal(str, str) if pyqtSignal is not None else None
    finished = pyqtSignal(object) if pyqtSignal is not None else None
    error_ready = pyqtSignal(str, str) if pyqtSignal is not None else None

    def __init__(self, pipeline: UrbanActPipeline, options: dict, *, phase_a_only: bool = False):
        super().__init__()
        self.pipeline = pipeline
        self.options = dict(options)
        self.phase_a_only = phase_a_only
        self.stop_event = Event()
        self._phase = "Vorbereitung"

    def stop(self) -> None:
        self.stop_event.set()
        self.log_ready.emit(localizer.get_string("step4_status_cancel_requested", default="Abbruch angefordert …"))

    def _emit_log(self, msg: str) -> None:
        if self.log_ready:
            from core.app.app_core.logging import format_pando_log
            self.log_ready.emit(format_pando_log(msg))

    def run(self) -> None:
        callbacks = PipelineCallbacks(
            phase_started=self._phase_started,
            phase_progress=self.phase_progress.emit,
            phase_detail=self.phase_detail.emit,
            log=self._emit_log,
            output=self.output_ready.emit,
        )
        try:
            if self.phase_a_only:
                result = self.pipeline.run_phase_a_only(
                    stop_event=self.stop_event, callbacks=callbacks, **self.options
                )
                self.finished.emit(result)
            else:
                result = self.pipeline.run(
                    stop_event=self.stop_event,
                    callbacks=callbacks,
                    **self.options,
                )
                self.finished.emit(result.context)
        except Exception as exc:
            self.error_ready.emit(self._phase, str(exc))

    def _phase_started(self, name: str, index: int, total: int) -> None:
        self._phase = name
        self.phase_started.emit(name, index, total)


class Step4ProcessingWidget(_Q_WIDGET_BASE):
    """Review external data and Model-2 values, then run the fixed chain."""

    status_changed = pyqtSignal(str) if pyqtSignal is not None else None
    next_requested = pyqtSignal() if pyqtSignal is not None else None

    _MODEL2_FIELDS = (
        ("minimum_population_level_0", "min_pop_0", "Mindestbevölkerung Level 0", 1, 100_000_000),
        ("minimum_population_level_1", "min_pop_1", "Mindestbevölkerung Level 1", 1, 100_000_000),
        ("minimum_population_level_2", "min_pop_2", "Mindestbevölkerung Level 2", 1, 100_000_000),
        ("population_tolerance", "pop_tol", "Bevölkerungstoleranz [%]", 0, 100),
        ("distance_tolerance", "dist_tol", "Distanztoleranz [%]", 0, 100),
        ("dual_centres_search_radius_km", "dual_radius", "Suchradius Doppelzentren [km]", 1, 10_000),
        ("dual_centres_population_tolerance", "dual_pop_tol", "Bevölkerungstoleranz Doppelzentren [%]", 0, 100),
    )

    _MODEL5_FIELDS = (
        ("minimum_distance_level_3_4_m", "dist_3_4", "Mindestabstand Level 3 zu Level 4 (m)", 1, 100_000),
        ("minimum_distance_level_3_m", "dist_3", "Mindestabstand Level 3 (m)", 1, 100_000),
        ("minimum_distance_level_4_m", "dist_4", "Mindestabstand Level 4 (m)", 1, 100_000),
        ("minimum_intensity_level_3", "min_intensity_3", "Mindest-Intensität Level 3", 0, 25),
        ("minimum_intensity_level_4", "min_intensity_4", "Mindest-Intensität Level 4", 0, 25),
    )

    def _create_parameter_label(self, model_prefix: str, short_key: str, default_label: str, edit_widget: Optional[QtWidgets.QWidget], parent: QtWidgets.QWidget) -> QtWidgets.QLabel:
        lbl_key = f"{model_prefix}_{short_key}" if "_" in model_prefix else f"{model_prefix}_param_{short_key}"
        label_str = self.localizer.get_string(lbl_key, default=default_label)
        tooltip_str = self.localizer.get_string(f"{lbl_key}_tooltip", default="")

        lbl = QtWidgets.QLabel(label_str, parent)
        lbl.setFont(app_font(10))
        lbl.setWordWrap(True)
        if tooltip_str:
            lbl.setToolTip(tooltip_str)
            if edit_widget is not None:
                edit_widget.setToolTip(tooltip_str)
        return lbl

    def __init__(self, localizer, step4_data: dict, parent: Optional[object] = None):
        require_qgis_qt()
        super().__init__(parent)
        self.localizer = localizer
        self.main_window = parent
        self.step_data = dict(step4_data)
        self.project_path = str(step4_data.get("project_path") or "")
        self._thread = None
        self._worker = None
        self._elapsed_seconds = 0
        self._current_phase = self.localizer.get_string("step4_phase_preparation", default="Vorbereitung")
        self._outputs: dict[str, str] = {}
        self._phase_a_only = False
        self._is_completed = False
        self._stopped_by_user = False
        self.model2_edits: dict[str, object] = {}
        self.model5_edits: dict[str, object] = {}

        self.reference_edit = None
        self.reference_field_edit = None
        self.census_edit = None
        self.no_reference_check = None
        self.readiness_label = None
        self.start_button = None
        self.restart_button = None
        self.stop_button = None
        self.overall_progress = None
        self.phase_progress = None
        self.phase_label = None
        self.elapsed_label = None
        self.log_box = None
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick_elapsed)
        self._build_ui()
        self._restore_saved_settings()
        self.refresh_readiness()
        QtCore.QTimer.singleShot(0, self.start_preprocessing)

    @property
    def is_completed(self) -> bool:
        return self.is_pipeline_completed()

    def is_pipeline_completed(self) -> bool:
        if getattr(self, "_is_completed", False):
            return True
        try:
            from core.app.app_core.project import load_pipeline_manifest, phase_can_be_reused

            manifest = load_pipeline_manifest(self.project_path)
            if phase_can_be_reused(manifest, "model6"):
                self._is_completed = True
                return True
        except Exception:
            pass
        return False

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 10)
        root.setSpacing(8)
        header, _title_label, _step_label = create_step_header(
            self.localizer.get_string("step4_title", default="Schritt 4: Parameterprüfung und Verarbeitung"),
            current_step=4,
            total_steps=6,
            localizer=self.localizer,
            parent=self,
        )
        root.addWidget(header)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget(scroll)
        layout = QtWidgets.QVBoxLayout(content)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        readiness_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_derived_models")), content)
        readiness_layout = QtWidgets.QVBoxLayout(readiness_box)
        readiness_layout.setSpacing(4)
        location = self.step_data.get("selected_loc")
        crs = get_utm_epsg(getattr(location, "latitude", 0), getattr(location, "longitude", 0))

        info_row = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step4_label_project')}:</b> {self.project_path or '–'} &nbsp;&nbsp;·&nbsp;&nbsp; "
            f"<b>{self.localizer.get_string('step4_label_local_crs')}:</b> {crs or '–'}<br>"
            f"<b>{self.localizer.get_string('step4_label_cell_size')}:</b> {self.step_data.get('cell_size_m', 4500)} m &nbsp;&nbsp;·&nbsp;&nbsp; "
            f"<b>{self.localizer.get_string('step4_label_min_extent')}:</b> {self.step_data.get('radius_km', 30)} km",
            readiness_box,
        )
        info_row.setFont(app_font(9))
        readiness_layout.addWidget(info_row)

        hover_tip_label = QtWidgets.QLabel(
            self.localizer.get_string(
                "step4_intro_hover_tip",
                default="Hinweis: Bewegen Sie den Mauszeiger über die Parameterbezeichnungen oder Eingabefelder, um ausführliche Hilfetexte zu den Parametern zu erhalten."
            ),
            readiness_box,
        )
        hover_tip_label.setFont(app_font(9))
        hover_tip_label.setWordWrap(True)
        hover_tip_label.setStyleSheet(
            "QLabel { background-color: #f7fafc; color: #2d3748; border: 1px solid #e2e8f0; "
            "padding: 8px; border-radius: 4px; font-style: normal; }"
        )
        readiness_layout.addWidget(hover_tip_label)

        self.readiness_label = QtWidgets.QLabel("", readiness_box)
        self.readiness_label.setWordWrap(True)
        readiness_layout.addWidget(self.readiness_label)
        layout.addWidget(readiness_box)

        EXT_EXEC_LABEL_WIDTH = 240

        # External Data Box (Full Width)
        external_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_external_data")), content)
        external = QtWidgets.QGridLayout(external_box)
        external.setSpacing(8)
        external.setColumnMinimumWidth(0, EXT_EXEC_LABEL_WIDTH)
        external.setColumnStretch(0, 0)
        external.setColumnStretch(1, 1)

        # 1. Ask: Use Local Reference Data (Ja / Nein Radio Buttons)
        lbl_ask_ref = QtWidgets.QLabel(self.localizer.get_string("step4_ask_use_reference", default="Lokale Referenzdaten verwenden:"), external_box)
        lbl_ask_ref.setFont(app_font(9, qfont_bold()))
        rb_ref_widget = QtWidgets.QWidget(external_box)
        rb_ref_layout = QtWidgets.QHBoxLayout(rb_ref_widget)
        rb_ref_layout.setContentsMargins(0, 0, 0, 0)
        rb_ref_layout.setSpacing(16)
        self.rb_ref_no = QtWidgets.QRadioButton(self.localizer.get_string("option_no", default="Nein"), rb_ref_widget)
        self.rb_ref_yes = QtWidgets.QRadioButton(self.localizer.get_string("option_yes", default="Ja"), rb_ref_widget)
        self.rb_ref_no.setChecked(True)
        self.bg_ref = QtWidgets.QButtonGroup(external_box)
        self.bg_ref.addButton(self.rb_ref_no)
        self.bg_ref.addButton(self.rb_ref_yes)
        rb_ref_layout.addWidget(self.rb_ref_no)
        rb_ref_layout.addWidget(self.rb_ref_yes)
        rb_ref_layout.addStretch()
        external.addWidget(lbl_ask_ref, 0, 0)
        external.addWidget(rb_ref_widget, 0, 1, 1, 2)

        # 2. Reference Polygon Layer Row (Visible only when Ja)
        self.reference_edit = QtWidgets.QLineEdit(external_box)
        self.ref_browse_btn = self._browse_button(self.reference_edit, "GeoPackage (*.gpkg)")
        self.lbl_ref = self._create_parameter_label("step4_label", "reference_layer", "Bevölkerungs-Polygonlayer", self.reference_edit, external_box)
        external.addWidget(self.lbl_ref, 1, 0)
        external.addWidget(self.reference_edit, 1, 1)
        external.addWidget(self.ref_browse_btn, 1, 2)
        self.lbl_ref.setVisible(False)
        self.reference_edit.setVisible(False)
        self.ref_browse_btn.setVisible(False)

        # 3. Population Field Row (Visible only when Ja)
        self.reference_field_edit = QtWidgets.QLineEdit("POP", external_box)
        self.lbl_pop = self._create_parameter_label("step4_label", "population_field", "Feld mit Bevölkerungszahl", self.reference_field_edit, external_box)
        self.lbl_pop.setContentsMargins(24, 0, 0, 0)
        external.addWidget(self.lbl_pop, 2, 0)
        external.addWidget(self.reference_field_edit, 2, 1, 1, 2)
        self.lbl_pop.setVisible(False)
        self.reference_field_edit.setVisible(False)

        # 4. Ask: Use Alternative Census Raster (Ja / Nein Radio Buttons)
        lbl_ask_census = QtWidgets.QLabel(self.localizer.get_string("step4_ask_use_census", default="Alternatives Zensusraster verwenden:"), external_box)
        lbl_ask_census.setFont(app_font(9, qfont_bold()))
        rb_census_widget = QtWidgets.QWidget(external_box)
        rb_census_layout = QtWidgets.QHBoxLayout(rb_census_widget)
        rb_census_layout.setContentsMargins(0, 0, 0, 0)
        rb_census_layout.setSpacing(16)
        self.rb_census_no = QtWidgets.QRadioButton(self.localizer.get_string("option_no", default="Nein"), rb_census_widget)
        self.rb_census_yes = QtWidgets.QRadioButton(self.localizer.get_string("option_yes", default="Ja"), rb_census_widget)
        self.rb_census_no.setChecked(True)
        self.bg_census = QtWidgets.QButtonGroup(external_box)
        self.bg_census.addButton(self.rb_census_no)
        self.bg_census.addButton(self.rb_census_yes)
        rb_census_layout.addWidget(self.rb_census_no)
        rb_census_layout.addWidget(self.rb_census_yes)
        rb_census_layout.addStretch()
        external.addWidget(lbl_ask_census, 3, 0)
        external.addWidget(rb_census_widget, 3, 1, 1, 2)

        # 5. Census Grid Row (Visible only when Ja)
        self.census_edit = QtWidgets.QLineEdit(external_box)
        self.census_browse_btn = self._browse_button(self.census_edit, "Raster (*.tif *.tiff)")
        self.lbl_census = self._create_parameter_label("step4_label", "census_grid", "Alternatives Zensusraster", self.census_edit, external_box)
        external.addWidget(self.lbl_census, 4, 0)
        external.addWidget(self.census_edit, 4, 1)
        external.addWidget(self.census_browse_btn, 4, 2)
        self.lbl_census.setVisible(False)
        self.census_edit.setVisible(False)
        self.census_browse_btn.setVisible(False)

        # Event connections
        self.rb_ref_yes.toggled.connect(self._reference_mode_changed)
        self.rb_census_yes.toggled.connect(self._census_mode_changed)
        self.reference_edit.textChanged.connect(self._reference_text_changed)
        self.census_edit.textChanged.connect(self.refresh_readiness)

        layout.addWidget(external_box)

        # Side-by-side Container for Model 2 and Model 5 Box (placed on the exact same top horizontal level!)
        models_widget = QtWidgets.QWidget(content)
        models_layout = QtWidgets.QHBoxLayout(models_widget)
        models_layout.setContentsMargins(0, 0, 0, 0)
        models_layout.setSpacing(12)

        # Model 2 Box (Left side of models container - 2/3 label width, 1/3 control width)
        model2_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_model2")), models_widget)
        model2 = QtWidgets.QGridLayout(model2_box)
        model2.setSpacing(8)
        model2.setColumnStretch(0, 2)
        model2.setColumnStretch(1, 1)
        model2_defaults = load_parameter_defaults("model2")
        for row_idx, (key, short_key, label, minimum, maximum) in enumerate(self._MODEL2_FIELDS):
            edit = QtWidgets.QSpinBox(model2_box)
            edit.setRange(minimum, maximum)
            edit.setValue(int(model2_defaults[key]))
            edit.valueChanged.connect(self.refresh_readiness)
            self.model2_edits[key] = edit
            lbl_widget = self._create_parameter_label("model2", short_key, label, edit, model2_box)
            model2.addWidget(lbl_widget, row_idx, 0)
            model2.addWidget(edit, row_idx, 1)

        # Model 5 Box (Right side of models container - 2/3 label width, 1/3 control width)
        model5_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_model5")), models_widget)
        model5 = QtWidgets.QGridLayout(model5_box)
        model5.setSpacing(8)
        model5.setColumnStretch(0, 2)
        model5.setColumnStretch(1, 1)
        model5_defaults = load_parameter_defaults("model5")
        for row_idx, (key, short_key, label, minimum, maximum) in enumerate(self._MODEL5_FIELDS):
            edit = QtWidgets.QSpinBox(model5_box)
            edit.setRange(minimum, maximum)
            edit.setValue(int(model5_defaults[key]))
            edit.valueChanged.connect(self.refresh_readiness)
            self.model5_edits[key] = edit
            lbl_widget = self._create_parameter_label("model5", short_key, label, edit, model5_box)
            model5.addWidget(lbl_widget, row_idx, 0)
            model5.addWidget(edit, row_idx, 1)

        models_layout.addWidget(model2_box, 1)
        models_layout.addWidget(model5_box, 1)
        layout.addWidget(models_widget)

        # Execution Box (Full Width - column 0 label width 240px matching external_box)
        execution_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_execution")), content)
        execution = QtWidgets.QGridLayout(execution_box)
        execution.setSpacing(8)
        execution.setColumnMinimumWidth(0, EXT_EXEC_LABEL_WIDTH)
        execution.setColumnStretch(0, 0)
        execution.setColumnStretch(1, 1)

        info_label = QtWidgets.QLabel(self.localizer.get_string("step4_info_start_models"), execution_box)
        info_label.setWordWrap(True)
        info_label.setFont(app_font(9))
        info_label.setStyleSheet(
            "QLabel { background-color: #f7fafc; color: #2d3748; border: 1px solid #e2e8f0; "
            "padding: 8px; border-radius: 4px; font-style: normal; }"
        )
        execution.addWidget(info_label, 0, 0, 1, 3)

        self.phase_label = QtWidgets.QLabel(self.localizer.get_string("step4_label_ready"), execution_box)
        self.elapsed_label = QtWidgets.QLabel("00:00", execution_box)
        self.overall_progress = AnimatedProgressBar(execution_box)
        self.phase_progress = AnimatedProgressBar(execution_box)
        self.start_button = QtWidgets.QPushButton(self.localizer.get_string("step4_button_start"), execution_box)
        self.restart_button = QtWidgets.QPushButton(self.localizer.get_string("step4_button_restart_models"), execution_box)
        self.restart_button.setToolTip(
            self.localizer.get_string(
                "step4_tooltip_restart",
                default="Verwirft die gespeicherten Modellfortschritte ab Modell 1; die OSM-Vorverarbeitung Phase A wird weiterhin wiederverwendet."
            )
        )
        self.stop_button = QtWidgets.QPushButton(self.localizer.get_string("step4_button_stop"), execution_box)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_pipeline)
        self.restart_button.clicked.connect(self.restart_pipeline)
        self.stop_button.clicked.connect(self.stop_pipeline)

        lbl_step = QtWidgets.QLabel(self.localizer.get_string("step4_label_current_step"), execution_box)
        execution.addWidget(lbl_step, 1, 0)

        step_row_layout = QtWidgets.QHBoxLayout()
        step_row_layout.addWidget(self.phase_label, 1)
        step_row_layout.addWidget(self.elapsed_label)
        execution.addLayout(step_row_layout, 1, 1, 1, 2)

        lbl_phase_prog = QtWidgets.QLabel(self.localizer.get_string("step4_label_step_progress"), execution_box)
        execution.addWidget(lbl_phase_prog, 2, 0)
        execution.addWidget(self.phase_progress, 2, 1, 1, 2)

        lbl_overall_prog = QtWidgets.QLabel(self.localizer.get_string("step4_label_overall_progress"), execution_box)
        execution.addWidget(lbl_overall_prog, 3, 0)
        execution.addWidget(self.overall_progress, 3, 1, 1, 2)

        # Bottom 3-button row starting at column 1 (X = 240px offset, aligned with Stopp button starting line)
        exec_buttons_widget = QtWidgets.QWidget(execution_box)
        exec_buttons_layout = QtWidgets.QHBoxLayout(exec_buttons_widget)
        exec_buttons_layout.setContentsMargins(0, 0, 0, 0)
        exec_buttons_layout.setSpacing(8)
        exec_buttons_layout.addWidget(self.stop_button, 1)
        exec_buttons_layout.addWidget(self.restart_button, 1)
        exec_buttons_layout.addWidget(self.start_button, 1)
        execution.addWidget(exec_buttons_widget, 4, 1, 1, 2)

        layout.addWidget(execution_box)

        log_group = QtWidgets.QGroupBox(self.localizer.get_string("step4_group_log"), content)
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.log_box = QtWidgets.QPlainTextEdit(log_group)
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(2000)
        log_layout.addWidget(self.log_box)
        layout.addWidget(log_group)

        self.next_button = QtWidgets.QPushButton(self.localizer.get_string("button_next"), content)
        self.next_button.setFont(app_font(10, qfont_bold()))
        self.next_button.clicked.connect(self._handle_next_clicked)
        self.next_button.hide()

    def _handle_next_clicked(self) -> None:
        if not self.is_pipeline_completed():
            Dialogs.warning(
                self,
                self.localizer.get_string("step4_warning_models_incomplete_title"),
                self.localizer.get_string("step4_warning_models_incomplete_body"),
            )
            return
        if self.next_requested:
            self.next_requested.emit()

    def refresh_readiness(self) -> None:
        blockers = pipeline_readiness(self.project_path, self.step_data)
        if self.rb_ref_yes.isChecked():
            reference = self.reference_edit.text().strip()
            if not reference:
                blockers.append(self.localizer.get_string("step4_blocker_select_ref"))
            elif not os.path.isfile(reference):
                blockers.append(self.localizer.get_string("step4_blocker_ref_missing", reference=reference))
            if not self.reference_field_edit.text().strip():
                blockers.append(self.localizer.get_string("step4_blocker_pop_missing"))
        if self.rb_census_yes.isChecked():
            census = self.census_edit.text().strip()
            if not census:
                blockers.append(self.localizer.get_string("step4_blocker_census_missing", census=""))
            elif not os.path.isfile(census):
                blockers.append(self.localizer.get_string("step4_blocker_census_missing", census=census))
        if blockers:
            prefix = self.localizer.get_string("step4_status_not_ready", default="Nicht bereit:")
            self.readiness_label.setText(f"{prefix}\n• " + "\n• ".join(blockers))
        else:
            self.readiness_label.setText(self.localizer.get_string("step4_status_all_ready", default="Alle Input-Daten bereit"))
        if self.start_button is not None:
            btn_key = "step4_button_resume" if getattr(self, "_stopped_by_user", False) else "step4_button_start"
            self.start_button.setText(self.localizer.get_string(btn_key))
            self.start_button.setEnabled(not blockers and self._thread is None)
        if self.restart_button is not None:
            self.restart_button.setEnabled(not blockers and self._thread is None)

    def start_pipeline(self) -> None:
        self._start_pipeline(force_restart_models=False)

    def restart_pipeline(self) -> None:
        if not Dialogs.confirm(
            self,
            self.localizer.get_string("step4_dialog_confirm_restart_title", default="Alle Modelle neu berechnen"),
            self.localizer.get_string("step4_dialog_confirm_restart_body", default="Sollen Modell 1 bis Modell 6 und OSM Phase C wirklich neu berechnet werden?"),
        ):
            return
        self._start_pipeline(force_restart_models=True)

    def _start_pipeline(self, *, force_restart_models: bool) -> None:
        self._stopped_by_user = False
        self.refresh_readiness()
        if not self.start_button.isEnabled() or self._thread is not None:
            return
        model3_defaults = load_parameter_defaults("model3")
        step_radius = self.step_data.get("radius_km")
        step_cell_size = self.step_data.get("cell_size_m")
        model3_parameters = {
            "minimum_extent_radius_km": int(step_radius) if step_radius is not None else int(model3_defaults["minimum_extent_radius_km"]),
            "grid_size_e0_m": int(step_cell_size) if step_cell_size is not None else int(model3_defaults["grid_size_e0_m"]),
        }
        pop_local_val = (self.reference_edit.text().strip() or None) if self.rb_ref_yes.isChecked() else None
        census_val = (self.census_edit.text().strip() or None) if self.rb_census_yes.isChecked() else None
        options = {
            "model2_parameters": {key: edit.value() for key, edit in self.model2_edits.items()},
            "model3_parameters": model3_parameters,
            "model5_parameters": {key: edit.value() for key, edit in self.model5_edits.items()},
            "pop_local": pop_local_val,
            "pop_local_field": self.reference_field_edit.text().strip() or "POP",
            "custom_census": census_val,
            "no_local_reference": not self.rb_ref_yes.isChecked(),
            "force_restart_models": force_restart_models,
        }
        self._start_worker(options, phase_a_only=False)

    def start_preprocessing(self) -> None:
        if self._thread is not None:
            return
        basic_blockers = pipeline_readiness(self.project_path, self.step_data)
        basic_blockers = [
            blocker
            for blocker in basic_blockers
            if blocker.startswith(("Projekt", "Keine PBF", "PBF fehlt", "Mindestens eine PA"))
        ]
        if basic_blockers:
            self.log_box.appendPlainText(self.localizer.get_string("step4_log_phase_a_waiting", blockers="; ".join(basic_blockers), default="OSM-Phase A wartet: " + "; ".join(basic_blockers)))
            return
        reusable = UrbanActPipeline(
            self.project_path, self.step_data
        ).reusable_phase_a_outputs()
        if reusable is not None:
            self._outputs.update(reusable)
            self.phase_progress.setRange(0, 100)
            self.phase_progress.setValue(100)
            self.phase_progress.setFormat(self.localizer.get_string("step4_format_phase_a_ready", default="%p% – OSM-Vorverarbeitung vorhanden"))
            self.phase_label.setText(self.localizer.get_string("step4_status_phase_a_ready", default="OSM-Vorverarbeitung bereits vorhanden"))
            self.log_box.appendPlainText(
                self.localizer.get_string("step4_log_phase_a_reused", default="Vorhandene OSM-Phase-A-Dateien werden wiederverwendet; keine Neuberechnung nötig.")
            )
            for key, path in reusable.items():
                self.log_box.appendPlainText(f"{key}: {path}")
            self.status_changed.emit(self.localizer.get_string("step4_status_phase_a_ready", default="OSM-Vorverarbeitung bereits vorhanden"))
            self.refresh_readiness()
            return
        self._start_worker({}, phase_a_only=True)

    def _start_worker(self, options: dict, *, phase_a_only: bool) -> None:
        self._phase_a_only = phase_a_only
        self._thread = QtCore.QThread(self)
        self._worker = PipelineWorker(
            UrbanActPipeline(self.project_path, self.step_data),
            options,
            phase_a_only=phase_a_only,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.phase_started.connect(self._handle_phase_started)
        self._worker.phase_progress.connect(self.phase_progress.setValue)
        self._worker.phase_detail.connect(self._handle_phase_detail)
        self._worker.log_ready.connect(self.log_box.appendPlainText)
        self._worker.output_ready.connect(self._handle_output)
        self._worker.finished.connect(self._handle_finished)
        self._worker.error_ready.connect(self._handle_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error_ready.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_worker)
        self.start_button.setEnabled(False)
        self.restart_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.overall_progress.start_animation()
        self.phase_progress.start_animation()
        self._elapsed_seconds = 0
        self._timer.start(1000)
        self._thread.start()

    def stop_pipeline(self) -> None:
        self._stopped_by_user = True
        self.overall_progress.stop_animation()
        self.phase_progress.stop_animation()
        if self._worker is not None:
            self._worker.stop()
            self.stop_button.setEnabled(False)
        self.refresh_readiness()

    def _handle_phase_started(self, name: str, index: int, total: int) -> None:
        self._current_phase = name
        self.phase_label.setText(name)
        self.overall_progress.setValue(int((index - 1) / total * 100))
        self.phase_progress.setRange(0, 100)
        self.phase_progress.setValue(0)
        self.phase_progress.setFormat(f"%p% – {name}")
        self.phase_progress.setToolTip(name)
        self.status_changed.emit(name)

    def _handle_phase_detail(self, detail: str) -> None:
        text = " ".join(str(detail).split())
        if not text:
            return
        if self._current_phase and not text.startswith(self._current_phase):
            display_text = f"{self._current_phase}: {text}"
        else:
            display_text = text
        self.phase_progress.setFormat(f"%p% – {display_text}")
        self.phase_progress.setToolTip(display_text)
        self.status_changed.emit(display_text)

    def _handle_output(self, key: str, path: str) -> None:
        self._outputs[key] = path
        self.log_box.appendPlainText(f"{key}: {path}")

    def _handle_finished(self, _context: object) -> None:
        self.phase_progress.setValue(100)
        self.overall_progress.stop_animation()
        self.phase_progress.stop_animation()
        if self._phase_a_only:
            self.phase_label.setText(self.localizer.get_string("step4_status_phase_a_done", default="OSM-Vorverarbeitung abgeschlossen"))
            self.log_box.appendPlainText(self.localizer.get_string("step4_log_phase_a_done", default="OSM-Phase A abgeschlossen; Parameter können freigegeben werden."))
            self.status_changed.emit(self.localizer.get_string("step4_status_phase_a_done", default="OSM-Vorverarbeitung abgeschlossen"))
        else:
            self._is_completed = True
            self._stopped_by_user = False
            self.overall_progress.setValue(100)
            self.phase_label.setText(self.localizer.get_string("step4_status_completed", default="Abgeschlossen"))
            self.log_box.appendPlainText(self.localizer.get_string("step4_log_pipeline_completed", default="Pipeline vollständig abgeschlossen."))
            self.status_changed.emit(self.localizer.get_string("step4_status_pipeline_done", default="Pipeline abgeschlossen"))
            self.refresh_readiness()

    def _handle_error(self, phase: str, message: str) -> None:
        self.overall_progress.stop_animation()
        self.phase_progress.stop_animation()
        self.phase_label.setText(self.localizer.get_string("step4_status_error_phase", phase=phase, default=f"Fehler: {phase}"))
        self.log_box.appendPlainText(self.localizer.get_string("step4_log_error_phase", phase=phase, message=message, default=f"FEHLER [{phase}]: {message}"))
        self.status_changed.emit(self.localizer.get_string("step4_status_error_phase", phase=phase, default=f"{phase} fehlgeschlagen"))
        Dialogs.error(self, self.localizer.get_string("step4_status_error_phase", phase=phase, default=f"Fehler in {phase}"), message)

    def _clear_worker(self) -> None:
        self._timer.stop()
        self.overall_progress.stop_animation()
        self.phase_progress.stop_animation()
        self._thread = None
        self._worker = None
        self.stop_button.setEnabled(False)
        self.refresh_readiness()

    def _tick_elapsed(self) -> None:
        self._elapsed_seconds += 1
        minutes, seconds = divmod(self._elapsed_seconds, 60)
        self.elapsed_label.setText(f"{minutes:02d}:{seconds:02d}")

    def _reference_text_changed(self, text: str) -> None:
        self.refresh_readiness()

    def _reference_mode_changed(self) -> None:
        use_ref = self.rb_ref_yes.isChecked()
        self.lbl_ref.setVisible(use_ref)
        self.reference_edit.setVisible(use_ref)
        self.ref_browse_btn.setVisible(use_ref)
        self.lbl_pop.setVisible(use_ref)
        self.reference_field_edit.setVisible(use_ref)
        self.refresh_readiness()

    def _census_mode_changed(self) -> None:
        use_census = self.rb_census_yes.isChecked()
        self.lbl_census.setVisible(use_census)
        self.census_edit.setVisible(use_census)
        self.census_browse_btn.setVisible(use_census)
        self.refresh_readiness()

    def _restore_saved_settings(self) -> None:
        """Restore persisted processing choices when reopening a project."""
        input_directory = os.path.join(self.project_path, "input")
        population = os.path.join(input_directory, "pop_local.gpkg")
        population_field = os.path.join(input_directory, "pop_local_fieldname.txt")
        census = os.path.join(input_directory, "custom_census.tif")

        if os.path.isfile(population):
            self.rb_ref_yes.setChecked(True)
            self.reference_edit.setText(population)
            if os.path.isfile(population_field):
                try:
                    with open(population_field, "r", encoding="utf-8") as pf:
                        f_name = pf.read().strip()
                        if f_name:
                            self.reference_field_edit.setText(f_name)
                except Exception:
                    pass
        else:
            self.rb_ref_no.setChecked(True)

        if os.path.isfile(census):
            self.rb_census_yes.setChecked(True)
            self.census_edit.setText(census)
        else:
            self.rb_census_no.setChecked(True)

        for model_num, edits in ((2, self.model2_edits), (5, self.model5_edits)):
            param_file = os.path.join(input_directory, f"Model{model_num}_params.json")
            if os.path.isfile(param_file):
                try:
                    with open(param_file, encoding="utf-8") as pf:
                        saved_parameters = json.load(pf)
                except (OSError, ValueError, TypeError):
                    saved_parameters = {}
                for key, edit in edits.items():
                    if key in saved_parameters:
                        edit.setValue(int(saved_parameters[key]))

    def _browse_button(self, target, file_filter: str):
        button = QtWidgets.QPushButton(self.localizer.get_string("button_browse"), self)
        button.clicked.connect(lambda: self._browse_into(target, file_filter))
        return button

    def _browse_into(self, target, file_filter: str) -> None:
        path = Dialogs.open_file(self, self.localizer.get_string("step4_dialog_select_file", default="Datei auswählen"), file_filter, self.project_path)
        if path:
            target.setText(path)
