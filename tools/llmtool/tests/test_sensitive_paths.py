import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from llmtool import sensitive_paths


# Every exact entry must exist. A glob may guard a path that has not landed
# yet; list it here so the existence check skips only that pattern.
FORWARD_GUARD_GLOBS = {
    '.claude/skills/**',
}


class SensitivePathsTests(unittest.TestCase):
    def test_real_list_path_table(self):
        positives = (
            'CLAUDE.md', 'AGENTS.md',
            'scripts/llmtool_monitor.py',
            'tools/llmtool/policy.json',
            'tools/llmtool/worker-dispatch.json',
            'tools/llmtool/developer-routes.json',
            'tools/llmtool/sensitive-paths.json',
            'tools/llmtool/llmtool/task_store.py',
            'tools/llmtool/llmtool/task_submission_cli.py',
            'tools/llmtool/llmtool/authorization_ledger.py',
            'tools/llmtool/llmtool/run_budget.py',
            'tools/llmtool/llmtool/high_debug.py',
            'tools/llmtool/llmtool/merge_train.py',
            'tools/llmtool/llmtool/merge_train_ops.py',
            'tools/llmtool/llmtool/developer_routes/agy.py',
            'tools/llmtool/llmtool/developer_routes/gw_agent.py',
            '.claude/skills/safe-git/SKILL.md',
            '.github/workflows/x.yml',
            'tools/mac-runner/a.sh',
            'tools/api-agent-gateway/api_agent_gateway/app.py',
        )
        negatives = (
            'skills/safe-git/SKILL.md',
            'claude.md', 'docs/CLAUDE.md', 'tools/llmtool/AGENTS.md',
            '.claude/settings.json',
            'tools/llmtool/llmtool/chat.py',
            'tools/llmtool/llmtool/merge_helper.py',
            'tools/llmtool/llmtool/developer_routes/README.md',
            'tools/llmtool/llmtool/self_update.py',
            'tools/llmtool/llmtool/owner_tty_issuer.py',
            'tools/llmtool/tests/test_task_advance.py',
            'tools/api-agent-gateway/README.md',
        )
        for path in positives:
            with self.subTest(path=path):
                self.assertTrue(sensitive_paths.is_sensitive(path))
        for path in negatives:
            with self.subTest(path=path):
                self.assertFalse(sensitive_paths.is_sensitive(path))

    def test_rename_checks_both_names(self):
        self.assertEqual(
            sensitive_paths.sensitive_matches([{
                'filename': 'docs/new-name.md',
                'previous_filename': 'tools/llmtool/llmtool/task_advance.py',
            }]),
            ('tools/llmtool/llmtool/task_advance.py',),
        )
        self.assertEqual(sensitive_paths.sensitive_matches([{
            'filename': 'docs/new.md', 'previous_filename': 'docs/old.md',
        }]), ())

    def test_invalid_file_entry_marker(self):
        for files in (None, {}, [None], [{'filename': ''}],
                      [{'filename': 'docs/a', 'previous_filename': ''}]):
            with self.subTest(files=files):
                self.assertEqual(sensitive_paths.sensitive_matches(files),
                                 (sensitive_paths.INVALID_FILE_ENTRY,))

    def test_valid_temporary_list_supports_glob_and_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'list.json'
            path.write_text(json.dumps({
                'schema_version': 1, 'exact': ['one'],
                'prefixes': ['secret/'], 'globs': ['*.key'],
            }), encoding='utf-8')
            with mock.patch.object(sensitive_paths, '_list_path', return_value=path):
                self.assertTrue(sensitive_paths.is_sensitive('secret/file'))
                self.assertTrue(sensitive_paths.is_sensitive('file.key'))
                self.assertFalse(sensitive_paths.is_sensitive('ordinary.txt'))

    def test_malformed_lists_fail_closed(self):
        valid = {'schema_version': 1, 'exact': ['one'],
                 'prefixes': [], 'globs': []}
        malformed = (
            ('missing file', None),
            ('oversize file', b' ' * (sensitive_paths.MAX_LIST_BYTES + 1)),
            ('bad utf8', b'\xff'),
            ('bad json', b'{'),
            ('duplicate keys', b'{"schema_version":1,"schema_version":1,"exact":[],"prefixes":[],"globs":[]}'),
            ('top-level array', b'[]'),
            ('unknown field', {**valid, 'extra': 1}),
            ('missing field', {'schema_version': 1, 'exact': ['one'], 'prefixes': []}),
            ('boolean version', {**valid, 'schema_version': True}),
            ('wrong version', {**valid, 'schema_version': 2}),
            ('empty exact', {**valid, 'exact': []}),
            ('non-list field', {**valid, 'globs': 'x'}),
            ('empty entry', {**valid, 'exact': ['']}),
            ('non-string entry', {**valid, 'exact': [1]}),
            ('absolute entry', {**valid, 'exact': ['/root/file']}),
            ('backslash entry', {**valid, 'exact': ['a\\b']}),
            ('parent component', {**valid, 'exact': ['a/../b']}),
            ('prefix without slash', {**valid, 'prefixes': ['secret']}),
        )
        for label, content in malformed:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'list.json'
                if content is not None:
                    if isinstance(content, bytes):
                        path.write_bytes(content)
                    else:
                        path.write_text(json.dumps(content), encoding='utf-8')
                with mock.patch.object(sensitive_paths, '_list_path', return_value=path):
                    self.assertEqual(
                        sensitive_paths.sensitive_matches([]),
                        (sensitive_paths.SENSITIVE_LIST_UNREADABLE,),
                    )
                    self.assertTrue(sensitive_paths.is_sensitive('ordinary.txt'))

    def test_list_entries_resolve_from_repository_root(self):
        list_path = sensitive_paths._list_path()
        rules = json.loads(list_path.read_text(encoding='utf-8'))
        repository_root = Path(__file__).resolve().parents[3]
        for path in rules['exact']:
            with self.subTest(path=path):
                self.assertTrue((repository_root / path).is_file())
        for prefix in rules['prefixes']:
            with self.subTest(prefix=prefix):
                self.assertTrue((repository_root / prefix).is_dir())
        for pattern in rules['globs']:
            with self.subTest(pattern=pattern):
                if pattern not in FORWARD_GUARD_GLOBS:
                    self.assertTrue(list(repository_root.glob(pattern)))

    def test_wp_g_list_changes(self):
        # #1502 WP-G section 6: required additions and removals.
        rules = json.loads(sensitive_paths._list_path().read_text(encoding='utf-8'))
        for path in ('CLAUDE.md', 'AGENTS.md', 'tools/llmtool/policy.json',
                     'tools/llmtool/llmtool/run_budget.py',
                     'tools/llmtool/llmtool/high_debug.py',
                     'scripts/llmtool_monitor.py'):
            with self.subTest(exact=path):
                self.assertIn(path, rules['exact'])
        for pattern in ('.claude/skills/**', 'tools/llmtool/llmtool/merge_train*.py',
                        'tools/llmtool/llmtool/developer_routes/*.py'):
            with self.subTest(glob=pattern):
                self.assertIn(pattern, rules['globs'])
        for removed in ('tools/llmtool/llmtool/self_update.py',
                        'tools/llmtool/llmtool/owner_tty_issuer.py'):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, rules['exact'])
        self.assertEqual(len(rules['exact']), len(set(rules['exact'])))
