"""#2087 slice A2-i: ruling claims never reach the repost path or the advance claim choice.

Covers three post-merge security findings (PR #2662 L1 and L2, PR #2656 L1):

* A2-L1: ``review_transcript.truncated_fail_repost`` refuses a ruling claim first.
* A2-L2: ``task_advance`` chooses the review claim of a PR head from formal
  claims only, so a ruling claim at a higher version changes nothing.
* #2656 L1: the version-used refusal of review dispatch counts every ruling
  claim on the PR, whatever its executor_task.

A ruling claim is a dict with a ``purpose`` key, whatever its value. Placeholder
ids and digests only; no runtime, GitHub, transcript or account is started
(every such function is injected or patched).
"""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import (review_dispatch, review_result, review_transcript, task_advance,
                     task_store, task_submission_cli)
from llmtool.errors import LLMToolError

import review_trust_fixture
import test_task_advance as ta
from test_dispute_ruling_claim_readers import (ARBITER_REVIEWER, CLAUDE_UUID, EXECUTOR, HEAD,
                                               PR, TASK, _arbiter_claim, _base_task_record,
                                               _dispute_entry, _formal_claim)

OTHER_EXECUTOR = 'other-d6'
RULING_LABELS = ('COMPLETED_RULING', 'COMPLETED_WITHHELD', 'UNKNOWN', 'CLAIMED', 'DISPATCHED')


def setUpModule():
    review_trust_fixture.start_module()
    ta.setUpModule()


def tearDownModule():
    ta.tearDownModule()
    review_trust_fixture.stop_module()


def _arbiter_variants(digest, **changes):
    claims = (
        ('COMPLETED_RULING', _arbiter_claim(digest, state='COMPLETED', verdict='RULING')),
        ('COMPLETED_WITHHELD', _arbiter_claim(digest, state='COMPLETED', verdict='BLOCKED')),
        ('UNKNOWN', _arbiter_claim(digest, state='UNKNOWN')),
        ('CLAIMED', _arbiter_claim(digest, state='CLAIMED')),
        ('DISPATCHED', _arbiter_claim(digest, state='DISPATCHED', reviewer='claude-reviewer')),
    )
    return tuple((label, dict(claim, **changes)) for label, claim in claims)


def _new_store(test):
    temp = tempfile.TemporaryDirectory()
    test.addCleanup(temp.cleanup)
    return task_store.TaskStore(Path(temp.name).resolve() / 'store')


def _store_with(test, claims):
    """A real TaskStore holding the formal claims, then each ruling claim (CLAIMED first)."""
    store = _new_store(test)
    base, digest = _base_task_record()
    formal = [c for c in claims if 'purpose' not in c]
    rulings = [c for c in claims if 'purpose' in c]
    if formal:
        base['review_dispatch_claim'] = formal
    record = store.create(base)
    if rulings:
        record = store.compare_and_update(
            TASK, expected={'record_sha256': record['record_sha256']},
            review_disputes=[_dispute_entry()])
    for ruling in rulings:
        claimed = {key: value for key, value in ruling.items() if key != 'review_result'}
        claimed['state'] = 'CLAIMED'
        claimed['native_session_id'] = None
        current = list(record.get('review_dispatch_claim') or [])
        record = store.compare_and_update(
            TASK, expected={'record_sha256': record['record_sha256']},
            review_dispatch_claim=current + [claimed])
        if ruling['state'] != 'CLAIMED':
            record = store.compare_and_update(
                TASK, expected={'record_sha256': record['record_sha256']},
                review_dispatch_claim=current + [ruling])
    return store, store.get(TASK), digest


def _formal_in_state(digest, state, version=1):
    claim = _formal_claim(digest, version=version)
    if state != 'COMPLETED':
        claim = {key: value for key, value in claim.items() if key != 'review_result'}
        claim['state'] = state
    return claim


