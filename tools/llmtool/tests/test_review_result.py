import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llmtool import cli, review_result, task_store, task_submission_cli


TASK, RUN, HEAD, CONTRACT = 'a' * 32, 'b' * 32, 'c' * 40, 'd' * 64
EXECUTOR, REVIEWER, PR = 'executor-d6', 'reviewer-d6', 42


def _store(root, *, claim_state='DISPATCHED', account_mode='DEFAULT_SECOND_ACCOUNT'):
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test review results', 'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}], 'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    stamp = '2026-10-27T00:00:00Z'
    claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
             'state': claim_state, 'reviewer_task': REVIEWER,
             'native_session_id': None if claim_state == 'CLAIMED' else 'review-session',
             'account_mode': account_mode,
             'claimed_at': stamp, 'updated_at': stamp}
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': stamp, 'updated_at': stamp,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test review results',
        'request_sha256': hashlib.sha256(b'test review results').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': None, 'pm_assignment': None, 'completion': None,
        'review_dispatch_claim': [claim],
    }
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store


def _developer_record(family):
    cli_family = family in ('codex', 'opencode', 'agy')
    waiting = family == 'claude'
    artifact = None if waiting else 'diff'
    return {
        'assignment_id': 'e' * 32, 'selection_source': 'PM',
        'state': 'DEVELOPER_PENDING' if waiting else 'DEVELOPER_RESPONDED',
        'provider_profile': family,
        'provider_type': {'claude': None, 'codex': 'codex-cli',
                          'company-gw': 'openai-compatible',
                          'gw-agent': 'openai-compatible',
                          'opencode': 'opencode-cli', 'agy': 'agy-cli'}[family],
        'model': ('claude-sonnet-5-5-medium' if family == 'agy' else
                  'opencode/deepseek-v4.1-flash-free' if family == 'opencode' else
                  'qwen3.8-27b' if family == 'gw-agent' else
                  None if family in ('claude', 'company-gw') else 'gpt-6-luna'),
        'backend_session_semantics': ('CLI_REPORTED_SESSION_ID' if cli_family
                                      else 'NO_NATIVE_SESSION_ID_EXPOSED'),
        'session_id': 'developer-session', 'source_sha': HEAD,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['tools/llmtool'], 'write_paths': ['tools/llmtool'],
        'acceptance': ['Record a bounded review disposition.'], 'context_sha256': 'f' * 64,
        'egress_basis': 'Bounded task context', 'input_bytes': 1,
        'max_tokens': None if cli_family else 1000,
        'artifact_format': 'unified_diff', 'artifact': artifact,
        'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest()
        if artifact is not None else None,
        'usage': None, 'error_code': None,
        'created_at': '2026-10-27T00:00:00Z',
        **({'owner_assignment_ref':
            'https://github.com/davidcatcat/davidcattoolsproject/issues/525#issuecomment-6468216854'}
           if family == 'claude' else {}),
    }


class FamilyStore:
    def __init__(self, store, family):
        self.store, self.family = store, family

    def get(self, task_id):
        record = self.store.get(task_id)
        record['developer'] = _developer_record(self.family)
        return record

    def record_completion_event(self, *args, **kwargs):
        return self.store.record_completion_event(*args, **kwargs)


def _comment(status='PASS', *, actor='Claude', head=HEAD, version=1,
             comment_id=7, created='2026-10-27T01:00:00Z', extra=''):
    stamp = 'PASS' if status == 'PASS' else 'FAIL' if status in ('FAIL', 'FIX') else status
    marker_status = 'PASS' if status == 'PASS' else 'FIX' if status == 'FIX' else 'FAIL'
    body = (f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker_status} -->\n'
            f'Verdict: {stamp} | Version: v{version} | HEAD: {head}\n')
    if actor in ('Claude', 'Codex'):
        if actor == 'Codex' and status == 'PASS':
            body += ('FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
                     f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
                     f'contract={CONTRACT} head={head}\n')
        else:
            body += (f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
                     f'contract={CONTRACT} head={head}\n')
            if status == 'PASS':
                body += 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
    body += extra
    return {'id': comment_id, 'body': body, 'created_at': created, 'updated_at': created,
            'user': {'login': 'shared-agent'}}


class ReviewResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = _store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_result(self, comments, *, github_get=None, **kwargs):
        digest = self.store.get(TASK)['task_binding']['contract_digest']
        comments = [dict(comment, body=comment['body'].replace(CONTRACT, digest))
                    for comment in comments]
        return review_result.record_review_result(
            store=self.store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
            github_get=(lambda path: comments) if github_get is None else github_get,
            **kwargs)

    def test_formal_claude_pass_is_recorded_and_replay_is_idempotent(self):
        self.store = FamilyStore(self.store, 'codex')
        first = self.run_result([_comment()])
        second = self.run_result([_comment()])
        self.assertEqual(first['state'], 'REVIEW_PASS')
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        event = self.store.get(TASK)['completion_events'][0]
        self.assertEqual(event['source'], 'REVIEWER')
        self.assertEqual(event['source_sha'], HEAD)

    def test_claude_pass_is_rejected_for_claude_developer_task(self):
        self.store = FamilyStore(self.store, 'claude')
        self.assertEqual(self.run_result([_comment(actor='Claude')])['state'], 'NOT_READY')

    def test_completed_codex_claim_is_recorded_without_github_fetch(self):
        codex_root = Path(self.temp.name) / 'codex'
        codex_root.mkdir(mode=0o700)
        codex_store = _store(codex_root, claim_state='CLAIMED', account_mode='CODEX_CLI')
        record = codex_store.get(TASK)
        digest = record['task_binding']['contract_digest']
        claim = record['review_dispatch_claim'][0]
        review_text = _comment(actor='Codex')['body'].replace(CONTRACT, digest)
        completed = dict(claim, state='COMPLETED', native_session_id='review-session')
        completed['review_result'] = {
            'task_id': TASK, 'run_id': RUN, 'contract_digest': digest,
            'head': HEAD, 'verdict': 'PASS', 'review_text': review_text,
        }
        codex_store.compare_and_update(
            TASK, expected={'record_sha256': record['record_sha256']},
            review_dispatch_claim=[completed])
        self.store = FamilyStore(codex_store, 'claude')
        result = self.run_result([], github_get=lambda _path: self.fail('unexpected GitHub read'))
        self.assertEqual(result['state'], 'REVIEW_PASS')

    def test_fail_and_blocked_no_verdict(self):
        failed = self.run_result([_comment('FIX')])
        self.assertEqual(failed['state'], 'REVIEW_FAIL')
        self.assertEqual(self.run_result([], reviewer_session_done=True)['state'], 'REVIEW_BLOCKED')

    def test_no_verdict_yet_does_not_record_event(self):
        result = self.run_result([])
        self.assertEqual(result, {'state': 'NOT_READY', 'event': None})
        self.assertEqual(self.store.get(TASK).get('completion_events', []), [])

    def test_wrong_head_and_version_are_ignored(self):
        result = self.run_result([_comment(head='e' * 40), _comment(version=2)])
        self.assertEqual(result['state'], 'NOT_READY')

    def test_later_fail_supersedes_pass(self):
        later = _comment('FAIL', comment_id=8, created='2026-10-27T02:00:00Z')
        self.assertEqual(self.run_result([_comment(), later])['state'], 'REVIEW_FAIL')

    def test_codex_pass_is_advisory(self):
        self.assertEqual(self.run_result([_comment(actor='Codex')])['state'], 'NOT_READY')

    def test_bound_codex_pass_is_formal_only_for_claude_task(self):
        self.store = FamilyStore(self.store, 'claude')
        self.assertEqual(self.run_result([_comment(actor='Codex')])['state'], 'REVIEW_PASS')

    def test_codex_binding_is_found_after_other_nonempty_lines(self):
        self.store = FamilyStore(self.store, 'claude')
        comment = _comment(actor='Codex')
        lines = comment['body'].splitlines()
        binding = next(line for line in lines if line.startswith('LLMTOOL-REVIEW-BINDING'))
        lines.remove(binding)
        lines.extend(('Finding: reviewed the bound diff', binding))
        comment['body'] = '\n'.join(lines)
        self.assertEqual(self.run_result([comment])['state'], 'REVIEW_PASS')

    def test_codex_pass_with_mismatched_binding_is_advisory(self):
        self.store = FamilyStore(self.store, 'claude')
        comment = _comment(actor='Codex')
        comment['body'] = comment['body'].replace(f'task={TASK}', 'task=wrong')
        self.assertEqual(self.run_result([comment])['state'], 'NOT_READY')

    def test_bound_codex_nonpass_is_recorded_only_for_claude_task(self):
        self.store = FamilyStore(self.store, 'claude')
        self.assertEqual(self.run_result([_comment('FIX', actor='Codex')])['state'],
                         'REVIEW_FAIL')

    def test_unbound_codex_nonpass_is_ignored(self):
        self.store = FamilyStore(self.store, 'claude')
        comment = _comment('FIX', actor='Codex')
        comment['body'] = comment['body'].replace(f'run={RUN}', 'run=wrong')
        self.assertEqual(self.run_result([comment])['state'], 'NOT_READY')

    def test_codex_pass_is_formal_for_other_developer_families(self):
        for family in ('agy', 'opencode', 'company-gw', 'gw-agent'):
            self.store = FamilyStore(self.store, family)
            self.assertEqual(self.run_result([_comment(actor='Codex')])['state'],
                             'REVIEW_PASS')

    def test_codex_pass_stays_advisory_for_same_family_tasks(self):
        for family in ('codex',):
            self.store = FamilyStore(self.store, family)
            self.assertEqual(self.run_result([_comment(actor='Codex')])['state'],
                             'NOT_READY')

    def test_event_survives_taskstore_restart(self):
        self.run_result([_comment()])
        restarted = task_store.TaskStore(Path(self.temp.name).resolve())
        events = restarted.get(TASK)['completion_events']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['source'], 'REVIEWER')

    def test_cli_route_reaches_handler(self):
        with patch.object(task_submission_cli, 'run_task_review_result', return_value=17) as handler:
            self.assertEqual(cli.main(['task', 'review-result', TASK,
                                       '--executor-task', EXECUTOR, '--pr', str(PR)]), 17)
        handler.assert_called_once()

    def test_review_dispatch_cli_accepts_explicit_reviewer(self):
        with patch('llmtool.source_freshness.require_fresh_source', return_value='OK'), \
                patch.object(task_submission_cli, 'run_task_review_dispatch', return_value=17) as handler:
            self.assertEqual(cli.main(['task', 'review-dispatch', TASK,
                                       '--executor-task', EXECUTOR, '--pr', str(PR),
                                       '--reviewer', 'codex-reviewer']), 17)
        self.assertEqual(handler.call_args.args[0].reviewer, 'codex-reviewer')


