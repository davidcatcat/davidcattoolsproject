"""#1148: the dispatch gate's committed-result carry, on a real TaskStore."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from llmtool import task_submission_cli as cli, worker_dispatch as wd
from llmtool.task_store import TaskStore
import review_trust_fixture

BASE, HEAD, D6, TASK, DIFF = '1' * 40, '2' * 40, 'c' * 32, 'a' * 32, 'diff --git a/x b/x\n'
REF = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
setUpModule, tearDownModule = review_trust_fixture.start_module, review_trust_fixture.stop_module


class CommittedCarryGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        workers = dict.fromkeys(wd.WORKERS, {'dispatch': 'ALLOWED', 'decided_by': 'Owner',
            'decided_at': '2026-11-12', 'reason': 'test', 'evidence': REF})
        workers['gw-agent'] = {'dispatch': 'PAUSED', 'decided_by': 'PM', 'reason': 'test',
            'decided_at': '2026-11-12', 'evidence': wd._GW_AGENT_PAUSED_REF}
        (root / 'p.json').write_text(json.dumps({'schema_version': 1, 'workers': workers}))
        policy = patch.object(wd, '_POLICY_PATH', root / 'p.json')
        policy.start()
        self.addCleanup(policy.stop)
        self.store = TaskStore(root / 'tasks')
        task = cli._record(TASK, 'b' * 32, 'pm-' + 'e' * 32, 'pm', 'Task.', state='PM_RESPONDED',
            provider_type='openai-compatible', model='pm', backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(b'Task.').hexdigest())
        task.update(response='PM.', error_code=None, pm_route_binding={
            'type': 'openai-compatible', 'model': 'pm', 'base_url': None}, pm_assignment={
            'schema_version': 1, 'route': 'claude', 'risk': 'LOW', 'goal': 'G.', 'acceptance': ['a']})
        self.store.create(task)
        self.task = self.store.get(TASK)
        self.d6 = NS(task_id=D6, trusted_agent_id='claude-developer', root='/d6', session_ref='r',
                     native_session_id=None, common_dir='/repo/.git', branch='b')
        pending = dict(dict.fromkeys(('provider_type', 'model', 'max_tokens', 'artifact', 'usage',
            'artifact_sha256', 'error_code', 'execution')), assignment_id='f' * 32,
            selection_source='PM', state='DEVELOPER_PENDING', provider_profile='claude',
            backend_session_semantics='CLI_REPORTED_SESSION_ID', session_id='dev-' + 'e' * 32,
            source_sha=HEAD, source_sha_binding='D6_REGISTRY_OBSERVED', read_paths=['x'],
            write_paths=['x'], acceptance=['a'], context_sha256='1' * 64, egress_basis='Test.',
            input_bytes=10, artifact_format='unified_diff', created_at=cli._timestamp(),
            executor_task_id=D6, owner_assignment_ref=REF + '#issuecomment-1234567890',
            direct_result_callback=cli._direct_result_callback(self.task, self.d6, HEAD,
                                                               test_policy='LOCAL'))
        evidence = pending['direct_result_callback']['evidence']
        self.completion = dict(pending['direct_result_callback'], reviewer_task_id=None,
            evidence={key: value for key, value in evidence.items()
                      if key not in ('verification_state', 'test_policy', 'executor_task_id')})
        self.write(pending, self.completion, None)
        self.dispatching = dict(pending, state='DEVELOPER_DISPATCHING', provider_type='claude-cli',
                                model='claude-test')
        self.write(self.dispatching, None, pending)

    def write(self, developer, completion, expected):
        changes = {'developer': developer, **({'completion': completion} if completion else {})}
        return self.store.compare_and_update(TASK, expected={'developer': expected}, **changes)

    def carry(self, *, base=BASE, head=HEAD, execution=(), record=True, **changes):
        callback = cli._direct_result_callback(self.task, self.d6, base, result_head=head,
                                               test_policy='LOCAL')
        callback['evidence']['carried_result'] = {'mode': 'NO_RUN_CHANGES',
            'assignment_id': 'f' * 32, 'd6_base': base, 'carried_head': head} if record else None
        developer = dict(self.dispatching, state='DEVELOPER_RESPONDED', source_sha=base,
            artifact=DIFF, artifact_sha256=hashlib.sha256(DIFF.encode()).hexdigest(),
            direct_result_callback=callback, execution=dict({'status': 'DONE',
                'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': 0, 'stdout_sha256': 'e' * 64, 'elapsed_ms': 1,
                'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN', 'source_bytes': None,
                'source_file_count': None, 'source_manifest_sha256': None}, **dict(execution)))
        developer.update(changes)
        return developer, dict(self.completion, evidence=dict(self.completion['evidence'],
                                                              source_sha=base, result_head=head))

    def test_finished_run_carries_the_committed_head(self):
        developer, completion = self.carry()
        current = self.store.get(TASK)
        self.assertEqual(wd.task_store_attempts(current, {'developer': developer}),
                         [('claude-developer', 'COMMITTED_RESULT_CARRY')])
        self.assertIsNone(wd.authorize_task_store_change(current, {
            'developer': developer, 'completion': completion}, {'developer': self.dispatching}))
        self.assertEqual(current['dispatch_audit'][-1]['action'], 'COMMITTED_RESULT_CARRY')
    def test_the_real_store_stores_the_carry(self):
        saved = self.write(*self.carry(), self.dispatching)
        evidence = saved['developer']['direct_result_callback']['evidence']
        self.assertEqual((saved['developer']['state'], saved['developer']['source_sha'],
                          evidence['source_sha'], evidence['result_head'],
                          saved['completion']['evidence']['result_head']),
                         ('DEVELOPER_RESPONDED', BASE, BASE, HEAD, HEAD))
        audit = saved['dispatch_audit'][-1]
        self.assertEqual((audit['action'], audit['decision'], audit['assignment_id']),
                         ('COMMITTED_RESULT_CARRY', 'ALLOWED', 'f' * 32))

    def assert_denied(self, carry, code):
        with self.assertRaises(wd.WorkerDispatchDenied) as caught:
            self.write(*carry, self.dispatching)
        self.assertEqual(caught.exception.dispatch_code, code)
        self.assertEqual(self.store.get(TASK)['developer']['source_sha'], HEAD)

    def test_refused_carries(self):
        cases = [{'head': '3' * 40}, {'write_paths': ['x', 'y']}, {'artifact_sha256': '0' * 64},
                 {'record': False},
                 {'execution': {'status': 'TIMEOUT'}}, {'execution': {'process_exit_code': 1}},
                 {'execution': {'cleanup_status': 'UNKNOWN'}}, {'execution': {'native_session_id': None}}]
        for change in [{'assignment_id': '9' * 32}] + cases:
            with self.subTest(change=change):
                self.setUp()
                self.assert_denied(self.carry(**change), 'WORKER_COMMITTED_CARRY_PRECONDITION'
                                   if change in cases else 'WORKER_REASSIGN_PRECONDITION')

    def test_unbound_carry_is_refused_and_a_refusal_is_counted(self):
        changes = dict(zip(('developer', 'completion'), self.carry()))
        self.assertEqual(wd.authorize_task_store_change(self.store.get(TASK), changes).dispatch_code,
                         'WORKER_COMMITTED_CARRY_PRECONDITION')
        self.assert_denied(self.carry(record=False), 'WORKER_COMMITTED_CARRY_PRECONDITION')
        self.assert_denied(self.carry(), 'WORKER_COMMITTED_CARRY_LIMIT')

    def test_a_carry_that_starts_a_reviewer_handoff_keeps_the_normal_gates(self):
        developer, completion = self.carry()
        developer['direct_result_callback']['review_handoff'] = {'dispatch_state': 'DISPATCHING'}
        attempts = wd.task_store_attempts(self.store.get(TASK), {'developer': developer})
        self.assertEqual([action for _worker, action in attempts], ['REVIEWER_HANDOFF', 'REASSIGN'])
        self.assert_denied((developer, completion), 'WORKER_REASSIGN_PRECONDITION')



if __name__ == '__main__':
    unittest.main()
