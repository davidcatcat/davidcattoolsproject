"""#2190: an EC2 FAIL before any PR exists gets one counted test-failure repair.

The repair is stored in the reserved pre-PR family ('prepr-ec2', pr None) and
the PR opened later keeps its count: the shared EC2/CI test-failure limit
(policy test_fail_max) covers repairs made before and after the PR. No family
key gives a fresh budget, and a count in doubt stops the repair.

No credential-shaped data: every ID and hash below is a fixed placeholder.
"""
import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import (lineage_counts, repair_policy, review_repair, start_failure,
                     task_advance, task_submission_cli, worker_dispatch)
from llmtool.developer_routes import RouteError
from llmtool.errors import LLMToolError
from llmtool.repair_policy import UNKNOWN
from llmtool.task_store import TaskStore

try:
    import test_review_repair as trr
except ImportError:
    from tests import test_review_repair as trr

import review_trust_fixture
import worker_policy_fixture

TASK, EXECUTOR, PR = trr.TASK, trr.EXECUTOR, trr.PR
HEADS = (trr.HEAD, '1' * 40, '2' * 40, '3' * 40, '4' * 40)
PREPR = 'prepr-ec2'
ALLOWED = type('Decision', (), {'allowed': True})()


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _sealed(record):
    record.pop('record_sha256', None)
    record['record_sha256'] = hashlib.sha256(json.dumps(
        record, sort_keys=True).encode()).hexdigest()
    return record


def _prepr_record(**changes):
    """The shared repair fixture Task with no review yet: nothing names a PR."""
    record = trr._record()
    record.pop('review_dispatch_claim')
    record.pop('completion_events')
    record.update(changes)
    return _sealed(record)


def _used_claim(family, pr, head, attempt):
    return {'executor_task': EXECUTOR, 'pr': pr, 'head_sha': head,
            'version': attempt, 'attempt': attempt, 'family': family,
            'state': 'FAILED', 'repair_task_id': f'{attempt + 0x90:032x}',
            'comment_id': None, 'packet_sha256': 'f' * 64,
            'claimed_at': f'2026-11-18T0{attempt}:00:00Z'}


class _Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = trr.TempTaskStore(self.temp.name, _prepr_record())
        self.dispatched = []
        worker_policy_fixture.install(self)
        for target, name, value in ((review_repair.worker_dispatch, 'evaluate', ALLOWED),
                                    (review_repair.shutil, 'which', '/usr/bin/agy')):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.policy = repair_policy.load_policy()
        self.max = self.policy['test_fail_max']
        # The per-family store limit is three attempts; seeding relies on it.
        self.assertTrue(1 <= self.max <= 3)

    def seed(self, claims, **changes):
        self.store = trr.TempTaskStore(
            self.temp.name, _prepr_record(review_repair_claims=claims, **changes))

    def claims(self):
        return self.store.get(TASK).get('review_repair_claims', [])

    def _adapter(self, **request):
        self.dispatched.append(request)
        return {'status': 'DISPATCHED'}

    @staticmethod
    def _recorder(**request):
        return {'task_id': request['repair_task_id'],
                'pm_assignment': {'acceptance': ['Repair the reported issue.']}}

    def repair(self, pr_number, head, **kwargs):
        """The real review_repair.repair_review with injected provider and D6 head."""
        kwargs.setdefault('remote_test_receipt', trr._receipt(head))
        return review_repair.repair_review(
            store=self.store, task_id=TASK, executor_task=EXECUTOR,
            pr_number=pr_number, dispatch_adapter=self._adapter,
            current_head=lambda _task, _executor: head,
            repair_task_recorder=self._recorder, **kwargs)

    def refused(self, code, pr_number, head, **kwargs):
        before = self.claims()
        with self.assertRaises(LLMToolError) as caught:
            self.repair(pr_number, head, **kwargs)
        self.assertEqual(caught.exception.review_repair_code, code)
        self.assertEqual(self.claims(), before)

    def advance(self, head, *, pr_number=None):
        """One real task advance step at head; only snapshots and GitHub are injected."""
        receipt = trr._receipt(head)
        d6 = {'result_head': head, 'recorded_result_head': head, 'dirty': False,
              'root': '/registered/d6', 'branch': 'feat/prepr', 'state': 'ACTIVE'}
        pr = (None if pr_number is None else
              {'number': pr_number, 'head': {'sha': head}, 'merged': False})
        invoked = []

        def repair_call(**kwargs):
            invoked.append(kwargs)
            return review_repair.repair_review(
                dispatch_adapter=self._adapter,
                current_head=lambda _task, _executor: head,
                repair_task_recorder=self._recorder, **kwargs)

        snapshots = {
            'external_action_refusal': None, '_closed_train_member_head': None,
            '_d6_snapshot': d6, '_find_published_pr': None, '_pr_snapshot': pr,
            '_review_outcomes': [], '_merged_review_target': None,
            '_remote_test_status': ('FAIL', receipt['failure_excerpt'], receipt),
        }
        patchers = [patch.object(task_advance, name, return_value=value)
                    for name, value in snapshots.items()]
        patchers.append(patch.object(task_advance, '_ack_consumed_events',
                                     side_effect=lambda result, *_args: result))
        for patcher in patchers:
            patcher.start()
        try:
            result = task_advance._advance_step(
                task_id=TASK, store=self.store, guard=lambda: None,
                pr_number=pr_number, dispatch_review_call=None,
                repair_review_call=repair_call, publish_git_runner=None,
                publish_gh_runner=None, merge_gh_runner=None)
        finally:
            for patcher in reversed(patchers):
                patcher.stop()
        return result, invoked


