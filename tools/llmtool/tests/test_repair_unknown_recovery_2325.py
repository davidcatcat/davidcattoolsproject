"""Issue #2325: a repair whose outcome or claim is unknown must not leave the Task stuck.

Every Task record lives in a real temporary TaskStore and is read back through
its full validation. Only the D6 snapshot, the GitHub/gh/git readers and the
repair dispatcher are injected; no network, provider or Git command runs.
All values are synthetic fixture data.
"""

import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import review_repair, task_advance, task_store, task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

HEAD = '1' * 40
HEAD2 = '2' * 40
SOURCE = '3' * 40
STAMP = '2026-11-18T00:00:00Z'
PM_SESSION = 'pm-fixture-2325'
COMMON_DIR = '/nonexistent/llmtool-fixture-2325/.git'
ARTIFACT = ('diff --git a/tools/sample.py b/tools/sample.py\n'
            '--- a/tools/sample.py\n'
            '+++ b/tools/sample.py\n'
            '@@ -1 +1 @@\n'
            '-VALUE = 1\n'
            '+VALUE = 2\n')

# Evidence 1: PR #2323, Task 07c7358d, test-failure repair Task 88b3c440.
E1_TASK = '07c7358d' + '0' * 24
E1_CHILD = '88b3c440' + '0' * 24
E1_EXECUTOR = 'executor-01'
# Evidence 2: PR #2340, Task e71e850c, review repair Task never delegated.
E2_TASK = 'e71e850c' + '0' * 24
E2_CHILD = '02bb9833' + '0' * 24
E2_EXECUTOR = 'executor-02'


def _base_record(task_id, run_id, *, goal):
    """A PM_RESPONDED Task in the production base shape, with no Developer yet."""
    request = 'Synthetic fixture request for issue 2325.'
    digest = hashlib.sha256(request.encode('utf-8')).hexdigest()
    record = task_submission_cli._record(
        task_id, run_id, PM_SESSION, 'fixture-pm', request,
        state='PM_RESPONDED', provider_type='openai-compatible', model='fixture-model',
        backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=digest,
        contract=task_submission_cli._contract(task_id, digest))
    record.update(
        created_at=STAMP, updated_at=STAMP,
        pm_route_binding={'type': 'openai-compatible', 'model': 'fixture-model',
                          'base_url': None},
        response='Synthetic fixture PM response.',
        pm_assignment={'schema_version': 1, 'route': 'opencode', 'risk': 'LOW',
                       'goal': goal, 'acceptance': ['Resolve the sample failure.']})
    return record


def _execution(*, status, cleanup_status):
    done = status == 'SUCCEEDED'
    return {
        'status': status,
        'native_session_id': 'ses_fixture2325' if done else None,
        'process_exit_code': 0 if done else None,
        'stdout_sha256': 'c' * 64 if done else None,
        'elapsed_ms': 1000 if done else None,
        'cleanup_status': cleanup_status,
        'usage_source': 'UNKNOWN',
        'source_bytes': 100 if done else None,
        'source_file_count': 1 if done else None,
        'source_manifest_sha256': 'd' * 64 if done else None,
    }


def _opencode_developer(*, state, execution, error_code=None, artifact=None,
                        source_sha=SOURCE):
    return {
        'assignment_id': 'e' * 32, 'selection_source': 'PM', 'state': state,
        'provider_profile': 'opencode', 'provider_type': 'opencode-cli', 'model': None,
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-fixture-2325', 'source_sha': source_sha,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['tools/sample.py'], 'write_paths': ['tools/sample.py'],
        'acceptance': ['Resolve the sample failure.'],
        'context_sha256': 'a' * 64,
        'egress_basis': 'Synthetic fixture data only.',
        'input_bytes': 100, 'max_tokens': 4096,
        'artifact_format': 'unified_diff', 'artifact': artifact,
        'artifact_sha256': (None if artifact is None else
                            hashlib.sha256(artifact.encode('utf-8')).hexdigest()),
        'usage': None, 'error_code': error_code, 'created_at': STAMP,
        'execution': execution,
    }


def _responded_developer(source_sha=SOURCE):
    return _opencode_developer(
        state='DEVELOPER_RESPONDED', artifact=ARTIFACT, source_sha=source_sha,
        execution=_execution(status='SUCCEEDED', cleanup_status='CONFIRMED'))


