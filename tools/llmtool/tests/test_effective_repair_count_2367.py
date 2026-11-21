"""#2367 P0-B: a quota-stopped run is an effective repair unless its progress proves otherwise.

Rule: a quota-stopped run is excluded from the effective-repair count only when
its recorded progress has the route's complete shape (state, head_sha,
changed_files, unfinished_checks, session_id), all consistent with each other
and with the D6, and shows no file change. Anything missing, partial,
contradictory or unreadable counts the run.

Every test goes through the delegate repair guard. The policy is patched to
repair_max=1 so the guard's limit is two runs: one real run plus the run under
test either passes (the run is excluded) or refuses
DELEGATE_REPAIR_LIMIT_REACHED (the run counts). No test holds a secret-shaped
value; every ID and SHA is a repeated hex digit.
"""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_submission_cli
from llmtool.errors import LLMToolError

from test_task_delegate_repair_cap import (
    _EXECUTOR_TASK, _HEAD, _POLICY, _Store, _args, _diagnosis, _entry,
    _no_high_debug, _patch_trust_and_binding)

_LIMIT = '^DELEGATE_REPAIR_LIMIT_REACHED$'
_CREATED_AT = '2026-11-04T00:00:00Z'
_SESSION = 'conversation_12345678'
_OTHER_HEAD = 'b' * 40


def _progress(**changes):
    progress = {'state': 'QUOTA_EXHAUSTED', 'head_sha': _HEAD, 'changed_files': [],
                'unfinished_checks': ['UNKNOWN'], 'session_id': _SESSION}
    progress.update(changes)
    return progress


def _execution(**changes):
    execution = {'status': 'BLOCKED', 'native_session_id': None,
                 'process_exit_code': 1, 'stdout_sha256': None, 'elapsed_ms': 1,
                 'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
                 'source_bytes': None, 'source_file_count': None,
                 'source_manifest_sha256': None}
    execution.update(changes)
    return execution


def _quota_record(progress='complete', *, executor=_EXECUTOR_TASK, **developer_changes):
    """A stored quota-stopped Developer record; progress='complete' is the route's shape."""
    developer = {
        'provider_profile': 'agy', 'state': 'BLOCKED', 'error_code': 'AGY_QUOTA_EXHAUSTED',
        'artifact': None, 'artifact_sha256': None, 'usage': None,
        'execution': _execution(), 'source_sha': _HEAD, 'write_paths': ['sample.py'],
        'created_at': _CREATED_AT,
        'progress': _progress() if progress == 'complete' else progress,
    }
    developer.update(developer_changes)
    return {'completion': {'executor_task_id': executor}, 'developer': developer}


def _without(mapping, key):
    reduced = dict(mapping)
    del reduced[key]
    return reduced


def _store_with(*records, real_runs=1):
    """One real (responded) run plus the given records, none of them the current Task."""
    store = _Store(count=real_runs)
    for number, record in enumerate(records):
        store.records[f'{0x100 + number:032x}'] = record
    return store


def _guard(store, *, repair_max=1, args=None, probe=None):
    """Run the delegate repair guard; probe replaces the live D6 probe when given."""
    policy = dict(_POLICY, repair_max=repair_max)
    with patch.object(task_submission_cli.repair_policy, 'load_policy',
                      return_value=policy):
        if probe is None:
            return task_submission_cli._check_delegate_repair_cap(
                args or _no_high_debug(), store)
        with patch.object(task_submission_cli, '_d6_unchanged_probe',
                          return_value=probe):
            return task_submission_cli._check_delegate_repair_cap(
                args or _no_high_debug(), store)


def _always_clean(_source_sha, write_paths=None):
    return True