class RepostRefusesRulingClaimTest(unittest.TestCase):
    """A2-L1: truncated_fail_repost refuses a ruling claim before any read."""

    def _repost_with_guards(self, claim, task, *, config_dir):
        """Run the repost with every read function failing the test if it is reached."""
        with ExitStack() as stack:
            for name in ('verdict_blocks', '_claim_config_dir', 'select_verdict'):
                stack.enter_context(patch.object(
                    review_transcript, name, side_effect=AssertionError(f'{name} was called')))
            stack.enter_context(patch.object(
                review_result, '_comment_verdict', side_effect=AssertionError('comment read')))
            stack.enter_context(patch.object(
                review_transcript.role_config, 'resolve_role',
                side_effect=AssertionError('role read')))
            return review_transcript.truncated_fail_repost(
                claim, task, [], config_dir=config_dir)

    def test_every_ruling_claim_is_refused_with_nothing_to_post(self):
        _, digest = _base_task_record()
        task = {'task_id': TASK, 'run_id': 'b' * 32,
                'task_binding': {'contract_digest': digest}}
        variants = list(_arbiter_variants(digest))
        # A claude-shaped claim that would otherwise pass the session and reviewer checks.
        claude_shaped = dict(
            _arbiter_claim(digest, state='DISPATCHED', reviewer='claude-reviewer'),
            native_session_id=CLAUDE_UUID, reviewer_task=ARBITER_REVIEWER)
        variants.append(('CLAUDE_SHAPED', claude_shaped))
        # Any value of purpose counts, as in every other reader.
        variants.append(('OTHER_PURPOSE', dict(claude_shaped, purpose='other')))
        for label, claim in variants:
            for config_dir in ('/placeholder-config', None):
                with self.subTest(claim=label, config_dir=config_dir):
                    self.assertEqual(
                        self._repost_with_guards(claim, task, config_dir=config_dir),
                        ('REPOST_NOT_CLAUDE_REVIEWER', None, None))

    def test_formal_claim_takes_the_same_path_as_before(self):
        formal = {'native_session_id': CLAUDE_UUID, 'reviewer_task': ARBITER_REVIEWER,
                  'head_sha': HEAD, 'version': 1, 'executor_task': EXECUTOR, 'pr': PR}
        with patch.object(review_transcript, 'verdict_blocks', return_value=None) as blocks:
            result = review_transcript.truncated_fail_repost(
                formal, {'task_id': TASK}, [], config_dir='/placeholder-config')
        self.assertEqual(result, ('REVIEWER_TRANSCRIPT_UNAVAILABLE', None, None))
        blocks.assert_called_once_with(CLAUDE_UUID, '/placeholder-config', ARBITER_REVIEWER)
        # No claude session: the pre-existing refusal, unchanged.
        self.assertEqual(review_transcript.truncated_fail_repost(
            dict(formal, native_session_id=None), {'task_id': TASK}, [],
            config_dir='/placeholder-config'), ('REPOST_NOT_CLAUDE_REVIEWER', None, None))


class RepostCliPostsNothingForRulingClaimTest(unittest.TestCase):
    """A2-L1 through the PM command: nothing is posted at the PM-given version."""

    def _run(self, claims, version):
        record = {'task_id': TASK, 'run_id': 'b' * 32, 'review_dispatch_claim': claims}
        store = SimpleNamespace(get=lambda _task_id: record)
        posted = []
        args = SimpleNamespace(task_id=TASK, executor_task=EXECUTOR, pr=PR, version=version,
                               repost_truncated_fail=True)

        def gh(_root, command):
            posted.append(command)
            raise AssertionError('gh was called')

        with patch.object(review_result, 'record_review_result',
                          side_effect=AssertionError('recorded')), \
                patch.object(task_submission_cli, '_print') as printed:
            code = task_submission_cli.run_task_review_result(
                args, store=store, github_get=lambda _url: [], gh_runner=gh)
        return code, posted, printed.call_args[0][0]

    def test_ruling_claim_at_the_given_version_posts_nothing(self):
        _, digest = _base_task_record()
        for label, ruling in _arbiter_variants(digest, version=2):
            with self.subTest(claim=label):
                code, posted, out = self._run([_formal_claim(digest, version=1), ruling], 2)
                self.assertEqual((code, posted, out['state'], out['code']),
                                 (1, [], 'REFUSED', 'REPOST_NOT_CLAUDE_REVIEWER'))


