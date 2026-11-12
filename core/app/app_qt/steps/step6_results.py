"""Qt page for Step 6: Model Results & Evaluation Overview."""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from core.locales import localizer
from core.scripts.visum.scripts.visum_com import create_visum
from core.app.app_qt.results_map import ResultsMapWidget
from core.app.app_qt.qt_base import (
    Dialogs,
    QgsApplication,
    Qt,
    QtCore,
    QtGui,
    QtWidgets,
    app_font,
    bind_header_scroll_separator,
    create_step_header,
    pyqtSignal,
    qfont_bold,
    qt_enum,
)

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
logger = logging.getLogger(__name__)
_BACKGROUND_PROCESSES: list[subprocess.Popen] = []
_ACTIVE_VISUM_INSTANCES: list[Any] = []


def open_path_in_explorer(target_path: str | Path) -> bool:
    """Open a folder or select a file in system file explorer."""
    path_str = str(target_path)
    if not os.path.exists(path_str):
        parent = os.path.dirname(path_str)
        if os.path.exists(parent):
            path_str = parent
        else:
            return False

    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path_str)
            return True
        elif system == "Darwin":
            proc = subprocess.Popen(["open", path_str])
            _BACKGROUND_PROCESSES.append(proc)
            return True
        else:
            proc = subprocess.Popen(["xdg-open", path_str])
            _BACKGROUND_PROCESSES.append(proc)
            return True
    except Exception as exc:
        logger.warning("Could not open file explorer for %s: %s", path_str, exc)
        return False


def find_qgis_executable() -> Optional[str]:
    """Resolve the desktop executable belonging to the running QGIS runtime.

    Never search other installations or PATH: a different QGIS version may be
    installed alongside the one whose libraries PANDO has loaded.
    """
    prefix = QgsApplication.prefixPath() if QgsApplication is not None else ""
    prefix = prefix or os.environ.get("QGIS_PREFIX_PATH", "")
    if not prefix:
        return None

    prefix_path = Path(prefix)
    system = platform.system()
    if system == "Windows":
        # Standalone and OSGeo4W installs use <root>/apps/qgis[-ltr].
        # Prefer the executable inside that prefix where available.
        names = ("qgis-ltr-bin.exe",) if "ltr" in prefix_path.name.lower() else (
            "qgis-bin.exe", "qgis.exe",
        )
        bin_dirs = [prefix_path / "bin"]
        if prefix_path.parent.name.lower() == "apps":
            bin_dirs.append(prefix_path.parent.parent / "bin")
        candidates = [directory / name for directory in bin_dirs for name in names]
    elif system == "Darwin":
        bundle = next((p for p in (prefix_path, *prefix_path.parents) if p.suffix == ".app"), None)
        candidates = [bundle / "Contents" / "MacOS" / "QGIS"] if bundle else []
    else:
        candidates = [prefix_path / "bin" / "qgis"]

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None

from core.app.app_core.result_maps import (
    create_qgis_project_for_layers, layer_draw_order_key, result_paths,
)


def open_in_qgis(layer_paths: Sequence[str | Path], project_path: str | Path = "", title: str = "pando_view") -> bool:
    """Launch QGIS 4 with a configured project containing local CRS, 50% grayscale OSM, and selected layers."""
    qgs_proj = None
    if project_path:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", title).strip("_").lower()
        if not slug:
            slug = "view"
        qgs_proj = create_qgis_project_for_layers(project_path, layer_paths, project_name=f"pando_{slug}")

    target_to_open = str(qgs_proj) if qgs_proj and os.path.exists(str(qgs_proj)) else None
    if not target_to_open:
        existing = [str(p) for p in layer_paths if os.path.exists(str(p))]
        if existing:
            target_to_open = existing[0]

    if not target_to_open:
        return False

    qgis_bin = find_qgis_executable()
    try:
        if qgis_bin:
            kwargs: dict[str, Any] = {}
            if platform.system() == "Windows":
                detached = getattr(subprocess, "DETACHED_PROCESS", 0)
                new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                kwargs["creationflags"] = detached | new_group
            proc = subprocess.Popen([qgis_bin, target_to_open], **kwargs)
            _BACKGROUND_PROCESSES.append(proc)
            return True
        logger.warning("Could not find the desktop executable for the running QGIS installation")
        return False
    except Exception as exc:
        logger.warning("Could not launch QGIS with %s: %s", target_to_open, exc)
        return False


