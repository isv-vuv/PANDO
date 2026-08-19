"""Standalone runner for the independent QmlMaterial frontend."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Callable, Iterable

from core.app.app_core.project import tool_root
from core.app.app_qml.bootstrap import (
    QtBootstrapError,
    ensure_qgis_application,
    find_qgis_contents_dir,
    find_qgis_prefix_path,
)
from core.app.app_qml.bridge import QmlAppBridge


QML_DIR = Path(__file__).resolve().parent
QML_MATERIAL_ENV = "QML_MATERIAL_IMPORT_PATH"


def qgis_qml_import_paths(qgis_prefix_path: str | None) -> list[str]:
    paths: list[str] = []
    contents_dir = find_qgis_contents_dir(qgis_prefix_path or "") if qgis_prefix_path else None
    if contents_dir:
        paths.extend(
            [
                os.path.join(contents_dir, "Qt6", "qml"),
                os.path.join(contents_dir, "qml"),
            ]
        )
    if qgis_prefix_path:
        normalized_prefix = os.path.normpath(qgis_prefix_path)
        prefix_parent = os.path.dirname(normalized_prefix)
        install_root = (
            os.path.dirname(prefix_parent)
            if os.path.basename(prefix_parent).lower() == "apps"
            else normalized_prefix
        )
        paths.extend(
            [
                os.path.join(install_root, "apps", "qt6", "qml"),
                os.path.join(install_root, "qml"),
            ]
        )
    env_paths = os.environ.get("QML2_IMPORT_PATH") or os.environ.get("QML_IMPORT_PATH") or ""
    paths.extend(path for path in env_paths.split(os.pathsep) if path)
    material_paths = os.environ.get(QML_MATERIAL_ENV, "")
    paths.extend(path for path in material_paths.split(os.pathsep) if path)
    paths.extend(
        [
            str(Path(tool_root()) / "third_party" / "QmlMaterial" / "qml"),
            str(Path(tool_root()) / "third_party" / "QmlMaterial" / "lib" / "qml"),
            str(Path(tool_root()) / "build" / "qml-material" / "qml"),
            str(Path(tool_root()) / "build" / "qml-material" / "qml_modules"),
        ]
    )
    return _existing_unique_paths(paths)


def _existing_unique_paths(paths: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for path in paths:
        normalized = os.path.normpath(path)
        if normalized in seen or not os.path.isdir(normalized):
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def qml_module_available(import_paths: Iterable[str], module_name: str) -> bool:
    module_path = os.path.join(*module_name.split("."))
    for import_path in import_paths:
        if os.path.exists(os.path.join(import_path, module_path, "qmldir")):
            return True
    return False


def import_qml_runtime(import_module: Callable[[str], object] = importlib.import_module):
    """Import QML classes from QGIS' PyQt shim or the underlying PyQt6 package."""
    errors: list[str] = []

    try:
        q_url = import_module("qgis.PyQt.QtCore").QUrl
    except Exception as exc:  # pragma: no cover - depends on QGIS runtime.
        errors.append(f"qgis.PyQt.QtCore: {exc}")
        try:
            q_url = import_module("PyQt6.QtCore").QUrl
        except Exception as fallback_exc:  # pragma: no cover - depends on QGIS runtime.
            errors.append(f"PyQt6.QtCore: {fallback_exc}")
            raise QtBootstrapError(
                "QML runtime could not be imported. Start this prototype with the "
                "QGIS 4 Python environment. Original errors: " + "; ".join(errors)
            ) from fallback_exc

    for module_name in ("qgis.PyQt.QtQml", "PyQt6.QtQml"):
        try:
            qml_module = import_module(module_name)
            return q_url, qml_module.QQmlApplicationEngine
        except Exception as exc:  # pragma: no cover - depends on QGIS runtime.
            errors.append(f"{module_name}: {exc}")

    raise QtBootstrapError(
        "QML runtime could not be imported. QGIS 4 may expose QtCore through "
        "qgis.PyQt while leaving QtQml available only through PyQt6. Tried both "
        "paths. Original errors: " + "; ".join(errors)
    )


def run_qml_application(argv: list[str] | None = None, *, qgis_prefix_path: str | None = None) -> int:
    effective_prefix = qgis_prefix_path or os.environ.get("QGIS_PREFIX_PATH") or find_qgis_prefix_path()
    import_paths = qgis_qml_import_paths(effective_prefix)
    qml_material_available = qml_module_available(import_paths, "Qcm.Material")
    if not qml_material_available:
        raise QtBootstrapError(
            "QmlMaterial (Qcm.Material) was not found. Build/install "
            "https://github.com/hypengw/QmlMaterial for the Qt version shipped "
            f"with QGIS and set {QML_MATERIAL_ENV} to the directory containing "
            "Qcm/Material/qmldir."
        )

    context = ensure_qgis_application(argv or sys.argv, qgis_prefix_path=qgis_prefix_path)
    try:
        QUrl, QQmlApplicationEngine = import_qml_runtime()
    except QtBootstrapError:
        context.shutdown()
        raise

    engine = QQmlApplicationEngine()
    for import_path in import_paths:
        engine.addImportPath(import_path)

    bridge = QmlAppBridge(qml_material_available=qml_material_available)
    engine.rootContext().setContextProperty("bridge", bridge)

    qml_file = QML_DIR / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file)))
    if not engine.rootObjects():
        context.shutdown()
        return 1

    try:
        return context.exec()
    finally:
        context.shutdown()


def main() -> int:
    try:
        return run_qml_application(sys.argv)
    except QtBootstrapError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
