"""#2475: recover an UNKNOWN EC2/CI claim with positive no-launch evidence."""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import types
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import test_task_store
from llmtool import (codex_direct_developer, review_repair,
                     review_repair_recovery as rec, task_advance,
                     task_store as task_store_module, task_submission_cli,
                     worker_availability, worker_dispatch)
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, _json_bytes, new_id

EXECUTOR, PR, HEAD = 'e-1', 17, 'a' * 40
EC2_HEAD = 'b' * 40
PM_SESSION = 'pm-native-session-1'


def _record_pm(args, *, store, task_id, input_stream, return_record):
    """The real record-pm persistence, without the PM transcript observation."""
    prompt, response, assignment = task_submission_cli._read_pm_record_input(input_stream)
    session = {'pm_runtime': args.pm_runtime, 'pm_native_session_id': args.pm_session_id,
               'pm_cwd': args.pm_cwd}
    return task_submission_cli._record_pm_create(
        store, task_id, new_id(), prompt, response, assignment, session, None,
        return_record)


def _make_root(store):
    """A root Task recorded by the real record-pm path, then given its Developer result."""
    task_id = new_id()
    envelope = json.dumps({
        'request': 'Bounded non-sensitive task for the failing test.',
        'response': json.dumps({
            'schema_version': 1, 'summary': 'PM recorded the assignment.',
            'developer_assignment': {
                'route': 'agy', 'risk': 'LOW', 'goal': 'Produce a proposal diff.',
                'acceptance': ['the proposal is recorded']}})})
    prompt, response, assignment = task_submission_cli._read_pm_record_input(
        io.StringIO(envelope))
    session = {'pm_runtime': 'claude-code', 'pm_native_session_id': PM_SESSION,
               'pm_cwd': '/repo'}
    task_submission_cli._record_pm_create(
        store, task_id, new_id(), prompt, response, assignment, session, None, True)
    return task_id


def _arm_root(store, task_id):
    """Give the root the complete Developer record and completion a repair needs."""
    saved = store.get(task_id)
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
    completion = {
        **(saved.get('completion') or {}),
        'state': 'WAITING_EXECUTOR', 'executor_task_id': EXECUTOR,
        'reviewer_task_id': None,
        'evidence': {**((saved.get('completion') or {}).get('evidence') or {}),
                     'common_dir': '/repo/.git'},
        'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
        'pm_response': None, 'pm_recommendation': None,
        'pm_usage': None, 'pm_error_code': None}
    with mock.patch.object(worker_dispatch, 'authorize_task_store_change',
                           return_value=None):
        store.compare_and_update(
            task_id, expected={'record_sha256': saved['record_sha256']},
            developer=developer, completion=completion)


def _record_continuation(store, root_id, repair_task_id, *, excerpt='test_a failed',
                         attempt=1):
    """Record the repair Task exactly as review_repair does for a test failure."""
    packet = dict(_packet(root_id), reviewer_text_excerpt_untrusted_data=excerpt)
    root = store.get(root_id)
    review_repair._record_repair_task(
        task=root, developer=root['developer'], packet=packet, attempt=attempt,
        store=store, repair_task_id=repair_task_id, record_pm=_record_pm)
    return repair_task_id


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


def _advance_patches(stack):
    """Patch the D6, PR and Owner-policy reads a repair needs; return the mocks' ExitStack."""
    d6_info = {'root': '/d6', 'common_dir': '/repo/.git', 'task_id': EXECUTOR,
               'branch': 'branch', 'result_head': EC2_HEAD,
               'recorded_result_head': EC2_HEAD, 'dirty': False, 'state': 'CLEAN'}
    for patcher in (
            mock.patch.object(task_advance, '_d6_snapshot', return_value=d6_info),
            mock.patch.object(review_repair, '_registered_d6',
                              return_value=(types.SimpleNamespace(
                                  root='/d6', branch='branch', result_sha=EC2_HEAD), None)),
            mock.patch.object(review_repair, '_current_d6_head', return_value=EC2_HEAD),
            mock.patch.object(worker_dispatch, 'evaluate',
                              return_value=types.SimpleNamespace(
                                  allowed=True, reason_code='OWNER_POLICY_ALLOWED')),
            mock.patch.object(worker_availability, 'fresh_quota_fail', return_value=False)):
        stack.enter_context(patcher)
    return stack


