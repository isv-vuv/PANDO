from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from core.app.app_qt.steps import step6_results as results


class ResultsVisumLaunchTests(TestCase):
    def test_opens_model_using_registered_visum_factory(self):
        with TemporaryDirectory() as directory:
            model = Path(directory) / 'processed/visum/ver/07_Test_Model.ver'
            model.parent.mkdir(parents=True)
            model.touch()
            visum = MagicMock()
            with patch.object(results, 'create_visum', return_value=visum) as create, \
                    patch.object(results, '_set_visum_project_directories'), \
                    patch.object(results, 'ensure_visum_language'), \
                    patch.object(results, '_ACTIVE_VISUM_INSTANCES', []):
                self.assertTrue(results.open_in_visum(directory))
                create.assert_called_once_with()
                visum.LoadVersion.assert_called_once_with(str(model.resolve()))
                visum.Graphic.ShowMaximized.assert_called_once()
                self.assertIn(visum, results._ACTIVE_VISUM_INSTANCES)

    def test_card_reports_failed_launch(self):
        card = SimpleNamespace(open_visum_callback=MagicMock(return_value=False),
                               folder_path='project')
        with patch.object(results.Dialogs, 'warning') as warning:
            results.ResultCardWidget._on_open_visum(card)
            warning.assert_called_once()
