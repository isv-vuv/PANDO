"""Regression tests for leaving the processing page during OSM preprocessing."""

from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from core.app.app_qt.steps import step4_processing
from core.app.app_qt.steps.step4_processing import PipelineWorker, Step4ProcessingWidget


class Step4WorkerShutdownTests(TestCase):
    def test_navigation_waits_for_worker_before_releasing_page(self):
        calls = []

        class Thread:
            running = True

            def quit(self):
                calls.append("quit")

            def isRunning(self):
                return self.running

            def wait(self, timeout):
                calls.append(("wait", timeout))

        thread = Thread()
        events = SimpleNamespace(processEvents=lambda: (calls.append("events"), setattr(thread, "running", False)))

        page = SimpleNamespace(
            _thread=thread,
            stop_pipeline=lambda: calls.append("stop"),
            main_window=None,
        )

        with patch.object(step4_processing, "QtCore", SimpleNamespace(QCoreApplication=events)):
            Step4ProcessingWidget.cancel_processing(page)

        self.assertEqual(calls, ["stop", "quit", ("wait", 50), "events"])
        self.assertIsNotNone(page._thread)
        self.assertTrue(page._leaving_page)

    def test_file_lock_wait_ends_when_navigation_cancels(self):
        stop_event = Event()

        class Signal:
            def emit(self, _path, sync):
                # The dialog signal has been queued to the blocked GUI thread.
                # Its response event cannot be set before navigation completes.
                stop_event.set()

        worker = SimpleNamespace(file_locked=Signal(), stop_event=stop_event)

        self.assertFalse(PipelineWorker._handle_file_locked(worker, "locked.gpkg"))
