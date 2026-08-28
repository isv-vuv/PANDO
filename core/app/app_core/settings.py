"""Persistent application settings for the Qt workflow."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


APP_SETTINGS_FILENAME = "settings.json"


def default_settings_dir() -> str:
    if os.name == "nt":
        base_dir = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base_dir, "PANDO")
    return os.path.join(os.path.expanduser("~"), ".pando")


def default_settings_path() -> str:
    return os.path.join(default_settings_dir(), APP_SETTINGS_FILENAME)


@dataclass
class AppSettings:
    last_workspace_path: str = ""
    language: str = "de"
    accepted_licenses: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        raw_accepted = data.get("accepted_licenses")
        accepted_dict = dict(raw_accepted) if isinstance(raw_accepted, dict) else {}
        return cls(
            last_workspace_path=str(data.get("last_workspace_path") or ""),
            language=str(data.get("language") or "de"),
            accepted_licenses={str(k): str(v) for k, v in accepted_dict.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_workspace_path": self.last_workspace_path,
            "language": self.language,
            "accepted_licenses": self.accepted_licenses,
        }

    def has_accepted_all_licenses(self, required_ids: Iterable[str]) -> bool:
        return all(key in self.accepted_licenses and bool(self.accepted_licenses[key]) for key in required_ids)


def load_app_settings(path: Optional[str] = None) -> AppSettings:
    settings_path = path or default_settings_path()
    try:
        with open(settings_path, "r", encoding="utf-8") as settings_file:
            data = json.load(settings_file)
    except FileNotFoundError:
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()
    return AppSettings.from_dict(data)


def save_app_settings(settings: AppSettings, path: Optional[str] = None) -> str:
    settings_path = path or default_settings_path()
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as settings_file:
        json.dump(settings.to_dict(), settings_file, ensure_ascii=False, indent=2, sort_keys=True)
        settings_file.write("\n")
    return settings_path
