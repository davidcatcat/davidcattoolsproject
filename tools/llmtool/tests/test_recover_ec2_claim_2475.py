"""#2475: recover an UNKNOWN EC2/CI claim with positive no-launch evidence."""
import hashlib
import json
import os
import types
import unittest
from unittest import mock

import test_task_store
import test_task_store_claim_recovery as base
from llmtool import (codex_direct_developer, review_repair,
                     review_repair_recovery as rec, task_advance,
                     worker_availability, worker_dispatch)
from llmtool.errors import LLMToolError
from llmtool.task_store import _json_bytes

EXECUTOR, PR, HEAD = 'e-1', 17, 'a' * 40
EC2_HEAD = 'b' * 40


def _packet(task_id):
    """The remote-test failure packet the next advance derives for the claim."""
    return {
        'task': task_id, 'pr': PR, 'executor_task': EXECUTOR,
        'failed_head': EC2_HEAD, 'version': 0,
        'verdict_comment_id': 0, 'findings_count': 0,
        'reviewer_text_excerpt_untrusted_data': 'test_a failed',
    }


def _packet_sha(packet):
    """The digest review_repair binds a claim to (same canonical JSON form)."""
    return hashlib.sha256(json.dumps(
        packet, ensure_ascii=True, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _clean_d6(head, clean=True):
    """A registered-D6 reader whose snapshot is at head; clean says dirty."""
    return lambda root, executor_task: (
        types.SimpleNamespace(result_sha=head, root='/d6', branch='branch'),
        types.SimpleNamespace(head=head, clean=clean))


def _process(status):
    return lambda store, executor_task: status


class _StoreView:
    """The real store, except that one Task record is supplied by the test."""

    def __init__(self, store, task_id, record, extra_ids=()):
        self._store, self._task_id, self._record = store, task_id, record
        self._extra_ids = tuple(extra_ids)

    def get(self, task_id):
        return self._record if task_id == self._task_id else self._store.get(task_id)

    def task_ids(self):
        return list(self._store.task_ids()) + list(self._extra_ids)


class RecoverEc2ClaimTests(unittest.TestCase):
    setUp = base.ClaimRecoveryStoreTests.setUp
    cas = base.ClaimRecoveryStoreTests.cas

    def _new_repair_task(self):
        """Create a repair Task with no Developer, from the TaskStore test builder."""
        repair = test_task_store.TaskStoreTests._codex_dispatch_task(self)
        self.store.create(repair)
        return repair['task_id']

    def _root_and_claim(self):
        root = self.store.get(self.task_id)
        claim = next(item for item in root['review_repair_claims']
                     if item.get('repair_task_id') == self.repair)
        return root, claim

    def _make_ec2(self):
        """Append one UNKNOWN '-ec2' claim, written as the production path writes it.

        The store allows a claim to be appended and its state to change, but
        not for its family or comment id to be rewritten. So the EC2 claim is
        a new claim on its own head: it is appended as CLAIMED and then moved
        to UNKNOWN, exactly as a failed provider launch records it.
        """
        repair_task_id = getattr(self, '_ec2_repair_id', None)
        if repair_task_id is not None:
            return self.store.get(self.task_id)
        repair_task_id = self._new_repair_task()
        root = self.store.get(self.task_id)
        source = next(item for item in root['review_repair_claims']
                      if item.get('repair_task_id') == self.repair)
        claim = dict(source, repair_task_id=repair_task_id, family=f'pr-{PR}-ec2',
                     comment_id=None, state='CLAIMED', head_sha=EC2_HEAD,
                     attempt=1, version=1,
                     packet_sha256=_packet_sha(_packet(self.task_id)))
        self.store.compare_and_update(
            self.task_id, expected={'record_sha256': root['record_sha256']},
            review_repair_claims=[dict(item) for item in root['review_repair_claims']] + [claim])
        root = self.store.get(self.task_id)
        claims = [dict(item, state='UNKNOWN')
                  if item.get('repair_task_id') == repair_task_id else dict(item)
                  for item in root['review_repair_claims']]
        self.store.compare_and_update(
            self.task_id, expected={'record_sha256': root['record_sha256']},
            review_repair_claims=claims)
        self.repair = repair_task_id
        self._ec2_repair_id = repair_task_id
        return self.store.get(self.task_id)

    def _recover(self, **kwargs):
        try:
            _root, claim = self._root_and_claim()
            head = claim.get('head_sha')
        except Exception:
            head = HEAD
        args = dict(store=self.store, task_id=self.task_id, executor_task=EXECUTOR,
                    pr_number=PR, repair_task_id=self.repair,
                    registered_d6=_clean_d6(head), process_status=_process('NONE'))
        return rec.recover_unknown_claim(**{**args, **kwargs})

    def _verify(self, *, developer=None, registered=None, status='NONE',
                pr_number=PR, executor_task=EXECUTOR):
        root = self._make_ec2()
        _root, claim = self._root_and_claim()
        repair = dict(self.store.get(self.repair), developer=developer)
        view = _StoreView(self.store, self.repair, repair)
        return rec.verify_ec2_recovery(
            view, root, self.repair, executor_task=executor_task, pr_number=pr_number,
            registered_d6=registered or _clean_d6(claim['head_sha']),
            process_status=_process(status))

    def test_a_ec2_unknown_claim_recovers_once_without_a_new_count(self):
        before = self._make_ec2()
        _root, claim = self._root_and_claim()
        result = self._recover()
        self.assertTrue(result['recovered'])
        saved = self.store.get(self.task_id)
        updated = next(item for item in saved['review_repair_claims']
                       if item.get('repair_task_id') == self.repair)
        # The claim is live again, so the next advance may delegate its repair Task.
        self.assertEqual(updated['state'], 'CLAIMED')
        self.assertEqual(updated['attempt'], claim['attempt'])
        self.assertEqual(updated['repair_task_id'], claim['repair_task_id'])
        # The same claim, head and attempt remain; no repair count or budget moved.
        self.assertEqual(len(saved['review_repair_claims']),
                         len(before['review_repair_claims']))
        self.assertEqual(saved.get('high_debug'), before.get('high_debug'))
        expected = [dict(item, state='CLAIMED')
                    if item.get('repair_task_id') == self.repair else item
                    for item in before['review_repair_claims']]
        self.assertEqual(saved['review_repair_claims'], expected)
        # The same repair Task remains the one the next step may delegate.
        self.assertIsNone(self.store.get(self.repair).get('developer'))
        self.assertEqual(result['claim']['repair_task_id'], self.repair)
        # Next task advance delegates the same repair Task on the same claim and attempt.
        receipt = {
            'task_id': saved['task_id'], 'run_id': saved['run_id'],
            'executor_task': EXECUTOR, 'result': 'FAIL',
            'source_sha': EC2_HEAD, 'modules': ['test_x'],
            'failure_excerpt': 'test_a failed',
        }
        receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
        packet = _packet(saved['task_id'])
        self.assertEqual(updated['packet_sha256'], _packet_sha(packet))
        delegated = []
        d6_info = {'root': '/d6', 'common_dir': '/repo/.git', 'task_id': EXECUTOR,
                   'branch': 'branch', 'result_head': EC2_HEAD,
                   'recorded_result_head': EC2_HEAD, 'dirty': False, 'state': 'CLEAN'}
        with mock.patch.object(task_advance, '_d6_snapshot', return_value=d6_info), \
                mock.patch.object(task_advance, '_pr_snapshot',
                                  return_value={'number': PR, 'state': 'open',
                                                'head': {'sha': EC2_HEAD}}), \
                mock.patch.object(task_advance, '_remote_test_status',
                                  return_value=('FAIL', 'test_a failed', receipt)), \
                mock.patch.object(review_repair, '_registered_d6',
                                  return_value=(types.SimpleNamespace(
                                      root='/d6', branch='branch', result_sha=EC2_HEAD), None)), \
                mock.patch.object(review_repair, '_current_d6_head', return_value=EC2_HEAD), \
                mock.patch.object(review_repair, '_remote_test_failure', return_value=packet), \
                mock.patch.object(worker_dispatch, 'evaluate',
                                  return_value=types.SimpleNamespace(
                                      allowed=True, reason_code='OWNER_POLICY_ALLOWED')), \
                mock.patch.object(worker_availability, 'fresh_quota_fail', return_value=False):
            # Ensure the root developer/completion exist for repair_review.
            with mock.patch.object(worker_dispatch, 'authorize_task_store_change',
                                   return_value=None):
                diff = 'diff --git a/src.py b/src.py\n'
                # A complete Developer record from the TaskStore test builder.
                developer = {
                    **test_task_store.TaskStoreTests._developer_assignment(provider='agy'),
                    'state': 'DEVELOPER_RESPONDED', 'provider_type': 'agy-cli',
                    'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
                    'owner_assignment_ref': None, 'executor_task_id': EXECUTOR,
                    'artifact': diff,
                    'artifact_sha256': hashlib.sha256(diff.encode('utf-8')).hexdigest(),
                }
                self.cas(
                    developer=developer,
                    completion={**(saved.get('completion') or {}),
                                'state': 'WAITING_EXECUTOR', 'executor_task_id': EXECUTOR,
                                'reviewer_task_id': None,
                                'evidence': {**((saved.get('completion') or {}).get('evidence') or {}),
                                             'common_dir': '/repo/.git'},
                                'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                                'pm_response': None, 'pm_recommendation': None,
                                'pm_usage': None, 'pm_error_code': None})
            # The claim's packet_sha256 is the real digest of this packet, so the
            # replay finds the recovered claim without any digest being faked.
            adv = task_advance.advance(
                task_id=self.task_id, store=self.store, pr_number=PR,
                repair_review_call=lambda **kw: review_repair.repair_review(
                    **kw, delegate_callable=lambda args, store=None: delegated.append(
                        args.task_id) or {'delegated': args.task_id}))
        self.assertEqual(delegated, [self.repair])
        self.assertEqual(adv['step'], 'REPAIR_REVIEW')
        self.assertEqual(adv['result']['repair_task_id'], self.repair)
        self.assertEqual(adv['result']['claim']['attempt'], claim['attempt'])
        after_adv = self.store.get(self.task_id)
        self.assertEqual(len(after_adv['review_repair_claims']),
                         len(before['review_repair_claims']))
        self.assertEqual(next(item for item in after_adv['review_repair_claims']
                              if item.get('repair_task_id') == self.repair)['state'],
                         'DISPATCHED')

    def test_b_any_developer_record_refuses(self):
        for state in ('DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING', 'STARTING',
                      'DEVELOPER_RESPONDED'):
            with self.subTest(state=state), self.assertRaises(LLMToolError) as caught:
                self._verify(developer={'state': state})
            self.assertEqual(caught.exception.review_repair_code,
                             'REPAIR_RECOVERY_EVIDENCE_INVALID')

    def test_c_dirty_or_moved_d6_refuses(self):
        _root, claim = self._root_and_claim()
        other = '0' * 40 if claim['head_sha'] != '0' * 40 else '1' * 40
        for registered in (_clean_d6(claim['head_sha'], clean=False), _clean_d6(other)):
            with self.subTest(registered=registered), self.assertRaises(LLMToolError) as caught:
                self._verify(registered=registered)
            self.assertEqual(caught.exception.review_repair_code,
                             'REPAIR_RECOVERY_EVIDENCE_INVALID')

    def test_d_live_or_unknown_process_refuses(self):
        for status in ('ALIVE', 'UNKNOWN'):
            with self.subTest(status=status), self.assertRaises(LLMToolError) as caught:
                self._verify(status=status)
            self.assertEqual(caught.exception.review_repair_code,
                             'REPAIR_RECOVERY_EVIDENCE_INVALID')
        # The default scanner sees a real live process recorded on this D6.
        root = self._make_ec2()
        _root, claim = self._root_and_claim()
        pid = os.getpid()
        live = {
            'task_id': 'c' * 32,
            'completion': {'executor_task_id': EXECUTOR},
            'developer': {'executor_task_id': EXECUTOR, 'execution': {
                'child_pid': pid,
                'child_start_time': codex_direct_developer.process_start_time(pid),
                'child_pgid': os.getpgid(pid)}},
        }
        view = _StoreView(self.store, live['task_id'], live, extra_ids=[live['task_id']])
        with self.assertRaises(LLMToolError) as caught:
            rec.verify_ec2_recovery(
                view, root, self.repair, executor_task=EXECUTOR, pr_number=PR,
                registered_d6=_clean_d6(claim['head_sha']))
        self.assertEqual(caught.exception.review_repair_code,
                         'REPAIR_RECOVERY_EVIDENCE_INVALID')

    def test_d3_exited_leader_needs_positive_group_absence(self):
        # The leader is gone, but a descendant may live on in its process group.
        gone = ProcessLookupError()
        with mock.patch.object(rec.os, 'kill', side_effect=gone):
            with mock.patch.object(rec.os, 'killpg', return_value=None) as killpg:
                self.assertEqual(rec._pid_status(4242, 'start', 4242), 'ALIVE')
                killpg.assert_called_once_with(4242, 0)
            with mock.patch.object(rec.os, 'killpg', side_effect=ProcessLookupError()):
                self.assertEqual(rec._pid_status(4242, 'start', 4242), 'DEAD')
            for error in (PermissionError(), OSError()):
                with mock.patch.object(rec.os, 'killpg', side_effect=error):
                    self.assertEqual(rec._pid_status(4242, 'start', 4242), 'UNKNOWN')
        # A reused pid or a leader that left the group is also checked by group.
        with mock.patch.object(rec.os, 'kill', return_value=None), \
                mock.patch.object(codex_direct_developer, 'process_start_time',
                                  return_value='other'):
            with mock.patch.object(rec.os, 'killpg', return_value=None):
                self.assertEqual(rec._pid_status(4242, 'start', 4242), 'ALIVE')
            with mock.patch.object(rec.os, 'killpg', side_effect=ProcessLookupError()):
                self.assertEqual(rec._pid_status(4242, 'start', 4242), 'DEAD')
        with mock.patch.object(rec.os, 'kill', return_value=None), \
                mock.patch.object(codex_direct_developer, 'process_start_time',
                                  return_value='start'), \
                mock.patch.object(rec.os, 'getpgid', return_value=1), \
                mock.patch.object(rec.os, 'killpg', return_value=None):
            self.assertEqual(rec._pid_status(4242, 'start', 4242), 'ALIVE')

    def test_d2_active_d6_developer_without_process_evidence_refuses(self):
        # A D6-bound Developer that is still active but has no recorded process
        # (provider launch in progress, PID not yet written) is UNKNOWN, not absent.
        root = self._make_ec2()
        _root, claim = self._root_and_claim()
        for state in ('DEVELOPER_DISPATCHING', 'DEVELOPER_PENDING',
                      'DEVELOPER_OUTCOME_UNKNOWN'):
            for execution in (None, {}):
                live = {
                    'task_id': 'd' * 32,
                    'developer': {'executor_task_id': EXECUTOR, 'state': state,
                                  'execution': execution},
                }
                view = _StoreView(self.store, live['task_id'], live,
                                  extra_ids=[live['task_id']])
                self.assertEqual(rec._d6_process_status(view, EXECUTOR), 'UNKNOWN')
                with self.subTest(state=state, execution=execution), \
                        self.assertRaises(LLMToolError) as caught:
                    rec.verify_ec2_recovery(
                        view, root, self.repair, executor_task=EXECUTOR, pr_number=PR,
                        registered_d6=_clean_d6(claim['head_sha']))
                self.assertEqual(caught.exception.review_repair_code,
                                 'REPAIR_RECOVERY_EVIDENCE_INVALID')
        # A terminal Developer holds no process, so it does not block recovery.
        done = {'task_id': 'd' * 32,
                'developer': {'executor_task_id': EXECUTOR,
                              'state': 'DEVELOPER_RESPONDED', 'execution': None}}
        only = types.SimpleNamespace(task_ids=lambda: [done['task_id']],
                                     get=lambda task_id: done)
        self.assertEqual(rec._d6_process_status(only, EXECUTOR), 'NONE')

    def test_e_non_unknown_other_pr_or_executor_refuses(self):
        for kwargs, code in (
                ({'pr_number': PR + 1}, 'REPAIR_RECOVERY_CLAIM_INVALID'),
                ({'executor_task': 'e-2'}, 'REPAIR_RECOVERY_CLAIM_INVALID')):
            with self.subTest(kwargs=kwargs), self.assertRaises(LLMToolError) as caught:
                self._verify(**kwargs)
            self.assertEqual(caught.exception.review_repair_code, code)
        # A claim that is already past UNKNOWN is not recovered again.
        self._make_ec2()
        self._recover()
        with self.assertRaises(LLMToolError) as caught:
            self._recover()
        self.assertEqual(caught.exception.review_repair_code,
                         'REPAIR_RECOVERY_CLAIM_INVALID')

    def test_f_review_family_recovery_is_unchanged(self):
        with mock.patch.object(rec, 'verify_recovery', return_value=None):
            result = self._recover()
        self.assertTrue(result['recovered'])
        saved = self.store.get(self.task_id)
        self.assertEqual(next(item for item in saved['review_repair_claims']
                              if item.get('repair_task_id') == self.repair)['state'],
                         'DISPATCHED')

    def test_g_no_repair_count_family_total_or_debug_budget_changes(self):
        before = self._make_ec2()
        _root, claim = self._root_and_claim()
        self._recover()
        after = self.store.get(self.task_id)

        def attempts(record):
            return sorted((item.get('family'), item.get('attempt'))
                          for item in record['review_repair_claims'])

        self.assertEqual(attempts(after), attempts(before))
        self.assertEqual(len(after['review_repair_claims']),
                         len(before['review_repair_claims']))
        self.assertEqual(after.get('high_debug'), before.get('high_debug'))
        updated = next(item for item in after['review_repair_claims']
                       if item.get('repair_task_id') == self.repair)
        self.assertEqual(updated['attempt'], claim['attempt'])

    def test_h_recovering_twice_refuses_and_changes_nothing(self):
        self._make_ec2()
        self._recover()
        saved = self.store.get(self.task_id)
        with self.assertRaises(LLMToolError) as caught:
            self._recover()
        self.assertEqual(caught.exception.review_repair_code,
                         'REPAIR_RECOVERY_CLAIM_INVALID')
        self.assertEqual(self.store.get(self.task_id), saved)


if __name__ == '__main__':
    unittest.main()