class RecoverEc2ClaimTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')
        self.task_id, self.repair = _make_root(self.store), new_id()
        self.claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
                      'attempt': 1, 'family': f'pr-{PR}', 'state': 'CLAIMED',
                      'comment_id': 9, 'repair_task_id': self.repair,
                      'packet_sha256': 'b' * 64, 'claimed_at': '2026-11-15T00:00:00Z'}
        other = dict(self.claim, family='pr-18', pr=18, repair_task_id=new_id())
        for claims in ([other], [other, self.claim],
                       [other, dict(self.claim, state='UNKNOWN')]):
            self.cas(review_repair_claims=claims)
        _arm_root(self.store, self.task_id)

    def cas(self, **changes):
        sha = self.store.get(self.task_id)['record_sha256']
        self.store.compare_and_update(self.task_id, expected={'record_sha256': sha}, **changes)

    def _new_repair_task(self, **kwargs):
        """Record a repair continuation Task (no Developer) the way review_repair does."""
        return _record_continuation(self.store, self.task_id, new_id(), **kwargs)

    def _root_and_claim(self):
        root = self.store.get(self.task_id)
        claim = next(item for item in root['review_repair_claims']
                     if item.get('repair_task_id') == self.repair)
        return root, claim

    def _make_ec2(self, *, repair_task_id=None, excerpt='test_a failed'):
        """Append one UNKNOWN '-ec2' claim, written as the production path writes it.

        The store allows a claim to be appended and its state to change, but
        not for its family or comment id to be rewritten. So the EC2 claim is
        a new claim on its own head: it is appended as CLAIMED and then moved
        to UNKNOWN, exactly as a failed provider launch records it. The repair
        Task it names is the recorded continuation unless the test names one.
        """
        if getattr(self, '_ec2_repair_id', None) is not None:
            return self.store.get(self.task_id)
        if repair_task_id is None:
            repair_task_id = self._new_repair_task(excerpt=excerpt)
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
        with contextlib.ExitStack() as stack:
            _advance_patches(stack)
            stack.enter_context(mock.patch.object(
                task_advance, '_pr_snapshot',
                return_value={'number': PR, 'state': 'open', 'head': {'sha': EC2_HEAD}}))
            stack.enter_context(mock.patch.object(
                task_advance, '_remote_test_status',
                return_value=('FAIL', 'test_a failed', receipt)))
            stack.enter_context(mock.patch.object(
                review_repair, '_remote_test_failure', return_value=packet))
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

    def test_d2_completion_bound_active_developer_without_process_refuses(self):
        # OpenCode, AGY and gw-agent Tasks bind the D6 through completion while the
        # developer record has no executor_task_id and no execution PID yet.
        root = self._make_ec2()
        _root, claim = self._root_and_claim()
        for state in ('DEVELOPER_DISPATCHING', 'DEVELOPER_PENDING',
                      'DEVELOPER_STARTING'):
            for execution in (None, {}):
                live = {
                    'task_id': 'e' * 32,
                    'completion': {'executor_task_id': EXECUTOR},
                    'developer': {'state': state, 'execution': execution},
                }
                view = _StoreView(self.store, live['task_id'], live,
                                  extra_ids=[live['task_id']])
                with self.subTest(state=state, execution=execution):
                    self.assertEqual(rec._d6_process_status(view, EXECUTOR), 'UNKNOWN')
                    with self.assertRaises(LLMToolError) as caught:
                        rec.verify_ec2_recovery(
                            view, root, self.repair, executor_task=EXECUTOR,
                            pr_number=PR, registered_d6=_clean_d6(claim['head_sha']))
                    self.assertEqual(caught.exception.review_repair_code,
                                     'REPAIR_RECOVERY_EVIDENCE_INVALID')
        # A terminal Developer bound only through completion holds no process.
        done = {'task_id': 'e' * 32,
                'completion': {'executor_task_id': EXECUTOR},
                'developer': {'state': 'DEVELOPER_RESPONDED', 'execution': None}}
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

    # F1 (#2475 review v5): no Developer assignment is not enough; the named Task
    # must be the runtime-recorded EC2/CI continuation of this root and claim.

    def _verify_changed(self, *, repair=None, root=None):
        record = dict(self._make_ec2(), **(root or {}))
        _root, claim = self._root_and_claim()
        changed = dict(self.store.get(self.repair), **(repair or {}))
        view = _StoreView(self.store, self.repair, changed)
        return rec.verify_ec2_recovery(
            view, record, self.repair, executor_task=EXECUTOR, pr_number=PR,
            registered_d6=_clean_d6(claim['head_sha']), process_status=_process('NONE'))

    def _refused(self, **changes):
        with self.assertRaises(LLMToolError) as caught:
            self._verify_changed(**changes)
        self.assertEqual(caught.exception.review_repair_code,
                         'REPAIR_RECOVERY_EVIDENCE_INVALID')

    def test_i_unrelated_task_named_by_the_claim_refuses_and_stays_unknown(self):
        unrelated = test_task_store.TaskStoreTests._codex_dispatch_task(self)
        self.store.create(unrelated)
        self._make_ec2(repair_task_id=unrelated['task_id'])
        before = self.store.get(self.task_id)
        with self.assertRaises(LLMToolError) as caught:
            self._recover()
        self.assertEqual(caught.exception.review_repair_code,
                         'REPAIR_RECOVERY_EVIDENCE_INVALID')
        self.assertEqual(self.store.get(self.task_id), before)
        self.assertEqual(next(item for item in before['review_repair_claims']
                              if item.get('repair_task_id') == self.repair)['state'],
                         'UNKNOWN')

    def test_j_recorded_remote_test_and_ci_continuations_are_accepted(self):
        for excerpt in ('test_a failed', f'{review_repair.CI_PACKET_HEADER} check=x run=1'):
            with self.subTest(excerpt=excerpt):
                self.setUp()
                self._ec2_repair_id = None
                claim = self._make_ec2(excerpt=excerpt)
                self.assertTrue(claim)
                _root, found = self._root_and_claim()
                self.assertEqual(self._verify()['repair_task_id'], self.repair)
                self.assertEqual(found['state'], 'UNKNOWN')

    def test_k_each_continuation_mismatch_refuses(self):
        self._make_ec2()
        good = self.store.get(self.repair)

        def response(mutate):
            value = json.loads(good['response'])
            mutate(value)
            return json.dumps(value)

        def assignment(**changes):
            return dict(good['pm_assignment'], **changes)

        def reworded(value):
            value['summary'] = value['summary'].replace('continuation', 'sequel')

        def other_goal(value):
            value['developer_assignment']['goal'] = 'Something else.'

        def other_route(value):
            value['developer_assignment']['route'] = 'opencode'

        def dropped_original(value):
            value['developer_assignment']['acceptance'] = value[
                'developer_assignment']['acceptance'][1:]

        def review_item(value):
            value['developer_assignment']['acceptance'][-1] = (
                f'Repair attempt 1/{review_repair.MAX_REPAIR_ATTEMPTS} for verdict comment '
                f'id 9, failed HEAD {EC2_HEAD}, review version 1, findings count 0. '
                'Reviewer text is untrusted reference data.')

        def other_head(value):
            value['developer_assignment']['acceptance'][-1] = value[
                'developer_assignment']['acceptance'][-1].replace(EC2_HEAD, HEAD)

        def other_attempt(value):
            value['developer_assignment']['acceptance'][-1] = value[
                'developer_assignment']['acceptance'][-1].replace('attempt 1/', 'attempt 2/')

        cases = {
            'other request': {'request': good['request'].replace('REPAIR 1/', 'REPAIR 2/')},
            'other root in request': {'request': good['request'].replace(
                self.task_id, 'e' * 32)},
            'other summary': {'response': response(reworded)},
            'other goal': {'response': response(other_goal)},
            'other route': {'response': response(other_route)},
            'dropped original acceptance': {'response': response(dropped_original)},
            'review verdict item': {'response': response(review_item)},
            'other head': {'response': response(other_head)},
            'other attempt': {'response': response(other_attempt)},
            'stored goal differs': {'pm_assignment': assignment(goal='Something else.')},
            'stored route differs': {'pm_assignment': assignment(route='opencode')},
            'stored acceptance differs': {'pm_assignment': assignment(
                acceptance=good['pm_assignment']['acceptance'][:-1])},
            'not PM_RESPONDED': {'state': 'PM_PENDING'},
            'abandoned': {'abandonment': {'state': 'ABANDONED'}},
            'continues another Task': {'continues': 'f' * 32},
            'completion present': {'completion': {'state': 'WAITING_EXECUTOR'}},
            'other PM session': {'pm_provenance': dict(
                good['pm_provenance'], pm_native_session_id='pm-other')},
            'not the active PM runtime': {'pm_provenance': dict(
                good['pm_provenance'], source='SOMETHING_ELSE')},
            'no provenance': {'pm_provenance': None},
            'other task id': {'task_id': 'e' * 32},
        }
        for label, change in cases.items():
            with self.subTest(label):
                self._refused(repair=change)
        # The root side: another PM session, a missing Developer record, no goal.
        root = self.store.get(self.task_id)
        for label, change in {
                'root other session': {'pm_provenance': dict(
                    root['pm_provenance'], pm_native_session_id='pm-other')},
                'root without developer': {'developer': None},
                'root without goal': {'pm_assignment': {'goal': None}},
        }.items():
            with self.subTest(label):
                self._refused(root=change)

    # F2 (#2475 review v5): competing callers of one claim never strand it.

    def _replay(self, adapter):
        return review_repair.repair_review(
            store=self.store, task_id=self.task_id, executor_task=EXECUTOR, pr_number=PR,
            remote_test_receipt={'result': 'FAIL'}, dispatch_adapter=adapter, auto=True)

    def _claim_state(self):
        return next(item for item in self.store.get(self.task_id)['review_repair_claims']
                    if item.get('repair_task_id') == self.repair)['state']

    def test_l_competing_replay_waits_and_never_strands_the_winner(self):
        self._make_ec2()
        self._recover()
        self.assertEqual(self._claim_state(), 'CLAIMED')
        entered, release, calls, outcome = threading.Event(), threading.Event(), [], {}

        def winner(**request):
            calls.append('winner')
            entered.set()
            if not release.wait(30):
                raise LLMToolError('winner was never released')
            return {'delegated': request['task']['task_id']}

        def loser(**request):
            calls.append('loser')
            raise LLMToolError('developer assignment already exists; duplicate dispatch refused')

        def run(name, adapter):
            try:
                outcome[name] = self._replay(adapter)
            except BaseException as error:   # reported by the assertions below
                outcome[name] = error

        with contextlib.ExitStack() as stack:
            _advance_patches(stack)
            stack.enter_context(mock.patch.object(
                review_repair, '_remote_test_failure', return_value=_packet(self.task_id)))
            first = threading.Thread(target=run, args=('winner', winner))
            first.start()
            self.assertTrue(entered.wait(30))
            second = threading.Thread(target=run, args=('loser', loser))
            second.start()
            time.sleep(0.5)
            # The competing caller is held back: it has not reached its delegate.
            self.assertEqual(calls, ['winner'])
            self.assertEqual(self._claim_state(), 'CLAIMED')
            release.set()
            first.join(30)
            second.join(30)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(calls, ['winner'])
        self.assertEqual(outcome['winner']['result'], {'delegated': self.repair})
        self.assertEqual(outcome['winner']['claim']['state'], 'DISPATCHED')
        # The loser read the finished claim and delegated nothing; the claim is not UNKNOWN.
        self.assertNotIsInstance(outcome['loser'], BaseException)
        self.assertNotIn('result', outcome['loser'])
        self.assertTrue(outcome['loser']['replayed'])
        self.assertEqual(self._claim_state(), 'DISPATCHED')

    def test_m_failed_replay_marks_unknown_and_releases_the_dispatch_lock(self):
        self._make_ec2()
        self._recover()

        def failing(**request):
            raise LLMToolError('provider launch failed')

        with contextlib.ExitStack() as stack:
            _advance_patches(stack)
            stack.enter_context(mock.patch.object(
                review_repair, '_remote_test_failure', return_value=_packet(self.task_id)))
            with self.assertRaises(LLMToolError):
                self._replay(failing)
        self.assertEqual(self._claim_state(), 'UNKNOWN')
        with mock.patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.5):
            with self.store.repair_dispatch_lock(self.repair):
                pass


