"""Shared Qt/PyQGIS bootstrap, dialogs, and styling helpers.

This module is intentionally independent from the current legacy UI. New Qt
windows should import Qt classes and application helpers from here so the final
standalone PyQGIS app has one place for startup, shutdown, dialogs, and theme
behavior.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import sys
import traceback
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Union


try:
    from qgis.PyQt import QtCore, QtGui, QtWidgets
    from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
    from qgis.PyQt.QtGui import QColor, QCursor, QFont, QIcon, QPalette
    from qgis.PyQt.QtWidgets import (
        QApplication,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSizePolicy,
        QStatusBar,
        QStyleFactory,
        QVBoxLayout,
        QWidget,
    )
    from qgis.core import QgsApplication

    QGIS_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only hit outside QGIS Python.
    QtCore = QtGui = QtWidgets = None
    Qt = QTimer = pyqtSignal = None
    QColor = QCursor = QFont = QIcon = QPalette = None
    QApplication = QFileDialog = QFrame = QHBoxLayout = QLabel = QMainWindow = None
    QMessageBox = QPushButton = QSizePolicy = QStatusBar = QStyleFactory = None
    QVBoxLayout = QWidget = None
    QgsApplication = None
    QGIS_IMPORT_ERROR = exc


def escape_mnemonic(text: str) -> str:
    """Escapes ampersands in string for Qt widgets so '&' is rendered literally instead of triggering a keyboard shortcut mnemonic."""
    if not text or not isinstance(text, str):
        return text
    return text.replace("&&", "\x00").replace("&", "&&").replace("\x00", "&&")


APP_NAME = "PANDO"
APP_VERSION = "Qt Migration"
ORG_NAME = "PANDO"


class QtBootstrapError(RuntimeError):
    """Raised when the standalone Qt/PyQGIS application cannot be initialized."""


@dataclass
class QtAppContext:
    app: object
    owns_app: bool
    qgis_initialized: bool
    qgis_prefix_path: Optional[str] = None

    def exec(self) -> int:
        return self.app.exec()

    def shutdown(self) -> None:
        if self.qgis_initialized and QgsApplication is not None:
            QgsApplication.exitQgis()


def require_qgis_qt() -> None:
    if QGIS_IMPORT_ERROR is None:
        return
    raise QtBootstrapError(
        "qgis.PyQt could not be imported. Start this application with the "
        "QGIS Python environment, not a generic system Python interpreter.\n"
        f"Original error: {QGIS_IMPORT_ERROR}"
    )


def find_qgis_prefix_path() -> Optional[str]:
    """Return a validated QGIS installation prefix for the current platform."""
    env_path = os.environ.get("QGIS_PREFIX_PATH")
    candidates = [env_path] if env_path else []
    candidates.extend(discover_qgis_prefix_candidates())

    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        normalized = normalize_qgis_prefix_path(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if is_valid_qgis_prefix(normalized):
            return normalized
    return None


def discover_qgis_prefix_candidates() -> list[str]:
    """Discover likely QGIS prefixes without depending on a specific release."""
    candidates = []
    executable_bundle = qgis_app_bundle_from_path(sys.executable)
    if executable_bundle:
        candidates.append(executable_bundle)

    system = platform.system()
    if system == "Darwin":
        mac_candidates = glob.glob("/Applications/QGIS*.app")
        mac_candidates.extend(glob.glob("/opt/homebrew/Caskroom/qgis/*/QGIS*.app"))
        candidates.extend(sorted(mac_candidates, key=_qgis_candidate_sort_key, reverse=True))
    elif system == "Windows":
        candidates.extend(glob.glob(r"C:\Program Files\QGIS *"))
        candidates.extend(glob.glob(r"C:\OSGeo4W*\apps\qgis*"))
    else:
        candidates.extend(("/usr/local", "/usr"))
    return candidates


def normalize_qgis_prefix_path(path: str) -> str:
    """Normalize a QGIS bundle subpath to the prefix expected by QGIS."""
    normalized = os.path.abspath(os.path.expanduser(path))
    contents_dir = find_qgis_contents_dir(normalized)
    if contents_dir:
        app_bundle = os.path.dirname(contents_dir)
        if app_bundle.endswith(".app"):
            return os.path.realpath(app_bundle)
    return os.path.realpath(normalized)


def qgis_app_bundle_from_path(path: str) -> Optional[str]:
    """Return the containing macOS app bundle for a path, if present."""
    current = os.path.abspath(os.path.expanduser(path))
    while current and current != os.path.dirname(current):
        if current.endswith(".app"):
            return current
        current = os.path.dirname(current)
    return None


def is_valid_qgis_prefix(prefix_path: str) -> bool:
    """Check that a prefix contains QGIS resources and the WMS provider."""
    if not os.path.isdir(prefix_path):
        return False
    return bool(find_qgis_resources_path(prefix_path) and find_qgis_plugin_path(prefix_path))


def _qgis_candidate_sort_key(path: str) -> tuple:
    normalized = path.lower()
    version_match = re.search(r"(\d+)[._](\d+)(?:[._](\d+))?", normalized)
    version = tuple(int(part or 0) for part in version_match.groups()) if version_match else (0, 0, 0)
    return ("final" in normalized, version, normalized)


def ensure_qgis_application(
    argv: Optional[Sequence[Union[str, bytes]]] = None,
    *,
    app_name: str = APP_NAME,
    qgis_prefix_path: Optional[str] = None,
    gui: bool = True,
) -> QtAppContext:
    """Create or reuse the QGIS Qt application for standalone PyQGIS use."""
    require_qgis_qt()

    args = list(argv if argv is not None else sys.argv)
    existing_app = QApplication.instance()
    if existing_app is not None:
        configure_application_metadata(existing_app, app_name)
        return QtAppContext(existing_app, owns_app=False, qgis_initialized=False)

    requested_prefix = qgis_prefix_path or find_qgis_prefix_path()
    prefix_path = normalize_qgis_prefix_path(requested_prefix) if requested_prefix else None
    if not prefix_path or not is_valid_qgis_prefix(prefix_path):
        env_path = os.environ.get("QGIS_PREFIX_PATH", "<not set>")
        raise QtBootstrapError(
            "No valid QGIS installation could be found.\n"
            f"QGIS_PREFIX_PATH: {env_path}\n"
            "The selected prefix must contain QGIS resources and the WMS provider. "
            "Set QGIS_PREFIX_PATH to the QGIS installation root (the .app bundle on macOS)."
        )

    plugin_path = configure_qgis_environment(prefix_path)
    QgsApplication.setPrefixPath(prefix_path, True)
    if plugin_path and hasattr(QgsApplication, "setPluginPath"):
        QgsApplication.setPluginPath(plugin_path)

    app = _create_qgs_application(args, gui)
    configure_application_metadata(app, app_name)
    QgsApplication.initQgis()
    initialize_processing()
    return QtAppContext(app, owns_app=True, qgis_initialized=True, qgis_prefix_path=prefix_path)


def initialize_processing() -> None:
    """Initialize QGIS Processing providers once for model/native algorithms."""
    try:
        from processing.core.Processing import Processing
    except ImportError as exc:
        raise QtBootstrapError(
            "QGIS Processing could not be imported from the selected QGIS installation."
        ) from exc
    Processing.initialize()


def _create_qgs_application(args: Sequence[Union[str, bytes]], gui: bool) -> object:
    try:
        return QgsApplication(list(args), gui)
    except TypeError as exc:
        if "expected bytes, str found" not in str(exc):
            raise
        return QgsApplication(_encode_argv(args), gui)


def _encode_argv(args: Sequence[Union[str, bytes]]) -> list[bytes]:
    encoding = sys.getfilesystemencoding() or "utf-8"
    encoded_args = []
    for arg in args:
        if isinstance(arg, bytes):
            encoded_args.append(arg)
        else:
            encoded_args.append(str(arg).encode(encoding, errors="surrogateescape"))
    return encoded_args


def configure_qgis_environment(prefix_path: str) -> Optional[str]:
    os.environ["QGIS_PREFIX_PATH"] = prefix_path

    resources_path = find_qgis_resources_path(prefix_path)
    proj_path = os.path.join(resources_path, "proj") if resources_path else os.path.join(prefix_path, "proj")
    gdal_data_path = find_gdal_data_path(prefix_path, resources_path)
    python_plugins_path = os.path.join(resources_path, "python", "plugins") if resources_path else ""
    if python_plugins_path and os.path.isdir(python_plugins_path) and python_plugins_path not in sys.path:
        sys.path.insert(0, python_plugins_path)

    if os.path.exists(os.path.join(proj_path, "proj.db")):
        os.environ["PROJ_DATA"] = proj_path
        os.environ["PROJ_LIB"] = proj_path
    if gdal_data_path:
        os.environ["GDAL_DATA"] = gdal_data_path

    plugin_path = find_qgis_plugin_path(prefix_path)
    if plugin_path:
        os.environ["QGIS_PLUGINPATH"] = prepend_env_path("QGIS_PLUGINPATH", plugin_path)
        qt_plugin_root = os.path.dirname(plugin_path)
        os.environ["QT_PLUGIN_PATH"] = prepend_env_path("QT_PLUGIN_PATH", qt_plugin_root)
    return plugin_path


def prepend_env_path(name: str, path: str) -> str:
    existing = os.environ.get(name)
    if not existing:
        return path
    parts = existing.split(os.pathsep)
    if path in parts:
        return existing
    return os.pathsep.join([path, existing])


def find_qgis_plugin_path(prefix_path: str) -> Optional[str]:
    contents_dir = find_qgis_contents_dir(prefix_path)
    candidates = [
        os.path.join(contents_dir, "PlugIns", "qgis") if contents_dir else "",
        os.path.join(os.path.dirname(prefix_path), "PlugIns", "qgis"),
        os.path.join(prefix_path, "plugins"),
        os.path.join(prefix_path, "lib", "qgis", "plugins"),
        os.path.join(prefix_path, "lib64", "qgis", "plugins"),
    ]
    for candidate in candidates:
        if _directory_has_wms_provider(candidate):
            return candidate
    return None


def _directory_has_wms_provider(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        names = os.listdir(path)
    except OSError:
        return False
    return any(
        "provider_wms" in name.lower() or "wmsprovider" in name.lower()
        for name in names
    )


def find_qgis_resources_path(prefix_path: str) -> Optional[str]:
    contents_dir = find_qgis_contents_dir(prefix_path)
    candidates = [
        os.path.join(contents_dir, "Resources", "qgis") if contents_dir else "",
        prefix_path if os.path.basename(prefix_path) == "qgis" else "",
        os.path.join(prefix_path, "share", "qgis"),
    ]
    for candidate in candidates:
        has_proj = os.path.exists(os.path.join(candidate, "proj", "proj.db"))
        has_qgis_db = os.path.exists(os.path.join(candidate, "resources", "qgis.db"))
        if candidate and (has_proj or has_qgis_db):
            return candidate
    return None


def find_gdal_data_path(prefix_path: str, resources_path: Optional[str] = None) -> Optional[str]:
    """Find the GDAL data directory shipped with the selected QGIS runtime."""
    contents_dir = find_qgis_contents_dir(prefix_path)
    qgis_resources = resources_path or find_qgis_resources_path(prefix_path)
    candidates = [
        os.path.join(qgis_resources, "gdal") if qgis_resources else "",
        os.path.join(contents_dir, "Resources", "gdal") if contents_dir else "",
        os.path.join(prefix_path, "share", "gdal"),
        os.path.join(prefix_path, "share", "qgis", "gdal"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, "osmconf.ini")):
            return candidate
    return None


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


def configure_application_metadata(app: object, app_name: str = APP_NAME) -> None:
    app.setApplicationName(app_name)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)


def install_exception_hook(parent_getter: Optional[Callable[[], object]] = None) -> None:
    """Route unhandled exceptions into a Qt error dialog and stderr."""
    require_qgis_qt()

    def _show_exception(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb)
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        parent = parent_getter() if parent_getter else None
        Dialogs.error(parent, "Unexpected Error", message)

    sys.excepthook = _show_exception


def apply_app_theme(app: object, mode: str = "light") -> None:
    """Apply a restrained cross-platform Fusion theme."""
    require_qgis_qt()

    if "Fusion" in QStyleFactory.keys():
        app.setStyle(QStyleFactory.create("Fusion"))

    dark = mode.lower() == "dark"
    palette = QPalette()
    if dark:
        palette.setColor(qt_enum(QPalette, "Window", "ColorRole"), QColor("#202124"))
        palette.setColor(qt_enum(QPalette, "WindowText", "ColorRole"), QColor("#f1f3f4"))
        palette.setColor(qt_enum(QPalette, "Base", "ColorRole"), QColor("#17181a"))
        palette.setColor(qt_enum(QPalette, "AlternateBase", "ColorRole"), QColor("#2b2d30"))
        palette.setColor(qt_enum(QPalette, "Text", "ColorRole"), QColor("#f1f3f4"))
        palette.setColor(qt_enum(QPalette, "Button", "ColorRole"), QColor("#2b2d30"))
        palette.setColor(qt_enum(QPalette, "ButtonText", "ColorRole"), QColor("#f1f3f4"))
        palette.setColor(qt_enum(QPalette, "Highlight", "ColorRole"), QColor("#2f6fed"))
        palette.setColor(qt_enum(QPalette, "HighlightedText", "ColorRole"), QColor("#ffffff"))
    else:
        palette.setColor(qt_enum(QPalette, "Window", "ColorRole"), QColor("#f7f8fa"))
        palette.setColor(qt_enum(QPalette, "WindowText", "ColorRole"), QColor("#202124"))
        palette.setColor(qt_enum(QPalette, "Base", "ColorRole"), QColor("#ffffff"))
        palette.setColor(qt_enum(QPalette, "AlternateBase", "ColorRole"), QColor("#eef1f4"))
        palette.setColor(qt_enum(QPalette, "Text", "ColorRole"), QColor("#202124"))
        palette.setColor(qt_enum(QPalette, "Button", "ColorRole"), QColor("#ffffff"))
        palette.setColor(qt_enum(QPalette, "ButtonText", "ColorRole"), QColor("#202124"))
        palette.setColor(qt_enum(QPalette, "Highlight", "ColorRole"), QColor("#2563eb"))
        palette.setColor(qt_enum(QPalette, "HighlightedText", "ColorRole"), QColor("#ffffff"))
    app.setPalette(palette)
    app.setFont(app_font(10))


def qt_enum(owner: object, name: str, enum_group: Optional[str] = None) -> object:
    if hasattr(owner, name):
        return getattr(owner, name)
    if enum_group and hasattr(owner, enum_group):
        enum_owner = getattr(owner, enum_group)
        if hasattr(enum_owner, name):
            return getattr(enum_owner, name)
    raise AttributeError(f"{owner!r} has no Qt enum {name!r}")


def qfont_bold() -> object:
    return qt_enum(QFont, "Bold", "Weight")


def app_font(size: int = 10, weight: Optional[object] = None) -> object:
    font_family = default_font_family()
    if weight is None:
        return QFont(font_family, size)
    return QFont(font_family, size, weight)


def default_font_family() -> str:
    system_name = platform.system()
    if system_name == "Darwin":
        return "Helvetica Neue"
    if system_name == "Windows":
        return "Segoe UI"
    return "Noto Sans"


def set_busy(parent: object, busy: bool = True) -> None:
    require_qgis_qt()
    cursor = qt_enum(Qt, "WaitCursor" if busy else "ArrowCursor", "CursorShape")
    parent.setCursor(QCursor(cursor))


class Dialogs:
    @staticmethod
    def _button_texts():
        try:
            from core.locales.localizer import localizer
            yes = localizer.get_string("button_yes", default=localizer.get_string("option_yes", default="Ja"))
            no = localizer.get_string("button_no", default=localizer.get_string("option_no", default="Nein"))
            ok = localizer.get_string("button_ok", default="OK")
            return yes, no, ok
        except Exception:
            return "Ja", "Nein", "OK"

    @staticmethod
    def info(parent: object, title: str, text: str) -> None:
        require_qgis_qt()
        _yes, _no, ok_text = Dialogs._button_texts()
        box = QMessageBox(parent)
        box.setIcon(qt_enum(QMessageBox, "Information", "Icon"))
        box.setWindowTitle(title)
        box.setText(text)
        box.addButton(ok_text, qt_enum(QMessageBox, "AcceptRole", "ButtonRole"))
        box.exec_() if hasattr(box, "exec_") else box.exec()

    @staticmethod
    def warning(parent: object, title: str, text: str) -> None:
        require_qgis_qt()
        _yes, _no, ok_text = Dialogs._button_texts()
        box = QMessageBox(parent)
        box.setIcon(qt_enum(QMessageBox, "Warning", "Icon"))
        box.setWindowTitle(title)
        box.setText(text)
        box.addButton(ok_text, qt_enum(QMessageBox, "AcceptRole", "ButtonRole"))
        box.exec_() if hasattr(box, "exec_") else box.exec()

    @staticmethod
    def error(parent: object, title: str, text: str) -> None:
        require_qgis_qt()
        _yes, _no, ok_text = Dialogs._button_texts()
        box = QMessageBox(parent)
        box.setIcon(qt_enum(QMessageBox, "Critical", "Icon"))
        box.setWindowTitle(title)
        box.setText(text)
        box.addButton(ok_text, qt_enum(QMessageBox, "AcceptRole", "ButtonRole"))
        box.exec_() if hasattr(box, "exec_") else box.exec()

    @staticmethod
    def confirm(parent: object, title: str, text: str) -> bool:
        require_qgis_qt()
        yes_text, no_text, _ok = Dialogs._button_texts()
        box = QMessageBox(parent)
        box.setIcon(qt_enum(QMessageBox, "Question", "Icon"))
        box.setWindowTitle(title)
        box.setText(text)
        yes_btn = box.addButton(yes_text, qt_enum(QMessageBox, "YesRole", "ButtonRole"))
        no_btn = box.addButton(no_text, qt_enum(QMessageBox, "NoRole", "ButtonRole"))
        box.setDefaultButton(no_btn)
        box.exec_() if hasattr(box, "exec_") else box.exec()
        return box.clickedButton() == yes_btn

    @staticmethod
    def open_file(parent: object, title: str, file_filter: str = "All files (*.*)", directory: str = "") -> str:
        path, _selected_filter = QFileDialog.getOpenFileName(parent, title, directory, file_filter)
        return path

    @staticmethod
    def save_file(parent: object, title: str, file_filter: str = "All files (*.*)", directory: str = "") -> str:
        path, _selected_filter = QFileDialog.getSaveFileName(parent, title, directory, file_filter)
        return path

    @staticmethod
    def select_directory(parent: object, title: str, directory: str = "") -> str:
        return QFileDialog.getExistingDirectory(parent, title, directory)


def create_step_header(
    title_raw: str,
    current_step: int,
    total_steps: int = 6,
    localizer: Optional[object] = None,
    parent: Optional[object] = None,
) -> tuple[QWidget, QLabel, QLabel]:
    """Creates a standardized step header widget with main title and inline step indicator."""
    header_widget = QWidget(parent)
    if QtWidgets is not None:
        header_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Fixed)
    layout = QHBoxLayout(header_widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)

    if ":" in title_raw:
        main_title_text = title_raw.split(":", 1)[1].strip()
    else:
        main_title_text = title_raw.strip()

    title_label = QLabel(main_title_text, header_widget)
    title_label.setFont(app_font(18, qfont_bold()))
    title_label.setWordWrap(False)
    layout.addWidget(title_label)

    if localizer and hasattr(localizer, "get_string"):
        step_text = localizer.get_string("wizard_step_indicator", current=current_step, total=total_steps)
    else:
        step_text = f"Schritt {current_step} von {total_steps}"

    step_label = QLabel(step_text, header_widget)
    step_label.setFont(app_font(11))

    layout.addStretch(1)
    layout.addWidget(step_label, 0, qt_enum(Qt, "AlignVCenter", "AlignmentFlag"))

    return header_widget, title_label, step_label


_Q_MAIN_WINDOW_BASE = QMainWindow if QMainWindow is not None else object


class BaseMainWindow(_Q_MAIN_WINDOW_BASE):
    """Small shared base for the future migrated step windows."""

    def __init__(self, title: str = APP_NAME, parent: Optional[object] = None):
        require_qgis_qt()
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1100, 800)
        self.setStatusBar(QStatusBar(self))

        if platform.system() == "Windows":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PANDO.UrbanAct.GeofabrikTool.1.0")
            except Exception:
                pass

        logo_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "icons", "Logo_2.png"))
        if not os.path.isfile(logo_path):
            logo_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Logo_2.png"))
        if os.path.isfile(logo_path) and QIcon is not None:
            icon = QIcon(logo_path)
            self.setWindowIcon(icon)
            app = QApplication.instance()
            if app is not None:
                app.setWindowIcon(icon)

    def set_status(self, text: str) -> None:
        self.statusBar().showMessage(text)


class PlaceholderMainWindow(BaseMainWindow):
    """Temporary shell proving the standalone Qt/PyQGIS app can start."""

    def __init__(self):
        super().__init__(APP_NAME)

        root = QWidget(self)
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(16)

        title = QLabel(APP_NAME)
        title.setFont(app_font(18, qfont_bold()))
        layout.addWidget(title)

        body = QLabel(
            "Qt/PyQGIS basis is initialized. The legacy steps will be migrated "
            "into this standalone shell one by one."
        )
        body.setWordWrap(True)
        body.setSizePolicy(qt_enum(QSizePolicy, "Expanding", "Policy"), qt_enum(QSizePolicy, "Fixed", "Policy"))
        layout.addWidget(body)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        layout.addWidget(separator)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        layout.addStretch(1)

        self.setCentralWidget(root)
        self.set_status("Ready")


class AnimatedProgressBar(QtWidgets.QProgressBar if QtWidgets is not None else object):
    """QProgressBar with a smooth moving shimmer / shine sweep animation (like Windows copy/delete)."""

    def __init__(self, parent=None):
        if QtWidgets is not None:
            super().__init__(parent)
        self._is_animating = False
        self._shimmer_pos = -0.3
        self._timer = None
        if QtCore is not None:
            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(25)
            self._timer.timeout.connect(self._on_timer)

    def start_animation(self) -> None:
        self._is_animating = True
        if self._timer is not None and not self._timer.isActive():
            self._timer.start()
        if hasattr(self, "update"):
            self.update()

    def stop_animation(self) -> None:
        self._is_animating = False
        if self._timer is not None and self._timer.isActive():
            self._timer.stop()
        self._shimmer_pos = -0.3
        if hasattr(self, "update"):
            self.update()

    def _on_timer(self) -> None:
        try:
            if not self._is_animating:
                return
            if hasattr(self, "isVisible") and not self.isVisible():
                return
            self._shimmer_pos += 0.015
            if self._shimmer_pos > 1.3:
                self._shimmer_pos = -0.3
            self.update()
        except Exception:
            pass

    def paintEvent(self, event) -> None:
        if QtWidgets is None:
            return
        super().paintEvent(event)
        if not self._is_animating or QtGui is None:
            return

        rect = self.rect()
        width = rect.width()
        height = rect.height()
        if width <= 0 or height <= 0:
            return

        painter = QtGui.QPainter()
        if not painter.begin(self):
            return
        try:
            hint = qt_enum(QtGui.QPainter, "Antialiasing", "RenderHint")
            if hint is not None:
                painter.setRenderHint(hint)

            # Sweep across the entire width of the bar (including 0% / empty parts)
            sweep_w = max(60.0, width * 0.35)
            sweep_center = self._shimmer_pos * (width + sweep_w) - (sweep_w / 2.0)

            gradient = QtGui.QLinearGradient(sweep_center - sweep_w / 2.0, 0, sweep_center + sweep_w / 2.0, 0)
            gradient.setColorAt(0.0, QtGui.QColor(33, 150, 243, 0))
            gradient.setColorAt(0.5, QtGui.QColor(33, 150, 243, 175))
            gradient.setColorAt(1.0, QtGui.QColor(33, 150, 243, 0))

            painter.setClipRect(rect.adjusted(1, 1, -1, -1))
            painter.fillRect(QtCore.QRectF(0, 0, width, height), QtGui.QBrush(gradient))
        except Exception:
            pass
        finally:
            painter.end()


def run_qt_application(
    window_factory: Callable[[], object] = PlaceholderMainWindow,
    argv: Optional[Sequence[str]] = None,
    *,
    qgis_prefix_path: Optional[str] = None,
    theme: str = "light",
) -> int:
    context = ensure_qgis_application(argv, qgis_prefix_path=qgis_prefix_path)
    apply_app_theme(context.app, theme)

    window = window_factory()
    install_exception_hook(lambda: window)
    window.show()

    try:
        return context.exec()
    finally:
        context.shutdown()
