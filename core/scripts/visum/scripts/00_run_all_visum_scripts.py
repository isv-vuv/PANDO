"""Master orchestrator script for automated execution of all PTV Visum post-processing steps (01-05)."""

from __future__ import annotations

import os
import sys
import logging
import importlib
import shutil
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


_ACTIVE_VISUM_INSTANCE = None


def deploy_visum_importer_to_appdata(log=print) -> Path:
    """Verifies PTV Visum 2025 installation and deploys the OSM importer folder without renaming or splitting."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("Umgebungsvariable APPDATA ist nicht vorhanden.")

    visum_2025_base = Path(appdata) / "PTV Vision" / "PTV Visum 2025"
    if not visum_2025_base.exists():
        msg = f"Fehler: PTV Visum 2025 ist nicht unter '{visum_2025_base}' installiert."
        log(msg)
        raise FileNotFoundError(msg)

    importer_target_dir = visum_2025_base / "Importer" / "PANDO_Importer"
    importer_target_dir.mkdir(parents=True, exist_ok=True)

    script_dir = Path(__file__).resolve().parent
    importer_src_dir = script_dir.parent / "importer" / "PANDO_Importer"

    if importer_src_dir.exists():
        for item in importer_src_dir.iterdir():
            dst = importer_target_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        log(f"OSM-Importer erfolgreich nach AppData kopiert: {importer_target_dir}")
    else:
        log(f"Warnung: Quell-Importer-Ordner nicht gefunden unter: {importer_src_dir}")

    return importer_target_dir


def populate_visum_procedure_files(visum_dir: Path, log=print) -> list[Path]:
    """Copy procedure XML files into processed/visum/pro/."""
    pro_dir = visum_dir / "pro"
    pro_dir.mkdir(parents=True, exist_ok=True)

    script_dir = Path(__file__).resolve().parent
    helper_dir = script_dir.parent / "helper_files"
    pro_src_dir = helper_dir / "pro"
    copied = []
    
    search_dirs = [pro_src_dir, helper_dir]
    for src_d in search_dirs:
        if src_d.exists():
            for xml_file in src_d.glob("*.xml"):
                dst = pro_dir / xml_file.name
                if not dst.exists() or dst.stat().st_mtime < xml_file.stat().st_mtime:
                    shutil.copy2(xml_file, dst)
                copied.append(dst)
    if copied:
        log(f"{len(copied)} Verfahrensablauf-Dateien (.xml) in '{pro_dir.name}' vorbereitet.")
    return copied


def populate_visum_gpa_files(visum_dir: Path, log=print, language: str = None) -> list[Path]:
    """Copy GPA graphic parameters files (.gpa/.gpax) matching the selected language into processed/visum/gpa/."""
    gpa_dir = visum_dir / "gpa"
    gpa_dir.mkdir(parents=True, exist_ok=True)

    base_project_dir = visum_dir.parent.parent if visum_dir.parent.name == "processed" else visum_dir.parent
    if not language:
        try:
            import importlib
            step7 = importlib.import_module("07_apply_gpa_parameters")
            language = step7.determine_app_language(base_project_dir)
        except Exception:
            language = "de"

    script_dir = Path(__file__).resolve().parent
    helper_dir = script_dir.parent / "helper_files"
    copied = []
    if helper_dir.exists():
        try:
            import importlib
            step7 = importlib.import_module("07_apply_gpa_parameters")
            gpa_files = step7.get_language_specific_gpa_files(helper_dir, language)
        except Exception:
            gpa_dir = helper_dir / "gpa"
            if gpa_dir.exists() and gpa_dir.is_dir():
                all_files = sorted(list(gpa_dir.glob("*.gpa")) + list(gpa_dir.glob("*.gpax")))
            else:
                all_files = []
            lang_str = (language or "de").lower()
            gpa_files = [f for f in all_files if not f.name.upper().startswith("EN_" if lang_str.startswith("de") else "DE_")]

        for gpa_file in gpa_files:
            stem = gpa_file.stem
            ext = gpa_file.suffix
            upper_stem = stem.upper()
            if upper_stem.startswith("DE_") or upper_stem.startswith("EN_"):
                clean_name = f"{stem[3:]}{ext}"
            elif upper_stem.startswith("DE") or upper_stem.startswith("EN"):
                clean_name = f"{stem[2:].lstrip('_')}{ext}"
            elif upper_stem.startswith("GPA_"):
                clean_name = f"{stem[4:]}{ext}"
            else:
                clean_name = gpa_file.name

            dst_file = gpa_dir / clean_name
            shutil.copy2(gpa_file, dst_file)
            copied.append(dst_file)
        if copied:
            log(f"{len(copied)} Grafikparameter-Dateien ({language.upper()}: .gpa/.gpax) bereinigt nach '{gpa_dir.name}' kopiert.")
    return copied


def populate_visum_filter_files(visum_dir: Path, log=print) -> list[Path]:
    """Copy filter (.fil) files into processed/visum/fil/."""
    fil_dir = visum_dir / "fil"
    fil_dir.mkdir(parents=True, exist_ok=True)

    script_dir = Path(__file__).resolve().parent
    helper_dir = script_dir.parent / "helper_files"
    copied = []
    if helper_dir.exists():
        for fil_file in helper_dir.glob("*.fil"):
            dst_file = fil_dir / fil_file.name
            shutil.copy2(fil_file, dst_file)
            copied.append(dst_file)
        if copied:
            log(f"{len(copied)} Filter-Dateien (.fil) nach '{fil_dir.name}' kopiert.")
    return copied


def run_visum_postprocessing_chain(project_path: str | Path, *, start_step: int = 1, log=print) -> bool:
    """Executes Visum scripts starting from start_step (1 to 5) in automated sequence."""
    global _ACTIVE_VISUM_INSTANCE

    base_dir = Path(project_path).resolve()
    visum_dir = base_dir / "processed" / "visum"
    ver_dir = visum_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    log(f"Starte Visum-Verarbeitungskette für: {base_dir.name} (Start ab Schritt {start_step})")

    visum = None
    try:
        # Step 0: Deploy Importer & Populate Procedures / GPA / Filter files
        log("Prüfe Visum 2025 Installation und kopiere Importer-Dateien...")
        deploy_visum_importer_to_appdata(log=log)
        populate_visum_procedure_files(visum_dir, log=log)
        populate_visum_gpa_files(visum_dir, log=log)
        populate_visum_filter_files(visum_dir, log=log)

        # Cleanup stray .ver files in processed/visum/ outside ver/
        for stray in visum_dir.glob("*.ver"):
            if stray.is_file():
                try:
                    stray.unlink()
                    log(f"Entferne deplazierte Datei aus Hauptverzeichnis: {stray.name}")
                except Exception:
                    pass

        if start_step > 1:
            import win32com.client as com
            ver_map = {
                2: ["02_Zones_Imported.ver", "01_Links_Imported.ver"],
                3: ["03_AccessNodes_Derived.ver", "02_Zones_Imported.ver"],
                4: ["04_LinkTypes_Restructured.ver", "03_AccessNodes_Derived.ver"],
                5: ["05_Connectors_Generated.ver", "04_LinkTypes_Restructured.ver"],
                6: ["06_DirectLineMatrices_Added.ver", "05_Connectors_Generated.ver"],
                7: [p.name for p in (ver_dir.glob("07_*_Model.ver"))] + ["06_DirectLineMatrices_Added.ver"],
            }
            start_ver_file = None
            for cand in ver_map.get(start_step, []):
                for p in [ver_dir / cand, visum_dir / cand]:
                    if p.exists():
                        start_ver_file = p
                        break
                if start_ver_file:
                    break

            if start_ver_file and start_ver_file.exists():
                log(f"Überspringe Schritte 1 bis {start_step - 1}. Lade vorhandene Version: {start_ver_file.name}")
                visum = com.Dispatch("Visum.Visum.250")
                visum.Graphic.ShowMaximized()
                visum.LoadVersion(str(start_ver_file))
                try:
                    step7 = importlib.import_module("07_apply_gpa_parameters")
                    app_lang = step7.determine_app_language(base_dir, visum)
                    visum = step7.ensure_visum_language(visum, app_lang, log=log)
                except Exception:
                    pass
                _ACTIVE_VISUM_INSTANCE = visum
            else:
                log(f"Hinweis: Benötigte Version für Start bei Schritt {start_step} nicht gefunden. Starte regulär bei Schritt 1.")
                start_step = 1

        # Step 01: Import Links and Zones
        if start_step <= 1:
            log("Schritt 1/7: Verbindungen und Zonen werden importiert")
            step1 = importlib.import_module("01_import_links_and_zones")
            zones_gpkg = base_dir / "processed" / "qgis_output" / "model6_ZoneAssembler" / "zones.gpkg"
            if not zones_gpkg.exists():
                zones_gpkg = visum_dir / "shapefile" / "Zones" / "zones.shp"
            if not zones_gpkg.exists():
                zones_gpkg = visum_dir / "Zones.gpkg"

            mainzones_gpkg = base_dir / "processed" / "qgis_output" / "model6_ZoneAssembler" / "mainzones.gpkg"
            if not mainzones_gpkg.exists():
                mainzones_gpkg = visum_dir / "shapefile" / "Zones" / "mainzones.shp"
            if not mainzones_gpkg.exists():
                mainzones_gpkg = visum_dir / "Mainzones.gpkg"

            osm_file = base_dir / "processed" / "osm" / "04_network" / "road_network_hierarchical_modified.osm"
            if not osm_file.exists():
                osm_file = base_dir / "processed" / "osm" / "04_network" / "road_network_hierarchical_modified.osm.pbf"

            visum = step1.run_import_links_and_zones(
                zones_gpkg=zones_gpkg,
                mainzones_gpkg=mainzones_gpkg,
                osm_file=osm_file,
                output_dir=visum_dir,
                visum=visum,
            )
            _ACTIVE_VISUM_INSTANCE = visum
            log("Schritt 1 abgeschlossen: 01_Links_Imported.ver und 02_Zones_Imported.ver wurden gespeichert.")

        # Step 02: Derive Access Nodes
        if start_step <= 2:
            log("Schritt 2/7: Anbindungsknoten werden abgeleitet")
            step2 = importlib.import_module("02_derive_access_nodes")
            visum = step2.run_access_node_derivation(target_project_dir=base_dir, visum=visum)
            _ACTIVE_VISUM_INSTANCE = visum
            log("Schritt 2 abgeschlossen: 03_AccessNodes_Derived.ver wurde gespeichert.")

        # Step 03: Restructure Link Types
        if start_step <= 3:
            log("Schritt 3/7: Streckentypen werden umstrukturiert")
            step3 = importlib.import_module("03_restructure_linktypes")
            visum = step3.run_restructure_link_types(target_project_dir=base_dir, visum=visum)
            _ACTIVE_VISUM_INSTANCE = visum
            log("Schritt 3 abgeschlossen: 04_LinkTypes_Restructured.ver wurde gespeichert.")

        # Step 04: Generate Connectors
        if start_step <= 4:
            log("Schritt 4/7: Anbindungen werden generiert")
            step4 = importlib.import_module("04_generate_connectors")
            visum = step4.run_generate_connectors(target_project_dir=base_dir, visum=visum)
            _ACTIVE_VISUM_INSTANCE = visum
            log("Schritt 4 abgeschlossen: 05_Connectors_Generated.ver wurde gespeichert.")

        # Step 05: Generate Direct Line Matrices
        if start_step <= 5:
            log("Schritt 5/7: Luftlinienmatrizen werden berechnet")
            step5 = importlib.import_module("05_generate_direct_line_matrices")
            visum = step5.run_direct_line_matrix_generation(target_project_dir=base_dir, visum=visum)
            _ACTIVE_VISUM_INSTANCE = visum
            log("Schritt 5 abgeschlossen: 06_DirectLineMatrices_Added.ver wurde gespeichert.")

        # Step 06: Execute Procedure Sequence
        if start_step <= 6:
            log("Schritt 6/7: Verfahrensablauf wird ausgeführt")
            step6 = importlib.import_module("06_run_procedure_sequence")
            visum = step6.run_procedure_sequence(target_project_dir=base_dir, visum=visum, log=log)
            _ACTIVE_VISUM_INSTANCE = visum
            log("Schritt 6 abgeschlossen: Verfahrensablauf wurde ausgeführt.")

        # Step 07: Apply GPA Parameters
        if start_step <= 7:
            log("Schritt 7/7: Grafikparameter werden angewendet")
            step7 = importlib.import_module("07_apply_gpa_parameters")
            visum = step7.apply_gpa_parameters_to_visum(target_project_dir=base_dir, visum=visum, log=log)
            _ACTIVE_VISUM_INSTANCE = visum
            log("Schritt 7 abgeschlossen: Grafikparameter wurden angewendet.")

        log("Visum-Verarbeitungskette wurde erfolgreich ausgeführt.")
        return True

    except Exception as e:
        log(f"Fehler während der Visum-Verarbeitungskette: {e}")
        if visum is not None:
            try:
                error_debug_ver = ver_dir / "00_ProcedureSequence_Debug.ver"
                visum.SaveVersion(str(error_debug_ver))
                log(f"Fehlerzustand-Version wurde erfolgreich gespeichert unter: {error_debug_ver.name}")
            except Exception as save_err:
                log(f"Hinweis: Fehlerzustand konnte nicht als .ver gespeichert werden: {save_err}")
            try:
                visum.Graphic.ShowMaximized()
            except Exception:
                pass
            _ACTIVE_VISUM_INSTANCE = visum
        log("Visum bleibt geöffnet, damit der Zustand im Programm überprüft werden kann.")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python 00_run_all_visum_scripts.py <path_to_project_dir> [start_step]")
        sys.exit(1)
    
    target_project = sys.argv[1]
    step_arg = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 1
    success = run_visum_postprocessing_chain(target_project, start_step=step_arg)
    if not success:
        sys.exit(1)