class PrePrRepairTests(_Base):
    def test_prepr_fail_is_one_counted_test_failure_repair(self):
        result = self.repair(None, HEADS[0])
        [claim] = self.claims()
        self.assertEqual((claim['pr'], claim['family'], claim['attempt']), (None, PREPR, 1))
        self.assertIsNone(claim['comment_id'])
        self.assertEqual(claim['state'], 'DISPATCHED')
        self.assertEqual(result['repair_task_id'], claim['repair_task_id'])
        [request] = self.dispatched
        packet = request['packet']
        self.assertEqual((packet['pr'], packet['failed_head'], packet['version']),
                         (None, HEADS[0], 0))
        # The Developer delegate accepts the pre-PR packet as it is.
        self.assertEqual(task_submission_cli._validate_repair_packet(packet), packet)
        record = self.store.get(TASK)
        self.assertEqual(task_advance._test_fail_count(record, None, self.store), 1)
        # A replay of the same FAIL reuses the claim: no second count.
        replay = self.repair(None, HEADS[0])
        self.assertEqual(replay['repair_task_id'], claim['repair_task_id'])
        self.assertEqual(len(self.claims()), 1)
        self.assertEqual(len(self.dispatched), 1)

    def test_prepr_repair_is_not_a_review_repair(self):
        self.repair(None, HEADS[0])
        record = self.store.get(TASK)
        counts = lineage_counts.lineage_counts(
            self.store, record, base_family=f'pr-{PR}', include_successors=True)
        self.assertEqual((counts['review_repairs'], counts['review_heads'],
                          counts['test_fails']), (0, {}, 1))
        self.assertEqual(lineage_counts.chain_review_repairs(self.store, record), 0)

    def test_prepr_count_is_kept_after_the_pr_exists(self):
        # Pre-PR FAIL -> repair -> PR -> FAIL ... until the shared limit.
        self.repair(None, HEADS[0])
        for used, head in enumerate(HEADS[1:self.max], 1):
            record = self.store.get(TASK)
            self.assertEqual(task_advance._test_fail_count(record, PR, self.store), used)
            result = self.repair(PR, head)
            self.assertEqual(result['claim']['family'], f'pr-{PR}-ec2')
        record = self.store.get(TASK)
        self.assertEqual(task_advance._test_fail_count(record, PR, self.store), self.max)
        self.assertTrue(task_advance._test_family_used_up(
            record, PR, HEADS[self.max], self.store))
        self.refused('REPAIR_LIMIT_REACHED', PR, HEADS[self.max])
        # The pre-PR family is spent too: no PR again gives no fresh budget.
        self.refused('REPAIR_LIMIT_REACHED', None, HEADS[self.max])
        self.assertEqual(len(self.dispatched), self.max)

    def test_a_pr_family_count_is_kept_when_no_pr_exists(self):
        used = [_used_claim(f'pr-{PR}-ec2', PR, HEADS[n], n) for n in range(1, self.max + 1)]
        self.seed(used)
        record = self.store.get(TASK)
        self.assertEqual(task_advance._test_fail_count(record, None, self.store), self.max)
        self.refused('REPAIR_LIMIT_REACHED', None, HEADS[0])
        # A legacy EC2 claim stored in a PR base family counts as well.
        legacy = [_used_claim(f'pr-{PR}', PR, HEADS[n], n) for n in range(1, self.max + 1)]
        self.seed(legacy)
        self.refused('REPAIR_LIMIT_REACHED', None, HEADS[0])
        self.assertEqual(self.dispatched, [])

    def test_the_prepr_family_cannot_be_chosen_or_switched(self):
        ci_failure = {'head': HEADS[0], 'check_name': 'check (Linux 3.14)',
                      'check_run_id': 7, 'conclusion': 'failure', 'excerpt': 'FAIL'}
        cases = {
            'reserved family with a PR': dict(pr_number=PR, family='prepr'),
            'reserved family without a PR': dict(pr_number=None, family='prepr'),
            'chosen family without a PR': dict(pr_number=None, family='network'),
            'CI repair without a PR': dict(pr_number=None, remote_test_receipt=None,
                                           ci_failure=ci_failure),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                pr_number = kwargs.pop('pr_number')
                self.refused('REPAIR_FAMILY_INVALID', pr_number, HEADS[0], **kwargs)
        # A review repair needs a review of a PR; without one nothing is claimed.
        self.refused('REVIEW_FAIL_EVENT_MISSING', None, HEADS[0], remote_test_receipt=None)
        self.assertEqual(self.dispatched, [])

    def test_an_unknown_count_fails_closed(self):
        # A predecessor that cannot be read puts the whole chain in doubt.
        self.seed([], **{'continues': 'e' * 32})
        record = self.store.get(TASK)
        self.assertEqual(task_advance._test_fail_count(record, None, self.store), UNKNOWN)
        self.assertEqual(task_advance._test_fail_count(record, None), UNKNOWN)
        self.refused('REPAIR_LIMIT_REACHED', None, HEADS[0])
        result, invoked = self.advance(HEADS[0])
        self.assertEqual((result['step'], result['codes']),
                         ('STOPPED', ['TEST_FAIL_LIMIT_REACHED']))
        self.assertEqual(invoked, [])
        # A claim family in doubt is UNKNOWN, never skipped.
        alone = {'task_id': TASK, 'review_repair_claims': [
            dict(_used_claim(PREPR, None, HEADS[1], 1), family=7)]}
        self.assertEqual(lineage_counts.lineage_counts(
            None, alone, base_family=lineage_counts.PREPR_BASE_FAMILY)['test_fails'],
            UNKNOWN)
        alone_pr = {'task_id': TASK, 'review_repair_claims': [
            dict(_used_claim(f'pr-{PR}-ec2', PR, HEADS[1], 1), family=7)]}
        self.assertEqual(lineage_counts.lineage_counts(
            None, alone_pr, base_family=f'pr-{PR}')['test_fails'],
            UNKNOWN)
        self.assertEqual(self.dispatched, [])

    def test_switching_family_keys_across_pr_test_failures_still_shares_cap(self):
        # Switching family keys across PR test failures never resets the shared test-failure cap.
        families = ('fam-alpha', 'fam-beta', 'fam-gamma')
        for used, (head, family) in enumerate(zip(HEADS[:self.max], families), 1):
            record = self.store.get(TASK)
            self.assertEqual(task_advance._test_fail_count(record, PR, self.store), used - 1)
            result = self.repair(PR, head, family=family)
            self.assertEqual(result['claim']['family'], f'pr-{PR}-ec2')
            record = self.store.get(TASK)
            self.assertEqual(task_advance._test_fail_count(record, PR, self.store), used)
        record = self.store.get(TASK)
        self.assertEqual(task_advance._test_fail_count(record, PR, self.store), self.max)
        self.assertTrue(task_advance._test_family_used_up(
            record, PR, HEADS[self.max], self.store))
        self.refused('REPAIR_LIMIT_REACHED', PR, HEADS[self.max], family='fam-delta')
        self.refused('REPAIR_LIMIT_REACHED', None, HEADS[self.max])

    def test_prepr_fail_then_switching_family_keys_shares_cap(self):
        # Pre-PR FAIL + PR test failures with distinct family keys share the cap.
        self.repair(None, HEADS[0])
        families = ('fam-one', 'fam-two')
        for used, (head, family) in enumerate(zip(HEADS[1:self.max], families), 2):
            record = self.store.get(TASK)
            self.assertEqual(task_advance._test_fail_count(record, PR, self.store), used - 1)
            result = self.repair(PR, head, family=family)
            self.assertEqual(result['claim']['family'], f'pr-{PR}-ec2')
            record = self.store.get(TASK)
            self.assertEqual(task_advance._test_fail_count(record, PR, self.store), used)
        record = self.store.get(TASK)
        self.assertEqual(task_advance._test_fail_count(record, PR, self.store), self.max)
        self.refused('REPAIR_LIMIT_REACHED', PR, HEADS[self.max], family='fam-three')


class PrePrAdvanceTests(_Base):
    def test_advance_dispatches_one_counted_repair_for_a_prepr_fail(self):
        result, invoked = self.advance(HEADS[0])
        self.assertEqual((result['step'], result['codes']), ('REPAIR_REVIEW', []))
        [call] = invoked
        self.assertIsNone(call['pr_number'])
        self.assertTrue(call['auto'])
        [claim] = self.claims()
        self.assertEqual((claim['pr'], claim['family'], claim['state']),
                         (None, PREPR, 'DISPATCHED'))
        self.assertEqual(len(self.dispatched), 1)
        # While the repair runs, the next advance waits: no second repair.
        self.store.put({'task_id': claim['repair_task_id'],
                        'developer': {'state': 'DEVELOPER_DISPATCHING'}})
        result, invoked = self.advance(HEADS[0])
        self.assertEqual(result['step'], 'WAITING_REPAIR')
        self.assertEqual(invoked, [])
        self.assertEqual(len(self.claims()), 1)

    def test_prepr_fail_then_pr_fail_share_one_limit_through_advance(self):
        result, _ = self.advance(HEADS[0])
        self.assertEqual((result['step'], result['codes']), ('REPAIR_REVIEW', []))
        for head in HEADS[1:self.max]:
            result, invoked = self.advance(head, pr_number=PR)
            self.assertEqual((result['step'], result['codes']), ('REPAIR_REVIEW', []))
            self.assertEqual(invoked[0]['pr_number'], PR)
        self.assertEqual(len(self.claims()), self.max)
        result, invoked = self.advance(HEADS[self.max], pr_number=PR)
        self.assertEqual((result['step'], result['codes']),
                         ('STOPPED', ['TEST_FAIL_LIMIT_REACHED']))
        self.assertEqual(invoked, [])
        self.assertEqual(len(self.claims()), self.max)
        self.assertEqual(len(self.dispatched), self.max)

    def test_prepr_fail_over_the_limit_stops(self):
        used = [_used_claim(PREPR, None, HEADS[n], n) for n in range(1, self.max + 1)]
        self.seed(used)
        result, invoked = self.advance(HEADS[0])
        self.assertEqual((result['step'], result['codes']),
                         ('STOPPED', ['TEST_FAIL_LIMIT_REACHED']))
        self.assertEqual(invoked, [])
        self.assertEqual(self.claims(), used)

    def test_ci_fail_after_the_pr_counts_prepr_repairs(self):
        used = [_used_claim(PREPR, None, HEADS[n], n) for n in range(1, self.max + 1)]
        self.seed(used)
        invoked = []
        routed = task_advance._route_ci_failure(
            store=self.store, record=self.store.get(TASK), task_id=TASK,
            executor_task=EXECUTOR, number=PR,
            d6={'result_head': HEADS[0], 'root': '/registered/d6'},
            related={'repair_claims': []},
            run={'name': 'check (Linux 3.14)', 'id': 7, 'conclusion': 'failure',
                 'head_sha': HEADS[0]},
            fail_code='LINUX_CI_FAILED', github_get=lambda _path: None,
            repair_review_call=lambda **kwargs: invoked.append(kwargs))
        self.assertEqual(routed, ('STOPPED', ['TEST_FAIL_LIMIT_REACHED'], None))
        self.assertEqual(invoked, [])


class PrePrClaimStoreTests(unittest.TestCase):
    """The real TaskStore accepts pr None only for a pre-PR EC2 claim."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        worker_policy_fixture.install(self)
        repository = patch.object(worker_dispatch, 'repository_config',
            return_value={'slug': 'davidcatcat/davidcattoolsproject',
                          'dispatch_issue': 289, 'dispatch_evidence_pr': 283})
        repository.start()
        self.addCleanup(repository.stop)

    def _task(self, store, task_id=TASK):
        request = 'Bounded non-sensitive pre-PR repair fixture.'
        run_id = '1' * 32
        task = task_submission_cli._record(
            task_id, run_id, 'pm-' + '1' * 32, 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible',
            model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
                    response='PM accepted a bounded request.', error_code=None,
                    pm_assignment={'schema_version': 1, 'route': 'claude', 'risk': 'LOW',
                                   'goal': 'Add bounded test.',
                                   'acceptance': ['Fix pre-PR failure.']})
        store.create(task)
        return store.get(task_id)

    def test_only_a_prepr_ec2_claim_may_have_no_pr(self):
        store = TaskStore(Path(self.temp.name).resolve())
        task = self._task(store)
        prepr = dict(_used_claim(PREPR, None, HEADS[0], 1), state='CLAIMED')
        saved = store.compare_and_update(
            TASK, expected={'record_sha256': task['record_sha256']},
            review_repair_claims=[prepr])
        self.assertEqual(saved['review_repair_claims'], [prepr])
        after = dict(_used_claim(f'pr-{PR}-ec2', PR, HEADS[1], 1), state='CLAIMED',
                     repair_task_id='2' * 32)
        saved = store.compare_and_update(
            TASK, expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[prepr, after])
        self.assertEqual(saved['review_repair_claims'], [prepr, after])
        invalid = {
            'PR family': dict(after, pr=None, version=2, attempt=2, head_sha=HEADS[2],
                              repair_task_id='3' * 32),
            'review comment': dict(prepr, comment_id=9, version=2, attempt=2,
                                   head_sha=HEADS[2], repair_task_id='3' * 32),
        }
        for name, claim in invalid.items():
            with self.subTest(case=name):
                with self.assertRaises(LLMToolError) as caught:
                    store.compare_and_update(
                        TASK, expected={'record_sha256': saved['record_sha256']},
                        review_repair_claims=[prepr, after, claim])
                self.assertEqual(caught.exception.task_store_error_code,
                                 'REVIEW_REPAIR_CLAIM_INVALID')
        self.assertEqual(store.get(TASK)['review_repair_claims'], [prepr, after])

    def test_real_delegation_persists_prepr_repair_packet_to_task_store(self):
        store = TaskStore(Path(self.temp.name).resolve())
        self._task(store)
        packet = {
            'task': TASK, 'pr': None, 'executor_task': EXECUTOR,
            'failed_head': HEADS[0], 'version': 0, 'verdict_comment_id': 0,
            'findings_count': 0,
            'reviewer_text_excerpt_untrusted_data': 'EC2 test failed before PR',
        }
        reg = SimpleNamespace(
            task_id=EXECUTOR, trusted_agent_id='claude-developer',
            session_ref='Session', native_session_id=None, root=self.temp.name,
            common_dir=self.temp.name, branch='main', source_sha=HEADS[0])
        snap = SimpleNamespace(head=HEADS[0], root=self.temp.name,
                               common_dir=self.temp.name, branch='main')
        card = '\nREQUIRED_SKILL_BEGIN path=card\nRules.\nREQUIRED_SKILL_END path=card'
        class Boundary:
            def prepare(self, root, paths, state_dirs=()):
                return ('fixture-policy', True)

            def verify(self, root, paths, state_dirs=(), *, runner):
                return {'verified': True, 'policy_sha256': hashlib.sha256(b'boundary').hexdigest()}

        class Route:
            def __call__(self, route, request, *, gate, boundary, snapshot):
                raise RouteError('CLAUDE_CONFIG_INVALID')

        args = Namespace(
            task_id=TASK, provider='claude', mode='direct', selection_source='PM',
            executor_task=EXECUTOR, read_path=['docs/notes.md'], write_path=['docs/notes.md'],
            acceptance=['Fix pre-PR failure.'], egress_basis='Plain test egress basis.',
            owner_ref=None, repair_packet=packet, context_stdin=False, timeout=None)
        with patch.object(task_submission_cli, '_delegate_d6_binding', return_value=(reg, snap)), \
                patch.object(task_submission_cli.task_completion, 'bind_executor_task',
                             return_value=(reg, snap)), \
                patch.object(task_submission_cli, '_delegate_timeout', return_value=600), \
                patch.object(task_submission_cli, 'load_route_config',
                             return_value={'routes': {'claude': {'denied_dirs': []}}}), \
                patch.object(task_submission_cli, '_claude_developer_profile',
                             return_value=('fixture-model', 'medium', '/nonexistent/config')), \
                patch.object(task_submission_cli.shutil, 'which', return_value='/nonexistent/bin/claude'), \
                patch.object(task_submission_cli, '_DiagnosedClaudeRoute',
                             side_effect=lambda config: SimpleNamespace()), \
                patch.object(task_submission_cli, 'for_platform', return_value=Boundary()), \
                patch.object(task_submission_cli, '_developer_card_prefix',
                             return_value=(card, hashlib.sha256(card.encode()).hexdigest())), \
                patch.object(task_submission_cli, '_delegate_chain_gate',
                             side_effect=lambda a, s, b, claim: claim()), \
                patch.object(task_submission_cli, '_check_delegate_repair_cap', return_value=None), \
                patch.object(task_submission_cli.worker_availability, 'quota_refusal', return_value=None), \
                patch.object(task_submission_cli.worker_dispatch, 'evaluate', return_value=ALLOWED), \
                patch('llmtool.pm_caller.check_caller', return_value=None), \
                redirect_stdout(StringIO()):
            rc = task_submission_cli.run_task_claude_delegate(args, store=store, route_runner=Route())
        self.assertEqual(rc, 1)
        persisted = store.get(TASK)
        expected_text = start_failure.canonical(packet)
        self.assertEqual(persisted['repair_packet'], expected_text)
        self.assertEqual(start_failure.decode_repair_packet(persisted['repair_packet']), packet)
        self.assertIsNone(packet['pr'])
        self.assertEqual(persisted['developer']['error_code'], 'CLAUDE_CONFIG_INVALID')


if __name__ == '__main__':
    unittest.main()
