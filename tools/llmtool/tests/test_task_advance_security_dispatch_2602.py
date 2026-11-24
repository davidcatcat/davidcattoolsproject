"""#2602 item n: advance refuses a formal review dispatch on a non-PASS security head.

The formal review dispatch step reuses the merge gate's exact-head security
verdict check (Task claims, REVIEWER completion events and the PR issue
comments). All ids, digests, heads and comments are placeholders; GitHub reads
are injected, and no Reviewer is ever started by the cases that refuse.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import review_dispatch, task_advance


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
TASK = '1' * 32
RUN = '2' * 32
CONTRACT = '3' * 64
CREATED = '2026-11-25T00:00:00Z'
PR = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False}
D6 = {'root': '/repo/task', 'task_id': 'executor-1', 'result_head': HEAD,
      'recorded_result_head': HEAD, 'registered_result_head': HEAD, 'dirty': False}

NOT_PASS = task_advance.REVIEW_SECURITY_NOT_PASS
UNKNOWN = task_advance.REVIEW_SECURITY_UNKNOWN


def _security_comment(status, *, head=HEAD, comment_id=1, version=91):
    """A well-formed security verdict comment at this head."""
    body = (
        f'<!-- activity: type=review_verdict actor=Codex target=PM status={status} -->\n'
        f'Verdict: {status} | Version: v{version} | HEAD: {head}')
    return {'id': comment_id, 'body': body,
            'created_at': CREATED, 'updated_at': CREATED}


def _ruling_comment():
    """A strictly well-formed, unedited dispute ruling comment (not a verdict)."""
    body = '\n'.join((
        '<!-- activity: type=dispute_ruling actor=Codex target=PM status=UPHELD -->',
        f'Ruling: F1 UPHELD | PR: #17 | Version: 5 | HEAD: {HEAD}',
        f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} '
        f'head={HEAD}',
    ))
    return {'id': 500, 'body': body, 'created_at': CREATED, 'updated_at': CREATED}


def _security_claim(verdict, *, head=HEAD, version=1, state='COMPLETED'):
    claim = {'pr': 17, 'head_sha': head, 'version': version,
             'review_kind': 'security', 'state': state}
    if state == 'COMPLETED':
        claim['review_result'] = {'verdict': verdict, 'head': head}
    return claim


def _refused_claim(version=2):
    return {'pr': 17, 'head_sha': HEAD, 'version': version, 'state': 'UNKNOWN',
            'failure_reason': 'OUTPUT_NOT_JSONL', 'reviewer_task': f'reviewer-{version}',
            'native_session_id': None,
            'claimed_at': '2026-11-25T00:00:00Z',
            'updated_at': '2026-11-25T00:00:01Z'}


def _record(claims=(), events=(), developer=None, **changes):
    record = {
        'task_id': TASK, 'run_id': RUN, 'record_sha256': 'r' * 64,
        'task_binding': {'contract_digest': CONTRACT},
        'developer': developer or {'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': 'executor-1', 'state': 'WAITING_REVIEW',
                       'evidence': {'result_head': HEAD}},
        'review_dispatch_claim': list(claims),
        'review_repair_claims': [],
        'completion_events': list(events),
        'remote_test_outcomes': [{
            'result': 'ADMISSION_DENIED', 'code': 'MODULE_NOT_ADMITTED',
            'executor_task': 'executor-1', 'sha': HEAD, 'modules': []}],
    }
    record.update(changes)
    return record


def _github(comments=(), *, issues=None):
    """Fake exact-head GitHub reads; `issues` may be a callable or raise."""
    def get(path):
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return {'total_count': 1, 'check_runs': [{
                'id': 1, 'name': 'check (Linux 3.14)', 'head_sha': HEAD,
                'status': 'completed', 'conclusion': 'success'}]}
        if path == 'pulls/17/files?per_page=100':
            return [{'filename': 'src/module.py'}]
        if path.startswith('issues/17/comments'):
            if issues is not None:
                return issues()
            return list(comments)
        raise AssertionError(f'unexpected read: {path}')
    return get


class _Store:
    def __init__(self, record, root=None):
        self.record = record
        self.root = Path(root) if root is not None else None

    def get(self, _task_id):
        return self.record


class SecurityDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.receipt_dir = Path(self.temp.name).resolve() / 'review-refusals'
        self.receipt_dir.mkdir(mode=0o700)

    def _store(self, record):
        return _Store(record, root=Path(self.temp.name).resolve() / 'tasks')

    def _write_receipt(self, claim):
        path = self.receipt_dir / f"{TASK}-{claim['head_sha']}-{claim['version']}.json"
        path.write_text(json.dumps({
            'task_id': TASK, 'pr': claim['pr'], 'head': claim['head_sha'],
            'version': claim['version'], 'reviewer_task': claim['reviewer_task'],
            'failure_reason': claim['failure_reason'], 'recorded_at': CREATED,
            'not_a_verdict': True,
            'excerpt': 'OUTPUT_SHAPE line=1 of 1 bytes=9 first=letter',
            'contract_digest': CONTRACT}), encoding='utf-8')
        path.chmod(0o600)
        return path

    def _advance(self, record, github_get, *, store=None, reviewer_session_done=False):
        dispatched = []

        def dispatch(**kwargs):
            dispatched.append(kwargs)
            return {'state': 'DISPATCHED'}

        with mock.patch.object(task_advance, '_d6_snapshot', return_value=dict(D6)), \
                mock.patch.object(task_advance, '_pr_snapshot', return_value=dict(PR)), \
                mock.patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=TASK, store=store or _Store(record), pr_number=17,
                github_get=github_get, dispatch_review_call=dispatch,
                reviewer_session_done=reviewer_session_done)
        return result, dispatched

    def assertRefused(self, outcome, code):
        result, dispatched = outcome
        self.assertEqual(result['step'], code, result)
        self.assertEqual(result['codes'], [code], result)
        self.assertEqual(dispatched, [])
        self.assertIn(code, result['next'])

    def assertDispatched(self, outcome):
        result, dispatched = outcome
        self.assertEqual(result['step'], 'DISPATCH_REVIEW', result)
        self.assertEqual(len(dispatched), 1)
        return result

    # A1/A2: the first dispatch refuses on a same-head non-PASS verdict.

    def test_same_head_fail_comment_refuses_first_dispatch(self):
        self.assertRefused(
            self._advance(_record(), _github([_security_comment('FAIL')])), NOT_PASS)

    def test_later_same_head_pass_does_not_clear_an_earlier_fail(self):
        for comments in (
                [_security_comment('FAIL', comment_id=1),
                 _security_comment('PASS', comment_id=2)],
                [_security_comment('PASS', comment_id=2),
                 _security_comment('FAIL', comment_id=1)]):
            with self.subTest(order=[comment['id'] for comment in comments]):
                self.assertRefused(
                    self._advance(_record(), _github(comments)), NOT_PASS)

    def test_same_head_fix_and_blocked_comments_refuse_first_dispatch(self):
        for status in ('FIX', 'BLOCKED'):
            with self.subTest(status=status):
                self.assertRefused(
                    self._advance(_record(), _github([_security_comment(status)])),
                    NOT_PASS)

    def test_same_head_security_reviewer_event_refuses_first_dispatch(self):
        event = {'source': 'REVIEWER', 'source_sha': HEAD,
                 'review_kind': 'security', 'outcome': 'REVIEW_FAIL'}
        self.assertRefused(
            self._advance(_record(events=[event]), _github([])), NOT_PASS)

    def test_refusal_result_names_the_next_step(self):
        result, _dispatched = self._advance(
            _record(), _github([_security_comment('FAIL')]))
        self.assertIn('publish a new head',
                      result['result']['next_step'])
        self.assertIn('PASS', result['result']['next_step'])
        self.assertEqual(result['result']['pr'], 17)
        self.assertEqual(result['result']['head'], HEAD)

    # A1/A2: the redispatch paths refuse before they consume anything.

    def test_unusable_claim_redispatch_refuses_before_dispatch(self):
        claim = _refused_claim(1)
        self._write_receipt(claim)
        # The first absence read cannot prove absence, so the second read chooses
        # a redispatch; the third read is the security verdict at that head.
        reads = iter(([{'id': 9, 'body': f'Verdict: FAIL | HEAD: {HEAD}',
                        'created_at': CREATED, 'updated_at': CREATED}],
                      [], [_security_comment('FAIL')]))

        def issues():
            return next(reads)

        self.assertRefused(
            self._advance(_record(claims=[claim]), _github(issues=issues),
                          store=self._store(_record(claims=[claim]))),
            NOT_PASS)

    def test_completed_security_claim_refuses_the_refusal_rerun(self):
        claim = _refused_claim(version=2)
        self._write_receipt(claim)
        record = _record(claims=[_security_claim('FAIL', version=1), claim])
        self.assertRefused(
            self._advance(record, _github([]), store=self._store(record)), NOT_PASS)

    def test_automatic_refusal_rerun_is_not_recorded_when_the_head_is_not_pass(self):
        claim = _refused_claim(1)
        path = self._write_receipt(claim)
        record = _record(claims=[claim])
        reads = iter(([], [_security_comment('FAIL')]))
        with mock.patch.object(review_dispatch, 'mark_refusal_rerun') as mark:
            result, dispatched = self._advance(
                record, _github(issues=lambda: next(reads)), store=self._store(record))
        self.assertRefused((result, dispatched), NOT_PASS)
        mark.assert_not_called()
        self.assertNotIn('rerun', json.loads(path.read_text(encoding='utf-8')))

    def test_blocked_reviewer_fallback_refuses_before_the_alternate_reviewer(self):
        record = _record(
            claims=[{'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            events=[{'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                     'outcome': 'REVIEW_BLOCKED'}],
            developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'agy',
                       'model': 'gemini-3.8-flash-medium'})
        policy = type('P', (), {'allowed': True})()
        with mock.patch.object(review_dispatch, '_policy', return_value=policy):
            outcome = self._advance(record, _github([_security_comment('FAIL')]))
        self.assertRefused(outcome, NOT_PASS)

    # A4: cases the shared check must not block.

    def test_other_head_fail_does_not_block(self):
        self.assertDispatched(self._advance(
            _record(), _github([_security_comment('FAIL', head=OTHER_HEAD)])))

    def test_earlier_head_fail_plus_current_pass_does_not_block(self):
        self.assertDispatched(self._advance(
            _record(), _github([
                _security_comment('FAIL', head=OTHER_HEAD, comment_id=1),
                _security_comment('PASS', head=HEAD, comment_id=2)])))

    def test_same_head_pass_alone_does_not_block(self):
        self.assertDispatched(self._advance(
            _record(), _github([_security_comment('PASS')])))

    def test_no_security_verdict_does_not_block(self):
        self.assertDispatched(self._advance(_record(), _github([])))

    def test_well_formed_ruling_comment_does_not_block(self):
        self.assertDispatched(self._advance(
            _record(), _github([_ruling_comment()])))

    def test_ruling_claim_at_the_head_does_not_block(self):
        claim = {'purpose': 'dispute_ruling', 'pr': 17, 'head_sha': HEAD,
                 'version': 92, 'state': 'COMPLETED'}
        self.assertDispatched(self._advance(
            _record(claims=[claim]), _github([])))

    def test_security_verdict_on_another_head_does_not_block(self):
        claim = _security_claim('FAIL', head=OTHER_HEAD)
        self.assertDispatched(self._advance(
            _record(claims=[claim]), _github([])))

    # A3: unreadable or malformed evidence refuses as UNKNOWN, never as no verdict.

    def test_unreadable_comment_read_gives_the_unknown_refusal(self):
        def issues():
            raise RuntimeError('comments unavailable')

        self.assertRefused(
            self._advance(_record(), _github(issues=issues)), UNKNOWN)

    def test_malformed_comment_page_gives_the_unknown_refusal(self):
        self.assertRefused(
            self._advance(_record(), _github(issues=lambda: {'message': 'bad'})),
            UNKNOWN)

    def test_malformed_security_verdict_gives_the_unknown_refusal(self):
        malformed = {'id': 1, 'body': f'Verdict: PASS | Version: v91 | HEAD: {HEAD}',
                     'created_at': CREATED, 'updated_at': CREATED}
        self.assertRefused(
            self._advance(_record(), _github([malformed])), UNKNOWN)


if __name__ == '__main__':
    unittest.main()
