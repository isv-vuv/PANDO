import logging
import unittest

from core.app.app_core.logging import (
    clean_log_message,
    format_pando_log,
    setup_pando_logger,
    should_ignore_log_message,
)


class PandoLoggingTests(unittest.TestCase):
    def test_clean_log_message_removes_ascii_borders_and_emojis(self):
        dirty_input = "=== === Step 1/5: Importing Links and Zones === ==="
        cleaned = clean_log_message(dirty_input)
        self.assertEqual(cleaned, "Step 1/5: Importing Links and Zones")

        emoji_input = "❌ Schwerwiegender Fehler: File not found"
        cleaned_emoji = clean_log_message(emoji_input)
        self.assertEqual(cleaned_emoji, "Schwerwiegender Fehler: File not found")

    def test_clean_log_message_strips_existing_timestamps(self):
        raw_log = "2026-08-04 09:26:03,798 - INFO - Identifiziere U-Turns..."
        cleaned = clean_log_message(raw_log)
        self.assertEqual(cleaned, "Identifiziere U-Turns...")

    def test_should_ignore_log_message_filters_noisy_warnings(self):
        self.assertTrue(should_ignore_log_message("Warning 1: Field Name of width 255 truncated to 254."))
        self.assertTrue(should_ignore_log_message("Warning 6: Normalized/laundered field name: 'Total_Intensity' to 'Total_Inte'"))
        self.assertTrue(should_ignore_log_message("DeprecationWarning: QgsVectorFileWriter.writeAsVectorFormatV2() is deprecated"))
        self.assertTrue(should_ignore_log_message("Requirement already satisfied: requests in c:\\site-packages"))
        self.assertFalse(should_ignore_log_message("Importiere OSM-Streckennetz in Visum..."))

    def test_format_pando_log_produces_standard_structure(self):
        formatted = format_pando_log("Test log message", level="INFO")
        self.assertTrue(formatted.startswith("["))
        self.assertIn("] [INFO] Test log message", formatted)

    def test_setup_pando_logger_routes_to_gui_callback(self):
        received_logs = []

        def gui_callback(msg: str) -> None:
            received_logs.append(msg)

        logger = setup_pando_logger("TestLogger", gui_callback=gui_callback)
        logger.info("Hello PANDO")

        self.assertEqual(len(received_logs), 1)
        self.assertIn("] [INFO] Hello PANDO", received_logs[0])


if __name__ == "__main__":
    unittest.main()
