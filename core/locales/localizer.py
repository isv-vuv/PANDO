# localizer.py
import json
import os
import logging
from typing import Callable, Optional, Dict, Any

DEFAULT_LOCALES_DIR = os.path.dirname(os.path.abspath(__file__))

logger = logging.getLogger(__name__)

# --- Singleton-Instanz der Localizer-Klasse ---
class _Localizer:
    def __init__(self, locales_dir: str = DEFAULT_LOCALES_DIR, default_language: str = "de") -> None:
        self.locales_dir: str = locales_dir
        self.default_language: str = default_language
        self.languages: Dict[str, Dict[str, str]] = {}
        self.current_lang_code: str = default_language

        # App-spezifische Referenzen
        self._refresh_callback: Optional[Callable[[], None]] = None
        self._root_ref: Any = None
        self._user_agent: Optional[str] = None

        self.load_translations()

    def load_translations(self) -> None:
        self.languages.clear()
        if not os.path.isdir(self.locales_dir):
            logger.error(f"Übersetzungsverzeichnis '{self.locales_dir}' nicht gefunden.")
            self.languages[self.default_language] = {}
            return

        for filename in os.listdir(self.locales_dir):
            if filename.endswith(".json"):
                lang_code = filename[:-5]
                filepath = os.path.join(self.locales_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        self.languages[lang_code] = json.load(f)
                    logger.debug(f"Übersetzungen für '{lang_code}' aus '{filename}' geladen.")
                except json.JSONDecodeError as e:
                    logger.warning(f"Fehler beim Dekodieren von JSON in '{filepath}': {e}")
                except Exception as e:
                    logger.warning(f"Fehler beim Laden von '{filepath}': {e}")

        if self.default_language not in self.languages:
            logger.warning(f"Standardsprache '{self.default_language}' fehlt. Leeres Wörterbuch wird verwendet.")
            self.languages[self.default_language] = {}

    def initialize(self,
                   refresh_callback: Callable[[], None],
                   root_window_ref: Any,
                   user_agent_string: str,
                   locales_path: Optional[str] = None) -> None:
        if locales_path and locales_path != self.locales_dir:
            self.locales_dir = locales_path
            self.load_translations()
        self._refresh_callback = refresh_callback
        self._root_ref = root_window_ref
        self._user_agent = user_agent_string
        self.update_window_title()

    def update_window_title(self) -> None:
        if self._root_ref and self._user_agent:
            version = self._user_agent.split('/')[-1] if self._user_agent else "N/A"
            title = self.get_string("window_title", version=version)
            try:
                self._root_ref.title(title)
            except Exception as e:
                logger.error(f"Fehler beim Setzen des Fenstertitels: {e}")
                try:
                    self._root_ref.title("Methoden für die automatisierte Erstellung von Verkehrsnachfragemodellen")
                except Exception:
                    pass

    def get_string(self, key: str, default: Optional[str] = None, **kwargs) -> str:
        template = self.languages.get(self.current_lang_code, {}).get(key)
        if template is None:
            template = self.languages.get(self.default_language, {}).get(key)
        if template is None:
            if default is not None:
                try:
                    return default.format(**kwargs) if kwargs else default
                except Exception:
                    return default
            logger.warning(f"Key '{key}' nicht gefunden in '{self.current_lang_code}' oder '{self.default_language}'.")
            return f"<{key}_NOT_FOUND>"
        try:
            return template.format(**kwargs)
        except KeyError as e:
            if default is not None:
                try:
                    return default.format(**kwargs) if kwargs else default
                except Exception:
                    return default
            logger.warning(f"Platzhalter '{e.args[0]}' fehlt für Key '{key}' in Vorlage '{template}'. Args: {kwargs}")
            return f"<{key}_PLACEHOLDER_ERROR:{e.args[0]}>"
        except Exception as e:
            if default is not None:
                return default
            logger.warning(f"Fehler beim Formatieren von '{key}': {e}. Vorlage: '{template}'. Args: {kwargs}")
            return template

    def set_language(self, lang_code: str) -> None:
        if lang_code in self.languages and lang_code != self.current_lang_code:
            logger.debug(f"Sprache wird zu '{lang_code}' gewechselt.")
            self.current_lang_code = lang_code
            self.update_window_title()
            if self._refresh_callback:
                self._refresh_callback()
            else:
                logger.warning("Main-App-Refresh-Callback nicht gesetzt.")
        elif lang_code == self.current_lang_code:
            logger.debug(f"Sprache ist bereits auf '{lang_code}' gesetzt. Keine Änderung.")
        else:
            logger.warning(f"Sprache '{lang_code}' nicht verfügbar.")

    def get_current_language(self) -> str:
        return self.current_lang_code

# --- Modulweite Singleton-Instanz ---
_localizer = _Localizer()

# --- Funktions-API wie bisher ---
def load_translations(locales_dir: str = DEFAULT_LOCALES_DIR) -> None:
    _localizer.locales_dir = locales_dir
    _localizer.load_translations()

def initialize_localizer(refresh_callback: Callable[[], None],
                        root_window_ref: Any,
                        user_agent_string: str,
                        locales_path: str = DEFAULT_LOCALES_DIR) -> None:
    _localizer.initialize(refresh_callback, root_window_ref, user_agent_string, locales_path)

def update_window_title() -> None:
    _localizer.update_window_title()

def get_string(key: str, **kwargs) -> str:
    return _localizer.get_string(key, **kwargs)

def set_language(lang_code: str) -> None:
    _localizer.set_language(lang_code)

def get_current_language() -> str:
    return _localizer.get_current_language()
