"""Guards for the #2281 split of llmtool.remote_test."""

import ast
import json
import unittest
from pathlib import Path

from llmtool import remote_test, sensitive_paths

PART_LIMIT = 24576
_PACKAGE = Path(remote_test.__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PARTS = tuple(_PACKAGE / name for name in (
    'remote_test_part1.py',
    'remote_test_part2.py',
    'remote_test_part3.py',
    'remote_test_part4.py',
))
_PUBLIC_NAMES = (
    'run_remote_test', 'valid_module_results', 'pass_receipt_covers',
    'run_incident_remote_test',
    'INCIDENT_UNKNOWN', 'INCIDENT_UNREADABLE', 'INCIDENT_NOT_OPEN',
    'INCIDENT_ARGUMENT_INVALID', 'INCIDENT_SHA_NOT_RECORDED',
    'INCIDENT_MODULE_NOT_FAILING', 'INCIDENT_MODULE_CAP',
    'INCIDENT_CONFIG_INVALID', 'INCIDENT_RECEIPT_SENSITIVE',
    'INCIDENT_RECEIPT_INCONSISTENT',
    'read_d6_registration', '_github_get', '_trusted_slug',
    '_validate_remote_receipt', '_hosted_job', '_hosted_post',
    '_hosted_dispatch', '_REMOTE_SCRIPT',
)


class RemoteTestSplitTests(unittest.TestCase):
    def test_each_module_is_within_the_read_piece_limit(self):
        for path in (Path(remote_test.__file__).resolve(),) + _PARTS:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertLessEqual(path.stat().st_size, PART_LIMIT)

    def test_every_part_is_a_sensitive_rule_file(self):
        list_path = _PACKAGE.parent / 'sensitive-paths.json'
        rules = json.loads(list_path.read_text(encoding='utf-8'))
        for path in _PARTS:
            relative = path.relative_to(_REPO_ROOT).as_posix()
            with self.subTest(path=relative):
                self.assertIn(relative, rules['exact'])
                self.assertTrue(sensitive_paths.is_sensitive(relative))
        self.assertTrue(sensitive_paths.is_sensitive(
            'tools/llmtool/llmtool/remote_test.py'))

    def test_moved_names_are_still_module_attributes(self):
        for name in _PUBLIC_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(remote_test, name))
        for path in _PARTS:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names = [node.name]
                elif isinstance(node, ast.Assign):
                    names = [target.id for target in node.targets
                             if isinstance(target, ast.Name)]
                else:
                    continue
                for name in names:
                    with self.subTest(part=path.name, name=name):
                        self.assertTrue(hasattr(remote_test, name))


if __name__ == '__main__':
    unittest.main()
