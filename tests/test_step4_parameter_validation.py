"""Parameter validation without requiring a running QGIS UI."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import xml.etree.ElementTree as ET
from pathlib import Path

from core.app.app_qt.steps import step4_processing as module
from core.app.app_qt.steps.step4_processing import Step4ProcessingWidget as Widget
from core.app.app_core.model_pipeline import MODEL_DESCRIPTIONS, QGIS_MODELS_SUBDIR


class Edit:
    def __init__(self, text):
        self.content = text
        self.properties = {}
        self.style = ""
        self.tooltip = "Help"

    def text(self): return self.content
    def property(self, key): return self.properties.get(key)
    def setProperty(self, key, value): self.properties[key] = value
    def styleSheet(self): return self.style
    def setStyleSheet(self, value): self.style = value
    def toolTip(self): return self.tooltip
    def setToolTip(self, value): self.tooltip = value


class ParameterValidationTests(TestCase):
    def page(self):
        page = SimpleNamespace(
            localizer=SimpleNamespace(get_string=lambda key, **kw: kw.get("default", key)),
            _format_initial_int=str,
            _read_int_field=lambda edit, default=None: Widget._read_int_field(None, edit, default),
        )
        return page

    def test_every_boundary_empty_and_malformed_text_and_recovery(self):
        for fields in (Widget._MODEL2_FIELDS, Widget._MODEL5_FIELDS):
            for key, _, _, minimum, maximum in fields:
                with self.subTest(parameter=key):
                    page = self.page()
                    edit = Edit(str(minimum))
                    page._numeric_parameter_fields = lambda: [(edit, key, minimum, maximum, True)]
                    for text, invalid in ((str(minimum - 1), True), (str(minimum), False),
                                          (str(maximum), False), (str(maximum + 1), True),
                                          ("", True), ("abc", True), ("500", not minimum <= 500 <= maximum)):
                        edit.content = text
                        self.assertEqual(bool(Widget._validate_numeric_parameters(page)), invalid)
                        self.assertEqual("#dc2626" in edit.style, invalid)
                        if not invalid:
                            self.assertEqual(edit.tooltip, "Help")

    def test_inactive_optional_parameter_does_not_block(self):
        page = self.page()
        edit = Edit("")
        page._numeric_parameter_fields = lambda: [(edit, "Optional", 0, 25, False)]
        self.assertEqual(Widget._validate_numeric_parameters(page), [])
        self.assertEqual(edit.style, "")

    def test_grouped_numbers(self):
        for text in ("100.000", "100,000", "100 000"):
            self.assertEqual(Widget._read_int_field(None, Edit(text)), 100000)

    def test_model5_limits_match_shipped_model(self):
        description = next(d for d in MODEL_DESCRIPTIONS if d.key == "model5")
        root = ET.parse(Path(QGIS_MODELS_SUBDIR) / description.filename).getroot()
        definitions = next(x for x in root.iter("Option") if x.get("name") == "parameterDefinitions")
        limits = {p.get("name"): {x.get("name"): x.get("value") for x in p} for p in definitions}
        for key, _, _, minimum, maximum in Widget._MODEL5_FIELDS:
            binding = next(b for b in description.inputs if b.context_key == key)
            bounds = limits[binding.aliases[0]]
            self.assertEqual(minimum, float(bounds["min"]))
            self.assertLessEqual(maximum, float(bounds["max"]))

    def test_validation_blocks_start_and_restart_and_recovers(self):
        page = self.page()
        enabled = {}
        page.project_path, page.step_data, page._thread = "", {}, None
        page.rb_ref_yes = page.rb_census_yes = SimpleNamespace(isChecked=lambda: False)
        page.readiness_label = None
        page._emit_status = lambda message: None
        page.start_button = SimpleNamespace(setText=lambda text: None, setEnabled=lambda state: enabled.update(start=state))
        page.restart_button = SimpleNamespace(setEnabled=lambda state: enabled.update(restart=state))
        for errors in (["Invalid parameter"], []):
            page._validate_numeric_parameters = lambda: errors
            with patch.object(module, "pipeline_readiness", return_value=[]):
                Widget.refresh_readiness(page)
            self.assertEqual(enabled, {"start": not errors, "restart": not errors})