class ReconcileStore(FamilyStore):
    def compare_and_update(self, *args, **kwargs):
        return self.store.compare_and_update(*args, **kwargs)


class UnknownClaimReconcileTests(unittest.TestCase):
    """#1636: only one bound Claude PASS after the claim settles an UNKNOWN claim."""

    run_result = ReviewResultTests.run_result

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ReconcileStore(_store(self.temp.name, claim_state='UNKNOWN'), 'gw-agent')

    def state(self, version=1):
        return next(item['state'] for item in self.store.get(TASK)['review_dispatch_claim']
                    if item['version'] == version)

    def test_one_bound_pass_after_the_claim_settles_it_once(self):
        self.assertEqual(self.run_result([_comment()])['state'], 'REVIEW_PASS')
        self.assertEqual(self.state(), 'DISPATCHED')
        self.assertFalse(self.run_result([_comment()])['created'])
        self.assertEqual(self.state(), 'DISPATCHED')

    def test_other_verdicts_do_not_settle(self):
        late = '2026-10-27T02:00:00Z'
        for comments in ([_comment(head='e' * 40)], [_comment(version=2)], [_comment('FAIL')],
                         [_comment('FIX')], [_comment(), _comment(comment_id=8, created=late)],
                         [_comment(), _comment('FAIL', comment_id=8)], [_comment(actor='Codex')],
                         [_comment(created='2026-10-26T23:59:59Z')],
                         [_comment(created='2026-10-27T00:00:00Z')],
                         [dict(_comment(), updated_at=late)]):
            with self.subTest(comments=comments):
                self.run_result(comments)
                self.assertEqual(self.state(), 'UNKNOWN')


