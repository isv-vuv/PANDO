"""QGIS application bootstrap owned by the QML frontend."""

from __future__ import annotations

import glob
import os
import platform
import re
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

try:
    from qgis.PyQt.QtWidgets import QApplication
    from qgis.core import QgsApplication

    QGIS_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only outside QGIS Python
    QApplication = QgsApplication = None
    QGIS_IMPORT_ERROR = exc


class QtBootstrapError(RuntimeError):
    pass


@dataclass
class QmlAppContext:
    app: object
    qgis_initialized: bool
    qgis_prefix_path: Optional[str]

    def exec(self) -> int:
        return self.app.exec()

    def shutdown(self) -> None:
        if self.qgis_initialized and QgsApplication is not None:
            QgsApplication.exitQgis()


def ensure_qgis_application(
    argv: Optional[Sequence[str]] = None,
    *,
    qgis_prefix_path: Optional[str] = None,
) -> QmlAppContext:
    if QGIS_IMPORT_ERROR is not None:
        raise QtBootstrapError(
            "qgis.PyQt could not be imported. Start this application with "
            f"python-qgis.bat. Original error: {QGIS_IMPORT_ERROR}"
        )
    existing = QApplication.instance()
    if existing is not None:
        return QmlAppContext(existing, False, qgis_prefix_path)
    prefix = normalize_qgis_prefix_path(qgis_prefix_path or find_qgis_prefix_path() or "")
    if not prefix or not is_valid_qgis_prefix(prefix):
        raise QtBootstrapError(
            "No valid QGIS installation could be found. Set QGIS_PREFIX_PATH "
            "to the QGIS installation prefix."
        )
    plugin_path = configure_qgis_environment(prefix)
    QgsApplication.setPrefixPath(prefix, True)
    if plugin_path and hasattr(QgsApplication, "setPluginPath"):
        QgsApplication.setPluginPath(plugin_path)
    app = QgsApplication(list(argv or sys.argv), True)
    app.setApplicationName("PANDO")
    app.setOrganizationName("PANDO")
    QgsApplication.initQgis()
    try:
        from processing.core.Processing import Processing

        Processing.initialize()
    except ImportError as exc:
        QgsApplication.exitQgis()
        raise QtBootstrapError("QGIS Processing could not be initialized.") from exc
    return QmlAppContext(app, True, prefix)


def find_qgis_prefix_path() -> Optional[str]:
    candidates = [os.environ.get("QGIS_PREFIX_PATH")]
    if platform.system() == "Windows":
        candidates.extend(glob.glob(r"C:\Program Files\QGIS *\apps\qgis"))
        candidates.extend(glob.glob(r"C:\OSGeo4W*\apps\qgis*"))
    elif platform.system() == "Darwin":
        candidates.extend(glob.glob("/Applications/QGIS*.app"))
        candidates.extend(glob.glob("/opt/homebrew/Caskroom/qgis/*/QGIS*.app"))
    else:
        candidates.extend(("/usr/local", "/usr"))
    valid = []
    for candidate in candidates:
        if candidate:
            normalized = normalize_qgis_prefix_path(candidate)
            if is_valid_qgis_prefix(normalized):
                valid.append(normalized)
    return sorted(valid, key=_candidate_sort_key, reverse=True)[0] if valid else None


def normalize_qgis_prefix_path(path: str) -> str:
    normalized = os.path.abspath(os.path.expanduser(path)) if path else ""
    contents = find_qgis_contents_dir(normalized)
    if contents:
        bundle = os.path.dirname(contents)
        if bundle.endswith(".app"):
            return os.path.realpath(bundle)
    return os.path.realpath(normalized) if normalized else ""


def find_qgis_contents_dir(prefix_path: str) -> Optional[str]:
    normalized = os.path.normpath(prefix_path)
    base = os.path.basename(normalized)
    parent = os.path.dirname(normalized)
    if base.endswith(".app"):
        return os.path.join(normalized, "Contents")
    if base == "MacOS":
        return parent
    if base == "Contents":
        return normalized
    if base == "qgis" and os.path.basename(parent) == "Resources":
        return os.path.dirname(parent)
    return None


def is_valid_qgis_prefix(prefix_path: str) -> bool:
    return bool(
        prefix_path
        and os.path.isdir(prefix_path)
        and find_qgis_resources_path(prefix_path)
        and find_qgis_plugin_path(prefix_path)
    )


def configure_qgis_environment(prefix_path: str) -> Optional[str]:
    os.environ["QGIS_PREFIX_PATH"] = prefix_path
    resources = find_qgis_resources_path(prefix_path)
    proj_path = os.path.join(resources, "proj") if resources else os.path.join(prefix_path, "proj")
    if os.path.isfile(os.path.join(proj_path, "proj.db")):
        os.environ["PROJ_DATA"] = proj_path
        os.environ["PROJ_LIB"] = proj_path
    python_plugins = os.path.join(resources, "python", "plugins") if resources else ""
    if python_plugins and os.path.isdir(python_plugins) and python_plugins not in sys.path:
        sys.path.insert(0, python_plugins)
    plugin_path = find_qgis_plugin_path(prefix_path)
    if plugin_path:
        os.environ["QGIS_PLUGINPATH"] = _prepend_env("QGIS_PLUGINPATH", plugin_path)
        os.environ["QT_PLUGIN_PATH"] = _prepend_env("QT_PLUGIN_PATH", os.path.dirname(plugin_path))
    return plugin_path


def find_qgis_resources_path(prefix_path: str) -> Optional[str]:
    contents = find_qgis_contents_dir(prefix_path)
    candidates = [
        os.path.join(contents, "Resources", "qgis") if contents else "",
        prefix_path if os.path.basename(prefix_path).lower() == "qgis" else "",
        os.path.join(prefix_path, "share", "qgis"),
    ]
    for candidate in candidates:
        if candidate and (
            os.path.isfile(os.path.join(candidate, "proj", "proj.db"))
            or os.path.isfile(os.path.join(candidate, "resources", "qgis.db"))
        ):
            return candidate
    return None


def find_qgis_plugin_path(prefix_path: str) -> Optional[str]:
    contents = find_qgis_contents_dir(prefix_path)
    candidates = [
        os.path.join(contents, "PlugIns", "qgis") if contents else "",
        os.path.join(prefix_path, "plugins"),
        os.path.join(prefix_path, "lib", "qgis", "plugins"),
        os.path.join(prefix_path, "lib64", "qgis", "plugins"),
    ]
    for candidate in candidates:
        if _has_wms_provider(candidate):
            return candidate
    return None


def _has_wms_provider(path: str) -> bool:
    try:
        names = os.listdir(path) if path else []
    except OSError:
        return False
    return any("provider_wms" in name.lower() or "wmsprovider" in name.lower() for name in names)


def _prepend_env(name: str, path: str) -> str:
    existing = os.environ.get(name, "")
    parts = [part for part in existing.split(os.pathsep) if part]
    return existing if path in parts else os.pathsep.join([path, *parts])


def _candidate_sort_key(path: str) -> tuple:
    match = re.search(r"(\d+)[._](\d+)(?:[._](\d+))?", path.lower())
    version = tuple(int(value or 0) for value in match.groups()) if match else (0, 0, 0)
    return ("final" in path.lower(), version, path.lower())
