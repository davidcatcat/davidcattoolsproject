import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import venv
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_MODULE_HOME = tempfile.TemporaryDirectory()
_IMPORT_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
_IMPORT_HOME_PATCH.start()
from llmtool import worker_dispatch
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record
from review_trust_fixture import start_module as _start_trust_fixture
from review_trust_fixture import stop_module as _stop_trust_fixture
import worker_policy_fixture
_IMPORT_HOME_PATCH.stop()


def setUpModule():
    _start_trust_fixture()
    global _MODULE_HOME_PATCH
    _MODULE_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
    _MODULE_HOME_PATCH.start()
    user_config = Path(_MODULE_HOME.name) / '.config' / 'llmtool' / 'config.yaml'
    user_config.parent.mkdir(parents=True)
    user_config.write_text('generation: {}\n', encoding='utf-8')


def tearDownModule():
    _MODULE_HOME_PATCH.stop()
    _stop_trust_fixture()
    _MODULE_HOME.cleanup()


class WorkerDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        # #1692: the shared all-ALLOWED test policy, never the live file.
        self.policy_path = worker_policy_fixture.install(self)
        self.policy = worker_policy_fixture.read(self.policy_path)
        self.repository_patch = patch.object(worker_dispatch, 'repository_config',
            return_value={'slug': 'davidcatcat/davidcattoolsproject',
                          'dispatch_issue': 289, 'dispatch_evidence_pr': 283})
        self.repository_patch.start()
        self.addCleanup(self.repository_patch.stop)
        self.store = TaskStore(self.root / 'tasks')
        self.task = self._task()
        self.store.create(self.task)

    def _task(self):
        request = 'Bounded non-sensitive Developer task.'
        task_id, run_id = new_id(), new_id()
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        value = _record(task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
                        state='PM_RESPONDED', provider_type='openai-compatible',
                        model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                        request_sha=request_sha)
        value.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM accepted a bounded request.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'company-gw', 'risk': 'LOW',
                           'goal': 'Add a focused test.', 'acceptance': ['the test passes']},
        )
        return value

    @staticmethod
    def developer(*, state='DEVELOPER_PENDING', provider='company-gw'):
        return {
            'assignment_id': new_id(), 'selection_source': 'PM', 'state': state,
            'provider_profile': provider, 'provider_type': None, 'model': None,
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['the test passes'], 'context_sha256': 'b' * 64,
            'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
            'max_tokens': 64, 'artifact_format': 'unified_diff', 'artifact': None,
            'artifact_sha256': None, 'usage': None, 'error_code': None,
            'created_at': datetime.now(timezone.utc).replace(microsecond=0).strftime(
                '%Y-%m-%dT%H:%M:%SZ'),
        }

    def _set_state(self, worker, state):
        worker_policy_fixture.set_dispatch(self.policy, worker, state)
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')

    def test_repository_reference_patterns_follow_validated_identity_and_escape_slug(self):
        owner_ref, evidence_ref = worker_dispatch._build_reference_patterns({
            'slug': 'example-org/repo.name',
            'dispatch_issue': 12,
            'dispatch_evidence_pr': 34,
        })
        self.assertIsNotNone(owner_ref.fullmatch(
            'https://github.com/example-org/repo.name/issues/12#issuecomment-7'))
        self.assertIsNotNone(evidence_ref.fullmatch(
            'https://github.com/example-org/repo.name/issues/12'))
        self.assertIsNotNone(evidence_ref.fullmatch(
            'https://github.com/example-org/repo.name/pull/34#issuecomment-8'))
        self.assertIsNone(owner_ref.fullmatch(
            'https://githubXcom/exampleXorg/repoXname/issues/12#issuecomment-7'))
        self.assertIsNone(owner_ref.fullmatch(
            'https://github.com/example-org/repoXname/issues/12#issuecomment-7'))
        self.assertIsNone(evidence_ref.fullmatch(
            'https://github.com/example-org/repo.name/pull/340'))
        self.assertIsNone(evidence_ref.fullmatch(
            'https://github.com/example-org/repo.name/issues/289'))

    def test_real_owner_policy_parses_and_validates(self):
        raw = Path(worker_dispatch._DEFAULT_POLICY_PATH).read_bytes()
        policy = worker_dispatch._parse_policy(raw)
        self.assertIsInstance(policy, dict)
        self.assertNotIn('owner_assignments', policy['workers']['agy'])
        self.assertNotIn('owner_assignments', policy['workers']['company-gw-agent'])
        gw_agent_entry = policy['workers']['company-gw-agent']
        self.assertEqual(gw_agent_entry['dispatch'], 'ALLOWED')
        self.assertEqual(gw_agent_entry['decided_by'], 'Owner')
        self.assertEqual(gw_agent_entry['decided_at'], '2026-11-10')
        self.assertEqual(gw_agent_entry['evidence'], worker_dispatch._GW_AGENT_OWNER_REF)
        with patch.object(worker_dispatch, '_POLICY_PATH',
                          Path(worker_dispatch._DEFAULT_POLICY_PATH)):
            decision = worker_dispatch.evaluate('agy')
            gw_agent_decision = worker_dispatch.evaluate('company-gw-agent')
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_POLICY_ALLOWED')
        self.assertTrue(gw_agent_decision.allowed)
        self.assertEqual(gw_agent_decision.reason_code, 'OWNER_POLICY_ALLOWED')

    def test_company_gw_agent_accepts_only_pending_pm_pause_or_exact_owner_allow(self):
        self.assertIn('company-gw-agent', worker_dispatch.WORKERS)
        self.assertEqual(worker_dispatch.worker_key_for('gw-agent'), 'company-gw-agent')
        # A PM-paused row validates and evaluates to OWNER_PAUSED.
        self._set_state('company-gw-agent', 'PAUSED')
        decision = worker_dispatch.evaluate('company-gw-agent')
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_PAUSED')

        worker = self.policy['workers']['company-gw-agent']
        owner_ref = ('https://github.com/davidcatcat/davidcattoolsproject/'
                     'issues/120#issuecomment-6760670167')
        old_ref = ('https://github.com/davidcatcat/davidcattoolsproject/'
                   'issues/120#issuecomment-6594101520')
        self.assertEqual(worker_dispatch._GW_AGENT_OWNER_REF, owner_ref)

        # The new row validates and evaluates to ALLOWED with no Owner assignment.
        worker.update(dispatch='ALLOWED', decided_by='Owner',
                      evidence=owner_ref)
        self.policy['workers']['company-gw-agent'].pop('owner_assignments', None)
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        new_row_decision = worker_dispatch.evaluate('company-gw-agent')
        self.assertTrue(new_row_decision.allowed)
        self.assertEqual(new_row_decision.reason_code, 'OWNER_POLICY_ALLOWED')

        # A row with the old ref is refused.
        worker.update(dispatch='ALLOWED', decided_by='Owner', evidence=old_ref)
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        old_ref_decision = worker_dispatch.evaluate('company-gw-agent')
        self.assertFalse(old_ref_decision.allowed)
        self.assertEqual(old_ref_decision.reason_code, 'WORKER_POLICY_INVALID')
        with self.assertRaises(ValueError) as caught:
            worker_dispatch._parse_policy(json.dumps(self.policy).encode('utf-8'))
        self.assertIn(str(caught.exception), (
            'invalid worker policy',
            'gw-agent requires the exact paused or Owner-authorized entry',
        ))

        # Other invalid variants fail closed.
        worker.update(decided_by='PM', evidence=owner_ref)
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw-agent').reason_code,
                         'WORKER_POLICY_INVALID')

        worker.update(decided_by='Owner', evidence=owner_ref + '0')
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw-agent').reason_code,
                         'WORKER_POLICY_INVALID')

        worker.update(dispatch='PAUSED', decided_by='Owner', evidence=owner_ref)
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw-agent').reason_code,
                         'WORKER_POLICY_INVALID')

        # A PM-paused row still validates.
        worker.update(dispatch='PAUSED', decided_by='PM',
                      evidence='https://github.com/davidcatcat/davidcattoolsproject/issues/120')
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        paused_decision = worker_dispatch.evaluate('company-gw-agent')
        self.assertFalse(paused_decision.allowed)
        self.assertEqual(paused_decision.reason_code, 'OWNER_PAUSED')
        parsed = worker_dispatch._parse_policy(json.dumps(self.policy).encode('utf-8'))
        self.assertEqual(parsed['workers']['company-gw-agent']['dispatch'], 'PAUSED')

    def test_owner_policy_states_are_read_from_the_exact_schema(self):
        self._set_state('opencode-developer', 'PAUSED')
        self.assertTrue(worker_dispatch.evaluate('codex-developer').allowed)
        self.assertTrue(worker_dispatch.evaluate('company-gw').allowed)
        self.assertTrue(worker_dispatch.evaluate('claude-reviewer').allowed)
        self.assertTrue(worker_dispatch.evaluate('codex-reviewer').allowed)
        self.assertTrue(worker_dispatch.evaluate('claude-developer').allowed)
        agy_decision = worker_dispatch.evaluate('agy')
        self.assertTrue(agy_decision.allowed)
        self.assertEqual(agy_decision.reason_code, 'OWNER_POLICY_ALLOWED')
        self._set_state('agy', 'PAUSED')
        self.assertEqual(worker_dispatch.evaluate('agy').reason_code, 'OWNER_PAUSED')
        self.assertEqual(worker_dispatch.evaluate('opencode-developer').reason_code,
                         'OWNER_PAUSED')
        self.assertFalse(worker_dispatch.evaluate('unknown-worker').allowed)

    def test_owner_assignment_only_stays_blocked_without_explicit_policy_row(self):
        self._set_state('agy', 'OWNER_ASSIGNMENT_ONLY')
        ref = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6351999848'
        self.policy['workers']['agy'].pop('owner_assignments', None)
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(self.policy['workers']['agy'].get('owner_assignments', []), [])
        decision = worker_dispatch.evaluate('agy', ref, self.task['task_id'])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_ASSIGNMENT_REQUIRED')

    def test_owner_assignment_requires_exact_task_reference_and_policy_allowlist(self):
        self._set_state('agy', 'OWNER_ASSIGNMENT_ONLY')
        ref = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-9999999999'
        task_id = self.task['task_id']
        self.assertEqual(worker_dispatch.evaluate('agy', ref, task_id).reason_code,
                         'OWNER_ASSIGNMENT_REQUIRED')
        assignment = {'task_id': task_id, 'owner_assignment_ref': ref}
        self.policy['workers']['agy']['owner_assignments'] = [assignment]
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertTrue(worker_dispatch.evaluate('agy', ref, task_id).allowed)
        self.assertEqual(worker_dispatch.evaluate('agy', ref, 'a' * 32).reason_code,
                         'OWNER_ASSIGNMENT_REQUIRED')
        self.assertEqual(worker_dispatch.evaluate('agy', ref + '0', task_id).reason_code,
                         'OWNER_ASSIGNMENT_REQUIRED')
        for suffix in ('?query=1', '/extra', '#fragment'):
            self.assertEqual(worker_dispatch.evaluate(
                'agy', ref + suffix, task_id).reason_code,
                'OWNER_ASSIGNMENT_REQUIRED')
        self.policy['workers']['opencode-developer']['dispatch'] = 'OWNER_ASSIGNMENT_ONLY'
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('opencode-developer', ref, task_id).reason_code,
                         'OWNER_ASSIGNMENT_REQUIRED')


    def test_agy_assignment_only_requires_exact_task_binding(self):
        # OWNER_ASSIGNMENT_ONLY remains exact-task scoped when selected by policy.
        self._set_state('agy', 'OWNER_ASSIGNMENT_ONLY')
        decision = worker_dispatch.evaluate('agy')
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_ASSIGNMENT_REQUIRED')
        ref = ('https://github.com/davidcatcat/davidcattoolsproject/'
               'issues/289#issuecomment-6433940134')
        task_id = self.task['task_id']
        self.assertEqual(
            worker_dispatch.evaluate('agy', ref, task_id).reason_code,
            'OWNER_ASSIGNMENT_REQUIRED')
        self.policy['workers']['agy']['owner_assignments'] = [
            {'task_id': task_id, 'owner_assignment_ref': ref}
        ]
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertTrue(worker_dispatch.evaluate('agy', ref, task_id).allowed)
        self.assertEqual(
            worker_dispatch.evaluate('agy', ref, 'a' * 32).reason_code,
            'OWNER_ASSIGNMENT_REQUIRED')
        self.assertEqual(
            worker_dispatch.evaluate('agy', None, task_id).reason_code,
            'OWNER_ASSIGNMENT_REQUIRED')

    def test_real_claude_developer_policy_allows_pm_selected_work(self):
        with patch.object(worker_dispatch, '_POLICY_PATH',
                          Path(worker_dispatch._DEFAULT_POLICY_PATH)):
            decision = worker_dispatch.evaluate('claude-developer')
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_POLICY_ALLOWED')
        real_policy = worker_dispatch._parse_policy(
            Path(worker_dispatch._DEFAULT_POLICY_PATH).read_bytes())
        worker = real_policy['workers']['claude-developer']
        self.assertEqual(worker['dispatch'], 'ALLOWED')
        self.assertNotIn('owner_assignments', worker)

    def test_owner_assignment_policy_rejects_duplicate_or_malformed_bindings(self):
        ref = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-9999999999'
        assignments = [{'task_id': self.task['task_id'], 'owner_assignment_ref': ref}]
        self.policy['workers']['claude-developer']['owner_assignments'] = assignments + assignments
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('claude-developer').reason_code,
                         'WORKER_POLICY_INVALID')

    def test_taskstore_persists_explicit_owner_assignment_reference(self):
        ref = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-9999999999'
        record = self._task()
        developer = self.developer(provider='claude')
        developer['owner_assignment_ref'] = ref
        record['developer'] = developer
        self.policy['workers']['claude-developer']['dispatch'] = 'OWNER_ASSIGNMENT_ONLY'
        self.policy['workers']['claude-developer']['owner_assignments'] = [{
            'task_id': record['task_id'], 'owner_assignment_ref': ref}]
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        saved = self.store.create(record)
        self.assertEqual(saved['developer']['owner_assignment_ref'], ref)
        self.assertEqual(saved['dispatch_audit'][0]['worker_key'], 'claude-developer')
        self.assertEqual(saved['dispatch_audit'][0]['reason_code'], 'OWNER_ASSIGNMENT_MATCHED')

    def test_policy_evidence_is_canonical_and_owner_label_does_not_grant_dispatch(self):
        self.policy['workers']['company-gw']['evidence'] = (
            'https://github.com/davidcatcat/davidcattoolsproject/issues/2890')
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'WORKER_POLICY_INVALID')
        self.policy['workers']['company-gw']['evidence'] = (
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289')
        self.policy['workers']['company-gw']['dispatch'] = 'OWNER_ASSIGNMENT_ONLY'
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'OWNER_ASSIGNMENT_REQUIRED')
        self.policy['workers']['company-gw']['decided_by'] = 'Worker'
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'WORKER_POLICY_INVALID')

    def test_source_policy_wins_and_conflicting_installed_copy_fails_closed(self):
        module = self.root / 'repo' / 'tools' / 'llmtool' / 'llmtool' / 'worker_dispatch.py'
        module.parent.mkdir(parents=True)
        module.write_text('# module fixture', encoding='utf-8')
        canonical = module.parent.parent / 'worker-dispatch.json'
        canonical.write_text(json.dumps(self.policy), encoding='utf-8')
        prefix = self.root / 'installed-prefix'
        installed = prefix / 'llmtool' / 'worker-dispatch.json'
        installed.parent.mkdir(parents=True)
        stale = json.loads(json.dumps(self.policy))
        stale['workers']['company-gw']['dispatch'] = 'PAUSED'
        installed.write_text(json.dumps(stale), encoding='utf-8')
        with patch.object(worker_dispatch, '__file__', str(module)), \
                patch.object(worker_dispatch.sysconfig, 'get_path',
                             return_value=str(prefix)), \
                patch.object(worker_dispatch, '_POLICY_PATH', canonical), \
                patch.object(worker_dispatch, '_DEFAULT_POLICY_PATH', canonical):
            self.assertEqual(worker_dispatch._policy_path(), canonical)
            decision = worker_dispatch.evaluate('company-gw')
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, 'WORKER_POLICY_AMBIGUOUS')

    def test_source_policy_resolves_to_the_single_canonical_repository_file(self):
        canonical = Path(__file__).resolve().parents[1] / 'worker-dispatch.json'
        self.assertTrue(os.path.samefile(worker_dispatch._policy_path(), canonical))
        self.assertFalse((Path(worker_dispatch.__file__).parent / 'worker-dispatch.json').exists())

    def test_policy_is_packaged_once_and_resolves_from_installed_wheel_and_sdist(self):
        project_root = Path(__file__).resolve().parents[1]
        expected_policy = (project_root / 'worker-dispatch.json').read_bytes()
        previous = os.getcwd()
        os.chdir(project_root)
        try:
            with tempfile.TemporaryDirectory() as output:
                output = Path(output)
                wheel_dir = output / 'wheel'
                install_dir = output / 'installed-venv'
                sdist_dir = output / 'sdist'
                isolated_cwd = output / 'cwd'
                for directory in (wheel_dir, install_dir, sdist_dir, isolated_cwd):
                    directory.mkdir()

                venv.EnvBuilder(with_pip=True).create(str(install_dir))
                if os.name == 'nt':
                    installed_python = install_dir / 'Scripts' / 'python.exe'
                else:
                    installed_python = install_dir / 'bin' / 'python'

                try:
                    import setuptools.build_meta  # noqa: F401
                except ImportError:
                    self.skipTest('run the package-install gate with local setuptools available')
                lookup_home = output / 'lookup-home'
                lookup_config = lookup_home / '.config' / 'llmtool' / 'config.yaml'
                lookup_config.parent.mkdir(parents=True)
                lookup_config.write_text('[invalid yaml', encoding='utf-8')
                subprocess_env = {**os.environ, 'HOME': str(lookup_home)}
                sdist_build = subprocess.run(
                    [sys.executable, '-B', '-c',
                     'import setuptools.build_meta as b, sys; print(b.build_sdist(sys.argv[1]))',
                     str(sdist_dir)],
                    cwd=project_root, env=subprocess_env, check=False, capture_output=True,
                    text=True, timeout=60)
                self.assertEqual(sdist_build.returncode, 0, sdist_build.stderr)
                sdist_name = sdist_build.stdout.strip().splitlines()[-1]
                sdist = sdist_dir / sdist_name
                with tarfile.open(sdist, 'r:gz') as archive:
                    sdist_members = [name for name in archive.getnames()
                                     if name.endswith('/worker-dispatch.json')]
                    self.assertEqual(len(sdist_members), 1)
                    self.assertEqual(archive.extractfile(sdist_members[0]).read(), expected_policy)

                built = subprocess.run(
                    [sys.executable, '-B', '-m', 'pip', 'wheel', '--no-build-isolation',
                     '--no-deps', '--wheel-dir', str(wheel_dir), str(sdist)],
                    cwd=isolated_cwd, env=subprocess_env, check=False, capture_output=True,
                    text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stderr)
                wheel = next(wheel_dir.glob('*.whl'))
                with zipfile.ZipFile(wheel) as archive:
                    members = [name for name in archive.namelist()
                               if name.endswith('/worker-dispatch.json')]
                    self.assertEqual(len(members), 1)
                    self.assertTrue(members[0].endswith(
                        '.data/data/llmtool/worker-dispatch.json'), members[0])
                    self.assertEqual(archive.read(members[0]), expected_policy)

                installed = subprocess.run(
                    [str(installed_python), '-B', '-m', 'pip', 'install', '--no-deps',
                     str(wheel)],
                    cwd=isolated_cwd, env=subprocess_env, check=False, capture_output=True,
                    text=True, timeout=60)
                self.assertEqual(installed.returncode, 0, installed.stderr)
                installed_policy = install_dir / 'llmtool' / 'worker-dispatch.json'
                self.assertTrue(installed_policy.is_file())
                self.assertEqual(installed_policy.read_bytes(), expected_policy)
                lookup_script = (
                    "import json, pathlib, sys; "
                    "from llmtool import worker_dispatch as w; "
                    "p=pathlib.Path(sys.prefix) / 'llmtool' / 'worker-dispatch.json'; "
                    "assert json.loads(p.read_text(encoding='utf-8'))['schema_version'] == 1; "
                    "assert w._POLICY_PATH.resolve() == p.resolve(), "
                    "(str(w._POLICY_PATH), sys.prefix, str(p)); "
                    "assert w.evaluate('codex-developer').reason_code == "
                    "'WORKER_REPOSITORY_CONFIG_INVALID'")
                lookup = subprocess.run(
                    [str(installed_python), '-B', '-I', '-c', lookup_script],
                    cwd=isolated_cwd, env=subprocess_env, check=False, capture_output=True,
                    text=True, timeout=10)
                self.assertEqual(lookup.returncode, 0, lookup.stderr)
        finally:
            os.chdir(previous)

    def test_invalid_user_repository_config_has_a_distinct_dispatch_denial(self):
        with patch.object(worker_dispatch, 'repository_config',
                          side_effect=LLMToolError('TRUST_PATH_INVALID')):
            self.assertTrue(all(worker_dispatch.evaluate(worker).reason_code ==
                                'WORKER_REPOSITORY_CONFIG_INVALID'
                                for worker in worker_dispatch.WORKERS))

    def test_trusted_repository_builds_both_patterns_without_owner_slug_fallback(self):
        repository = {'slug': 'example-org/custom-repo', 'dispatch_issue': 12,
                      'dispatch_evidence_pr': 34}
        owner_pattern, evidence_pattern = worker_dispatch._build_reference_patterns(repository)
        self.assertIsNotNone(owner_pattern.fullmatch(
            'https://github.com/example-org/custom-repo/issues/12#issuecomment-7'))
        self.assertIsNone(owner_pattern.fullmatch(
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-7'))
        self.assertIsNotNone(evidence_pattern.fullmatch(
            'https://github.com/example-org/custom-repo/pull/34'))
        self.assertIsNone(evidence_pattern.fullmatch(
            'https://github.com/davidcatcat/davidcattoolsproject/pull/283'))

    def test_policy_is_reloaded_after_each_change(self):
        self._set_state('opencode-developer', 'PAUSED')
        self.assertFalse(worker_dispatch.evaluate('opencode-developer').allowed)
        self._set_state('opencode-developer', 'ALLOWED')
        self.assertTrue(worker_dispatch.evaluate('opencode-developer').allowed)
        self._set_state('opencode-developer', 'PAUSED')
        self.assertFalse(worker_dispatch.evaluate('opencode-developer').allowed)

    def test_missing_malformed_duplicate_and_unknown_policy_fail_closed(self):
        self.policy_path.unlink()
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'WORKER_POLICY_MISSING')
        self.policy_path.write_text('{broken', encoding='utf-8')
        self.assertFalse(worker_dispatch.evaluate('company-gw').allowed)
        self.policy_path.write_text(
            '{"schema_version":1,"schema_version":1,"workers":{}}', encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'WORKER_POLICY_INVALID')
        self.policy['workers']['extra-worker'] = self.policy['workers']['company-gw']
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'WORKER_POLICY_INVALID')

    def test_codex_reviewer_switch_fails_closed(self):
        self._set_state('codex-reviewer', 'PAUSED')
        self.assertEqual(worker_dispatch.evaluate('codex-reviewer').reason_code,
                         'OWNER_PAUSED')
        self.assertTrue(worker_dispatch.evaluate('claude-reviewer').allowed)
        # A missing codex-reviewer entry is an invalid policy for every Worker.
        del self.policy['workers']['codex-reviewer']
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        for worker in ('codex-reviewer', 'claude-reviewer', 'codex-developer'):
            self.assertEqual(worker_dispatch.evaluate(worker).reason_code,
                             'WORKER_POLICY_INVALID')
        self.assertEqual(worker_dispatch.worker_key_for('codex-reviewer'), None)

    def test_taskstore_denies_direct_assignment_and_persists_blocked_audit(self):
        self._set_state('opencode-developer', 'PAUSED')
        developer = self.developer(provider='opencode')
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
            self.store.compare_and_update(
                self.task['task_id'],
                expected={'developer': None, 'state': 'PM_RESPONDED'},
                developer=developer)
        saved = self.store.get(self.task['task_id'])
        self.assertIsNone(saved['developer'])
        self.assertEqual(saved['dispatch_audit'][-1]['worker_key'], 'opencode-developer')
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'], 'OWNER_PAUSED')

    def test_allowed_taskstore_assignment_and_adapter_attempt_are_both_audited(self):
        developer = self.developer()
        saved = self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None, 'state': 'PM_RESPONDED'},
            developer=developer)
        dispatching = {**developer, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'openai-compatible', 'model': 'gw-model'}
        saved = self.store.update(self.task['task_id'], developer=dispatching)
        self.assertEqual([entry['decision'] for entry in saved['dispatch_audit']],
                         ['ALLOWED', 'ALLOWED'])
        transition = saved['dispatch_audit'][-1]
        decision = worker_dispatch.authorize_dispatch(
            self.store, self.task['task_id'], 'company-gw', 'DISPATCH')
        saved = self.store.get(self.task['task_id'])
        adapter = saved['dispatch_audit'][-1]
        self.assertTrue(decision.allowed)
        self.assertEqual(adapter['assignment_id'], developer['assignment_id'])
        self.assertEqual(adapter['source_sha'], developer['source_sha'])
        self.assertEqual(adapter['contract_digest'], self.task['task_binding']['contract_digest'])
        self.assertEqual(adapter['authorization_id'], transition['attempt_id'])
        self.assertEqual(adapter['decision'], 'ALLOWED')
        self._set_state('company-gw', 'PAUSED')
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            worker_dispatch.authorize_dispatch(
                self.store, self.task['task_id'], 'company-gw', 'DISPATCH')
        self.assertEqual(caught.exception.dispatch_code,
                         'WORKER_DISPATCH_AUTHORIZATION_CONSUMED')
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'],
                         'WORKER_DISPATCH_AUTHORIZATION_CONSUMED')

    def test_adapter_requires_a_durable_taskstore_dispatching_claim(self):
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            worker_dispatch.authorize_dispatch(
                self.store, self.task['task_id'], 'company-gw', 'DISPATCH')
        self.assertEqual(caught.exception.dispatch_code,
                         'WORKER_ASSIGNMENT_NOT_DISPATCHING')
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')

    def test_ambiguous_provider_outcome_cannot_be_replayed_through_taskstore(self):
        developer = self.developer(state='DEVELOPER_DISPATCHING')
        developer.update(provider_type='openai-compatible', model='gw-model')
        self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None}, developer=developer)
        unknown = {**developer, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                   'error_code': 'DEVELOPER_TIMEOUT'}
        self.store.update(self.task['task_id'], developer=unknown)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
            self.store.update(self.task['task_id'],
                developer={**unknown, 'state': 'DEVELOPER_DISPATCHING', 'error_code': None})
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'],
                         'WORKER_REPLAY_FORBIDDEN')

    def test_ambiguous_provider_outcome_cannot_reset_to_pending(self):
        developer = self.developer(state='DEVELOPER_DISPATCHING')
        developer.update(provider_type='openai-compatible', model='gw-model')
        self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None}, developer=developer)
        unknown = {**developer, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                   'error_code': 'DEVELOPER_TIMEOUT'}
        self.store.update(self.task['task_id'], developer=unknown)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
            self.store.update(self.task['task_id'],
                              developer={**unknown, 'state': 'DEVELOPER_PENDING'})
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'],
                         'WORKER_REPLAY_FORBIDDEN')

    def test_provider_identity_changes_require_a_new_reassignment(self):
        claude_pending = self.developer(provider='claude')
        claude_dispatching = {**claude_pending, 'state': 'DEVELOPER_DISPATCHING',
                              'provider_type': 'claude-cli', 'model': 'claude-model'}
        self.assertEqual(worker_dispatch.task_store_attempt(
            {'developer': claude_pending}, {'developer': claude_dispatching}),
            ('claude-developer', 'DISPATCH'))

        developer = self.developer()
        self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None}, developer=developer)
        for field, value in (
                ('provider_profile', 'opencode'), ('provider_type', 'codex-cli'),
                ('model', 'other-model'), ('source_sha', 'c' * 40),
                ('session_id', 'dev-new-session')):
            with self.subTest(field=field):
                replacement = {**developer, field: value}
                with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
                    self.store.update(self.task['task_id'], developer=replacement)
                self.assertEqual(caught.exception.dispatch_code,
                                 'WORKER_REASSIGN_PRECONDITION')
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.compare_and_update(
                self.task['task_id'], expected={'developer': developer},
                developer={**developer, 'provider_profile': 'opencode'})
        self.assertEqual(caught.exception.dispatch_code,
                         'WORKER_REASSIGN_NEW_ASSIGNMENT_REQUIRED')
        replacement = {**developer, 'assignment_id': 'e' * 32,
                       'provider_profile': 'opencode',
                       'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
                       'execution': None}
        self._set_state('opencode-developer', 'PAUSED')
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.compare_and_update(
                self.task['task_id'], expected={'developer': developer},
                developer=replacement)
        self.assertEqual(caught.exception.dispatch_code, 'OWNER_PAUSED')
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['developer']['provider_profile'], 'company-gw')
        self.assertEqual(saved['dispatch_audit'][-1]['worker_key'], 'opencode-developer')
        self.assertEqual(saved['dispatch_audit'][-1]['action'], 'REASSIGN')
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'], 'OWNER_PAUSED')

        self._set_state('opencode-developer', 'ALLOWED')
        saved = self.store.compare_and_update(
            self.task['task_id'], expected={'developer': developer},
            developer=replacement)
        self.assertEqual(saved['developer']['provider_profile'], 'opencode')
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['dispatch_audit'][-1]['worker_key'], 'opencode-developer')
        self.assertEqual(saved['dispatch_audit'][-1]['action'], 'REASSIGN')
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'ALLOWED')

    def test_gw_agent_served_model_binding_only_allows_a_valid_success_result(self):
        old = self.developer(state='DEVELOPER_DISPATCHING', provider='gw-agent')
        old.update(provider_type='gw-agent-cli', model='gw')
        new = {**old, 'state': 'DEVELOPER_RESPONDED', 'model': 'qwen3.8-27b',
               'error_code': None,
               'execution': {'status': 'DONE', 'process_exit_code': 0,
                             'cleanup_status': 'CONFIRMED'},
               'audit_sha256': 'a' * 64, 'audit_copy_path_sha256': 'b' * 64}

        self.assertFalse(worker_dispatch._developer_identity_changed(old, new))
        self.assertEqual(worker_dispatch.task_store_attempts(
            {'developer': old}, {'developer': new}), [])

        for field in worker_dispatch._DEVELOPER_IDENTITY_FIELDS:
            if field == 'model':
                continue
            with self.subTest(stable_field=field):
                changed = {**new, field: 'changed'}
                self.assertFalse(worker_dispatch._gw_agent_served_model_binding(
                    old, changed))
                self.assertTrue(worker_dispatch._developer_identity_changed(
                    old, changed))

        for field, value in (
                ('state', 'DEVELOPER_OUTCOME_UNKNOWN'),
                ('error_code', 'DEVELOPER_RESULT_UNKNOWN'),
                ('execution', {**new['execution'], 'status': 'FAILED'}),
                ('execution', {**new['execution'], 'process_exit_code': 1}),
                ('execution', {**new['execution'], 'cleanup_status': 'UNKNOWN'}),
                ('audit_sha256', None),
                ('audit_copy_path_sha256', 'not-a-digest')):
            with self.subTest(completion_evidence=(field, value)):
                changed = {**new, field: value}
                self.assertFalse(worker_dispatch._gw_agent_served_model_binding(
                    old, changed))
                self.assertTrue(worker_dispatch._developer_identity_changed(
                    old, changed))

    def test_audit_cannot_be_forged_by_generic_taskstore_update(self):
        with self.assertRaises(LLMToolError):
            self.store.update(self.task['task_id'], dispatch_audit=[])

    def test_unknown_audit_action_is_rejected_instead_of_coerced(self):
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            worker_dispatch.audit_entry(
                worker_dispatch.PolicyDecision('company-gw', True,
                                               'OWNER_POLICY_ALLOWED', 'a' * 64),
                'SOME_NEW_ACTION')
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_DISPATCH_ACTION_INVALID')

    def test_recovery_and_repair_are_distinct_gated_actions(self):
        developer = self.developer(state='DEVELOPER_UNAVAILABLE')
        recovery = {**developer, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'openai-compatible', 'model': 'gw-model'}
        self.assertEqual(worker_dispatch.task_store_attempt(
            {'developer': developer}, {'developer': recovery}), ('company-gw', 'RECOVERY'))
        self.assertIsNone(worker_dispatch.authorize_task_store_change(
            {'developer': developer, 'dispatch_audit': []}, {'developer': recovery}))
        recovery_reassigned = {**recovery, 'assignment_id': 'b' * 32}
        self.assertEqual(worker_dispatch.task_store_attempt(
            {'developer': developer}, {'developer': recovery_reassigned}),
            ('company-gw', 'REASSIGN'))

        responded = self.developer(state='DEVELOPER_RESPONDED')
        responded.update(provider_type='openai-compatible', model='gw-model')
        responded['direct_result_callback'] = {
            'review_return': {'state': 'RETURNED',
                              'next_step': 'RETURN_TO_DEVELOPER_ONCE'}}
        repair = {**responded, 'state': 'DEVELOPER_DISPATCHING'}
        self.assertEqual(worker_dispatch.task_store_attempt(
            {'developer': responded}, {'developer': repair}), ('company-gw', 'REPAIR'))
        self.assertIsNone(worker_dispatch.authorize_task_store_change(
            {'developer': responded, 'dispatch_audit': []}, {'developer': repair}))
        repair_reassigned = {**repair, 'assignment_id': 'b' * 32}
        self.assertEqual(worker_dispatch.task_store_attempt(
            {'developer': responded}, {'developer': repair_reassigned}),
            ('company-gw', 'REASSIGN'))

    def test_repair_and_recovery_limits_are_per_assignment(self):
        unavailable = self.developer(state='DEVELOPER_UNAVAILABLE')
        recovery = {**unavailable, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'openai-compatible', 'model': 'gw-model'}
        recovery_task = self._task()
        recovery_task['developer'] = unavailable
        recovery_task['dispatch_audit'] = []
        self.assertIsNone(worker_dispatch.authorize_task_store_change(
            recovery_task, {'developer': recovery}))
        second_recovery = worker_dispatch.authorize_task_store_change(
            recovery_task, {'developer': recovery})
        self.assertIsInstance(second_recovery, worker_dispatch.WorkerDispatchDenied)
        self.assertEqual(second_recovery.dispatch_code, 'WORKER_RECOVERY_LIMIT_REACHED')
        self.assertEqual(recovery_task['dispatch_audit'][-1]['reason_code'],
                         'WORKER_RECOVERY_LIMIT_REACHED')

        responded = self.developer(state='DEVELOPER_RESPONDED')
        responded.update(provider_type='openai-compatible', model='gw-model')
        responded['direct_result_callback'] = {
            'review_return': {'state': 'RETURNED',
                              'next_step': 'RETURN_TO_DEVELOPER_ONCE'}}
        repair = {**responded, 'state': 'DEVELOPER_DISPATCHING'}
        repair_task = self._task()
        repair_task['developer'] = responded
        repair_task['dispatch_audit'] = []
        self.assertIsNone(worker_dispatch.authorize_task_store_change(
            repair_task, {'developer': repair}))
        second_repair = worker_dispatch.authorize_task_store_change(
            repair_task, {'developer': repair})
        self.assertIsInstance(second_repair, worker_dispatch.WorkerDispatchDenied)
        self.assertEqual(second_repair.dispatch_code, 'WORKER_REPAIR_LIMIT_REACHED')
        self.assertEqual(repair_task['dispatch_audit'][-1]['reason_code'],
                         'WORKER_REPAIR_LIMIT_REACHED')

    def test_codex_timeout_continuation_is_same_scope_and_durable(self):
        previous = self.developer(state='DEVELOPER_OUTCOME_UNKNOWN', provider='codex')
        previous.update(provider_type='codex-cli', model='gpt-6-luna',
                        executor_task_id='executor-task-1',
                        error_code='CODEX_TIMEOUT_WITH_CHANGES',
                        execution={'status': 'TIMEOUT', 'diff_sha256': 'c' * 64,
                                   'diff_base_sha': previous['source_sha']})
        continued = {**previous, 'assignment_id': new_id(),
                     'state': 'DEVELOPER_DISPATCHING', 'session_id': 'dev-' + new_id(),
                     'artifact': None, 'artifact_sha256': None, 'usage': None,
                     'error_code': None, 'execution': None}
        completion = {'executor_task_id': 'executor-task-1',
                      'evidence': {'result_head': 'd' * 40}}
        current = {'task_id': self.task['task_id'], 'developer': previous,
                   'completion': None, 'dispatch_audit': []}
        changes = {'developer': continued, 'completion': completion}
        expected = {'developer': previous}
        self.assertEqual(worker_dispatch.task_store_attempts(current, changes),
                         [('codex-developer', 'TIMEOUT_CONTINUATION')])
        self.assertIsNone(worker_dispatch.authorize_task_store_change(
            current, changes, expected=expected))
        self.assertEqual(current['dispatch_audit'][-1]['action'], 'TIMEOUT_CONTINUATION')
        self.assertEqual(current['dispatch_audit'][-1]['decision'], 'ALLOWED')

    def test_codex_timeout_continuation_refuses_stale_scope_and_replay(self):
        previous = self.developer(state='DEVELOPER_OUTCOME_UNKNOWN', provider='codex')
        previous.update(provider_type='codex-cli', model='gpt-6-luna',
                        executor_task_id='executor-task-1',
                        error_code='CODEX_TIMEOUT_WITH_CHANGES',
                        execution={'status': 'TIMEOUT', 'diff_sha256': 'c' * 64,
                                   'diff_base_sha': previous['source_sha']})
        continued = {**previous, 'assignment_id': new_id(),
                     'state': 'DEVELOPER_DISPATCHING', 'session_id': 'dev-' + new_id(),
                     'error_code': None, 'execution': None}
        completion = {'executor_task_id': 'executor-task-1',
                      'evidence': {'result_head': 'd' * 40}}
        current = {'task_id': self.task['task_id'], 'developer': previous,
                   'completion': None, 'dispatch_audit': []}
        changes = {'developer': {**continued, 'write_paths': ['other.py']},
                   'completion': completion}
        denied = worker_dispatch.authorize_task_store_change(
            current, changes, expected={'developer': previous})
        self.assertEqual(denied.dispatch_code,
                         'WORKER_TIMEOUT_CONTINUATION_PRECONDITION')
        self.assertEqual(current['dispatch_audit'][-1]['decision'], 'BLOCKED')

        current = {'task_id': self.task['task_id'], 'developer': previous,
                   'completion': None, 'dispatch_audit': []}
        denied = worker_dispatch.authorize_task_store_change(
            current, {'developer': continued, 'completion': completion},
            expected={'developer': self.developer(state='DEVELOPER_OUTCOME_UNKNOWN',
                                                  provider='codex')})
        self.assertEqual(denied.dispatch_code,
                         'WORKER_TIMEOUT_CONTINUATION_PRECONDITION')

        current['dispatch_audit'] = [worker_dispatch.audit_entry(
            worker_dispatch.PolicyDecision('codex-developer', True,
                'OWNER_POLICY_ALLOWED', 'a' * 64), 'TIMEOUT_CONTINUATION')]
        denied = worker_dispatch.authorize_task_store_change(
            current, {'developer': continued, 'completion': completion},
            expected={'developer': previous})
        self.assertEqual(denied.dispatch_code, 'WORKER_TIMEOUT_CONTINUATION_LIMIT')

    def test_codex_timeout_continuation_honors_paused_policy(self):
        previous = self.developer(state='DEVELOPER_OUTCOME_UNKNOWN', provider='codex')
        previous.update(provider_type='codex-cli', model='gpt-6-luna',
                        executor_task_id='executor-task-1',
                        error_code='CODEX_TIMEOUT_WITH_CHANGES',
                        execution={'status': 'TIMEOUT', 'diff_sha256': 'c' * 64,
                                   'diff_base_sha': previous['source_sha']})
        continued = {**previous, 'assignment_id': new_id(),
                     'state': 'DEVELOPER_DISPATCHING', 'session_id': 'dev-' + new_id(),
                     'error_code': None, 'execution': None}
        current = {'task_id': self.task['task_id'], 'developer': previous,
                   'completion': None, 'dispatch_audit': []}
        self._set_state('codex-developer', 'PAUSED')
        denied = worker_dispatch.authorize_task_store_change(current,
            {'developer': continued, 'completion': {'executor_task_id': 'executor-task-1',
                'evidence': {'result_head': 'd' * 40}}}, expected={'developer': previous})
        self.assertEqual(denied.dispatch_code, 'OWNER_PAUSED')

    def test_codex_timeout_continuation_adapter_consumes_recorded_action(self):
        pending = self.developer(state='DEVELOPER_PENDING', provider='codex')
        pending.update(max_tokens=None, executor_task_id='executor-task-1')
        self.store.compare_and_update(self.task['task_id'],
            expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna',
                       'executor_task_id': 'executor-task-1'}
        self.store.update(self.task['task_id'], developer=dispatching)
        previous = {**dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                        'error_code': 'CODEX_TIMEOUT_WITH_CHANGES',
                        'execution': {'status': 'TIMEOUT', 'native_session_id': None,
                            'process_exit_code': None, 'stdout_sha256': None,
                            'elapsed_ms': 100, 'cleanup_status': 'CONFIRMED',
                            'usage_source': 'UNKNOWN', 'source_bytes': None,
                            'source_file_count': None, 'source_manifest_sha256': None,
                            'diff_sha256': 'c' * 64,
                            'diff_base_sha': dispatching['source_sha']}}
        self.store.update(self.task['task_id'], developer=previous)
        continued = {**previous, 'assignment_id': new_id(),
                     'state': 'DEVELOPER_DISPATCHING', 'session_id': 'dev-' + new_id(),
                     'artifact': None, 'artifact_sha256': None, 'usage': None,
                     'error_code': None, 'execution': None}
        result_head = 'd' * 40
        evidence = {'task_id': self.task['task_id'], 'run_id': self.task['run_id'],
            'contract_digest': self.task['task_binding']['contract_digest'],
            'executor_task_id': 'executor-task-1', 'source_sha': previous['source_sha'],
            'result_head': result_head, 'execution_mode': 'DIRECT_D6'}
        completion = {'state': 'WAITING_EXECUTOR',
            'executor_task_id': 'executor-task-1', 'reviewer_task_id': None,
            'evidence': evidence, 'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
            'pm_response': None, 'pm_recommendation': None, 'pm_usage': None,
            'pm_error_code': None}
        transitioned = self.store.compare_and_update(self.task['task_id'],
            expected={'developer': previous}, developer=continued, completion=completion)
        transition = transitioned['dispatch_audit'][-1]
        self.assertEqual(transition['action'], 'TIMEOUT_CONTINUATION')
        decision = worker_dispatch.authorize_dispatch(self.store, self.task['task_id'],
            'codex-developer', 'TIMEOUT_CONTINUATION')
        saved = self.store.get(self.task['task_id'])
        self.assertTrue(decision.allowed)
        self.assertEqual(saved['dispatch_audit'][-1]['authorization_id'],
                         transition['attempt_id'])
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'ALLOWED')

    def test_repair_requires_a_persisted_failed_review_decision(self):
        responded = self.developer(state='DEVELOPER_RESPONDED')
        responded.update(provider_type='openai-compatible', model='gw-model')
        repair = {**responded, 'state': 'DEVELOPER_DISPATCHING'}
        denied = worker_dispatch.authorize_task_store_change(
            {'developer': responded, 'dispatch_audit': []}, {'developer': repair})
        self.assertIsInstance(denied, worker_dispatch.WorkerDispatchDenied)
        self.assertEqual(denied.dispatch_code, 'WORKER_REPAIR_NOT_AUTHORIZED')
        repair_reassigned = {**repair, 'assignment_id': 'b' * 32}
        denied = worker_dispatch.authorize_task_store_change(
            {'developer': responded, 'dispatch_audit': []},
            {'developer': repair_reassigned})
        self.assertIsInstance(denied, worker_dispatch.WorkerDispatchDenied)
        self.assertEqual(denied.dispatch_code, 'WORKER_REASSIGN_PRECONDITION')

    def test_combined_developer_and_reviewer_transition_gates_both_actions(self):
        previous = self.developer(state='DEVELOPER_PENDING')
        previous['direct_result_callback'] = {
            'review_handoff': {'dispatch_state': 'PENDING'}}
        updated = {**previous, 'state': 'DEVELOPER_DISPATCHING',
                   'direct_result_callback': {
                       'review_handoff': {'dispatch_state': 'DISPATCHING'}}}
        current = {'task_id': self.task['task_id'], 'developer': previous,
                   'dispatch_audit': []}
        self._set_state('claude-reviewer', 'PAUSED')
        self._set_state('company-gw', 'PAUSED')
        denial = worker_dispatch.authorize_task_store_change(
            current, {'developer': updated})
        self.assertIsInstance(denial, worker_dispatch.WorkerDispatchDenied)
        self.assertEqual(denial.dispatch_code, 'OWNER_PAUSED')
        self.assertEqual([entry['action'] for entry in current['dispatch_audit']],
                         ['REVIEWER_HANDOFF', 'DISPATCH'])
        self.assertEqual([entry['decision'] for entry in current['dispatch_audit']],
                         ['BLOCKED', 'BLOCKED'])

    def test_reassignment_requires_safe_prior_state_and_bound_precondition(self):
        unknown = self.developer(state='DEVELOPER_OUTCOME_UNKNOWN')
        reassigned = {**unknown, 'assignment_id': 'c' * 32}
        current = {'task_id': self.task['task_id'], 'developer': unknown,
                   'dispatch_audit': []}
        denied = worker_dispatch.authorize_task_store_change(
            current, {'developer': reassigned})
        self.assertEqual(denied.dispatch_code, 'WORKER_REASSIGN_PRECONDITION')

        pending = self.developer(state='DEVELOPER_PENDING')
        reassigned_pending = {**pending, 'assignment_id': 'd' * 32}
        current = {'task_id': self.task['task_id'], 'developer': pending,
                   'dispatch_audit': []}
        denied = worker_dispatch.authorize_task_store_change(
            current, {'developer': reassigned_pending})
        self.assertEqual(denied.dispatch_code, 'WORKER_REASSIGN_PRECONDITION')
        self.assertIsNone(worker_dispatch.authorize_task_store_change(
            current, {'developer': reassigned_pending}, expected={'developer': pending}))

    def test_audit_cap_blocks_without_dropping_prior_evidence(self):
        developer = self.developer(state='DEVELOPER_PENDING')
        dispatching = {**developer, 'state': 'DEVELOPER_DISPATCHING'}
        audit = [worker_dispatch.audit_entry(
            worker_dispatch.PolicyDecision('company-gw', True,
                                           'OWNER_POLICY_ALLOWED', 'a' * 64), 'DISPATCH')
            for _ in range(worker_dispatch.MAX_AUDIT_ENTRIES)]
        current = {'task_id': self.task['task_id'], 'developer': developer,
                   'dispatch_audit': audit}
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            worker_dispatch.authorize_task_store_change(
                current, {'developer': dispatching})
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_DISPATCH_AUDIT_LIMIT')
        self.assertEqual(current['dispatch_audit'], audit)

    def test_audit_append_retries_against_latest_concurrent_audit(self):
        developer = self.developer()
        self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None}, developer=developer)
        before = self.store.get(self.task['task_id'])
        first = worker_dispatch.audit_entry(
            worker_dispatch.PolicyDecision('company-gw', True,
                                           'OWNER_POLICY_ALLOWED', 'a' * 64), 'DISPATCH')
        concurrent = worker_dispatch.audit_entry(
            worker_dispatch.PolicyDecision('company-gw', True,
                                           'OWNER_POLICY_ALLOWED', 'a' * 64), 'RECOVERY')
        original_update = self.store._update
        injected = {'done': False}

        def race(task_id, *, expected, changes, trusted_dispatch_audit=False):
            if not injected['done']:
                injected['done'] = True
                self.store.append_dispatch_audit(task_id, concurrent)
            return original_update(task_id, expected=expected, changes=changes,
                                   trusted_dispatch_audit=trusted_dispatch_audit)

        with patch.object(self.store, '_update', side_effect=race):
            saved = self.store.append_dispatch_audit(
                self.task['task_id'], first,
                expected={'developer': before['developer'],
                          'dispatch_audit': before['dispatch_audit']})
        self.assertEqual([entry['attempt_id'] for entry in saved['dispatch_audit']],
                         [entry['attempt_id'] for entry in before['dispatch_audit']] +
                         [concurrent['attempt_id'], first['attempt_id']])


if __name__ == '__main__':
    unittest.main()
