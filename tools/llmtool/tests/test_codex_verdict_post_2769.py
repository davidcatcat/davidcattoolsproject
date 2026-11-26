"""#2769: a COMPLETED Codex verdict is posted before it is recorded or merged."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_dispatch, task_advance
from llmtool.errors import LLMToolError


HEAD = 'a' * 40
MAIN = 'e' * 40
TASK = '1' * 32
RUN = '2' * 32
CONTRACT = 'd' * 64
PR = 17
LOGIN = 'reviewer-user'
STAMP = '2026-11-26T00:00:00Z'


def codex_text(verdict='PASS', version=1):
    status = 'PASS' if verdict == 'PASS' else 'FAIL'
    marker = 'PASS' if verdict == 'PASS' else 'FIX'
    lines = [
        f'<!-- activity: type=review_verdict actor=Codex target=PM status={marker} -->',
        f'Verdict: {status} | Version: v{version} | HEAD: {HEAD}',
    ]
    if verdict == 'PASS':
        lines.append('FORMAL SECURITY/FUNCTIONAL/QA ACCEPT')
    else:
        lines.append('Finding: the retry count is lost.')
    lines.append(
        f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
        f'contract={CONTRACT} head={HEAD}')
    return '\n'.join(lines)


def codex_claim(verdict='PASS', *, version=1, text=None):
    return {
        'executor_task': 'executor-1', 'pr': PR, 'head_sha': HEAD,
        'version': version, 'state': 'COMPLETED',
        'claimed_at': STAMP, 'updated_at': STAMP,
        'reviewer_task': 'reviewer-1', 'native_session_id': 'session-1',
        'review_result': {
            'task_id': TASK, 'run_id': RUN, 'contract_digest': CONTRACT,
            'head': HEAD, 'verdict': verdict,
            'review_text': codex_text(verdict, version) if text is None else text,
        },
    }


def rec(claim, *, events=()):
    return {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-session',
        'task_binding': {'contract_digest': CONTRACT},
        'developer': {'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude'},
        'completion': {'executor_task_id': 'executor-1', 'state': 'WAITING_REVIEW',
                       'evidence': {'result': {'result_head': HEAD}}},
        'review_dispatch_claim': [claim],
        'review_repair_claims': [], 'completion_events': list(events),
    }


def posted(comment_id, text, *, login=LOGIN):
    comment = {'body': text, 'created_at': STAMP, 'updated_at': STAMP,
               'user': {'login': login}}
    if comment_id is not None:
        comment['id'] = comment_id
    return comment


def event_id_for(task, claim, outcome, comment_id):
    identity = {'task': task['task_id'], 'run': task['run_id'],
                'head': claim['head_sha'], 'version': claim['version'],
                'outcome': outcome, 'comment_id': comment_id}
    return hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]


class CompletedCodexPostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'tasks'
        self.root.mkdir()

    def _store(self, record):
        root = self.root

        class Store:
            def __init__(self):
                self.record = record
                self.root = root
                self.events = []

            def get(self, _task_id):
                return self.record

            def record_completion_event(self, task_id, **event):
                self.events.append((task_id, dict(event)))
                return {'created': True, 'event': event}

        return Store()

    @staticmethod
    def _github(comments=()):
        def get(path):
            if path.startswith('issues/'):
                return list(comments)
            return []

        return get

    def _advance(self, store, *, comments=(), created_id=321, resolved=LOGIN):
        record = store.record
        created = posted(created_id, codex_text(), login=LOGIN)

        def poster(_root, _pr, body):
            return dict(created, body=body)

        with patch.object(task_advance, '_d6_snapshot', return_value={
                'root': '/repo/d6', 'result_head': HEAD,
                'recorded_result_head': HEAD, 'dirty': False}), \
             patch.object(task_advance, '_pr_snapshot', return_value={
                'number': PR, 'head': {'sha': HEAD}, 'merged': False}), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(review_dispatch, '_policy',
                          return_value=SimpleNamespace(allowed=True)), \
             patch.object(review_dispatch, '_resolve_github_login',
                          return_value=resolved), \
             patch.object(review_dispatch, '_create_issue_comment', side_effect=poster):
            return task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=PR,
                github_get=self._github(comments))

    def test_posts_then_records_event_with_real_comment_id(self):
        claim = codex_claim('PASS')
        store = self._store(rec(claim))

        result = self._advance(store)

        self.assertEqual(result['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(result['codes'], [])
        self.assertEqual(result['result']['comment_id'], 321)
        self.assertEqual(result['result']['state'], 'REVIEW_PASS')
        self.assertEqual(len(store.events), 1)
        task_id, event = store.events[0]
        self.assertEqual(task_id, TASK)
        self.assertEqual(event['event_id'],
                         event_id_for(store.record, claim, 'REVIEW_PASS', 321))
        self.assertEqual(event['source'], 'REVIEWER')
        self.assertEqual(event['source_sha'], HEAD)
        self.assertEqual(event['sequence'], 0)
        self.assertEqual(event['review_kind'], 'formal')

    def test_unconfirmed_post_records_nothing(self):
        claim = codex_claim('PASS')
        store = self._store(rec(claim))

        result = self._advance(store, created_id=None)

        self.assertEqual(result['step'], 'REVIEW_NOT_POSTED')
        self.assertIn('REVIEW_VERDICT_NOT_POSTED', result['codes'])
        self.assertEqual(store.events, [])

    def test_recorded_result_is_recognised_by_the_posted_comment(self):
        claim = codex_claim('PASS')
        record = rec(claim)
        event_id = event_id_for(record, claim, 'REVIEW_PASS', 321)
        record['completion_events'] = [{
            'event_id': event_id, 'source': 'REVIEWER', 'source_sha': HEAD,
            'review_kind': 'formal'}]
        comments = [posted(321, claim['review_result']['review_text'])]

        with patch.object(review_dispatch, '_resolve_github_login',
                          return_value=LOGIN):
            outcomes = task_advance._review_outcomes(
                record, [claim], self._github(comments))

        self.assertEqual(outcomes, [{
            'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
            'outcome': 'REVIEW_PASS', 'findings_count': 0}])

    def test_posts_later_version_beside_an_older_posted_version(self):
        claim = codex_claim('PASS', version=2)
        store = self._store(rec(claim))

        result = self._advance(
            store, comments=[posted(100, codex_text('PASS', 1))])

        self.assertEqual(result['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(result['result']['comment_id'], 321)
        self.assertEqual(len(store.events), 1)
        _, event = store.events[0]
        self.assertEqual(event['event_id'],
                         event_id_for(store.record, claim, 'REVIEW_PASS', 321))

    def test_already_posted_later_version_is_reused_beside_older_post(self):
        claim = codex_claim('PASS', version=2)
        store = self._store(rec(claim))
        comments = [posted(100, codex_text('PASS', 1)),
                    posted(777, claim['review_result']['review_text'])]

        result = self._advance(store, comments=comments)

        self.assertEqual(result['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(result['result']['comment_id'], 777)
        _, event = store.events[0]
        self.assertEqual(event['event_id'],
                         event_id_for(store.record, claim, 'REVIEW_PASS', 777))

    def test_recorded_later_version_is_recognised_beside_older_post(self):
        claim = codex_claim('PASS', version=2)
        record = rec(claim)
        event_id = event_id_for(record, claim, 'REVIEW_PASS', 321)
        record['completion_events'] = [{
            'event_id': event_id, 'source': 'REVIEWER', 'source_sha': HEAD,
            'review_kind': 'formal'}]
        comments = [posted(100, codex_text('PASS', 1)),
                    posted(321, claim['review_result']['review_text'])]

        with patch.object(review_dispatch, '_resolve_github_login',
                          return_value=LOGIN):
            outcomes = task_advance._review_outcomes(
                record, [claim], self._github(comments))

        self.assertEqual(outcomes, [{
            'source': 'REVIEWER', 'source_sha': HEAD, 'version': 2,
            'outcome': 'REVIEW_PASS', 'findings_count': 0}])


class MergeCodexPassPostTests(unittest.TestCase):
    def _github(self, comments):
        pr = {'number': PR, 'head': {'sha': HEAD}, 'state': 'open',
              'merged': False, 'mergeable': True,
              'labels': [{'name': 'mac-ci'}]}
        runs = [
            {'id': 1, 'name': 'check (Linux 3.14)', 'head_sha': HEAD,
             'status': 'completed', 'conclusion': 'success'},
            {'id': 2, 'name': 'check (macOS 3.13)', 'head_sha': HEAD,
             'status': 'completed', 'conclusion': 'success'},
        ]

        def get(path):
            if path == f'commits/{HEAD}/check-runs?per_page=100':
                return {'total_count': len(runs), 'check_runs': runs}
            if path == f'pulls/{PR}/files?per_page=100':
                return [{'filename': 'src/module.py'}]
            if path == f'pulls/{PR}':
                return pr
            if path == 'branches/main':
                return {'commit': {'sha': MAIN}}
            if path == f'compare/{MAIN}...{HEAD}':
                return {'status': 'ahead'}
            if path == f'issues/{PR}/comments?per_page=100':
                return list(comments)
            if path.startswith(f'issues/{PR}/comments?per_page=100&page='):
                return []
            raise AssertionError(path)

        return pr, get

    def _merge(self, comments, *, resolved=LOGIN):
        claim = codex_claim('PASS', version=2)
        record = rec(claim)
        record['record_sha256'] = 'r' * 64
        event = {'source': 'REVIEWER', 'source_sha': HEAD,
                 'version': 2, 'outcome': 'REVIEW_PASS'}
        pr, get = self._github(comments)
        calls = []

        class Store:
            def compare_and_update(self, *_args, **_kwargs):
                return None

        def gh_runner(_root, args):
            calls.append(args)
            return SimpleNamespace(returncode=0)

        patches = [
            patch.object(review_dispatch, '_resolve_github_login',
                         return_value=resolved),
        ]
        if isinstance(resolved, Exception):
            patches = [patch.object(review_dispatch, '_resolve_github_login',
                                    side_effect=resolved)]
        with patches[0]:
            result = task_advance._merge_once(
                store=Store(), record=record, number=PR, head=HEAD, pr=pr,
                events=[event], github_get=get, gh_runner=gh_runner, root='/repo')
        return result, calls

    def test_identical_posted_pass_by_verified_login_merges(self):
        claim = codex_claim('PASS', version=2)
        text = claim['review_result']['review_text']
        result, calls = self._merge([posted(501, text)])

        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)

    def test_no_posted_pass_is_refused(self):
        result, calls = self._merge([])

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_REVIEW_VERDICT_NOT_POSTED'])
        self.assertEqual(calls, [])

    def test_identical_copy_by_another_login_is_refused(self):
        claim = codex_claim('PASS', version=2)
        text = claim['review_result']['review_text']
        result, calls = self._merge([posted(501, text, login='other-user')])

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_REVIEW_VERDICT_NOT_POSTED'])
        self.assertEqual(calls, [])

    def test_different_text_by_the_same_login_is_refused(self):
        result, calls = self._merge([posted(501, 'not the stored verdict')])

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_REVIEW_VERDICT_NOT_POSTED'])
        self.assertEqual(calls, [])

    def test_unresolved_login_is_refused(self):
        claim = codex_claim('PASS', version=2)
        text = claim['review_result']['review_text']
        result, calls = self._merge(
            [posted(501, text)], resolved=LLMToolError('unresolved'))

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_REVIEW_VERDICT_NOT_POSTED'])
        self.assertEqual(calls, [])

    def test_older_posted_version_does_not_block_the_current_pass(self):
        claim = codex_claim('PASS', version=2)
        text = claim['review_result']['review_text']
        result, calls = self._merge(
            [posted(500, codex_text('PASS', 1)), posted(501, text)])

        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)

    def test_only_the_older_posted_version_is_refused(self):
        result, calls = self._merge([posted(500, codex_text('PASS', 1))])

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_REVIEW_VERDICT_NOT_POSTED'])
        self.assertEqual(calls, [])

    def test_current_pass_by_another_login_is_refused_beside_older_post(self):
        claim = codex_claim('PASS', version=2)
        text = claim['review_result']['review_text']
        result, calls = self._merge(
            [posted(500, codex_text('PASS', 1)),
             posted(501, text, login='other-user')])

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_REVIEW_VERDICT_NOT_POSTED'])
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
