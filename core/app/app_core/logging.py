"""Centralized clean logging system for PANDO (GUI, QGIS, Visum, CLI)."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import warnings
from pathlib import Path
from typing import Callable, Optional

PANDO_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
PANDO_DATE_FORMAT = "%H:%M:%S"

# Noisy warning patterns to filter out of console & GUI logs
IGNORE_LOG_PATTERNS = (
    r"Warning 1: Field .* truncated",
    r"Warning 6: Normalized/laundered field name",
    r"Warning 1: Value '.*' of field .* truncated",
    r"ERROR 1: .*, band \d+: Failed to compute statistics",
    r"DeprecationWarning: QgsVectorFileWriter",
    r"Requirement already satisfied",
    r"\[notice\] A new release of pip is available",
    r"\[notice\] To update, run:",
    r"Created [\d,.]+\s+records",
)

_IGNORE_LOG_REGEX = re.compile("|".join(IGNORE_LOG_PATTERNS), re.IGNORECASE)


def should_ignore_log_message(message: str) -> bool:
    """Returns True if the log message is a noisy/irrelevant library warning."""
    if not message:
        return True
    return bool(_IGNORE_LOG_REGEX.search(message))


class ConsoleMilestoneFilter(logging.Filter):
    """Filters console (terminal) stream to show only key milestones, warnings, and errors.

    The GUI log widget and log files continue to receive full, complete logs.
    """

    NOISY_SUBPATTERNS = (
        r"^created\s+\d+[\d,.]*\s+records",
        r"^\d+\s+link types updated",
        r"^kategorie\s+'.*':",
        r"^clustere\s+knotentyp",
        r"^vfs_\d+:\s+the neighborhood degree",
        r"^starting the export of",
        r"^rin_vfs_.*:\s+was read",
        r"^matrix\s+rin_vfs_.*saved",
        r"^demand segment\s+",
        r"^linked\s+'cfl",
        r"^\[operation\]\s+set",
        r"^ausschnitts-grafikparameter",
        r"^grafikparameter gespeichert",
        r"^filter\s+'.*'\s+geladen",
        r"^wende filter-datei an",
    )
    _NOISY_CONSOLE_REGEX = re.compile("|".join(NOISY_SUBPATTERNS), re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        msg = record.getMessage()
        if should_ignore_log_message(msg):
            return False
        if self._NOISY_CONSOLE_REGEX.search(msg.strip()):
            return False
        return True


class GuiLogHandler(logging.Handler):
    """Custom logging handler that routes formatted log records to GUI callbacks."""

    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if should_ignore_log_message(msg):
                return
            formatted_msg = format_pando_log(msg, level=record.levelname)
            self.callback(formatted_msg)
        except Exception:
            self.handleError(record)


def clean_log_message(message: str) -> str:
    """Removes ugly ASCII borders, dividers, timestamps/level prefixes, and emojis from log strings."""
    if not message:
        return ""
    is_inline = message.startswith("PROGRESS_INLINE:")
    if is_inline:
        message = message[len("PROGRESS_INLINE:"):].strip()
    text = message.strip()

    # Strip existing standard timestamp / level prefixes if present
    # e.g. "2026-08-04 08:56:55,353 - INFO - Lese Bezirke..." or "08:56:55 - INFO - Lese Bezirke..."
    text = re.sub(
        r"^(?:\d{4}-\d{2}-\d{2}\s+)?\d{2}:\d{2}:\d{2}(?:,\d+)?\s*-\s*(?:INFO|WARNING|WARN|ERROR|DEBUG|CRITICAL)\s*-\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Strip existing bracketed format e.g. "[08:56:55] [INFO] Lese Bezirke..."
    text = re.sub(
        r"^\[\d{2}:\d{2}:\d{2}\]\s*\[(?:INFO|WARNING|WARN|ERROR|DEBUG|CRITICAL)\]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Strip leading/trailing decorative lines like '===', '---', '***'
    text = text.strip(" =#*~\n\t")
    # Replace any internal decorative line blocks
    lines = [line.strip(" =#*~\t") for line in text.splitlines() if line.strip(" =#*~\t")]
    cleaned = " ".join(lines)
    # Strip unwanted raw unicode emojis (e.g. ❌, ⚠️)
    cleaned = re.sub(r'[❌⚠️]', '', cleaned)
    res = cleaned.strip()
    return f"PROGRESS_INLINE:{res}" if is_inline and res else res


def format_pando_log(message: str, level: str = "INFO") -> str:
    """Format a raw log string with standard timestamp and level tag."""
    if not message:
        return ""

    if message.startswith("PROGRESS_INLINE:"):
        inner = message[len("PROGRESS_INLINE:"):].strip()
        return f"PROGRESS_INLINE:{format_pando_log(inner, level=level)}"

    # If already formatted cleanly like "[08:56:55] [INFO] Message", preserve it
    if re.match(r"^\[\d{2}:\d{2}:\d{2}\]\s*\[[A-Z]+\]\s+", message.strip()):
        return message.strip()

    # Try extracting timestamp from beginning of raw string if available (e.g. 2026-08-04 08:56:55 or 08:56:55)
    time_match = re.match(r"^\s*(?:\d{4}-\d{2}-\d{2}\s+)?(\d{2}:\d{2}:\d{2})", message)
    if time_match:
        now_str = time_match.group(1)
    else:
        now_str = time.strftime(PANDO_DATE_FORMAT)

    cleaned = clean_log_message(message)
    if not cleaned:
        return ""
    return f"[{now_str}] [{level.upper()}] {cleaned}"


def add_project_file_handler(
    logger: logging.Logger,
    project_path: str | Path,
    level: int = logging.INFO,
) -> Optional[str]:
    """Attach FileHandlers targeting [project_path]/pipeline.log and [project_path]/temp/pando.log."""
    if not project_path:
        return None
    try:
        project_dir = Path(project_path)
        temp_dir = project_dir / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        log_files = [project_dir / "pipeline.log", temp_dir / "pando.log"]

        first_file = str(log_files[0])
        for log_file in log_files:
            log_path = os.path.abspath(str(log_file))
            already_attached = any(
                isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == log_path
                for h in logger.handlers
            )
            if not already_attached:
                file_handler = logging.FileHandler(log_path, encoding="utf-8")
                file_handler.setLevel(level)
                formatter = logging.Formatter(PANDO_LOG_FORMAT, datefmt=PANDO_DATE_FORMAT)
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
        return first_file
    except Exception as exc:
        sys.stderr.write(f"Could not initialize project log file: {exc}\n")
        return None


def setup_pando_logger(
    name: str = "PANDO",
    project_path: Optional[str | Path] = None,
    gui_callback: Optional[Callable[[str], None]] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure or retrieve a logger with standard clean PANDO formatting.

    If name is "" or None, configures the root logger so all sub-modules propagate logs to GUI.
    """
    # Quiet GDAL C-level logging noise
    os.environ["CPL_LOG"] = "OFF"
    os.environ["GDAL_PAM_ENABLED"] = "NO"
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    target_name = name if name is not None else ""
    logger = logging.getLogger(target_name)
    logger.setLevel(level)

    # For root logger or named logger, manage handlers
    if target_name != "":
        logger.propagate = True

    # Remove existing handlers to avoid duplicates
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    # Console Handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    formatter = logging.Formatter(PANDO_LOG_FORMAT, datefmt=PANDO_DATE_FORMAT)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(ConsoleMilestoneFilter())
    logger.addHandler(console_handler)

    # GUI Handler (if GUI callback provided)
    if gui_callback is not None:
        gui_handler = GuiLogHandler(gui_callback)
        gui_handler.setLevel(level)
        gui_handler.setFormatter(formatter)
        logger.addHandler(gui_handler)

    # File Handler (if project_path provided)
    if project_path:
        add_project_file_handler(logger, project_path, level=level)

    return logger