def _set_visum_project_directories(visum, language: str = "de") -> None:
    """Set project directory paths in Visum using the script from procedure sequence XML (DE_Verfahrensablauf.xml / EN_ProcedureSequence.xml)."""
    is_en = str(language).lower().startswith("en")
    target_xml_name = "EN_ProcedureSequence.xml" if is_en else "DE_Verfahrensablauf.xml"

    script_executed = False
    try:
        from core.app.app_core.model_pipeline import tool_root
        root = Path(tool_root())
        candidate_paths = [
            root / "core" / "scripts" / "visum" / "helper_files" / "pro" / target_xml_name,
            root / "core" / "scripts" / "visum" / "helper_files" / target_xml_name,
        ]

        for cand in candidate_paths:
            if cand.exists():
                import xml.etree.ElementTree as ET
                tree = ET.parse(str(cand))
                for op in tree.getroot().findall(".//OPERATION"):
                    comment = op.get("COMMENT", "")
                    if "Project Directories" in comment or "Verzeichnis" in comment:
                        script_elem = op.find(".//INTERNALSCRIPTCODE")
                        if script_elem is not None and script_elem.text:
                            globs = {"Visum": visum, "visum": visum, "print": logger.info}
                            exec(script_elem.text, globs)
                            script_executed = True
                            logger.info("Project directories successfully set in Visum from %s", cand.name)
                            break
            if script_executed:
                break
    except Exception as exc:
        logger.warning("Could not execute script from %s: %s", target_xml_name, exc)

    if not script_executed:
        try:
            doc_name = str(visum.UserPreferences.DocumentName or "")
            if "\\" in doc_name:
                p = doc_name.rsplit("\\", 2)[0]
            else:
                p = str(visum.GetPath(2) or "").rstrip("\\")
            paths = {
                37: "\\script",
                21: "\\fil",
                15: "\\att",
                41: "\\screenshot",
                43: "\\tt-gpa",
                92: "\\lay",
                8:  "\\gpa",
                47: "\\gpa",
                20: "\\lla",
                57: "\\log-file",
                69: "\\mtx",
                5:  "\\dmd",
                1:  "\\net",
                2:  "\\ver",
                25: "\\shapefile",
                81: "\\qla",
                33: "\\icon",
            }
            for k, v in paths.items():
                visum.SetPath(k, p + v)
            logger.info("Projektpfade gesetzt (Fallback): %s", p)
        except Exception as exc:
            logger.warning("Fallback setting Visum project paths failed: %s", exc)


def find_visum_target_version(visum_dir: Path) -> Optional[Path]:
    """Find the best candidate .ver (or .net) file in visum directory or its subdirectories."""
    ver_files = list(visum_dir.rglob("*.ver"))
    if not ver_files:
        net_files = list(visum_dir.rglob("*.net"))
        return net_files[0] if net_files else None

    # Priority 1: 07_*_Model.ver or *Model*.ver (excluding Debug)
    model_vers = [f for f in ver_files if "model" in f.stem.lower() and "debug" not in f.stem.lower()]
    if model_vers:
        return sorted(model_vers, key=lambda f: f.name, reverse=True)[0]

    # Priority 2: Non-debug .ver files with highest step number
    non_debug = [f for f in ver_files if "debug" not in f.stem.lower()]
    if non_debug:
        return sorted(non_debug, key=lambda f: f.name, reverse=True)[0]

    return sorted(ver_files, key=lambda f: f.name, reverse=True)[0]


def resolve_visum_gpa(visum_dir: Path, gpa_filename: str) -> Optional[Path]:
    """Resolve a graphic parameter file (.gpax / .gpa) from project or helper templates."""
    if not gpa_filename:
        return None
    visum_dir = Path(visum_dir)
    gpa_dir = visum_dir / "gpa"

    # 1. Exact match in project gpa dir
    cand = gpa_dir / gpa_filename
    if cand.is_file():
        return cand

    # 2. Match without DE_/EN_ prefix in project gpa dir
    clean_name = gpa_filename.replace("DE_", "").replace("EN_", "")
    cand = gpa_dir / clean_name
    if cand.is_file():
        return cand

    # 3. Match stem with .gpax or .gpa in project gpa dir
    stem = Path(clean_name).stem
    for ext in (".gpax", ".gpa"):
        cand = gpa_dir / f"{stem}{ext}"
        if cand.is_file():
            return cand

    # 4. Search in helper_files template folder
    try:
        from core.app.app_core.model_pipeline import tool_root
        helper_dir = Path(tool_root()) / "core" / "scripts" / "visum" / "helper_files" / "gpa"
        for name in (gpa_filename, clean_name, f"{stem}.gpax", f"{stem}.gpa"):
            cand = helper_dir / name
            if cand.is_file():
                return cand
    except Exception:
        pass

    return None


def ensure_visum_language(visum, language: str = "de") -> None:
    """Ensure Visum application language matches PANDO's current UI language ('ENG' or 'DEU')."""
    target_code = "ENG" if str(language).lower().startswith("en") else "DEU"
    try:
        curr_lang = str(visum.GetCurrentLanguage()).strip().upper()
        if target_code in curr_lang or (target_code == "DEU" and ("DE" in curr_lang or "GER" in curr_lang)):
            return
        try:
            visum.SetLanguage(target_code)
        except Exception:
            try:
                visum.SetCurrentLanguage(target_code)
            except Exception:
                pass
        logger.info("Visum-Sprache auf '%s' umgestellt.", target_code)
    except Exception as exc:
        logger.debug("Visum language switch: %s", exc)


