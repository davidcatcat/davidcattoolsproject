"""#1831 P2: one redispatch with the other Reviewer after a verdict-less REVIEW_BLOCKED."""
import unittest
from unittest.mock import patch

from llmtool import review_dispatch, task_advance
from llmtool.errors import LLMToolError
from test_task_advance import HEAD, PR, ClaimStore, base_record, d6, green_linux_ci as ci

MODELS = {'agy': 'gemini-3.8-flash-medium', 'claude': 'claude-opus-5-5'}


class BlockedReviewFallbackTests(unittest.TestCase):
    def run_case(self, profile, versions, refuse=False, done=False, paused=()):
        calls = []

        def dispatch(**kwargs):
            calls.append((kwargs['reviewer'], kwargs['version']))
            if refuse:
                err = LLMToolError('refused')
                err.review_dispatch_code = 'REVIEWER_NOT_ALLOWED'
                raise err
            return {'state': 'DISPATCHED'}

        event = {'event_id': 'e' * 32, 'source': 'REVIEWER', 'source_sha': HEAD,
                 'version': versions[-1], 'outcome': 'REVIEW_BLOCKED', 'state': 'PM_CONSUMED'}
        record = base_record(
            developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': profile,
                       'model': MODELS[profile]},
            pm_session_id='pm', completion_events=[event], review_dispatch_claim=[
                {'pr': 17, 'head_sha': HEAD, 'version': v, 'state': 'DISPATCHED',
                 'reviewer': 'codex-reviewer'} for v in versions])

        def policy(_record, key):
            return type('P', (), {'allowed': key not in paused})()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance.active_pm_inbox, 'ack'), \
             patch.object(review_dispatch, '_policy', side_effect=policy):
            out = task_advance.advance(task_id=record['task_id'], store=ClaimStore(record),
                                       pr_number=17, github_get=ci, dispatch_review_call=dispatch,
                                       reviewer_session_done=done)
        return out['step'], out['codes'], calls

    def test_blocked_head_redispatches_once_with_the_other_reviewer(self):
        fb = ['REVIEW_BLOCKED', 'REVIEWER_FALLBACK']
        self.assertEqual(self.run_case('agy', [1]), ('DISPATCH_REVIEW', fb, [('claude-reviewer', 2)]))
        self.assertEqual(self.run_case('agy', [1], refuse=True),
                         ('DISPATCH_REVIEW', fb + ['REVIEWER_NOT_ALLOWED'], [('claude-reviewer', 2)]))
        for args, kw, step in ((('agy', [1, 2]), {}, 'REVIEWER_ATTENTION'),
                               (('claude', [1]), {}, 'REVIEWER_ATTENTION'),
                               (('agy', [1]), {'done': True}, 'REVIEW_NOT_POSTED')):
            with self.subTest(args=args, kw=kw):
                self.assertEqual(self.run_case(*args, **kw)[::2], (step, []))

    def test_blocked_fallback_uses_claim_reviewer_after_its_route_pauses(self):
        # The claim identity says codex-reviewer was dispatched; codex then
        # became PAUSED. The one redispatch must reach claude-reviewer.
        self.assertEqual(
            self.run_case('agy', [1], paused=('codex-reviewer',)),
            ('DISPATCH_REVIEW', ['REVIEW_BLOCKED', 'REVIEWER_FALLBACK'],
             [('claude-reviewer', 2)]))

    def test_refused_fallback_uses_attempted_reviewer_after_its_route_pauses(self):
        calls = []
        paused = set()

        def dispatch(**kwargs):
            calls.append(kwargs.get('reviewer'))
            if kwargs.get('reviewer') is None:
                # codex-reviewer was attempted while ALLOWED, then paused.
                paused.add('codex-reviewer')
                err = LLMToolError('refused')
                err.review_dispatch_code = 'REVIEWER_NOT_ALLOWED'
                err.review_dispatch_reviewer = 'codex-reviewer'
                raise err
            return {'state': 'DISPATCHED'}

        record = base_record(
            developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'agy',
                       'model': MODELS['agy']}, pm_session_id='pm')

        def policy(_record, key):
            return type('P', (), {'allowed': key not in paused})()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance.active_pm_inbox, 'ack'), \
             patch.object(review_dispatch, '_policy', side_effect=policy):
            out = task_advance.advance(task_id=record['task_id'], store=ClaimStore(record),
                                       pr_number=17, github_get=ci,
                                       dispatch_review_call=dispatch)
        self.assertEqual(out['step'], 'DISPATCH_REVIEW')
        self.assertEqual(out['codes'], ['REVIEWER_NOT_ALLOWED', 'REVIEWER_FALLBACK'])
        self.assertEqual(calls, [None, 'claude-reviewer'])


if __name__ == '__main__':
    unittest.main()
