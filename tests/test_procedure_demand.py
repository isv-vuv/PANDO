from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from xml.etree import ElementTree

from core.scripts.visum.scripts.procedure_demand import restore_assignment_segments


class ProcedureDemandTests(TestCase):
    def test_walk_corrections_keep_missing_paths_unreachable(self):
        folder = Path(__file__).resolve().parents[1] / 'core/scripts/visum/helper_files/pro'
        for name in ('DE_Verfahrensablauf.xml', 'EN_ProcedureSequence.xml'):
            operations = ElementTree.parse(folder / name).findall('./OPERATIONS/OPERATION')
            formulas = [operation.find('MATRIXFORMULAPARA').get('FORMULA')
                        for operation in operations
                        if operation.get('COMMENT') == 'Travel time Walk corrected']
            self.assertEqual(len(formulas), 2)
            for original, expected in ((999999, 999999), (999995, 999999), (10, 10), (100, 20)):
                value = original
                for formula in formulas:
                    expression = formula.split(':=', 1)[1].strip()
                    expression = expression.replace('Matrix([NAME]="t0 W")', 'value')
                    expression = expression.replace('Matrix([NAME]="DID")', '1')
                    expression = expression.replace('if ', 'choose ').replace('&', 'and')
                    value = eval(expression, {'__builtins__': {}}, {
                        'value': value, 'choose': lambda condition, yes, no: yes if condition else no})
                self.assertEqual(value, expected, (name, original))

    def test_restores_unnamed_final_walk_assignment_in_both_templates(self):
        folder = Path(__file__).resolve().parents[1] / 'core/scripts/visum/helper_files/pro'
        for name in ('DE_Verfahrensablauf.xml', 'EN_ProcedureSequence.xml'):
            with self.subTest(template=name):
                path = folder / name
                templates = ElementTree.parse(path).findall('./OPERATIONS/OPERATION')
                operations = [MagicMock() for _ in templates]
                for template, operation in zip(templates, operations):
                    values = {'OperationType': 'unexpected Visum enum',
                              'Comment': template.get('COMMENT', '')}
                    operation.AttValue.side_effect = values.__getitem__
                    if template.find('PRTASSIGNMENTPARA') is None:
                        operation.PrTAssignmentParameters.AttValue.side_effect = RuntimeError('Not an IV assignment')
                # Visum may insert extra operations and renumber the imported ones.
                extra = MagicMock()
                extra.AttValue.return_value = ''
                extra.PrTAssignmentParameters.AttValue.side_effect = RuntimeError('Not an IV assignment')
                visum = SimpleNamespace(Procedures=SimpleNamespace(
                    Operations=SimpleNamespace(GetAll=[extra] + operations)))
                restore_assignment_segments(visum, path, MagicMock())
                self.assertEqual(templates[-1].get('COMMENT'), '')
                operations[-1].PrTAssignmentParameters.SetAttValue.assert_called_once_with('DSegSet', 'W')
                for template, operation in zip(templates, operations):
                    if template.find('PRTASSIGNMENTPARA') is None:
                        operation.PrTAssignmentParameters.SetAttValue.assert_not_called()

    def test_rejects_missing_assignments(self):
        path = Path(__file__).resolve().parents[1] / 'core/scripts/visum/helper_files/pro/DE_Verfahrensablauf.xml'
        visum = SimpleNamespace(Procedures=SimpleNamespace(
            Operations=SimpleNamespace(GetAll=[])))
        with self.assertRaises(ValueError):
            restore_assignment_segments(visum, path, MagicMock())
