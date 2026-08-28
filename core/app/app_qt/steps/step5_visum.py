"""Qt page for Step 5: PTV Visum Import & Post-Processing."""

from __future__ import annotations

import os
import sys
import logging
from typing import Optional
from pathlib import Path

from core.locales import localizer
from core.app.app_qt.qt_base import (
    AnimatedProgressBar,
    Dialogs,
    Qt,
    QtCore,
    QtWidgets,
    WindowsTaskbarProgress,
    app_font,
    create_step_header,
    escape_mnemonic,
    pyqtSignal,
    qfont_bold,
    qt_enum,
)

_Q_WIDGET_BASE = QtWidgets.QWidget if QtWidgets is not None else object
_Q_OBJECT_BASE = QtCore.QObject if QtCore is not None else object


class VisumWorker(_Q_OBJECT_BASE):
    """Background worker for Visum COM automation pipeline."""

    log_ready = pyqtSignal(str) if pyqtSignal is not None else None
    phase_started = pyqtSignal(str) if pyqtSignal is not None else None
    substep_started = pyqtSignal(str) if pyqtSignal is not None else None
    phase_progress_value = pyqtSignal(int) if pyqtSignal is not None else None
    overall_progress_value = pyqtSignal(int) if pyqtSignal is not None else None
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

        PROGRESS_MILESTONES = [
            # -------------------------------------------------------------------------
            # Schritt 1/7: Strecken und Verkehrszellen importieren (~368s / 20.2% Gesamt)
            # -------------------------------------------------------------------------
            ("Schritt 1/7: Strecken und Verkehrszellen", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Verkehrszellen einlesen & Projektion ermitteln", 0, 0),
            ("Lese Bezirke:", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Verkehrszellen & Hauptverkehrszellen einlesen", 0, 0),
            ("Netzdatei der Bezirke erzeugt:", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Verkehrszellennetzdatei erzeugen", 1, 0),
            ("Starte frische Visum COM-Instanz...", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Visum COM-Instanz initialisieren", 2, 1),
            ("Schritt 1: Importiere OSM-Streckennetz", "Schritt 1/7: Strecken und Verkehrszellen importieren", "OSM-Streckennetz importieren (OSM-Importer)", 3, 1),
            ("OSM-Streckennetz erfolgreich in Visum importiert", "Schritt 1/7: Strecken und Verkehrszellen importieren", "UTM-Projektion & Flächenzuweisung", 92, 18),
            ("Schritt 2: Schalte auf UTM-Zielprojektion um", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Koordinaten auf UTM transformieren", 93, 19),
            ("Lade Bezirke additiv ins UTM-Netz", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Verkehrszellen additiv einlesen", 95, 19),
            ("Polygon-Flächengeometrien erfolgreich den Bezirken zugewiesen", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Flächengeometrien zuweisen", 97, 19),
            ("Polygon-Flächengeometrien erfolgreich den Hauptbezirken zugewiesen", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Hauptverkehrszellengeometrien zuweisen", 98, 20),
            ("Schritt 1 abgeschlossen:", "Schritt 1/7: Strecken und Verkehrszellen importieren", "Schritt 1 abgeschlossen", 100, 20),

            # -------------------------------------------------------------------------
            # Schritt 2/7: Anbindungsknoten ableiten (~708s / 38.9% Gesamt)
            # -------------------------------------------------------------------------
            ("Schritt 2/7: Anbindungsknoten werden abgeleitet", "Schritt 2/7: Anbindungsknoten ableiten", "Verfahrensdatei laden", 0, 20),
            ("1/6 Preprocessing: Einbahnstraßen", "Schritt 2/7: Anbindungsknoten ableiten", "Einbahnstraßen & Fährverbindungen (1/6)", 0, 20),
            ("Einbahnstraßen-Rückrichtungen", "Schritt 2/7: Anbindungsknoten ableiten", "Fährverbindungen vorbereiten (1/6)", 3, 21),
            ("2/6 Netz-Analyse: Rampen", "Schritt 2/7: Anbindungsknoten ableiten", "Netzanalyse: Rampen & Knotentypen (2/6)", 5, 22),
            ("Analysiere Rampen- und Knotenverbindungen", "Schritt 2/7: Anbindungsknoten ableiten", "Rampenverbindungen analysieren (2/6)", 7, 23),
            ("Identifiziere U-Turns", "Schritt 2/7: Anbindungsknoten ableiten", "U-Turns identifizieren", 43, 37),
            ("3/6 Knotentypen der Kreuzungen bestimmen", "Schritt 2/7: Anbindungsknoten ableiten", "Knotentypen der Kreuzungen (3/6)", 46, 38),
            ("4/6 Berechne Erreichbarkeit via Isochronen", "Schritt 2/7: Anbindungsknoten ableiten", "Erreichbarkeit via Isochronen (4/6)", 49, 39),
            ("5/6 Clustere Zugangsknoten", "Schritt 2/7: Anbindungsknoten ableiten", "Zugangsknoten clustern (5/6)", 55, 42),
            ("Clustere Knotentyp '1-1'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 1-1 (5/6)", 56, 42),
            ("Clustere Knotentyp '1-2'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 1-2 (5/6)", 57, 42),
            ("Clustere Knotentyp '1-3'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 1-3 (5/6)", 62, 44),
            ("Clustere Knotentyp '2-2'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 2-2 (5/6)", 64, 45),
            ("Clustere Knotentyp '2-3'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 2-3 (5/6)", 66, 46),
            ("Clustere Knotentyp '2-4'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 2-4 (5/6)", 69, 47),
            ("Clustere Knotentyp '3-3'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 3-3 (5/6)", 70, 47),
            ("Clustere Knotentyp '3-4'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 3-4 (5/6)", 72, 48),
            ("Clustere Knotentyp '4-4'", "Schritt 2/7: Anbindungsknoten ableiten", "Clustere Knotentyp 4-4 (5/6)", 73, 48),
            ("6/6 Erzeuge Hilfsnetz und führe Ausdünnung aus", "Schritt 2/7: Anbindungsknoten ableiten", "Hilfsnetz & Zugangsknoten erzeugen (6/6)", 74, 49),
            ("Starte Visum-Verfahrensablauf zur Netz-Umlegung", "Schritt 2/7: Anbindungsknoten ableiten", "Testumlegung zur Ausdünnung", 80, 51),
            ("Visum-Verfahrensablauf erfolgreich ausgeführt", "Schritt 2/7: Anbindungsknoten ableiten", "Ausdünnung abgeschlossen", 97, 58),
            ("Räume temporäre Objekte auf", "Schritt 2/7: Anbindungsknoten ableiten", "Temporäre Objekte aufräumen", 98, 59),
            ("Schritt 2 abgeschlossen:", "Schritt 2/7: Anbindungsknoten ableiten", "Schritt 2 abgeschlossen", 100, 59),

            # -------------------------------------------------------------------------
            # Schritt 3/7: Streckentypen umstrukturieren (~226s / 12.4% Gesamt)
            # -------------------------------------------------------------------------
            ("Schritt 3/7: Streckentypen werden umstrukturiert", "Schritt 3/7: Streckentypen umstrukturieren", "Master-Streckentypen initialisieren", 0, 59),
            ("Phase 0: Lösche überflüssige Hilfsstrecken", "Schritt 3/7: Streckentypen umstrukturieren", "Hilfsstrecken bereinigen (Phase 0)", 0, 59),
            ("Phase 1: Lade Ziel-Streckentypen", "Schritt 3/7: Streckentypen umstrukturieren", "Ziel-Streckentypen laden (Phase 1)", 3, 60),
            ("Phase 2: Führe Zuweisung über native Visum-Attribute", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Hauptnetz (Phase 2)", 5, 60),
            ("Kategorie 'Motorway       ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Motorway", 21, 61),
            ("Kategorie 'Motorway_link  ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Motorway Links", 24, 62),
            ("Kategorie 'Primary        ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Primary", 42, 64),
            ("Kategorie 'Primary_link   ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Primary Links", 45, 65),
            ("Kategorie 'Trunk          ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Trunk", 59, 66),
            ("Kategorie 'Trunk_link     ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Trunk Links", 62, 67),
            ("Kategorie 'Secondary      ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Secondary", 74, 68),
            ("Kategorie 'Secondary_link ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Secondary Links", 77, 69),
            ("Kategorie 'Tertiary       ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Tertiary", 83, 70),
            ("Kategorie 'Unclassified   ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Unclassified", 88, 70),
            ("Phase 3: Führe 1:1 Zuweisung für einfache Streckentypen", "Schritt 3/7: Streckentypen umstrukturieren", "Nebennetz: Residential, Blocked, Ferry (Phase 3)", 88, 70),
            ("Kategorie 'Residential    ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Residential", 89, 70),
            ("Kategorie 'Blocked Oneway ':", "Schritt 3/7: Streckentypen umstrukturieren", "Streckenzuweisung Blocked Oneway", 95, 71),
            ("Phase 4: Finale Bereinigung", "Schritt 3/7: Streckentypen umstrukturieren", "Qualitätsprüfung & Speichern (Phase 4)", 97, 71),
            ("Schritt 3 abgeschlossen:", "Schritt 3/7: Streckentypen umstrukturieren", "Schritt 3 abgeschlossen", 100, 72),

            # -------------------------------------------------------------------------
            # Schritt 4/7: Anbindungen generieren (~265s / 14.5% Gesamt)
            # -------------------------------------------------------------------------
            ("Schritt 4/7: Anbindungen werden generiert", "Schritt 4/7: Anbindungen generieren", "Distanzberechnung der Bezirke", 0, 72),
            ("Starte Distanzberechnung", "Schritt 4/7: Anbindungen generieren", "Distanzberechnung zu Zugangsknoten", 0, 72),
            ("Klassifiziere Bezirke in 'Near' und 'Far'", "Schritt 4/7: Anbindungen generieren", "Nah- und Fernbereich klassifizieren", 11, 74),
            ("Erzeuge Anbindungen für nahe Bezirke", "Schritt 4/7: Anbindungen generieren", "Anbindungen Nahbereich (Typ 0)", 14, 74),
            ("Anbindungen_Typ0.net", "Schritt 4/7: Anbindungen generieren", "Anbindungen Typ 0 gespeichert", 20, 75),
            ("Anbindungen_Typ1.net", "Schritt 4/7: Anbindungen generieren", "Anbindungen Typ 1 gespeichert", 28, 76),
            ("Anbindungen_Typ2.net", "Schritt 4/7: Anbindungen generieren", "Anbindungen Typ 2 gespeichert", 34, 77),
            ("Anbindungen_Typ3.net", "Schritt 4/7: Anbindungen generieren", "Anbindungen Typ 3 gespeichert", 42, 78),
            ("Anbindungen_Typ4.net", "Schritt 4/7: Anbindungen generieren", "Anbindungen Typ 4 gespeichert", 50, 79),
            ("Anbindungen_Typ5.net", "Schritt 4/7: Anbindungen generieren", "Anbindungen Typ 5 gespeichert", 59, 80),
            ("Erzeuge Anbindungen für entfernte Bezirke", "Schritt 4/7: Anbindungen generieren", "Anbindungen Fernbereich (Typ 9)", 60, 81),
            ("Anbindungen_Typ9.net", "Schritt 4/7: Anbindungen generieren", "Anbindungen Typ 9 gespeichert", 73, 82),
            ("Lade alle generierten Anbildungsdateien ins Modell", "Schritt 4/7: Anbindungen generieren", "Anbindungsdateien einlesen", 74, 83),
            ("Finale Prüfung: Full 1-to-1 Assignment", "Schritt 4/7: Anbindungen generieren", "Vollständigkeitsprüfung aller Bezirke", 92, 85),
            ("Finale Zuordnung erfolgreich", "Schritt 4/7: Anbindungen generieren", "Zuordnung aller Bezirke abgeschlossen", 96, 85),
            ("Init.xml erfolgreich ausgeführt", "Schritt 4/7: Anbindungen generieren", "Bereinigung temporärer Objekte", 98, 86),
            ("Schritt 4 abgeschlossen:", "Schritt 4/7: Anbindungen generieren", "Schritt 4 abgeschlossen", 100, 86),

            # -------------------------------------------------------------------------
            # Schritt 5/7: Luftlinienmatrizen berechnen (~46s / 2.5% Gesamt)
            # -------------------------------------------------------------------------
            ("Schritt 5/7: Luftlinienmatrizen werden berechnet", "Schritt 5/7: Luftlinienmatrizen berechnen", "Delaunay-Triangulation (Durchlauf 1: n = 1)", 0, 86),
            ("Starting the export of 5 matrices", "Schritt 5/7: Luftlinienmatrizen berechnen", "Matrizen exportieren (n = 1)", 2, 86),
            ("RIN_VFS_0_n=1: was read into Visum", "Schritt 5/7: Luftlinienmatrizen berechnen", "Matrizen in Visum einlesen (n = 1)", 7, 86),
            ("RIN_VFS_2_n=1: was read into Visum", "Schritt 5/7: Luftlinienmatrizen berechnen", "Matrizen in Visum einlesen (n = 1)", 13, 87),
            ("Berechne Luftliniennetz (Durchlauf 2: n = 2 und n = 5)", "Schritt 5/7: Luftlinienmatrizen berechnen", "Nachbarschaftsgrade (Durchlauf 2: n = 2 & 5)", 22, 87),
            ("VFS_0: the neighborhood degree must be calculated", "Schritt 5/7: Luftlinienmatrizen berechnen", "Delaunay-Nachbarschaften VFS_0", 24, 87),
            ("VFS_3: the neighborhood degree must be calculated", "Schritt 5/7: Luftlinienmatrizen berechnen", "Delaunay-Nachbarschaften VFS_3", 30, 87),
            ("VFS_4: the neighborhood degree must be calculated", "Schritt 5/7: Luftlinienmatrizen berechnen", "Delaunay-Nachbarschaften VFS_4", 50, 88),
            ("Matrix RIN_VFS_1_n=2 saved", "Schritt 5/7: Luftlinienmatrizen berechnen", "Matrizen exportieren (n = 2 & 5)", 72, 88),
            ("Matrix RIN_VFS_3_n=5 saved", "Schritt 5/7: Luftlinienmatrizen berechnen", "Matrizen exportieren (n = 2 & 5)", 83, 89),
            ("Matrix RIN_VFS_4_n=5 saved", "Schritt 5/7: Luftlinienmatrizen berechnen", "Matrizen exportieren (n = 2 & 5)", 87, 89),
            ("Folgende RIN-Luftlinienmatrizen wurden im Modell angelegt", "Schritt 5/7: Luftlinienmatrizen berechnen", "Matrizen-Übersicht prüfen", 87, 89),
            ("Schritt 5 abgeschlossen:", "Schritt 5/7: Luftlinienmatrizen berechnen", "Schritt 5 abgeschlossen", 100, 89),

            # -------------------------------------------------------------------------
            # Schritt 6/7: Verfahrensablauf ausführen (~181s / 9.9% Gesamt)
            # -------------------------------------------------------------------------
            ("Schritt 6/7: Verfahrensablauf wird ausgeführt", "Schritt 6/7: Verfahrensablauf ausführen", "Nachfragesegmente & Modi einrichten", 0, 89),
            ("106 link types updated with 'PUTCAR'", "Schritt 6/7: Verfahrensablauf ausführen", "PUTCAR auf Streckentypen belegt", 38, 93),
            ("Demand segment 'W'", "Schritt 6/7: Verfahrensablauf ausführen", "Nachfragesegmente anlegen", 39, 93),
            ("Linked 'CFL", "Schritt 6/7: Verfahrensablauf ausführen", "Nachfragematrizen verknüpfen", 40, 93),
            ("[Operation] Set DSeg", "Schritt 6/7: Verfahrensablauf ausführen", "Verfahrensablauf-Operationen belegen", 41, 93),
            ("Führe Verfahrensablauf aus...", "Schritt 6/7: Verfahrensablauf ausführen", "Visum-Verfahrensablauf berechnen (Umlegungen)", 45, 93),
            ("Speichere finale Modellversion:", "Schritt 6/7: Verfahrensablauf ausführen", "Modellversion speichern", 96, 98),
            ("Schritt 6 abgeschlossen:", "Schritt 6/7: Verfahrensablauf ausführen", "Schritt 6 abgeschlossen", 100, 99),

            # -------------------------------------------------------------------------
            # Schritt 7/7: Grafikparameter anwenden (~27s / 1.5% Gesamt)
            # -------------------------------------------------------------------------
            ("Schritt 7/7: Grafikparameter werden angewendet", "Schritt 7/7: Grafikparameter anwenden", "Ausschnitts-Grafikparameter anwenden", 0, 99),
            ("Ausschnitts-Grafikparameter (.gpa Land)", "Schritt 7/7: Grafikparameter anwenden", "Ausschnitt Land speichern", 4, 99),
            ("Ausschnitts-Grafikparameter (.gpa Untersuchungsraum)", "Schritt 7/7: Grafikparameter anwenden", "Ausschnitt Untersuchungsraum speichern", 7, 99),
            ("Grafikparameter gespeichert: Erreichbarkeit", "Schritt 7/7: Grafikparameter anwenden", "Grafikparameter Erreichbarkeit speichern", 15, 99),
            ("Grafikparameter gespeichert: Strecken", "Schritt 7/7: Grafikparameter anwenden", "Grafikparameter Strecken speichern", 26, 99),
            ("Grafikparameter gespeichert: Zentrale_Orte", "Schritt 7/7: Grafikparameter anwenden", "Grafikparameter Luftlinien speichern", 44, 99),
            ("Grafikparameter gespeichert: Zugangsknoten", "Schritt 7/7: Grafikparameter anwenden", "Grafikparameter Zugangsknoten speichern", 55, 100),
            ("Wende Filter-Datei an:", "Schritt 7/7: Grafikparameter anwenden", "Knoten-Filter im Netz aktivieren", 59, 100),
            ("Filter 'ZK_TYP-5.fil' geladen", "Schritt 7/7: Grafikparameter anwenden", "Netz-Filter aktiviert", 67, 100),
            ("Schritt 7 abgeschlossen:", "Schritt 7/7: Grafikparameter anwenden", "Schritt 7 abgeschlossen", 100, 100),
            ("Visum-Verarbeitungskette wurde erfolgreich ausgeführt", "Visum-Verarbeitung vollständig abgeschlossen", "Abgeschlossen", 100, 100),
        ]

        def log_adapter(msg: str) -> None:
            if self.log_ready:
                from core.app.app_core.logging import format_pando_log, should_ignore_log_message
                if not should_ignore_log_message(msg):
                    self.log_ready.emit(format_pando_log(msg))

            for trigger, main_step_title, substep_title, phase_pct, overall_pct in PROGRESS_MILESTONES:
                if trigger in msg:
                    if main_step_title and self.phase_started:
                        self.phase_started.emit(main_step_title)
                    if substep_title and self.substep_started:
                        self.substep_started.emit(substep_title)
                    if phase_pct is not None and self.phase_progress_value:
                        self.phase_progress_value.emit(phase_pct)
                    if overall_pct is not None and self.overall_progress_value:
                        self.overall_progress_value.emit(overall_pct)
                    break

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
    running_state_changed = pyqtSignal(bool) if pyqtSignal is not None else None

    def is_running(self) -> bool:
        return bool(self._thread is not None and getattr(self._thread, "isRunning", lambda: False)())

    def cancel_processing(self) -> None:
        self.stop_visum_pipeline()

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

        # Intro description label (clean text without surrounding box)
        self.desc_label = QtWidgets.QLabel(
            self.localizer.get_string(
                "step5_desc",
                default="Automatisiertes Einlesen des Straßennetzes und der Verkehrszellen in PTV Visum sowie anschließende Anbindungserzeugung, Netzkategorisierung und Erreichbarkeitsanalysen."
            ),
            self
        )
        self.desc_label.setFont(app_font(9))
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color: #475569; padding: 2px 2px;")
        main_layout.addWidget(self.desc_label)

        # Unified Control & Execution Box (consistent with Step 4 styling)
        self.execution_group = QtWidgets.QGroupBox(
            escape_mnemonic(self.localizer.get_string("step5_group_execution", default="Visum-Steuerung und Ausführung")),
            self
        )
        execution_layout = QtWidgets.QGridLayout(self.execution_group)
        execution_layout.setSpacing(8)
        execution_layout.setColumnMinimumWidth(0, 140)
        execution_layout.setColumnStretch(0, 0)
        execution_layout.setColumnStretch(1, 1)

        # Row 0: Start Step Dropdown
        self.step_label = QtWidgets.QLabel(self.localizer.get_string("step5_label_start_from_step", default="Starten ab Schritt:"), self.execution_group)
        self.step_label.setFont(app_font(9, qfont_bold()))

        self.step_combo = QtWidgets.QComboBox(self.execution_group)
        self.step_combo.setMaxVisibleItems(8)
        list_view = QtWidgets.QListView(self.step_combo)
        if QtWidgets is not None:
            list_view.setFrameShape(qt_enum(QtWidgets.QFrame, "NoFrame", "Shape"))
        self.step_combo.setView(list_view)
        self._populate_combo_items()

        execution_layout.addWidget(self.step_label, 0, 0)
        execution_layout.addWidget(self.step_combo, 0, 1, 1, 2)

        # Row 1: Current Step Label + Elapsed Time
        lbl_step = QtWidgets.QLabel(self.localizer.get_string("step4_label_current_step", default="Aktueller Schritt:"), self.execution_group)
        lbl_step.setFont(app_font(9))

        self.phase_label = QtWidgets.QLabel(self.localizer.get_string("step4_label_ready", default="Bereit zum Starten"), self.execution_group)
        self.phase_label.setFont(app_font(9))
        self.elapsed_label = QtWidgets.QLabel("00:00", self.execution_group)
        self.elapsed_label.setFont(app_font(9))

        step_row_layout = QtWidgets.QHBoxLayout()
        step_row_layout.addWidget(self.phase_label, 1)
        step_row_layout.addWidget(self.elapsed_label)
        execution_layout.addWidget(lbl_step, 1, 0)
        execution_layout.addLayout(step_row_layout, 1, 1, 1, 2)

        # Row 2: Step Progress
        lbl_phase_prog = QtWidgets.QLabel(self.localizer.get_string("step4_label_step_progress", default="Schritt-Fortschritt:"), self.execution_group)
        lbl_phase_prog.setFont(app_font(9))
        self.phase_progress = AnimatedProgressBar(self.execution_group)
        self.phase_progress.setRange(0, 100)
        self.phase_progress.setValue(0)
        self.phase_progress.setFormat("%p%")
        execution_layout.addWidget(lbl_phase_prog, 2, 0)
        execution_layout.addWidget(self.phase_progress, 2, 1, 1, 2)

        # Row 3: Overall Progress
        lbl_overall_prog = QtWidgets.QLabel(self.localizer.get_string("step4_label_overall_progress", default="Gesamtfortschritt:"), self.execution_group)
        lbl_overall_prog.setFont(app_font(9))
        self.overall_progress = AnimatedProgressBar(self.execution_group, sync_taskbar=True)
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        self.overall_progress.setFormat("%p%")
        execution_layout.addWidget(lbl_overall_prog, 3, 0)
        execution_layout.addWidget(self.overall_progress, 3, 1, 1, 2)

        # Row 4: Action Buttons (Stopp & Start)
        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 4, 0, 0)
        button_row.setSpacing(8)

        self.stop_button = QtWidgets.QPushButton(self.localizer.get_string("step5_button_stop", default="Stopp"), self.execution_group)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_visum_pipeline)

        self.start_button = QtWidgets.QPushButton(self.localizer.get_string("step5_button_start_visum", default="Visum-Verarbeitung starten"), self.execution_group)
        self.start_button.clicked.connect(self.start_visum_pipeline)

        button_row.addStretch()
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.start_button)
        execution_layout.addLayout(button_row, 4, 1, 1, 2)

        main_layout.addWidget(self.execution_group)

        # Log Display Window
        self.log_group = QtWidgets.QGroupBox(escape_mnemonic(self.localizer.get_string("step5_group_log", default="Verarbeitungs-Protokoll (Log)")), self)
        log_layout = QtWidgets.QVBoxLayout(self.log_group)
        log_layout.setContentsMargins(10, 10, 10, 10)

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
            self.localizer.get_string("step5_combo_step1", default="Schritt 1: Komplett (Strecken und Verkehrszellen importieren)"),
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
        self.desc_label.setText(self.localizer.get_string("step5_desc"))
        self.step_label.setText(self.localizer.get_string("step5_label_start_from_step", default="Starten ab Schritt:"))
        self._populate_combo_items()
        self.execution_group.setTitle(escape_mnemonic(self.localizer.get_string("step5_group_execution", default="Visum-Steuerung und Ausführung")))
        self.stop_button.setText(self.localizer.get_string("step5_button_stop", default="Stopp"))
        self.start_button.setText(self.localizer.get_string("step5_button_start_visum", default="Visum-Verarbeitung starten"))
        self.log_group.setTitle(escape_mnemonic(self.localizer.get_string("step5_group_log", default="Verarbeitungs-Protokoll (Log)")))
        if self._thread is None:
            self.phase_label.setText(self.localizer.get_string("step4_label_ready", default="Bereit zum Starten"))

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
        self.stop_button.setEnabled(True)
        self.step_combo.setEnabled(False)
        self._visum_seconds = 0
        self.elapsed_label.setText("00:00")
        if self._visum_timer is not None:
            self._visum_timer.start()

        self.phase_progress.setValue(0)
        self.phase_progress.start_animation()
        self.overall_progress.setValue(0)
        self.overall_progress.start_animation()

        self.phase_label.setText(self.localizer.get_string("step5_status_running", default="Visum-Verarbeitung läuft..."))
        self.log_edit.clear()
        self.append_log(self.localizer.get_string("step5_log_starting", project_path=self.project_path, start_step=start_step))

        self._thread = QtCore.QThread()
        self._worker = VisumWorker(self.project_path, start_step=start_step)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log_ready.connect(self.append_log)
        self._worker.phase_started.connect(self._on_phase_started)
        self._worker.substep_started.connect(self._on_substep_started)
        self._worker.phase_progress_value.connect(self.phase_progress.setValue)
        self._worker.overall_progress_value.connect(self.overall_progress.setValue)
        self._worker.finished.connect(self._on_finished)

        if self.running_state_changed:
            self.running_state_changed.emit(True)

        self._thread.start()

    def stop_visum_pipeline(self) -> None:
        self.append_log(self.localizer.get_string("step5_log_stopping", default="Visum-Verarbeitung wird abgebrochen und COM-Verbindung getrennt..."))
        if self._visum_timer is not None and self._visum_timer.isActive():
            self._visum_timer.stop()

        self.phase_progress.stop_animation()
        self.phase_progress.setFormat("%p%")
        self.overall_progress.stop_animation()

        if self._thread is not None:
            try:
                self._thread.requestInterruption()
                self._thread.terminate()
                self._thread.wait(2000)
            except Exception:
                pass
            self._thread = None
            self._worker = None

        # Cleanly disconnect COM and release Visum instance
        try:
            import importlib
            mod = importlib.import_module("core.scripts.visum.scripts.00_run_all_visum_scripts")
            if hasattr(mod, "disconnect_active_visum"):
                mod.disconnect_active_visum(close_app=False, log=self.append_log)
        except Exception as e:
            self.append_log(f"Hinweis beim Freigeben von COM: {e}")

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.step_combo.setEnabled(True)
        if self.running_state_changed:
            self.running_state_changed.emit(False)
        self.phase_label.setText(self.localizer.get_string("step5_status_stopped", default="Status: Durch Benutzer abgebrochen"))
        self.append_log(self.localizer.get_string("step5_log_stopped", default="Visum-Verarbeitung wurde abgebrochen."))

    def _on_phase_started(self, phase_name: str) -> None:
        self.phase_label.setText(phase_name)
        if hasattr(self.phase_progress, "start_animation"):
            self.phase_progress.start_animation()

    def _on_substep_started(self, substep_name: str) -> None:
        if substep_name:
            self.phase_progress.setFormat(f"%p% – {substep_name}")
            self.phase_progress.setToolTip(substep_name)
        else:
            self.phase_progress.setFormat("%p%")

    def _update_visum_status_timer(self) -> None:
        self._visum_seconds += 1
        mins, secs = divmod(self._visum_seconds, 60)
        self.elapsed_label.setText(f"{mins:02d}:{secs:02d}")

    def append_log(self, text: str) -> None:
        if text.startswith("PROGRESS_INLINE:"):
            text = text[len("PROGRESS_INLINE:"):].strip()
        self.log_edit.append(text)
        sb = self.log_edit.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_finished(self, success: bool, message: str) -> None:
        if self._visum_timer is not None and self._visum_timer.isActive():
            self._visum_timer.stop()

        self.phase_progress.stop_animation()
        self.overall_progress.stop_animation()

        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.step_combo.setEnabled(True)
        if self.running_state_changed:
            self.running_state_changed.emit(False)

        if success:
            try:
                WindowsTaskbarProgress.get_instance().clear()
            except Exception:
                pass
            self.phase_progress.setValue(100)
            self.phase_progress.setFormat("%p% – Abgeschlossen")
            self.overall_progress.setValue(100)
            self.phase_label.setText(self.localizer.get_string("step5_status_completed", default="Status: Erfolgreich abgeschlossen"))
            title = self.localizer.get_string("step5_title_success", default="Erfolg")
            msg = self.localizer.get_string("step5_msg_success", default=message)
            Dialogs.info(self, title, msg)
        else:
            try:
                WindowsTaskbarProgress.get_instance().set_error()
            except Exception:
                pass
            self.phase_label.setText(self.localizer.get_string("step5_status_error", default="Status: Abgebrochen / Fehler"))
            title = self.localizer.get_string("step5_title_error", default="Fehler")
            Dialogs.error(self, title, message)
