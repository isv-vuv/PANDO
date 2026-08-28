# translation_table_generator.py
import json
import os
import sys
from collections import OrderedDict

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


DEFAULT_LOCALES_DIR = os.path.dirname(os.path.abspath(__file__))

CATEGORIES = [
    (1, "01_common_navigation_and_actions", "1. Allgemeine Navigation, Buttons & Aktionen"),
    (2, "02_main_app_window_and_licenses", "2. Hauptfenster, Lizenzen, Updates & Status"),
    (3, "03_step_help_texts", "3. Schritt-Hilfetexte"),
    (4, "04_step0_welcome_and_project", "4. Schritt 0: Willkommen & Projektauswahl"),
    (5, "05_step1_location_search", "5. Schritt 1: Ortssuche"),
    (6, "06_step2_geodata_download", "6. Schritt 2: Geodaten-Download (POP, GADM, Geofabrik)"),
    (7, "07_step3_study_area_and_grid", "7. Schritt 3: Untersuchungsraum & Gitter-Definition"),
    (8, "08_models_and_parameters", "8. Modelle & Parameterdefinitionen (Modell 1–6)"),
    (9, "09_step4_processing_and_validation", "9. Schritt 4: Parameterprüfung & Modellverarbeitung"),
    (10, "10_step5_visum_processing", "10. Schritt 5: Visum-Verarbeitung"),
    (11, "11_step6_results", "11. Schritt 6: Ergebnisse & Layer-Übersicht"),
    (12, "12_step7_evaluation", "12. Schritt 7: Auswertung"),
    (99, "99_other", "13. Sonstige Meldungen"),
]


def get_key_category(key: str) -> tuple[int, str, str]:
    """Classify translation keys into clear, logical workflow and application categories."""
    if key.startswith('button_') or key.startswith('option_') or key.startswith('action_') or key in ('message_general_error_title', 'message_unknown_place', 'message_size_unknown', 'osm_copyright'):
        return CATEGORIES[0]
    elif (key.startswith('dialog_copyright_') or key.startswith('main_') or key.startswith('error_project_')
          or key.startswith('window_title') or key.startswith('wizard_step_') or key.startswith('update_')
          or key.startswith('status_project_folder_') or key.startswith('status_stopping_') or key.startswith('status_terms_') or key.startswith('status_processing_cancelled')):
        return CATEGORIES[1]
    elif key.startswith('help_'):
        return CATEGORIES[2]
    elif key.startswith('step0_'):
        return CATEGORIES[3]
    elif key.startswith('step1_'):
        return CATEGORIES[4]
    elif key.startswith('step2_'):
        return CATEGORIES[5]
    elif key.startswith('step3_') or key.startswith('grid_') or key.startswith('message_grid_'):
        return CATEGORIES[6]
    elif key.startswith('model_name_') or key.startswith('model2_param_') or key.startswith('model5_param_'):
        return CATEGORIES[7]
    elif key.startswith('step4_'):
        return CATEGORIES[8]
    elif key.startswith('step5_'):
        return CATEGORIES[9]
    elif key.startswith('step6_'):
        return CATEGORIES[10]
    elif key.startswith('step7_'):
        return CATEGORIES[11]
    else:
        return CATEGORIES[12]