def deactivate_visum_links_and_odpairs(visum) -> None:
    """Hide links and desire lines / OD matrix layers in Visum graphic parameters for a clean zone view."""
    try:
        gp = visum.Net.GraphicParameters

        # 1. Strecken-Layer ausblenden (DRAW = 0)
        try:
            gp.Links.SetAttValue("DRAW", 0)
            logger.info("Strecken-Layer in Visum ausgeblendet (DRAW = 0).")
        except Exception as exc:
            logger.debug("Fehler beim Ausblenden von Links: %s", exc)

        # 2. Wunschlinien / OD-Matrix-Layer (Verkehrszellen & Hauptverkehrszellen) ausblenden
        for od_attr in ["DesireLinesZones", "DesireLinesMainZones"]:
            if hasattr(gp, od_attr):
                try:
                    getattr(gp, od_attr).SetAttValue("DRAW", 0)
                    logger.info("%s-Layer in Visum ausgeblendet (DRAW = 0).", od_attr)
                except Exception as exc:
                    logger.debug("Fehler beim Ausblenden von %s: %s", od_attr, exc)

        logger.info("Strecken- und Wunschlinien-Layer in Visum erfolgreich ausgeblendet.")
    except Exception as exc:
        logger.warning("Hinweis beim Ausblenden von Strecken/Wunschlinien in Visum: %s", exc)


def open_in_visum(
    project_path: str | Path,
    gpa_filename: Optional[str] = None,
    zoom_study_area: bool = False,
    deactivate_links_and_odpairs: bool = False,
    language: str = "de",
) -> bool:
    """Launch PTV Visum with the project .ver file, optionally apply GPA file, run step 2, and keep Visum open."""
    project_path = Path(project_path)
    visum_dir = project_path / "processed" / "visum"
    if not visum_dir.exists():
        return False

    target_ver = find_visum_target_version(visum_dir)
    if not target_ver or not target_ver.exists():
        return open_path_in_explorer(visum_dir)

    is_en = str(language).lower().startswith("en")
    gpa_path = resolve_visum_gpa(visum_dir, gpa_filename) if gpa_filename else None

    study_zoom_gpa = None
    if zoom_study_area:
        zoom_name = "_Extent_StudyArea.gpa" if is_en else "_Ausschnitt_Untersuchungsraum.gpa"
        study_zoom_gpa = resolve_visum_gpa(visum_dir, zoom_name)

    try:
        visum = create_visum()

        visum.Graphic.ShowMaximized()
        visum.LoadVersion(str(target_ver.resolve()))
        logger.info("Visum version loaded via COM: %s", target_ver)

        # 1. Sprache synchronisieren
        ensure_visum_language(visum, language=language)

        # 2. Set Project Directories aus dem Skript der XML-Datei
        _set_visum_project_directories(visum, language=language)

        # 3. Apply Study Area Zoom GPA if requested
        if study_zoom_gpa and study_zoom_gpa.exists():
            try:
                visum.Net.GraphicParameters.Open(str(study_zoom_gpa.resolve()))
                logger.info("Zoom GPA applied: %s", study_zoom_gpa.name)
            except Exception as exc:
                logger.warning("Could not open zoom GPA: %s", exc)

        # 4. Apply Graphic Parameters GPA
        if gpa_path and gpa_path.exists():
            try:
                if gpa_path.suffix.lower() == ".gpax":
                    try:
                        visum.Net.GraphicParameters.OpenXml(str(gpa_path.resolve()))
                    except Exception:
                        visum.Net.GraphicParameters.Open(str(gpa_path.resolve()))
                else:
                    visum.Net.GraphicParameters.Open(str(gpa_path.resolve()))
                logger.info("GPA applied: %s", gpa_path.name)
            except Exception as exc:
                logger.warning("Could not open GPA %s: %s", gpa_path, exc)

        # 5. Strecken und OD-Pairs nach dem GPA-Laden deaktivieren (damit GPA den Filter nicht überschreibt)
        if deactivate_links_and_odpairs:
            deactivate_visum_links_and_odpairs(visum)

        visum.Graphic.Redraw()
        _ACTIVE_VISUM_INSTANCES.append(visum)
        return True

    except Exception as exc:
        logger.warning("Visum COM automation failed (%s). Opening .ver file directly.", exc)
        return open_path_in_explorer(target_ver)


