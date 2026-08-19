"""Standalone Qt/PyQGIS entry point for the migration."""

import os
import sys
import traceback
import warnings
from datetime import datetime

warnings.filterwarnings("ignore", category=ResourceWarning)

from core.app.app_qt.main_window import UrbanActQtMainWindow
from core.app.app_qt.qt_base import QtBootstrapError, run_qt_application


def _global_excepthook(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    err_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(err_text, file=sys.stderr)
    try:
        with open("app_crash.log", "a", encoding="utf-8") as f:
            f.write(f"\n--- CRASH AT {datetime.now().isoformat()} ---\n")
            f.write(err_text)
            f.flush()
    except Exception:
        pass


def main() -> int:
    sys.excepthook = _global_excepthook
    try:
        return run_qt_application(UrbanActQtMainWindow, argv=sys.argv)
    except QtBootstrapError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
