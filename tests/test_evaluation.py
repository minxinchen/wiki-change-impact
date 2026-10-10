import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('runner',Path(__file__).resolve().parents[1]/'evaluation/run.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class EvaluationTests(unittest.TestCase):
    def test_empty_metrics_are_undefined_not_perfect(self):
        result=runner.score([],[])
        self.assertIsNone(result['precision']);self.assertIsNone(result['recall'])

    def test_false_positive_and_miss_are_not_hidden_by_overlap(self):
        result=runner.score(['a','b'],['b','c'])
        self.assertEqual(result['extra'],['a']);self.assertEqual(result['missed'],['c'])
        self.assertEqual(result['precision'],.5);self.assertEqual(result['recall'],.5)

    def test_known_limitations_survive_a_successful_contract_check(self):
        result=runner.evaluate()
        self.assertTrue(result['all_status_contracts_match'])
        rows={r['id']:r for r in result['cases']}
        self.assertEqual(rows['undeclared-body']['report']['status'],'unchanged')
        self.assertEqual(len(rows['undeclared-body']['methods']['dependency_tool']['missed']),3)
        self.assertEqual(len(rows['format-only']['methods']['dependency_tool']['extra']),3)

    def test_portable_results_are_deterministic_and_leave_inputs_alone(self):
        fixture=Path(runner.__file__).with_name('cases.json');before=fixture.read_bytes()
        self.assertEqual(runner.evaluate(),runner.evaluate())
        self.assertEqual(fixture.read_bytes(),before)


if __name__=='__main__':unittest.main()
