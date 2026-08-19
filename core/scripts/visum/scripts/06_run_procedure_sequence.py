import logging
import os
import re
import sys
from pathlib import Path
import win32com.client as com

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout
)


def get_project_paths(target_project_dir=None) -> tuple[Path, Path, Path]:
    if target_project_dir:
        base_project_dir = Path(target_project_dir).resolve()
    elif len(sys.argv) > 1 and sys.argv[1].strip():
        base_project_dir = Path(sys.argv[1]).resolve()
    else:
        script_dir = Path(__file__).resolve().parent
        base_project_dir = script_dir.parent if script_dir.name == "visum_processing" else script_dir.parent.parent.parent

    visum_input_dir = base_project_dir / "processed" / "visum"

    script_dir = Path(__file__).resolve().parent
    visum_helper_dir = script_dir.parent / "helper_files"

    return base_project_dir, visum_input_dir, visum_helper_dir


def get_model_city_name(base_project_dir: Path) -> str:
    """Returns clean city/location name without date prefix."""
    for filename in ["config.json", "project.json", "project_metadata.json"]:
        meta_file = base_project_dir / filename
        if meta_file.exists():
            try:
                import json
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                loc = data.get("selected_location") or {}
                address = ""
                if isinstance(loc, dict):
                    address = loc.get("address") or loc.get("display_name") or ""
                elif hasattr(loc, "address"):
                    address = getattr(loc, "address", "")

                if address:
                    city_part = address.split(",")[0].strip()
                    if city_part:
                        clean = re.sub(r"[^\w\s-]", "", city_part).strip()
                        clean = re.sub(r"[-\s]+", "_", clean)
                        if clean:
                            return clean
            except Exception:
                pass

    folder_name = base_project_dir.name
    city_from_folder = re.sub(r"^\d{8}_?", "", folder_name).strip("_")
    return city_from_folder if city_from_folder else "Model"


def cleanup_stray_ver_files(visum_input_dir: Path, log=logging.info) -> None:
    for item in visum_input_dir.glob("*.ver"):
        if item.is_file():
            try:
                item.unlink()
                log(f"Entferne Datei aus Hauptverzeichnis: {item.name}")
            except Exception:
                pass


def run_procedure_sequence(target_project_dir=None, visum=None, log=logging.info):
    base_project_dir, visum_input_dir, visum_helper_dir = get_project_paths(target_project_dir)
    ver_dir = visum_input_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)

    city_name = get_model_city_name(base_project_dir)
    target_ver = ver_dir / f"07_{city_name}_Model.ver"

    input_ver = ver_dir / "06_DirectLineMatrices_Added.ver"
    if not input_ver.exists():
        input_ver = visum_input_dir / "06_DirectLineMatrices_Added.ver"

    if visum is None:
        if input_ver.exists():
            log(f"Lade Version: {input_ver.name}")
            visum = com.Dispatch("Visum.Visum.250")
            visum.Graphic.ShowMaximized()
            visum.LoadVersion(str(input_ver))
        else:
            log(f"Fehler: Version {input_ver.name} nicht gefunden.")
            return None

    try:
        import importlib
        step7 = importlib.import_module("07_apply_gpa_parameters")
        app_lang = step7.determine_app_language(base_project_dir, visum)
    except Exception:
        app_lang = "de"

    is_en = str(app_lang).lower().startswith("en")
    target_xml_name = "EN_ProcedureSequence.xml" if is_en else "DE_Verfahrensablauf.xml"

    candidate_paths = [
        visum_helper_dir / "pro" / target_xml_name,
        visum_helper_dir / target_xml_name,
        visum_input_dir / "pro" / target_xml_name,
        visum_input_dir / target_xml_name,
        visum_helper_dir / "pro" / "ProcedureSequence.xml",
        visum_helper_dir / "ProcedureSequence.xml",
        visum_input_dir / "pro" / "ProcedureSequence.xml",
    ]

    proc_xml = None
    for cand in candidate_paths:
        if cand.exists():
            proc_xml = cand
            break

    if proc_xml and proc_xml.exists():
        log(f"Importiere Verfahrensablauf ({app_lang.upper()}): {proc_xml.name}")
        visum.Procedures.Open(str(proc_xml.resolve()), True, True, True)
    else:
        log(f"Warnung: Verfahrensablauf-Datei '{target_xml_name}' nicht gefunden in pro/")

    setup_dsegments_path = visum_helper_dir / "Setup_DSegments.py"
    if setup_dsegments_path.exists():
        log(f"Führe {setup_dsegments_path.name} aus...")
        globs = {
            "Visum": visum,
            "visum": visum,
            "print": log,
            "__file__": str(setup_dsegments_path.resolve()),
        }
        with open(setup_dsegments_path, "r", encoding="utf-8") as f:
            code = f.read()
        exec(code, globs)
    else:
        log(f"Warnung: {setup_dsegments_path.name} nicht gefunden.")

    input_stem = input_ver.stem
    prep_ver = ver_dir / f"{input_stem}_ProcedureSequence_Debug.ver"
    try:
        log(f"Speichere vorbereitete Version vor Verfahrensausführung: {prep_ver.name}")
        visum.SaveVersion(str(prep_ver))
    except Exception as prep_exc:
        log(f"Hinweis beim Speichern von {prep_ver.name}: {prep_exc}")

    try:
        log("Führe Verfahrensablauf aus...")
        visum.Procedures.Execute()
        log(f"Speichere finale Modellversion: {target_ver.name}")
        visum.SaveVersion(str(target_ver))
    except Exception as exc:
        error_ver = ver_dir / f"07_{city_name}_Model_ProcedureSequence_Debug.ver"
        log(f"Fehler bei Verfahrensablauf-Ausführung: {exc}")
        log(f"Speichere Fehlerzustand-Version für Debugging unter: {error_ver.name}")
        try:
            visum.SaveVersion(str(error_ver))
            visum.SaveVersion(str(prep_ver))
            visum.SaveVersion(str(target_ver))
        except Exception:
            pass
        raise

    cleanup_stray_ver_files(visum_input_dir, log=log)
    return visum


if __name__ == "__main__":
    run_procedure_sequence()
