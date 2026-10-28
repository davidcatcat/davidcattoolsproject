import unittest

from target import select_rule


class RuleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.record = {"kind": "book", "region": "west"}
        self.rules = [
            {"name": "general", "conditions": {"kind": "book"}, "priority": 1},
            {"name": "regional", "conditions": {"region": "west"}, "priority": 4},
            {"name": "both", "conditions": self.record, "priority": 4},
        ]

    def test_first_preserves_input_order(self):
        self.assertEqual(select_rule(self.record, self.rules), "general")

    def test_priority_selects_greatest(self):
        self.assertEqual(select_rule(self.record, self.rules, "priority"), "regional")

    def test_priority_tie_keeps_first_match(self):
        self.assertEqual(select_rule(self.record, self.rules[1:], "priority"), "regional")

    def test_empty_rules(self):
        self.assertIsNone(select_rule(self.record, []))
        self.assertIsNone(select_rule(self.record, [], "priority"))

    def test_no_match(self):
        self.assertIsNone(select_rule({"kind": "pen"}, self.rules))

    def test_empty_conditions_match(self):
        self.assertEqual(select_rule({}, [{"name": "any", "conditions": {}}]), "any")

    def test_invalid_mode(self):
        with self.assertRaisesRegex(ValueError, "^mode must be 'first' or 'priority'$"):
            select_rule(self.record, [], "random")

    def test_invalid_rules(self):
        with self.assertRaisesRegex(ValueError, "^rules must be a list$"):
            select_rule(self.record, ())
        with self.assertRaisesRegex(ValueError, "^each rule needs name and conditions$"):
            select_rule(self.record, [{"name": "bad"}])
        with self.assertRaisesRegex(ValueError, "^each rule needs name and conditions$"):
            select_rule(self.record, [{"name": 7, "conditions": {}}])
        with self.assertRaisesRegex(ValueError, "^each rule needs name and conditions$"):
            select_rule(self.record, [{"name": "bad", "conditions": []}])

    def test_invalid_priority(self):
        for value in (None, True, 2.5):
            with self.subTest(value=value), self.assertRaisesRegex(
                    ValueError, "^priority must be an integer$"):
                select_rule(self.record, [{"name": "bad", "conditions": {},
                                           "priority": value}], "priority")
        with self.assertRaisesRegex(ValueError, "^priority must be an integer$"):
            select_rule(self.record, [{"name": "bad", "conditions": {}}], "priority")
