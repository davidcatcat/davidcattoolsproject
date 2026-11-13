"""#1502 WP-E (E9): the gw Agent worker id is gw-agent.

Records written with the retired id company-gw-agent still read, as gw-agent,
through the one alias helper (worker_dispatch.stored_worker_id). A new
dispatch-check, delegate, review or worker evaluation naming the retired id is
refused with WORKER_ID_RETIRED_ALIAS. The company-gw direct route stays retired.
"""

import argparse
import ast
import contextlib
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import dispatch_check, review_dispatch, task_completion, task_submission_cli
from llmtool import task_store, worker_dispatch
from llmtool.errors import LLMToolError
import worker_policy_fixture

RETIRED = 'company-gw-agent'
TASK = 'a' * 32
ASSIGNMENT = 'b' * 32
BINDING = {'assignment_id': ASSIGNMENT, 'source_sha': 'c' * 40, 'contract_digest': 'd' * 64}


class _PolicyCase(unittest.TestCase):
    def setUp(self):
        # Every known key ALLOWED: a refusal below comes from the retired id itself.
        self.policy_path = worker_policy_fixture.install(self)
        repository = patch.object(worker_dispatch, 'repository_config', return_value={
            'slug': 'davidcatcat/davidcattoolsproject', 'dispatch_issue': 289,
            'dispatch_evidence_pr': 283})
        repository.start()
        self.addCleanup(repository.stop)


class AliasHelperTests(unittest.TestCase):
    def test_one_alias_maps_the_retired_id_to_gw_agent(self):
        self.assertEqual(worker_dispatch.RETIRED_WORKER_ALIASES, {RETIRED: 'gw-agent'})
        self.assertEqual(worker_dispatch.stored_worker_id(RETIRED), 'gw-agent')
        for value in ('gw-agent', 'company-gw', 'agy', 'claude-developer', 'unknown', '',
                      None, 7, ['company-gw-agent']):
            with self.subTest(value=value):
                self.assertEqual(worker_dispatch.stored_worker_id(value), value)

    def test_gw_agent_is_the_current_key_and_the_retired_id_is_not(self):
        self.assertIn('gw-agent', worker_dispatch.WORKERS)
        self.assertNotIn(RETIRED, worker_dispatch.WORKERS | worker_dispatch.DEBUG_WORKERS)
        self.assertIn('company-gw', worker_dispatch.WORKERS)   # still a key, still retired
        self.assertEqual(worker_dispatch.worker_key_for('gw-agent'), 'gw-agent')

    def test_shipped_policy_uses_gw_agent_with_the_same_values(self):
        raw = worker_dispatch._DEFAULT_POLICY_PATH.read_bytes()
        workers = json.loads(raw)['workers']
        self.assertNotIn(RETIRED, workers)
        self.assertEqual(
            {key: workers['gw-agent'][key] for key in ('dispatch', 'decided_by', 'evidence')},
            {'dispatch': 'ALLOWED', 'decided_by': 'Owner',
             'evidence': worker_dispatch._GW_AGENT_OWNER_REF})