class ResultCardWidget(_Q_WIDGET_BASE):
    """Card representing a result dataset with explanation and action buttons."""

    def __init__(
        self,
        title: str,
        description: str,
        layer_paths: list[str],
        folder_path: str,
        open_qgis_callback=None,
        open_visum_callback=None,
        show_folder: bool = True,
        parent: Optional[object] = None,
        preview_callback=None,
    ):
        super().__init__(parent)
        self.preview_callback = preview_callback
        self.title = title
        self.description = description
        self.layer_paths = layer_paths
        self.folder_path = folder_path
        self.open_qgis_callback = open_qgis_callback
        self.open_visum_callback = open_visum_callback
        self.show_folder = show_folder

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.group_box = QtWidgets.QGroupBox(self.title, self)
        card_layout = QtWidgets.QVBoxLayout(self.group_box)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)

        # Description text
        desc_label = QtWidgets.QLabel(self.description, self.group_box)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #333333; font-size: 11px;")
        card_layout.addWidget(desc_label)

        # Action Buttons row
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch()

        # Check availability
        exists = any(os.path.exists(p) for p in self.layer_paths) if self.layer_paths else os.path.exists(self.folder_path)

        # Folder button (if enabled)
        if self.show_folder and self.folder_path:
            self.btn_folder = QtWidgets.QPushButton(localizer.get_string("step6_btn_open_folder", default="Ordner öffnen"), self.group_box)
            self.btn_folder.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
            self.btn_folder.clicked.connect(self._on_open_folder)
            btn_row.addWidget(self.btn_folder)

        if self.preview_callback:
            self.btn_preview = QtWidgets.QPushButton(localizer.get_string("step6_btn_show_map"), self.group_box)
            self.btn_preview.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
            self.btn_preview.setEnabled(any(Path(path).is_file() for path in self.layer_paths))
            self.btn_preview.clicked.connect(lambda: self.preview_callback(self.layer_paths, self.title))
            btn_row.addWidget(self.btn_preview)

        # QGIS button
        if self.open_qgis_callback:
            self.btn_qgis = QtWidgets.QPushButton(localizer.get_string("step6_btn_open_qgis", default="In QGIS öffnen"), self.group_box)
            self.btn_qgis.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
            self.btn_qgis.setEnabled(exists)
            self.btn_qgis.clicked.connect(self._on_open_qgis)
            btn_row.addWidget(self.btn_qgis)

        # Visum button
        if self.open_visum_callback:
            self.btn_visum = QtWidgets.QPushButton(localizer.get_string("step6_btn_open_visum", default="In Visum öffnen"), self.group_box)
            self.btn_visum.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
            self.btn_visum.setEnabled(exists)
            self.btn_visum.clicked.connect(self._on_open_visum)
            btn_row.addWidget(self.btn_visum)

        card_layout.addLayout(btn_row)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.group_box)

    def _on_open_folder(self) -> None:
        open_path_in_explorer(self.folder_path)

    def _on_open_qgis(self) -> None:
        if self.open_qgis_callback:
            self.open_qgis_callback(self.layer_paths)

    def _on_open_visum(self) -> None:
        if self.open_visum_callback:
            if not self.open_visum_callback(self.folder_path):
                Dialogs.warning(
                    self,
                    localizer.get_string('step5_title_error', default='Fehler'),
                    localizer.get_string('step6_visum_open_failed', default=(
                        'Das Ergebnis konnte nicht in Visum geöffnet werden. '
                        'Bitte die Modelldatei und die Visum-Installation prüfen. '
                        'Weitere Details stehen im Protokoll.')),
                )


