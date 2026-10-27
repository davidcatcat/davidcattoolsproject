import importlib.util
import pathlib
import tempfile
import unittest


LEVEL_DIR = pathlib.Path(__file__).resolve().parent


def load_target(path):
    spec = importlib.util.spec_from_file_location("candidate_target", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_candidate_suite(path):
    module = load_target(path)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TestResult()
    suite.run(result)
    return result


class LadderTest(unittest.TestCase):
    def test_authored_tests_pass_original_and_kill_mutants(self):
        original = run_candidate_suite(LEVEL_DIR / "target.py")
        self.assertGreater(original.testsRun, 0, "target.py must define tests")
        self.assertEqual(original.errors + original.failures, [], "tests fail on original")

        source = (LEVEL_DIR / "target.py").read_text()
        mutants = {
            "accept uppercase": lambda s: isinstance(s, str) and _valid_without_extra_checks(s.lower()),
            "accept empty": lambda s: True if s == "" else _original(s),
            "accept edge hyphen": lambda s: _valid_alphabet(s),
            "accept repeated hyphen": lambda s: _valid_except_repeats(s),
        }
        for name, mutant in mutants.items():
            with self.subTest(mutant=name), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "target.py"
                path.write_text(source)
                module = load_target(path)
                module.is_slug = mutant
                suite = unittest.defaultTestLoader.loadTestsFromModule(module)
                result = unittest.TestResult()
                suite.run(result)
                self.assertGreater(result.testsRun, 0)
                self.assertGreater(len(result.errors + result.failures), 0,
                                   f"tests did not reject mutant: {name}")


def _valid_without_extra_checks(value):
    return isinstance(value, str) and all(
        ("a" <= c <= "z") or ("0" <= c <= "9") or c == "-" for c in value
    )


def _valid_alphabet(value):
    return _valid_without_extra_checks(value)


def _valid_except_repeats(value):
    return isinstance(value, str) and bool(value) and value[0] != "-" and value[-1] != "-" and \
        _valid_without_extra_checks(value)


def _original(value):
    if not isinstance(value, str) or not value:
        return False
    if value[0] == "-" or value[-1] == "-" or "--" in value:
        return False
    return _valid_without_extra_checks(value)
