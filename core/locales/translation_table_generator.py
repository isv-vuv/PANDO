# translation_table_generator.py
import json
import os
import sys
from collections import OrderedDict

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


DEFAULT_LOCALES_DIR = os.path.dirname(os.path.abspath(__file__))


def load_all_translations(locales_dir=DEFAULT_LOCALES_DIR):
    """
    Lädt alle Übersetzungsdateien (z.B. en.json, de.json) aus dem angegebenen Verzeichnis.
    Gibt ein Dictionary zurück, bei dem Schlüssel die Sprachcodes und Werte die Übersetzungs-Dictionaries sind.
    """
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
    """
    Sammelt alle eindeutigen Übersetzungsschlüssel aus allen geladenen Sprachen.
    Gibt eine sortierte Liste von Schlüsseln zurück.
    """
    all_keys = set()
    for lang_code, lang_data in translations_dict.items():
        all_keys.update(lang_data.keys())
    return sorted(list(all_keys))


def generate_markdown_table(translations_dict, unique_keys, lang_codes):
    """
    Generiert eine Markdown-Tabelle mit den Übersetzungen.
    """
    if not translations_dict or not unique_keys or not lang_codes:
        return "Keine Daten zum Generieren der Tabelle vorhanden."

    header = "| Key | " + " | ".join(lang_codes) + " |"
    separator = "| --- | " + " | ".join(["---"] * len(lang_codes)) + " |"

    table_rows = [header, separator]

    for key in unique_keys:
        escaped_key = key.replace("|", "\\|")
        row_values = [escaped_key]

        for lang_code in lang_codes:
            translation = translations_dict.get(lang_code, {}).get(key, "*N/A*")
            processed_translation = translation.replace("\n", "<br>")
            processed_translation = processed_translation.replace("|", "\\|")

            if "{" in translation or "}" in translation:
                processed_translation = processed_translation.replace("`", "\\`")
                processed_translation = f"`{processed_translation}`"

            row_values.append(processed_translation)
        table_rows.append("| " + " | ".join(row_values) + " |")

    return "\n".join(table_rows)


def main():
    locales_directory = DEFAULT_LOCALES_DIR
    all_translations = load_all_translations(locales_directory)

    if not all_translations:
        print("Keine Übersetzungen gefunden. Tabelle kann nicht erstellt werden.")
        return

    language_codes = list(all_translations.keys())
    unique_translation_keys = get_all_unique_keys(all_translations)

    if not unique_translation_keys:
        print("Keine Übersetzungsschlüssel gefunden.")
        return

    markdown_table = generate_markdown_table(all_translations, unique_translation_keys, language_codes)

    output_filename = os.path.join(locales_directory, "translations_overview.md")
    try:
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write(f"# Übersetzungstabelle ({', '.join(language_codes)})\n\n")
            f.write(markdown_table)
        print(f"\nTabelle wurde erfolgreich in '{output_filename}' gespeichert.")
    except IOError as e:
        print(f"\nFehler beim Schreiben der Tabelle in die Datei '{output_filename}': {e}")


if __name__ == "__main__":
    main()