class UnknownClaimReconcileShapeTests(unittest.TestCase):
    """#1636: claim shape, another claim, same family and a store conflict."""

    run_result = ReviewResultTests.run_result
    setUp = UnknownClaimReconcileTests.setUp
    state = UnknownClaimReconcileTests.state

    def test_claim_with_failure_reason_or_no_session_does_not_settle(self):
        for name, change in (('r', {'failure_reason': 'NO_FINAL_MESSAGE',
                                    'native_session_id': 'review-session'}), ('n', {})):
            with self.subTest(change=change):
                Path(self.temp.name, name).mkdir(mode=0o700)
                store = _store(Path(self.temp.name, name), claim_state='CLAIMED')
                record = store.get(TASK)
                store.compare_and_update(
                    TASK, expected={'record_sha256': record['record_sha256']},
                    review_dispatch_claim=[dict(record['review_dispatch_claim'][0],
                                                state='UNKNOWN', **change)])
                self.store = ReconcileStore(store, 'gw-agent')
                self.assertEqual(self.run_result([_comment()])['state'], 'REVIEW_PASS')
                self.assertEqual(self.state(), 'UNKNOWN')

    def test_pass_for_another_reviewer_claim_settles_only_that_claim(self):
        first = self.store.get(TASK)['review_dispatch_claim'][0]
        second = dict(first, version=2, reviewer_task='reviewer-other', state='CLAIMED',
                      native_session_id=None)
        for claims in ([first, second],
                       [first, dict(second, state='UNKNOWN', native_session_id='other')]):
            self.store.compare_and_update(
                TASK, expected={'record_sha256': self.store.get(TASK)['record_sha256']},
                review_dispatch_claim=claims)
        self.run_result([_comment(version=2)])
        self.assertEqual(self.state(1), 'UNKNOWN')
        self.run_result([_comment(version=2)], version=2)
        self.assertEqual((self.state(1), self.state(2)), ('UNKNOWN', 'DISPATCHED'))

    def test_claude_developer_task_or_store_conflict_does_not_settle(self):
        self.store.family = 'claude'
        self.run_result([_comment()])
        self.assertEqual(self.state(), 'UNKNOWN')
        self.store.family = 'gw-agent'
        with patch.object(self.store.store, 'compare_and_update', side_effect=RuntimeError):
            with self.assertRaises(Exception) as caught:
                self.run_result([_comment()])
        self.assertEqual(caught.exception.review_dispatch_code,
                         'REVIEW_RESULT_CLAIM_RECONCILE_FAILED')
        self.assertEqual(self.state(), 'UNKNOWN')


class StableFindingIdTests(unittest.TestCase):
    """#1502 WP-D section 4.12: finding IDs, severity and blocking."""

    def test_ids_map_to_severity_and_low_or_advisory_never_block(self):
        body = ('Verdict: FAIL\nFinding: F1 [High] a.py:3 wrong\n'
                'Finding: F2 [Low] a.py:9 style\nFinding: F3 [Advisory] b.py:1 note\n'
                'Finding: F10 [Medium] c.py:2 gap\nFinding: F4 [Blocker] d.py:5 crash\n')
        self.assertEqual(review_result.finding_ids(body),
                         {'F1': 'High', 'F2': 'Low', 'F3': 'Advisory',
                          'F10': 'Medium', 'F4': 'Blocker'})
        self.assertEqual(review_result.blocking_findings(body), ['F1', 'F4', 'F10'])

    def test_only_low_findings_leave_no_blocking_finding(self):
        self.assertEqual(review_result.blocking_findings(
            'Finding: F1 [Low] a.py:1 x\nFinding: F2 [Advisory] a.py:2 y\n'), [])

    def test_legacy_or_malformed_verdicts_are_none(self):
        for body in ('no findings\n', 'Finding: a.py:1 bug\n',
                     'Finding: F1 [High] a.py:1 x\nFinding: F1 [Low] a.py:2 y\n',
                     'Finding: F1 [Severe] a.py:1 x\n', 'Finding: F0 [High] a.py:1 x\n',
                     'Finding: F1 [High] a.py:1 x\nFinding: [P1] issue\n', None):
            with self.subTest(body=body):
                self.assertIsNone(review_result.finding_ids(body))
                self.assertIsNone(review_result.blocking_findings(body))


class PriorityFindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = _store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _findings(self, extra):
        verdict = review_result._comment_verdict(
            _comment('FAIL', extra=extra), head=HEAD, version=1,
            task=self.store.get(TASK))
        return verdict[3]

    def test_plain_and_bold_priority_lines_count(self):
        self.assertEqual(self._findings('[P0] issue\n**[P1] issue\n'), 2)

    def test_priority_line_with_existing_prefix_counts_once(self):
        self.assertEqual(self._findings('- [P0] issue\n'), 1)
        self.assertEqual(self._findings('Finding: [P1] issue\n'), 1)

    def test_mid_line_and_invalid_priority_tags_count_zero(self):
        self.assertEqual(self._findings('issue [P0]\n[P4] issue\n[Px] issue\n'), 0)

    def test_no_finding_line_counts_zero(self):
        self.assertEqual(self._findings('no findings\n'), 0)