def load_all_translations(locales_dir=DEFAULT_LOCALES_DIR):
    """Lädt alle Übersetzungsdateien aus dem angegebenen Verzeichnis."""
    translations = OrderedDict()
    if not os.path.isdir(locales_dir):
        print(f"Fehler: Übersetzungsverzeichnis '{locales_dir}' nicht gefunden.")
        return translations

    print(f"Lade Übersetzungen aus dem Verzeichnis: {os.path.abspath(locales_dir)}")
    sorted_filenames = sorted(os.listdir(locales_dir))

    for filename in sorted_filenames:
        if filename.endswith(".json"):
            lang_code = filename[:-5]
            filepath = os.path.join(locales_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    translations[lang_code] = json.load(f)
                print(f"Übersetzungen für '{lang_code}' aus {filename} geladen.")
            except json.JSONDecodeError as e:
                print(f"Fehler beim Dekodieren von JSON aus {filepath}: {e}")
            except Exception as e:
                print(f"Fehler beim Laden der Sprachdatei {filepath}: {e}")
    return translations


def get_all_unique_keys(translations_dict):
    """Sammelt alle eindeutigen Übersetzungsschlüssel, sortiert nach logischer Kategorie und Name."""
    all_keys = set()
    for lang_code, lang_data in translations_dict.items():
        all_keys.update(lang_data.keys())
    return sorted(list(all_keys), key=lambda k: (get_key_category(k)[0], k))


def generate_markdown_table(translations_dict, unique_keys, lang_codes):
    """Generiert eine strukturierte Markdown-Tabelle mit Überschriften je Kategorie."""
    if not translations_dict or not unique_keys or not lang_codes:
        return "Keine Daten zum Generieren der Tabelle vorhanden."

    sections = []
    current_cat_id = None
    table_rows = []

    header = "| Key | " + " | ".join(lang_codes) + " |"
    separator = "| --- | " + " | ".join(["---"] * len(lang_codes)) + " |"

    for key in unique_keys:
        cat_id, _cat_slug, cat_title = get_key_category(key)
        if cat_id != current_cat_id:
            if table_rows:
                sections.append("\n".join(table_rows))
                table_rows = []
            current_cat_id = cat_id
            sections.append(f"\n## {cat_title}\n")
            table_rows = [header, separator]

        escaped_key = key.replace("|", "\\|")
        row_values = [escaped_key]

        for lang_code in lang_codes:
            translation = str(translations_dict.get(lang_code, {}).get(key, "*N/A*"))
            processed_translation = translation.replace("\n", "<br>").replace("|", "\\|")
            if "{" in translation or "}" in translation:
                processed_translation = processed_translation.replace("`", "\\`")
                processed_translation = f"`{processed_translation}`"
            row_values.append(processed_translation)

        table_rows.append("| " + " | ".join(row_values) + " |")

    if table_rows:
        sections.append("\n".join(table_rows))

    return "\n".join(sections)


def sort_and_save_json_files(locales_dir=DEFAULT_LOCALES_DIR):
    """Sortiert alle JSON-Sprachdateien exakt nach den logischen Kategorien."""
    all_translations = load_all_translations(locales_dir)
    unique_keys = get_all_unique_keys(all_translations)

    for lang_code, lang_data in all_translations.items():
        ordered_data = OrderedDict()
        for k in unique_keys:
            if k in lang_data:
                ordered_data[k] = lang_data[k]
        filepath = os.path.join(locales_dir, f"{lang_code}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(ordered_data, f, ensure_ascii=False, indent=4)
        print(f"JSON-Sprachdatei sortiert und gespeichert: {lang_code}.json ({len(ordered_data)} Keys)")


def main():
    locales_directory = DEFAULT_LOCALES_DIR
    sort_and_save_json_files(locales_directory)
    all_translations = load_all_translations(locales_directory)

    if not all_translations:
        print("Keine Übersetzungen gefunden. Tabelle kann nicht erstellt werden.")
        return

    language_codes = list(all_translations.keys())
    unique_translation_keys = get_all_unique_keys(all_translations)

    markdown_table = generate_markdown_table(all_translations, unique_translation_keys, language_codes)

    output_filename = os.path.join(locales_directory, "translations_overview.md")
    try:
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write(f"# Übersetzungstabelle ({', '.join(language_codes)})\n")
            f.write(f"> Übersicht aller {len(unique_translation_keys)} Übersetzungsschlüssel, strukturiert nach Modulen und Schritten.\n\n")
            f.write(markdown_table)
        print(f"\nTabelle wurde erfolgreich in '{output_filename}' gespeichert.")
    except IOError as e:
        print(f"\nFehler beim Schreiben der Tabelle in die Datei '{output_filename}': {e}")


if __name__ == "__main__":
    main()

