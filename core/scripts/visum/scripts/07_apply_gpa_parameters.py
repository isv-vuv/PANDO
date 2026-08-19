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


def set_zone_based_zoom(visum, margin_factor: float = 0.05, log=logging.info) -> None:
    try:
        import pandas as pd
        zone_coords = visum.Net.Zones.GetMultipleAttributes(["XCOORD", "YCOORD"])
        if zone_coords:
            df_coords = pd.DataFrame(zone_coords, columns=["XCOORD", "YCOORD"])
            x_coords = pd.to_numeric(df_coords['XCOORD'], errors='coerce').dropna()
            y_coords = pd.to_numeric(df_coords['YCOORD'], errors='coerce').dropna()

            if not (x_coords.empty or y_coords.empty):
                x_min = float(x_coords.quantile(0.005))
                x_max = float(x_coords.quantile(0.995))
                y_min = float(y_coords.quantile(0.005))
                y_max = float(y_coords.quantile(0.995))

                dx = x_max - x_min
                dy = y_max - y_min

                x_min_padded = x_min - (dx * margin_factor)
                x_max_padded = x_max + (dx * margin_factor)
                y_min_padded = y_min - (dy * margin_factor)
                y_max_padded = y_max + (dy * margin_factor)

                visum.Graphic.SetWindow(x_min_padded, y_min_padded, x_max_padded, y_max_padded)
                visum.Graphic.Redraw()
                return
    except Exception as exc:
        log(f"Warnung: Zoom über Bezirke fehlgeschlagen: {exc}")

    try:
        import pandas as pd
        node_coords = visum.Net.Nodes.GetMultipleAttributes(["XCOORD", "YCOORD"])
        if node_coords:
            df_coords = pd.DataFrame(node_coords, columns=["XCOORD", "YCOORD"])
            x_coords = pd.to_numeric(df_coords['XCOORD'], errors='coerce').dropna()
            y_coords = pd.to_numeric(df_coords['YCOORD'], errors='coerce').dropna()

            if not (x_coords.empty or y_coords.empty):
                x_min = float(x_coords.quantile(0.005))
                x_max = float(x_coords.quantile(0.995))
                y_min = float(y_coords.quantile(0.005))
                y_max = float(y_coords.quantile(0.995))

                dx = x_max - x_min
                dy = y_max - y_min

                x_min_padded = x_min - (dx * margin_factor)
                x_max_padded = x_max + (dx * margin_factor)
                y_min_padded = y_min - (dy * margin_factor)
                y_max_padded = y_max + (dy * margin_factor)

                visum.Graphic.SetWindow(x_min_padded, y_min_padded, x_max_padded, y_max_padded)
                visum.Graphic.Redraw()
                return
    except Exception as exc:
        log(f"Warnung: Zoom über Knoten fehlgeschlagen: {exc}")


def set_territory_10_zoom(visum, log=logging.info) -> bool:
    """Zooms onto Territory 10 (Gebiet Key 10) and sets scale of view to 1:50,000."""
    try:
        ne = visum.CreateNetElements()
        ne.Add(visum.Net.Territories.ItemByKey(10))
        visum.Graphic.Autozoom(ne)
        visum.Graphic.SetScaleOfView(50000)
        visum.Graphic.Redraw()
        return True
    except Exception as exc:
        log(f"Hinweis: Autozoom auf Gebiet 10 nicht möglich: {exc}")
        return False


def apply_filter_files_to_visum(visum_helper_dir: Path, visum_input_dir: Path, visum, log=logging.info) -> None:
    """Applies .fil filter files from helper_files / fil folder to Visum and activates NodeFilter."""
    fil_files = []
    if visum_helper_dir.exists():
        fil_files.extend(list(visum_helper_dir.glob("*.fil")))
    fil_dir = visum_input_dir / "fil"
    if fil_dir.exists():
        for f in fil_dir.glob("*.fil"):
            if f not in fil_files and f.name not in [x.name for x in fil_files]:
                fil_files.append(f)

    if not fil_files:
        return

    for fil_file in sorted(fil_files, key=lambda p: p.name):
        try:
            log(f"Wende Filter-Datei an: {fil_file.name}")
            visum.Filters.Open(str(fil_file.resolve()))
            visum.Filters.NodeFilter().UseFilter = True
            log(f"Filter '{fil_file.name}' geladen und Knotens-Filter im Netz aktiviert.")
        except Exception as exc:
            log(f"Warnung: Filter-Datei {fil_file.name} konnte nicht aktiviert werden: {exc}")