class DecideSkipsRulingClaimsTest(unittest.TestCase):
    """A2-L2: decide() gives the result of the formal claim alone."""

    D6 = {'result_head': HEAD, 'recorded_result_head': HEAD, 'dirty': False}
    PR_SNAPSHOT = {'number': PR, 'head': {'sha': HEAD}}

    def _event(self, outcome, version=1):
        return {'source': 'REVIEWER', 'source_sha': HEAD, 'version': version,
                'outcome': outcome}

    def _decide(self, record, events):
        record = dict(record, developer={'state': 'DEVELOPER_RESPONDED'})
        return task_advance.decide(record, self.D6, self.PR_SNAPSHOT,
                                   {'events': events, 'repair_claims': []})

    def test_formal_claim_at_n_and_ruling_claim_at_n_plus_1(self):
        _, digest = _base_task_record()
        cases = (
            ('COMPLETED', None, 'RECORD_REVIEW_RESULT'),
            ('DISPATCHED', None, 'RECORD_REVIEW_RESULT'),
            ('UNKNOWN', None, 'REVIEW_OUTCOME_UNKNOWN'),
            ('DISPATCHED', 'REVIEW_BLOCKED', 'REVIEWER_ATTENTION'),
            ('DISPATCHED', 'REVIEW_PASS', 'RUN_TASK_COMPLETE'),
            ('DISPATCHED', 'REVIEW_FAIL', 'REPAIR_REVIEW'),
        )
        for state, outcome, expected in cases:
            events = [] if outcome is None else [self._event(outcome)]
            formal = _formal_in_state(digest, state)
            _, base_task, _ = _store_with(self, [formal])
            baseline = self._decide(base_task, events)
            self.assertEqual(baseline, expected, (state, outcome))
            for label, ruling in _arbiter_variants(digest):
                with self.subTest(formal=state, outcome=outcome, ruling=label):
                    _, task, _ = _store_with(self, [formal, ruling])
                    self.assertEqual(self._decide(task, events), baseline)
                    # The ruling claim listed first changes nothing either.
                    claims = list(task['review_dispatch_claim'])
                    flipped = dict(task, review_dispatch_claim=list(reversed(claims)))
                    self.assertEqual(self._decide(flipped, events), baseline)

    def test_ruling_claim_with_any_purpose_value_is_left_out(self):
        _, digest = _base_task_record()
        formal = _formal_in_state(digest, 'DISPATCHED')
        record = {'review_dispatch_claim': [formal]}
        baseline = self._decide(record, [])
        other = dict(_arbiter_claim(digest, state='UNKNOWN'), purpose='other')
        self.assertEqual(self._decide({'review_dispatch_claim': [formal, other]}, []), baseline)

    def test_ruling_claim_alone_is_no_review_claim(self):
        _, digest = _base_task_record()
        ruling = _arbiter_claim(digest, state='UNKNOWN')
        none = self._decide({'review_dispatch_claim': []}, [])
        self.assertEqual(self._decide({'review_dispatch_claim': [ruling]}, []), none)

    def test_stored_refusal_redispatch_ignores_ruling_claims(self):
        _, digest = _base_task_record()
        reason = sorted(review_dispatch.REVIEW_FAILURE_REASONS)[0]
        refused = dict(_formal_in_state(digest, 'UNKNOWN'), failure_reason=reason)
        ruling = _arbiter_claim(digest, state='UNKNOWN')
        def record(claims):
            return dict(ta.base_record(), review_dispatch_claim=claims)

        args = (ta.d6(), ta.PR, {'events': [], 'repair_claims': []})
        # docs_only skips the EC2 gate; the head is otherwise as decide() sees it.
        refused = dict(refused, pr=17, head_sha=ta.HEAD)
        ruling = dict(ruling, pr=17, head_sha=ta.HEAD)
        baseline = task_advance.decide(record([refused]), *args, docs_only=True)
        self.assertEqual(baseline, 'REVIEW_REDISPATCH')
        self.assertEqual(task_advance.decide(record([refused, ruling]), *args, docs_only=True),
                         baseline)
        # Two formal claims on the head still stop, ruling claims or not.
        older = dict(refused, version=0)
        self.assertEqual(task_advance.decide(record([older, refused]), *args, docs_only=True),
                         'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(
            task_advance.decide(record([older, refused, ruling]), *args, docs_only=True),
            'REVIEW_UNUSABLE_STOPPED')


