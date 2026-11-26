"""#1886: a legal PM continuation repair completes hand-off; every check stays.

A PM continuation repair is a Task recorded with ``continues`` and delegated
into the PR Task's D6. Completion used to build the artifact chain from the
root Task's review repair claims only, so the continuation's artifact was
missing: PR #2407 (no claims) refused DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH
and PR #2402 (a later claim, the earlier continuation unrecorded) refused
DEVELOPER_ARTIFACT_CHAIN_INVALID. A Claude record-only root ignored its
continuations, so its result stayed bound to the earlier HEAD and refused
CLAUDE_RESULT_BINDING_INVALID. A Claude record-only continuation has no
artifact, only a recorded result HEAD, and refused DEVELOPER_ARTIFACT_CHAIN_INVALID
under either root route (review v2 F1); it now binds that recorded HEAD. A
continuation is admitted only on an ALLOWED
dispatch for its own provider route. These tests run the real chain builder
and the real D6 artifact check on a real Git D6. The Task store is an
in-memory double with the two reads completion uses; it has no write method.
"""
import copy
import hashlib
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import task_completion
from llmtool import task_submission_cli as cli

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import review_trust_fixture  # noqa: E402


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


CHAIN_INVALID = 'DEVELOPER_ARTIFACT_CHAIN_INVALID'
TREE_MISMATCH = 'DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH'
CLAUDE_BINDING = 'CLAUDE_RESULT_BINDING_INVALID'
AGENT = 'executor-agent'
# The worker key of each provider route. worker-dispatch.json differs between
# hosts, so the route lookup is injected; the dispatch check itself is real.
ROUTE_WORKERS = {'codex': 'route-codex', 'opencode': 'route-opencode',
                 'agy': 'route-agy', 'claude': 'route-claude'}

# Task id prefixes follow the Tasks named in the #1886 evidence.
ROOT_2407 = '9f546b44' + '0' * 24
CONT_2407_FIRST = 'ac93085a' + '0' * 24
CONT_2407_SECOND = 'd4335631' + '0' * 24
ROOT_2402 = '9c043e90' + '0' * 24
CONT_2402 = '5c8e55d0' + '0' * 24
CLAIM_REPAIR_2402 = 'e2402000' + '0' * 24


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _hex32(label):
    return _digest(label)[:32]


class _Store:
    """The two TaskStore reads completion makes; nothing here can write."""

    def __init__(self, *records):
        self._records = {record['task_id']: copy.deepcopy(record) for record in records}

    def get(self, task_id):
        if task_id not in self._records:
            raise LLMToolError('task not found')
        return copy.deepcopy(self._records[task_id])

    def completion_d6_task_ids(self, executor_task_id):
        return sorted(task_id for task_id, record in self._records.items()
                      if type(record.get('completion')) is dict and
                      record['completion'].get('executor_task_id') == executor_task_id)

    def snapshot(self):
        return copy.deepcopy(self._records)