def _unknown_developer(cleanup_status):
    """OpenCode run that ended DEVELOPER_OUTCOME_UNKNOWN (evidence 1)."""
    return _opencode_developer(
        state='DEVELOPER_OUTCOME_UNKNOWN', error_code='OPENCODE_OUTPUT_TRUNCATED',
        execution=_execution(status='UNKNOWN', cleanup_status=cleanup_status))


def _completion(executor_task):
    return {'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task,
            'reviewer_task_id': None, 'evidence': {'common_dir': COMMON_DIR},
            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None}


def _receipt(*, task_id, run_id, executor_task, source_sha=HEAD):
    receipt = {
        'result': 'FAIL', 'exit_code': 1, 'source_sha': source_sha, 'tree': '4' * 40,
        'fetch_head': source_sha, 'modules': ['test_sample'], 'identity': {'uid': 1000},
        'started_at': STAMP, 'ended_at': '2026-11-18T00:01:00Z', 'elapsed_s': 60,
        'ran_total': 1, 'final': ['FAILED (failures=1)'], 'failures': ['test_sample'],
        'log_bytes': 100, 'log_sha256': '5' * 64, 'remote_log_path': None,
        'task_id': task_id, 'run_id': run_id, 'executor_task': executor_task,
        'pm_session': PM_SESSION, 'command_digest': '6' * 64,
        'failure_excerpt': 'AssertionError: synthetic sample failure',
    }
    receipt['receipt_digest'] = hashlib.sha256(
        task_store._json_bytes(receipt)).hexdigest()
    return receipt


def _repair_claim(*, executor_task, pr, family, state, repair_task_id, comment_id):
    return {'executor_task': executor_task, 'pr': pr, 'head_sha': HEAD, 'version': 1,
            'attempt': 1, 'family': family, 'state': state,
            'repair_task_id': repair_task_id, 'comment_id': comment_id,
            'packet_sha256': 'f' * 64, 'claimed_at': '2026-11-18T00:05:00Z'}


def _d6(root, executor_task, *, head=HEAD, dirty=False):
    return {'root': root, 'common_dir': COMMON_DIR, 'task_id': executor_task,
            'branch': 'fix-2325', 'result_head': head, 'recorded_result_head': head,
            'registered_result_head': head, 'dirty': dirty,
            'state': 'DIRTY' if dirty else 'CLEAN', 'registration_state': 'ACTIVE',
            'trusted_agent_id': 'pm-deterministic'}


def _review_fail_outcome():
    return [{'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
             'outcome': 'REVIEW_FAIL', 'findings_count': 1}]


