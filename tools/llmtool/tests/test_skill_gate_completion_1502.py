"""#1502 WP-F minimal gate: missing card-delivery evidence blocks completion.

Test data rule (Owner #763 A): every value is a fixed, non-secret test label.
The ledger home is a private temporary directory injected through
skill_ledger._path; the authenticated main read and the role-card build are
patched, so no Git, network or provider is used. The ledger rows are written
by the real writers (pin_dispatch, record_card_delivery, record_not_delivered,
revoke), shaped exactly like the rows the routes write today. No production
check is disabled. Stdlib unittest only.
"""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import skill_gate, skill_ledger, skill_manifest, task_completion
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError

PIN = '1' * 40
PREFIX = 'fixed role-card prefix used by completion gate tests\n'
PREFIX_DIGEST = hashlib.sha256(PREFIX.encode('utf-8')).hexdigest()
CARDS = (
    {'path': '.claude/skills/role-cards/common-core.md', 'sha256': 'a' * 64},
    {'path': '.claude/skills/role-cards/developer.md', 'sha256': 'b' * 64},
)


def task(profile='claude', created='2026-12-01T00:00:00Z', task_id='task-a', run_id='run-a'):
    return {'task_id': task_id, 'run_id': run_id,
            'developer': {'provider_profile': profile, 'created_at': created}}