class ContinuationArtifactChainTests(unittest.TestCase):
    def setUp(self):
        review_trust_fixture.install_for_case(self)
        route_lookup = patch.object(cli.worker_dispatch, 'worker_key_for',
                                    side_effect=ROUTE_WORKERS.get)
        route_lookup.start()
        self.addCleanup(route_lookup.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'executor'
        self.root.mkdir(mode=0o700)
        self._git('init', '-b', 'feat/executor')
        self._git('config', 'user.name', 'Executor')
        self._git('config', 'user.email', 'executor@example.invalid')
        self.path = 'tools/llmtool/tests/test_fixture.py'
        source = self.root / self.path
        source.parent.mkdir(parents=True)
        (self.root / 'tools' / 'llmtool' / '.gitignore').write_text(
            '/.d6-bootstrap-report.json\n')
        source.write_text('import unittest\nclass Fixture(unittest.TestCase):\n'
                          '    def test_ok(self): self.assertTrue(True)\n')
        self._git('add', self.path, 'tools/llmtool/.gitignore')
        self._git('commit', '-m', 'source')
        self.source_sha = self._git('rev-parse', 'HEAD')
        self.common = str(self.root / '.git')
        (Path(self.common) / 'llmtool-worktrees').mkdir(mode=0o700)
        self.d6 = 'executor-task'
        self._write_registration(self.source_sha)

    # -- D6 fixture ------------------------------------------------------

    def _git(self, *args):
        return subprocess.check_output(['/usr/bin/git', *args], cwd=self.root,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def _write_registration(self, head):
        self.root.chmod(0o700)
        root_stat = self.root.stat()
        value = {'schema_version': 1, 'task_id': self.d6, 'trusted_agent_id': AGENT,
                 'session_ref': 'Executor D6 session', 'root_realpath': str(self.root),
                 'physical_identity': {'device': root_stat.st_dev, 'inode': root_stat.st_ino,
                                       'uid': root_stat.st_uid,
                                       'mode': oct(stat.S_IMODE(root_stat.st_mode))},
                 'git_common_dir': self.common, 'branch': 'feat/executor',
                 'base_sha': self.source_sha, 'head_sha': head, 'state': 'ACTIVE',
                 'lock_state': 'LOCKED', 'native_session_id': 'native-executor'}
        report_path = self.root / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
        report = {'schema_version': 1, 'result': 'PASS', 'status': 'env_reused',
                  'task_id': self.d6, 'root_realpath': str(self.root), 'head_sha': head,
                  'tooling_sha': self.source_sha,
                  'cache_fingerprint': 'cpython3.13-darwin-arm64-012345abcdef'}
        report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':')) +
                        '\n').encode()
        report_path.write_bytes(report_bytes)
        report_path.chmod(0o600)
        value['env2_pass_marker'] = {
            'schema_version': 1, 'result': 'PASS', 'task_id': self.d6,
            'root_realpath': str(self.root), 'submitted_head_sha': head,
            'current_head_sha': head, 'tooling_sha': self.source_sha,
            'environment_fingerprint': report['cache_fingerprint'],
            'report_sha256': hashlib.sha256(report_bytes).hexdigest()}
        path = Path(self.common) / 'llmtool-worktrees' / (self.d6 + '.json')
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def _commit(self, text):
        target = self.root / self.path
        target.write_text(target.read_text() + text)
        self._git('add', self.path)
        self._git('commit', '-m', 'developer change')
        return self._git('rev-parse', 'HEAD')

    def _register_head(self, head):
        self._write_registration(head)
        return head

    def _artifact(self, start, end):
        return subprocess.check_output(['/usr/bin/git', 'diff', '--binary', start, end],
                                       cwd=self.root, text=True)

    # -- Task records ----------------------------------------------------

    def _root(self, task_id, artifact, claims=None, profile='codex'):
        run_id = _hex32('run ' + task_id)
        record = {
            'task_id': task_id, 'run_id': run_id,
            'task_binding': {'task_id': task_id, 'run_id': run_id,
                             'contract_digest': _digest('contract ' + task_id)},
            'developer': {'provider_profile': profile, 'state': 'DEVELOPER_RESPONDED',
                          'source_sha': self.source_sha, 'write_paths': [self.path],
                          'artifact': artifact, 'artifact_sha256': _digest(artifact)},
            'completion': {'executor_task_id': self.d6},
        }
        if claims is not None:
            record['review_repair_claims'] = claims
        return record

    def _claude_root(self, task_id, claude_head, *, executor=None):
        """A Claude record-only root: no artifact, a recorded Claude result head."""
        record = self._root(task_id, '', profile='claude')
        record['developer'].update(
            artifact=None, artifact_sha256=None,
            backend_session_semantics='NO_NATIVE_SESSION_ID_EXPOSED',
            claude_result={'executor_task_id': executor or self.d6,
                           'head_sha': claude_head,
                           'recorded_at': '2026-11-20T00:00:00Z'})
        return record

    def _audit(self, assignment, head, contract, *, worker=ROUTE_WORKERS['codex'],
               decision='ALLOWED', action='DISPATCH', label='transition',
               authorization=None):
        entry = {'attempt_id': _hex32(label + assignment), 'worker_key': worker,
                 'action': action, 'decision': decision, 'reason_code': 'ALLOWED',
                 'policy_sha256': None, 'at': '2026-11-20T00:00:00Z',
                 'assignment_id': assignment, 'source_sha': head,
                 'contract_digest': contract}
        if authorization is not None:
            entry['authorization_id'] = authorization
        return entry

    def _continuation(self, task_id, continues, head, artifact, *, profile='codex',
                      state='DEVELOPER_RESPONDED', audit=None, executor=None,
                      developer_executor=None, binding=None):
        run_id = _hex32('run ' + task_id)
        contract = _digest('contract ' + task_id)
        assignment = _hex32('assignment ' + task_id)
        developer = {'provider_profile': profile, 'state': state, 'source_sha': head,
                     'executor_task_id': developer_executor or self.d6,
                     'assignment_id': assignment, 'write_paths': [self.path],
                     'selection_source': 'PM'}
        if artifact is not None:
            developer.update(artifact=artifact, artifact_sha256=_digest(artifact))
        record = {
            'task_id': task_id, 'run_id': run_id,
            'task_binding': binding or {'task_id': task_id, 'run_id': run_id,
                                        'contract_digest': contract},
            'developer': developer,
            'completion': {'executor_task_id': executor or self.d6},
            'dispatch_audit': ([self._audit(assignment, head, contract,
                                            worker=ROUTE_WORKERS[profile])]
                               if audit is None else audit),
        }
        if continues is not None:
            record['continues'] = continues
        return record

    def _claim_repair(self, task_id, head, artifact):
        return {'task_id': task_id, 'run_id': _hex32('run ' + task_id),
                'developer': {'provider_profile': 'codex', 'state': 'DEVELOPER_RESPONDED',
                              'source_sha': head, 'artifact': artifact,
                              'artifact_sha256': _digest(artifact)},
                'completion': {'executor_task_id': self.d6}}

    @staticmethod
    def _claim(family, attempt, head, repair_id, pr=2402):
        return {'pr': pr, 'family': family, 'attempt': attempt, 'state': 'DISPATCHED',
                'head_sha': head, 'repair_task_id': repair_id}

    # -- completion steps ------------------------------------------------

    def _segments(self, root_record, store):
        return cli._completion_repair_artifact_segments(
            root_record, store, executor_task=self.d6, common_dir=self.common,
            root=str(self.root), branch='feat/executor', source_sha=self.source_sha)

    def _collect(self, root_record, segments, trusted=None):
        developer = root_record['developer']
        kwargs = dict(
            task_id=self.d6, expected_common_dir=self.common, expected_root=str(self.root),
            expected_branch='feat/executor', source_sha=self.source_sha,
            task_binding=root_record['task_binding'], expected_agent=AGENT,
            expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor',
            write_paths=developer['write_paths'], artifact=developer['artifact'],
            artifact_sha256=developer['artifact_sha256'],
            trusted_base_sha=trusted or self.source_sha)
        if segments is not None:
            kwargs['artifact_segments'] = segments
        return task_completion.collect_executor_result(**kwargs)

    def _complete(self, root_record, store):
        return self._collect(root_record, self._segments(root_record, store))

    def _refusal(self, root_record, store):
        """The completion code of the segment build or the D6 artifact check."""
        with self.assertRaises(LLMToolError) as raised:
            self._complete(root_record, store)
        return getattr(raised.exception, 'completion_code', None)

    @staticmethod
    def _claims_only(store):
        """The pre-#1886 view: a store read with no D6 scan sees the claims only."""
        return SimpleNamespace(get=store.get)

    def _main_is(self, sha):
        return patch.object(task_completion, '_github_get', return_value={
            'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': sha}})

    def _head_admitted(self, root_record, store, recorded, head):
        evidence = {'root': str(self.root), 'common_dir': self.common,
                    'branch': 'feat/executor', 'executor_identity': AGENT,
                    'executor_session_ref': 'Executor D6 session',
                    'executor_native_session_id': 'native-executor'}
        with self._main_is(self.source_sha):
            return cli.result_head_admitted(root_record, store, evidence,
                                            executor_task=self.d6, root=str(self.root),
                                            recorded_head=recorded, head=head)

    # -- fixtures of the two real failure shapes -------------------------

    def _pr_2407(self):
        """Root with no claims, then two chained PM continuation repairs."""
        original = self._commit('# original change\n')
        first = self._commit('# continuation repair one\n')
        head = self._register_head(self._commit('# continuation repair two\n'))
        root = self._root(ROOT_2407, self._artifact(self.source_sha, original))
        store = _Store(
            root,
            self._continuation(CONT_2407_FIRST, ROOT_2407, original,
                               self._artifact(original, first)),
            self._continuation(CONT_2407_SECOND, CONT_2407_FIRST, first,
                               self._artifact(first, head)))
        return root, store, (original, first, head)

    def _pr_2402(self):
        """A continuation repair the root does not record, then a claimed review repair."""
        original = self._commit('# original change\n')
        continued = self._commit('# continuation repair\n')
        head = self._register_head(self._commit('# review repair\n'))
        root = self._root(ROOT_2402, self._artifact(self.source_sha, original), claims=[
            self._claim('pr-2402', 1, continued, CLAIM_REPAIR_2402)])
        store = _Store(
            root,
            self._continuation(CONT_2402, ROOT_2402, original,
                               self._artifact(original, continued)),
            self._claim_repair(CLAIM_REPAIR_2402, continued,
                               self._artifact(continued, head)))
        return root, store, (original, continued, head)

    # -- A6: the real failure shapes, before and after -------------------

    def test_pr_2407_shape_refused_tree_mismatch_before_and_completes_now(self):
        root, store, (original, first, head) = self._pr_2407()
        self.assertIsNone(self._segments(root, self._claims_only(store)))
        self.assertEqual(self._refusal(root, self._claims_only(store)), TREE_MISMATCH)

        segments = self._segments(root, store)
        self.assertEqual([segment[2:] for segment in segments],
                         [(self.source_sha, original), (original, first), (first, head)])
        result = self._collect(root, segments)
        self.assertEqual(result['result_head'], head)

    def test_pr_2402_shape_refused_chain_invalid_before_and_completes_now(self):
        root, store, (original, continued, head) = self._pr_2402()
        before = self._segments(root, self._claims_only(store))
        # The original artifact was assigned the wrong end: the claim's HEAD.
        self.assertEqual([segment[2:] for segment in before],
                         [(self.source_sha, continued), (continued, head)])
        self.assertEqual(self._refusal(root, self._claims_only(store)), CHAIN_INVALID)

        segments = self._segments(root, store)
        self.assertEqual([segment[2:] for segment in segments],
                         [(self.source_sha, original), (original, continued),
                          (continued, head)])
        self.assertEqual(self._collect(root, segments)['result_head'], head)

    # -- A1: mixed review, CI/test and continuation repairs --------------

    def test_mixed_review_ec2_and_continuation_repairs_complete(self):
        original = self._commit('# original change\n')
        reviewed = self._commit('# review repair\n')
        continued = self._commit('# continuation repair\n')
        head = self._register_head(self._commit('# ec2 repair\n'))
        review_id, ec2_id = _hex32('review repair'), _hex32('ec2 repair')
        refused_id = _hex32('refused continuation')
        continuation_id = _hex32('continuation repair')
        root_id = _hex32('mixed root')
        root = self._root(root_id, self._artifact(self.source_sha, original), claims=[
            self._claim('pr-7', 1, original, review_id, pr=7),
            self._claim('pr-7-ec2', 1, continued, ec2_id, pr=7)])
        store = _Store(
            root,
            self._claim_repair(review_id, original, self._artifact(original, reviewed)),
            self._continuation(continuation_id, root_id, reviewed,
                               self._artifact(reviewed, continued)),
            # A refused continuation made no commit: it is skipped at its HEAD.
            self._continuation(refused_id, continuation_id, continued, None,
                               state='DEVELOPER_UNAVAILABLE'),
            self._claim_repair(ec2_id, continued, self._artifact(continued, head)))
        segments = self._segments(root, store)
        self.assertEqual([segment[2:] for segment in segments],
                         [(self.source_sha, original), (original, reviewed),
                          (reviewed, continued), (continued, head)])
        self.assertEqual(self._collect(root, segments)['result_head'], head)

    def _unlaunched_claim_repair(self, task_id, head):
        """A claimed repair Task whose Developer never ran (no artifact, no commit)."""
        return {'task_id': task_id, 'run_id': _hex32('run ' + task_id),
                'developer': {'provider_profile': 'codex', 'state': 'DEVELOPER_UNAVAILABLE',
                              'source_sha': head},
                'completion': {'executor_task_id': self.d6}}

    def test_continuation_of_a_claimed_repair_task_joins_the_chain(self):
        # 2026-11-27 (#2799 shape): review repair claim 1 never ran; a PM
        # continuation continues that claimed repair Task (not the root), then
        # a later review repair claim moves the head again.
        original = self._commit('# original change\n')
        continued = self._commit('# continuation of the claimed repair\n')
        head = self._register_head(self._commit('# second review repair\n'))
        unlaunched_id, second_id = _hex32('unlaunched repair'), _hex32('second repair')
        continuation_id = _hex32('continuation of claimed repair')
        root_id = _hex32('claimed repair root')
        root = self._root(root_id, self._artifact(self.source_sha, original), claims=[
            self._claim('pr-9', 1, original, unlaunched_id, pr=9),
            self._claim('pr-9', 2, continued, second_id, pr=9)])
        store = _Store(
            root,
            self._unlaunched_claim_repair(unlaunched_id, original),
            self._continuation(continuation_id, unlaunched_id, original,
                               self._artifact(original, continued)),
            self._claim_repair(second_id, continued, self._artifact(continued, head)))
        segments = self._segments(root, store)
        self.assertEqual([segment[2:] for segment in segments],
                         [(self.source_sha, original), (original, continued),
                          (continued, head)])
        self.assertEqual(self._collect(root, segments)['result_head'], head)

    def test_continuation_of_an_unclaimed_task_still_does_not_join(self):
        original = self._commit('# original change\n')
        continued = self._commit('# continuation of an unrelated task\n')
        head = self._register_head(self._commit('# second review repair\n'))
        unlaunched_id, second_id = _hex32('unlaunched repair b'), _hex32('second repair b')
        unrelated_id = _hex32('unrelated task b')
        root_id = _hex32('unclaimed root b')
        root = self._root(root_id, self._artifact(self.source_sha, original), claims=[
            self._claim('pr-9', 1, original, unlaunched_id, pr=9),
            self._claim('pr-9', 2, continued, second_id, pr=9)])
        unrelated = self._unlaunched_claim_repair(unrelated_id, original)
        unrelated['completion'] = {'executor_task_id': 'another-d6'}
        store = _Store(
            root,
            self._unlaunched_claim_repair(unlaunched_id, original),
            unrelated,
            self._continuation(_hex32('continuation of unrelated'), unrelated_id, original,
                               self._artifact(original, continued)),
            self._claim_repair(second_id, continued, self._artifact(continued, head)))
        self.assertEqual(self._refusal(root, store), CHAIN_INVALID)

    # -- A2: an artifact that differs from the result tree still refuses --

    def test_continuation_artifact_that_differs_from_its_commit_is_refused(self):
        root, store, (original, first, head) = self._pr_2407()
        records = store.snapshot()
        second = records[CONT_2407_SECOND]
        forged = second['developer']['artifact'].replace(
            '+# continuation repair two', '+# something the commit does not hold')
        self.assertNotEqual(forged, second['developer']['artifact'])
        second['developer'].update(artifact=forged, artifact_sha256=_digest(forged))
        self.assertEqual(self._refusal(root, _Store(*records.values())), CHAIN_INVALID)

    def test_unrecorded_commit_after_the_last_continuation_is_refused(self):
        root, store, _ = self._pr_2407()
        self._register_head(self._commit('# bare change with no record\n'))
        self.assertEqual(self._refusal(root, store), CHAIN_INVALID)

    def test_original_artifact_that_differs_from_its_commit_is_refused(self):
        root, store, (original, _, _) = self._pr_2407()
        forged = root['developer']['artifact'].replace(
            '+# original change', '+# not the original change')
        root['developer'].update(artifact=forged, artifact_sha256=_digest(forged))
        records = store.snapshot()
        records[ROOT_2407] = root
        self.assertEqual(self._refusal(root, _Store(*records.values())), CHAIN_INVALID)

    # -- A3: authorization, lineage, Task binding and HEAD ---------------

    def _with_second(self, store, first, **options):
        """The PR #2407 records with only the second continuation rebuilt."""
        records = store.snapshot()
        artifact = records[CONT_2407_SECOND]['developer']['artifact']
        records[CONT_2407_SECOND] = self._continuation(
            CONT_2407_SECOND, CONT_2407_FIRST, first, artifact, **options)
        return _Store(*records.values())

    def test_continuation_without_an_allowed_dispatch_is_refused(self):
        root, store, (_, first, head) = self._pr_2407()
        assignment = _hex32('assignment ' + CONT_2407_SECOND)
        contract = _digest('contract ' + CONT_2407_SECOND)
        # Control: the exact recorded authorization completes.
        self.assertEqual(self._complete(root, self._with_second(store, first))['result_head'],
                         head)
        audits = {
            'none': [],
            'blocked': [self._audit(assignment, first, contract, decision='BLOCKED')],
            'other worker': [self._audit(assignment, first, contract, worker='other-agent')],
            # F2: the D6's registered agent is not the continuation's route.
            'D6 registered agent': [self._audit(assignment, first, contract, worker=AGENT)],
            'another route': [self._audit(assignment, first, contract,
                                          worker=ROUTE_WORKERS['agy'])],
            'other assignment': [self._audit(_hex32('other'), first, contract)],
            'other head': [self._audit(assignment, self.source_sha, contract)],
            'other contract': [self._audit(assignment, first, _digest('other'))],
            'other action': [self._audit(assignment, first, contract, action='REASSIGN')],
        }
        for name, audit in audits.items():
            with self.subTest(audit=name):
                changed = self._with_second(store, first, audit=audit)
                self.assertEqual(self._refusal(root, changed), CHAIN_INVALID)

    def test_adapter_route_needs_its_adapter_authorization(self):
        task_id = CONT_2407_SECOND
        assignment = _hex32('assignment ' + task_id)
        contract = _digest('contract ' + task_id)
        root, store, (_, first, head) = self._pr_2407()
        opencode = ROUTE_WORKERS['opencode']
        transition = self._audit(assignment, first, contract, worker=opencode)
        records = store.snapshot()
        artifact = records[task_id]['developer']['artifact']
        only_transition = self._continuation(task_id, CONT_2407_FIRST, first, artifact,
                                             profile='opencode', audit=[transition])
        records[task_id] = only_transition
        self.assertEqual(self._refusal(root, _Store(*records.values())), CHAIN_INVALID)

        adapted = self._audit(assignment, first, contract, worker=opencode,
                              label='adapter', authorization=transition['attempt_id'])
        records[task_id] = self._continuation(task_id, CONT_2407_FIRST, first, artifact,
                                              profile='opencode',
                                              audit=[transition, adapted])
        self.assertEqual(self._complete(root, _Store(*records.values()))['result_head'],
                         head)

    def test_continuation_needs_the_authorization_of_its_own_route(self):
        """F2: an ALLOWED audit for another worker never admits the repair.

        The continuation is recorded as opencode. An agy (or D6 agent)
        transition with a matching adapter entry, assignment, start HEAD and
        contract is still not an opencode-developer authorization.
        """
        task_id = CONT_2407_SECOND
        assignment = _hex32('assignment ' + task_id)
        contract = _digest('contract ' + task_id)
        root, store, (_, first, head) = self._pr_2407()
        records = store.snapshot()
        artifact = records[task_id]['developer']['artifact']
        for name, worker in (('agy route', ROUTE_WORKERS['agy']), ('D6 agent', AGENT),
                             ('codex route', ROUTE_WORKERS['codex'])):
            with self.subTest(worker=name):
                transition = self._audit(assignment, first, contract, worker=worker)
                adapted = self._audit(assignment, first, contract, worker=worker,
                                      label='adapter',
                                      authorization=transition['attempt_id'])
                changed = dict(records)
                changed[task_id] = self._continuation(
                    task_id, CONT_2407_FIRST, first, artifact, profile='opencode',
                    audit=[transition, adapted])
                self.assertEqual(self._refusal(root, _Store(*changed.values())),
                                 CHAIN_INVALID)
        # A route with no worker key has no authorization at all.
        with patch.object(cli.worker_dispatch, 'worker_key_for', return_value=None):
            self.assertEqual(self._refusal(root, store), CHAIN_INVALID)
        # Control: the same entries for the opencode worker complete.
        opencode = ROUTE_WORKERS['opencode']
        transition = self._audit(assignment, first, contract, worker=opencode)
        adapted = self._audit(assignment, first, contract, worker=opencode,
                              label='adapter', authorization=transition['attempt_id'])
        records[task_id] = self._continuation(task_id, CONT_2407_FIRST, first, artifact,
                                              profile='opencode',
                                              audit=[transition, adapted])
        self.assertEqual(self._complete(root, _Store(*records.values()))['result_head'],
                         head)

    def test_wrong_task_binding_is_refused(self):
        bindings = {
            'other task': {'task_id': _hex32('other task'),
                           'run_id': _hex32('run ' + CONT_2407_SECOND),
                           'contract_digest': _digest('contract ' + CONT_2407_SECOND)},
            'other run': {'task_id': CONT_2407_SECOND, 'run_id': _hex32('other run'),
                          'contract_digest': _digest('contract ' + CONT_2407_SECOND)},
        }
        root, store, (_, first, _) = self._pr_2407()
        for name, binding in bindings.items():
            with self.subTest(binding=name):
                changed = self._with_second(store, first, binding=binding)
                self.assertEqual(self._refusal(root, changed), CHAIN_INVALID)

    def test_wrong_head_and_wrong_developer_d6_are_refused(self):
        root, store, (original, first, head) = self._pr_2407()
        side = self._git('commit-tree', self._git('rev-parse', first + '^{tree}'),
                         '-p', self.source_sha, '-m', 'side line')
        records = store.snapshot()
        artifact = records[CONT_2407_SECOND]['developer']['artifact']
        cases = {
            'head on another line': dict(head=side),
            'head that is not the start': dict(head=original),
            'developer bound to another D6': dict(head=first,
                                                  developer_executor='other-d6-task'),
        }
        for name, options in cases.items():
            with self.subTest(case=name):
                changed = dict(records)
                changed[CONT_2407_SECOND] = self._continuation(
                    CONT_2407_SECOND, CONT_2407_FIRST, artifact=artifact, **options)
                self.assertEqual(self._refusal(root, _Store(*changed.values())),
                                 CHAIN_INVALID)

    def test_a_d6_and_sha_match_without_lineage_is_not_admitted(self):
        root, store, (original, first, head) = self._pr_2407()
        records = store.snapshot()
        cases = {
            'no continues link': None,
            'another lineage': _hex32('unrelated root'),
        }
        unrelated = {'task_id': _hex32('unrelated root'), 'run_id': _hex32('run x')}
        for name, continues in cases.items():
            with self.subTest(case=name):
                changed = {key: value for key, value in records.items()
                           if key != CONT_2407_FIRST}
                changed[CONT_2407_FIRST] = self._continuation(
                    CONT_2407_FIRST, continues, original,
                    records[CONT_2407_FIRST]['developer']['artifact'])
                changed[unrelated['task_id']] = unrelated
                # The second continuation's link now ends outside the root lineage.
                self.assertIsNone(self._segments(root, _Store(*changed.values())))
                self.assertEqual(self._refusal(root, _Store(*changed.values())),
                                 TREE_MISMATCH)

    def test_continuation_bound_to_another_d6_is_not_admitted(self):
        root, store, (original, first, head) = self._pr_2407()
        records = store.snapshot()
        for task_id in (CONT_2407_FIRST, CONT_2407_SECOND):
            records[task_id]['completion'] = {'executor_task_id': 'other-d6-task'}
        changed = _Store(*records.values())
        self.assertIsNone(self._segments(root, changed))
        self.assertEqual(self._refusal(root, changed), TREE_MISMATCH)

    def test_unreadable_or_cyclic_lineage_is_refused(self):
        root, store, _ = self._pr_2407()
        records = store.snapshot()
        missing = dict(records)
        missing[CONT_2407_SECOND] = dict(records[CONT_2407_SECOND],
                                         continues=_hex32('missing predecessor'))
        cyclic = dict(records)
        cyclic[CONT_2407_FIRST] = dict(records[CONT_2407_FIRST], continues=CONT_2407_SECOND)
        for name, changed in (('missing', missing), ('cycle', cyclic)):
            with self.subTest(case=name), self.assertRaises(LLMToolError) as raised:
                self._segments(root, _Store(*changed.values()))
            self.assertEqual(raised.exception.completion_code, CHAIN_INVALID)

    def test_failed_d6_scan_reads_the_claims_only_and_still_refuses(self):
        root, store, _ = self._pr_2407()

        def failed_scan(_executor_task_id):
            raise LLMToolError('store scan unavailable')

        scan_fails = SimpleNamespace(get=store.get, completion_d6_task_ids=failed_scan)
        self.assertIsNone(self._segments(root, scan_fails))
        self.assertEqual(self._refusal(root, scan_fails), TREE_MISMATCH)

    def test_continuation_that_moved_the_head_without_a_result_is_refused(self):
        original = self._commit('# original change\n')
        self._register_head(self._commit('# change with no recorded result\n'))
        root = self._root(ROOT_2407, self._artifact(self.source_sha, original))
        store = _Store(root, self._continuation(
            CONT_2407_FIRST, ROOT_2407, original, None, state='DEVELOPER_OUTCOME_UNKNOWN'))
        with self.assertRaises(LLMToolError) as raised:
            self._segments(root, store)
        self.assertEqual(raised.exception.completion_code, CHAIN_INVALID)

    # -- A4: records and the effective repair count ----------------------

    def test_building_the_chain_changes_no_record_and_counts_effective_repairs(self):
        root, store, _ = self._pr_2402()
        before = store.snapshot()
        segments = self._segments(root, store)
        self._collect(root, segments)
        self.assertEqual(store.snapshot(), before)
        # One continuation and one claimed review repair: two effective repairs.
        self.assertEqual(len(segments) - 1, 2)
        self.assertEqual(root['review_repair_claims'],
                         before[ROOT_2402]['review_repair_claims'])

    def test_skipped_no_commit_continuation_is_not_an_effective_repair(self):
        original = self._commit('# original change\n')
        head = self._register_head(self._commit('# continuation repair\n'))
        refused_id, repair_id = _hex32('refused'), _hex32('repair')
        root = self._root(ROOT_2407, self._artifact(self.source_sha, original))
        store = _Store(
            root,
            self._continuation(refused_id, ROOT_2407, original, None,
                               state='DEVELOPER_UNAVAILABLE'),
            self._continuation(repair_id, refused_id, original,
                               self._artifact(original, head)))
        segments = self._segments(root, store)
        self.assertEqual([segment[2:] for segment in segments],
                         [(self.source_sha, original), (original, head)])
        self.assertEqual(self._collect(root, segments)['result_head'], head)

    def test_continuation_never_delegated_adds_no_segment(self):
        head = self._register_head(self._commit('# original change\n'))
        root = self._root(ROOT_2407, self._artifact(self.source_sha, head))
        record = self._continuation(CONT_2407_FIRST, ROOT_2407, head, None)
        record['developer'] = None
        store = _Store(root, record)
        self.assertIsNone(self._segments(root, store))
        self.assertEqual(self._complete(root, store)['result_head'], head)

    # -- A5: evidence stays bound to the right Task, contract and HEAD ---

    def test_evidence_binds_the_root_task_contract_and_head(self):
        root, store, (_, _, head) = self._pr_2407()
        result = self._complete(root, store)
        self.assertEqual(result['task_id'], root['task_binding']['task_id'])
        self.assertEqual(result['run_id'], root['task_binding']['run_id'])
        self.assertEqual(result['executor_task_id'], self.d6)
        self.assertEqual(result['source_sha'], self.source_sha)
        self.assertEqual(result['result_head'], head)

    def test_continuation_head_admits_review_and_test_evidence_only_with_its_chain(self):
        root, store, (original, _, head) = self._pr_2407()
        self.assertTrue(self._head_admitted(root, store, original, head))
        self.assertFalse(self._head_admitted(root, self._claims_only(store), original, head))
        bare = self._register_head(self._commit('# bare change with no record\n'))
        self.assertFalse(self._head_admitted(root, store, original, bare))

    # -- F1: a Claude-authored root with PM continuation repairs ---------

    def _claude_2407(self):
        """The PR #2407 shape under a Claude record-only root Task."""
        original = self._commit('# original change\n')
        first = self._commit('# continuation repair one\n')
        head = self._register_head(self._commit('# continuation repair two\n'))
        root = self._claude_root(ROOT_2407, original)
        store = _Store(
            root,
            self._continuation(CONT_2407_FIRST, ROOT_2407, original,
                               self._artifact(original, first)),
            self._continuation(CONT_2407_SECOND, CONT_2407_FIRST, first,
                               self._artifact(first, head)))
        return root, store, (original, first, head)

    def _collect_claude(self, root_record, segments, trusted=None):
        developer = root_record['developer']
        kwargs = dict(
            task_id=self.d6, expected_common_dir=self.common, expected_root=str(self.root),
            expected_branch='feat/executor', source_sha=self.source_sha,
            task_binding=root_record['task_binding'], expected_agent=AGENT,
            expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor',
            write_paths=developer['write_paths'],
            claude_result=developer['claude_result'],
            trusted_base_sha=trusted or self.source_sha)
        if segments is not None:
            kwargs['artifact_segments'] = segments
        return task_completion.collect_claude_result(**kwargs)

    def _claude_refusal(self, root_record, store):
        with self.assertRaises(LLMToolError) as raised:
            self._collect_claude(root_record, self._segments(root_record, store))
        return getattr(raised.exception, 'completion_code', None)

    def test_claude_root_continuation_refused_before_and_completes_at_its_later_head(self):
        root, store, (original, first, head) = self._claude_2407()
        # Before #1886 F1: the continuations were not read, so the Claude result
        # bound to the earlier HEAD refused the later D6 head.
        self.assertIsNone(self._segments(root, self._claims_only(store)))
        self.assertEqual(self._claude_refusal(root, self._claims_only(store)),
                         CLAUDE_BINDING)

        segments = self._segments(root, store)
        self.assertEqual(segments[0], (None, None, self.source_sha, original))
        self.assertEqual([segment[2:] for segment in segments],
                         [(self.source_sha, original), (original, first), (first, head)])
        result = self._collect_claude(root, segments)
        self.assertEqual(result['result_head'], head)
        self.assertEqual(result['task_id'], root['task_binding']['task_id'])
        self.assertEqual(result['run_id'], root['task_binding']['run_id'])
        self.assertEqual(result['source_sha'], self.source_sha)

    def test_claude_root_later_head_admits_review_and_test_evidence_only_with_its_chain(self):
        root, store, (original, _, head) = self._claude_2407()
        self.assertTrue(self._head_admitted(root, store, original, head))
        self.assertFalse(self._head_admitted(root, self._claims_only(store), original, head))
        bare = self._register_head(self._commit('# bare change with no record\n'))
        self.assertFalse(self._head_admitted(root, store, original, bare))

    def test_claude_root_chain_keeps_every_check(self):
        root, store, (original, first, head) = self._claude_2407()
        records = store.snapshot()
        # The Claude result must be the head the first continuation started on.
        moved = self._claude_root(ROOT_2407, first)
        moved_store = _Store(*{**records, ROOT_2407: moved}.values())
        self.assertEqual(self._claude_refusal(moved, moved_store), CLAUDE_BINDING)
        # A Claude result recorded for another D6 is not this chain's start.
        other = self._claude_root(ROOT_2407, original, executor='other-d6-task')
        with self.assertRaises(LLMToolError) as raised:
            self._segments(other, _Store(*{**records, ROOT_2407: other}.values()))
        self.assertEqual(raised.exception.completion_code, CHAIN_INVALID)
        # A continuation artifact that differs from its commits (A2).
        forged_records = store.snapshot()
        second = forged_records[CONT_2407_SECOND]
        forged = second['developer']['artifact'].replace(
            '+# continuation repair two', '+# something the commit does not hold')
        second['developer'].update(artifact=forged, artifact_sha256=_digest(forged))
        self.assertEqual(self._claude_refusal(root, _Store(*forged_records.values())),
                         CHAIN_INVALID)
        # A continuation authorized for another route (A3, F2).
        assignment = _hex32('assignment ' + CONT_2407_SECOND)
        contract = _digest('contract ' + CONT_2407_SECOND)
        wrong_route = self._with_second(store, first, audit=[
            self._audit(assignment, first, contract, worker=ROUTE_WORKERS['claude'])])
        self.assertEqual(self._claude_refusal(root, wrong_route), CHAIN_INVALID)
        # The chain segments are never accepted by the artifact-bound collector.
        with self.assertRaises(LLMToolError) as raised:
            self._collect(dict(root, developer=dict(
                root['developer'], artifact='', artifact_sha256=_digest(''))),
                self._segments(root, store))
        self.assertEqual(raised.exception.completion_code, CHAIN_INVALID)
        # An unrecorded commit after the last continuation (A2).
        self._register_head(self._commit('# bare change with no record\n'))
        self.assertEqual(self._claude_refusal(root, store), CHAIN_INVALID)
        self.assertEqual(store.snapshot(), records)

    # -- F1 (review v2): Claude record-only continuation repairs ---------
    #
    # A Claude record-only Developer records its result HEAD and keeps
    # artifact=None (TaskStore.record_claude_result). As a continuation repair
    # its segment used to carry artifact=None and refused CHAIN_INVALID under
    # either root route. It now binds its recorded result HEAD instead.

    def _claude_continuation(self, task_id, continues, head, recorded, *,
                             result_executor=None, **options):
        """A Claude record-only continuation: no artifact, a recorded result HEAD."""
        record = self._continuation(task_id, continues, head, None, profile='claude',
                                    **options)
        record['developer'].update(
            artifact=None, artifact_sha256=None,
            backend_session_semantics='NO_NATIVE_SESSION_ID_EXPOSED',
            claude_result={'executor_task_id': result_executor or self.d6,
                           'head_sha': recorded,
                           'recorded_at': '2026-11-20T00:00:00Z'})
        return record

    def _record_only_chain(self, root_route, routes):
        """The PR #2407 shape: a root, then two continuations of the given routes."""
        self._git('reset', '--hard', self.source_sha)
        original = self._commit('# original change\n')
        first = self._commit('# continuation repair one\n')
        head = self._register_head(self._commit('# continuation repair two\n'))
        root = (self._claude_root(ROOT_2407, original) if root_route == 'claude' else
                self._root(ROOT_2407, self._artifact(self.source_sha, original)))
        records = [root]
        links = ((CONT_2407_FIRST, ROOT_2407, original, first),
                 (CONT_2407_SECOND, CONT_2407_FIRST, first, head))
        for route, (task_id, continues, start, end) in zip(routes, links):
            records.append(
                self._claude_continuation(task_id, continues, start, end)
                if route == 'claude' else
                self._continuation(task_id, continues, start, self._artifact(start, end)))
        return root, _Store(*records), (original, first, head)

    def _route_collect(self, root_record, segments, trusted=None):
        if cli._binds_no_artifact(root_record):
            return self._collect_claude(root_record, segments, trusted)
        return self._collect(root_record, segments, trusted)

    def _route_refusal(self, root_record, store):
        with self.assertRaises(LLMToolError) as raised:
            self._route_collect(root_record, self._segments(root_record, store))
        return getattr(raised.exception, 'completion_code', None)

    def test_record_only_continuation_completes_under_both_root_routes(self):
        for root_route in ('codex', 'claude'):
            for routes in (('claude', 'claude'), ('artifact', 'claude'),
                           ('claude', 'artifact')):
                with self.subTest(root=root_route, continuations=routes):
                    root, store, (original, first, head) = self._record_only_chain(
                        root_route, routes)
                    before = store.snapshot()
                    segments = self._segments(root, store)
                    self.assertEqual([segment[2:4] for segment in segments],
                                     [(self.source_sha, original), (original, first),
                                      (first, head)])
                    for segment, route, end in zip(segments[1:], routes, (first, head)):
                        if route == 'claude':
                            # Bound to the HEAD its Task recorded, not to an artifact.
                            self.assertEqual(segment[:2] + segment[4:], (None, None, end))
                        else:
                            self.assertEqual(len(segment), 4)
                    result = self._route_collect(root, segments)
                    self.assertEqual(result['result_head'], head)
                    self.assertEqual(result['task_id'], root['task_binding']['task_id'])
                    self.assertEqual(result['run_id'], root['task_binding']['run_id'])
                    self.assertEqual(result['source_sha'], self.source_sha)
                    # A4: nothing is written; two effective repairs.
                    self.assertEqual(store.snapshot(), before)
                    self.assertEqual(len(segments) - 1, 2)

    def test_record_only_continuation_later_head_admits_evidence_only_with_its_chain(self):
        for root_route in ('codex', 'claude'):
            with self.subTest(root=root_route):
                root, store, (original, _, head) = self._record_only_chain(
                    root_route, ('claude', 'claude'))
                self.assertTrue(self._head_admitted(root, store, original, head))
                self.assertFalse(self._head_admitted(root, self._claims_only(store),
                                                     original, head))
                bare = self._register_head(self._commit('# bare change with no record\n'))
                self.assertFalse(self._head_admitted(root, store, original, bare))

    def test_record_only_review_repair_claim_joins_a_mixed_chain(self):
        original = self._commit('# original change\n')
        continued = self._commit('# continuation repair\n')
        head = self._register_head(self._commit('# review repair\n'))
        root = self._root(ROOT_2402, self._artifact(self.source_sha, original), claims=[
            self._claim('pr-2402', 1, continued, CLAIM_REPAIR_2402)])
        repair = self._claim_repair(CLAIM_REPAIR_2402, continued, '')
        repair['developer'].update(
            provider_profile='claude', artifact=None, artifact_sha256=None,
            backend_session_semantics='NO_NATIVE_SESSION_ID_EXPOSED',
            executor_task_id=self.d6,
            claude_result={'executor_task_id': self.d6, 'head_sha': head,
                           'recorded_at': '2026-11-20T00:00:00Z'})
        store = _Store(root, self._continuation(CONT_2402, ROOT_2402, original,
                                                self._artifact(original, continued)),
                       repair)
        segments = self._segments(root, store)
        self.assertEqual([segment[2:4] for segment in segments],
                         [(self.source_sha, original), (original, continued),
                          (continued, head)])
        self.assertEqual(segments[-1], (None, None, continued, head, head))
        self.assertEqual(self._collect(root, segments)['result_head'], head)

    def test_record_only_continuation_ending_in_a_clean_main_merge_completes(self):
        original = self._commit('# original change\n')
        recorded = self._commit('# continuation repair\n')
        self._git('branch', 'main', self.source_sha)
        self._git('checkout', 'main')
        (self.root / 'README.md').write_text('Main moved.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'trusted main change')
        trusted = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        self._git('merge', '--no-ff', '-m', 'merge main', 'main')
        head = self._register_head(self._git('rev-parse', 'HEAD'))
        for root_route in ('codex', 'claude'):
            with self.subTest(root=root_route):
                root = (self._claude_root(ROOT_2407, original) if root_route == 'claude'
                        else self._root(ROOT_2407,
                                        self._artifact(self.source_sha, original)))
                store = _Store(root, self._claude_continuation(
                    CONT_2407_FIRST, ROOT_2407, original, recorded))
                segments = self._segments(root, store)
                self.assertEqual(segments[-1], (None, None, original, head, recorded))
                result = self._route_collect(root, segments, trusted)
                self.assertEqual(result['result_head'], head)
                self.assertEqual(result['developer_changed_paths'], [self.path])

    def test_record_only_continuation_keeps_every_check(self):
        for root_route in ('codex', 'claude'):
            with self.subTest(root=root_route):
                self._assert_record_only_checks(root_route)

    def _assert_record_only_checks(self, root_route):
        root, store, (original, first, head) = self._record_only_chain(
            root_route, ('artifact', 'claude'))
        records = store.snapshot()
        side = self._git('commit-tree', self._git('rev-parse', head + '^{tree}'),
                         '-p', first, '-m', 'side line')
        assignment = _hex32('assignment ' + CONT_2407_SECOND)
        contract = _digest('contract ' + CONT_2407_SECOND)

        def second(recorded=head, **options):
            return self._claude_continuation(CONT_2407_SECOND, CONT_2407_FIRST, first,
                                             recorded, **options)

        no_result = second()
        del no_result['developer']['claude_result']
        with_artifact = second()
        artifact = self._artifact(first, head)
        with_artifact['developer'].update(artifact=artifact,
                                          artifact_sha256=_digest(artifact))
        absent_artifact = self._continuation(CONT_2407_SECOND, CONT_2407_FIRST, first, None)
        absent_artifact['developer'].update(artifact=None, artifact_sha256=None)
        cases = {
            'no recorded result': no_result,
            'result recorded for another D6': second(result_executor='other-d6-task'),
            'developer bound to another D6': second(developer_executor='other-d6-task'),
            'recorded HEAD is the start (no commit)': second(recorded=first),
            'recorded HEAD on another line': second(recorded=side),
            'recorded HEAD that is not hex': second(recorded='not-a-head'),
            'record-only result that also carries an artifact': with_artifact,
            # An absent artifact alone never admits a repair of another route.
            'codex repair with no artifact': absent_artifact,
            # A3 / F2: an ALLOWED dispatch for another route never admits it.
            'authorized for another route': second(audit=[self._audit(
                assignment, first, contract, worker=ROUTE_WORKERS['codex'])]),
            'no allowed dispatch': second(audit=[]),
            'wrong Task binding': second(binding={
                'task_id': _hex32('other task'), 'run_id': _hex32('run ' + CONT_2407_SECOND),
                'contract_digest': contract}),
        }
        for name, record in cases.items():
            with self.subTest(case=name):
                changed = _Store(*{**records, CONT_2407_SECOND: record}.values())
                self.assertEqual(self._route_refusal(root, changed), CHAIN_INVALID)
                self.assertEqual(changed.snapshot()[CONT_2407_SECOND], record)

        segments = self._segments(root, store)
        forged = {
            # Without its recorded HEAD the segment is an artifact segment and refuses.
            'binding dropped': segments[:-1] + [segments[-1][:4]],
            'artifact added to a recorded segment': segments[:-1] + [
                (artifact, _digest(artifact)) + segments[-1][2:]],
            'recorded HEAD replaced': segments[:-1] + [segments[-1][:4] + (side,)],
            'root segment made record-only': [segments[0] + (original,)] + segments[1:],
        }
        for name, changed in forged.items():
            with self.subTest(segments=name), self.assertRaises(LLMToolError) as raised:
                self._route_collect(root, changed)
            self.assertEqual(raised.exception.completion_code, CHAIN_INVALID)

        # A2: an unrecorded commit after the recorded HEAD.
        self._register_head(self._commit('# bare change with no record\n'))
        self.assertEqual(self._route_refusal(root, store), CHAIN_INVALID)
        self.assertEqual(store.snapshot(), records)


if __name__ == '__main__':
    unittest.main()
