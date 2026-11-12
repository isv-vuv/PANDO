"""Native preview, save and child-process lifecycle regression tests."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from core.app.app_qt.qt_base import QGIS_IMPORT_ERROR


@unittest.skipIf(QGIS_IMPORT_ERROR is not None, 'Requires QGIS Python')
class ReportPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.core import QgsApplication
        cls.app = QgsApplication.instance() or QgsApplication([], True)
        cls.app.initQgis()

    def setUp(self):
        try:
            import typst
            import pypdfium2
        except ImportError:
            self.skipTest('Requires report dependencies')
        from core.app.app_qt.steps.step7_report import Step7ReportWidget
        from core.locales import localizer
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'report').mkdir()
        pdf = typst.compile(b'First page #pagebreak() Second page')
        (self.root / 'report/report.pdf').write_bytes(pdf)
        self.widget = Step7ReportWidget(localizer, project_path=str(self.root))

    def tearDown(self):
        if not hasattr(self, 'widget'):
            return
        from qgis.PyQt import QtCore
        self.widget.release_resources()
        self.widget.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def test_language_defaults_to_ui_and_progress_lifecycle(self):
        from core.locales import localizer
        from core.app.app_qt.qt_base import AnimatedProgressBar
        self.assertIsInstance(self.widget.progress, AnimatedProgressBar)
        self.assertTrue(self.widget.language_buttons[localizer.get_current_language()].isChecked())
        other = 'en' if localizer.get_current_language() == 'de' else 'de'
        self.widget.language_buttons[other].click()
        self.assertTrue(self.widget.language_buttons[other].isChecked())
        self.widget._controls(True)
        self.assertFalse(self.widget.language_buttons[other].isEnabled())
        self.widget.progress.start_animation()
        self.widget.release_resources()
        self.assertFalse(self.widget.progress._is_animating)

    def test_continuous_scroll_and_zoom(self):
        self.widget.resize(800, 600)
        self.widget.show()
        self.app.processEvents()
        self.widget.render_page()
        self.assertEqual(len(self.widget.page_images), 2)
        first, second = self.widget.page_images
        self.assertGreater(second.y(), first.y() + first.height())
        old_width = first.width()
        self.widget.scroll.verticalScrollBar().setValue(second.y())
        self.widget.render_page()
        self.assertEqual(self.widget.page, 1)
        self.assertFalse(self.widget.page_images[1].pixmap().isNull())
        self.widget.change_zoom(1.25)
        self.app.processEvents()
        self.assertGreater(self.widget.page_images[0].width(), old_width)

    def test_saves_exact_pdf_copy(self):
        destination = self.root / 'saved.pdf'
        with patch('core.app.app_qt.steps.step7_report.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(destination), 'PDF')):
            self.widget.save_pdf()
        self.assertEqual(destination.read_bytes(), self.widget.pdf_path.read_bytes())

    def test_cancellation_stops_child_and_unlocks_session(self):
        from qgis.PyQt import QtCore
        from qgis.PyQt.QtTest import QSignalSpy
        process = QtCore.QProcess(self.widget)
        self.widget.process = process
        self.widget.edit_session.busy = True
        process.finished.connect(self.widget._finished)
        process.start(sys.executable, ['-c', 'import time; time.sleep(30)'])
        self.assertTrue(process.waitForStarted(3000))
        finished = QSignalSpy(self.widget.generation_finished)
        with patch.object(process, 'waitForFinished', side_effect=AssertionError('Cancel must not block')):
            self.widget.cancel()
        self.assertTrue(finished.wait(5000))
        self.assertIsNone(self.widget.process)
        self.assertFalse(self.widget.edit_session.busy)
        self.assertTrue(self.widget.pdf_path.is_file())

    def test_failed_child_start_unlocks_session(self):
        from qgis.PyQt import QtCore
        process = QtCore.QProcess(self.widget)
        self.widget.process = process
        self.widget.edit_session.busy = True
        process.errorOccurred.connect(self.widget._process_error)
        process.start(str(self.root / 'does-not-exist'))
        process.waitForStarted(3000)
        self.assertIsNone(self.widget.process)
        self.assertFalse(self.widget.edit_session.busy)


if __name__ == '__main__':
    unittest.main()