def cleanup_stray_ver_files(visum_input_dir: Path, log=logging.info) -> None:
    for item in visum_input_dir.glob("*.ver"):
        if item.is_file():
            try:
                item.unlink()
                log(f"Entferne Datei aus Hauptverzeichnis: {item.name}")
            except Exception:
                pass


def clean_gpax_after_save(saved_gpax_path: Path, master_template_path: Path) -> None:
    """Combines new view bounds / viewPort from Visum save with master template's clean XML structure."""
    try:
        import xml.etree.ElementTree as ET
        from xml.dom import minidom

        if not saved_gpax_path.exists() or not master_template_path.exists():
            return

        tree_saved = ET.parse(saved_gpax_path)
        root_saved = tree_saved.getroot()
        ne2d_saved = root_saved.find("netEditor2D")

        view_port = ne2d_saved.find("viewPort") if ne2d_saved is not None else None
        base_saved = ne2d_saved.find("base") if ne2d_saved is not None else None

        tree_master = ET.parse(master_template_path)
        root_master = tree_master.getroot()
        ne2d_master = root_master.find("netEditor2D")

        if view_port is not None and ne2d_master is not None:
            existing_vp = ne2d_master.find("viewPort")
            if existing_vp is not None:
                ne2d_master.remove(existing_vp)
            ne2d_master.insert(0, view_port)

        if base_saved is not None and ne2d_master is not None:
            existing_base = ne2d_master.find("base")
            if existing_base is not None:
                ne2d_master.remove(existing_base)
            ne2d_master.insert(0, base_saved)

        raw_str = ET.tostring(root_master, encoding="utf-8")
        parsed = minidom.parseString(raw_str)
        lines = [l for l in parsed.toprettyxml(indent="\t", encoding="utf-8").decode("utf-8").split("\n") if l.strip()]
        with open(saved_gpax_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as exc:
        logging.warning(f"Konnte .gpax Bereinigung für {saved_gpax_path.name} nicht ausführen: {exc}")


def determine_app_language(base_project_dir: Path = None, visum = None) -> str:
    """Determines application language ('de' or 'en') from project metadata, Visum COM instance, or app settings."""
    if base_project_dir:
        base_dir = Path(base_project_dir).resolve()
        for fname in ["config.json", "project.json", "project_metadata.json"]:
            meta_file = base_dir / fname
            if meta_file.exists():
                try:
                    import json
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    lang = data.get("language") or data.get("app_language")
                    if lang:
                        lang_str = str(lang).strip().lower()
                        if lang_str.startswith("en"):
                            return "en"
                        if lang_str.startswith("de"):
                            return "de"
                except Exception:
                    pass

    if visum is not None:
        try:
            v_lang = str(visum.GetCurrentLanguage()).strip().upper()
            if "ENG" in v_lang or "EN" in v_lang:
                return "en"
            if "DEU" in v_lang or "GER" in v_lang or "DE" in v_lang:
                return "de"
        except Exception:
            pass

    return "de"


def get_language_specific_gpa_files(helper_dir: Path, lang: str = "de") -> list[Path]:
    """Returns GPA files (.gpa/.gpax) matching the selected language ('de' or 'en') ONLY from helper_files/gpa/."""
    if not helper_dir.exists():
        return []

    gpa_dir = helper_dir if helper_dir.name.lower() == "gpa" else helper_dir / "gpa"
    if not gpa_dir.exists() or not gpa_dir.is_dir():
        return []

    all_gpas = sorted(list(gpa_dir.glob("*.gpa")) + list(gpa_dir.glob("*.gpax")))
    lang_str = (lang or "de").lower()

    selected = []
    for gpa_file in all_gpas:
        name_upper = gpa_file.name.upper()
        if lang_str.startswith("en"):
            if name_upper.startswith("DE_") or name_upper.startswith("DE"):
                continue
            selected.append(gpa_file)
        else:
            if name_upper.startswith("EN_") or name_upper.startswith("EN"):
                continue
            selected.append(gpa_file)

    return selected


def ensure_visum_language(visum, target_lang: str, log=print):
    """Switches Visum language directly on active Visum instance without closing/re-opening."""
    if visum is None:
        return visum

    target_code = "ENG" if str(target_lang).lower().startswith("en") else "DEU"
    try:
        curr_lang = str(visum.GetCurrentLanguage()).strip().upper()
        log(f"Visum-Sprachprüfung: Aktuell '{curr_lang}' | Ziel-Sprache '{target_code}'")
        if target_code in curr_lang or (target_code == "DEU" and ("DE" in curr_lang or "GER" in curr_lang)):
            return visum

        log(f"Wechsle Visum-Sprache: {curr_lang} -> {target_code}")
        switched = False
        try:
            visum.SetLanguage(target_code)
            switched = True
        except Exception:
            try:
                visum.SetCurrentLanguage(target_code)
                switched = True
            except Exception as exc:
                log(f"Warnung: Visum-Sprachwechsel fehlgeschlagen: {exc}")

        if switched:
            new_lang = str(visum.GetCurrentLanguage()).strip().upper()
            log(f"Visum-Sprache erfolgreich umgestellt auf: {new_lang}")
        return visum
    except Exception as exc:
        log(f"Hinweis beim Visum-Sprachwechsel: {exc}")
        return visum


def open_graphic_parameters(visum, gpa_path: str) -> None:
    """Robustly opens graphic parameters (.gpax or .gpa) using Visum COM API."""
    exceptions = []

    if str(gpa_path).lower().endswith(".gpax"):
        try:
            visum.Net.GraphicParameters.OpenXml(gpa_path)
            return
        except Exception as exc:
            exceptions.append(f"Net.GraphicParameters.OpenXml: {exc}")

    try:
        visum.Net.GraphicParameters.Open(gpa_path)
        return
    except Exception as exc:
        exceptions.append(f"Net.GraphicParameters.Open: {exc}")

    try:
        visum.Graphic.OpenGraphicParameters(gpa_path)
        return
    except Exception as exc:
        exceptions.append(f"Graphic.OpenGraphicParameters: {exc}")

    try:
        visum.Graphic.GraphicParameters.Open(gpa_path)
        return
    except Exception as exc:
        exceptions.append(f"Graphic.GraphicParameters.Open: {exc}")

    try:
        visum.Net.GraphicParameters.Read(gpa_path)
        return
    except Exception as exc:
        exceptions.append(f"Net.GraphicParameters.Read: {exc}")

    raise RuntimeError("; ".join(exceptions))


def save_graphic_parameters(visum, gpa_path: str) -> None:
    """Robustly saves graphic parameters (.gpax or .gpa) using Visum COM API."""
    exceptions = []

    if str(gpa_path).lower().endswith(".gpax"):
        try:
            visum.Net.GraphicParameters.SaveXml(gpa_path)
            return
        except Exception as exc:
            exceptions.append(f"Net.GraphicParameters.SaveXml: {exc}")

    try:
        visum.Net.GraphicParameters.Save(gpa_path)
        return
    except Exception as exc:
        exceptions.append(f"Net.GraphicParameters.Save: {exc}")

    try:
        visum.Graphic.SaveGraphicParameters(gpa_path)
        return
    except Exception as exc:
        exceptions.append(f"Graphic.SaveGraphicParameters: {exc}")

    try:
        visum.Graphic.GraphicParameters.Save(gpa_path)
        return
    except Exception as exc:
        exceptions.append(f"Graphic.GraphicParameters.Save: {exc}")

    raise RuntimeError("; ".join(exceptions))


def apply_gpa_parameters_to_visum(target_project_dir=None, visum=None, log=logging.info, language: str = None):
    base_project_dir, visum_input_dir, visum_helper_dir = get_project_paths(target_project_dir)
    ver_dir = visum_input_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)

    gpa_dir = visum_input_dir / "gpa"
    gpa_dir.mkdir(parents=True, exist_ok=True)

    if not language:
        language = determine_app_language(base_project_dir, visum)

    if visum is None:
        cand_ver = None
        ver_07_matches = list(ver_dir.glob("07_*_Model.ver"))
        if ver_07_matches:
            cand_ver = ver_07_matches[0]
        elif (ver_dir / "06_DirectLineMatrices_Added.ver").exists():
            cand_ver = ver_dir / "06_DirectLineMatrices_Added.ver"
        elif (visum_input_dir / "06_DirectLineMatrices_Added.ver").exists():
            cand_ver = visum_input_dir / "06_DirectLineMatrices_Added.ver"

        if cand_ver and cand_ver.exists():
            log(f"Lade Version: {cand_ver.name}")
            visum = com.Dispatch("Visum.Visum.250")
            visum.Graphic.ShowMaximized()
            visum.LoadVersion(str(cand_ver))
        else:
            log("Fehler: Keine geeignete Modellversion (.ver) gefunden.")
            return None

    visum = ensure_visum_language(visum, language, log=log)
    links_gpa_file = None

    # 1. Speichere zwei eigenständige .gpa (binäre Grafikparameter mit Bildschirmausschnitt)
    is_en = (language or "de").lower().startswith("en")
    country_gpa_name = "_Extent_Country.gpa" if is_en else "_Ausschnitt_Land.gpa"
    study_gpa_name = "_Extent_StudyArea.gpa" if is_en else "_Ausschnitt_Untersuchungsraum.gpa"

    dst_country_gpa = gpa_dir / country_gpa_name
    dst_study_gpa = gpa_dir / study_gpa_name

    try:
        set_zone_based_zoom(visum, margin_factor=0.05, log=log)
        visum.Graphic.Redraw()
        visum.Net.GraphicParameters.Save(str(dst_country_gpa.resolve()))
        log(f"Ausschnitts-Grafikparameter (.gpa Land) gespeichert: {dst_country_gpa.name}")
    except Exception as exc:
        log(f"Warnung: Ausschnitts-Grafikparameter ({dst_country_gpa.name}) konnte nicht gespeichert werden: {exc}")

    try:
        set_territory_10_zoom(visum, log=log)
        visum.Graphic.Redraw()
        visum.Net.GraphicParameters.Save(str(dst_study_gpa.resolve()))
        log(f"Ausschnitts-Grafikparameter (.gpa Untersuchungsraum) gespeichert: {dst_study_gpa.name}")
    except Exception as exc:
        log(f"Warnung: Ausschnitts-Grafikparameter ({dst_study_gpa.name}) konnte nicht gespeichert werden: {exc}")

    # 2. Verarbeite die .gpax Vorlagendateien (ohne _Zoom Variante)
    if visum_helper_dir.exists():
        gpa_files = get_language_specific_gpa_files(visum_helper_dir, language)
        log(f"{len(gpa_files)} GPA-Dateien ({language.upper()}: .gpa/.gpax) im Hilfsordner identifiziert.")

        for gpa_file in gpa_files:
            abs_src_gpa = str(gpa_file.resolve())

            stem = gpa_file.stem
            ext = gpa_file.suffix

            upper_stem = stem.upper()
            if "LINK" in upper_stem or "STRECKE" in upper_stem:
                links_gpa_file = gpa_file

            if upper_stem.startswith("DE_") or upper_stem.startswith("EN_"):
                base_name = stem[3:]
            elif upper_stem.startswith("DE") or upper_stem.startswith("EN"):
                base_name = stem[2:].lstrip("_")
            elif upper_stem.startswith("GPA_"):
                base_name = stem[4:]
            elif upper_stem.startswith("GPA"):
                base_name = stem[3:].lstrip("_")
            else:
                base_name = stem

            dst_std = gpa_dir / f"{base_name}{ext}"

            try:
                open_graphic_parameters(visum, abs_src_gpa)
                set_zone_based_zoom(visum, margin_factor=0.05, log=log)
                visum.Graphic.Redraw()
                save_graphic_parameters(visum, str(dst_std.resolve()))
                log(f"Grafikparameter gespeichert: {dst_std.name}")
            except Exception as exc:
                log(f"Warnung: Grafikparameter {gpa_file.name} konnte nicht angewendet werden: {exc}")

        for stray_gpa in gpa_dir.glob("GPA_*.gpa"):
            try:
                stray_gpa.unlink()
            except Exception:
                pass
    else:
        log(f"Warnung: Hilfsdatei-Ordner nicht gefunden: {visum_helper_dir}")

    # Wende Links GPA erneut an, falls vorhanden
    if links_gpa_file is not None and links_gpa_file.exists():
        try:
            open_graphic_parameters(visum, str(links_gpa_file.resolve()))
            log(f"Grafikparameter '{links_gpa_file.name}' für finale Version geladen.")
        except Exception as exc:
            log(f"Hinweis beim Laden der Links-Grafikparameter: {exc}")

    # Wende alle .fil Filterdateien an und aktiviere Knotens-Filter pauschal im Netz
    apply_filter_files_to_visum(visum_helper_dir, visum_input_dir, visum, log=log)
    set_zone_based_zoom(visum, margin_factor=0.05, log=log)
    try:
        visum.Graphic.Redraw()
    except Exception:
        pass

    # Speichere finale 07_{city_name}_Model.ver Datei
    city_name = get_model_city_name(base_project_dir)
    final_ver_file = ver_dir / f"07_{city_name}_Model.ver"
    try:
        visum.SaveVersion(str(final_ver_file.resolve()))
        log(f"Finale Modellversion mit Links-Grafikparametern und aktivem Filter gespeichert: {final_ver_file.name}")
    except Exception as exc:
        log(f"Warnung: Finale Modellversion {final_ver_file.name} konnte nicht gespeichert werden: {exc}")

    cleanup_stray_ver_files(visum_input_dir, log=log)
    return visum


if __name__ == "__main__":
    apply_gpa_parameters_to_visum()