class RetiredIdRefusalTests(_PolicyCase):
    def test_evaluation_refuses_the_retired_id(self):
        decision = worker_dispatch.evaluate(RETIRED)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, 'WORKER_ID_RETIRED_ALIAS')
        self.assertEqual(decision.worker_key, RETIRED)
        # Whatever the task or reference given.
        decision = worker_dispatch.evaluate(
            RETIRED, 'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
            '#issuecomment-1', TASK)
        self.assertEqual(decision.reason_code, 'WORKER_ID_RETIRED_ALIAS')
        allowed = worker_dispatch.evaluate('gw-agent')
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.reason_code, 'OWNER_POLICY_ALLOWED')

    def test_company_gw_direct_route_stays_refused(self):
        worker_policy_fixture.update(self.policy_path, 'company-gw', 'PAUSED')
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code, 'OWNER_PAUSED')
        with self.assertRaisesRegex(LLMToolError, '^COMPANY_GW_DIRECT_RETIRED'):
            task_submission_cli.run_task_delegate(
                SimpleNamespace(provider='company-gw'), store=_NoStore())

    def test_policy_file_with_the_retired_key_is_refused_for_every_worker(self):
        value = worker_policy_fixture.read(self.policy_path)
        value['workers'][RETIRED] = value['workers'].pop('gw-agent')
        worker_policy_fixture.write(self.policy_path, value)
        for key in ('gw-agent', 'agy', 'claude-developer'):
            with self.subTest(worker=key):
                self.assertEqual(worker_dispatch.evaluate(key).reason_code,
                                 'WORKER_POLICY_INVALID')

    def test_dispatch_check_refuses_the_retired_id(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = dispatch_check.run_dispatch_check(SimpleNamespace(
                worker=RETIRED, owner_assignment_ref=None, task_id=None))
        self.assertEqual(code, 3)
        printed = json.loads(out.getvalue())
        self.assertEqual((printed['worker'], printed['allowed'], printed['reason']),
                         (RETIRED, False, 'WORKER_ID_RETIRED_ALIAS'))

    def test_delegate_refuses_the_retired_id_and_names_gw_agent(self):
        parser = argparse.ArgumentParser()
        actions = parser.add_subparsers(dest='subcommand')
        task_submission_cli.add_task_submission_parsers(actions)
        provider = next(action for action in actions.choices['delegate']._actions
                        if '--provider' in action.option_strings)
        self.assertIn(RETIRED, provider.choices)
        with self.assertRaises(LLMToolError) as raised:
            task_submission_cli.run_task_delegate(
                SimpleNamespace(provider=RETIRED, background=False), store=_NoStore())
        message = str(raised.exception)
        self.assertTrue(message.startswith('WORKER_ID_RETIRED_ALIAS:'), message)
        self.assertIn('use gw-agent', message)

    def test_review_policy_evaluation_refuses_the_retired_id(self):
        task = {'task_id': TASK, 'developer': {'provider_profile': 'gw-agent'}}
        self.assertEqual(review_dispatch._policy(task, RETIRED).reason_code,
                         'WORKER_ID_RETIRED_ALIAS')


class _NoStore:
    """A TaskStore stand-in: a refused delegate never reads or writes a Task."""

    def __getattr__(self, name):
        raise AssertionError(f'refused delegate touched the store: {name}')


def _legacy_entry(action, attempt_id, **extra):
    return dict({'attempt_id': attempt_id, 'worker_key': RETIRED, 'action': action,
                 'decision': 'ALLOWED', 'reason_code': 'OWNER_POLICY_ALLOWED',
                 'policy_sha256': 'e' * 64, 'at': '2026-11-12T00:00:00Z'}, **extra)


class StoredRecordReadTests(_PolicyCase):
    def test_transition_recorded_under_the_retired_id_authorizes_gw_agent(self):
        record = {'dispatch_audit': [_legacy_entry('DISPATCH', 'f' * 32, **BINDING)]}
        self.assertEqual(worker_dispatch._latest_transition_authorization(
            record, 'gw-agent', 'DISPATCH', BINDING), 'f' * 32)
        self.assertIsNone(worker_dispatch._latest_transition_authorization(
            record, 'agy', 'DISPATCH', BINDING))

    def test_adapter_authorization_already_used_under_the_retired_id_stays_consumed(self):
        transition = _legacy_entry('DISPATCH', 'f' * 32, **BINDING)
        used = _legacy_entry('DISPATCH', '1' * 32, authorization_id='f' * 32, **BINDING)
        record = {'task_id': TASK, 'task_binding': {'contract_digest': 'd' * 64},
                  'developer': {'state': 'DEVELOPER_DISPATCHING', 'provider_profile': 'gw-agent',
                                'assignment_id': ASSIGNMENT, 'source_sha': 'c' * 40},
                  'dispatch_audit': [transition, used]}
        attempt = dict(used, worker_key='gw-agent', attempt_id='2' * 32)
        self.assertEqual(worker_dispatch._adapter_authorization_block_reason(record, attempt),
                         'WORKER_DISPATCH_AUTHORIZATION_CONSUMED')
        record['dispatch_audit'] = [transition]
        self.assertIsNone(worker_dispatch._adapter_authorization_block_reason(record, attempt))

    def _repair_change(self, audit):
        developer = {'assignment_id': ASSIGNMENT, 'selection_source': 'PM',
                     'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'gw-agent',
                     'provider_type': 'gw-agent-cli', 'model': 'served-model',
                     'source_sha': 'c' * 40, 'direct_result_callback': {'review_return': {
                         'state': 'RETURNED', 'next_step': 'RETURN_TO_DEVELOPER_ONCE'}}}
        current = {'task_id': TASK, 'dispatch_audit': list(audit), 'developer': developer}
        changes = {'developer': dict(developer, state='DEVELOPER_PENDING')}
        return current, changes

    def test_repair_recorded_under_the_retired_id_counts_toward_the_limit(self):
        current, changes = self._repair_change([])
        self.assertEqual(worker_dispatch.task_store_attempts(current, changes),
                         [('gw-agent', 'REPAIR')])
        self.assertIsNone(worker_dispatch.authorize_task_store_change(current, changes))
        self.assertEqual(current['dispatch_audit'][-1]['worker_key'], 'gw-agent')

        current, changes = self._repair_change([_legacy_entry('REPAIR', '3' * 32)])
        denied = worker_dispatch.authorize_task_store_change(current, changes)
        self.assertIsInstance(denied, worker_dispatch.WorkerDispatchDenied)
        self.assertEqual(denied.dispatch_code, 'WORKER_REPAIR_LIMIT_REACHED')
        # The stored entry is kept as written; only the new entry uses gw-agent.
        self.assertEqual([entry['worker_key'] for entry in current['dispatch_audit']],
                         [RETIRED, 'gw-agent'])

    def test_task_store_read_maps_the_retired_profile_and_route(self):
        # Codex v1 F1 on PR #1774: TaskStore reads stored Task JSON through the
        # shared alias; test_task_store covers the full on-disk round trip.
        stored = {'task_id': TASK, 'developer': {'provider_profile': RETIRED, 'state': 'S'},
                  'pm_assignment': {'route': RETIRED, 'risk': 'LOW'}}
        stored['record_sha256'] = task_store._digest(stored)
        read = task_store._read_current_worker_ids(stored)
        self.assertEqual(read['developer'], {'provider_profile': 'gw-agent', 'state': 'S'})
        self.assertEqual(read['pm_assignment'], {'route': 'gw-agent', 'risk': 'LOW'})
        self.assertEqual(read['record_sha256'], task_store._digest(read))
        # The stored copy is not changed in place.
        self.assertEqual(stored['developer']['provider_profile'], RETIRED)
        self.assertEqual(stored['pm_assignment']['route'], RETIRED)

        for current in ({'task_id': TASK, 'developer': None, 'pm_assignment': None},
                        {'task_id': TASK, 'developer': {'provider_profile': 'gw-agent'},
                         'pm_assignment': {'route': 'company-gw'}}):
            with self.subTest(current=current):
                self.assertIs(task_store._read_current_worker_ids(current), current)

        tampered = dict(stored, task_id='e' * 32)
        with self.assertRaises(LLMToolError):
            task_store._read_current_worker_ids(tampered)

    def test_task_store_reads_the_stored_pm_response_route_through_the_alias(self):
        # New writes naming the retired id are refused in test_task_store.
        response = {'route': RETIRED, 'risk': 'LOW'}
        self.assertEqual(task_store._current_route(response), {'route': 'gw-agent', 'risk': 'LOW'})
        self.assertEqual(response['route'], RETIRED)
        self.assertEqual(task_store._current_route({'risk': 'LOW'}), {'risk': 'LOW'})

    def test_task_record_with_the_retired_profile_keeps_its_review_family(self):
        for model, family in (('claude-opus-5-5', 'claude'), ('gpt-6-luna', 'codex'),
                              ('served-model', 'other'), (None, 'other')):
            with self.subTest(model=model):
                legacy = {'provider_profile': RETIRED, 'model': model}
                current = {'provider_profile': 'gw-agent', 'model': model}
                self.assertEqual(task_completion._reviewer_author_family(legacy), family)
                self.assertEqual(task_completion._reviewer_author_family(legacy),
                                 task_completion._reviewer_author_family(current))


_LLMTOOL_ROOT = Path(__file__).resolve().parent.parent     # tools/llmtool
_REPO_ROOT = _LLMTOOL_ROOT.parent.parent
_PACKAGE = _LLMTOOL_ROOT / 'llmtool'
_MONITOR = _REPO_ROOT / 'scripts' / 'llmtool_monitor.py'
_POLICY = _LLMTOOL_ROOT / 'worker-dispatch.json'
_GW_ROOT_STATS = _LLMTOOL_ROOT / 'scripts' / 'gw_root_stats.py'


def _assignments_to(name):
    def find(tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                yield node
    return find


def _delegate_provider_argument(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'add_argument'
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'delegate'
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == '--provider'):
            yield node


def _module_constant(name):
    """A top-level assignment to name (a test module's legacy fixture constant)."""
    def find(tree):
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name):
                yield node
    return find


# The only places the retired id may be written: the legacy alias tables
# (the alias map and the retired-route parse list of task delegate).
_ALIAS_TABLES = {
    'worker_dispatch.py': _assignments_to('RETIRED_WORKER_ALIASES'),
    'task_submission_cli.py': _delegate_provider_argument,
}
# Test modules under scripts/ load old records on purpose; each may name the
# retired id only in one top-level fixture constant.
_FIXTURE_CONSTANT = _module_constant('LEGACY_WORKER_ID')
# Modules that read stored records naming the retired id: they use the shared
# alias (worker_dispatch.stored_worker_id) and keep no table of their own.
_SHARED_ALIAS_READERS = ('task_store.py',)


def _names_retired_id(node):
    return any(isinstance(child, ast.Constant) and child.value == RETIRED
               for child in ast.walk(node))


def _allowed_spans(table):
    """(line, start, end) of each string constant in the table that is exactly
    the retired id; AST columns are UTF-8 byte offsets."""
    return {(node.lineno, node.col_offset, node.end_col_offset)
            for node in ast.walk(table)
            if isinstance(node, ast.Constant) and node.value == RETIRED
            and node.lineno == node.end_lineno}


def _stray_lines(text, find_table=None, required=True):
    """Line numbers naming the retired id outside the one alias table.

    Only the exact retired-id constants inside the table are allowed, so a
    second use on a table line (code or comment) is still reported. With a
    required table finder there must be exactly one table and it must itself
    name the retired id, so the guard cannot pass because a table moved.
    With required=False there may be no table, and then nothing is allowed."""
    allowed = set()
    if find_table is not None:
        tables = list(find_table(ast.parse(text)))
        if (len(tables) > 1 or (required and not tables)
                or (tables and not _names_retired_id(tables[0]))):
            raise AssertionError(f'expected one alias table naming {RETIRED}, '
                                 f'found {len(tables)}')
        if tables:
            allowed = _allowed_spans(tables[0])
    needle = RETIRED.encode('utf-8')
    stray = []
    for number, line in enumerate(text.split('\n'), 1):
        raw = line.encode('utf-8')
        start = raw.find(needle)
        while start != -1:
            if not any(row == number and begin <= start and start + len(needle) <= end
                       for row, begin, end in allowed):
                stray.append(number)
                break
            start = raw.find(needle, start + 1)
    return stray


class RetiredIdGuardTests(unittest.TestCase):
    """The retired id appears only in the legacy alias tables (#1502 WP-E)."""

    def test_package_modules_name_the_retired_id_only_in_alias_tables(self):
        modules = sorted(_PACKAGE.glob('*.py'))
        self.assertTrue(modules)
        for name in _ALIAS_TABLES:
            self.assertTrue((_PACKAGE / name).is_file(), name)
        for path in modules:
            with self.subTest(module=path.name):
                text = path.read_text(encoding='utf-8')
                self.assertEqual(_stray_lines(text, _ALIAS_TABLES.get(path.name)), [])

    def test_task_store_reads_through_the_shared_alias(self):
        for name in _SHARED_ALIAS_READERS:
            with self.subTest(module=name):
                tree = ast.parse((_PACKAGE / name).read_text(encoding='utf-8'))
                imported = {alias.name for node in ast.walk(tree)
                            if isinstance(node, ast.ImportFrom) and node.module == 'worker_dispatch'
                            for alias in node.names}
                self.assertIn('stored_worker_id', imported)
                for table in ('RETIRED_WORKER_ALIASES', 'RETIRED_WORKER_IDS'):
                    self.assertEqual(list(_assignments_to(table)(tree)), [])

    def test_monitor_keeps_no_alias_table_and_reads_through_the_gate(self):
        # Codex v1 F1 on PR #1780: no second table that could drift from the gate.
        text = _MONITOR.read_text(encoding='utf-8')
        self.assertEqual(_stray_lines(text), [])
        tree = ast.parse(text)
        for table in ('RETIRED_WORKER_ALIASES', 'RETIRED_WORKER_IDS'):
            self.assertEqual(list(_assignments_to(table)(tree)), [])
        loads = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == '_checkout_llmtool_module'
                 and [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
                 == ['worker_dispatch', 'stored_worker_id']]
        self.assertTrue(loads)

    def test_audit_readers_use_the_shared_alias_and_keep_no_table(self):
        # High Debug #1780: gw_root_stats and board_check read dispatch audits
        # through worker_dispatch.stored_worker_id, never a table of their own.
        stats_text = _GW_ROOT_STATS.read_text(encoding='utf-8')
        self.assertEqual(_stray_lines(stats_text), [])
        stats_tree = ast.parse(stats_text)
        imported = {alias.name for node in ast.walk(stats_tree)
                    if isinstance(node, ast.ImportFrom)
                    and node.module == 'llmtool.worker_dispatch'
                    for alias in node.names}
        self.assertIn('stored_worker_id', imported)
        board_tree = ast.parse((_PACKAGE / 'board_check.py').read_text(encoding='utf-8'))
        self.assertTrue([node for node in ast.walk(board_tree)
                         if isinstance(node, ast.Attribute) and node.attr == 'stored_worker_id'
                         and isinstance(node.value, ast.Name)
                         and node.value.id == 'worker_dispatch'])
        for tree in (stats_tree, board_tree):
            for table in ('RETIRED_WORKER_ALIASES', 'RETIRED_WORKER_IDS'):
                self.assertEqual(list(_assignments_to(table)(tree)), [])

    def test_script_modules_name_the_retired_id_only_in_a_fixture_constant(self):
        # High Debug #1780 completeness: every *.py under tools/llmtool/scripts
        # and scripts is scanned. A script reads stored ids through the shared
        # alias and never names the retired id; the alias tables and the
        # retired-route parse list live only in the package (checked above).
        # A test module (test_*.py) may name it only in one top-level
        # LEGACY_WORKER_ID fixture constant (Codex v1 F1 on PR #1791).
        scanned = set()
        for directory in (_LLMTOOL_ROOT / 'scripts', _REPO_ROOT / 'scripts'):
            modules = sorted(directory.glob('*.py'))
            with self.subTest(directory=str(directory.relative_to(_REPO_ROOT))):
                self.assertTrue(modules)
            for path in modules:
                scanned.add(path)
                finder = _FIXTURE_CONSTANT if path.name.startswith('test_') else None
                with self.subTest(module=str(path.relative_to(_REPO_ROOT))):
                    self.assertEqual(_stray_lines(path.read_text(encoding='utf-8'), finder,
                                                  required=False), [])
        # The monitor, gw_root_stats and the monitor's legacy-record tests are in the scan.
        for path in (_MONITOR, _GW_ROOT_STATS, _REPO_ROOT / 'scripts' / 'test_llmtool_monitor.py',
                     _REPO_ROOT / 'scripts' / 'test_llmtool_monitor_gw_done.py'):
            self.assertIn(path, scanned)

    def test_dispatch_policy_never_names_the_retired_id(self):
        self.assertEqual(_stray_lines(_POLICY.read_text(encoding='utf-8')), [])

    def test_guard_reports_a_stray_use_and_a_missing_table(self):
        source = ("RETIRED_WORKER_ALIASES = {\n    'company-gw-agent': 'gw-agent',\n}\n"
                  "ROUTE = 'company-gw-agent'\n")
        self.assertEqual(_stray_lines(source, _assignments_to('RETIRED_WORKER_ALIASES')), [4])
        self.assertEqual(_stray_lines(source), [2, 4])
        with self.assertRaisesRegex(AssertionError, 'expected one alias table'):
            _stray_lines("ROUTE = 'gw-agent'\n", _assignments_to('RETIRED_WORKER_ALIASES'))

    def test_guard_reports_a_second_use_on_a_table_line(self):
        # Codex v1 F2 on PR #1791: only the table's own constants are allowed.
        table = _assignments_to('RETIRED_WORKER_ALIASES')
        allowed = "RETIRED_WORKER_ALIASES = {'company-gw-agent': 'gw-agent'}\n"
        self.assertEqual(_stray_lines(allowed, table), [])
        for source in (
                "RETIRED_WORKER_ALIASES = {'company-gw-agent': 'gw-agent'}; "
                "ROUTE = 'company-gw-agent'\n",
                "RETIRED_WORKER_ALIASES = {'company-gw-agent': 'gw-agent'}  # company-gw-agent\n",
                "RETIRED_WORKER_ALIASES = {'company-gw-agent': 'company-gw-agent is retired'}\n",
                "RETIRED_WORKER_ALIASES = {'company-gw-agent': 'gw-agent',  # company-gw-agent\n}\n"):
            with self.subTest(source=source):
                self.assertEqual(_stray_lines(source, table), [1])

    def test_guard_fixture_constant_is_optional_but_single(self):
        fixture = "LEGACY_WORKER_ID = 'company-gw-agent'\nX = {LEGACY_WORKER_ID: 1}\n"
        self.assertEqual(_stray_lines(fixture, _FIXTURE_CONSTANT, required=False), [])
        self.assertEqual(_stray_lines("X = 'gw-agent'\n", _FIXTURE_CONSTANT, required=False), [])
        self.assertEqual(_stray_lines("X = 'company-gw-agent'\n", _FIXTURE_CONSTANT,
                                      required=False), [1])
        self.assertEqual(_stray_lines(fixture + "Y = 'company-gw-agent'\n", _FIXTURE_CONSTANT,
                                      required=False), [3])
        # A function-local constant is not the module fixture.
        self.assertEqual(_stray_lines("def f():\n    LEGACY_WORKER_ID = 'company-gw-agent'\n",
                                      _FIXTURE_CONSTANT, required=False), [2])
        for source in (fixture + fixture, "LEGACY_WORKER_ID = 'gw-agent'\n"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(AssertionError, 'expected one alias table'):
                    _stray_lines(source, _FIXTURE_CONSTANT, required=False)


if __name__ == '__main__':
    unittest.main()
