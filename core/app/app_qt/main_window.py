"""Main Qt window controller and step coordinator for the PANDO application."""

from __future__ import annotations

import json
import os
import webbrowser

from core.locales import localizer
from core.app.app_core.project import PROJECT_CONFIG_FILENAME, PROJECT_METADATA_FILENAME, tool_root
from core.app.app_core.settings import load_app_settings, save_app_settings
from core.app.app_core.update_checker import check_for_updates, perform_git_pull
from core.app.app_qt.app_state import AppState, StepId, previous_step_id, progress_percent_for_step
from core.app.app_qt.help_panel import HelpPanelWidget
from core.app.app_qt.qt_base import APP_NAME, BaseMainWindow, Dialogs, Qt, QtCore, QtGui, QtWidgets, QTimer, app_font, pyqtSignal, qfont_bold, qt_enum
from core.app.app_qt.steps.step0_welcome import Step0WelcomeWidget
from core.app.app_qt.steps.step1_search import Step1SearchWidget
from core.app.app_qt.steps.step2_data import Step2DataWidget
from core.app.app_qt.steps.step3_grid_area import Step3GridAreaWidget
from core.app.app_qt.steps.step4_processing import Step4ProcessingWidget
from core.app.app_qt.steps.step5_visum import Step5VisumWidget
from core.app.app_qt.steps.step6_results import Step6ResultsWidget


USER_AGENT = "PANDO V1.0 (Urban-Act Tool)"
_Q_OBJECT_BASE = QtCore.QObject if QtCore is not None else object


class _UpdateWorker(QtCore.QThread if QtCore is not None else object):
    finished_signal = pyqtSignal(object) if pyqtSignal is not None else None

    def run(self):
        result = check_for_updates()
        if self.finished_signal is not None:
            self.finished_signal.emit(result)


class _GitPullWorker(QtCore.QThread if QtCore is not None else object):
    finished_signal = pyqtSignal(bool, str) if pyqtSignal is not None else None

    def run(self):
        success, msg = perform_git_pull()
        if self.finished_signal is not None:
            self.finished_signal.emit(success, msg)


class GeocodeWorker(QtCore.QObject if QtCore is not None else object):
    """Performs Nominatim geocoding in a background QThread."""

    results_ready = pyqtSignal(list) if pyqtSignal is not None else None
    error_ready = pyqtSignal(str) if pyqtSignal is not None else None
    finished = pyqtSignal() if pyqtSignal is not None else None

    def __init__(self, city_name: str, language: str = "de"):
        super().__init__()
        self.city_name = city_name
        self.language = language or "de"

    def run(self) -> None:
        try:
            from geopy.geocoders import Nominatim

            geolocator = Nominatim(user_agent=USER_AGENT)
            locations = geolocator.geocode(
                self.city_name,
                exactly_one=False,
                limit=10,
                addressdetails=True,
                language=self.language,
                timeout=20,
            )
            self.results_ready.emit(locations or [])
        except Exception as exc:
            self.error_ready.emit(str(exc))
        finally:
            self.finished.emit()


