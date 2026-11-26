"""#289: optional formal Reviewer order in policy.json and its selection rules."""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import repair_policy as rp
from llmtool import review_dispatch, task_advance
from llmtool.errors import LLMToolError

HEAD = 'a' * 40
ORDER = ['codex-reviewer', 'claude-reviewer']
GOOD = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 3,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}


def _task(profile, model=None):
    developer = {'provider_profile': profile}
    if model is not None:
        developer['model'] = model
    return {'task_id': '1' * 32, 'developer': developer}


def _refusal(code='REVIEWER_NOT_ALLOWED'):
    error = LLMToolError('refused')
    error.review_dispatch_code = code
    return error


class PolicyFileOrderTests(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    def test_repository_policy_sets_codex_then_claude(self):
        self.assertEqual(rp.load_policy()['formal_reviewer_order'],
                         ['codex-reviewer', 'claude-reviewer'])
        self.assertEqual(rp.formal_reviewer_order(), ORDER)

    def test_valid_order_loads_and_absent_key_returns_none(self):
        for order in (['codex-reviewer'], ['claude-reviewer', 'codex-reviewer']):
            with self.subTest(order=order):
                loaded = rp.load_policy(self._write(
                    dict(GOOD, formal_reviewer_order=order)))
                self.assertEqual(loaded['formal_reviewer_order'], order)
        self.assertIsNone(rp.formal_reviewer_order(self._write(GOOD)))

    def test_invalid_order_refuses_policy_invalid(self):
        bad = ([], ['claude-reviewer', 'claude-reviewer'], ['other-reviewer'],
               ['Codex-Reviewer'], [1], 'codex-reviewer', None, {'reviewer': 1})
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(LLMToolError) as caught:
                    rp.load_policy(self._write(
                        dict(GOOD, formal_reviewer_order=value)))
                self.assertEqual(str(caught.exception), 'POLICY_INVALID')


class ReviewerSelectionOrderTests(unittest.TestCase):
    def _select(self, task, requested=None, allowed=None, order=ORDER):
        def policy(_task, reviewer_key):
            if allowed is None:
                return SimpleNamespace(allowed=True, reason_code='ALLOWED')
            return SimpleNamespace(allowed=allowed.get(reviewer_key, False),
                                   reason_code='PAUSED')
        with patch.object(rp, 'formal_reviewer_order', return_value=order), \
                patch.object(review_dispatch, '_policy', side_effect=policy):
            return review_dispatch._select_reviewer(task, requested)

    def test_non_claude_author_gets_codex_first(self):
        self.assertEqual(self._select(_task('agy', 'gemini-2.5-pro')),
                         'codex-reviewer')
        self.assertEqual(self._select(_task('opencode')), 'codex-reviewer')
        self.assertEqual(self._select(_task('gw-agent')), 'codex-reviewer')

    def test_first_allowed_cross_family_key_wins(self):
        task = _task('agy', 'gemini-2.5-pro')
        self.assertEqual(self._select(
            task, allowed={'codex-reviewer': False, 'claude-reviewer': True}),
            'claude-reviewer')

    def test_none_qualifies_keeps_the_existing_refusal(self):
        with self.assertRaises(LLMToolError) as caught:
            self._select(_task('agy', 'gemini-2.5-pro'),
                         allowed={'codex-reviewer': False, 'claude-reviewer': False})
        self.assertEqual(caught.exception.review_dispatch_code,
                         'REVIEWER_NOT_ALLOWED')

    def test_claude_author_skips_the_same_family_key(self):
        self.assertEqual(self._select(_task('claude')), 'codex-reviewer')

    def test_explicit_reviewer_is_checked_as_before(self):
        task = _task('agy', 'gemini-2.5-pro')
        self.assertEqual(self._select(task, 'claude-reviewer'), 'claude-reviewer')
        with self.assertRaises(LLMToolError) as caught:
            self._select(task, 'other-reviewer')
        self.assertEqual(caught.exception.review_dispatch_code, 'REVIEWER_INVALID')

    def test_absent_key_keeps_the_legacy_default(self):
        self.assertEqual(self._select(_task('agy', 'gemini-2.5-pro'), order=None),
                         'claude-reviewer')
        self.assertEqual(self._select(_task('claude'), order=None),
                         'codex-reviewer')


class AdvanceReviewerFallbackOrderTests(unittest.TestCase):
    def test_paused_attempted_route_falls_back_to_the_other_order_key(self):
        task = _task('agy', 'gemini-2.5-pro')

        def select(record, reviewer=None):
            if reviewer == 'codex-reviewer':
                raise _refusal()
            return reviewer
        with patch.object(rp, 'formal_reviewer_order', return_value=ORDER), \
                patch.object(review_dispatch, '_select_reviewer', side_effect=select):
            alternate, code = task_advance._reviewer_fallback(
                task, _refusal(), 'codex-reviewer')
        self.assertEqual((alternate, code), ('claude-reviewer', 'REVIEWER_NOT_ALLOWED'))

    def test_blocked_review_excludes_the_attempted_reviewer(self):
        task = dict(_task('agy', 'gemini-2.5-pro'),
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                                            'version': 1, 'state': 'DISPATCHED'}])

        def select(record, reviewer=None):
            if reviewer == 'codex-reviewer':
                raise _refusal()
            return reviewer
        with patch.object(rp, 'formal_reviewer_order', return_value=ORDER), \
                patch.object(task_advance, '_review_claim_reviewer',
                             return_value='codex-reviewer'), \
                patch.object(review_dispatch, '_select_reviewer', side_effect=select):
            self.assertEqual(task_advance._blocked_fallback_reviewer(task, HEAD),
                             'claude-reviewer')

    def test_blocked_review_assumes_the_order_default_when_unproven(self):
        task = dict(_task('agy', 'gemini-2.5-pro'),
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                                            'version': 1, 'state': 'DISPATCHED'}])
        with patch.object(rp, 'formal_reviewer_order', return_value=ORDER), \
                patch.object(task_advance, '_review_claim_reviewer', return_value=None), \
                patch.object(review_dispatch, '_select_reviewer',
                             side_effect=lambda record, reviewer=None: reviewer):
            self.assertEqual(task_advance._blocked_fallback_reviewer(task, HEAD),
                             'claude-reviewer')

    def test_absent_order_keeps_the_legacy_alternate(self):
        task = _task('agy', 'gemini-2.5-pro')
        with patch.object(rp, 'formal_reviewer_order', return_value=None), \
                patch.object(review_dispatch, '_select_reviewer',
                             side_effect=lambda record, reviewer=None: reviewer):
            self.assertEqual(task_advance._reviewer_fallback(
                task, _refusal(), 'claude-reviewer'),
                ('codex-reviewer', 'REVIEWER_NOT_ALLOWED'))


if __name__ == '__main__':
    unittest.main()