class ClaimChoiceHelpersSkipRulingClaimsTest(unittest.TestCase):
    """A2-L2: the claim-choosing helpers of task_advance."""

    def _claims(self, digest):
        formal = dict(_formal_in_state(digest, 'DISPATCHED'), pr=17, head_sha=ta.HEAD)
        return formal, [dict(claim, pr=17, head_sha=ta.HEAD, version=2)
                        for _, claim in _arbiter_variants(digest)]

    def test_head_review_claim_returns_the_formal_claim(self):
        _, digest = _base_task_record()
        formal, rulings = self._claims(digest)
        self.assertEqual(task_advance._head_review_claim(
            {'review_dispatch_claim': [formal]}, 17, ta.HEAD), formal)
        for ruling in rulings:
            for claims in ([formal, ruling], [ruling, formal]):
                self.assertEqual(task_advance._head_review_claim(
                    {'review_dispatch_claim': claims}, 17, ta.HEAD), formal)
        self.assertIsNone(task_advance._head_review_claim(
            {'review_dispatch_claim': [rulings[0]]}, 17, ta.HEAD))
        self.assertIsNone(task_advance._head_review_claim({}, 17, ta.HEAD))

    def test_pr_number_comes_from_a_formal_claim(self):
        _, digest = _base_task_record()
        formal, rulings = self._claims(digest)
        self.assertEqual(task_advance._pr_number({'review_dispatch_claim': [formal]}, None), 17)
        for ruling in rulings:
            record = {'review_dispatch_claim': [formal, dict(ruling, pr=23)]}
            self.assertEqual(task_advance._pr_number(record, None), 17)
        self.assertIsNone(task_advance._pr_number(
            {'review_dispatch_claim': [dict(rulings[0], pr=23)]}, None))

    def test_blocked_fallback_counts_formal_claims_only(self):
        _, digest = _base_task_record()
        formal, rulings = self._claims(digest)
        with patch.object(task_advance.task_completion, '_reviewer_author_family',
                          return_value='codex'), \
                patch.object(review_dispatch, '_select_reviewer', return_value=object()):
            baseline = task_advance._blocked_fallback_reviewer(
                {'developer': {}, 'review_dispatch_claim': [formal]}, ta.HEAD)
            self.assertEqual(baseline, 'codex-reviewer')
            for ruling in rulings:
                self.assertEqual(task_advance._blocked_fallback_reviewer(
                    {'developer': {}, 'review_dispatch_claim': [formal, ruling]}, ta.HEAD),
                    baseline)
            # A second formal claim on the head still ends the fallback.
            self.assertIsNone(task_advance._blocked_fallback_reviewer(
                {'developer': {}, 'review_dispatch_claim': [
                    formal, dict(formal, version=2)]}, ta.HEAD))

    def test_rerun_live_check_ignores_ruling_claims(self):
        _, digest = _base_task_record()
        reason = sorted(review_dispatch.REVIEW_FAILURE_REASONS)[0]
        refused = dict(_formal_in_state(digest, 'UNKNOWN'), pr=17, head_sha=ta.HEAD,
                       failure_reason=reason, native_session_id=None)
        d6 = ta.d6()
        pr = {'number': 17, 'head': {'sha': ta.HEAD}}

        def rerun(claims):
            record = {'task_id': TASK, 'review_dispatch_claim': claims,
                      'task_binding': {'contract_digest': digest}}
            with patch.object(review_dispatch, 'read_refusal_receipt',
                              return_value={'contract_digest': digest}):
                return task_advance._review_refusal_rerun(
                    record, refused, d6, pr, object(), lambda _path: [])

        self.assertEqual(rerun([refused]), (2, None))
        for label, ruling in _arbiter_variants(digest):
            with self.subTest(ruling=label):
                claim = dict(ruling, pr=17, head_sha=ta.HEAD, version=2)
                # A ruling claim is never a live Reviewer attempt for the refused
                # review, so the one re-run stays allowed. The next version still
                # counts the version the ruling claim holds, so it cannot collide.
                self.assertEqual(rerun([refused, claim]), (3, None))


