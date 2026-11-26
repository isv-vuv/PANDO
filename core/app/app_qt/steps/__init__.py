"""Semantic Qt workflow step modules and lazy legacy-name aliases for PANDO."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from types import ModuleType

_PKG = __name__

LEGACY_STEP_MODULE_ALIASES: dict[str, str] = {
    "step0_welcome": "step_welcome",
    "step1_search": "step_search",
    "step2_data": "step_data",
    "step2_grid_area": "step_grid_area",
    "step3_data": "step_data",
    "step3_grid_area": "step_grid_area",
    "step4_processing": "step_processing",
    "step5_linktypes": "step_linktypes",
    "step5_visum": "step_visum",
    "step6_visum": "step_visum",
    "step6_results": "step_results",
    "step7_results": "step_results",
    "step7_report": "step_report",
    "step8_report": "step_report",
}

SEMANTIC_WIDGET_EXPORTS: dict[str, tuple[str, str]] = {
    "WelcomeStepWidget": ("step_welcome", "WelcomeStepWidget"),
    "SearchStepWidget": ("step_search", "SearchStepWidget"),
    "DataStepWidget": ("step_data", "DataStepWidget"),
    "GridAreaStepWidget": ("step_grid_area", "GridAreaStepWidget"),
    "ProcessingStepWidget": ("step_processing", "ProcessingStepWidget"),
    "LinkTypesStepWidget": ("step_linktypes", "LinkTypesStepWidget"),
    "VisumStepWidget": ("step_visum", "VisumStepWidget"),
    "ResultsStepWidget": ("step_results", "ResultsStepWidget"),
    "ReportStepWidget": ("step_report", "ReportStepWidget"),
    "LinkTypesEditorWidget": ("step_linktypes", "LinkTypesEditorWidget"),
}


class _LegacyStepAliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Redirect legacy numbered step imports (`step5_visum`) to canonical modules (`step_visum`)."""

    def find_spec(self, fullname: str, path=None, target=None):
        prefix = f"{_PKG}."
        if not fullname.startswith(prefix):
            return None
        short = fullname[len(prefix):]
        if "." in short or short not in LEGACY_STEP_MODULE_ALIASES:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        short = spec.name[len(f"{_PKG}."):]
        canonical_short = LEGACY_STEP_MODULE_ALIASES[short]
        mod = importlib.import_module(f"{_PKG}.{canonical_short}")
        sys.modules[spec.name] = mod
        setattr(sys.modules[_PKG], short, mod)
        return mod

    def exec_module(self, module: ModuleType) -> None:
        return None


if not any(isinstance(finder, _LegacyStepAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _LegacyStepAliasFinder())


def __getattr__(name: str):
    if name in LEGACY_STEP_MODULE_ALIASES:
        canonical = LEGACY_STEP_MODULE_ALIASES[name]
        mod = importlib.import_module(f"{_PKG}.{canonical}")
        sys.modules[f"{_PKG}.{name}"] = mod
        globals()[name] = mod
        return mod
    if name in SEMANTIC_WIDGET_EXPORTS:
        mod_name, attr_name = SEMANTIC_WIDGET_EXPORTS[name]
        mod = importlib.import_module(f"{_PKG}.{mod_name}")
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(SEMANTIC_WIDGET_EXPORTS.keys())