class RepairUnknownRecovery2325Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = os.path.realpath(self._tmp.name)
        root = os.path.join(base, 'tasks')
        os.mkdir(root, 0o700)
        os.chmod(root, 0o700)
        self.store = TaskStore(root)
        self.d6_root = os.path.join(base, 'd6')
        self.published_pr = 2323
        self.gh_calls = []
        self.git_calls = []
        self.get_calls = []

    def tearDown(self):
        self._tmp.cleanup()

    # -- fixtures ---------------------------------------------------------

    def save(self, record):
        """Write one record into the real store and read it back validated."""
        record = dict(record)
        record['record_sha256'] = task_store._digest(record)
        path = os.path.join(str(self.store.root), record['task_id'] + '.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(task_store._json_bytes(record))
        os.chmod(path, 0o600)
        return self.store.get(record['task_id'])

    def evidence_1(self, *, child_cleanup='CONFIRMED', claim_state='DISPATCHED'):
        """PR #2323: test-failure repair claim, repair Task DEVELOPER_OUTCOME_UNKNOWN."""
        run_id = 'a' * 32
        parent = _base_record(E1_TASK, run_id, goal='Fix the remote test failure.')
        parent.update(
            developer=_responded_developer(), completion=_completion(E1_EXECUTOR),
            remote_test_receipts=[_receipt(task_id=E1_TASK, run_id=run_id,
                                           executor_task=E1_EXECUTOR)],
            review_repair_claims=[_repair_claim(
                executor_task=E1_EXECUTOR, pr=2323, family='pr-2323-ec2',
                state=claim_state, repair_task_id=E1_CHILD, comment_id=None)])
        self.save(parent)
        child = _base_record(E1_CHILD, 'b' * 32, goal='Repair the remote test failure.')
        child.update(developer=_unknown_developer(child_cleanup))
        self.save(child)

    def evidence_2(self, *, child_developer=None, test_failure=False):
        """PR #2340: repair claim UNKNOWN; repair Task never delegated.

        test_failure=True gives the same claim in the '-ec2' family after an
        exact-head EC2 FAIL, before any review was dispatched.
        """
        run_id = 'c' * 32
        parent = _base_record(E2_TASK, run_id, goal='Repair the review finding.')
        parent.update(
            developer=_responded_developer(), completion=_completion(E2_EXECUTOR),
            review_repair_claims=[_repair_claim(
                executor_task=E2_EXECUTOR, pr=2340,
                family='pr-2340-ec2' if test_failure else 'pr-2340', state='UNKNOWN',
                repair_task_id=E2_CHILD, comment_id=None if test_failure else 1001)])
        if test_failure:
            parent['remote_test_receipts'] = [_receipt(
                task_id=E2_TASK, run_id=run_id, executor_task=E2_EXECUTOR)]
        else:
            parent['review_dispatch_claim'] = [{
                'executor_task': E2_EXECUTOR, 'pr': 2340, 'head_sha': HEAD, 'version': 1,
                'state': 'DISPATCHED', 'reviewer_task': 'reviewer-02',
                'native_session_id': 'fixture-review-session',
                'account_mode': 'DEFAULT_SECOND_ACCOUNT',
                'claimed_at': STAMP, 'updated_at': STAMP}]
        self.save(parent)
        # Recorded by record-pm (PM_RESPONDED); the delegate never wrote a developer.
        child = _base_record(E2_CHILD, 'd' * 32, goal='Repair the review finding.')
        if child_developer is not None:
            child['developer'] = child_developer
        self.save(child)

    def gh_runner(self, root, args, *rest, **kwargs):
        """Read-only `gh pr list`: the published PR is open at HEAD."""
        self.gh_calls.append(list(args))
        if list(args[:2]) == ['pr', 'list']:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                [{'number': self.published_pr, 'headRefOid': HEAD}]))
        raise RuntimeError('unexpected gh command in fixture')

    def git_runner(self, root, args, *rest, **kwargs):
        self.git_calls.append(list(args))
        raise RuntimeError('git is not available in this fixture')

    def github_get(self, path):
        self.get_calls.append(path)
        for number in (2323, 2340):
            if path == f'pulls/{number}':
                return {'number': number, 'state': 'open', 'merged': False,
                        'head': {'sha': HEAD}}
        raise RuntimeError('unexpected GitHub read in fixture')

    def advance(self, task_id, d6, repair_call, *, review_outcomes=()):
        with patch.object(task_advance, '_d6_snapshot', return_value=d6), \
                patch.object(task_advance, '_review_outcomes',
                             return_value=list(review_outcomes)):
            return task_advance.advance(
                task_id=task_id, store=self.store, repair_review_call=repair_call,
                github_get=self.github_get, publish_gh_runner=self.gh_runner,
                publish_git_runner=self.git_runner, merge_gh_runner=self.gh_runner)

    def stored_claims(self, task_id):
        return self.store.get(task_id)['review_repair_claims']

    # -- fixtures are real store records ----------------------------------

    def test_fixtures_pass_task_store_validation(self):
        self.evidence_1()
        self.evidence_2()
        for task_id in (E1_TASK, E1_CHILD, E2_TASK, E2_CHILD):
            self.assertEqual(self.store.get(task_id)['task_id'], task_id)
        self.assertEqual(task_advance.derive_repair_state(self.store.get(E1_CHILD)), 'UNKNOWN')
        self.assertEqual(task_advance.derive_repair_state(self.store.get(E2_CHILD)), 'UNKNOWN')

    # -- evidence 1: test-failure repair DEVELOPER_OUTCOME_UNKNOWN --------

    def test_evidence_1_confirmed_cleanup_retries_the_dispatched_claim(self):
        self.evidence_1()
        before = self.stored_claims(E1_TASK)
        repair = Mock(return_value={'attempt': 2, 'repair_task_id': '9' * 32,
                                    'claim': {'state': 'DISPATCHED'}})
        result = self.advance(E1_TASK, _d6(self.d6_root, E1_EXECUTOR), repair)
        self.assertEqual(result['step'], 'REPAIR_RETRY')
        self.assertEqual(result['codes'], [])
        repair.assert_called_once()
        kwargs = repair.call_args.kwargs
        # The retry goes through review_repair's counted retry of this claim.
        self.assertEqual(kwargs['retry_claim_id'], E1_CHILD)
        self.assertEqual(kwargs['remote_test_receipt']['source_sha'], HEAD)
        self.assertEqual(kwargs['pr_number'], 2323)
        self.assertIs(kwargs['auto'], True)
        # advance itself writes no claim; counts are review_repair's.
        self.assertEqual(self.stored_claims(E1_TASK), before)
        # Only read-only lookups ran: no git command and only `gh pr list`.
        self.assertEqual(self.git_calls, [])
        self.assertTrue(self.gh_calls)
        self.assertTrue(all(call[:2] == ['pr', 'list'] for call in self.gh_calls))

    def test_evidence_1_dirty_d6_stays_refused(self):
        self.evidence_1()
        repair = Mock()
        result = self.advance(E1_TASK, _d6(self.d6_root, E1_EXECUTOR, dirty=True), repair)
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])
        self.assertIsNone(result['result'])
        repair.assert_not_called()

    def test_evidence_1_moved_head_never_retries(self):
        self.evidence_1()
        repair = Mock()
        result = self.advance(E1_TASK, _d6(self.d6_root, E1_EXECUTOR, head=HEAD2), repair)
        self.assertNotEqual(result.get('step'), 'REPAIR_RETRY')
        repair.assert_not_called()
        self.assertEqual(self.stored_claims(E1_TASK)[0]['state'], 'DISPATCHED')

    def test_evidence_1_moved_pr_head_never_retries(self):
        # F1 regression: D6 is clean at HEAD, exact-head EC2 FAIL at HEAD,
        # DISPATCHED repair at HEAD, but PR head moved to HEAD2. Must not retry.
        self.evidence_1()
        repair = Mock()
        def gh_get(path):
            if path == 'pulls/2323':
                return {'number': 2323, 'state': 'open', 'merged': False,
                        'head': {'sha': HEAD2}}
            return self.github_get(path)
        with patch.object(task_advance, '_d6_snapshot',
                          return_value=_d6(self.d6_root, E1_EXECUTOR)):
            result = task_advance.advance(
                task_id=E1_TASK, store=self.store, repair_review_call=repair,
                github_get=gh_get, publish_gh_runner=self.gh_runner,
                publish_git_runner=self.git_runner, merge_gh_runner=self.gh_runner,
                pr_number=2323)
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])
        repair.assert_not_called()
        self.assertEqual(self.stored_claims(E1_TASK)[0]['state'], 'DISPATCHED')

    def test_evidence_1_unconfirmed_cleanup_stays_refused(self):
        self.evidence_1(child_cleanup='UNKNOWN')
        repair = Mock()
        result = self.advance(E1_TASK, _d6(self.d6_root, E1_EXECUTOR), repair)
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])
        repair.assert_not_called()

    # -- evidence 2: review repair claim UNKNOWN, never delegated --------

    def test_evidence_2_never_delegated_claim_is_replayed_once(self):
        self.evidence_2()
        before = self.stored_claims(E2_TASK)
        repair = Mock(return_value={'attempt': 1, 'repair_task_id': E2_CHILD,
                                    'claim': {'state': 'DISPATCHED'}})
        result = self.advance(E2_TASK, _d6(self.d6_root, E2_EXECUTOR), repair,
                              review_outcomes=_review_fail_outcome())
        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertEqual(result['codes'], [])
        repair.assert_called_once()
        kwargs = repair.call_args.kwargs
        # A replay of the same claim: no retry, so no new claim and no new count.
        self.assertIsNone(kwargs['retry_claim_id'])
        self.assertNotIn('remote_test_receipt', kwargs)
        self.assertEqual(kwargs['pr_number'], 2340)
        self.assertEqual(result['result']['attempt'], 1)
        self.assertEqual(result['result']['repair_task_id'], E2_CHILD)
        self.assertEqual(self.stored_claims(E2_TASK), before)
        self.assertEqual(self.get_calls, ['pulls/2340'])
        self.assertEqual((self.gh_calls, self.git_calls), ([], []))

    def test_evidence_2_refused_replay_names_the_next_command(self):
        self.evidence_2()
        repair = Mock(side_effect=LLMToolError('D6_SOURCE_BINDING_MISMATCH'))
        result = self.advance(E2_TASK, _d6(self.d6_root, E2_EXECUTOR), repair,
                              review_outcomes=_review_fail_outcome())
        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertEqual(result['codes'], ['TASK_ADVANCE_REFUSED', 'REPAIR_REPLAY_REFUSED'])
        self.assertEqual(result['result'], {
            'repair_task_id': E2_CHILD, 'claim_state': 'UNKNOWN',
            'next_command': (f'llmtool task repair {E2_TASK} '
                             f'--executor-task {E2_EXECUTOR} --pr 2340')})
        self.assertEqual(self.stored_claims(E2_TASK)[0]['state'], 'UNKNOWN')

    def test_evidence_2_dirty_d6_stays_refused(self):
        self.evidence_2()
        repair = Mock()
        result = self.advance(E2_TASK, _d6(self.d6_root, E2_EXECUTOR, dirty=True), repair,
                              review_outcomes=_review_fail_outcome())
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])
        self.assertIsNone(result['result'])
        repair.assert_not_called()

    def test_evidence_2_moved_head_never_replays(self):
        self.evidence_2()
        repair = Mock()
        result = self.advance(E2_TASK, _d6(self.d6_root, E2_EXECUTOR, head=HEAD2), repair,
                              review_outcomes=_review_fail_outcome())
        self.assertNotIn(result.get('step'), ('REPAIR_REVIEW', 'REPAIR_RETRY'))
        repair.assert_not_called()

    def test_evidence_2_moved_pr_head_never_replays(self):
        # F1 regression: D6 is clean at HEAD, UNKNOWN never-delegated claim at HEAD,
        # but PR 2340 head moved to HEAD2. Must not replay.
        self.evidence_2()
        repair = Mock()
        def gh_get(path):
            if path == 'pulls/2340':
                return {'number': 2340, 'state': 'open', 'merged': False,
                        'head': {'sha': HEAD2}}
            return self.github_get(path)
        with patch.object(task_advance, '_d6_snapshot',
                          return_value=_d6(self.d6_root, E2_EXECUTOR)), \
                patch.object(task_advance, '_review_outcomes',
                             return_value=list(_review_fail_outcome())):
            result = task_advance.advance(
                task_id=E2_TASK, store=self.store, repair_review_call=repair,
                github_get=gh_get, publish_gh_runner=self.gh_runner,
                publish_git_runner=self.git_runner, merge_gh_runner=self.gh_runner,
                pr_number=2340)
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])
        repair.assert_not_called()
        self.assertEqual(self.stored_claims(E2_TASK)[0]['state'], 'UNKNOWN')

    def test_unknown_claim_with_responded_developer_names_recover_claim(self):
        # The repair Task finished with DEVELOPER_RESPONDED: verified recovery
        # accepts it, so the stop names --recover-claim.
        self.evidence_2(child_developer=_responded_developer())
        repair = Mock()
        result = self.advance(E2_TASK, _d6(self.d6_root, E2_EXECUTOR), repair,
                              review_outcomes=_review_fail_outcome())
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])
        self.assertEqual(result['result'], {
            'repair_task_id': E2_CHILD, 'claim_state': 'UNKNOWN',
            'next_command': (f'llmtool task repair {E2_TASK} --executor-task '
                             f'{E2_EXECUTOR} --pr 2340 --recover-claim {E2_CHILD}')})
        repair.assert_not_called()

    def test_unknown_claim_with_unknown_developer_stops_without_recover_claim(self):
        # F2: If the repair run ended in DEVELOPER_OUTCOME_UNKNOWN, verified
        # recovery refuses; advance provides an actionable stop without
        # naming a --recover-claim that would fail.
        self.evidence_2(child_developer=_unknown_developer('CONFIRMED'))
        repair = Mock()
        result = self.advance(E2_TASK, _d6(self.d6_root, E2_EXECUTOR), repair,
                              review_outcomes=_review_fail_outcome())
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])
        self.assertEqual(result['result'], {
            'repair_task_id': E2_CHILD, 'claim_state': 'UNKNOWN'})
        repair.assert_not_called()

    def test_test_failure_never_delegated_claim_is_replayed_not_retried(self):
        self.published_pr = 2340
        self.evidence_2(test_failure=True)
        repair = Mock(return_value={'attempt': 1, 'repair_task_id': E2_CHILD})
        result = self.advance(E2_TASK, _d6(self.d6_root, E2_EXECUTOR), repair)
        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertEqual(result['codes'], [])
        repair.assert_called_once()
        kwargs = repair.call_args.kwargs
        self.assertIsNone(kwargs['retry_claim_id'])
        self.assertEqual(kwargs['pr_number'], 2340)
        self.assertEqual(kwargs['remote_test_receipt']['source_sha'], HEAD)

    def test_test_failure_refused_replay_names_no_review_command(self):
        # `task repair` takes no EC2 receipt, so no command is named for '-ec2'.
        self.published_pr = 2340
        self.evidence_2(test_failure=True)
        repair = Mock(side_effect=LLMToolError('D6_SOURCE_BINDING_MISMATCH'))
        result = self.advance(E2_TASK, _d6(self.d6_root, E2_EXECUTOR), repair)
        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertEqual(result['codes'], ['TASK_ADVANCE_REFUSED', 'REPAIR_REPLAY_REFUSED'])
        self.assertEqual(result['result'],
                         {'repair_task_id': E2_CHILD, 'claim_state': 'UNKNOWN'})

    # -- decision table ---------------------------------------------------

    def test_unknown_claim_is_never_retried(self):
        self.evidence_2(child_developer=_unknown_developer('CONFIRMED'))
        d6 = _d6(self.d6_root, E2_EXECUTOR)
        child = self.store.get(E2_CHILD)
        for repair_state in ('UNAVAILABLE', 'BLOCKED', 'OUTPUT_REJECTED', 'UNKNOWN'):
            with self.subTest(repair_state=repair_state):
                claim = dict(self.stored_claims(E2_TASK)[0], repair_state=repair_state,
                             repair_record=child)
                self.assertEqual(task_advance._repair_claim_action(claim, d6),
                                 'REPAIR_OUTCOME_UNKNOWN')

    # -- review_repair: replayed claim state is reported as stored --------

    def test_replayed_claim_stays_unknown_when_recovery_is_refused(self):
        # The real store refuses the plain UNKNOWN -> DISPATCHED write; the
        # real recovery validator refuses too when the child ended in
        # DEVELOPER_OUTCOME_UNKNOWN, so UNKNOWN is reported as stored.
        self.evidence_2(child_developer=_unknown_developer('CONFIRMED'))
        claim = dict(self.stored_claims(E2_TASK)[0])
        state = review_repair._replayed_claim_state(
            self.store, E2_TASK, claim, executor_task=E2_EXECUTOR, pr_number=2340)
        self.assertEqual(state, 'UNKNOWN')
        claims = self.stored_claims(E2_TASK)
        self.assertEqual(len(claims), 1)
        self.assertEqual((claims[0]['state'], claims[0]['attempt']), ('UNKNOWN', 1))

    def test_replayed_claim_uses_the_verified_recovery(self):
        # When child has DEVELOPER_RESPONDED, real verified recovery succeeds.
        self.evidence_2(child_developer=_responded_developer(source_sha=HEAD))
        claim = dict(self.stored_claims(E2_TASK)[0])
        state = review_repair._replayed_claim_state(
            self.store, E2_TASK, claim, executor_task=E2_EXECUTOR, pr_number=2340)
        self.assertEqual(state, 'DISPATCHED')
        # No new claim: the replay is counted once.
        claims = self.stored_claims(E2_TASK)
        self.assertEqual(len(claims), 1)
        self.assertEqual((claims[0]['state'], claims[0]['attempt']), ('DISPATCHED', 1))

    # -- review_repair: dirty tree guard checks clean on real GitSnapshot -

    def test_dirty_d6_refuses_repair_review_with_real_gitsnapshot(self):
        # F3: GitSnapshot defines `clean`, not `dirty`. A dirty tree
        # (clean=False) must refuse with REPAIR_HEAD_MOVED.
        from llmtool.task_completion import GitSnapshot, D6Registration
        self.evidence_2()
        parent = self.store.get(E2_TASK)
        dirty_snapshot = GitSnapshot(
            root=self.d6_root, common_dir=COMMON_DIR, branch='fix-2325',
            head=HEAD, clean=False)
        reg = D6Registration(
            task_id=E2_EXECUTOR, root=self.d6_root, common_dir=COMMON_DIR,
            branch='fix-2325', source_sha=SOURCE, result_sha=HEAD,
            trusted_agent_id='pm-deterministic', session_ref='fixture-session',
            native_session_id='fixture-native-session', state='ACTIVE')
        with patch.object(review_repair.task_completion, 'read_d6_registration',
                          return_value=reg), \
                patch.object(review_repair.task_completion, 'collect_git_snapshot',
                             return_value=dirty_snapshot):
            with self.assertRaises(LLMToolError) as exc_info:
                review_repair._registered_d6(parent, E2_EXECUTOR)
            self.assertEqual(getattr(exc_info.exception, 'review_repair_code', None),
                             'REPAIR_HEAD_MOVED')


if __name__ == '__main__':
    unittest.main()