class _LedgerCase(unittest.TestCase):

    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        home = Path(root.name) / 'ledger-home'
        home.mkdir(mode=0o700)
        os.chmod(home, 0o700)
        self.path = home / 'skill-ledger.jsonl'
        for target, name, value in (
                (skill_ledger, '_path', lambda: self.path),
                (task_completion, 'read_authenticated_main_sha', lambda: PIN),
                (skill_manifest, 'role_card_routes',
                 lambda: frozenset(('claude-developer', 'agy', 'opencode-developer'))),
                (skill_manifest, 'build_role_cards', self._build),
                (skill_manifest, 'role_card_prefix', lambda built: PREFIX)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _build(self, repo_root, base_commit, route, *, main_ref):
        return {'route': route, 'commit': base_commit,
                'cards': [dict(card) for card in CARDS],
                'cards_sha256': 'c' * 64, 'prefix_size': len(PREFIX)}

    def deliver(self, route='claude-developer', provider='claude',
                task_id='task-a', run_id='run-a'):
        skill_ledger.pin_dispatch(task_id, run_id, route)
        return skill_ledger.record_card_delivery({
            'task_id': task_id, 'run_id': run_id, 'session_id': 'session-a',
            'agent_id': 'agent-a', 'role': 'developer', 'provider': provider,
            'model': 'model-a', 'repo': 'repo-a', 'head': 'head-a',
            'manifest_sha256': PREFIX_DIGEST, 'route': route,
        }, repo_root='repo-root-read-only-by-fake-builder')

    def refused(self, record, code):
        with self.assertRaises(LLMToolError) as caught:
            skill_gate.check_completion(record)
        self.assertTrue(str(caught.exception).startswith(code), str(caught.exception))
        self.assertIn('next: dispatch a new Developer run', str(caught.exception))

class CompletionGateTest(_LedgerCase):

    def test_unpinned_run_is_not_gated_and_reads_no_clock(self):
        # Older records, claude-bind and any run that never reached the pin.
        for created in ('2026-10-01T00:00:00Z', '2027-01-01T00:00:00Z', None):
            with self.subTest(created=created):
                self.assertEqual(skill_gate.check_completion(task(created=created)),
                                 'NOT_PINNED')

    def test_card_routes_pass_with_their_own_receipt(self):
        for profile, route in skill_gate.CARD_ROUTES.items():
            with self.subTest(profile=profile):
                self.deliver(route=route, provider=profile, task_id='task-' + profile)
                self.assertEqual(skill_gate.check_completion(
                    task(profile, task_id='task-' + profile)), 'VERIFIED')

    def test_pinned_run_without_receipt_is_refused(self):
        for profile, route in skill_gate.CARD_ROUTES.items():
            with self.subTest(profile=profile):
                skill_ledger.pin_dispatch('task-' + profile, 'run-a', route)
                self.refused(task(profile, task_id='task-' + profile),
                             skill_ledger.DELIVERY_RECEIPT_MISSING)

    def test_other_runs_receipt_does_not_count(self):
        self.deliver(run_id='run-other')
        skill_ledger.pin_dispatch('task-a', 'run-a', 'claude-developer')
        self.refused(task(), skill_ledger.DELIVERY_RECEIPT_MISSING)

    def test_other_routes_receipt_does_not_count(self):
        self.deliver(route='agy', provider='agy')
        skill_ledger.pin_dispatch('task-a', 'run-a', 'claude-developer')
        self.refused(task('claude'), skill_ledger.DELIVERY_RECEIPT_MISSING)

    def test_revoked_receipt_is_refused(self):
        receipt_id = self.deliver()
        skill_ledger.revoke(receipt_id, 'test revocation')
        self.refused(task(), skill_ledger.DELIVERY_REVOKED)

    def test_routes_without_cards_are_not_gated(self):
        for profile in ('gw-agent', 'codex', 'unknown-route'):
            with self.subTest(profile=profile):
                self.assertEqual(skill_gate.check_completion(task(profile)),
                                 'NOT_CARD_ROUTE')

    def test_broken_ledger_is_refused(self):
        self.deliver()
        lines = self.path.read_bytes().splitlines(keepends=True)
        lines[-1] = lines[-1].replace(b'agent-a', b'agent-x')
        self.path.write_bytes(b''.join(lines))
        self.refused(task(), skill_ledger.DELIVERY_LEDGER_UNREADABLE)

    def test_task_without_run_id_has_no_pin(self):
        record = task()
        record['run_id'] = None
        self.assertEqual(skill_gate.check_completion(record), 'NOT_PINNED')

    def test_no_developer_is_not_gated(self):
        self.assertEqual(skill_gate.check_completion({'task_id': 't', 'developer': None}),
                         'NO_DEVELOPER')

    def test_status_reads_append_nothing(self):
        self.deliver()
        before = self.path.read_bytes()
        skill_gate.check_completion(task())
        self.assertEqual(self.path.read_bytes(), before)


class _Store:
    def __init__(self, record):
        self.record = record
        self.writes = 0

    def get(self, task_id):
        return self.record

    def __getattr__(self, name):  # any write would fail the test
        def write(*args, **kwargs):
            self.writes += 1
            raise AssertionError('store write ' + name)
        return write


class CompletionEntryWiringTest(_LedgerCase):
    """Both protected entries call the gate before any binding check or write."""

    def direct_task(self):
        record = task()
        record.update(schema_version=2, state='PM_RESPONDED',
                      pm_route_binding={'route': 'fixed'},
                      task_binding={'contract_digest': 'd' * 64})
        record['developer'].update(
            state='DEVELOPER_RESPONDED', artifact='diff', execution={
                'status': 'SUCCEEDED', 'process_exit_code': 0})
        return record

    def test_direct_result_event_refuses_without_receipt(self):
        skill_ledger.pin_dispatch('task-a', 'run-a', 'claude-developer')
        store = _Store(self.direct_task())
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.run_task_direct_result_event('task-a', 'exec-a', store=store)
        self.assertTrue(str(caught.exception).startswith(skill_ledger.DELIVERY_RECEIPT_MISSING))
        self.assertEqual(store.writes, 0)

    def test_direct_result_event_passes_gate_with_receipt(self):
        self.deliver()
        store = _Store(self.direct_task())
        with self.assertRaises(Exception) as caught:
            task_submission_cli.run_task_direct_result_event('task-a', 'exec-a', store=store)
        self.assertFalse(str(caught.exception).startswith('SKILL_DELIVERY_'))

    def test_task_complete_refuses_without_receipt(self):
        skill_ledger.pin_dispatch('task-a', 'run-a', 'claude-developer')
        record = self.direct_task()
        record['developer'].pop('artifact')
        record['completion'] = {'executor_task_id': 'exec-a', 'pm_dispatch': 'NOT_STARTED',
                                'evidence': {}}
        store = _Store(record)
        args = mock.Mock(task_id='task-a', executor_task='exec-a', reviewer_task=None, pr=None)
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.run_task_complete(args, store=store)
        self.assertTrue(str(caught.exception).startswith(skill_ledger.DELIVERY_RECEIPT_MISSING))
        self.assertEqual(store.writes, 0)


if __name__ == '__main__':
    unittest.main()
