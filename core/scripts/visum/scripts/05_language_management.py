## @package language_management
# @brief Language management system for the DLNT application
#
# This class provides multilingual functionality for the Direct-Line Network Tool.
# It handles dynamic management and switching of different languages throughout
# the user interface and all text outputs.
#
# Core functionalities:
# - Language resource file management
# - Runtime language switching
# - Fallback mechanism for missing translations
#
# Supported languages:
# - German (default)
# - English
#
# Language file format:
# - JSON format
# - Excel format
#
# @note Missing translations will fall back to default language
# @author MaS, based on code and ideas from Ali M.



import logging
from pathlib import Path
import pandas as pd

## @class Translator
# @brief Translation class to allow the user to choose the language
class Translator:
    ## @brief Initializes the translator with the specified language.
    def __init__(self, dict_path=None, language: str="en"):
        self.translations = dict()
        self._selected_language = language

        if dict_path is None or not Path(dict_path).exists():
            script_dir = Path(__file__).resolve().parent
            candidates = [
                script_dir.parent / "helper_files" / "translations.json",
                script_dir.parent / "helper_files" / "Translations.json",
                script_dir / "translations.json",
                script_dir / "Translations.json",
                Path.cwd() / "translations.json",
                Path.cwd() / "Translations.json",
            ]
            for cand in candidates:
                if cand.exists():
                    dict_path = cand
                    break

        self.excel_path = Path(dict_path) if dict_path and Path(dict_path).exists() else None
        self.load_translations()
        self.update_selected_language(language)

    ## @brief Loads translations from the Excel or JSON file.
    def load_translations(self):
        if not self.excel_path or not self.excel_path.exists():
            logging.debug(f"Translation file does not exist: {self.excel_path}")
            return

        try:
            if self.excel_path.suffix == ".json":
                df = pd.read_json(self.excel_path, orient="index")
            elif self.excel_path.suffix == ".xlsx":
                df = pd.read_excel(self.excel_path, header=0, index_col=0)
            else:
                logging.error(f"Unsupported file format for translations: {self.excel_path}")
                return

            self.translations = df.to_dict()
        except Exception as e:
            logging.warning(f"Could not load translations from {self.excel_path}: {e}")

    ## @brief Translates a key to the selected language.
    def translate(self, key):
        if not self._selected_language or self._selected_language not in self.translations:
            return key
        return self.translations.get(self._selected_language, {}).get(key, key)

    ## @brief Updates the selected language.
    def update_selected_language(self, language):
        if language in self.translations:
            self._selected_language = language
        else:
            self._selected_language = language


    ## @brief Getter method for the selected language (optional, but good practice).
    # @return The currently selected language code.
    def get_selected_language(self):
        return self._selected_language