class Step6ResultsWidget(_Q_WIDGET_BASE):
    """Step 6 Widget providing an overview of all generated results and opening tools."""

    recalculate_requested = pyqtSignal() if pyqtSignal else None

    def __init__(self, localizer_obj, parent: Optional[object] = None, project_path: str = ""):
        super().__init__(parent)
        self.localizer = localizer_obj
        self.project_path = project_path
        from core.app.app_core.centrality_edits import project_session
        self.edit_session = project_session(parent, project_path)

        self._setup_ui()

    def set_project_path(self, path: str) -> None:
        self.results_map.clear()
        self.project_path = path
        from core.app.app_core.centrality_edits import project_session
        self.edit_session = project_session(self.parent(), path)
        self._load_ferry_wait()
        self.results_map.edit_session = self.edit_session
        self.refresh_results()
        self.results_map.show_planning_area(self.project_path)

    def _setup_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 10, 16, 10)
        main_layout.setSpacing(8)

        # Header
        self.header, _title_label, _step_label = create_step_header(
            self.localizer.get_string("step6_title", default="Schritt 6: Ergebnisse und Auswertung"),
            current_step=6,
            total_steps=7,
            localizer=self.localizer,
            parent=self,
        )
        main_layout.addWidget(self.header)

        ferry_box = QtWidgets.QGroupBox(self.localizer.get_string('ferry_parameters'), self)
        ferry_layout = QtWidgets.QHBoxLayout(ferry_box)
        self.ferry_label = QtWidgets.QLabel(self.localizer.get_string('ferry_wait_minutes'), ferry_box)
        ferry_layout.addWidget(self.ferry_label)
        self.ferry_wait_spin = QtWidgets.QDoubleSpinBox(ferry_box)
        self.ferry_wait_spin.setRange(0, 10080)
        self.ferry_wait_spin.setDecimals(1)
        self.ferry_wait_spin.setSuffix(' min')
        self._load_ferry_wait()
        ferry_layout.addWidget(self.ferry_wait_spin)
        self.ferry_save_button = QtWidgets.QPushButton(self.localizer.get_string('ferry_save'), ferry_box)
        self.ferry_save_button.clicked.connect(self._save_ferry_wait)
        ferry_layout.addWidget(self.ferry_save_button)
        ferry_layout.addStretch()
        main_layout.addWidget(ferry_box)
        self.ferry_box = ferry_box

        self.stale_banner = QtWidgets.QFrame(self)
        self.stale_banner.setStyleSheet('QFrame { background: #fef3c7; color: #713f12; border-radius: 5px; }')
        banner_layout = QtWidgets.QHBoxLayout(self.stale_banner)
        self.stale_text = QtWidgets.QLabel(self.localizer.get_string('centres_stale'), self.stale_banner)
        self.stale_text.setWordWrap(True)
        banner_layout.addWidget(self.stale_text, 1)
        self.recalculate_button = QtWidgets.QPushButton(self.localizer.get_string('centres_prepare_update'), self.stale_banner)
        self.recalculate_button.clicked.connect(self.recalculate_requested.emit)
        banner_layout.addWidget(self.recalculate_button)
        main_layout.addWidget(self.stale_banner)

        # Scrollable area for result cards and section 0 overview
        self.scroll_area = QtWidgets.QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))
        bind_header_scroll_separator(self.header, self.scroll_area)

        self.scroll_widget = QtWidgets.QWidget()
        self.scroll_layout = QtWidgets.QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 2, 8, 0)
        self.scroll_layout.setSpacing(10)

        self.splitter = QtWidgets.QSplitter(qt_enum(Qt, "Horizontal", "Orientation"), self)
        self.results_map = ResultsMapWidget(self.localizer, self.splitter, edit_session=self.edit_session)
        self.results_map.edits_changed.connect(self._refresh_stale_banner)
        self._refresh_stale_banner()
        self._build_cards()

        self.scroll_area.setWidget(self.scroll_widget)
        self.splitter.addWidget(self.scroll_area)
        self.splitter.addWidget(self.results_map)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([600, 600])
        main_layout.addWidget(self.splitter, 1)
        self.results_map.show_planning_area(self.project_path)

    def _load_ferry_wait(self):
        import json
        path = Path(self.project_path) / 'ferry_parameters.json'
        value = json.loads(path.read_text(encoding='utf-8'))['wait_minutes'] if path.is_file() else 20
        self.ferry_wait_spin.setValue(float(value))

    def _save_ferry_wait(self):
        if not self.project_path or self.edit_session.busy:
            return
        import json
        value = self.ferry_wait_spin.value()
        path = Path(self.project_path) / 'ferry_parameters.json'
        temporary = path.with_suffix('.json.tmp')
        try:
            temporary.write_text(json.dumps({'wait_minutes': value}, indent=2), encoding='utf-8')
            temporary.replace(path)
            self.edit_session.set_ferry_wait(value)
        except Exception as exc:
            Dialogs.error(self, self.localizer.get_string('centres_error'), str(exc))
            return
        self._refresh_stale_banner()

    def _on_open_project_dir(self) -> None:
        if self.project_path and os.path.exists(self.project_path):
            open_path_in_explorer(self.project_path)
        else:
            Dialogs.warning(self, self.localizer.get_string("error_project_dir_not_set_title"), self.localizer.get_string("step2_error_project_dir_first"))

    def _open_layer_group_in_qgis(self, layer_paths: list[str], title: str) -> None:
        open_in_qgis(layer_paths, project_path=self.project_path, title=title)

    def _on_open_visum_dir(self) -> None:
        base = Path(self.project_path) if self.project_path else Path("")
        visum_dir = base / "processed" / "visum"
        open_path_in_explorer(str(visum_dir) if visum_dir.exists() else str(base))

    def _all_result_layers(self) -> list[Path]:
        base = Path(self.project_path) if self.project_path else Path("")
        qgis_out = base / "processed" / "qgis_output"
        nationwide = qgis_out / 'model5_NationwideIntensity'
        return [
            qgis_out / "model6_ZoneAssembler" / "zone_centroids.gpkg",
            qgis_out / "model6_ZoneAssembler" / "zones.gpkg",
            nationwide / ('inner_urban_central_points_nationwide.gpkg' if (nationwide / 'inner_urban_central_points_nationwide.gpkg').exists() else 'nationwide_centrality_points.gpkg'),
            nationwide / 'central_place_points_nationwide.gpkg',
            qgis_out / "model5_UrbanCentrality" / "zone_pa_ia1_ia2_points.gpkg",
            qgis_out / "model5_UrbanCentrality" / "zone_pa_ia1_ia2.gpkg",
            qgis_out / "model2_ZoneClass" / "central_place_points.gpkg",
            qgis_out / "model2_ZoneClass" / "zone_adm2_typeno.gpkg",
            qgis_out / "model5_NationwideIntensity" / "sector_all_intensity.tif",
            qgis_out / "model5_UrbanCentrality" / "sector_all_intensity.tif",
            qgis_out / "model5_UrbanCentrality" / "sector_all_points.gpkg",
            qgis_out / "model1_DataPrep" / "adm3.gpkg",
            qgis_out / "model1_DataPrep" / "adm2.gpkg",
            qgis_out / "model1_DataPrep" / "adm1.gpkg",
            qgis_out / "model1_DataPrep" / "adm0.gpkg",
            qgis_out / "model1_DataPrep" / "pop_raster_corr.tif",
        ]

    def _on_open_all_in_qgis(self) -> None:
        open_in_qgis(self._all_result_layers(), project_path=self.project_path, title="all_results")

    def _show_layers_in_map(self, layer_paths, title) -> None:
        self.edit_session.last_preview = (list(layer_paths), title)
        project_file = create_qgis_project_for_layers(
            self.project_path, layer_paths, project_name="pando_embedded_preview"
        ) if self.project_path else None
        if project_file is None or project_file.suffix.lower() != ".qgs":
            self.results_map.clear()
            self.results_map.status.setText(self.localizer.get_string("step6_map_error"))
            return
        self.results_map.show_project(project_file, title)

    def _on_show_all_in_map(self) -> None:
        self._show_layers_in_map(self._all_result_layers(), self.localizer.get_string("step6_map_all_results"))

    def _build_cards(self) -> None:
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        base = Path(self.project_path) if self.project_path else Path("")
        qgis_out = base / "processed" / "qgis_output"
        visum_out = base / "processed" / "visum"
        lang = getattr(self.localizer, "current_language", "de")
        is_en = str(lang).lower().startswith("en")

        # Intro description label
        self.intro_label = QtWidgets.QLabel(
            self.localizer.get_string(
                "step6_desc",
                default="Übersicht ausgewählter Ergebnisdaten sowie kurze Erklärungen zu den Daten."
            ),
            self.scroll_widget,
        )
        self.intro_label.setFont(app_font(9))
        self.intro_label.setWordWrap(True)
        self.intro_label.setStyleSheet("color: #475569; padding: 2px 0px;")
        self.scroll_layout.addWidget(self.intro_label)

        # Action Buttons Row
        btn_container = QtWidgets.QWidget(self.scroll_widget)
        btn_layout = QtWidgets.QGridLayout(btn_container)
        btn_layout.setContentsMargins(0, 2, 0, 4)
        btn_layout.setSpacing(8)

        self.btn_open_project_dir = QtWidgets.QPushButton(
            self.localizer.get_string("step6_btn_open_project_dir", default="Projektordner öffnen"), btn_container
        )
        self.btn_open_project_dir.setFont(app_font(9))
        self.btn_open_project_dir.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        self.btn_open_project_dir.clicked.connect(self._on_open_project_dir)
        btn_layout.addWidget(self.btn_open_project_dir, 0, 0)

        self.btn_show_all_map = QtWidgets.QPushButton(
            self.localizer.get_string("step6_btn_show_all_map"), btn_container
        )
        self.btn_show_all_map.setFont(app_font(9))
        self.btn_show_all_map.clicked.connect(self._on_show_all_in_map)
        self.btn_show_all_map.setEnabled(any(path.is_file() for path in self._all_result_layers()))
        btn_layout.addWidget(self.btn_show_all_map, 1, 0)

        self.btn_open_all_qgis = QtWidgets.QPushButton(
            self.localizer.get_string("step6_btn_open_all_qgis", default="Alle Layer in QGIS öffnen"), btn_container
        )
        self.btn_open_all_qgis.setFont(app_font(9))
        self.btn_open_all_qgis.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        self.btn_open_all_qgis.clicked.connect(self._on_open_all_in_qgis)
        btn_layout.addWidget(self.btn_open_all_qgis, 1, 1)

        self.btn_open_visum_dir = QtWidgets.QPushButton(
            self.localizer.get_string("step6_btn_open_visum_dir", default="Visum-Projektordner öffnen"), btn_container
        )
        self.btn_open_visum_dir.setFont(app_font(9))
        self.btn_open_visum_dir.setCursor(qt_enum(Qt, "PointingHandCursor", "CursorShape"))
        self.btn_open_visum_dir.clicked.connect(self._on_open_visum_dir)
        btn_layout.addWidget(self.btn_open_visum_dir, 0, 1)

        self.scroll_layout.addWidget(btn_container)

        # =========================================================================
        # 1. Grundlagendaten
        # =========================================================================
        sec1_header = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step6_cat_base_data', default='1. Grundlagendaten')}</b>",
            self.scroll_widget,
        )
        sec1_header.setStyleSheet("font-size: 12px; color: #1a252f; margin-top: 10px;")
        self.scroll_layout.addWidget(sec1_header)

        # 1.1 Zensus-Einwohnerdaten
        m1_dir = qgis_out / "model1_DataPrep"
        pop_tif = m1_dir / "pop_raster_corr.tif"
        pop_layers = [str(p) for p in result_paths(self.project_path, "population") if p.exists()]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step6_layer_census_title", default="Zensus-Einwohnerdaten (Modell 1)"),
                description=self.localizer.get_string("step6_layer_census_desc", default="Zensus-Einwohnerdaten in einer Auflösung von 3 Bogensekunden (WGS 84, ca. 100 m) für den gesamten Modellraum, korrigiert durch OSM-Siedlungsflächen und optionale Referenzdaten."),
                layer_paths=pop_layers,
                folder_path=str(m1_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "zensusraster"),
                preview_callback=self._show_layers_in_map,
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # 1.2 Administrative Grenzen (ADM0-ADM3)
        adm_layers = [str(m1_dir / f"adm{i}.gpkg") for i in (3, 2, 1, 0) if (m1_dir / f"adm{i}.gpkg").exists()]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step6_layer_adm_title", default="Administrative Grenzen (ADM0–ADM3)"),
                description=self.localizer.get_string("step6_layer_adm_desc", default="Verwaltungsgrenzen der Ebenen ADM0 bis ADM3 inklusive aggregierter Einwohnerzahlen und hierarchischer Nummerierungsstruktur."),
                layer_paths=adm_layers,
                folder_path=str(m1_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "administrative_grenzen"),
                preview_callback=self._show_layers_in_map,
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # =========================================================================
        # 2. Zentrale Orte und innergemeindliche Zentralitäten
        # =========================================================================
        sec2_header = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step6_cat_centrality', default='2. Zentrale Orte und innergemeindliche Zentralitäten')}</b>",
            self.scroll_widget,
        )
        sec2_header.setStyleSheet("font-size: 12px; color: #1a252f; margin-top: 10px;")
        self.scroll_layout.addWidget(sec2_header)

        # 2.1 Zentrale Orte (Modell 2 / ADM2)
        m2_dir = qgis_out / "model2_ZoneClass"
        m2_zone = m2_dir / "zone_adm2_typeno.gpkg"
        m2_points = m2_dir / "central_place_points.gpkg"
        m2_layers = [str(p) for p in result_paths(self.project_path, "central_places")]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step6_layer_central_places_title", default="Zentrale Orte (Modell 2 / ADM2)"),
                description=self.localizer.get_string("step6_layer_central_places_desc", default="Zentrale Orte des Modellraums, gespeichert als Gemeindeflächen und Punktobjekte basierend auf den Bevölkerungsschwellenwerten."),
                layer_paths=m2_layers,
                folder_path=str(m2_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "zentrale_orte"),
                preview_callback=self._show_layers_in_map,
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # 2.2 Innergemeindliche Zentralitäten und Intensitätsschätzung (Modell 5)
        m5_dir = qgis_out / "model5_UrbanCentrality"
        m5_intensity = m5_dir / "sector_all_intensity.tif"
        m5_poi = m5_dir / "sector_all_points.gpkg"
        m5_zone = m5_dir / "zone_pa_ia1_ia2.gpkg"
        m5_points = m5_dir / "zone_pa_ia1_ia2_points.gpkg"
        m5_layers = [str(p) for p in result_paths(self.project_path, "urban")]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step6_layer_urban_centrality_title", default="Intensitätsschätzung und innergemeindliche Zentralitäten im Untersuchungsraum (Modell 5)"),
                description=self.localizer.get_string("step6_layer_urban_centrality_desc", default="Flächenhafte Intensitätsschätzung auf Basis von OSM-Daten nach Funktionsbereichen (Heatmaps), Sektor-POI-Punkte sowie daraus abgeleitete innergemeindliche Zentralitäten und Planungs- bzw. Einflussräume (PA, IA1, IA2) unter Nutzung der Gitter zur räumlichen Verteilung."),
                layer_paths=m5_layers,
                folder_path=str(m5_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "urban_centrality"),
                preview_callback=self._show_layers_in_map,
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # 2.3 Landesweite Intensitätsschätzung und Zentralitäten (Modell 5 Landesweit)
        m5_nw_dir = qgis_out / "model5_NationwideIntensity"
        m5_nw_intensity = m5_nw_dir / "sector_all_intensity.tif"
        m5_nw_poi = m5_nw_dir / "sector_all_points.gpkg"
        m5_nw_inner_points = (
            m5_nw_dir / "inner_urban_central_points_nationwide.gpkg"
            if (m5_nw_dir / "inner_urban_central_points_nationwide.gpkg").exists()
            else m5_nw_dir / "nationwide_centrality_points.gpkg"
        )
        m5_nw_central_places = m5_nw_dir / "central_place_points_nationwide.gpkg"
        m5_nw_layers = [
            str(p)
            for p in (m5_nw_intensity, m5_nw_poi, m5_nw_central_places, m5_nw_inner_points)
            if p.exists()
        ]
        if m5_nw_dir.exists() or len(m5_nw_layers) > 0:
            self.scroll_layout.addWidget(
                ResultCardWidget(
                    title=self.localizer.get_string("step6_layer_nationwide_intensity_title", default="Landesweite Intensitätsschätzung und innergemeindliche Zentralitäten (Modell 5 Landesweit)"),
                    description=self.localizer.get_string("step6_layer_nationwide_intensity_desc", default="Landesweite Intensitätsschätzung auf Basis der OSM-Gesamtdaten (Gesamt-Heatmap), Sektor-POI-Punkte und daraus abgeleitete innergemeindliche Zentralitäten unter Nutzung der existierenden zentralen Orte (ADM2) als Seeds sowie räumlicher Abstände."),
                    layer_paths=m5_nw_layers,
                    folder_path=str(m5_nw_dir),
                    open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "landesweite_intensitaet"),
                    preview_callback=self._show_layers_in_map,
                    show_folder=True,
                    parent=self.scroll_widget,
                )
            )

        # 2.4 Finale Verkehrszellen und zentrale Orte (Modell 6)
        m6_dir = qgis_out / "model6_ZoneAssembler"
        m6_zones = m6_dir / "zones.gpkg"
        m6_centroids = m6_dir / "zone_centroids.gpkg"
        m6_layers = [str(p) for p in result_paths(self.project_path, "zones")]
        cfl_gpa_name = "EN_Central_Places_Direct_Lines_CFL.gpax" if is_en else "DE_Zentrale_Orte_Luftlinienverbindungen_VFS.gpax"
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step6_layer_zones_title", default="Finale Verkehrszellen und zentrale Orte (Modell 6)"),
                description=self.localizer.get_string("step6_layer_zones_desc", default="Zusammengeführte finale Verkehrszellenstruktur inklusive zugewiesener zentraler Orte, Zensus-Einwohnerzahlen, Strukturdaten und Schwerpunkte."),
                layer_paths=m6_layers,
                folder_path=str(m6_dir),
                open_qgis_callback=lambda layers: self._open_layer_group_in_qgis(layers, "verkehrszellen"),
                preview_callback=self._show_layers_in_map,
                open_visum_callback=lambda _: open_in_visum(self.project_path, gpa_filename=cfl_gpa_name, deactivate_links_and_odpairs=True, language=lang),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # =========================================================================
        # 3. Netzkategorisierung für den Kfz-Verkehr
        # =========================================================================
        sec3_header = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step6_cat_network', default='3. Netzkategorisierung für den Kfz-Verkehr')}</b>",
            self.scroll_widget,
        )
        sec3_header.setStyleSheet("font-size: 12px; color: #1a252f; margin-top: 10px;")
        self.scroll_layout.addWidget(sec3_header)

        # 3.1 Aktuelles Straßennetz
        links_gpa_name = "EN_Links.gpax" if is_en else "DE_Strecken.gpax"
        visum_layers = [str(p) for p in (list(visum_out.rglob("*.ver")) + list(visum_out.rglob("*.net")))]
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step6_layer_links_osm_title", default="Aktuelles Straßennetz"),
                description=self.localizer.get_string("step6_layer_links_osm_desc", default="Kategorisierte Darstellung des Straßennetzes nach OSM-Streckentypen im aktuellen Zustand in PTV Visum."),
                layer_paths=visum_layers,
                folder_path=str(visum_out),
                open_visum_callback=lambda _: open_in_visum(self.project_path, gpa_filename=links_gpa_name, language=lang),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # 3.2 RIN-Netzkategorisierung und Luftlinienverbindungen
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step6_layer_network_rin_title", default="RIN-Netzkategorisierung und Luftlinienverbindungen"),
                description=self.localizer.get_string("step6_layer_network_rin_desc", default="Darstellung der Luftlinienverbindungen und der automatisierten Netzkategorisierung nach RIN in PTV Visum."),
                layer_paths=visum_layers,
                folder_path=str(visum_out),
                open_visum_callback=lambda _: open_in_visum(self.project_path, gpa_filename=cfl_gpa_name, language=lang),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        # =========================================================================
        # 4. Erreichbarkeitsanalysen
        # =========================================================================
        sec4_header = QtWidgets.QLabel(
            f"<b>{self.localizer.get_string('step6_cat_accessibility', default='4. Erreichbarkeitsanalysen')}</b>",
            self.scroll_widget,
        )
        sec4_header.setStyleSheet("font-size: 12px; color: #1a252f; margin-top: 10px;")
        self.scroll_layout.addWidget(sec4_header)

        # 4.1 Erreichbarkeitsanalyse
        acc_gpa_name = "EN_Accessibility.gpax" if is_en else "DE_Erreichbarkeit.gpax"
        self.scroll_layout.addWidget(
            ResultCardWidget(
                title=self.localizer.get_string("step6_layer_accessibility_title", default="Erreichbarkeitsanalyse"),
                description=self.localizer.get_string("step6_layer_accessibility_desc", default="Darstellung der Reisezeit zum nächsten untersuchten Ziel auf Basis der Verkehrszellen für Pkw, ÖV, Fuß und Rad in PTV Visum."),
                layer_paths=visum_layers,
                folder_path=str(visum_out),
                open_visum_callback=lambda _: open_in_visum(self.project_path, gpa_filename=acc_gpa_name, zoom_study_area=True, language=lang),
                show_folder=True,
                parent=self.scroll_widget,
            )
        )

        self.scroll_layout.addStretch()

    def release_resources(self) -> None:
        """Release map providers before navigation can reopen project outputs."""
        self.results_map.clear()

    def refresh_results(self) -> None:
        """Re-render cards with updated paths."""
        self._build_cards()
        self._refresh_stale_banner()

    def _refresh_stale_banner(self):
        self.stale_banner.setVisible(bool(self.edit_session.pending()))

    def retranslate_ui(self) -> None:
        self.ferry_box.setTitle(self.localizer.get_string('ferry_parameters'))
        self.ferry_label.setText(self.localizer.get_string('ferry_wait_minutes'))
        self.ferry_save_button.setText(self.localizer.get_string('ferry_save'))
        self._build_cards()
        self.stale_text.setText(self.localizer.get_string('centres_stale'))
        self.recalculate_button.setText(self.localizer.get_string('centres_prepare_update'))
        self.results_map.retranslate_ui()