class UrbanActQtMainWindow(BaseMainWindow):
    """Main window coordinating step workflow navigation, state management, and side panels."""

    def __init__(self):
        super().__init__(APP_NAME)
        self._initialize_localizer()
        self.app_settings = load_app_settings()
        self._licenses_accepted_this_session = False
        if self.app_settings.language:
            localizer.set_language(self.app_settings.language)
        self.state = AppState(language=localizer.get_current_language())
        self.state.set_project_context(tool_root())
        self._geocode_thread = None
        self._geocode_worker = None
        self._current_step_widget = None
        self._shell_widget = None
        self._back_button = None
        self._step_label = None
        self._progress_bar = None
        self._splitter = None
        self._help_visible = False
        self._help_panel = None
        self._last_update_result = None
        self.geofabrik_index = None

        try:
            self.setWindowFlags(self.windowFlags() | qt_enum(Qt, "WindowContextHelpButtonHint", "WindowType"))
        except Exception:
            pass

        try:
            from core.app.app_qt.qt_base import QtGui
            self._help_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence("F1"), self)
            self._help_shortcut.activated.connect(self._handle_toggle_help_clicked)
        except Exception:
            pass

        from core.app.app_core.processing import ensure_qgis_scripts_installed
        try:
            ensure_qgis_scripts_installed()
        except Exception:
            pass

        self._show_step0()
        self._trigger_update_check()
        if QTimer is not None:
            QTimer.singleShot(100, self._start_background_index_preload)

    def _start_background_index_preload(self) -> None:
        if self.geofabrik_index is not None:
            return
        import threading
        def _bg():
            self.geofabrik_index = self._load_geofabrik_index()
        threading.Thread(target=_bg, daemon=True).start()

    def _get_geofabrik_index(self) -> dict:
        if self.geofabrik_index is None:
            self.geofabrik_index = self._load_geofabrik_index()
        return self.geofabrik_index

    def changeEvent(self, event) -> None:
        if event is not None:
            evt_type = event.type()
            whats_this_type = getattr(QtCore.QEvent, "EnterWhatsThisMode", None)
            if whats_this_type is not None and evt_type == whats_this_type:
                try:
                    QtWidgets.QWhatsThis.leaveWhatsThisMode()
                except Exception:
                    pass
                self.toggle_help()
                event.accept()
                return
        super().changeEvent(event)

    def _initialize_localizer(self) -> None:
        locales_dir = os.path.join(tool_root(), "core", "locales")
        localizer.load_translations(locales_dir)

    def _load_geofabrik_index(self) -> dict:
        index_path = os.path.join(tool_root(), "core", "data", "osm", "geofabrik-index.json")
        try:
            with open(index_path, "r", encoding="utf-8") as index_file:
                data = json.load(index_file)
            try:
                from core.app.app_core.geofabrik import _get_or_build_shapes_cache
                _get_or_build_shapes_cache(data.get("features", []))
            except Exception:
                pass
            return data
        except Exception as exc:
            return {"error": str(exc)}

    def _handle_step1_search(self, city_name: str) -> None:
        if not self.show_copyright_dialog():
            return
        if self._geocode_thread is not None:
            return
        if not self.state.has_tool_context():
            Dialogs.warning(
                self,
                localizer.get_string("step1_error_project_missing_title"),
                localizer.get_string("step1_error_project_missing_message"),
            )
            self.set_status(localizer.get_string("step1_status_project_required"))
            return
        self.state.begin_search(city_name)
        self._save_project_metadata_if_possible()
        self._set_step1_search_enabled(False)
        self.set_status(localizer.get_string("step1_status_searching", city_name=city_name))

        self._geocode_thread = QtCore.QThread()
        self._geocode_worker = GeocodeWorker(city_name, language=localizer.get_current_language())
        self._geocode_worker.moveToThread(self._geocode_thread)

        self._geocode_thread.started.connect(self._geocode_worker.run)
        self._geocode_worker.results_ready.connect(self._handle_geocode_results)
        self._geocode_worker.error_ready.connect(self._handle_geocode_error)
        self._geocode_worker.finished.connect(self._geocode_thread.quit)
        self._geocode_worker.finished.connect(self._geocode_worker.deleteLater)
        self._geocode_thread.finished.connect(self._geocode_thread.deleteLater)
        self._geocode_thread.finished.connect(self._clear_geocode_worker)
        self._geocode_thread.start()

    def _handle_geocode_results(self, locations: list) -> None:
        if not locations:
            Dialogs.info(
                self,
                localizer.get_string("step1_info_no_results_title"),
                localizer.get_string("step1_info_no_results_message", city_name=self.state.search_city),
            )
            self.set_status(localizer.get_string("step1_status_ready"))
            return

        self._set_step1_search_enabled(True)
        self.state.set_geocode_results(locations)
        if isinstance(self._current_step_widget, Step1SearchWidget):
            self._current_step_widget.set_locations(locations)
            self.set_status(localizer.get_string("step2_status_select_or_confirm"))
        else:
            self._show_step1()

    def _handle_geocode_error(self, error_message: str) -> None:
        err_lower = error_message.lower()
        if any(term in err_lower for term in ("getaddrinfo", "name resolution", "nodename nor servname", "temporary failure in name resolution", "connection refused", "network is unreachable", "timed out", "time out", "unavailable", "serviceerror", "no internet")):
            message = localizer.get_string("step1_error_geocoding_no_internet", default="Keine Internetverbindung: Für die Ortssuche über Nominatim wird eine aktive Internetverbindung benötigt. Bitte prüfen Sie Ihre Verbindung.")
        else:
            message = localizer.get_string("step1_error_geocoding_message", error_details=error_message)
        Dialogs.error(
            self,
            localizer.get_string("step1_error_geocoding_title"),
            message,
        )
        self.set_status(localizer.get_string("step1_status_ready"))

    def _clear_geocode_worker(self) -> None:
        self._geocode_thread = None
        self._geocode_worker = None
        self._set_step1_search_enabled(True)

    def _get_default_projects_dir(self) -> str:
        """Determines the default projects directory for opening existing projects/config files."""
        if self.app_settings.last_workspace_path:
            p_dir = os.path.join(self.app_settings.last_workspace_path, "projects")
            if os.path.isdir(p_dir):
                return p_dir
            if os.path.isdir(self.app_settings.last_workspace_path):
                return self.app_settings.last_workspace_path

        p_dir = os.path.join(tool_root(), "projects")
        if os.path.isdir(p_dir):
            return p_dir
        return tool_root()

    def _handle_open_project_folder_requested(self) -> None:
        if not self.show_copyright_dialog():
            return
        from core.app.app_qt.dialogs.project_select_dialog import ProjectSelectDialog
        initial_dir = self._get_default_projects_dir()
        dialog = ProjectSelectDialog(self, projects_dir=initial_dir, localizer=localizer)
        exec_fn = getattr(dialog, "exec_", None) or getattr(dialog, "exec", None)
        accepted_code = qt_enum(QtWidgets.QDialog, "Accepted", "DialogCode")
        if exec_fn() in (1, accepted_code) and dialog.selected_project_path:
            self._load_existing_project(dialog.selected_project_path)

    def _handle_create_project_requested(self) -> None:
        workspace_path = Dialogs.select_directory(
            self,
            localizer.get_string("step1_dialog_new_project_workspace_title"),
            self.app_settings.last_workspace_path,
        )
        if not workspace_path:
            self.set_status(localizer.get_string("status_project_folder_cancelled"))
            return
        try:
            project_path = self.state.create_new_project(
                workspace_path,
                localizer.get_string("message_unknown_place"),
            )
        except Exception as exc:
            Dialogs.error(
                self,
                localizer.get_string("error_project_folder_title"),
                localizer.get_string("error_project_folder_message", error_details=exc),
            )
            return
        self._persist_app_settings(workspace_path=workspace_path)
        self._show_step1()
        self.set_status(localizer.get_string("status_project_folder_created", folder_path=os.path.normpath(project_path)))

    def _handle_language_switch_requested(self, lang_code: str | None = None) -> None:
        if lang_code:
            localizer.set_language(lang_code)
        else:
            current_language = localizer.get_current_language()
            next_language = "de" if current_language == "en" else "en"
            localizer.set_language(next_language)
        self.state.set_language(localizer.get_current_language())
        self._persist_app_settings(language=self.state.language)
        self._save_project_metadata_if_possible()
        self._trigger_update_check()
        self._rebuild_current_step()

    def _load_existing_project(self, project_path_or_file: str) -> None:
        try:
            step_id = self.state.load_project(project_path_or_file)
            if self.state.language:
                localizer.set_language(self.state.language)
            self._persist_app_settings(
                workspace_path=self.state.workspace_path,
                language=localizer.get_current_language(),
            )
            self._show_loaded_project_step(step_id)
        except Exception as exc:
            Dialogs.error(
                self,
                localizer.get_string("step1_error_open_project_title"),
                localizer.get_string("step1_error_open_project_message", error_details=exc),
            )

    def _show_loaded_project_step(self, step_id: StepId) -> None:
        if step_id in (StepId.GRID_AREA, StepId.PROJECT_PBF) and self.state.selected_location is None:
            raise ValueError("Project metadata does not contain a selected location.")
        self._show_step_by_id(step_id)
        self.set_status(
            localizer.get_string(
                "step1_status_project_loaded",
                project_path=os.path.normpath(self.state.project_path),
                step=int(self.state.current_step),
            )
        )

    def _set_step1_search_enabled(self, enabled: bool) -> None:
        if not isinstance(self._current_step_widget, Step1SearchWidget):
            return
        try:
            self._current_step_widget.set_search_enabled(enabled)
        except RuntimeError:
            pass

    def _handle_step2_selection(self, location) -> None:
        self.state.set_selected_location(location)
        display_name = getattr(location, "address", "")
        if display_name:
            self.set_status(display_name)
        if self._next_button is not None and self.state.current_step in (StepId.SEARCH, StepId.CITY_SELECTION):
            self._next_button.setEnabled(location is not None)

    def _handle_manual_marker_requested(self) -> None:
        self.set_status(localizer.get_string("step2_status_select_or_confirm"))

    def _open_location_on_osm(self, location) -> None:
        lat = getattr(location, "latitude", None)
        lon = getattr(location, "longitude", None)
        if lat is None or lon is None:
            return
        webbrowser.open_new_tab(f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=13/{lat}/{lon}")

    def _handle_step1_confirmed(self, location) -> None:
        if location is None:
            Dialogs.warning(
                self,
                localizer.get_string("step2_error_selection_missing_title"),
                localizer.get_string("step2_error_selection_missing_message"),
            )
            return
        self.state.confirm_location(location)
        if not self.state.project_path:
            self.state.create_new_project(
                selected_location=self.state.selected_location,
                unknown_place=localizer.get_string("message_unknown_place"),
            )
        self._show_step2()
        self._save_project_metadata_if_possible()

    def _handle_step2_confirmed(self, step2_data: dict) -> None:
        self._cancel_pbf_region_search()
        try:
            self.state.set_step4_data(step2_data)
        except ValueError as exc:
            Dialogs.error(self, localizer.get_string("message_general_error_title"), str(exc))
            return
        self._save_project_metadata_if_possible()
        self._show_step3()

    def _handle_step3_position_changed(self, location) -> None:
        self.state.set_selected_location(location)
        self._save_project_metadata_if_possible()

    def _handle_step3_confirmed(self, step3_data: dict) -> None:
        try:
            self.state.set_step3_data(step3_data)
        except ValueError as exc:
            Dialogs.error(self, localizer.get_string("message_general_error_title"), str(exc))
            return
        self._save_project_metadata_if_possible()
        self._show_step4()

    def _handle_back_requested(self) -> None:
        target_step = previous_step_id(self.state.current_step)
        if target_step is None:
            return
        if self._current_step_widget is not None:
            for cancel_fn_name in ("cancel_all_workers", "cancel_region_search", "cancel_processing", "stop_visum_pipeline", "stop_pipeline"):
                cancel_fn = getattr(self._current_step_widget, cancel_fn_name, None)
                if callable(cancel_fn):
                    try:
                        cancel_fn()
                    except Exception:
                        pass
        self._show_step_by_id(target_step)

    def _cancel_pbf_region_search(self) -> None:
        if isinstance(self._current_step_widget, Step2DataWidget):
            self._current_step_widget.cancel_region_search()

    def _show_step_by_id(self, step_id: StepId) -> None:
        if step_id == StepId.WELCOME:
            self._show_step0()
        elif step_id in (StepId.SEARCH, StepId.CITY_SELECTION):
            self._show_step1()
        elif step_id == StepId.PROJECT_PBF:
            self._show_step2()
        elif step_id == StepId.GRID_AREA:
            self._show_step3()
        elif step_id == StepId.PROCESSING:
            self._show_step4()
        elif step_id == StepId.VISUM:
            self._show_step5()
        elif step_id == StepId.RESULTS:
            self._show_step6()
        else:
            raise ValueError(f"Unsupported step: {step_id}")

    def _rebuild_current_step(self) -> None:
        self._show_step_by_id(self.state.current_step)

    def _show_step0(self) -> None:
        step0_widget = Step0WelcomeWidget(localizer, self)
        step0_widget.start_requested.connect(self._handle_step0_start_requested)
        step0_widget.project_folder_open_requested.connect(self._handle_open_project_folder_requested)
        step0_widget.language_switch_requested.connect(self._handle_language_switch_requested)
        self._show_step(StepId.WELCOME, step0_widget, localizer.get_string("window_title_welcome", default="Willkommen zu PANDO"))

    def _handle_step0_start_requested(self) -> None:
        if not self.show_copyright_dialog():
            return
        self._show_step1()

    def _show_step1(self) -> None:
        step1_widget = Step1SearchWidget(
            localizer,
            self,
            project_path=self.state.project_path,
            workspace_path=self.state.workspace_path,
            locations=self.state.geocode_results,
        )
        step1_widget.search_requested.connect(self._handle_step1_search)
        step1_widget.selection_changed.connect(self._handle_step2_selection)
        step1_widget.open_osm_requested.connect(self._open_location_on_osm)
        step1_widget.selection_confirmed.connect(self._handle_step1_confirmed)
        step1_widget.project_folder_open_requested.connect(self._handle_open_project_folder_requested)
        step1_widget.language_switch_requested.connect(self._handle_language_switch_requested)
        if self.state.search_city:
            step1_widget.set_city_name(self.state.search_city)
        self._show_step(StepId.SEARCH, step1_widget, localizer.get_string("step1_status_ready"))
        if self.state.geocode_results and step1_widget.selected():
            self._handle_step2_selection(step1_widget.selected())
        QTimer.singleShot(0, step1_widget.focus_city_input)

    def _show_step2(self) -> None:
        step2_widget = Step2DataWidget(localizer, self.state.payload_for_step4(), self._get_geofabrik_index(), self)
        step2_widget.status_changed.connect(self.set_status)
        step2_widget.confirmed.connect(self._handle_step2_confirmed)
        self._show_step(StepId.PROJECT_PBF, step2_widget)

    def _show_step3(self) -> None:
        step3_widget = Step3GridAreaWidget(
            localizer,
            self.state.selected_location,
            self,
            step3_data=self.state.step3_data,
            step2_data=self.state.step4_data,
        )
        step3_widget.status_changed.connect(self.set_status)
        step3_widget.position_changed.connect(self._handle_step3_position_changed)
        step3_widget.confirmed.connect(self._handle_step3_confirmed)
        self._show_step(StepId.GRID_AREA, step3_widget)

    def _show_step4(self) -> None:
        step4_widget = Step4ProcessingWidget(localizer, self.state.payload_for_step5(), self)
        step4_widget.status_changed.connect(self._handle_step4_status_changed)
        step4_widget.next_requested.connect(self._show_step5)
        project_path = self.state.step4_data.get("project_path") or ""
        self._show_step(
            StepId.PROCESSING,
            step4_widget,
            localizer.get_string("step4_status_pbf_ready") + f" {project_path}",
        )

    def _handle_step4_status_changed(self, status_msg: str) -> None:
        self.set_status(status_msg)
        if self.state.current_step == StepId.PROCESSING and self._next_button is not None:
            is_done = getattr(self._current_step_widget, "is_pipeline_completed", lambda: False)()
            self._next_button.setEnabled(is_done)

    def _show_step5(self) -> None:
        project_path = self.state.project_path or self.state.step4_data.get("project_path") or ""
        step5_widget = Step5VisumWidget(localizer, self, project_path=project_path)
        step5_widget.finished.connect(lambda ok, msg: self._show_step6() if ok else None)
        if hasattr(step5_widget, "running_state_changed") and step5_widget.running_state_changed is not None:
            step5_widget.running_state_changed.connect(self._handle_step5_running_state_changed)
        self._show_step(
            StepId.VISUM,
            step5_widget,
            f"Visum 2025 Import und Verarbeitung: {project_path}",
        )

    def _handle_step5_running_state_changed(self, is_running: bool) -> None:
        if self._next_button is not None and self.state.current_step == StepId.VISUM:
            self._next_button.setEnabled(not is_running)

    def _show_step6(self) -> None:
        project_path = self.state.project_path or self.state.step4_data.get("project_path") or ""
        step6_widget = Step6ResultsWidget(localizer, self, project_path=project_path)
        self._show_step(
            StepId.RESULTS,
            step6_widget,
            localizer.get_string("step7_title", default="Schritt 6: Ergebnisse & Auswertung") + f": {project_path}",
        )

    def show_copyright_dialog(self, force: bool = False) -> bool:
        if getattr(self, "_licenses_accepted_this_session", False) and not force:
            return True
        from core.app.app_qt.dialogs.copyright_dialog import CopyrightDialog
        dialog = CopyrightDialog(
            parent=self,
            already_accepted=self.app_settings.accepted_licenses,
        )
        exec_fn = getattr(dialog, "exec_", None) or getattr(dialog, "exec", None)
        accepted_code = qt_enum(QtWidgets.QDialog, "Accepted", "DialogCode")
        if exec_fn() in (1, accepted_code):
            accepted_map = dialog.get_accepted_result()
            self.app_settings.accepted_licenses = accepted_map
            self._licenses_accepted_this_session = True
            save_app_settings(self.app_settings)
            self.set_status(localizer.get_string("status_terms_accepted"))
            return True
        return False

    def _show_step(self, step_id: StepId, widget, status_message: str | None = None) -> None:
        if self._current_step_widget is not None and self._current_step_widget != widget:
            for cancel_fn_name in ("cancel_all_workers", "cancel_region_search", "cancel_processing", "stop_visum_pipeline", "stop_pipeline"):
                cancel_fn = getattr(self._current_step_widget, cancel_fn_name, None)
                if callable(cancel_fn):
                    try:
                        cancel_fn()
                    except Exception:
                        pass
        self.state.current_step = step_id
        self._current_step_widget = widget
        self.setCentralWidget(self._build_shell(widget))
        self._update_shell_navigation(step_id)
        if self._help_visible and self._help_panel is not None:
            self._help_panel.update_help(step_id, localizer, project_path=self.state.project_path)
        if status_message:
            self.set_status(status_message)
        self._save_project_metadata_if_possible()

    def _handle_next_requested(self) -> None:
        widget = self._current_step_widget
        if widget is None:
            return
        if self.state.current_step == StepId.VISUM:
            self._show_step6()
            return
        if hasattr(widget, "_confirm_selection"):
            widget._confirm_selection()
        elif hasattr(widget, "_request_search"):
            widget._request_search()
        elif hasattr(widget, "next_requested") and hasattr(widget.next_requested, "emit"):
            widget.next_requested.emit()
        elif hasattr(widget, "_start_processing"):
            widget._start_processing()

    def _build_shell(self, content_widget):
        shell = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Left main area (Progress bar + Step content + Bottom navigation bar)
        left_box = QtWidgets.QWidget(shell)
        left_layout = QtWidgets.QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self._progress_bar = QtWidgets.QProgressBar(left_box)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setStyleSheet(
            "QProgressBar { border: none; background-color: #e2e8f0; height: 4px; } "
            "QProgressBar::chunk { background-color: #2563eb; }"
        )
        left_layout.addWidget(self._progress_bar)

        left_layout.addWidget(content_widget, 1)

        nav = QtWidgets.QWidget(left_box)
        nav.setObjectName("wizard_nav_bar")
        nav.setStyleSheet(
            "QWidget#wizard_nav_bar { background-color: #f8fafc; border-top: 1px solid #e2e8f0; }"
        )
        nav_layout = QtWidgets.QHBoxLayout(nav)
        nav_layout.setContentsMargins(18, 10, 18, 10)
        nav_layout.setSpacing(10)

        standard_btn_style = (
            "QPushButton { background-color: #ffffff; color: #334155; border: 1px solid #cbd5e1; border-radius: 4px; padding: 5px 14px; } "
            "QPushButton:hover { background-color: #f1f5f9; color: #0f172a; border-color: #94a3b8; } "
            "QPushButton:checked { background-color: #e2e8f0; border-color: #64748b; color: #0f172a; } "
            "QPushButton:disabled { background-color: #f8fafc; color: #94a3b8; border-color: #e2e8f0; }"
        )

        if self._last_update_result is not None:
            initial_msg = self._last_update_result.message
            has_update = self._last_update_result.has_update
            btn_txt = self._last_update_result.button_text
        else:
            initial_msg = localizer.get_string("main_update_checking", default="Prüfe auf Updates...")
            has_update = False
            btn_txt = ""

        self._update_status_label = QtWidgets.QLabel(initial_msg, nav)
        self._update_status_label.setFont(app_font(9))
        self._update_status_label.setStyleSheet("color: #64748b;")
        nav_layout.addWidget(self._update_status_label)

        self._btn_git_pull = QtWidgets.QPushButton(
            btn_txt or localizer.get_string("main_button_git_pull", default="Update durchführen (git pull)"),
            nav
        )
        self._btn_git_pull.setFont(app_font(9, qfont_bold()))
        self._btn_git_pull.setStyleSheet(
            "QPushButton { background-color: #10b981; color: #ffffff; font-weight: bold; border-radius: 4px; padding: 5px 14px; border: 1px solid #059669; } "
            "QPushButton:hover { background-color: #059669; }"
        )
        self._btn_git_pull.setVisible(has_update)
        self._btn_git_pull.clicked.connect(self._handle_git_pull_click)
        nav_layout.addWidget(self._btn_git_pull)

        self._btn_github_repo = QtWidgets.QPushButton(localizer.get_string("main_button_github_repo", default="GitHub Repository ↗"), nav)
        self._btn_github_repo.setFont(app_font(9))
        self._btn_github_repo.setStyleSheet(standard_btn_style)
        self._btn_github_repo.clicked.connect(lambda: webbrowser.open("https://github.com/isv-vuv/PANDO"))
        nav_layout.addWidget(self._btn_github_repo)

        self._back_button = QtWidgets.QPushButton(localizer.get_string("button_back"), nav)
        self._back_button.setStyleSheet(standard_btn_style)
        self._back_button.clicked.connect(self._handle_back_requested)
        nav_layout.addWidget(self._back_button)

        nav_layout.addStretch(1)

        self._help_button = QtWidgets.QPushButton(
            localizer.get_string("button_help_hide") if self._help_visible else localizer.get_string("button_help"),
            nav,
        )
        try:
            self._help_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogHelpButton))
        except Exception:
            pass
        self._help_button.setToolTip("Hilfe (F1)")
        self._help_button.setCheckable(True)
        self._help_button.setChecked(self._help_visible)
        self._help_button.setStyleSheet(standard_btn_style)
        self._help_button.clicked.connect(self._handle_toggle_help_clicked)
        nav_layout.addWidget(self._help_button)

        self._next_button = QtWidgets.QPushButton(localizer.get_string("button_next"), nav)
        self._next_button.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; font-weight: bold; border-radius: 4px; padding: 6px 18px; border: 1px solid #1d4ed8; } "
            "QPushButton:hover { background-color: #1d4ed8; border-color: #1e40af; } "
            "QPushButton:pressed { background-color: #1e40af; } "
            "QPushButton:disabled { background-color: #cbd5e1; color: #94a3b8; border-color: #cbd5e1; }"
        )
        self._next_button.clicked.connect(self._handle_next_requested)
        nav_layout.addWidget(self._next_button)

        self._update_nav_button_widths()

        left_layout.addWidget(nav)

        # Splitter dividing main view (left) and help panel (right)
        self._splitter = QtWidgets.QSplitter(qt_enum(Qt, "Horizontal", "Orientation"), shell)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(left_box)

        if self._help_panel is None:
            self._help_panel = HelpPanelWidget(self)
            self._help_panel.close_requested.connect(self._handle_close_help)

        self._splitter.addWidget(self._help_panel)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)

        self._help_panel.setVisible(self._help_visible)
        if self._help_visible:
            self._help_panel.update_help(self.state.current_step, localizer)

        layout.addWidget(self._splitter, 1)
        self._shell_widget = shell
        return shell

    def _update_shell_navigation(self, step_id: StepId) -> None:
        if self._progress_bar is not None:
            self._progress_bar.setValue(progress_percent_for_step(step_id))
        is_welcome = (step_id == StepId.WELCOME)

        if getattr(self, "_update_status_label", None) is not None:
            self._update_status_label.setVisible(is_welcome)
        if getattr(self, "_btn_git_pull", None) is not None and not is_welcome:
            self._btn_git_pull.setVisible(False)
        if getattr(self, "_btn_github_repo", None) is not None:
            self._btn_github_repo.setVisible(is_welcome)

        if self._back_button is not None:
            self._back_button.setText(localizer.get_string("button_back"))
            self._back_button.setEnabled(previous_step_id(step_id) is not None)
            self._back_button.setVisible(not is_welcome)
        if self._next_button is not None:
            self._next_button.setText(localizer.get_string("button_next"))
            if is_welcome or step_id == StepId.RESULTS:
                self._next_button.setVisible(False)
            else:
                self._next_button.setVisible(True)
                if step_id in (StepId.SEARCH, StepId.CITY_SELECTION):
                    has_sel = (
                        getattr(self._current_step_widget, "selected", lambda: None)() is not None
                    )
                    self._next_button.setEnabled(has_sel)
                elif step_id == StepId.PROCESSING:
                    is_done = getattr(self._current_step_widget, "is_pipeline_completed", lambda: False)()
                    self._next_button.setEnabled(is_done)
                elif step_id == StepId.VISUM:
                    is_running = getattr(self._current_step_widget, "is_running", lambda: False)()
                    self._next_button.setEnabled(not is_running)
                else:
                    self._next_button.setEnabled(True)

        self._update_nav_button_widths()

    def _update_nav_button_widths(self) -> None:
        if getattr(self, "_help_button", None) is None:
            return
        fm = self._help_button.fontMetrics() if hasattr(self._help_button, "fontMetrics") else (QtGui.QFontMetrics(self._help_button.font()) if QtGui is not None else None)
        if fm is None:
            return

        texts = [
            localizer.get_string("button_back"),
            localizer.get_string("button_help"),
            localizer.get_string("button_help_hide"),
            localizer.get_string("button_next"),
            "Hilfe ausblenden",
            "Hide Help",
            "Hilfe",
            "Help",
            "Zurück",
            "Back",
            "Weiter",
            "Next",
        ]
        max_w = 0
        for t in texts:
            try:
                w = fm.horizontalAdvance(t) if hasattr(fm, "horizontalAdvance") else fm.width(t)
                if w > max_w:
                    max_w = w
            except Exception:
                pass

        uniform_width = max(max_w + 58, 120)
        if getattr(self, "_back_button", None) is not None:
            self._back_button.setFixedWidth(uniform_width)
        if getattr(self, "_help_button", None) is not None:
            self._help_button.setFixedWidth(uniform_width)
        if getattr(self, "_next_button", None) is not None:
            self._next_button.setFixedWidth(uniform_width)

    def _trigger_update_check(self) -> None:
        self._update_worker = _UpdateWorker(self)
        self._update_worker.finished_signal.connect(self._on_update_check_finished)
        self._update_worker.start()

    def _on_update_check_finished(self, result) -> None:
        self._last_update_result = result
        if getattr(self, "_update_status_label", None) is not None:
            self._update_status_label.setText(result.message)
            if getattr(self, "_btn_git_pull", None) is not None:
                if result.has_update:
                    if getattr(result, "button_text", ""):
                        self._btn_git_pull.setText(result.button_text)
                    self._btn_git_pull.setVisible(True)
                else:
                    self._btn_git_pull.setVisible(False)

    def _handle_git_pull_click(self) -> None:
        if getattr(self, "_btn_git_pull", None) is not None:
            self._btn_git_pull.setEnabled(False)
        if getattr(self, "_update_status_label", None) is not None:
            self._update_status_label.setText(localizer.get_string("main_update_running", default="⌛ Führe git pull aus..."))
        self._pull_worker = _GitPullWorker(self)
        self._pull_worker.finished_signal.connect(self._on_git_pull_finished)
        self._pull_worker.start()

    def _on_git_pull_finished(self, success: bool, message: str) -> None:
        if getattr(self, "_btn_git_pull", None) is not None:
            self._btn_git_pull.setEnabled(True)
        if getattr(self, "_update_status_label", None) is not None:
            if success:
                self._update_status_label.setText("✅ " + message.replace("\n", " "))
                if getattr(self, "_btn_git_pull", None) is not None:
                    self._btn_git_pull.setVisible(False)
                Dialogs.info(self, localizer.get_string("main_update_success_title", default="Update erfolgreich"), message)
                self._trigger_update_check()
            else:
                self._update_status_label.setText("❌ " + message.replace("\n", " "))
                Dialogs.error(self, localizer.get_string("main_update_error_title", default="Update Fehler"), message)

    def _handle_toggle_help_clicked(self) -> None:
        self.toggle_help()

    def _handle_close_help(self) -> None:
        self.toggle_help(show=False)

    def _get_available_screen_geometry(self) -> QtCore.QRect:
        try:
            if hasattr(self, "screen") and self.screen() is not None:
                return self.screen().availableGeometry()
            if hasattr(self, "windowHandle") and self.windowHandle() and self.windowHandle().screen():
                return self.windowHandle().screen().availableGeometry()
        except Exception:
            pass
        if QtWidgets.QApplication.desktop() is not None:
            return QtWidgets.QApplication.desktop().availableGeometry(self)
        return QtCore.QRect(0, 0, 1920, 1080)

    def toggle_help(self, show: bool | None = None) -> None:
        HELP_PANEL_WIDTH = 340

        if show is None:
            show = not self._help_visible

        self._help_visible = show

        if getattr(self, "_help_button", None) is not None:
            self._help_button.setChecked(show)
            btn_text = localizer.get_string("button_help_hide") if show else localizer.get_string("button_help")
            self._help_button.setText(btn_text)
            self._update_nav_button_widths()

        if self._help_panel is None:
            return

        if show:
            self._help_panel.update_help(self.state.current_step, localizer, project_path=self.state.project_path)
            self._help_panel.setVisible(True)

            if not self.isMaximized():
                curr_w = self.width()
                curr_h = self.height()
                curr_x = self.x()
                curr_y = self.y()

                handle_w = self._splitter.handleWidth() if getattr(self, "_splitter", None) is not None else 0
                target_w = curr_w + HELP_PANEL_WIDTH + handle_w

                avail_geom = self._get_available_screen_geometry()
                screen_left = avail_geom.left()
                screen_right = avail_geom.left() + avail_geom.width()

                right_edge = curr_x + target_w

                if right_edge > screen_right:
                    overflow = right_edge - screen_right
                    new_x = curr_x - overflow
                    if new_x < screen_left:
                        new_x = screen_left
                        new_w = avail_geom.width()
                    else:
                        new_w = target_w
                else:
                    new_x = curr_x
                    new_w = target_w

                self.move(new_x, curr_y)
                self.resize(new_w, curr_h)

                if getattr(self, "_splitter", None) is not None:
                    self._splitter.setSizes([curr_w, HELP_PANEL_WIDTH])
            else:
                # Maximized: expand inwards by letting splitter divide the maximized window space
                if getattr(self, "_splitter", None) is not None:
                    total_w = self._splitter.width()
                    content_w = max(100, total_w - HELP_PANEL_WIDTH)
                    self._splitter.setSizes([content_w, HELP_PANEL_WIDTH])
        else:
            self._help_panel.setVisible(False)
            if not self.isMaximized():
                curr_w = self.width()
                curr_h = self.height()
                handle_w = self._splitter.handleWidth() if getattr(self, "_splitter", None) is not None else 0
                new_w = max(self.minimumWidth(), curr_w - HELP_PANEL_WIDTH - handle_w)
                self.resize(new_w, curr_h)

    def _persist_app_settings(self, workspace_path: str | None = None, language: str | None = None) -> None:
        if workspace_path is not None:
            self.app_settings.last_workspace_path = workspace_path
        if language is not None:
            self.app_settings.language = language
        save_app_settings(self.app_settings)

    def _save_project_metadata_if_possible(self) -> None:
        if not self.state.has_project_context():
            return
        try:
            self.state.save_project_metadata()
        except Exception:
            return
