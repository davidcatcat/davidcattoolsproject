"""#2793 A13.0 wiring (b): the CI profile drives CI repair packet binding.

R1: the default profile accepts and refuses exactly the names accepted before.
R2: packet building, real TaskStore persistence and re-read, normal claim
    verification and retry use the same effective CI profile resolution and the
    same accepted set.
R3: a name outside the effective accepted set is refused even when the claim
    digest matches; an unresolvable, invalid or ambiguous profile refuses and
    never silently uses another set.
R4: the TaskStore claim schema stays unchanged (no new claim fields); a claim
    written without any new field verifies as before.
T1: replaces the resolved profile through the real resolver with an isolated
    fixture, builds through review_repair.repair_review, persists and re-reads
    with the real TaskStore, then runs claim verification and
    run_task_retry_start. All text is plain prose (Owner #763 A).
"""

from __future__ import annotations

from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile, review_repair, start_failure  # noqa: E402
from llmtool import task_store, task_submission_cli as cli, worker_dispatch  # noqa: E402
from llmtool.ci_profile import CiProfile, CiProfileError, default_profile  # noqa: E402
from llmtool.developer_routes import RouteError  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_store import (  # noqa: E402
    TaskStore,
    _REVIEW_REPAIR_CLAIM_FIELDS,
    _REVIEW_REPAIR_OPTIONAL_FIELDS,
)

import worker_policy_fixture  # noqa: E402

HEAD = 'a' * 40
D6 = 'executor-ci-profile'
PR = 42
CONTRACT = 'd' * 64
PM_NATIVE_ID = '12345678-1234-1234-1234-123456789abc'
PM_SESSION = 'pm-' + PM_NATIVE_ID
ROOT_PATH = '/nonexistent/ci-profile-d6'
COMMON_PATH = '/nonexistent/ci-profile-common'
BRANCH = 'feat/ci-profile-repair'
WRITE = 'docs/ci_repair_note.md'
READ = ['docs/ci_repair_context.md', WRITE]
ACCEPTANCE = ['Repair the failed CI check.']
EGRESS = 'Plain project text only.'
DIFF = (
    'diff --git a/docs/ci_repair_note.md b/docs/ci_repair_note.md\n'
    '--- a/docs/ci_repair_note.md\n+++ b/docs/ci_repair_note.md\n'
    '@@ -1 +1 @@\n-Old note.\n+New note.\n'
)
CARD = '\nREQUIRED_SKILL_BEGIN path=card\nFixture rules.\nREQUIRED_SKILL_END path=card'
_UNSET = object()

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)

REGISTRATION = types.SimpleNamespace(
    task_id=D6,
    trusted_agent_id='claude-developer',
    session_ref='ci-profile-session',
    native_session_id=None,
    root=ROOT_PATH,
    common_dir=COMMON_PATH,
    branch=BRANCH,
    source_sha=HEAD,
)
SNAPSHOT = types.SimpleNamespace(
    head=HEAD,
    root=ROOT_PATH,
    common_dir=COMMON_PATH,
    branch=BRANCH,
)
ALLOWED_CLAUDE = worker_dispatch.PolicyDecision(
    'claude-developer', True, 'OWNER_POLICY_ALLOWED', 'e' * 64
)


