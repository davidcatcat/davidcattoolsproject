"""#2780: version-aware verdict post conflict and duplicate detection."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import review_dispatch  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

import review_trust_fixture  # noqa: E402


TASK = 'a' * 32
RUN = 'b' * 32
HEAD = 'c' * 40
CONTRACT = 'd' * 64
LOGIN = 'reviewer-user'
PR = 42


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _binding_line(*, head=HEAD):
    return f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={head}'


def _verdict_text(*, version=1, verdict='PASS', head=HEAD, extra='Review body.'):
    marker_status = 'PASS' if verdict == 'PASS' else 'FIX'
    displayed = 'PASS' if verdict == 'PASS' else 'FAIL'
    lines = [
        f'<!-- activity: type=review_verdict actor=Codex target=PM status={marker_status} -->',
        f'Verdict: {displayed} | Version: v{version} | HEAD: {head}',
        _binding_line(head=head),
    ]
    if verdict == 'PASS':
        lines.append('FORMAL SECURITY/FUNCTIONAL/QA ACCEPT')
    if extra:
        lines.append(extra)
    return '\n'.join(lines)


class VerdictPostVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.store = SimpleNamespace(root=root / 'store')
        self.task = {
            'task_id': TASK,
            'run_id': RUN,
            'task_binding': {
                'task_id': TASK,
                'run_id': RUN,
                'contract_digest': CONTRACT,
            },
        }
        self.post_dir = root / 'posts'
        patcher = patch.object(
            review_dispatch, '_verdict_post_dir',
            lambda _store: review_dispatch._private_post_dir(self.post_dir))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = 1000.0
        self.comments = []
        self.posts = []

    def _claim(self, *, version=2, reviewer_task='reviewer-v2', text=None):
        verdict_text = text if text is not None else _verdict_text(version=version, verdict='PASS')
        return {
            'executor_task': 'executor-d6',
            'pr': PR,
            'head_sha': HEAD,
            'version': version,
            'state': 'COMPLETED',
            'reviewer_task': reviewer_task,
            'native_session_id': 'codex-thread',
            'account_mode': 'CODEX_CLI',
            'review_result': {
                'task_id': TASK,
                'run_id': RUN,
                'contract_digest': CONTRACT,
                'head': HEAD,
                'verdict': 'PASS',
                'review_text': verdict_text,
            },
        }

    def _comment(self, comment_id, body, login=LOGIN):
        return {'id': comment_id, 'user': {'login': login}, 'body': body}

    def _poster(self, _root, pr_number, body):
        self.posts.append((pr_number, body))
        return self._comment(900 + len(self.posts), body)

    def _ensure(self, claim, text):
        return review_dispatch._ensure_verdict_posted(
            store=self.store,
            task=self.task,
            task_id=TASK,
            claim=claim,
            text=text,
            login_resolver=lambda: LOGIN,
            root='/repo/executor',
            lister=lambda _pr: list(self.comments),
            poster=self._poster,
            clock=lambda: self.now,
            allowed=lambda: True,
        )

    def test_different_version_same_binding_line_posts_and_verifies_already_posted(self):
        v1_text = _verdict_text(version=1, verdict='FAIL', extra='Finding: F1 [High] a.py:1 bug')
        v2_text = _verdict_text(version=2, verdict='PASS', extra='All findings fixed.')
        claim_v2 = self._claim(version=2, text=v2_text)
        # Even multiple older-version comments with the same binding line do not block v2.
        self.comments = [self._comment(10, v1_text), self._comment(11, v1_text)]

        first = self._ensure(claim_v2, v2_text)
        self.assertEqual(first, {'state': 'POSTED', 'comment_id': 901})
        self.assertEqual(self.posts, [(PR, v2_text)])

        # Replay on the POSTED-receipt path verifies the exact v2 comment without conflict/duplicate.
        self.comments.append(self._comment(901, v2_text))
        second = self._ensure(claim_v2, v2_text)
        self.assertEqual(second, {'state': 'ALREADY_POSTED', 'comment_id': 901})
        self.assertEqual(len(self.posts), 1)

    def test_same_version_different_text_is_still_conflict(self):
        v1_text = _verdict_text(version=1, verdict='FAIL', extra='Finding: F1 [High] a.py:1 bug')
        v2_text = _verdict_text(version=2, verdict='PASS', extra='All findings fixed.')
        v2_other = _verdict_text(version=2, verdict='PASS', extra='Different v2 text.')
        claim_v2 = self._claim(version=2, text=v2_text)
        self.comments = [self._comment(10, v1_text), self._comment(12, v2_other)]

        with self.assertRaises(LLMToolError) as raised:
            self._ensure(claim_v2, v2_text)
        self.assertEqual(raised.exception.review_verdict_post_code, 'VERDICT_POST_CONFLICT')
        self.assertEqual(self.posts, [])

    def test_same_version_duplicates_are_still_duplicate_on_post_and_already_posted_paths(self):
        v1_text = _verdict_text(version=1, verdict='FAIL', extra='Finding: F1 [High] a.py:1 bug')
        v2_text = _verdict_text(version=2, verdict='PASS', extra='All findings fixed.')
        claim_v2 = self._claim(version=2, text=v2_text)

        # Post path with v1 present + two exact v2 comments.
        self.comments = [self._comment(10, v1_text), self._comment(20, v2_text), self._comment(21, v2_text)]
        with self.assertRaises(LLMToolError) as raised:
            self._ensure(claim_v2, v2_text)
        self.assertEqual(raised.exception.review_verdict_post_code, 'VERDICT_POST_DUPLICATE')
        self.assertEqual(self.posts, [])

        # POSTED-receipt path with v1 present + two exact v2 comments.
        claim_v2_posted = self._claim(version=2, reviewer_task='reviewer-v2-posted', text=v2_text)
        self.comments = [self._comment(10, v1_text)]
        self.assertEqual(self._ensure(claim_v2_posted, v2_text)['state'], 'POSTED')
        self.comments = [
            self._comment(10, v1_text),
            self._comment(901, v2_text),
            self._comment(902, v2_text),
        ]
        with self.assertRaises(LLMToolError) as replay_raised:
            self._ensure(claim_v2_posted, v2_text)
        self.assertEqual(replay_raised.exception.review_verdict_post_code, 'VERDICT_POST_DUPLICATE')
        self.assertEqual(len(self.posts), 1)

    def test_unestablished_comment_version_keeps_conflict_refusal(self):
        v2_text = _verdict_text(version=2, verdict='PASS')
        other_head = 'e' * 40
        cases = (
            ('missing_status_line', '\n'.join([
                '<!-- activity: type=review_verdict actor=Codex target=PM status=FIX -->',
                _binding_line(),
                'Freeform comment.',
            ])),
            ('missing_marker_line', '\n'.join([
                f'Verdict: FAIL | Version: v1 | HEAD: {HEAD}',
                _binding_line(),
                'No marker.',
            ])),
            ('invalid_version_zero', '\n'.join([
                '<!-- activity: type=review_verdict actor=Codex target=PM status=FIX -->',
                f'Verdict: FAIL | Version: v0 | HEAD: {HEAD}',
                _binding_line(),
            ])),
            ('mismatched_status_head', '\n'.join([
                '<!-- activity: type=review_verdict actor=Codex target=PM status=FIX -->',
                f'Verdict: FAIL | Version: v1 | HEAD: {other_head}',
                _binding_line(),
            ])),
            ('binding_line_with_trailing_text', '\n'.join([
                '<!-- activity: type=review_verdict actor=Codex target=PM status=FIX -->',
                f'Verdict: FAIL | Version: v1 | HEAD: {HEAD}',
                _binding_line() + ' extra',
            ])),
        )
        for label, bad_body in cases:
            with self.subTest(case=label):
                claim_v2 = self._claim(version=2, reviewer_task=f'reviewer-{label}', text=v2_text)
                self.comments = [self._comment(30, bad_body)]
                with self.assertRaises(LLMToolError) as raised:
                    self._ensure(claim_v2, v2_text)
                self.assertEqual(raised.exception.review_verdict_post_code, 'VERDICT_POST_CONFLICT')
                self.assertEqual(self.posts, [])


if __name__ == '__main__':
    unittest.main()
