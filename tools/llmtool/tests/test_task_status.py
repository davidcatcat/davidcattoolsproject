import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import task_status
from llmtool.cli import main, parser_for_cli
from llmtool.task_index import TaskStatusIndex
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_abandon
from test_task_store import record as valid_record


class MemoryTaskStore(TaskStore):
    def __init__(self, root, records, unreadable=()):
        super().__init__(root)
        self.records = records
        self.unreadable = set(unreadable)

    def task_ids(self):
        return [record['task_id'] for record in self.records] + list(self.unreadable)

    def get(self, task_id):
        if task_id in self.unreadable:
            raise ValueError('bad record')
        return next(dict(record) for record in self.records if record['task_id'] == task_id)


class CountingTaskStore(TaskStore):
    def __init__(self, root):
        super().__init__(root)
        self.get_calls = 0

    def get(self, task_id):
        self.get_calls += 1
        return super().get(task_id)


def record(root, **extra):
    """Start status fixtures with a record accepted and persisted by TaskStore."""
    store = TaskStore(Path(root).resolve() / 'tasks')
    value = valid_record()
    value.update(extra)
    return store.create(value)


class TaskStatusTests(unittest.TestCase):
    def test_dispatched_review_claim_requires_same_head_accepted_evidence_for_pass(self):
        claim = {'state': 'DISPATCHED', 'head_sha': 'a' * 40, 'version': 3,
                 'reviewer_task': 'reviewer-v3'}

        def summary(*, claim_value=claim, events=(), completion=None):
            return task_status._summary({
                'task_id': '1' * 32,
                'review_dispatch_claim': claim_value,
                'completion_events': list(events),
                'completion': completion or {},
            })['review_claim_state']

        matching = {'source': 'REVIEWER', 'source_sha': 'a' * 40}
        accepted_review = {'head_sha': 'a' * 40,
                           'formal_review_state': 'ACCEPTED',
                           'reviewer_task_id': 'reviewer-v3'}
        accepted = {'evidence': {'review_pr_ci': accepted_review}}
        self.assertEqual(summary(events=(matching,), completion=accepted), 'PASS')
        with tempfile.TemporaryDirectory() as root:
            closed = record(root)
        closed.update({
            'completion': {'state': 'PM_RETURNED', **accepted},
            'review_dispatch_claim': claim,
            'completion_events': [matching],
        })
        self.assertEqual(task_status._summary(closed)['review_claim_state'], 'PASS')
        self.assertEqual(summary(events=(matching,)), 'DISPATCHED')
        self.assertEqual(summary(events=({**matching, 'source_sha': 'b' * 40},),
                               completion={'evidence': {'review_pr_ci': {
                                   'head_sha': 'b' * 40,
                                   'formal_review_state': 'ACCEPTED',
                                   'reviewer_task_id': 'reviewer-v3'}}}), 'DISPATCHED')
        self.assertEqual(summary(events=(matching,), completion={
            'evidence': {'review_pr_ci': {
                'head_sha': 'a' * 40, 'formal_review_state': 'ACCEPTED',
                'reviewer_task_id': 'reviewer-v2'}}}), 'DISPATCHED')
        self.assertEqual(summary(events=({**matching, 'version': 2,
                                          'outcome': 'REVIEW_PASS'},)), 'DISPATCHED')
        self.assertEqual(summary(events=({**matching, 'version': 3,
                                          'outcome': 'REVIEW_FAIL'},)), 'REVIEW_FAIL')
        self.assertEqual(summary(events=({**matching, 'version': 3,
                                          'outcome': 'REVIEW_FIX'},)), 'DISPATCHED')
        self.assertEqual(summary(events=({**matching, 'version': 3,
                                          'outcome': 'REVIEW_PASS'},),
                               completion=accepted), 'PASS')
        self.assertEqual(summary(claim_value=None), 'NONE')

    def test_newest_dispatched_claim_ignores_older_claim_event(self):
        older = {'state': 'COMPLETED', 'head_sha': 'a' * 40, 'version': 1,
                 'reviewer_task': 'reviewer-v1'}
        newest = {'state': 'DISPATCHED', 'head_sha': 'a' * 40, 'version': 2,
                  'reviewer_task': 'reviewer-v2'}
        summary = task_status._summary({
            'task_id': '1' * 32,
            'review_dispatch_claim': [older, newest],
            'completion_events': [{
                'source': 'REVIEWER', 'source_sha': 'a' * 40,
                'version': 1, 'outcome': 'REVIEW_PASS'}],
        })
        self.assertEqual(summary['review_claim_state'], 'DISPATCHED')

        # A v2 failure on the same HEAD must not inherit v1's accepted PASS.
        v2_failure = task_status._summary({
            'task_id': '1' * 32,
            'review_dispatch_claim': [older, newest],
            'completion_events': [{
                'source': 'REVIEWER', 'source_sha': 'a' * 40,
                'version': 2, 'outcome': 'REVIEW_FAIL'}],
            'completion': {'evidence': {'review_pr_ci': {
                'head_sha': 'a' * 40, 'formal_review_state': 'UNVERIFIED',
                'reviewer_task_id': 'reviewer-v2'}}},
        })
        self.assertEqual(v2_failure['review_claim_state'], 'REVIEW_FAIL')

        # Stored-shape v2 events omit version/outcome. Without accepted
        # evidence for v2, the summary does not guess a verdict.
        v2_stored_shape = task_status._summary({
            'task_id': '1' * 32,
            'review_dispatch_claim': [older, newest],
            'completion_events': [{
                'source': 'REVIEWER', 'source_sha': 'a' * 40}],
            'completion': {'evidence': {'review_pr_ci': {
                'head_sha': 'a' * 40, 'formal_review_state': 'UNVERIFIED',
                'reviewer_task_id': 'reviewer-v2'}}},
        })
        self.assertEqual(v2_stored_shape['review_claim_state'], 'DISPATCHED')

        # While v2 is still running, a v1 event alone is not evidence for v2.
        v2_running = task_status._summary({
            'task_id': '1' * 32,
            'review_dispatch_claim': [older, newest],
            'completion_events': [{
                'source': 'REVIEWER', 'source_sha': 'a' * 40}],
            'completion': {'evidence': {'review_pr_ci': {
                'head_sha': 'a' * 40, 'formal_review_state': 'ACCEPTED',
                'reviewer_task_id': 'reviewer-v1'}}},
        })
        self.assertEqual(v2_running['review_claim_state'], 'DISPATCHED')
        completed = task_status._summary({
            'task_id': '1' * 32,
            'review_dispatch_claim': {**newest, 'state': 'COMPLETED'},
            'completion_events': [{
                'source': 'REVIEWER', 'source_sha': 'a' * 40}],
        })
        self.assertEqual(completed['review_claim_state'], 'COMPLETED')

    def test_derived_states(self):
        with tempfile.TemporaryDirectory() as root:
            base = record(root)
        mappings = (
            ('task', {
                'PM_PENDING': 'RUNNING', 'PM_RESPONDED': 'RUNNING',
                'PM_UNAVAILABLE': 'UNAVAILABLE', 'INPUT_REJECTED': 'FAILED',
                'PM_RESPONSE_REJECTED': 'FAILED',
            }),
            ('developer', {
                'DEVELOPER_PENDING': 'RUNNING', 'DEVELOPER_DISPATCHING': 'RUNNING',
                'DEVELOPER_RESPONDED': 'RUNNING', 'DEVELOPER_UNAVAILABLE': 'UNAVAILABLE',
                'DEVELOPER_OUTCOME_UNKNOWN': 'UNKNOWN',
                'DEVELOPER_OUTPUT_REJECTED': 'FAILED',
                'BLOCKED': 'BLOCKED',
            }),
            ('callback', {
                'WAITING_EXECUTOR': 'RUNNING', 'VERIFYING_EXECUTOR': 'RUNNING',
                'WAITING_PM': 'RUNNING', 'PM_EVIDENCE_READY': 'RUNNING',
                'PM_DISPATCHING': 'RUNNING', 'PM_RETURNED': 'COMPLETED',
                'PM_RESPONSE_REJECTED': 'FAILED', 'PM_UNAVAILABLE': 'UNAVAILABLE',
                'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
            }),
            ('completion', {
                'WAITING_EXECUTOR': 'RUNNING', 'WAITING_REVIEW': 'RUNNING',
                'WAITING_CI': 'RUNNING', 'WAITING_PM': 'RUNNING',
                'PM_EVIDENCE_READY': 'RUNNING', 'PM_DISPATCHING': 'RUNNING',
                'PM_RETURNED': 'COMPLETED', 'PM_RESPONSE_REJECTED': 'FAILED',
                'PM_UNAVAILABLE': 'UNAVAILABLE', 'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
            }),
        )
        self.assertEqual(set(mappings[0][1]), set(task_status.STATES))
        for kind, states in mappings:
            for state, expected in states.items():
                if kind == 'task':
                    value = {**base, 'state': state}
                elif kind == 'developer':
                    value = {**base, 'developer': {'state': state}}
                elif kind == 'callback':
                    value = {**base, 'developer': {'direct_result_callback': {
                        'state': state}}}
                else:
                    value = {**base, 'completion': {'state': state}}
                with self.subTest(kind=kind, state=state):
                    self.assertEqual(task_status.derive_state(value), expected)
        blocked = {**base, 'developer': {
            'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'CODEX_BLOCKED'}}
        self.assertEqual(task_status.derive_state(blocked), 'BLOCKED')

    def test_unknown_shape_is_unknown(self):
        self.assertEqual(task_status.derive_state({'state': 'NEW_STATE'}), 'UNKNOWN')
        self.assertEqual(task_status.derive_state({
            'state': 'PM_PENDING', 'developer': {'state': 'FUTURE_DEVELOPER_STATE'}}),
            'UNKNOWN')
        self.assertEqual(task_status.derive_state({
            'state': 'PM_PENDING', 'developer': {'direct_result_callback': {
                'state': 'FUTURE_CALLBACK_STATE'}}}), 'UNKNOWN')
        self.assertEqual(task_status.derive_state({
            'state': 'PM_PENDING', 'completion': {'state': 'FUTURE_COMPLETION_STATE'}}),
            'UNKNOWN')

    def test_merged_github_event_completes_task(self):
        with tempfile.TemporaryDirectory() as root:
            base = record(root)
        base['completion_events'] = [{
            'source': 'GITHUB', 'marker_type': 'merge', 'marker_status': 'MERGED'}]
        self.assertEqual(task_status.derive_state(base), 'COMPLETED')

    def test_abandonment_overrides_merge_and_is_listed(self):
        with tempfile.TemporaryDirectory() as root:
            saved = record(root)
            store = TaskStore(Path(root).resolve() / 'tasks')
            store.abandon(saved['task_id'], pm_session_id=saved['pm_session_id'],
                          reason='PM closed the work')
            current = store.get(saved['task_id'])
            current['completion_events'] = [{
                'source': 'GITHUB', 'marker_type': 'merge', 'marker_status': 'MERGED'}]
            self.assertEqual(task_status.derive_state(current), 'ABANDONED')
            listed = task_status.task_status(store=store)
            self.assertEqual(listed['tasks'][0]['state'], 'ABANDONED')

    def test_responded_developer_uses_completion_state(self):
        with tempfile.TemporaryDirectory() as root:
            base = record(root)
        outcomes = {
            'PM_RETURNED': 'COMPLETED',
            'PM_RESPONSE_REJECTED': 'FAILED',
            'PM_UNAVAILABLE': 'UNAVAILABLE',
            'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
            'WAITING_EXECUTOR': 'RUNNING',
            'WAITING_REVIEW': 'RUNNING',
            'WAITING_CI': 'RUNNING',
            'WAITING_PM': 'RUNNING',
            'PM_EVIDENCE_READY': 'RUNNING',
            'PM_DISPATCHING': 'RUNNING',
        }
        for completion_state, expected in outcomes.items():
            value = {
                **base,
                'developer': {'state': 'DEVELOPER_RESPONDED'},
                'completion': {'state': completion_state},
            }
            with self.subTest(completion=completion_state):
                self.assertEqual(task_status.derive_state(value), expected)

    def test_terminal_developer_state_precedes_completion(self):
        with tempfile.TemporaryDirectory() as root:
            base = record(root)
        cases = (
            ({'state': 'DEVELOPER_UNAVAILABLE'}, 'UNAVAILABLE'),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED'}, 'FAILED'),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN'}, 'UNKNOWN'),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'CODEX_BLOCKED'}, 'BLOCKED'),
            ({'state': 'BLOCKED'}, 'BLOCKED'),
        )
        for developer, expected in cases:
            value = {
                **base,
                'developer': developer,
                'completion': {'state': 'PM_RETURNED'},
            }
            with self.subTest(developer=developer):
                self.assertEqual(task_status.derive_state(value), expected)

    def test_temp_store_limit_filter_unreadable_and_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            rows = [record(root, pm_session_id='pm-one' if i != 2 else 'pm-two')
                    for i in range(1, 4)]
            before = [hashlib.sha256(row['record_sha256'].encode()).hexdigest() for row in rows]
            store = MemoryTaskStore(root, rows, unreadable=('f' * 32,))
            result = task_status.task_status(store=store, limit=1)
            self.assertEqual(len(result['tasks']), 1)
            self.assertEqual(result['unreadable'], 1)
            filtered = task_status.task_status(store=store, pm_session='pm-two')
            self.assertEqual([row['pm_session_id'] for row in filtered['tasks']], ['pm-two'])
            self.assertEqual(before, [hashlib.sha256(row['record_sha256'].encode()).hexdigest() for row in rows])

    def test_native_pm_session_uuid_filter_matches_pm_id_and_rejects_invalid(self):
        native_session = '550e8400-e29b-41d4-a716-446655440000'
        with tempfile.TemporaryDirectory() as root:
            rows = [record(root, pm_session_id='pm-' + native_session),
                    record(root, pm_session_id='pm-other')]
            store = MemoryTaskStore(root, rows)

            native_result = task_status.task_status(store=store, pm_session=native_session)
            prefixed_result = task_status.task_status(
                store=store, pm_session='pm-' + native_session)

        self.assertEqual(native_result, prefixed_result)
        self.assertEqual([row['pm_session_id'] for row in native_result['tasks']],
                         ['pm-' + native_session])
        with self.assertRaisesRegex(ValueError, '^invalid PM session$'):
            task_status.task_status(store=store, pm_session='not-a-session')

    def test_limit_bound(self):
        with self.assertRaises(ValueError):
            task_status.task_status(limit=201, store=MemoryTaskStore('/tmp/unused', []))

    def test_cli_route_reaches_handler(self):
        args = parser_for_cli().parse_args(['task', 'status', '--pm-session', 'pm-one', '--limit', '3', '--json'])
        self.assertEqual(args.action, 'status')
        output = io.StringIO()
        with patch('llmtool.task_status.TaskStore') as store_class:
            store_class.return_value.task_ids.return_value = []
            with patch('sys.stdout', output):
                self.assertEqual(main(['task', 'status', '--json']), 0)
        self.assertIn('"tasks":[]', output.getvalue())

    def test_task_abandon_cli_handler_uses_temp_store(self):
        with tempfile.TemporaryDirectory() as root:
            saved = record(root)
            store = TaskStore(Path(root).resolve() / 'tasks')
            args = parser_for_cli().parse_args([
                'task', 'abandon', saved['task_id'], '--pm-session',
                saved['pm_session_id'], '--reason', 'No longer needed'])
            output = io.StringIO()
            self.assertEqual(run_task_abandon(args, store=store, stdout=output), 0)
            self.assertIn('"state":"ABANDONED"', output.getvalue())
            self.assertEqual(task_status.task_status(store=store)['tasks'][0]['state'],
                             'ABANDONED')

    def test_task_abandon_runs_gw_agent_session_sweep(self):
        with patch("llmtool.gw_agent_sessions.sweep_quietly") as sweep:
            with tempfile.TemporaryDirectory() as root:
                saved = record(root)
                store = TaskStore(Path(root).resolve() / 'tasks')
                args = parser_for_cli().parse_args([
                    'task', 'abandon', saved['task_id'], '--pm-session',
                    saved['pm_session_id'], '--reason', 'No longer needed'])
                output = io.StringIO()
                self.assertEqual(run_task_abandon(args, store=store, stdout=output), 0)
            sweep.assert_called_once_with(store)
            self.assertIn('"state":"ABANDONED"', output.getvalue())

    def test_index_matches_full_scan_and_skips_unprinted_reads(self):
        with tempfile.TemporaryDirectory() as root:
            rows = [record(root, created_at=f'2026-10-2{i}T00:00:00Z',
                           pm_session_id='pm-one' if i < 3 else 'pm-two')
                    for i in range(1, 5)]
            store = CountingTaskStore(Path(root).resolve() / 'tasks')
            unreadable_id = 'f' * 32
            (store.root / f'{unreadable_id}.json').write_text('invalid')

            full = task_status.task_status(store=store, limit=2)
            self.assertEqual(full['unreadable'], 1)
            store.get_calls = 0
            indexed = task_status.task_status(store=store, limit=2)
            self.assertEqual(indexed, full)
            self.assertEqual(store.get_calls, 2)

            filtered = task_status.task_status(store=store, pm_session='pm-two')
            index_path = Path(root) / 'task-status-index.json'
            index_path.unlink()
            without_index = task_status.task_status(store=store, pm_session='pm-two')
            self.assertEqual(without_index, filtered)
            self.assertEqual(len(rows), 4)

    def test_changed_unprinted_record_is_revalidated(self):
        with tempfile.TemporaryDirectory() as root:
            rows = [record(root, created_at=f'2026-10-2{i}T00:00:00Z')
                    for i in range(1, 4)]
            store = CountingTaskStore(Path(root).resolve() / 'tasks')
            task_status.task_status(store=store, limit=1)
            old_record = min(rows, key=lambda item: item['created_at'])
            os.utime(store.root / f"{old_record['task_id']}.json", None)
            store.get_calls = 0
            task_status.task_status(store=store, limit=1)
            self.assertEqual(store.get_calls, 2)

    def test_invalid_index_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            record(root, created_at='2026-10-29T00:00:00Z')
            store = CountingTaskStore(Path(root).resolve() / 'tasks')
            expected = task_status.task_status(store=store)
            index_path = Path(root) / 'task-status-index.json'

            index_path.write_text('{')
            self.assertEqual(task_status.task_status(store=store), expected)
            index_path.write_text('{}')
            index_path.chmod(0o644)
            self.assertEqual(task_status.task_status(store=store), expected)
            index_path.unlink()
            target = Path(root) / 'index-target'
            target.write_text('{}')
            index_path.symlink_to(target)
            self.assertEqual(task_status.task_status(store=store), expected)

    def test_validator_fingerprint_change_invalidates_index(self):
        with tempfile.TemporaryDirectory() as root:
            record(root, created_at='2026-10-29T00:00:00Z')
            store = CountingTaskStore(Path(root).resolve() / 'tasks')
            task_status.task_status(store=store)
            store.get_calls = 0
            with patch('llmtool.task_index.validator_fingerprint', return_value='f' * 64):
                task_status.task_status(store=store)
            self.assertGreaterEqual(store.get_calls, 1)

    def test_index_drops_entry_when_record_changes_during_read(self):
        with tempfile.TemporaryDirectory() as root:
            created = record(root, created_at='2026-10-29T00:00:00Z')

            class ChangingTaskStore(CountingTaskStore):
                def get(self, task_id):
                    value = super().get(task_id)
                    path = self.root / f'{task_id}.json'
                    info = path.stat()
                    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
                    return value

            store = ChangingTaskStore(Path(root).resolve() / 'tasks')
            index = TaskStatusIndex(store)
            rows, unreadable = index.scan([created['task_id']])

            self.assertEqual(rows, [(created['task_id'], created['created_at'],
                                     created['pm_session_id'])])
            self.assertEqual(unreadable, 0)
            self.assertNotIn(created['task_id'], index.entries)

    def test_persisted_developer_blocked_precedes_stale_callback_and_preserves_precedence(self):
        def callback_for(base, *, state='WAITING_EXECUTOR'):
            return {
                'state': state,
                'executor_task_id': 'executor-d6',
                'evidence': {
                    'task_id': base['task_id'],
                    'run_id': base['run_id'],
                    'contract_digest': base['task_binding']['contract_digest'],
                    'executor_task_id': 'executor-d6',
                    'source_sha': 'd' * 40,
                    'execution_mode': 'DIRECT_D6',
                    'verification_state': 'NOT_STARTED',
                },
                'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED',
                'pm_response': None,
                'pm_recommendation': None,
                'pm_usage': None,
                'pm_error_code': None,
            }

        def developer_for(base, *, profile, state='BLOCKED', error_code='AGY_EMPTY_RESULT',
                          callback_state='WAITING_EXECUTOR', reported=None, artifact=None):
            provider_type = 'agy-cli' if profile == 'agy' else 'claude-cli'
            dev = {
                'assignment_id': '1' * 32,
                'selection_source': 'PM',
                'state': state,
                'provider_profile': profile,
                'provider_type': provider_type,
                'model': 'gemini-test' if profile == 'agy' else 'claude-test',
                'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
                'session_id': 'dev-' + '1' * 32,
                'source_sha': 'd' * 40,
                'source_sha_binding': 'D6_REGISTRY_OBSERVED',
                'read_paths': ['src.py'],
                'write_paths': ['src.py'],
                'acceptance': ['the result is recorded'],
                'context_sha256': 'a' * 64,
                'egress_basis': 'Approved non-sensitive Task context.',
                'input_bytes': 100,
                'max_tokens': None,
                'artifact_format': 'unified_diff',
                'artifact': artifact,
                'artifact_sha256': (None if artifact is None else
                                    hashlib.sha256(artifact.encode('utf-8')).hexdigest()),
                'usage': None,
                'error_code': error_code,
                'execution': None,
                'created_at': '2026-11-27T00:00:00Z',
                'owner_assignment_ref': None,
                'direct_result_callback': callback_for(base, state=callback_state),
            }
            if profile == 'claude':
                dev['executor_task_id'] = 'executor-d6'
            if reported is not None:
                dev['reported'] = reported
            return dev

        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(Path(root).resolve() / 'tasks')
            agy_base = record(root)
            agy_saved = store.update(
                agy_base['task_id'],
                developer=developer_for(agy_base, profile='agy', state='BLOCKED',
                                        error_code='AGY_EMPTY_RESULT'))
            self.assertEqual(task_status.derive_state(agy_saved), 'BLOCKED')
            listed = task_status.task_status(store=store)
            self.assertEqual(
                next(item['state'] for item in listed['tasks']
                     if item['task_id'] == agy_saved['task_id']),
                'BLOCKED')

            claude_base = record(root)
            claude_saved = store.update(
                claude_base['task_id'],
                developer=developer_for(claude_base, profile='claude', state='BLOCKED',
                                        error_code='CLAUDE_NO_CHANGES'))
            self.assertEqual(task_status.derive_state(claude_saved), 'BLOCKED')

            # Genuine terminal callback states retain precedence over Developer BLOCKED.
            for cb_state, expected in (
                    ('PM_RETURNED', 'COMPLETED'),
                    ('PM_RESPONSE_REJECTED', 'FAILED'),
                    ('PM_UNAVAILABLE', 'UNAVAILABLE'),
                    ('PM_OUTCOME_UNKNOWN', 'UNKNOWN')):
                with self.subTest(callback_state=cb_state):
                    record_value = {
                        **agy_saved,
                        'developer': developer_for(
                            agy_saved, profile='agy', state='BLOCKED',
                            error_code='AGY_EMPTY_RESULT', callback_state=cb_state),
                    }
                    self.assertEqual(task_status.derive_state(record_value), expected)

            # Merged and abandoned records retain precedence over Developer BLOCKED.
            self.assertEqual(task_status.derive_state({
                **agy_saved,
                'merge_attempt': {'pr': 1, 'head_sha': 'd' * 40, 'state': 'MERGED',
                                  'claimed_at': '2026-11-27T00:00:00Z'},
            }), 'COMPLETED')
            self.assertEqual(task_status.derive_state({
                **agy_saved,
                'completion_events': [{'source': 'GITHUB', 'marker_type': 'merge',
                                       'marker_status': 'MERGED'}],
            }), 'COMPLETED')
            store.abandon(agy_saved['task_id'], pm_session_id=agy_saved['pm_session_id'],
                          reason='PM closed blocked work')
            self.assertEqual(task_status.derive_state(store.get(agy_saved['task_id'])), 'ABANDONED')

            # Negative controls: generic unknown and prose-only BLOCKED summary.
            unknown_dev = developer_for(agy_base, profile='agy',
                                        state='DEVELOPER_OUTCOME_UNKNOWN',
                                        error_code='AGY_RESULT_UNKNOWN')
            unknown_dev.pop('direct_result_callback')
            self.assertEqual(task_status.derive_state({**agy_base, 'developer': unknown_dev}),
                             'UNKNOWN')
            prose_responded = developer_for(
                agy_base, profile='agy', state='DEVELOPER_RESPONDED',
                error_code=None, artifact='diff --git a/src.py b/src.py\n',
                reported={'summary': 'BLOCKED: prose summary only'})
            self.assertEqual(task_status.derive_state({**agy_base, 'developer': prose_responded}),
                             'RUNNING')


if __name__ == '__main__':
    unittest.main()
