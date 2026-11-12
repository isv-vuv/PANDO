"""Project switching must preserve state and never leave active workers behind."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from core.app.app_qt import main_window
from core.app.app_qt.app_state import StepId


class HomeNavigationTests(TestCase):
    def window(self, busy=False):
        return SimpleNamespace(
            state=SimpleNamespace(current_step=StepId.RESULTS,
                                  has_project_context=lambda: True,
                                  save_project_metadata=Mock()),
            _home_navigation_busy=lambda: busy,
            _current_step_widget=SimpleNamespace(release_resources=Mock()),
            _centrality_edit_session=object(), _show_step0=Mock())

    def test_busy_navigation_keeps_project_and_page(self):
        window = self.window(busy=True)
        original = window.state
        with patch.object(main_window.Dialogs, 'info') as info:
            main_window.UrbanActQtMainWindow._handle_home_requested(window)
        info.assert_called_once()
        self.assertIs(window.state, original)
        original.save_project_metadata.assert_not_called()
        window._show_step0.assert_not_called()

    def test_save_failure_keeps_project_open(self):
        window = self.window()
        original = window.state
        original.save_project_metadata.side_effect = OSError('disk full')
        with patch.object(main_window.Dialogs, 'error') as error:
            main_window.UrbanActQtMainWindow._handle_home_requested(window)
        error.assert_called_once()
        self.assertIs(window.state, original)
        window._current_step_widget.release_resources.assert_not_called()
        window._show_step0.assert_not_called()

    def test_home_saves_and_releases_before_reset(self):
        window = self.window()
        original = window.state
        widget = window._current_step_widget
        def release():
            original.save_project_metadata.assert_called_once()
            self.assertIs(window.state, original)
        widget.release_resources.side_effect = release
        main_window.UrbanActQtMainWindow._handle_home_requested(window)
        widget.release_resources.assert_called_once()
        self.assertIsNot(window.state, original)
        self.assertFalse(window.state.has_project_context())
        self.assertIsNone(window._centrality_edit_session)
        window._show_step0.assert_called_once()

    def test_busy_detection_includes_workers_report_and_edit_session(self):
        for field, value in [('_thread', SimpleNamespace(isRunning=lambda: True)),
                             ('_download_thread', SimpleNamespace(isRunning=lambda: True)),
                             ('process', object())]:
            with self.subTest(field=field):
                window = SimpleNamespace(_geocode_thread=None,
                                         _current_step_widget=SimpleNamespace(**{field: value}))
                self.assertTrue(main_window.UrbanActQtMainWindow._home_navigation_busy(window))
        window = SimpleNamespace(_geocode_thread=None, _current_step_widget=None,
                                 _centrality_edit_session=SimpleNamespace(busy=True))
        self.assertTrue(main_window.UrbanActQtMainWindow._home_navigation_busy(window))
