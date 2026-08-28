"""Final Qt page for parameter approval and the complete processing pipeline."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from threading import Event
from typing import Optional

from core.locales import localizer
from core.app.app_core.geo import get_utm_epsg
from core.app.app_core.model_pipeline import load_parameter_defaults
from core.app.app_core.pipeline import PipelineCallbacks, UrbanActPipeline, pipeline_readiness
from core.app.app_qt.qt_base import (
    AnimatedProgressBar,
    Dialogs,
    QColor,
    QCursor,
    QIcon,
    Qt,
    QtCore,
    QtGui,
    QtWidgets,
    WindowsTaskbarProgress,
    app_font,
    bind_header_scroll_separator,
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
    file_locked = pyqtSignal(str, object) if pyqtSignal is not None else None

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

    def _handle_file_locked(self, path: str) -> bool:
        if self.file_locked is None:
            return False
        event = Event()
        result = {"retry": False}
        self.file_locked.emit(str(path), (event, result))
        event.wait()
        return result["retry"]

    def run(self) -> None:
        callbacks = PipelineCallbacks(
            phase_started=self._phase_started,
            phase_progress=self.phase_progress.emit,
            phase_detail=self.phase_detail.emit,
            log=self._emit_log,
            output=self.output_ready.emit,
            file_locked=self._handle_file_locked,
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
        ("minimum_population_level_0", "min_pop_0", "Mindestbevölkerung Metropolregion (Stufe 0)", 1, 100_000_000),
        ("minimum_population_level_1", "min_pop_1", "Mindestbevölkerung Oberzentrum (Stufe 1)", 1, 100_000_000),
        ("minimum_population_level_2", "min_pop_2", "Mindestbevölkerung Mittelzentrum (Stufe 2)", 1, 100_000_000),
        ("population_tolerance", "pop_tol", "Zulässige Abweichung der Mindestbevölkerungszahl [%]", 0, 100),
        ("distance_tolerance", "dist_tol", "Zulässige Abweichung der Mindestdistanz [%]", 0, 100),
        ("dual_centres_search_radius_km", "dual_radius", "Suchradius Doppelzentren [km]", 1, 10_000),
        ("dual_centres_population_tolerance", "dual_pop_tol", "Zulässige Abweichung der Mindestbevölkerungszahl bei Doppelzentren [%]", 0, 100),
    )

    _MODEL5_FIELDS = (
        ("minimum_distance_level_3_4_m", "dist_3_4", "Mindestabstand zwischen Stufe 3 und 4 [m]", 1, 100_000),
        ("minimum_distance_level_3_m", "dist_3", "Mindestabstand zwischen Stufe 3 [m]", 1, 100_000),
        ("minimum_distance_level_4_m", "dist_4", "Mindestabstand zwischen Stufe 4 [m]", 1, 100_000),
        ("minimum_intensity_level_3", "min_intensity_3", "Mindest-Intensität für die Klassifizierung als Stufe 3", 0, 25),
        ("minimum_intensity_level_4", "min_intensity_4", "Mindest-Intensität für die Klassifizierung als Stufe 4", 0, 25),
    )

    def _create_parameter_label(self, model_prefix: str, short_key: str, default_label: str, edit_widget: Optional[QtWidgets.QWidget], parent: QtWidgets.QWidget, word_wrap: bool = True) -> QtWidgets.QLabel:
        lbl_key = f"{model_prefix}_{short_key}" if "_" in model_prefix else f"{model_prefix}_param_{short_key}"
        label_str = self.localizer.get_string(lbl_key, default=default_label)
        tooltip_str = self.localizer.get_string(f"{lbl_key}_tooltip", default="")

        lbl = QtWidgets.QLabel(label_str, parent)
        lbl.setFont(app_font(10))
        lbl.setWordWrap(word_wrap)
        if tooltip_str:
            lbl.setToolTip(tooltip_str)
            if edit_widget is not None:
                edit_widget.setToolTip(tooltip_str)
        return lbl

    status_updated = pyqtSignal(str) if pyqtSignal is not None else None

    def _emit_status(self, message: str) -> None:
        if self.status_updated is not None:
            try:
                self.status_updated.emit(message)
            except Exception:
                pass
        if self.main_window is not None and hasattr(self.main_window, "set_status"):
            try:
                self.main_window.set_status(message)
            except Exception:
                pass

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
        self.model2_edits: dict[str, QtWidgets.QLineEdit] = {}
        self.model5_edits: dict[str, QtWidgets.QLineEdit] = {}

        self.reference_edit = None
        self.reference_field_edit = None
        self.census_edit = None
        self.no_reference_check = None
        self.rb_nationwide_no = None
        self.rb_nationwide_yes = None
        self.nationwide_params_widget = None
        self.nw_radius_edit = None
        self.nw_min_intensity_edit = None
        self.nw_min_intensity_l3_edit = None
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

    def _format_initial_int(self, val: int) -> str:
        """Formatiert einen ganzzahligen Parameter mit Tausendertrennpunkten für das Textfeld."""
        lang = self.localizer.get_current_language() if self.localizer else "de"
        if lang == "de":
            return f"{val:,}".replace(",", ".")
        return f"{val:,}"

    def _read_int_field(self, edit: Optional[object], default: int = 0) -> int:
        """Liest den Integer-Wert aus einem QLineEdit oder Widget aus, bereinigt Trennpunkte und fällt ggf. auf den Standardwert zurück."""
        if edit is None:
            return default
        if hasattr(edit, "value") and not hasattr(edit, "text"):
            try:
                return int(edit.value())
            except Exception:
                pass
        text = edit.text().strip() if hasattr(edit, "text") else str(edit)
        cleaned = text.replace(".", "").replace(",", "").replace(" ", "").strip()
        try:
            return int(cleaned)
        except Exception:
            return default

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
        scroll.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget(scroll)
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(0, 2, 8, 4)
        layout.setSpacing(6)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        bind_header_scroll_separator(header, scroll)

        # Top Action & Tip Bar
        top_bar = QtWidgets.QWidget(content)
        top_bar_layout = QtWidgets.QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(0, 2, 0, 4)
        top_bar_layout.setSpacing(12)

        hover_tip_label = QtWidgets.QLabel(
            self.localizer.get_string(
                "step4_intro_hover_tip",
                default="Hinweis: Bewegen Sie den Mauszeiger über die Parameterbezeichnungen oder Eingabefelder, um ausführliche Hilfetexte zu den Parametern zu erhalten."
            ),
            top_bar,
        )
        hover_tip_label.setFont(app_font(9))
        hover_tip_label.setWordWrap(True)
        hover_tip_label.setStyleSheet("color: #475569; padding: 2px 2px;")
        top_bar_layout.addWidget(hover_tip_label, 1)

        btn_import_params = QtWidgets.QPushButton(
            self.localizer.get_string("step4_button_import_params", default="Parameter aus anderem Projekt laden..."),
            top_bar,
        )
        btn_import_params.setFont(app_font(9))
        btn_import_params.setFixedHeight(30)
        btn_import_params.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        btn_import_params.clicked.connect(self._handle_import_params_clicked)
        top_bar_layout.addWidget(btn_import_params, 0, qt_enum(Qt, "AlignRight", "AlignmentFlag"))

        layout.addWidget(top_bar)

        EXT_EXEC_LABEL_WIDTH = 240

        # External Data Box (Full Width)
        external_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_external_data")), content)
        external = QtWidgets.QGridLayout(external_box)
        external.setSpacing(6)
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
        self.rb_ref_no.toggled.connect(self._reference_mode_changed)
        self.rb_census_yes.toggled.connect(self._census_mode_changed)
        self.rb_census_no.toggled.connect(self._census_mode_changed)
        self.reference_edit.textChanged.connect(self._reference_text_changed)
        self.reference_field_edit.textChanged.connect(self._on_param_changed)
        self.census_edit.textChanged.connect(self._on_param_changed)

        layout.addWidget(external_box)

        # Nationwide Intensity Estimation Box
        nationwide_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_nationwide_intensity", default="Landesweite Intensitätsschätzung")), content)
        nationwide_layout = QtWidgets.QVBoxLayout(nationwide_box)
        nationwide_layout.setContentsMargins(12, 8, 12, 8)
        nationwide_layout.setSpacing(8)

        # Row 1: Question + Radio Buttons (Ja / Nein)
        ask_widget = QtWidgets.QWidget(nationwide_box)
        ask_layout = QtWidgets.QHBoxLayout(ask_widget)
        ask_layout.setContentsMargins(0, 0, 0, 0)
        ask_layout.setSpacing(16)

        lbl_ask_nationwide = QtWidgets.QLabel(self.localizer.get_string("step4_ask_nationwide_intensity", default="Landesweite Berechnung innergemeindlicher Zentralitäten durchführen:"), ask_widget)
        lbl_ask_nationwide.setFont(app_font(9, qfont_bold()))
        nw_tooltip = self.localizer.get_string("step4_ask_nationwide_intensity_tooltip", default="")
        if nw_tooltip:
            lbl_ask_nationwide.setToolTip(nw_tooltip)

        self.rb_nationwide_no = QtWidgets.QRadioButton(self.localizer.get_string("option_no", default="Nein"), ask_widget)
        self.rb_nationwide_yes = QtWidgets.QRadioButton(self.localizer.get_string("option_yes", default="Ja"), ask_widget)
        self.rb_nationwide_no.setChecked(True)
        self.bg_nationwide = QtWidgets.QButtonGroup(nationwide_box)
        self.bg_nationwide.addButton(self.rb_nationwide_no)
        self.bg_nationwide.addButton(self.rb_nationwide_yes)

        ask_layout.addWidget(lbl_ask_nationwide)
        ask_layout.addWidget(self.rb_nationwide_no)
        ask_layout.addWidget(self.rb_nationwide_yes)
        ask_layout.addStretch()
        nationwide_layout.addWidget(ask_widget)

        COMMON_SPINBOX_WIDTH = 115

        # Row 2: All 3 parameters in ONE horizontal line without line breaks (visible only when Ja)
        self.nationwide_params_widget = QtWidgets.QWidget(nationwide_box)
        nw_layout = QtWidgets.QHBoxLayout(self.nationwide_params_widget)
        nw_layout.setContentsMargins(0, 4, 0, 0)
        nw_layout.setSpacing(12)

        self.nw_radius_edit = QtWidgets.QLineEdit("500", self.nationwide_params_widget)
        self.nw_radius_edit.setAlignment(qt_enum(Qt, "AlignRight", "AlignmentFlag"))
        self.nw_radius_edit.setFixedWidth(70)
        self.nw_radius_edit.textChanged.connect(self._on_param_changed)
        lbl_nw_radius = self._create_parameter_label("step4_label", "nationwide_radius", "Ausschlussradius um bestehende zentrale Orte [m]", self.nw_radius_edit, self.nationwide_params_widget, word_wrap=False)

        self.nw_min_intensity_edit = QtWidgets.QLineEdit("7", self.nationwide_params_widget)
        self.nw_min_intensity_edit.setAlignment(qt_enum(Qt, "AlignRight", "AlignmentFlag"))
        self.nw_min_intensity_edit.setFixedWidth(70)
        self.nw_min_intensity_edit.textChanged.connect(self._on_param_changed)
        lbl_nw_min_int = self._create_parameter_label("step4_label", "nationwide_min_intensity", "Mindest-Intensität landesweit", self.nw_min_intensity_edit, self.nationwide_params_widget, word_wrap=False)

        self.nw_min_intensity_l3_edit = QtWidgets.QLineEdit("10", self.nationwide_params_widget)
        self.nw_min_intensity_l3_edit.setAlignment(qt_enum(Qt, "AlignRight", "AlignmentFlag"))
        self.nw_min_intensity_l3_edit.setFixedWidth(70)
        self.nw_min_intensity_l3_edit.textChanged.connect(self._on_param_changed)
        lbl_nw_min_int_l3 = self._create_parameter_label("step4_label", "nationwide_min_intensity_level_3", "Mindest-Intensität Stufe 3", self.nw_min_intensity_l3_edit, self.nationwide_params_widget, word_wrap=False)

        nw_layout.addWidget(lbl_nw_radius)
        nw_layout.addWidget(self.nw_radius_edit)
        nw_layout.addSpacing(12)
        nw_layout.addWidget(lbl_nw_min_int)
        nw_layout.addWidget(self.nw_min_intensity_edit)
        nw_layout.addSpacing(12)
        nw_layout.addWidget(lbl_nw_min_int_l3)
        nw_layout.addWidget(self.nw_min_intensity_l3_edit)
        nw_layout.addStretch()

        nationwide_layout.addWidget(self.nationwide_params_widget)
        self.nationwide_params_widget.setVisible(False)
        self.rb_nationwide_yes.toggled.connect(self._nationwide_mode_changed)
        self.rb_nationwide_no.toggled.connect(self._nationwide_mode_changed)
        layout.addWidget(nationwide_box)

        # Side-by-side Container for Model 2 and Model 5 Box (placed on the exact same top horizontal level!)
        models_widget = QtWidgets.QWidget(content)
        models_layout = QtWidgets.QHBoxLayout(models_widget)
        models_layout.setContentsMargins(0, 0, 0, 0)
        models_layout.setSpacing(12)

        # Uniform width for all numeric parameter inputs in Model 2 and Model 5
        PARAM_FIELD_WIDTH = 80

        # Model 2 Box (Left side of models container)
        model2_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_model2")), models_widget)
        model2 = QtWidgets.QGridLayout(model2_box)
        model2.setSpacing(6)
        model2.setColumnStretch(0, 1)
        model2.setColumnStretch(1, 0)
        model2_defaults = load_parameter_defaults("model2")
        for row_idx, (key, short_key, label, minimum, maximum) in enumerate(self._MODEL2_FIELDS):
            val = int(model2_defaults[key])
            edit = QtWidgets.QLineEdit(model2_box)
            edit.setText(self._format_initial_int(val))
            edit.setAlignment(qt_enum(Qt, "AlignRight", "AlignmentFlag"))
            edit.setFixedWidth(PARAM_FIELD_WIDTH)
            edit.textChanged.connect(self._on_param_changed)
            self.model2_edits[key] = edit
            lbl_widget = self._create_parameter_label("model2", short_key, label, edit, model2_box)
            model2.addWidget(lbl_widget, row_idx, 0)
            model2.addWidget(edit, row_idx, 1, qt_enum(Qt, "AlignRight", "AlignmentFlag"))

        # Model 5 Box (Right side of models container)
        model5_box = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step4_group_model5")), models_widget)
        model5 = QtWidgets.QGridLayout(model5_box)
        model5.setSpacing(6)
        model5.setColumnStretch(0, 1)
        model5.setColumnStretch(1, 0)
        model5_defaults = load_parameter_defaults("model5")
        for row_idx, (key, short_key, label, minimum, maximum) in enumerate(self._MODEL5_FIELDS):
            val = int(model5_defaults[key])
            edit = QtWidgets.QLineEdit(model5_box)
            edit.setText(self._format_initial_int(val))
            edit.setAlignment(qt_enum(Qt, "AlignRight", "AlignmentFlag"))
            edit.setFixedWidth(PARAM_FIELD_WIDTH)
            edit.textChanged.connect(self._on_param_changed)
            self.model5_edits[key] = edit
            lbl_widget = self._create_parameter_label("model5", short_key, label, edit, model5_box)
            model5.addWidget(lbl_widget, row_idx, 0)
            model5.addWidget(edit, row_idx, 1, qt_enum(Qt, "AlignRight", "AlignmentFlag"))

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
        self.overall_progress = AnimatedProgressBar(execution_box, sync_taskbar=True)
        self.phase_progress = AnimatedProgressBar(execution_box, sync_taskbar=False)
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
            status_msg = f"{prefix} " + ", ".join(blockers)
        else:
            status_msg = self.localizer.get_string("step4_status_all_ready", default="Alle Input-Daten bereit")

        if self.readiness_label is not None:
            self.readiness_label.setText(status_msg)
        self._emit_status(status_msg)

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
        model2_defaults = load_parameter_defaults("model2")
        model5_defaults = load_parameter_defaults("model5")
        model2_params = {key: self._read_int_field(edit, int(model2_defaults.get(key, 0))) for key, edit in self.model2_edits.items()}
        model5_params = {key: self._read_int_field(edit, int(model5_defaults.get(key, 0))) for key, edit in self.model5_edits.items()}

        options = {
            "model2_parameters": model2_params,
            "model3_parameters": model3_parameters,
            "model5_parameters": model5_params,
            "pop_local": pop_local_val,
            "pop_local_field": self.reference_field_edit.text().strip() or "POP",
            "custom_census": census_val,
            "no_local_reference": not self.rb_ref_yes.isChecked(),
            "force_restart_models": force_restart_models,
            "run_nationwide_intensity": self.rb_nationwide_yes.isChecked(),
            "nationwide_parameters": {
                "radius": self._read_int_field(self.nw_radius_edit, 500),
                "min_intensity": self._read_int_field(self.nw_min_intensity_edit, 7),
                "min_intensity_level_3": self._read_int_field(self.nw_min_intensity_l3_edit, 10),
            },
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
        self._thread = QtCore.QThread()
        self._worker = PipelineWorker(
            UrbanActPipeline(self.project_path, self.step_data),
            options,
            phase_a_only=phase_a_only,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.phase_started.connect(self._handle_phase_started)
        self._worker.phase_progress.connect(self._handle_phase_progress)
        self._worker.phase_detail.connect(self._handle_phase_detail)
        self._worker.log_ready.connect(self._handle_log_ready)
        self._worker.output_ready.connect(self._handle_output)
        self._worker.finished.connect(self._handle_finished)
        self._worker.error_ready.connect(self._handle_error)
        self._worker.file_locked.connect(self._handle_file_locked)
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

    def cancel_processing(self) -> None:
        """Safely stops, disconnects, and tears down the active pipeline worker thread."""
        self.stop_pipeline()
        if self._worker is not None:
            try:
                self._worker.phase_started.disconnect()
                self._worker.phase_progress.disconnect()
                self._worker.phase_detail.disconnect()
                self._worker.log_ready.disconnect()
                self._worker.output_ready.disconnect()
                self._worker.finished.disconnect()
                self._worker.error_ready.disconnect()
                self._worker.file_locked.disconnect()
            except Exception:
                pass
        if self._thread is not None:
            try:
                self._thread.quit()
                self._thread.wait(100)
                if getattr(self._thread, "isRunning", lambda: False)():
                    self._thread.requestInterruption()
                    self._thread.setParent(None)
            except Exception:
                pass
            self._thread = None
            self._worker = None

    def cancel_all_workers(self) -> None:
        self.cancel_processing()

    def closeEvent(self, event) -> None:
        self.cancel_processing()
        super().closeEvent(event)

    def _handle_phase_started(self, name: str, index: int, total: int) -> None:
        self._current_phase = name
        self._phase_index = index
        self._phase_total = total
        self.phase_label.setText(name)
        self.overall_progress.setValue(int((index - 1) / total * 100))
        self.phase_progress.setRange(0, 100)
        self.phase_progress.setValue(0)
        self.phase_progress.setFormat(f"%p% – {name}")
        self.phase_progress.setToolTip(name)
        self.status_changed.emit(name)

    def _handle_phase_progress(self, val: int) -> None:
        self.phase_progress.setValue(val)
        if getattr(self, "_phase_total", 0) > 0 and getattr(self, "_phase_index", 0) > 0:
            overall = ((self._phase_index - 1) + (max(0, min(100, val)) / 100.0)) / self._phase_total * 100.0
            self.overall_progress.setValue(int(round(overall)))

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

    def _handle_log_ready(self, message: str) -> None:
        if not message:
            return
        if message.startswith("PROGRESS_INLINE:"):
            formatted = message[len("PROGRESS_INLINE:"):].strip()
            if not getattr(self, "_is_logging_inline", False):
                self.log_box.appendPlainText(formatted)
                self._is_logging_inline = True
            else:
                doc = self.log_box.document() if hasattr(self.log_box, "document") else None
                last_block = doc.lastBlock() if doc is not None and hasattr(doc, "lastBlock") else None
                if last_block is not None and last_block.isValid() and QtGui is not None:
                    cursor = QtGui.QTextCursor(last_block)
                    end_of_block = qt_enum(QtGui.QTextCursor, "EndOfBlock", "MoveOperation")
                    keep_anchor = qt_enum(QtGui.QTextCursor, "KeepAnchor", "MoveMode")
                    cursor.movePosition(end_of_block, keep_anchor)
                    cursor.removeSelectedText()
                    cursor.insertText(formatted)
                    self.log_box.ensureCursorVisible()
                else:
                    self.log_box.appendPlainText(formatted)

            if "100 - done" in formatted or "done." in formatted:
                self._is_logging_inline = False
        else:
            self._is_logging_inline = False
            self.log_box.appendPlainText(message)

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

    def _handle_file_locked(self, filepath: str, sync_tuple: tuple) -> None:
        event, result = sync_tuple
        filename = Path(filepath).name
        title = self.localizer.get_string("file_locked_title", default="Datei gesperrt (QGIS geöffnet?)")
        msg = self.localizer.get_string(
            "file_locked_message",
            filename=filename,
            filepath=filepath,
            default=(
                f"Die Ausgabedatei '{filename}' ist durch ein anderes Programm (z. B. QGIS) gesperrt und kann nicht überschrieben werden.\n\n"
                f"Dateipfad:\n{filepath}\n\n"
                "Bitte schließen Sie QGIS bzw. das Projekt mit dieser Datei und klicken Sie anschließend auf 'Wiederholen'."
            ),
        )
        retry_text = self.localizer.get_string("button_retry", default="Wiederholen")
        cancel_text = self.localizer.get_string("button_cancel", default="Abbrechen")
        try:
            result["retry"] = Dialogs.retry_cancel(
                self,
                title,
                msg,
                retry_text=retry_text,
                cancel_text=cancel_text,
            )
        except Exception:
            result["retry"] = False
        event.set()

    def _handle_error(self, phase: str, message: str) -> None:
        self.overall_progress.stop_animation()
        self.phase_progress.stop_animation()
        try:
            WindowsTaskbarProgress.get_instance().set_error()
        except Exception:
            pass
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
        self._is_logging_inline = False
        self.stop_button.setEnabled(False)
        self.refresh_readiness()

    def _tick_elapsed(self) -> None:
        self._elapsed_seconds += 1
        minutes, seconds = divmod(self._elapsed_seconds, 60)
        self.elapsed_label.setText(f"{minutes:02d}:{seconds:02d}")

    def _reference_text_changed(self, text: str) -> None:
        self._save_current_settings()
        self.refresh_readiness()

    def _reference_mode_changed(self) -> None:
        use_ref = self.rb_ref_yes.isChecked()
        self.lbl_ref.setVisible(use_ref)
        self.reference_edit.setVisible(use_ref)
        self.ref_browse_btn.setVisible(use_ref)
        self.lbl_pop.setVisible(use_ref)
        self.reference_field_edit.setVisible(use_ref)
        self._save_current_settings()
        self.refresh_readiness()

    def _census_mode_changed(self) -> None:
        use_census = self.rb_census_yes.isChecked()
        self.lbl_census.setVisible(use_census)
        self.census_edit.setVisible(use_census)
        self.census_browse_btn.setVisible(use_census)
        self._save_current_settings()
        self.refresh_readiness()

    def _nationwide_mode_changed(self) -> None:
        use_nw = self.rb_nationwide_yes.isChecked()
        self.nationwide_params_widget.setVisible(use_nw)
        self._save_current_settings()
        self.refresh_readiness()

    def _on_param_changed(self) -> None:
        self._save_current_settings()
        self.refresh_readiness()

    def _save_current_settings(self) -> None:
        """Persist all current user settings and input parameters to project/input directory."""
        if getattr(self, "_is_restoring", False) or not self.project_path:
            return
        input_directory = os.path.join(self.project_path, "input")
        try:
            os.makedirs(input_directory, exist_ok=True)
            # 1. Nationwide parameters
            nw_params = {
                "enabled": self.rb_nationwide_yes.isChecked(),
                "radius": self._read_int_field(self.nw_radius_edit, 500),
                "min_intensity": self._read_int_field(self.nw_min_intensity_edit, 7),
                "min_intensity_level_3": self._read_int_field(self.nw_min_intensity_l3_edit, 10),
            }
            with open(os.path.join(input_directory, "Nationwide_params.json"), "w", encoding="utf-8") as f:
                json.dump(nw_params, f, indent=2)

            # 2. Model 2 & Model 5 parameters
            model2_defaults = load_parameter_defaults("model2")
            model5_defaults = load_parameter_defaults("model5")
            m2_params = {k: self._read_int_field(e, int(model2_defaults.get(k, 0))) for k, e in self.model2_edits.items()}
            m5_params = {k: self._read_int_field(e, int(model5_defaults.get(k, 0))) for k, e in self.model5_edits.items()}
            with open(os.path.join(input_directory, "Model2_params.json"), "w", encoding="utf-8") as f:
                json.dump(m2_params, f, indent=2)
            with open(os.path.join(input_directory, "Model5_params.json"), "w", encoding="utf-8") as f:
                json.dump(m5_params, f, indent=2)

            # 3. Population field
            if self.rb_ref_yes.isChecked() and self.reference_field_edit:
                f_name = self.reference_field_edit.text().strip()
                if f_name:
                    with open(os.path.join(input_directory, "pop_local_fieldname.txt"), "w", encoding="utf-8") as f:
                        f.write(f_name)
        except Exception:
            pass

    def _restore_saved_settings(self) -> None:
        """Restore persisted processing choices when reopening a project."""
        self._is_restoring = True
        try:
            if not self.project_path or not os.path.isdir(self.project_path):
                return
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

            nw_param_file = os.path.join(input_directory, "Nationwide_params.json")
            if os.path.isfile(nw_param_file):
                try:
                    with open(nw_param_file, "r", encoding="utf-8") as pf:
                        nw_saved = json.load(pf)
                    enabled = nw_saved.get("enabled", True)
                    if enabled:
                        self.rb_nationwide_yes.setChecked(True)
                    else:
                        self.rb_nationwide_no.setChecked(True)
                    if "radius" in nw_saved:
                        self.nw_radius_edit.setText(self._format_initial_int(int(nw_saved["radius"])))
                    if "min_intensity" in nw_saved:
                        self.nw_min_intensity_edit.setText(self._format_initial_int(int(nw_saved["min_intensity"])))
                    if "min_intensity_level_3" in nw_saved:
                        self.nw_min_intensity_l3_edit.setText(self._format_initial_int(int(nw_saved["min_intensity_level_3"])))
                except Exception:
                    pass
            else:
                self.rb_nationwide_no.setChecked(True)

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
                            edit.setText(self._format_initial_int(int(saved_parameters[key])))
        finally:
            self._is_restoring = False

    def _browse_button(self, target, file_filter: str):
        button = QtWidgets.QPushButton(self.localizer.get_string("button_browse"), self)
        button.clicked.connect(lambda: self._browse_into(target, file_filter))
        return button

    def _browse_into(self, target, file_filter: str) -> None:
        path = Dialogs.open_file(self, self.localizer.get_string("step4_dialog_select_file", default="Datei auswählen"), file_filter, self.project_path)
        if path:
            target.setText(path)

    def _handle_import_params_clicked(self) -> None:
        from core.app.app_qt.dialogs.project_select_dialog import ProjectSelectDialog
        from core.app.app_core.project import tool_root

        workspace_dir = None
        if self.project_path:
            workspace_dir = os.path.dirname(os.path.normpath(self.project_path))
        if not workspace_dir or not os.path.isdir(workspace_dir):
            workspace_dir = os.path.join(tool_root(), "projects")

        dialog = ProjectSelectDialog(
            self,
            projects_dir=workspace_dir,
            localizer=self.localizer,
            dialog_title=self.localizer.get_string("dialog_project_select_params_title", default="Projekt für Parameter-Import auswählen"),
            accept_button_text=self.localizer.get_string("dialog_project_select_btn_import", default="Parameter übernehmen"),
        )
        exec_fn = getattr(dialog, "exec_", None) or getattr(dialog, "exec", None)
        accepted_code = qt_enum(QtWidgets.QDialog, "Accepted", "DialogCode")
        if exec_fn() in (1, accepted_code) and dialog.selected_project_path:
            self._import_params_from_project(dialog.selected_project_path)

    def _import_params_from_project(self, source_project_path: str) -> None:
        from core.app.app_core.project import load_project_metadata
        try:
            input_dir = os.path.join(source_project_path, "input")
            m2_file = os.path.join(input_dir, "Model2_params.json")
            m5_file = os.path.join(input_dir, "Model5_params.json")
            nw_file = os.path.join(input_dir, "Nationwide_params.json")
            pop_file = os.path.join(input_dir, "pop_local.gpkg")
            pop_field_file = os.path.join(input_dir, "pop_local_fieldname.txt")
            census_file = os.path.join(input_dir, "custom_census.tif")

            m2_data = {}
            m5_data = {}
            nw_data = {}
            metadata_loaded = False
            metadata = {}

            if os.path.isfile(m2_file):
                try:
                    with open(m2_file, "r", encoding="utf-8") as f:
                        m2_data = json.load(f)
                except Exception:
                    pass
            if os.path.isfile(m5_file):
                try:
                    with open(m5_file, "r", encoding="utf-8") as f:
                        m5_data = json.load(f)
                except Exception:
                    pass
            if os.path.isfile(nw_file):
                try:
                    with open(nw_file, "r", encoding="utf-8") as f:
                        nw_data = json.load(f)
                except Exception:
                    pass

            # Fallback to config.json
            if not m2_data or not m5_data or not nw_data:
                try:
                    metadata = load_project_metadata(source_project_path)
                    metadata_loaded = True
                    step4_data = metadata.get("step4_data") or {}
                    if not m2_data:
                        m2_data = step4_data.get("model2_parameters") or {}
                    if not m5_data:
                        m5_data = step4_data.get("model5_parameters") or {}
                    if not nw_data:
                        nw_data = step4_data.get("nationwide_parameters") or {}
                except Exception:
                    pass

            self._is_restoring = True
            try:
                # 1. Local Reference Population Data
                if os.path.isfile(pop_file):
                    self.rb_ref_yes.setChecked(True)
                    dest_pop = os.path.join(self.project_path, "input", "pop_local.gpkg") if self.project_path else pop_file
                    if self.project_path and os.path.normpath(pop_file) != os.path.normpath(dest_pop):
                        try:
                            os.makedirs(os.path.dirname(dest_pop), exist_ok=True)
                            shutil.copy2(pop_file, dest_pop)
                        except Exception:
                            pass
                    self.reference_edit.setText(dest_pop)
                    pop_field_name = "POP"
                    if os.path.isfile(pop_field_file):
                        try:
                            with open(pop_field_file, "r", encoding="utf-8") as pf:
                                pop_field_name = pf.read().strip() or "POP"
                        except Exception:
                            pass
                    elif metadata_loaded:
                        pop_field_name = (metadata.get("step4_data") or {}).get("pop_local_field") or "POP"
                    self.reference_field_edit.setText(pop_field_name)
                else:
                    self.rb_ref_no.setChecked(True)
                    self.reference_edit.setText("")

                # 2. Custom Census Raster
                if os.path.isfile(census_file):
                    self.rb_census_yes.setChecked(True)
                    dest_census = os.path.join(self.project_path, "input", "custom_census.tif") if self.project_path else census_file
                    if self.project_path and os.path.normpath(census_file) != os.path.normpath(dest_census):
                        try:
                            os.makedirs(os.path.dirname(dest_census), exist_ok=True)
                            shutil.copy2(census_file, dest_census)
                        except Exception:
                            pass
                    self.census_edit.setText(dest_census)
                else:
                    self.rb_census_no.setChecked(True)
                    self.census_edit.setText("")

                # 3. Nationwide Intensity Estimation
                if nw_data:
                    enabled = nw_data.get("enabled", True)
                    if enabled:
                        self.rb_nationwide_yes.setChecked(True)
                    else:
                        self.rb_nationwide_no.setChecked(True)
                    if "radius" in nw_data:
                        self.nw_radius_edit.setText(self._format_initial_int(int(nw_data["radius"])))
                    if "min_intensity" in nw_data:
                        self.nw_min_intensity_edit.setText(self._format_initial_int(int(nw_data["min_intensity"])))
                    if "min_intensity_level_3" in nw_data:
                        self.nw_min_intensity_l3_edit.setText(self._format_initial_int(int(nw_data["min_intensity_level_3"])))
                else:
                    self.rb_nationwide_no.setChecked(True)

                # 4. Model 2 Parameters
                for key, edit in self.model2_edits.items():
                    if key in m2_data:
                        edit.setText(self._format_initial_int(int(m2_data[key])))

                # 5. Model 5 Parameters
                for key, edit in self.model5_edits.items():
                    if key in m5_data:
                        edit.setText(self._format_initial_int(int(m5_data[key])))

            finally:
                self._is_restoring = False

            # Update visibility of conditional sections
            self._reference_mode_changed()
            self._census_mode_changed()
            self._nationwide_mode_changed()

            self._save_current_settings()
            self.refresh_readiness()

            proj_name = os.path.basename(os.path.normpath(source_project_path))
            msg = self.localizer.get_string(
                "step4_status_params_imported",
                project_name=proj_name,
                default=f"QGIS-Parameter erfolgreich aus '{proj_name}' importiert.",
            )
            self.append_log(msg)
            Dialogs.info(self, self.localizer.get_string("step4_title", default="Schritt 4"), msg)
        except Exception as exc:
            Dialogs.error(
                self,
                self.localizer.get_string("message_general_error_title", default="Fehler"),
                f"Fehler beim Laden der QGIS-Parameter: {exc}",
            )

    def append_log(self, text: str) -> None:
        if hasattr(self, "log_box") and self.log_box is not None:
            self.log_box.appendPlainText(text)
