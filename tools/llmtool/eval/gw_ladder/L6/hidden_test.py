import ast
import unittest
import target


class LadderTest(unittest.TestCase):
    def test_fixed_output_cases(self):
        cases = [
            ([], "Name | Qty | Total\nTOTAL | 0 | 0.00"),
            ([("Tea", 2, 1.25), ("Cake", 1, 3)],
             "Name | Qty | Total\nTea | 2 | 2.50\nCake | 1 | 3.00\nTOTAL | 3 | 5.50"),
            ([("credit", -2, 1.5)],
             "Name | Qty | Total\ncredit | -2 | -3.00\nTOTAL | -2 | -3.00"),
        ]
        for rows, expected in cases:
            with self.subTest(rows=rows):
                self.assertEqual(target.render_report(rows), expected)

    def test_functions_are_short(self):
        tree = ast.parse(open(target.__file__, encoding="utf-8").read())
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.assertTrue(functions)
        for function in functions:
            with self.subTest(function=function.name):
                self.assertLessEqual(function.end_lineno - function.lineno + 1, 20)
