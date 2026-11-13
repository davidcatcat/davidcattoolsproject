"""#1502 WP-E (E9): the gw Agent worker id is gw-agent.

Records written with the retired id company-gw-agent still read, as gw-agent,
through the one alias helper (worker_dispatch.stored_worker_id). A new
dispatch-check, delegate, review or worker evaluation naming the retired id is
refused with WORKER_ID_RETIRED_ALIAS. The company-gw direct route stays retired.
"""

import argparse
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import dispatch_check, review_dispatch, task_completion, task_submission_cli
from llmtool import worker_dispatch
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

    def test_task_record_with_the_retired_profile_keeps_its_review_family(self):
        for model, family in (('claude-opus-5-5', 'claude'), ('gpt-6-luna', 'codex'),
                              ('served-model', 'other'), (None, 'other')):
            with self.subTest(model=model):
                legacy = {'provider_profile': RETIRED, 'model': model}
                current = {'provider_profile': 'gw-agent', 'model': model}
                self.assertEqual(task_completion._reviewer_author_family(legacy), family)
                self.assertEqual(task_completion._reviewer_author_family(legacy),
                                 task_completion._reviewer_author_family(current))


if __name__ == '__main__':
    unittest.main()