def _sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class _Boundary:
    def prepare(self, root, paths, state_dirs=()):
        return ('fixture-policy', True)

    def verify(self, root, paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': _sha256('fixture boundary')}


class _Route:
    def __init__(self, error=None):
        self.error = error
        self.requests = []

    def __call__(self, route, request, *, gate, boundary, snapshot):
        self.requests.append(dict(request))
        if self.error is not None:
            raise RouteError(self.error)
        return types.SimpleNamespace(
            session_id='12345678-1234-1234-1234-123456789abd',
            exit_code=0,
            stdout_sha256=_sha256('ci retry output'),
            elapsed_ms=500,
            cleanup_status='CONFIRMED',
            usage=None,
            provider_evidence=None,
        )


def _default_checks():
    profile = default_profile()
    return (profile.linux_code_check, profile.docs_check, profile.gateway_check,
            profile.ai_loop_check, profile.macos_check)


def _alt_checks():
    return (ALT_PROFILE.linux_code_check, ALT_PROFILE.docs_check,
            ALT_PROFILE.gateway_check, ALT_PROFILE.ai_loop_check,
            ALT_PROFILE.macos_check)


def _failure(check_name, *, head=HEAD, run_id=9001):
    return {'head': head, 'check_name': check_name, 'check_run_id': run_id,
            'conclusion': 'failure', 'excerpt': 'FAIL: example check\nAssertionError'}


def _identity(check_name, *, task, executor=D6, head=HEAD, pr=PR, run_id=9001):
    value = {'kind': 'CI', 'task': task, 'pr': pr, 'executor_task': executor,
             'head': head, 'check_name': check_name, 'check_run_id': run_id,
             'conclusion': 'failure'}
    return _sha256(start_failure.canonical(value))


def _header(packet):
    return packet['reviewer_text_excerpt_untrusted_data'].split('\n', 1)[0]


def _task_record(task_id, *, with_developer=False):
    run_id = task_store.new_id()
    contract = cli._contract(task_id, _sha256('Repair the failed CI check.'))
    assignment = {
        'route': 'claude',
        'risk': 'LOW',
        'goal': 'Repair the failed CI check.',
        'acceptance': list(ACCEPTANCE),
    }
    response = json.dumps({
        'schema_version': 1,
        'summary': 'Dispatch bounded CI repair.',
        'developer_assignment': assignment,
    }, ensure_ascii=True, separators=(',', ':'))
    record = {
        'schema_version': 2,
        'task_id': task_id,
        'run_id': run_id,
        'pm_session_id': PM_SESSION,
        'pm_provider_profile': 'active-pm-runtime',
        'pm_provider_type': 'active-pm-runtime',
        'pm_model': None,
        'pm_route_binding': {'type': 'active-pm-runtime', 'model': None, 'base_url': None},
        'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
        'native_session_id': None,
        'state': 'PM_RESPONDED',
        'created_at': '2026-11-27T00:00:00Z',
        'updated_at': '2026-11-27T00:00:00Z',
        'task_contract': contract.to_dict(),
        'task_binding': contract.binding(run_id).to_dict(),
        'request': 'Repair the failed CI check.',
        'request_sha256': _sha256('Repair the failed CI check.'),
        'response': response,
        'usage': None,
        'error_code': None,
        'pm_assignment': {'schema_version': 1, **assignment},
        'pm_provenance': {
            'source': 'ACTIVE_PM_RUNTIME',
            'provider_invoked': False,
            'native_session_id_status': 'LOCAL_SESSION_FILE_OBSERVED',
            'pm_runtime': 'claude-code',
            'pm_native_session_id': PM_NATIVE_ID,
            'pm_cwd': '/nonexistent/pm',
            'usage_status': 'UNKNOWN',
            'session_restored': False,
            'automatic_callback': False,
        },
        'developer': None,
        'completion': None,
    }
    if with_developer:
        record['developer'] = {
            'assignment_id': task_store.new_id(),
            'selection_source': 'PM',
            'state': 'DEVELOPER_RESPONDED',
            'provider_profile': 'agy',
            'provider_type': 'agy-cli',
            'model': 'gemini-test',
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + task_store.new_id(),
            'source_sha': HEAD,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': sorted(READ),
            'write_paths': [WRITE],
            'acceptance': list(ACCEPTANCE),
            'context_sha256': _sha256('context'),
            'egress_basis': EGRESS,
            'input_bytes': 100,
            'max_tokens': None,
            'artifact_format': 'unified_diff',
            'artifact': DIFF,
            'artifact_sha256': _sha256(DIFF),
            'usage': None,
            'error_code': None,
            'execution': None,
            'owner_assignment_ref': None,
            'created_at': '2026-11-27T00:00:00Z',
        }
        record['completion'] = {
            'state': 'WAITING_REVIEW',
            'executor_task_id': D6,
            'reviewer_task_id': None,
            'evidence': {'common_dir': COMMON_PATH},
            'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED',
            'pm_response': None,
            'pm_recommendation': None,
            'pm_usage': None,
            'pm_error_code': None,
        }
    return record


class RealTaskStoreRepairAndRetryTests(unittest.TestCase):
    """Build through repair_review, persist/re-read with TaskStore, verify & retry."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self._stores = 0
        worker_policy_fixture.install(self)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patcher in (
                mock.patch.object(
                    worker_dispatch, 'evaluate',
                    side_effect=lambda worker, *_args, **_kw: worker_dispatch.PolicyDecision(
                        worker, True, 'OWNER_POLICY_ALLOWED', 'e' * 64)),
                mock.patch.object(review_repair.shutil, 'which',
                                  return_value='/nonexistent/bin/agy'),
                mock.patch.object(cli, '_delegate_d6_binding',
                                  return_value=(REGISTRATION, SNAPSHOT)),
                mock.patch.object(cli.task_completion, 'validate_task_binding',
                                  return_value=None),
                mock.patch.object(cli.task_completion, 'bind_executor_task',
                                  return_value=(REGISTRATION, SNAPSHOT)),
                mock.patch.object(cli.task_completion, 'read_d6_registration',
                                  return_value=REGISTRATION),
                mock.patch.object(cli.task_completion, 'collect_git_snapshot',
                                  return_value=SNAPSHOT),
                mock.patch.object(cli.task_completion, '_worktree_diff',
                                  return_value=([WRITE], DIFF.encode('utf-8'))),
                mock.patch.object(cli, '_delegate_timeout', return_value=600),
                mock.patch.object(cli, 'load_route_config',
                                  return_value={'routes': {'claude': {'denied_dirs': []}}}),
                mock.patch.object(cli, '_claude_developer_profile',
                                  return_value=('fixture-model', 'medium', '/nonexistent/config')),
                mock.patch.object(cli.shutil, 'which',
                                  return_value='/nonexistent/bin/claude'),
                mock.patch.object(cli, '_DiagnosedClaudeRoute',
                                  side_effect=lambda config: types.SimpleNamespace()),
                mock.patch.object(cli, 'for_platform', return_value=_Boundary()),
                mock.patch.object(cli, '_developer_card_prefix',
                                  return_value=(CARD, _sha256(CARD))),
                mock.patch.object(cli, '_record_skill_delivery', return_value=None),
                mock.patch.object(cli, 'resolve_route_max_diff_bytes', return_value=1 << 20),
                mock.patch.object(cli, 'run_task_direct_result_event', return_value=0),
                mock.patch.object(cli, '_task_view',
                                  side_effect=lambda record: {'task_id': record['task_id']}),
                mock.patch.object(cli, '_delegate_chain_gate',
                                  side_effect=lambda args, store, d6_binding, claim: claim()),
                mock.patch.object(cli, '_check_delegate_repair_cap', return_value=None),
                mock.patch.object(cli.worker_availability, 'quota_refusal', return_value=None),
                mock.patch('llmtool.pm_caller.check_caller', return_value=None),
                redirect_stdout(io.StringIO())):
            stack.enter_context(patcher)

    def _new_store(self):
        self._stores += 1
        store_dir = Path(self.temp.name) / f'store_{self._stores}'
        store_dir.mkdir(mode=0o700)
        os.chmod(store_dir, 0o700)
        return TaskStore(store_dir)

    def _build_and_persist_repair(self, store, check_name, *, profile=_UNSET):
        root_id = task_store.new_id()
        store.create(_task_record(root_id, with_developer=True))
        dispatched = []

        def adapter(**request):
            dispatched.append(request)
            return {'status': 'DISPATCHED', 'head': HEAD}

        def recorder(**request):
            created = store.create(_task_record(request['repair_task_id']))
            return created

        kwargs = {
            'store': store,
            'task_id': root_id,
            'executor_task': D6,
            'pr_number': PR,
            'github_get': lambda _path: [],
            'dispatch_adapter': adapter,
            'current_head': lambda _task, _executor: HEAD,
            'repair_task_recorder': recorder,
            'ci_failure': _failure(check_name),
        }
        if profile is not _UNSET:
            kwargs['profile'] = profile
        result = review_repair.repair_review(**kwargs)
        persisted_root = store.get(root_id)
        claim = persisted_root['review_repair_claims'][0]
        return root_id, result['repair_task_id'], dispatched[0]['packet'], persisted_root, claim

    def _fail_first_claude_start(self, store, repair_task_id, packet):
        args = Namespace(
            task_id=repair_task_id,
            provider='claude',
            mode='direct',
            selection_source='PM',
            executor_task=D6,
            read_path=list(READ),
            write_path=[WRITE],
            acceptance=list(ACCEPTANCE),
            egress_basis=EGRESS,
            owner_ref=None,
            repair_packet=packet,
            context_stdin=False,
            timeout=None,
        )
        route = _Route(error='CLAUDE_CONFIG_INVALID')
        rc = cli.run_task_claude_delegate(args, store=store, route_runner=route)
        self.assertEqual(rc, 1)

    def _retry_start(self, store, repair_task_id):
        route = _Route()
        rc = cli.run_task_retry_start(
            Namespace(task_id=repair_task_id, executor_task=D6, pm_session=PM_SESSION),
            store=store,
            route_runner=route,
            d6_unchanged=lambda source_sha, write_paths=None: source_sha == HEAD,
        )
        return rc, route

    def test_default_profile_builds_persists_verifies_and_retries_without_new_claim_fields(self):
        store = self._new_store()
        name = _default_checks()[0]
        root_id, repair_task_id, packet, persisted_root, claim = (
            self._build_and_persist_repair(store, name)
        )
        self.assertNotIn('ci_check_names', claim)
        self.assertTrue(set(claim) <= (_REVIEW_REPAIR_CLAIM_FIELDS | _REVIEW_REPAIR_OPTIONAL_FIELDS))
        self.assertTrue(start_failure.repair_packet_matches(
            packet, claim, persisted_root, store))

        self._fail_first_claude_start(store, repair_task_id, packet)
        rc, route = self._retry_start(store, repair_task_id)
        self.assertEqual(rc, 0)
        self.assertEqual(len(route.requests), 1)
        self.assertEqual(store.get(root_id)['review_repair_claims'][0], claim)

    def test_substituted_profile_via_real_resolver_builds_persists_verifies_and_retries(self):
        store = self._new_store()
        name = _alt_checks()[0]
        with mock.patch.object(ci_profile, 'default_profile',
                               return_value=ALT_PROFILE):
            root_id, repair_task_id, packet, persisted_root, claim = (
                self._build_and_persist_repair(store, name)
            )
            self.assertNotIn('ci_check_names', claim)
            self.assertTrue(start_failure.repair_packet_matches(
                packet, claim, persisted_root, store))

            self._fail_first_claude_start(store, repair_task_id, packet)
            rc, route = self._retry_start(store, repair_task_id)
            self.assertEqual(rc, 0)
            self.assertEqual(len(route.requests), 1)
            self.assertEqual(store.get(root_id)['review_repair_claims'][0], claim)

    def test_out_of_set_name_with_matching_digest_refuses_in_verifier_and_retry(self):
        store = self._new_store()
        forged_name = _alt_checks()[0]
        with mock.patch.object(ci_profile, 'default_profile',
                               return_value=ALT_PROFILE):
            root_id, repair_task_id, forged_packet, _persisted_root, claim = (
                self._build_and_persist_repair(store, forged_name)
            )
        reread_root = store.get(root_id)
        reread_claim = reread_root['review_repair_claims'][0]
        self.assertEqual(
            reread_claim['packet_sha256'],
            _identity(forged_name, task=root_id, executor=D6, head=HEAD, pr=PR),
        )

        self.assertFalse(start_failure.repair_packet_matches(
            forged_packet, reread_claim, reread_root, store))

        self._fail_first_claude_start(store, repair_task_id, forged_packet)
        with self.assertRaises(LLMToolError) as caught:
            self._retry_start(store, repair_task_id)
        self.assertTrue(str(caught.exception).startswith(
            'START_RETRY_REPAIR_PACKET_MISMATCH'))


class R1DefaultProfileTests(unittest.TestCase):
    def test_default_names_build_and_bind_exactly_as_before(self):
        root_id = task_store.new_id()
        root = {'task_id': root_id}
        for name in _default_checks():
            with self.subTest(check=name):
                packet, identity = review_repair._ci_failure(
                    root, executor_task=D6, pr_number=PR,
                    ci_failure=_failure(name))
                self.assertEqual(
                    _header(packet),
                    f'CI_CHECK_FAILURE check={name} run=9001 '
                    f'conclusion=failure head={HEAD}')
                self.assertEqual(identity, _identity(name, task=root_id))
                claim = {'executor_task': D6, 'pr': PR, 'head_sha': HEAD,
                         'version': 1, 'attempt': 1, 'family': f'pr-{PR}-ec2',
                         'state': 'DISPATCHED', 'repair_task_id': '2' * 32,
                         'comment_id': None, 'packet_sha256': identity,
                         'claimed_at': '2026-11-27T00:00:00Z'}
                self.assertTrue(start_failure.repair_packet_matches(packet, claim, root))

    def test_default_profile_refuses_names_it_does_not_carry(self):
        root = {'task_id': task_store.new_id()}
        for name in _alt_checks():
            with self.subTest(check=name):
                with self.assertRaises(LLMToolError) as caught:
                    review_repair._ci_failure(
                        root, executor_task=D6, pr_number=PR,
                        ci_failure=_failure(name))
                self.assertEqual(caught.exception.review_repair_code,
                                 'CI_FAILURE_INVALID')


class R2SubstitutedProfileTests(unittest.TestCase):
    def test_substituted_names_build_and_bind_and_original_names_are_refused(self):
        root_id = task_store.new_id()
        root = {'task_id': root_id}
        for name in _alt_checks():
            with self.subTest(check=name):
                with mock.patch.object(ci_profile, 'default_profile',
                                       return_value=ALT_PROFILE):
                    packet, identity = review_repair._ci_failure(
                        root, executor_task=D6, pr_number=PR,
                        ci_failure=_failure(name))
                    claim = {'executor_task': D6, 'pr': PR, 'head_sha': HEAD,
                             'version': 1, 'attempt': 1, 'family': f'pr-{PR}-ec2',
                             'state': 'DISPATCHED', 'repair_task_id': '2' * 32,
                             'comment_id': None, 'packet_sha256': identity,
                             'claimed_at': '2026-11-27T00:00:00Z'}
                    self.assertTrue(start_failure.repair_packet_matches(
                        packet, claim, root))
                self.assertTrue(start_failure.repair_packet_matches(
                    packet, claim, root, profile=ALT_PROFILE))

        for name in _default_checks():
            with self.subTest(original=name):
                with mock.patch.object(ci_profile, 'default_profile',
                                       return_value=ALT_PROFILE):
                    with self.assertRaises(LLMToolError) as caught:
                        review_repair._ci_failure(
                            root, executor_task=D6, pr_number=PR,
                            ci_failure=_failure(name))
                self.assertEqual(caught.exception.review_repair_code,
                                 'CI_FAILURE_INVALID')


class R3InvalidProfileTests(unittest.TestCase):
    def _bad_profiles(self):
        mutated = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='m.yml',
        )
        object.__setattr__(mutated, 'macos_check', '')
        bad_path = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='dir/m.yml',
        )
        return (
            ('missing_none', None),
            ('invalid_str', 'llmtool-mac-ci.yml'),
            ('invalid_dict', {'macos_check': 'check (macOS 3.13)'}),
            ('invalid_namespace', types.SimpleNamespace(macos_check='check (macOS 3.13)')),
            ('mutated_empty', mutated),
            ('path_separator', bad_path),
            ('ambiguous_list', [ALT_PROFILE, default_profile()]),
            ('ambiguous_tuple', (ALT_PROFILE,)),
        )

    def test_direct_and_default_bad_profile_refuses_with_fixed_code(self):
        root = {'task_id': task_store.new_id()}
        name = _default_checks()[0]
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                with self.assertRaises(LLMToolError) as caught:
                    review_repair._ci_failure(
                        root, executor_task=D6, pr_number=PR,
                        ci_failure=_failure(name), profile=bad)
                self.assertEqual(caught.exception.review_repair_code,
                                 'CI_REPAIR_PROFILE_INVALID')
                with mock.patch.object(ci_profile, 'default_profile',
                                       return_value=bad):
                    with self.assertRaises(LLMToolError) as caught_default:
                        review_repair._ci_failure(
                            root, executor_task=D6, pr_number=PR,
                            ci_failure=_failure(name))
                    self.assertEqual(caught_default.exception.review_repair_code,
                                     'CI_REPAIR_PROFILE_INVALID')

        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(ci_profile, 'default_profile',
                                       side_effect=exc):
                    with self.assertRaises(LLMToolError) as caught_exc:
                        review_repair._ci_failure(
                            root, executor_task=D6, pr_number=PR,
                            ci_failure=_failure(name))
                    self.assertEqual(caught_exc.exception.review_repair_code,
                                     'CI_REPAIR_PROFILE_INVALID')

    def test_bad_or_missing_profile_never_binds_a_packet(self):
        root_id = task_store.new_id()
        root = {'task_id': root_id}
        name = _default_checks()[0]
        packet, identity = review_repair._ci_failure(
            root, executor_task=D6, pr_number=PR, ci_failure=_failure(name))
        claim = {'executor_task': D6, 'pr': PR, 'head_sha': HEAD,
                 'version': 1, 'attempt': 1, 'family': f'pr-{PR}-ec2',
                 'state': 'DISPATCHED', 'repair_task_id': '2' * 32,
                 'comment_id': None, 'packet_sha256': identity,
                 'claimed_at': '2026-11-27T00:00:00Z'}
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                self.assertFalse(start_failure.repair_packet_matches(
                    packet, claim, root, profile=bad))
                with mock.patch.object(ci_profile, 'default_profile',
                                       return_value=bad):
                    self.assertFalse(start_failure.repair_packet_matches(
                        packet, claim, root))
        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(ci_profile, 'default_profile',
                                       side_effect=exc):
                    self.assertFalse(start_failure.repair_packet_matches(
                        packet, claim, root))


if __name__ == '__main__':
    unittest.main()