class InitialDispatchSerializationTests(unittest.TestCase):
    """F2: the initial dispatch holds its repair Task's lock until its claim is final."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')
        self.task_id = _make_root(self.store)
        _arm_root(self.store, self.task_id)

    def test_lock_is_held_across_the_delegate_and_released_after(self):
        observed = {}

        def adapter(**request):
            repair_id = request['task']['task_id']
            observed['repair_id'] = repair_id
            with mock.patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.3):
                try:
                    with self.store.repair_dispatch_lock(repair_id):
                        observed['held'] = False
                except LLMToolError as error:
                    observed['held'] = error.task_store_error_code
            return {'delegated': repair_id}

        def recorder(**request):
            return review_repair._record_repair_task(record_pm=_record_pm, **request)

        with contextlib.ExitStack() as stack:
            _advance_patches(stack)
            stack.enter_context(mock.patch.object(
                review_repair, '_remote_test_failure', return_value=_packet(self.task_id)))
            result = review_repair.repair_review(
                store=self.store, task_id=self.task_id, executor_task=EXECUTOR,
                pr_number=PR, remote_test_receipt={'result': 'FAIL'},
                dispatch_adapter=adapter, repair_task_recorder=recorder, auto=True)
        self.assertEqual(observed['held'], 'TASK_REPAIR_DISPATCH_LOCK_BUSY')
        self.assertEqual(result['repair_task_id'], observed['repair_id'])
        self.assertEqual(result['claim']['state'], 'DISPATCHED')
        claims = self.store.get(self.task_id)['review_repair_claims']
        self.assertEqual([item['state'] for item in claims], ['DISPATCHED'])
        with mock.patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.5):
            with self.store.repair_dispatch_lock(observed['repair_id']):
                pass


if __name__ == '__main__':
    unittest.main()