class AdvanceStepsChooseFormalClaimTest(unittest.TestCase):
    """A2-L2: RECORD_REVIEW_RESULT, RUN_TASK_COMPLETE and REVIEWER_ATTENTION steps."""

    def _claims(self):
        _, digest = _base_task_record()
        formal = {'executor_task': 'executor-1', 'pr': 17, 'head_sha': ta.HEAD, 'version': 1,
                  'state': 'DISPATCHED', 'reviewer_task': 'reviewer-1',
                  'native_session_id': 'session-1'}
        rulings = [dict(claim, executor_task='executor-1', pr=17, head_sha=ta.HEAD, version=2,
                        reviewer_task='reviewer-2')
                   for _, claim in _arbiter_variants(digest)]
        return formal, rulings

    def _advance(self, claims, outcomes, **kwargs):
        record = ta.base_record(review_dispatch_claim=claims)
        store = type('Store', (), {'get': lambda self, _id: record})()
        with patch.object(task_advance, '_d6_snapshot', return_value=ta.d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=ta.PR), \
                patch.object(task_advance, '_review_outcomes', return_value=outcomes):
            return task_advance.advance(task_id=record['task_id'], store=store, pr_number=17,
                                        github_get=lambda _url: [], **kwargs)

    def test_record_review_result_uses_formal_version(self):
        formal, rulings = self._claims()
        for label, claims in [('BASELINE', [formal])] + [
                (name, [formal, ruling]) for name, ruling in zip(RULING_LABELS, rulings)]:
            with self.subTest(ruling=label):
                calls = []

                def record_result(**kwargs):
                    calls.append(kwargs)
                    return {'state': 'REVIEW_PASS'}

                result = self._advance(claims, [], record_review_result_call=record_result)
                self.assertEqual((result['step'], result['codes']), ('RECORD_REVIEW_RESULT', []))
                self.assertEqual([call['version'] for call in calls], [1])

    def test_run_task_complete_uses_formal_reviewer_task(self):
        formal, rulings = self._claims()
        outcomes = [{'source': 'REVIEWER', 'source_sha': ta.HEAD, 'version': 1,
                     'outcome': 'REVIEW_PASS'}]
        for label, claims in [('BASELINE', [formal])] + [
                (name, [formal, ruling]) for name, ruling in zip(RULING_LABELS, rulings)]:
            with self.subTest(ruling=label):
                seen = []

                def complete(args, store=None):
                    seen.append(args.reviewer_task)
                    print('{"task_id":"done"}')
                    return 0

                result = self._advance(claims, outcomes, run_task_complete_call=complete)
                self.assertEqual((result['step'], result['result']),
                                 ('RUN_TASK_COMPLETE', {'task_id': 'done'}))
                self.assertEqual(seen, ['reviewer-1'])

    def test_reviewer_attention_uses_formal_claim(self):
        formal, rulings = self._claims()
        outcomes = [{'source': 'REVIEWER', 'source_sha': ta.HEAD, 'version': 1,
                     'outcome': 'REVIEW_BLOCKED'}]
        for label, claims in [('BASELINE', [formal])] + [
                (name, [formal, ruling]) for name, ruling in zip(RULING_LABELS, rulings)]:
            with self.subTest(ruling=label):
                result = self._advance(claims, outcomes, reviewer_session_done=True)
                self.assertEqual((result['step'], result['codes']),
                                 ('REVIEW_NOT_POSTED', ['REVIEW_VERDICT_NOT_POSTED']))
                self.assertEqual(result['result'], {
                    'reviewer_task': 'reviewer-1', 'reviewer_session_id': 'session-1',
                    'pr': 17, 'head_sha': ta.HEAD, 'version': 1})

    def test_review_outcome_unknown_names_the_formal_claim(self):
        formal, rulings = self._claims()
        unknown = dict(formal, state='UNKNOWN')
        for label, claims in [('BASELINE', [unknown])] + [
                (name, [unknown, ruling]) for name, ruling in zip(RULING_LABELS, rulings)]:
            with self.subTest(ruling=label):
                result = self._advance(claims, [])
                self.assertEqual((result['step'], result['codes']),
                                 ('REVIEW_OUTCOME_UNKNOWN', ['REVIEW_OUTCOME_UNKNOWN']))


