"""Compatibility imports for the QWidget presentation layer.

The workflow state lives in ``app_core`` so alternative frontends never need
to import the QWidget package.
"""

from core.app.app_core.workflow_state import (  # noqa: F401
    AppState,
    LocationAdapter,
    STEP3_REQUIRED_KEYS,
    STEP4_REQUIRED_KEYS,
    StepId,
    coerce_step_id,
    previous_step_id,
    progress_percent_for_step,
)

__all__ = [
    "AppState",
    "LocationAdapter",
    "STEP3_REQUIRED_KEYS",
    "STEP4_REQUIRED_KEYS",
    "StepId",
    "coerce_step_id",
    "previous_step_id",
    "progress_percent_for_step",
]
