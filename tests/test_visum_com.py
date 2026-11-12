import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

path = Path('core/scripts/visum/scripts/visum_com.py')
spec = importlib.util.spec_from_file_location('visum_com', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class VisumComTests(unittest.TestCase):
    def test_registered_versions_and_matching_appdata(self):
        registry = MagicMock()
        with patch.dict(sys.modules, winreg=registry), patch.dict(os.environ, APPDATA='C:/UserData'):
            for suffix, year in [('250', 2025), ('2601', 2026), ('240', 2024)]:
                registry.QueryValueEx.return_value = ('Visum.Visum.' + suffix, 1)
                self.assertEqual(module.get_visum_progid(), 'Visum.Visum.' + suffix)
                self.assertEqual(module.get_visum_appdata_dir(), Path('C:/UserData/PTV Vision') / f'PTV Visum {year}')

    def test_missing_registration_has_actionable_message(self):
        registry = MagicMock()
        registry.OpenKey.side_effect = FileNotFoundError()
        with patch.dict(sys.modules, winreg=registry):
            with self.assertRaisesRegex(RuntimeError, 'PTV-Setup'):
                module.get_visum_progid()

    def test_dispatch_uses_registered_version(self):
        client = types.ModuleType('win32com.client')
        client.Dispatch = MagicMock(return_value=object())
        package = types.ModuleType('win32com')
        package.client = client
        with patch.dict(sys.modules, {'win32com': package, 'win32com.client': client}), patch.object(module, 'get_visum_progid', return_value='Visum.Visum.2601'):
            self.assertIs(module.create_visum(), client.Dispatch.return_value)
            client.Dispatch.assert_called_once_with('Visum.Visum.2601')
            client.Dispatch.side_effect = Exception('COM error')
            with self.assertRaisesRegex(RuntimeError, '2601'):
                module.create_visum()

if __name__ == "__main__":
    unittest.main()