class VersionUsedRefusalCountsRulingClaimsTest(unittest.TestCase):
    """#2656 L1: the suggested next version never names a version a ruling claim holds."""

    def _refuse(self, claims, *, kind='formal', version=1):
        with self.assertRaises(LLMToolError) as caught:
            review_dispatch._refuse_version_used(
                claims, executor_task=EXECUTOR, pr_number=PR, version=version, kind=kind,
                message='Review version 1 is already used; use version {next_version}.')
        return caught.exception.review_dispatch_code, str(caught.exception)

    def _claim(self, **changes):
        claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
                 'state': 'COMPLETED'}
        claim.update(changes)
        return claim

    def test_ruling_claim_of_another_executor_is_counted(self):
        formal = self._claim()
        baseline = self._refuse([formal])
        self.assertEqual(baseline, ('REVIEW_DISPATCH_VERSION_USED',
                                    'Review version 1 is already used; use version 2.'))
        for purpose in ('dispute_ruling', 'other'):
            ruling = self._claim(executor_task=OTHER_EXECUTOR, version=2, purpose=purpose)
            self.assertEqual(self._refuse([formal, ruling])[1],
                             'Review version 1 is already used; use version 3.')
            # A ruling claim of the same executor is counted exactly as before.
            same = self._claim(version=2, purpose=purpose)
            self.assertEqual(self._refuse([formal, same])[1],
                             'Review version 1 is already used; use version 3.')

    def test_ruling_claim_of_another_pr_is_not_counted(self):
        ruling = self._claim(executor_task=OTHER_EXECUTOR, version=7, pr=PR + 1,
                             purpose='dispute_ruling')
        self.assertEqual(self._refuse([self._claim(), ruling])[1],
                         'Review version 1 is already used; use version 2.')

    def test_formal_and_security_claims_of_other_executors_count_as_before(self):
        formal = self._claim()
        other_formal = self._claim(executor_task=OTHER_EXECUTOR, version=5)
        other_security = self._claim(executor_task=OTHER_EXECUTOR, version=92,
                                     review_kind='security')
        own_security = self._claim(version=91, review_kind='security')
        self.assertEqual(self._refuse([formal, other_formal, other_security, own_security])[1],
                         'Review version 1 is already used; use version 2.')
        # Without any claim of the kind, the first version of the range is suggested.
        self.assertEqual(self._refuse([own_security], kind='formal')[1],
                         'Review version 1 is already used; use version 1.')
        self.assertEqual(self._refuse([formal, other_security], kind='security')[1],
                         'Review version 1 is already used; use version 91.')
        self.assertEqual(self._refuse([formal, own_security], kind='security')[1],
                         'Review version 1 is already used; use version 92.')

    def test_range_refusal_still_applies_with_a_ruling_claim(self):
        ruling = self._claim(executor_task=OTHER_EXECUTOR, version=90, purpose='dispute_ruling')
        self.assertEqual(self._refuse([self._claim(), ruling])[0], 'REVIEW_VERSION_RANGE_INVALID')

    def test_dispatch_review_suggests_a_version_no_ruling_claim_holds(self):
        _, digest = _base_task_record()
        formal = _formal_claim(digest, version=1)
        executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git')
        gate = SimpleNamespace(allowed=True, reason_code='ALLOWED')

        def dispatch(store, task, version, head=HEAD):
            posters = []
            with patch.object(review_dispatch, '_preflight',
                              return_value=(task, executor, head, 'd' * 40, 'codex-reviewer')), \
                    patch.object(review_dispatch.worker_dispatch, 'evaluate',
                                 return_value=gate), \
                    patch.object(review_dispatch, '_resolve_github_login',
                                 return_value='reviewer-user'), \
                    self.assertRaises(LLMToolError) as caught:
                review_dispatch.dispatch_review(
                    store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                    version=version, comment_lister=lambda _pr: [],
                    comment_poster=lambda *args: posters.append(args))
            self.assertEqual(posters, [])
            return caught.exception.review_dispatch_code, str(caught.exception)

        # Same version, another head: refused, next version is max(formal) + 1.
        base_store, base_task, _ = _store_with(self, [formal])
        self.assertEqual(dispatch(base_store, base_task, 1, head='f' * 40), (
            'REVIEW_DISPATCH_VERSION_USED',
            f'Review version 1 is already used for PR {PR} on another head; use version 2.'))
        for label, ruling in _arbiter_variants(digest, executor_task=OTHER_EXECUTOR):
            with self.subTest(ruling=label):
                store, task, _ = _store_with(self, [formal, ruling])
                # The ruling claim holds version 2 on this PR: never suggest 2.
                self.assertEqual(dispatch(store, task, 1, head='f' * 40), (
                    'REVIEW_DISPATCH_VERSION_USED',
                    f'Review version 1 is already used for PR {PR} on another head; '
                    'use version 3.'))
                # Dispatching the ruling claim's own version names the next free one.
                self.assertEqual(dispatch(store, task, 2), (
                    'REVIEW_DISPATCH_VERSION_USED',
                    f'Review version 2 is already used for PR {PR} by a ruling claim; '
                    'use version 3.'))


if __name__ == '__main__':
    unittest.main()