class EffectiveRepairCountTests(unittest.TestCase):
    def setUp(self):
        _patch_trust_and_binding(self)

    def assert_excluded(self, record):
        store = _store_with(record)
        # The run is not an effective repair, with or without a clean live D6.
        self.assertIsNone(_guard(store))
        self.assertIsNone(_guard(store, probe=_always_clean))
        self.assertEqual(store.writes, [])

    def assert_counted(self, record):
        store = _store_with(record)
        # A clean live D6 never excuses a run whose own record is in doubt.
        for probe in (None, _always_clean):
            with self.assertRaisesRegex(LLMToolError, _LIMIT):
                _guard(store, probe=probe)
        self.assertEqual(store.writes, [])

    def test_a_complete_consistent_progress_with_no_changed_files_is_excluded(self):
        self.assert_excluded(_quota_record())
        self.assert_excluded(_quota_record(
            provider_profile='claude', error_code='CLAUDE_QUOTA_EXHAUSTED'))
        self.assert_excluded(_quota_record(progress=_progress(unfinished_checks=[])))
        # A recorded native session ID that equals the progress session ID agrees.
        self.assert_excluded(_quota_record(
            execution=_execution(native_session_id=_SESSION)))

    def test_a_exclusion_does_not_need_the_live_d6(self):
        # A later run moved the D6: the quota run is still proved by its record.
        def moved(_source_sha, write_paths=None):
            return False
        self.assertIsNone(_guard(_store_with(_quota_record()), probe=moved))

    def test_b_only_head_sha_is_counted(self):
        self.assert_counted(_quota_record(progress={'head_sha': _HEAD}))

    def test_c_missing_changed_files_is_counted(self):
        self.assert_counted(_quota_record(progress=_without(_progress(), 'changed_files')))

    def test_c_every_other_missing_field_is_counted(self):
        for key in ('state', 'head_sha', 'unfinished_checks', 'session_id'):
            with self.subTest(missing=key):
                self.assert_counted(_quota_record(progress=_without(_progress(), key)))

    def test_c_unknown_extra_field_is_counted(self):
        self.assert_counted(_quota_record(progress=_progress(extra=1)))

    def test_d_non_empty_changed_files_is_counted(self):
        self.assert_counted(_quota_record(progress=_progress(changed_files=['sample.py'])))
        self.assert_counted(_quota_record(progress=_progress(changed_files='')))
        self.assert_counted(_quota_record(progress=_progress(changed_files=None)))
        self.assert_counted(_quota_record(changed_files=['sample.py']))

    def test_e_contradictory_state_is_counted(self):
        for state in ('DONE', 'DEVELOPER_RESPONDED', 'SUCCEEDED', '', None):
            with self.subTest(progress_state=state):
                self.assert_counted(_quota_record(progress=_progress(state=state)))
        # The Developer record contradicts a quota stop.
        self.assert_counted(_quota_record(state='DEVELOPER_RESPONDED'))
        self.assert_counted(_quota_record(error_code='AGY_RESULT_UNKNOWN'))
        self.assert_counted(_quota_record(error_code='AGY_WRITE_SCOPE_VIOLATION'))
        self.assert_counted(_quota_record(error_code=None))
        # One route's quota code on the other route's profile.
        self.assert_counted(_quota_record(error_code='CLAUDE_QUOTA_EXHAUSTED'))
        self.assert_counted(_quota_record(provider_profile='opencode'))
        self.assert_counted(_quota_record(provider_profile=None))
        # A recorded artifact is a result, not a quota stop.
        self.assert_counted(_quota_record(artifact='diff'))

    def test_f_head_sha_that_differs_from_the_d6_head_is_counted(self):
        for head in (_OTHER_HEAD, 'UNKNOWN', 'a' * 39, None, 7):
            with self.subTest(head_sha=head):
                self.assert_counted(_quota_record(progress=_progress(head_sha=head)))
        # The run's own D6 head is missing or unusable.
        self.assert_counted(_quota_record(source_sha=_OTHER_HEAD))
        self.assert_counted(_quota_record(source_sha=None))
        self.assert_counted(_without(_quota_record(), 'developer') | {
            'developer': _without(_quota_record()['developer'], 'source_sha')})

    def test_g_session_id_missing_or_different_is_counted(self):
        for session in (None, '', 'short', 'has space ok', 5, 'x' * 129):
            with self.subTest(session_id=session):
                self.assert_counted(_quota_record(progress=_progress(session_id=session)))
        self.assert_counted(_quota_record(
            execution=_execution(native_session_id='conversation_87654321')))

    def test_h_unreadable_or_non_dict_progress_is_counted(self):
        for progress in ('QUOTA_EXHAUSTED', None, [], [_progress()], 5, True, ()):
            with self.subTest(progress=progress):
                self.assert_counted(_quota_record(progress=progress))

    def test_h_unfinished_checks_that_are_not_strings_are_counted(self):
        for checks in (None, 'UNKNOWN', [1], [None], ['UNKNOWN', 3]):
            with self.subTest(unfinished_checks=checks):
                self.assert_counted(_quota_record(
                    progress=_progress(unfinished_checks=checks)))

    def test_h_unconfirmed_cleanup_is_counted(self):
        self.assert_counted(_quota_record(execution=_execution(cleanup_status='UNKNOWN')))
        self.assert_counted(_quota_record(execution=None))
        self.assert_counted(_without(_quota_record(), 'developer') | {
            'developer': _without(_quota_record()['developer'], 'execution')})

    def test_k_quota_stop_without_a_progress_field_is_counted_despite_a_clean_d6(self):
        # Case (b): the record names a quota stop but carries no progress at all.
        # The generic clean-D6 probe must not excuse it.
        for profile, code in (('agy', 'AGY_QUOTA_EXHAUSTED'),
                              ('claude', 'CLAUDE_QUOTA_EXHAUSTED')):
            with self.subTest(profile=profile):
                record = _quota_record(provider_profile=profile, error_code=code)
                record['developer'] = _without(record['developer'], 'progress')
                self.assertNotIn('progress', record['developer'])
                self.assert_counted(record)

    def test_l_blocked_record_with_done_execution_evidence_is_counted(self):
        # Case (c): a BLOCKED quota record whose execution says the run finished.
        for status in ('DONE', 'SUCCEEDED', 'RUNNING', 'TIMEOUT', '', None, 7):
            with self.subTest(execution_status=status):
                self.assert_counted(_quota_record(
                    execution=_execution(status=status)))
        # A missing execution status is not proof either.
        execution = _without(_execution(), 'status')
        self.assert_counted(_quota_record(execution=execution))

    def test_m_partial_progress_is_counted_by_the_complete_shape_rule(self):
        # Case (d), restated against the whole shape: every strict subset counts.
        complete = _progress()
        for key in complete:
            with self.subTest(missing=key):
                self.assert_counted(_quota_record(progress=_without(complete, key)))
        self.assert_counted(_quota_record(progress={}))

    def test_i_six_pr_2383_shaped_rounds_compute_two_effective_repairs(self):
        # Six rounds: the first run, two repairs, two quota stops with complete
        # no-change progress and one refused before launch. Only the first run
        # and the two repairs count: 3 runs, so the next run is repair #3.
        prelaunch = {
            'completion': {'executor_task_id': _EXECUTOR_TASK},
            'developer': {'provider_profile': 'agy', 'state': 'BLOCKED',
                          'error_code': 'AGY_DISPATCH_DENIED', 'artifact': None,
                          'artifact_sha256': None, 'execution': None, 'usage': None,
                          'created_at': _CREATED_AT}}
        store = _store_with(_quota_record(), prelaunch, _quota_record(), real_runs=3)
        self.assertEqual(len(store.records), 6)
        args = _args()
        # The root cause taken before repair #2 covers repair #3.
        store.records['e' * 32] = _diagnosis([_entry()])
        # Exactly 3 counted runs: repair #3 is allowed at repair_max=3 ...
        self.assertIsNone(_guard(store, repair_max=3, args=args))
        # ... and refused at repair_max=2, so no more and no fewer counted.
        with self.assertRaisesRegex(LLMToolError, _LIMIT):
            _guard(store, repair_max=2, args=args)
        self.assertEqual(store.writes, [])

    def test_i_the_same_rounds_with_doubtful_quota_stops_count_them(self):
        doubtful = _quota_record(progress={'head_sha': _HEAD})
        store = _store_with(doubtful, _quota_record(), doubtful, real_runs=1)
        # 1 real run + 2 doubtful ones: 3 counted, so repair_max=2 is the limit.
        with self.assertRaisesRegex(LLMToolError, _LIMIT):
            _guard(store, repair_max=2)

    def test_j_five_real_repairs_still_refuse(self):
        # 1 first run + 5 repairs: the limit under the repository policy.
        self.assertEqual(_POLICY['repair_max'], 5)
        for extra in ((), (_quota_record(),)):
            with self.subTest(excluded_quota_stops=len(extra)):
                store = _store_with(*extra, real_runs=6)
                with patch.object(task_submission_cli.repair_policy, 'load_policy',
                                  return_value=_POLICY):
                    with self.assertRaisesRegex(LLMToolError, _LIMIT):
                        task_submission_cli._check_delegate_repair_cap(
                            _args(), store)
                self.assertEqual(store.writes, [])

    def test_j_four_real_repairs_with_quota_stops_are_not_refused(self):
        # 1 first run + 4 repairs, and quota stops that prove no change: repair #5 passes.
        store = _store_with(_quota_record(), _quota_record(), real_runs=5)
        store.records['e' * 32] = _diagnosis([_entry()])
        with patch.object(task_submission_cli.repair_policy, 'load_policy',
                          return_value=_POLICY):
            self.assertIsNone(task_submission_cli._check_delegate_repair_cap(
                _args(), store))
        self.assertEqual(store.writes, [])

    def test_the_guard_never_changes_a_stored_record(self):
        store = _store_with(_quota_record(), _quota_record(progress={'head_sha': _HEAD}))
        before = copy.deepcopy(store.records)
        with self.assertRaisesRegex(LLMToolError, _LIMIT):
            _guard(store)
        self.assertEqual(store.records, before)


