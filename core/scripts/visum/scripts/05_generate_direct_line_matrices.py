import os
import sys
import logging
from pathlib import Path
import win32com.client as com

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout
)


def get_project_paths(target_project_dir=None) -> tuple[Path, Path, Path]:
    """Ermittelt dynamisch die Projektpfade und fügt Hilfsordner zum Python-Suchpfad hinzu."""
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

    for path in [script_dir, visum_helper_dir, base_project_dir]:
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))

    return base_project_dir, visum_input_dir, visum_helper_dir


def find_helper_file(helper_dir: Path, base_dir: Path, filename: str) -> Path | None:
    """Sucht erst im Hilfsdatei-Ordner und danach rekursiv im Projektbaum."""
    if helper_dir.exists():
        direct_match = list(helper_dir.rglob(filename))
        if direct_match:
            return direct_match[0]

    project_match = list(base_dir.rglob(filename))
    if project_match:
        return project_match[0]

    return None


def open_visum():
    logging.info("Initialisiere Visum-Instanz...")
    visum = com.Dispatch("Visum.Visum.250")
    visum.Graphic.ShowMaximized()
    return visum


def run_direct_line_matrix_generation(target_project_dir=None, visum=None):
    logging.info("--- Visum Schritt 5: Luftlinienmatrizen & Grafikparameter gestartet ---")
    base_project_dir, visum_input_dir, visum_helper_dir = get_project_paths(target_project_dir)

    ver_dir = visum_input_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)
    mtx_dir = visum_input_dir / "mtx"
    mtx_dir.mkdir(parents=True, exist_ok=True)

    output_ver = ver_dir / "06_DirectLineMatrices_Added.ver"

    # Import des ISV DLNT Tools & Translator
    try:
        import importlib
        try:
            dlnt = importlib.import_module("05_cfl_directlinenetwork_tool")
        except ImportError:
            dlnt = importlib.import_module("cfl_directlinenetwork_tool")

        try:
            lang_mgmt = importlib.import_module("05_language_management")
        except ImportError:
            lang_mgmt = importlib.import_module("language_management")
        Translator = lang_mgmt.Translator
    except ImportError as e:
        logging.error(f"Konnte benötigte Module nicht importieren: {e}")
        logging.error(f"Bitte stelle sicher, dass die Dateien im Skript-Ordner liegen.")
        sys.exit(1)

    # Übersetzungsdatei im Hilfsordner suchen
    json_path = find_helper_file(visum_helper_dir, base_project_dir, "translations.json")
    if not json_path:
        json_path = find_helper_file(visum_helper_dir, base_project_dir, "Translations.json")

    try:
        import importlib
        step7 = importlib.import_module("07_apply_gpa_parameters")
        app_lang = step7.determine_app_language(base_project_dir, visum)
    except Exception:
        app_lang = "de"

    translator = Translator(dict_path=json_path if json_path and json_path.exists() else None, language=app_lang)

    if visum is None:
        input_ver = ver_dir / "05_Connectors_Generated.ver"
        if not input_ver.exists():
            input_ver = visum_input_dir / "05_Connectors_Generated.ver"
        if not input_ver.exists():
            input_ver = ver_dir / "04_LinkTypes_Restructured.ver"
        if not input_ver.exists():
            input_ver = visum_input_dir / "04_LinkTypes_Restructured.ver"

        visum = open_visum()
        logging.info(f"Lade Version: {input_ver.name}")
        visum.LoadVersion(str(input_ver))

    try:
        import importlib
        step7 = importlib.import_module("07_apply_gpa_parameters")
        visum = step7.ensure_visum_language(visum, app_lang, log=print)
    except Exception:
        pass

    cfl_prefix = translator.translate("cfl")
    dict_cfl = {
        f"{cfl_prefix}_0": 0,
        f"{cfl_prefix}_1": 1,
        f"{cfl_prefix}_2": 2,
        f"{cfl_prefix}_3": 3,
        f"{cfl_prefix}_4": 4
    }

    # =========================================================================
    # Durchlauf 1: n = 1 für alle Stufen
    logging.info(f"Berechne Luftliniennetz (Sprache: {app_lang.upper()}, Durchlauf 1: n = 1)...")
    calc_n1 = dlnt.DirectLineNetworkCalculator(
        source=visum,
        attr_cfl="TYPENO",
        dict_cfl=dict_cfl,
        max_distance=1,
        no_suppliers=0,
        formula_distance="euclidean",
        translator=translator,
        path_output=mtx_dir
    )
    calc_n1.calculate_main()
    calc_n1.export_matrix()

    logging.info(f"Berechne Luftliniennetz (Durchlauf 2: n = 2 und n = 5)...")
    max_distance_pass2 = {
        f"{cfl_prefix}_0": 2,
        f"{cfl_prefix}_1": 2,
        f"{cfl_prefix}_2": 2,
        f"{cfl_prefix}_3": 5,
        f"{cfl_prefix}_4": 5
    }

    calc_n2_n5 = dlnt.DirectLineNetworkCalculator(
        source=visum,
        attr_cfl="TYPENO",
        dict_cfl=dict_cfl,
        max_distance=max_distance_pass2,
        no_suppliers=0,
        formula_distance="euclidean",
        translator=translator,
        path_output=mtx_dir
    )
    calc_n2_n5.calculate_main()
    calc_n2_n5.export_matrix()

    logging.info("Folgende RIN-Luftlinienmatrizen wurden im Modell angelegt:")
    for m in visum.Net.Matrices.GetAll:
        code = m.AttValue("CODE")
        if code and code.startswith("RIN_"):
            logging.info(f"Matrix-Code: {code:20s} | Name: {m.AttValue('NAME')}")

    # Speichern der Modellversion 06_DirectLineMatrices_Added.ver
    logging.info(f"Speichere Modellversion unter: {output_ver.name}")
    visum.SaveVersion(str(output_ver))

    return visum


if __name__ == "__main__":
    visum_instance = run_direct_line_matrix_generation()
    print("\n" + "=" * 60)
    print("Erfolg: Luftlinien-Matrizen erfolgreich erzeugt und importiert!")
    print("Gespeicherte Datei: 06_DirectLineMatrices_Added.ver")
    print("=" * 60)
    input("\n[Hinweis] Visum bleibt geöffnet. Drücke ENTER im Terminal zum Beenden...")