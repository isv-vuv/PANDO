"""Backward compatibility module: forwards to step3_grid_area."""

from __future__ import annotations

from core.app.app_qt.steps.step3_grid_area import (
    Step3GridAreaWidget,
    Step3GridAreaWidget as Step2GridAreaWidget,
    FloatingMapToolbar,
    Step3SelectionMapTool,
    AREA_COLORS,
    MAX_CELL_LIMIT,
    MAX_CELL_WARN,
)

__all__ = [
    "Step3GridAreaWidget",
    "Step2GridAreaWidget",
    "FloatingMapToolbar",
    "Step3SelectionMapTool",
    "AREA_COLORS",
    "MAX_CELL_LIMIT",
    "MAX_CELL_WARN",
]
