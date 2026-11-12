"""Resolve Visum automation and user files from the registered default version."""

import os
import re
from pathlib import Path


def get_visum_progid():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Visum.Visum\CurVer") as key:
            progid = winreg.QueryValueEx(key, "")[0]
    except OSError as exc:
        raise RuntimeError(
            "Keine registrierte Visum-COM-Version gefunden. Bitte die Visum-Installation "
            "über das PTV-Setup reparieren bzw. die COM-Registrierung prüfen."
        ) from exc
    if not re.fullmatch(r"Visum\.Visum(?:-64)?\.\d{2,4}", progid):
        raise RuntimeError(f"Ungültige registrierte Visum-Version: {progid!r}")
    return progid


def get_visum_appdata_dir():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("Umgebungsvariable APPDATA ist nicht vorhanden.")
    version = get_visum_progid().rsplit(".", 1)[1]
    return Path(appdata) / "PTV Vision" / f"PTV Visum {2000 + int(version[:2])}"


def create_visum():
    import logging
    import win32com.client as com

    progid = get_visum_progid()
    logging.info("Verwende registrierte Visum-COM-Klasse: %s", progid)
    try:
        return com.Dispatch(progid)
    except Exception as exc:
        raise RuntimeError(
            f"Visum konnte über {progid} nicht gestartet werden: {exc}. "
            "Bitte Visum-Installation und COM-Registrierung prüfen."
        ) from exc
