import unittest

from target import schedule


class ScheduleTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(schedule([], {}), [])

    def test_independent_tasks_follow_declared_order(self):
        self.assertEqual(schedule(["z", "a", "m"], {}), ["z", "a", "m"])

    def test_dependency_is_respected(self):
        result = schedule(["ship", "build", "test"],
                          {"ship": ["test"], "test": ["build"]})
        self.assertEqual(result, ["build", "test", "ship"])

    def test_ready_tasks_use_declared_order(self):
        result = schedule(["b", "a", "c"], {"c": ["a"]})
        self.assertEqual(result, ["b", "a", "c"])

    def test_newly_ready_task_is_reconsidered(self):
        result = schedule(["a", "b", "c"], {"b": ["a"], "c": ["a"]})
        self.assertEqual(result, ["a", "b", "c"])

    def test_omitted_dependencies(self):
        self.assertEqual(schedule(["first", "second"], {"second": []}),
                         ["first", "second"])

    def test_cycle_error(self):
        with self.assertRaisesRegex(ValueError, "^dependency cycle$"):
            schedule(["a", "b"], {"a": ["b"], "b": ["a"]})

    def test_unknown_dependency_error(self):
        with self.assertRaisesRegex(ValueError, "^dependencies must refer to listed tasks$"):
            schedule(["a"], {"a": ["missing"]})

    def test_invalid_task_input(self):
        for tasks in (["a", "a"], ["a", 3]):
            with self.subTest(tasks=tasks), self.assertRaisesRegex(
                    ValueError, "^dependencies must refer to listed tasks$"):
                schedule(tasks, {})

    def test_dependency_order_regression(self):
        result = schedule(["final", "middle", "start"],
                          {"final": ["middle"], "middle": ["start"]})
        self.assertEqual(result, ["start", "middle", "final"])