class ChainEffectiveRepairCountTests(unittest.TestCase):
    """The chain-wide guard reads the same record evidence, whichever D6 the run was on."""

    OTHER_D6 = 'executor-other-d6'

    def setUp(self):
        _patch_trust_and_binding(self)

    @staticmethod
    def _chain_with_probe(record, probe):
        real = {'completion': {'executor_task_id': _EXECUTOR_TASK},
                'developer': {'state': 'DEVELOPER_RESPONDED', 'created_at': _CREATED_AT}}
        members = {'1' * 32: real, '2' * 32: record}
        args = SimpleNamespace(task_id='f' * 32, executor_task=_EXECUTOR_TASK)
        policy = dict(_POLICY, repair_max=1)
        with patch.object(task_submission_cli, '_d6_unchanged_probe',
                          return_value=probe):
            task_submission_cli._check_chain_repair_count(
                args, _Store(count=0), None, policy, {'members': members})

    @classmethod
    def _chain_with_clean_probe(cls, record):
        cls._chain_with_probe(record, _always_clean)

    def _chain(self, record):
        self._chain_with_probe(record, lambda *_a, **_k: False)

    def test_a_complete_quota_stop_on_another_d6_is_excluded(self):
        self._chain(_quota_record(executor=self.OTHER_D6))

    def test_a_quota_stop_without_progress_on_another_d6_is_counted(self):
        record = _quota_record(executor=self.OTHER_D6)
        record['developer'] = _without(record['developer'], 'progress')
        with self.assertRaisesRegex(LLMToolError, _LIMIT):
            self._chain_with_clean_probe(record)

    def test_a_blocked_quota_stop_with_done_execution_on_another_d6_is_counted(self):
        record = _quota_record(executor=self.OTHER_D6,
                               execution=_execution(status='DONE'))
        with self.assertRaisesRegex(LLMToolError, _LIMIT):
            self._chain(record)

    def test_a_doubtful_quota_stop_on_another_d6_is_counted(self):
        for progress in ({'head_sha': _HEAD}, _progress(changed_files=['sample.py']),
                         _progress(head_sha=_OTHER_HEAD), _progress(state='DONE'),
                         _without(_progress(), 'session_id'), 'unreadable'):
            with self.subTest(progress=progress):
                with self.assertRaisesRegex(LLMToolError, _LIMIT):
                    self._chain(_quota_record(progress, executor=self.OTHER_D6))


if __name__ == '__main__':
    unittest.main()
