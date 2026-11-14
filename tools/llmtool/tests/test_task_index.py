"""#1838: the task status index keeps an optional TUI projection of each readable record."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import chat_tui_status, task_index


def _record(task_id, **fields):
    """A synthetic TaskStore-shaped record (no real Task data, no credential-shaped values)."""
    base = {'task_id': task_id, 'state': 'PM_RESPONDED', 'developer': None,
            'request': 'synthetic request\nsecond line', 'created_at': '2026-11-14T00:00:00Z',
            'updated_at': '2026-11-14T01:00:00Z', 'pm_session_id': None}
    base.update(fields)
    return base


class _Store:
    def __init__(self, root, records):
        self.root = root
        self.records = records

    def get(self, task_id):
        return dict(self.records[task_id])

    def task_ids(self):
        return sorted(self.records)


class TaskIndexTuiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name).resolve() / 'state'
        self.root = self.state / 'tasks'
        self.root.mkdir(parents=True)
        os.chmod(self.state, 0o700)
        os.chmod(self.root, 0o700)
        self.task_id = 'a' * 32
        self.store = _Store(self.root, {self.task_id: _record(self.task_id)})
        (self.root / (self.task_id + '.json')).write_text('{}')
        self.path = self.state / 'task-status-index.json'

    def saved(self):
        index = task_index.TaskStatusIndex(self.store)
        _record_read, readable = index.remember_full_scan(self.task_id)
        self.assertTrue(readable)
        index.flush()
        return json.loads(self.path.read_text())['entries'][self.task_id]

    def test_written_entry_carries_the_tui_projection_and_reads_back(self):
        entry = self.saved()
        self.assertEqual(entry['tui'], chat_tui_status.index_summary(_record(self.task_id)))
        self.assertEqual(entry['tui']['name'], 'synthetic request')
        self.assertTrue(task_index._valid_tui(entry['tui']))
        self.assertEqual(task_index.TaskStatusIndex(self.store).entries[self.task_id], entry)
        entries, reason = task_index.display_entries(self.store)
        self.assertIsNone(reason)
        self.assertEqual(entries[self.task_id], entry)
        self.assertEqual(entry['stat'], task_index.record_key(self.store, self.task_id))

    def test_projection_failure_keeps_the_status_entry_without_tui(self):
        with patch.object(chat_tui_status, 'index_summary', side_effect=ValueError('shape')):
            entry = self.saved()
        self.assertNotIn('tui', entry)
        self.assertEqual(entry['created_at'], '2026-11-14T00:00:00Z')
        self.assertEqual(task_index.TaskStatusIndex(self.store).entries[self.task_id], entry)

    def test_oversize_payload_drops_only_the_tui_projection(self):
        tui = self.saved()['tui']
        full = self.path.stat().st_size
        projection = len(json.dumps(tui, sort_keys=True, separators=(',', ':')))
        os.unlink(self.path)
        # A bound the full payload exceeds but the payload without the projection fits.
        with patch.object(task_index, '_MAX_INDEX_BYTES', full - projection // 2):
            entry = self.saved()
        self.assertNotIn('tui', entry)
        self.assertEqual(entry['readable'], True)

    def test_invalid_tui_rejects_the_index(self):
        entry = self.saved()
        for bad in ({'stage': 7}, {'events': [['t', 'TASK', 'x', True]] * 4},
                    {'event_count': 0, 'events': [[None, 'TASK', 'x', True]]},
                    {'events': [[None, 'TASK', 'x' * (task_index.TUI_TEXT_LIMIT + 1), True]]},
                    {'prs': [True]}, {'extra': 1}):
            with self.subTest(bad):
                broken = dict(entry, tui=dict(entry['tui'], **bad))
                self.assertFalse(task_index._valid_entry(self.task_id, broken))
                data = json.loads(self.path.read_text())
                data['entries'][self.task_id] = broken
                self.path.write_text(json.dumps(data))
                self.assertEqual(task_index.display_entries(self.store),
                                 (None, 'unreadable'))
                self.assertEqual(task_index.TaskStatusIndex(self.store).entries, {})
        unreadable = {'stat': entry['stat'], 'readable': False, 'created_at': None,
                      'pm_session_id': None, 'tui': entry['tui']}
        self.assertFalse(task_index._valid_entry(self.task_id, unreadable))

    def test_display_entries_reasons_and_read_only(self):
        self.assertEqual(task_index.display_entries(self.store), (None, 'missing'))
        self.assertFalse(self.path.exists())
        self.saved()
        data = json.loads(self.path.read_text())
        data['fingerprint'] = '0' * 64
        self.path.write_text(json.dumps(data))
        self.assertEqual(task_index.display_entries(self.store), (None, 'stale'))
        self.path.write_text('{not json')
        self.assertEqual(task_index.display_entries(self.store), (None, 'unreadable'))
        os.chmod(self.path, 0o644)
        self.assertEqual(task_index.display_entries(self.store), (None, 'unreadable'))
        os.chmod(self.state, 0o777)
        try:
            self.assertEqual(task_index.display_entries(self.store), (None, 'unavailable'))
        finally:
            os.chmod(self.state, 0o700)
        self.assertEqual(task_index.display_entries(object()), (None, 'unavailable'))
        self.assertEqual(sorted(p.name for p in self.state.iterdir()),
                         ['task-status-index.json', 'tasks'])


_PACKAGE = Path(task_index.__file__).resolve().parent
_EDIT = b'\n# synthetic edit (fingerprint scope test)\n'
# Modules the closure must reach: the roots, the TUI projection (function-local imports)
# and the record-validation modules the old hand list missed.
_REQUIRED = ('__init__.py', 'task_index.py', 'task_store.py', 'chat_tui_status.py',
             'chat_tui_events.py', 'task_verification.py', 'action_contract.py',
             'test_output_excerpts.py', 'diagnostics.py')
_NAMED = ('task_verification.py', 'action_contract.py', 'test_output_excerpts.py',
          'diagnostics.py')


def _package_modules():
    return sorted(path.relative_to(_PACKAGE).as_posix() for path in _PACKAGE.rglob('*.py')
                  if '__pycache__' not in path.parts)


def _unrelated():
    """An existing package module outside the closure (chat_tui.py when it is outside)."""
    closure = set(task_index.fingerprint_modules())
    outside = [module for module in _package_modules() if module not in closure]
    if not outside:
        raise AssertionError('every package module is in the validator closure')
    return 'chat_tui.py' if 'chat_tui.py' in outside else outside[0]


def build(base, name, edit=(), add=None, drop=()):
    """A synthetic build: a copy of the whole package, with ``edit`` modules changed, an
    ``add`` module (synthetic, imported by nothing) added and ``drop`` modules removed."""
    directory = Path(base) / name
    shutil.copytree(_PACKAGE, directory, ignore=shutil.ignore_patterns('__pycache__'))
    for module in edit:
        append(directory, module, _EDIT)
    if add is not None:
        (directory / add).write_bytes(b'"""Synthetic unrelated module."""\n')
    for module in drop:
        (directory / module).unlink()
    return directory


def append(directory, module, text):
    path = directory / module
    path.write_bytes(path.read_bytes() + text)


class ValidatorFingerprintScopeTests(unittest.TestCase):
    """The fingerprint covers the AST import closure of task_store.py and task_index.py,
    so builds that differ only outside it keep the index."""

    saved = TaskIndexTuiTests.saved

    def setUp(self):
        TaskIndexTuiTests.setUp(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.builds = temp.name
        self.real = task_index.validator_fingerprint
        self.unrelated = _unrelated()

    def fingerprint(self, directory):
        return self.real(package=directory)

    def assert_unavailable(self, directory):
        """An index written by the current build is unavailable to ``directory``."""
        with self.assertRaises(task_index.FingerprintUnavailable):
            self.fingerprint(directory)
        if not self.path.exists():
            self.saved()
        with patch.object(task_index, 'validator_fingerprint',
                          lambda: self.real(package=directory)):
            self.assertEqual(task_index.display_entries(self.store), (None, 'unavailable'))
            index = task_index.TaskStatusIndex(self.store)
            self.assertFalse(index.enabled)
            self.assertEqual(index.entries, {})

    def test_closure_is_rooted_at_store_and_index_and_follows_imports(self):
        modules = task_index.fingerprint_modules()
        self.assertEqual(modules, sorted(set(modules)))
        for module in _REQUIRED:
            with self.subTest(module):
                self.assertIn(module, modules)
        for module in modules:
            self.assertTrue((_PACKAGE / module).is_file(), module)
        self.assertNotIn(self.unrelated, modules)
        # The closure and the fingerprint depend on module names and bytes, not location.
        copy = build(self.builds, 'copy')
        self.assertEqual(task_index.fingerprint_modules(package=copy), modules)
        self.assertEqual(self.fingerprint(copy), self.real())
        self.assertRegex(self.real(), r'^[0-9a-f]{64}$')

    def test_unrelated_module_changes_keep_the_fingerprint(self):
        same = build(self.builds, 'same')
        other = build(self.builds, 'other', edit=(self.unrelated,),
                      add='synthetic_unrelated.py')
        # A module outside the closure is never parsed: a syntax error there is harmless.
        (other / 'synthetic_broken.py').write_bytes(b'def (:\n')
        without = build(self.builds, 'without', drop=(self.unrelated,))
        self.assertEqual(self.fingerprint(same), self.real())
        self.assertEqual(self.fingerprint(other), self.real())
        self.assertEqual(self.fingerprint(without), self.real())

    def test_each_closure_module_change_changes_the_fingerprint(self):
        directory = build(self.builds, 'edits')
        seen = {self.real()}
        for module in task_index.fingerprint_modules():
            with self.subTest(module):
                original = (directory / module).read_bytes()
                append(directory, module, _EDIT)
                edited = self.fingerprint(directory)
                (directory / module).write_bytes(original)
                self.assertNotIn(edited, seen)
                seen.add(edited)
        self.assertEqual(self.fingerprint(directory), self.real())

    def test_named_validation_module_change_invalidates_an_existing_index(self):
        entry = self.saved()
        self.assertEqual(task_index.display_entries(self.store)[0][self.task_id], entry)
        for module in _NAMED:
            with self.subTest(module):
                edited = build(self.builds, 'edit-' + module, edit=(module,))
                with patch.object(task_index, 'validator_fingerprint',
                                  lambda: self.real(package=edited)):
                    self.assertEqual(task_index.display_entries(self.store), (None, 'stale'))
                    self.assertEqual(task_index.TaskStatusIndex(self.store).entries, {})
        # The index on disk is untouched by the stale reads above.
        self.assertEqual(task_index.display_entries(self.store)[0][self.task_id], entry)

    def test_new_package_import_added_to_task_store_is_picked_up(self):
        forms = {
            'relative': b'\nfrom . import synthetic_dep\n',
            'relative module': b'\nfrom .synthetic_dep import SYNTHETIC\n',
            'function-local': (b'\ndef _synthetic_local():\n'
                               b'    from .synthetic_dep import SYNTHETIC\n'
                               b'    return SYNTHETIC\n'),
            'absolute': b'\nimport llmtool.synthetic_dep\n',
            'absolute from': b'\nfrom llmtool import synthetic_dep\n',
        }
        for form, text in forms.items():
            with self.subTest(form):
                directory = build(self.builds, 'import-' + form.replace(' ', '-'))
                (directory / 'synthetic_dep.py').write_bytes(b'SYNTHETIC = 1\n')
                before = self.fingerprint(directory)
                self.assertEqual(before, self.real())
                append(directory, 'synthetic_dep.py', _EDIT)
                self.assertEqual(self.fingerprint(directory), before)
                append(directory, 'task_store.py', text)
                self.assertIn('synthetic_dep.py',
                              task_index.fingerprint_modules(package=directory))
                added = self.fingerprint(directory)
                self.assertNotEqual(added, before)
                append(directory, 'synthetic_dep.py', _EDIT)
                self.assertNotIn(self.fingerprint(directory), (before, added))

    def test_subpackage_import_includes_the_package_init(self):
        directory = build(self.builds, 'subpackage')
        nested = directory / 'synthetic_pkg'
        nested.mkdir()
        (nested / '__init__.py').write_bytes(b'"""Synthetic package."""\n')
        (nested / 'leaf.py').write_bytes(b'from .. import task_index\nVALUE = 1\n')
        (nested / 'other.py').write_bytes(b'VALUE = 2\n')
        self.assertEqual(self.fingerprint(directory), self.real())
        append(directory, 'task_index.py', b'\nfrom .synthetic_pkg import leaf\n')
        modules = task_index.fingerprint_modules(package=directory)
        self.assertIn('synthetic_pkg/__init__.py', modules)
        self.assertIn('synthetic_pkg/leaf.py', modules)
        self.assertNotIn('synthetic_pkg/other.py', modules)
        before = self.fingerprint(directory)
        append(directory, 'synthetic_pkg/__init__.py', _EDIT)
        after_init = self.fingerprint(directory)
        self.assertNotEqual(after_init, before)
        append(directory, 'synthetic_pkg/other.py', _EDIT)
        self.assertEqual(self.fingerprint(directory), after_init)
        (nested / '__init__.py').unlink()
        self.assert_unavailable(directory)

    def test_missing_closure_module_makes_the_index_unavailable(self):
        for module in ('task_store.py', 'task_index.py', 'diagnostics.py', '__init__.py'):
            with self.subTest(module):
                self.assert_unavailable(build(self.builds, 'missing-' + module,
                                              drop=(module,)))

    def test_unparsable_or_unresolved_closure_import_makes_the_index_unavailable(self):
        cases = {
            'unparsable': ('action_contract.py', b'\ndef (:\n'),
            'unresolved module': ('task_store.py',
                                  b'\nfrom .synthetic_missing import SYNTHETIC\n'),
            'unresolved absolute': ('task_store.py', b'\nimport llmtool.synthetic_missing\n'),
            'beyond the package': ('task_store.py', b'\nfrom .. import synthetic_outside\n'),
            'unresolved name': ('task_store.py',
                                b'\nfrom .task_index import synthetic_missing_name\n'),
        }
        for case, (module, text) in cases.items():
            with self.subTest(case):
                directory = build(self.builds, 'broken-' + case.replace(' ', '-'))
                append(directory, module, text)
                self.assert_unavailable(directory)

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'file modes do not stop reads by root')
    def test_unreadable_closure_module_makes_the_index_unavailable(self):
        directory = build(self.builds, 'unreadable')
        os.chmod(directory / 'test_output_excerpts.py', 0)
        try:
            self.assert_unavailable(directory)
        finally:
            os.chmod(directory / 'test_output_excerpts.py', 0o600)

    def test_index_from_another_build_with_identical_closure_is_used(self):
        writer = build(self.builds, 'writer', edit=(self.unrelated,))
        reader = build(self.builds, 'reader', add='synthetic_unrelated.py')
        with patch.object(task_index, 'validator_fingerprint',
                          lambda: self.real(package=writer)):
            entry = self.saved()
        with patch.object(task_index, 'validator_fingerprint',
                          lambda: self.real(package=reader)):
            entries, reason = task_index.display_entries(self.store)
            self.assertIsNone(reason)
            self.assertEqual(entries[self.task_id], entry)
            self.assertEqual(task_index.TaskStatusIndex(self.store).entries[self.task_id], entry)
        # Another build whose closure modules differ finds the same index stale.
        changed = build(self.builds, 'changed', edit=('chat_tui_status.py',))
        with patch.object(task_index, 'validator_fingerprint',
                          lambda: self.real(package=changed)):
            self.assertEqual(task_index.display_entries(self.store), (None, 'stale'))
            self.assertEqual(task_index.TaskStatusIndex(self.store).entries, {})

    def test_discovery_never_imports_modules(self):
        directory = build(self.builds, 'no-import')
        (directory / 'synthetic_dep.py').write_bytes(
            b'raise RuntimeError("synthetic: discovery must not import")\n')
        append(directory, 'task_store.py', b'\nfrom . import synthetic_dep\n')
        loaded = set(sys.modules)
        # Importing synthetic_dep would raise; discovery only reads and parses it.
        self.assertIn('synthetic_dep.py', task_index.fingerprint_modules(package=directory))
        self.fingerprint(directory)
        self.assertEqual([name for name in set(sys.modules) - loaded if 'synthetic' in name],
                         [])

    def test_parse_cache_never_hides_an_edit(self):
        directory = build(self.builds, 'cache')
        self.assertEqual(self.fingerprint(directory), self.real())
        task_index._SCAN_CACHE.clear()
        self.assertEqual(self.fingerprint(directory), self.real())
        append(directory, 'task_store.py', b'\nfrom .synthetic_missing import SYNTHETIC\n')
        with self.assertRaises(task_index.FingerprintUnavailable):
            self.fingerprint(directory)


if __name__ == '__main__':
    unittest.main()
