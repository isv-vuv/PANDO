import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock


SCRIPT = Path(__file__).resolve().parents[1] / 'core/scripts/visum/scripts/ferry_wait.py'
spec = importlib.util.spec_from_file_location('ferry_wait', SCRIPT)
ferry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ferry)


class FerryWaitTests(unittest.TestCase):
    def test_default_zero_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(ferry.load_wait_minutes(root), 20)
            path = Path(root) / 'ferry_parameters.json'
            path.write_text(json.dumps({'wait_minutes': 0}))
            self.assertEqual(ferry.load_wait_minutes(root), 0)
            for value in (-1, float('nan'), float('inf')):
                path.write_text(json.dumps({'wait_minutes': value}))
                with self.assertRaises(ValueError):
                    ferry.load_wait_minutes(root)

    def test_boarding_split_route_return_to_zero_and_existing_penalty(self):
        rows = [
            ['Ferry_Access', 'Ferry', 7, 0, 0],
            ['Ferry', 'Ferry', 0, 0, 0],
            ['Ferry', 'Residential', 4, 0, 0],
            ['Residential', 'Primary', 8, 0, 0],
        ]
        visum = MagicMock()
        turns = visum.Net.Turns
        turns.GetMultipleAttributes.side_effect = lambda *_: [r[:4] for r in rows]

        turns.GetMultiAttValues.side_effect = lambda *_: [(i, row[4]) for i, row in enumerate(rows, 1)]

        def write(attr, values):
            col = {'T0PRT': 2, 'PANDO_FERRY_WAIT': 3, 'ADDVAL2': 4}[attr]
            for index, value in values:
                rows[index - 1][col] = value

        turns.SetMultiAttValues.side_effect = write
        for minutes in (20, 20, 5, 0):
            ferry.apply_ferry_wait(visum, minutes)
            self.assertEqual([r[2] for r in rows], [7 + minutes * 60, 0, 4, 8])
        # No restrictions or link speeds are opened/rewritten.
        self.assertEqual({call.args[0] for call in turns.SetMultiAttValues.call_args_list},
                         {'T0PRT', 'PANDO_FERRY_WAIT', 'ADDVAL2'})

    def test_custom_additional_values_are_not_silently_overwritten(self):
        visum = MagicMock()
        visum.Net.Links.GetMultiAttValues.return_value = [(1, 5)]
        with self.assertRaisesRegex(ValueError, 'reserved'):
            ferry.apply_ferry_wait(visum, 20)
        visum.Net.Turns.SetMultiAttValues.assert_not_called()

    def test_both_procedures_include_unscaled_put_wait_and_protect_walk_wait(self):
        import xml.etree.ElementTree as ET
        directory = SCRIPT.parent.parent / 'helper_files/pro'
        for name in ('DE_Verfahrensablauf.xml', 'EN_ProcedureSequence.xml'):
            root = ET.parse(directory / name).getroot()
            turn = root.find("./FUNCTIONS/PRTSKIMMATRIXSETTINGS/USERDEFSKIM/LINCOMB[@NETOBJECTTYPE='TURN']/LINCOMBITEM")
            self.assertEqual(turn.get('FIRSTATTRNAME'), 'PANDO_FERRY_WAIT')
            self.assertEqual(turn.get('SECONDATTRCOEFF'), '60')
            walk = root.find(".//OPERATION[@COMMENT='Travel time Walk']/PRTSKIMMATRIXPARA")
            self.assertIsNotNone(walk.find("SINGLESKIMMATRIXPARA[@NAME='ADDVAL2']"))
            formulas = [item.get('FORMULA') for item in root.findall('.//MATRIXFORMULAPARA')]
            self.assertTrue(any('[CODE]="AD2"' in formula and 'MIN(' in formula for formula in formulas))

    def test_parameter_invalidates_analysis_and_presentation_only(self):
        from core.app.app_core.centrality_edits import EditSession
        with tempfile.TemporaryDirectory() as root:
            session = EditSession(root)
            session.set_ferry_wait(0)
            self.assertEqual(session.pending(), ['analysis', 'presentation'])
            for name in session.pending():
                session.complete(name, session.revision(name))
            self.assertEqual(EditSession(root).pending(), [])
            session.set_ferry_wait(20)
            self.assertEqual(session.pending(), ['analysis', 'presentation'])


if __name__ == '__main__':
    unittest.main()
