"""Offline fixture checks; no project clone or provider calls."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack, contextmanager
import os
import copy

spec = importlib.util.spec_from_file_location('ai_loop', Path(__file__).with_name('ai_loop.py'))
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)
HEAD = 'a' * 40


def result(status='PASS', findings=None):
    return {'status': status, 'summary': 'checked', 'reviewed_head': HEAD,
            'findings': findings or []}


class LauncherInstallTests(unittest.TestCase):
    source = '#!/usr/bin/env bash\nset -euo pipefail\necho fixture\n'

    def test_install_creates_exact_launcher_and_preserves_other_work(self):
        with tempfile.TemporaryDirectory() as runtime, \
             patch.object(loop, 'command', side_effect=[(0, self.source, ''), (0, '', '')]) as command:
            root = Path(runtime)
            (root / 'stopped-report.md').write_text('NEEDS_DECISION')
            loop.install_launcher(root, HEAD)
            self.assertEqual((root / 'scripts/ai-loop.sh').read_text(), self.source)
            self.assertEqual((root / 'stopped-report.md').read_text(), 'NEEDS_DECISION')
            self.assertEqual(command.call_args_list[1].args[0], ['bash', '-n'])
            self.assertEqual(command.call_args_list[1].kwargs['stdin'], self.source)

    def test_identical_existing_launcher_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as runtime, \
             patch.object(loop, 'command', side_effect=[(0, self.source, ''), (0, '', '')]):
            root = Path(runtime)
            (root / 'scripts').mkdir()
            target = root / 'scripts/ai-loop.sh'
            target.write_text(self.source)
            original = target.stat().st_mtime_ns
            loop.install_launcher(root, HEAD)
            self.assertEqual(target.stat().st_mtime_ns, original)

    def test_different_existing_launcher_is_preserved(self):
        with tempfile.TemporaryDirectory() as runtime, \
             patch.object(loop, 'command', side_effect=[(0, self.source, ''), (0, '', '')]):
            root = Path(runtime)
            (root / 'scripts').mkdir()
            target = root / 'scripts/ai-loop.sh'
            target.write_text('unrelated user script')
            with self.assertRaises(loop.Stop):
                loop.install_launcher(root, HEAD)
            self.assertEqual(target.read_text(), 'unrelated user script')

    def test_symlink_or_non_directory_install_paths_are_rejected(self):
        for kind in ('directory_symlink', 'target_symlink', 'non_directory'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as runtime, \
                 patch.object(loop, 'command', side_effect=[(0, self.source, ''), (0, '', '')]):
                root = Path(runtime)
                if kind == 'directory_symlink':
                    (root / 'elsewhere').mkdir()
                    (root / 'scripts').symlink_to(root / 'elsewhere', target_is_directory=True)
                elif kind == 'target_symlink':
                    (root / 'scripts').mkdir()
                    (root / 'scripts/ai-loop.sh').symlink_to(root / 'absent-user-file')
                else:
                    (root / 'scripts').write_text('user file')
                with self.assertRaises(loop.Stop):
                    loop.install_launcher(root, HEAD)
                self.assertFalse((root / 'elsewhere/ai-loop.sh').exists())
                self.assertFalse((root / 'absent-user-file').exists())

    def test_invalid_git_source_or_bash_syntax_never_creates_launcher(self):
        for outputs in ([(1, '', '')], [(0, 'invalid script', '')],
                        [(0, self.source + 'x' * (64 * 1024), '')],
                        [(0, self.source, ''), (1, '', 'syntax error')]):
            with self.subTest(outputs=len(outputs)), tempfile.TemporaryDirectory() as runtime, \
                 patch.object(loop, 'command', side_effect=outputs):
                root = Path(runtime)
                with self.assertRaises(loop.Stop):
                    loop.install_launcher(root, HEAD)
                self.assertFalse((root / 'scripts').exists())

    def test_install_main_bypasses_stopped_task_checks_models_and_dependencies(self):
        with tempfile.TemporaryDirectory() as runtime, ExitStack() as stack:
            root = Path(runtime)
            gitdir = root / 'git-metadata-fixture'
            gitdir.mkdir()
            (root / 'stopped-report.md').write_text('NEEDS_DECISION retained')
            def fake_git(cwd, *args):
                values = {('rev-parse', '--show-toplevel'): str(root),
                          ('remote', 'get-url', 'origin'): 'git@github.com:' + loop.IDENTITY + '.git',
                          ('rev-parse', '--absolute-git-dir'): str(gitdir)}
                self.assertIn(args, values)
                return values[args]
            stack.enter_context(patch.object(loop, 'REPO', root))
            stack.enter_context(patch.object(loop, 'git', side_effect=fake_git))
            stack.enter_context(patch.object(loop, 'command', side_effect=[(0, self.source, ''), (0, '', '')]))
            forbidden = [stack.enter_context(patch.object(loop, name)) for name in
                         ('load_task', 'codex', 'claude', 'tests', 'prepare_new_branch', 'run')]
            stack.enter_context(patch.object(sys, 'argv',
                ['ai_loop.py', '--control-sha', HEAD, '--install-launcher']))
            previous = Path.cwd()
            os.chdir(root)
            try:
                loop.main()
            finally:
                os.chdir(previous)
            for call in forbidden:
                call.assert_not_called()
            self.assertEqual((root / 'stopped-report.md').read_text(), 'NEEDS_DECISION retained')
            self.assertTrue((root / 'scripts/ai-loop.sh').is_file())

    def test_install_cannot_be_combined_with_task_or_check_options(self):
        for option in ('--check', '--related', '--full'):
            with self.subTest(option=option), \
                 patch.object(sys, 'argv', ['ai_loop.py', '--control-sha', HEAD, '--install-launcher', option]), \
                 patch.object(loop, 'git') as git:
                with self.assertRaises(SystemExit) as stopped:
                    loop.main()
                self.assertEqual(stopped.exception.code, 2)
                git.assert_not_called()


class QuotaResumeTests(unittest.TestCase):
    run_id = '20261007T075141Z-70725'

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as runtime, ExitStack() as stack:
            root = Path(runtime)
            old = root / '.ai-loop' / self.run_id
            old.mkdir(parents=True)
            baseline = {'mode': 'implement_new_pr', 'task_id': 'fixture', 'branch': 'feat/fixture',
                        'base_sha': HEAD, 'allowed_files': ['source.py']}
            task = copy.deepcopy(baseline)
            task['initial_quota_recovery'] = {'run_id': self.run_id, 'control_sha': 'b' * 40}
            loop.save_json(old / 'task.json', baseline)
            (old / 'task.md').write_text('pinned requirements\n')
            loop.save_json(old / 'provenance.json', {'root': str(root), 'run_id': self.run_id,
                'control_sha': 'b' * 40, 'base': HEAD})
            loop.save_json(old / 'summary.json', {'status': 'STOPPED', 'history': [],
                'branch': task['branch'], 'head': HEAD, 'decision_checkpoint': None,
                'reason': 'command failed (exit 1): codex; see local logs'})
            (old / 'codex-0.stderr.log').write_text('ERROR: You’ve hit your usage limit.\n')
            (root / 'source.py').write_text('partial implementation\n')
            (root / 'user.txt').write_text('unrelated preserved\n')
            inventory = {p: loop.digest(root / p) for p in ('source.py', 'user.txt')}
            stack.enter_context(patch.object(loop, 'load_task', return_value=(baseline, 'pinned requirements')))
            stack.enter_context(patch.object(loop, 'git', side_effect=lambda cwd, *args:
                task['branch'] if args == ('branch', '--show-current') else HEAD))
            runner = stack.enter_context(patch.object(loop, 'run', side_effect=['[]', '']))
            yield root, old, task, inventory, runner

    def test_partial_files_adopted_unrelated_preserved_and_backed_up(self):
        with self.fixture() as (root, old, task, inventory, runner):
            original, recovery = loop.prepare_quota_resume(root, task, self.run_id, inventory)
            self.assertEqual(original, {'user.txt': inventory['user.txt']})
            self.assertEqual(recovery['partial_files'], {'source.py': inventory['source.py']})
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            logs = root / '.ai-loop/new-run'
            logs.mkdir()
            loop.snapshot_recovery(root, logs, recovery)
            self.assertEqual((logs / 'recovery-inputs/source.py').read_bytes(), (root / 'source.py').read_bytes())
            self.assertEqual((root / 'user.txt').read_text(), 'unrelated preserved\n')
            self.assertTrue((old / 'summary.json').exists())
            self.assertEqual(runner.call_count, 2)

    def test_unapproved_run_stops_before_remote_or_model_calls(self):
        with self.fixture() as (root, old, task, inventory, runner):
            with self.assertRaises(loop.Stop):
                loop.prepare_quota_resume(root, task, 'other-run', inventory)
            runner.assert_not_called()

    def test_published_branch_or_existing_pr_is_rejected(self):
        for responses in (['[{"number":47}]'], ['[]', HEAD]):
            with self.subTest(responses=responses), self.fixture() as (root, old, task, inventory, runner):
                runner.side_effect = responses
                with self.assertRaises(loop.Stop):
                    loop.prepare_quota_resume(root, task, self.run_id, inventory)

    def test_branch_or_head_drift_is_rejected(self):
        for responses in (['feat/unrelated'], [ 'feat/fixture', 'c' * 40]):
            with self.subTest(responses=responses), self.fixture() as (root, old, task, inventory, runner), \
                 patch.object(loop, 'git', side_effect=responses):
                with self.assertRaises(loop.Stop):
                    loop.prepare_quota_resume(root, task, self.run_id, inventory)
                runner.assert_not_called()

    def test_changed_contract_is_rejected(self):
        with self.fixture() as (root, old, task, inventory, runner):
            task['max_repairs'] = 0
            with self.assertRaises(loop.Stop):
                loop.prepare_quota_resume(root, task, self.run_id, inventory)

    def test_only_initial_quota_stop_can_resume(self):
        for change in ({'history': [{'round': 0}]}, {'reason': 'Codex reported NEEDS_DECISION'},
                       {'head': 'c' * 40}, {'status': 'READY_FOR_FINAL_REVIEW'},
                       {'decision_checkpoint': {'url': 'published'}}):
            with self.subTest(change=change), self.fixture() as (root, old, task, inventory, runner):
                summary = json.loads((old / 'summary.json').read_text())
                summary.update(change)
                loop.save_json(old / 'summary.json', summary)
                with self.assertRaises(loop.Stop):
                    loop.prepare_quota_resume(root, task, self.run_id, inventory)

    def test_missing_quota_error_or_provenance_drift_is_rejected(self):
        for kind in ('quota', 'provenance', 'saved_contract', 'saved_text'):
            with self.subTest(kind=kind), self.fixture() as (root, old, task, inventory, runner):
                if kind == 'quota':
                    (old / 'codex-0.stderr.log').write_text('authentication error')
                elif kind == 'provenance':
                    loop.save_json(old / 'provenance.json', {})
                elif kind == 'saved_contract':
                    loop.save_json(old / 'task.json', {})
                else:
                    (old / 'task.md').write_text('different requirements')
                with self.assertRaises(loop.Stop):
                    loop.prepare_quota_resume(root, task, self.run_id, inventory)

    def test_symlink_evidence_or_candidate_is_rejected(self):
        for kind in ('summary.json', 'codex-0.stderr.log', 'candidate', 'logs'):
            with self.subTest(kind=kind), self.fixture() as (root, old, task, inventory, runner):
                if kind == 'logs':
                    old.rename(old.with_name('retained'))
                    old.symlink_to(old.with_name('retained'), target_is_directory=True)
                else:
                    path = root / 'source.py' if kind == 'candidate' else old / kind
                    path.unlink()
                    path.symlink_to(root / 'user.txt')
                with self.assertRaises(loop.Stop):
                    loop.prepare_quota_resume(root, task, self.run_id, inventory)

    def test_snapshot_detects_candidate_drift_without_overwriting_user_file(self):
        with self.fixture() as (root, old, task, inventory, runner):
            _, recovery = loop.prepare_quota_resume(root, task, self.run_id, inventory)
            (root / 'source.py').write_text('concurrent edit')
            logs = root / '.ai-loop/new-run'
            logs.mkdir()
            with self.assertRaises(loop.Stop):
                loop.snapshot_recovery(root, logs, recovery)
            self.assertEqual((root / 'source.py').read_text(), 'concurrent edit')

    def test_empty_or_oversized_partial_candidate_is_rejected(self):
        for kind in ('empty', 'oversize'):
            with self.subTest(kind=kind), self.fixture() as (root, old, task, inventory, runner):
                if kind == 'empty':
                    inventory.pop('source.py')
                else:
                    (root / 'source.py').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
                with self.assertRaises(loop.Stop):
                    loop.prepare_quota_resume(root, task, self.run_id, inventory)

    def test_resume_authorization_is_one_use_even_after_another_stop(self):
        with tempfile.TemporaryDirectory() as runtime:
            state = Path(runtime)
            recovery = {'from_run': self.run_id}
            loop.claim_quota_resume(state, recovery, 'new-run', HEAD)
            marker = state / ('resume-' + self.run_id + '.json')
            retained = marker.read_bytes()
            with self.assertRaises(loop.Stop):
                loop.claim_quota_resume(state, recovery, 'second-run', HEAD)
            self.assertEqual(marker.read_bytes(), retained)

    def test_resume_claim_rejects_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as runtime:
            state = Path(runtime)
            target = state / 'user-file'
            target.write_text('preserved')
            (state / ('resume-' + self.run_id + '.json')).symlink_to(target)
            with self.assertRaises(loop.Stop):
                loop.claim_quota_resume(state, {'from_run': self.run_id}, 'new-run', HEAD)
            self.assertEqual(target.read_text(), 'preserved')


class DecisionCheckpointTests(unittest.TestCase):
    @contextmanager
    def fixture(self, **options):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            logs = root / '20261007T064116Z-68730'
            logs.mkdir()
            report = root / 'reports/validation.md'
            report.parent.mkdir()
            report.write_text('# Checkpoint\nNEEDS_DECISION: continuity unresolved.\n')
            (logs / 'private.stdout.log').write_text('PRIVATE STREAM MUST STAY LOCAL')
            task = {'task_id': 'fixture-task', 'mode': 'implement_new_pr', 'base_sha': HEAD,
                    'branch': 'feat/fixture', 'acceptance_report': 'reports/validation.md',
                    'publish_decision_checkpoint': True}
            decision = {'status': 'NEEDS_DECISION', 'summary': 'Coordinator decision required.'}
            calls, posted = [], {}
            ref_reads = [0]
            control = 'd' * 40

            def fake_run(argv, cwd, **kwargs):
                calls.append(argv)
                if '--method' in argv:
                    method, endpoint = argv[argv.index('--method') + 1:argv.index('--method') + 3]
                    payload = json.loads(Path(argv[argv.index('--input') + 1]).read_text())
                    posted[method + ' ' + endpoint] = payload
                    if endpoint.endswith('/trees'):
                        if options.get('change_report'):
                            report.write_text('NEEDS_DECISION changed concurrently')
                        return json.dumps({'sha': 'e' * 40})
                    if endpoint.endswith('/commits'):
                        return json.dumps({'sha': 'f' * 40})
                    return json.dumps({'object': {'sha': 'f' * 40}})
                endpoint = argv[2]
                if endpoint.endswith('ref/heads/automation/ai-loop'):
                    ref_reads[0] += 1
                    if options.get('api_error'):
                        raise loop.Stop('command failed (exit 1): gh; see local logs')
                    drift = options.get('control_drift') or (options.get('race') and ref_reads[0] == 2)
                    return json.dumps({'object': {'sha': 'c' * 40 if drift else control}})
                if '/git/trees/' in endpoint:
                    path = 'reports/ai-loop/checkpoints/fixture-task-' + logs.name + '.md'
                    return json.dumps({'truncated': options.get('truncated', False),
                        'tree': [{'path': path}] if options.get('collision') else []})
                if '/contents/' in endpoint:
                    content = next(v['tree'][0]['content'] for k, v in posted.items() if k.endswith('/trees'))
                    raw = content.encode()
                    sha = loop.hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                    return json.dumps({'type': 'file', 'sha': '0' * 40 if options.get('bad_blob') else sha})
                raise AssertionError(argv)

            with patch.object(loop, 'run', side_effect=fake_run), \
                 patch.object(loop, 'check_scope', return_value=options.get('scope', ['reports/validation.md'])):
                yield root, logs, report, task, decision, calls, posted, control

    def invoke(self, fixture, **kwargs):
        root, logs, report, task, decision, calls, posted, control = fixture
        return loop.publish_decision_checkpoint(root, logs, task, decision, {},
            kwargs.get('head', HEAD), control, kwargs.get('round_no', 0))

    def test_report_only_decision_publishes_control_checkpoint_without_local_git_or_logs(self):
        with self.fixture() as f:
            before = f[2].read_bytes()
            value = self.invoke(f)
            self.assertEqual(value['status'], 'NEEDS_DECISION')
            self.assertIn('/blob/' + 'f' * 40 + '/', value['url'])
            self.assertEqual(f[2].read_bytes(), before)
            patch_payload = next(v for k, v in f[6].items() if k.startswith('PATCH '))
            self.assertIs(patch_payload['force'], False)
            content = next(v['tree'][0]['content'] for k, v in f[6].items() if k.endswith('/trees'))
            self.assertIn('NOT RUN', content)
            self.assertIn(before.decode(), content)
            self.assertNotIn('PRIVATE STREAM MUST STAY LOCAL', content)
            self.assertTrue(all(a[:2] == ['gh', 'api'] for a in f[5]))
            self.assertTrue((f[1] / 'published-checkpoint.json').is_file())

    def test_ineligible_results_modes_and_repairs_never_call_api(self):
        for mode, enabled, status, round_no in [
                ('implement_new_pr', False, 'NEEDS_DECISION', 0),
                ('review_existing_pr', True, 'NEEDS_DECISION', 0),
                ('implement_new_pr', True, 'FAIL', 0),
                ('implement_new_pr', True, 'PASS', 0),
                ('implement_new_pr', True, 'NEEDS_DECISION', 1)]:
            with self.subTest(mode=mode, enabled=enabled, status=status, round_no=round_no), self.fixture() as f:
                f[3].update(mode=mode, publish_decision_checkpoint=enabled)
                f[4]['status'] = status
                self.assertIsNone(self.invoke(f, round_no=round_no))
                self.assertEqual(f[5], [])

    def test_source_changes_or_changed_head_stop_before_api(self):
        with self.fixture(scope=['reports/validation.md', 'source.py']) as f:
            with self.assertRaises(loop.Stop):
                self.invoke(f)
            self.assertEqual(f[5], [])
        with self.fixture() as f:
            with self.assertRaises(loop.Stop):
                self.invoke(f, head='b' * 40)
            self.assertEqual(f[5], [])

    def test_unsafe_oversized_or_invalid_report_stays_local(self):
        for mutation in ('symlink', 'oversized', 'invalid'):
            with self.subTest(mutation=mutation), self.fixture() as f:
                if mutation == 'symlink':
                    f[2].unlink()
                    f[2].symlink_to(f[1] / 'private.stdout.log')
                elif mutation == 'oversized':
                    f[2].write_bytes(b'NEEDS_DECISION' + b'x' * (128 * 1024))
                else:
                    f[2].write_text('NEEDS_DECISION\0invalid')
                with self.assertRaises(loop.Stop):
                    self.invoke(f)
                self.assertEqual(f[5], [])

    def test_drift_collision_or_incomplete_tree_stops_before_object_writes(self):
        for option in ('control_drift', 'collision', 'truncated'):
            with self.subTest(option=option), self.fixture(**{option: True}) as f:
                with self.assertRaises(loop.Stop):
                    self.invoke(f)
                self.assertEqual(f[6], {})

    def test_concurrent_ref_or_report_change_never_updates_branch(self):
        for option in ('race', 'change_report'):
            with self.subTest(option=option), self.fixture(**{option: True}) as f:
                with self.assertRaises(loop.Stop):
                    self.invoke(f)
                self.assertFalse(any(k.startswith('PATCH ') for k in f[6]))

    def test_api_error_preserves_original_report(self):
        with self.fixture(api_error=True) as f:
            before = f[2].read_bytes()
            with self.assertRaises(loop.Stop):
                self.invoke(f)
            self.assertEqual(f[2].read_bytes(), before)
            self.assertEqual(f[6], {})

    def test_remote_mismatch_cannot_claim_verified_publication(self):
        with self.fixture(bad_blob=True) as f:
            with self.assertRaisesRegex(loop.Stop, 'remote content verification'):
                self.invoke(f)
            self.assertTrue((f[1] / 'published-checkpoint.json').is_file())

    def test_parent_directory_symlink_cannot_export_outside_report(self):
        with self.fixture() as f:
            parent = f[2].parent
            moved = f[0] / 'outside-reports'
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
            with self.assertRaises(loop.Stop):
                self.invoke(f)
            self.assertEqual(f[5], [])

    def test_task_contract_requires_boolean_and_report_for_decision_publication(self):
        metadata = json.loads((Path(__file__).parents[1] / 'automation/ai-loop/task.json').read_text())
        def load(value):
            with patch.object(loop, 'git', side_effect=lambda root, *args:
                    json.dumps(value) if args[0] == 'show' and args[1].endswith('task.json') else ''):
                return loop.load_task(Path('.'), 'd' * 40)[0]
        self.assertTrue(load(metadata)['publish_decision_checkpoint'])
        for mutation in ('non_boolean', 'missing_report'):
            invalid = copy.deepcopy(metadata)
            if mutation == 'non_boolean':
                invalid['publish_decision_checkpoint'] = 'true'
            else:
                invalid.pop('acceptance_report')
            with self.subTest(mutation=mutation), self.assertRaises(loop.Stop):
                load(invalid)


class ResultTests(unittest.TestCase):
    def test_success_wrapper_and_exact_revision(self):
        raw = {'is_error': False, 'subtype': 'success', 'structured_output': result()}
        self.assertEqual(loop.parse_claude(raw, HEAD)['status'], 'PASS')

    def test_exit_success_without_structured_result_is_not_pass(self):
        with self.assertRaises(loop.Stop):
            loop.parse_claude({'is_error': False, 'subtype': 'success', 'result': 'PASS'}, HEAD)

    def test_quota_or_turn_limit_cannot_pass(self):
        for raw in ({'is_error': True, 'subtype': 'success', 'structured_output': result()},
                    {'is_error': False, 'subtype': 'error_max_turns', 'structured_output': result()}):
            with self.assertRaises(loop.Stop):
                loop.parse_claude(raw, HEAD)

    def test_stale_review_cannot_pass(self):
        with self.assertRaises(loop.Stop):
            loop.validate_result(result(), 'review', 'b' * 40)

    def test_high_finding_cannot_be_hidden_as_nonblocking(self):
        finding = {'severity': 'High', 'blocking': False, 'file': 'source.py',
                   'detail': 'reproducible failure', 'evidence': 'test X fails'}
        with self.assertRaises(loop.Stop):
            loop.validate_result(result(findings=[finding]), 'review', HEAD)

    def test_low_nonblocking_improvement_does_not_force_repairs(self):
        finding = {'severity': 'Low', 'blocking': False, 'file': 'source.py',
                   'detail': 'optional naming change', 'evidence': 'line 20'}
        self.assertEqual(loop.validate_result(result(findings=[finding]), 'review', HEAD)['status'], 'PASS')

    def test_fail_needs_actionable_blocker(self):
        with self.assertRaises(loop.Stop):
            loop.validate_result(result('FAIL'), 'review', HEAD)

    def test_findings_need_evidence(self):
        finding = {'severity': 'High', 'blocking': True, 'file': 'source.py',
                   'detail': 'bug', 'evidence': ''}
        with self.assertRaises(loop.Stop):
            loop.validate_result(result('FAIL', [finding]), 'review', HEAD)

    def test_developer_requires_full_contract(self):
        with self.assertRaises(loop.Stop):
            loop.validate_result({'status': 'PASS'}, 'developer')


class ScopeTests(unittest.TestCase):
    def git_value(self, root, *args):
        if args == ('branch', '--show-current'):
            return 'feat/example'
        if args == ('rev-parse', 'HEAD'):
            return HEAD
        return ''

    def test_unrelated_untracked_work_preserved(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'untracked', return_value={'notes.txt': 'digest'}), \
             patch.object(loop, 'changed', return_value=['notes.txt', 'source.py']):
            self.assertEqual(loop.check_scope(Path('.'), {'allowed_files': ['source.py']},
                {'notes.txt': 'digest'}, 'feat/example', HEAD), ['source.py'])

    def test_unrelated_work_changes_stop(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'untracked', return_value={'notes.txt': 'new'}):
            with self.assertRaises(loop.Stop):
                loop.check_scope(Path('.'), {'allowed_files': ['source.py']},
                    {'notes.txt': 'old'}, 'feat/example', HEAD)

    def test_scope_expansion_stops(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'untracked', return_value={}), \
             patch.object(loop, 'changed', return_value=['AGENTS.md']):
            with self.assertRaises(loop.Stop):
                loop.check_scope(Path('.'), {'allowed_files': ['source.py']}, {}, 'feat/example', HEAD)

    def test_agent_commit_stops(self):
        with patch.object(loop, 'git', return_value='wrong'):
            with self.assertRaises(loop.Stop):
                loop.check_scope(Path('.'), {'allowed_files': ['source.py']}, {}, 'feat/example', HEAD)


class RuntimeTests(unittest.TestCase):
    def test_process_reads_prompt_via_stdin(self):
        rc, out, _ = loop.command([sys.executable, '-c', 'import sys; print(sys.stdin.read())'], Path.cwd(), stdin='task content')
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), 'task content')

    def test_normal_exit_does_not_signal_reaped_pgid(self):
        with patch.object(loop.os, 'killpg') as kill:
            self.assertEqual(loop.command([sys.executable, '-c', 'pass'], Path.cwd())[0], 0)
            kill.assert_not_called()

    def test_timeout_stops(self):
        with self.assertRaises(loop.Stop):
            loop.command([sys.executable, '-c', 'import time; time.sleep(5)'], Path.cwd(), timeout=0.1)

    def test_output_logs_do_not_spill_to_console(self):
        with tempfile.TemporaryDirectory() as runtime:
            stdout, stderr = Path(runtime) / 'stdout', Path(runtime) / 'stderr'
            rc, out, err = loop.command([sys.executable, '-c', 'import sys; print("out"); print("err",file=sys.stderr)'],
                                       Path.cwd(), stdout_path=stdout, stderr_path=stderr)
            self.assertEqual((rc, out, err), (0, '', ''))
            self.assertEqual(stdout.read_text().strip(), 'out')
            self.assertEqual(stderr.read_text().strip(), 'err')

    def test_ci_exact_head_success(self):
        rows = [{'headSha': HEAD, 'status': 'completed', 'conclusion': 'success', 'url': 'run-url'}]
        with patch.object(loop, 'run', return_value=json.dumps(rows)):
            self.assertEqual(loop.wait_ci(Path('.'), HEAD, 'llmtool-ci.yml'), rows)

    def test_ci_failure_never_passes(self):
        rows = [{'headSha': HEAD, 'status': 'completed', 'conclusion': 'failure'}]
        with patch.object(loop, 'run', return_value=json.dumps(rows)):
            with self.assertRaises(loop.Stop):
                loop.wait_ci(Path('.'), HEAD, 'llmtool-ci.yml')

    def test_ci_old_success_and_missing_checks_time_out(self):
        for rows in ([], [{'headSha': 'b'*40, 'status': 'completed', 'conclusion': 'success'}]):
            with patch.object(loop, 'run', return_value=json.dumps(rows)), \
                 patch.object(loop.time, 'monotonic', side_effect=[0, 1201]):
                with self.assertRaises(loop.Stop):
                    loop.wait_ci(Path('.'), HEAD, 'llmtool-ci.yml')


class TestPhaseTests(unittest.TestCase):
    def test_related_and_full_execute_the_configured_commands(self):
        task = {'related_tests': [['related-command']], 'full_tests': [['full-command']],
                'timeout_seconds': 60}
        with tempfile.TemporaryDirectory() as runtime:
            logs = Path(runtime)
            for suite, expected in [('related', 'related-command'), ('full', 'full-command'),
                                    ('full-final', 'full-command')]:
                with patch.object(loop, 'command', return_value=(0, '', '')) as command:
                    self.assertTrue(loop.tests(logs, logs, task, 0, suite))
                    self.assertEqual(command.call_args.args[0], [expected])
                rows = json.loads(loop.test_result_file(logs, 0, suite).read_text())
                self.assertEqual(rows[0]['suite'], suite)
                self.assertEqual(rows[0]['exit_code'], 0)

    def test_failed_command_preserves_evidence_and_skips_later_commands(self):
        task = {'related_tests': [['fails'], ['must-not-run']], 'timeout_seconds': 60}
        with tempfile.TemporaryDirectory() as runtime:
            logs = Path(runtime)
            with patch.object(loop, 'command', return_value=(7, '', 'failure')) as command:
                self.assertFalse(loop.tests(logs, logs, task, 2, 'related'))
                self.assertEqual(command.call_count, 1)
            rows = json.loads(loop.test_result_file(logs, 2, 'related').read_text())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['argv'], ['fails'])
            self.assertEqual(rows[0]['exit_code'], 7)


class MacEvidenceTests(unittest.TestCase):
    def evidence(self):
        value = {'schema_version': 1, 'platform': 'darwin', 'os_version': 'test-os',
                 'python_version': '3.13.3', 'tested_sha256': {'source.txt': 'hash'},
                 'scenarios': {n: {'status': 'PASS', 'checks': 1, 'skipped': 0, 'repetitions': []}
                               for n in loop.MAC_SCENARIOS}}
        row = {'leader_pid': 123, 'pgid': 123, 'descendant_pids': [124], 'latency_seconds': 0.5,
               'identity_verified': True, 'leader_absent': True, 'descendants_absent': True,
               'group_absent': True, 'containment_confirmed': True}
        for name in ('parent_sigkill_plain', 'parent_sigkill_popen'):
            value['scenarios'][name]['checks'] = 20
            value['scenarios'][name]['repetitions'] = [copy.deepcopy(row) for _ in range(20)]
        return value

    def validate(self, value):
        with tempfile.TemporaryDirectory() as runtime:
            path = Path(runtime) / 'evidence.json'
            path.write_text(json.dumps(value))
            with patch.object(loop.sys, 'platform', 'darwin'), \
                 patch.object(loop.platform, 'mac_ver', return_value=('test-os', (), '')):
                return loop.validate_mac_evidence(path, {'source.txt': 'hash', 'reports/report.md': 'doc'}, '3.13.3')

    def test_complete_bound_mac_evidence_is_accepted(self):
        self.assertEqual(self.validate(self.evidence())['platform'], 'darwin')

    def test_wrong_environment_or_source_hash_cannot_pass(self):
        for key, replacement in [('platform', 'linux'), ('os_version', 'other'),
                                  ('python_version', '3.9'), ('tested_sha256', {'source.txt': 'old'})]:
            value = self.evidence(); value[key] = replacement
            with self.assertRaises(loop.Stop):
                self.validate(value)

    def test_missing_failed_or_skipped_scenario_cannot_pass(self):
        for fault in ('missing', 'failed', 'skipped'):
            value = self.evidence()
            if fault == 'missing':
                del value['scenarios']['sigterm']
            elif fault == 'failed':
                value['scenarios']['sigterm']['status'] = 'FAIL'
            else:
                value['scenarios']['sigterm']['skipped'] = 1
            with self.assertRaises(loop.Stop):
                self.validate(value)

    def test_both_sigkill_variants_require_twenty_repetitions(self):
        for variant in ('parent_sigkill_plain', 'parent_sigkill_popen'):
            value = self.evidence(); value['scenarios'][variant]['repetitions'].pop()
            with self.assertRaises(loop.Stop):
                self.validate(value)

    def test_latency_and_identity_absence_proof_cannot_be_weakened(self):
        for key, replacement in [('latency_seconds', 2.01), ('latency_seconds', float('nan')),
                                 ('identity_verified', False), ('group_absent', False),
                                 ('descendants_absent', False), ('descendant_pids', [])]:
            value = self.evidence(); value['scenarios']['parent_sigkill_plain']['repetitions'][0][key] = replacement
            with self.assertRaises(loop.Stop):
                self.validate(value)

    def test_missing_or_oversized_evidence_cannot_pass(self):
        with tempfile.TemporaryDirectory() as runtime:
            path = Path(runtime) / 'missing.json'
            with self.assertRaises(loop.Stop):
                loop.validate_mac_evidence(path, {}, '')
            path.write_bytes(b' ' * (128 * 1024 + 1))
            with self.assertRaises(loop.Stop):
                loop.validate_mac_evidence(path, {}, '')


class IntegratedHeadTests(unittest.TestCase):
    base = 'b' * 40

    def git_value(self, root, *args):
        if args == ('rev-parse', 'HEAD'):
            return HEAD
        if args == ('branch', '--show-current'):
            return 'feat/merged'
        return 'c' * 40  # Identical tracked trees in the squash case.

    def merged_pr(self):
        return {'state': 'closed', 'merged_at': '2026-10-07T05:24:00Z',
            'merge_commit_sha': 'e' * 40,
            'head': {'sha': HEAD, 'ref': 'feat/merged', 'repo': {'full_name': loop.IDENTITY}},
            'base': {'ref': 'main', 'repo': {'full_name': loop.IDENTITY}}}

    def test_normal_ancestry_requires_no_pr_lookup(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'command', return_value=(0, '', '')), \
             patch.object(loop, 'run') as run:
            self.assertTrue(loop.current_is_integrated(Path('.'), self.base))
            run.assert_not_called()

    def test_exact_merged_squash_head_with_equal_tree_is_accepted(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'command', side_effect=[(1, '', ''), (0, '', '')]) as command, \
             patch.object(loop, 'run', return_value=json.dumps([self.merged_pr()])):
            self.assertTrue(loop.current_is_integrated(Path('.'), self.base))
            self.assertEqual(command.call_args.args[0][-2:], ['e' * 40, self.base])

    def test_equal_tree_does_not_accept_unpublished_extra_commit(self):
        pr = self.merged_pr(); pr['head']['sha'] = 'd' * 40
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'command', return_value=(1, '', '')), \
             patch.object(loop, 'run', return_value=json.dumps([pr])):
            self.assertFalse(loop.current_is_integrated(Path('.'), self.base))

    def test_unmerged_wrong_repo_or_wrong_branch_is_rejected(self):
        for fault in ('unmerged', 'repo', 'branch', 'base'):
            pr = self.merged_pr()
            if fault == 'unmerged':
                pr['merged_at'] = None
            elif fault == 'repo':
                pr['head']['repo']['full_name'] = 'other/repo'
            elif fault == 'branch':
                pr['head']['ref'] = 'feat/other'
            else:
                pr['base']['ref'] = 'release'
            with patch.object(loop, 'git', side_effect=self.git_value), \
                 patch.object(loop, 'command', return_value=(1, '', '')), \
                 patch.object(loop, 'run', return_value=json.dumps([pr])):
                self.assertFalse(loop.current_is_integrated(Path('.'), self.base))

    def test_merge_commit_must_be_in_pinned_main(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'command', side_effect=[(1, '', ''), (1, '', '')]), \
             patch.object(loop, 'run', return_value=json.dumps([self.merged_pr()])):
            self.assertFalse(loop.current_is_integrated(Path('.'), self.base))

    def test_different_tree_stops_before_github_lookup(self):
        def git(root, *args):
            if args == ('rev-parse', self.base + '^{tree}'):
                return 'different main tree'
            return self.git_value(root, *args)
        with patch.object(loop, 'git', side_effect=git), \
             patch.object(loop, 'command', return_value=(1, '', '')), \
             patch.object(loop, 'run') as run:
            self.assertFalse(loop.current_is_integrated(Path('.'), self.base))
            run.assert_not_called()

    def test_ancestry_command_error_cannot_be_treated_as_squash(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'command', return_value=(128, '', 'error')):
            with self.assertRaises(loop.Stop):
                loop.current_is_integrated(Path('.'), self.base)


class NewBranchTests(unittest.TestCase):
    task = {'branch': 'feat/new-fixture', 'base_sha': HEAD}

    def test_fresh_branch_starts_at_pinned_main(self):
        with patch.object(loop, 'run', side_effect=['[]', '']), \
             patch.object(loop, 'command', side_effect=[(1, '', ''), (0, '', '')]), \
             patch.object(loop, 'git', return_value=HEAD) as git:
            loop.prepare_new_branch(Path('.'), self.task)
            self.assertIn(('switch', '--no-overwrite-ignore', '-c', 'feat/new-fixture', 'origin/main'),
                          [c.args[1:] for c in git.call_args_list])

    def test_existing_pr_remote_or_local_branch_stops_without_switch(self):
        for responses, rc in [(['[{}]'], 1), (['[]', 'existing remote'], 1), (['[]', ''], 0)]:
            with patch.object(loop, 'run', side_effect=responses), \
                 patch.object(loop, 'command', return_value=(rc, '', '')), \
                 patch.object(loop, 'git', return_value=HEAD) as git:
                with self.assertRaises(loop.Stop):
                    loop.prepare_new_branch(Path('.'), self.task)
                self.assertFalse(any(c.args[1] == 'switch' for c in git.call_args_list))

    def test_unpublished_commits_stop_without_switch(self):
        def git_value(root, *args):
            if args == ('rev-parse', 'HEAD'):
                return 'b' * 40
            return args[-1]
        with patch.object(loop, 'run', side_effect=['[]', '']), \
             patch.object(loop, 'command', side_effect=[(1, '', ''), (1, '', '')]), \
             patch.object(loop, 'git', side_effect=git_value) as git:
            with self.assertRaises(loop.Stop):
                loop.prepare_new_branch(Path('.'), self.task)
            self.assertFalse(any(c.args[1] == 'switch' for c in git.call_args_list))

    def test_precheck_never_creates_branch(self):
        with patch.object(loop, 'run', side_effect=['[]', '']), \
             patch.object(loop, 'command', side_effect=[(1, '', ''), (0, '', '')]), \
             patch.object(loop, 'git', return_value=HEAD) as git:
            loop.prepare_new_branch(Path('.'), self.task, check_only=True)
            self.assertFalse(any(c.args[1] == 'switch' for c in git.call_args_list))


class ExistingPRTests(unittest.TestCase):
    def task(self):
        return {'task_id': 'pr46-review', 'mode': 'review_existing_pr', 'pr_number': 46,
                'base_sha': 'b' * 40, 'expected_head': HEAD, 'branch': 'feat/existing',
                'allowed_files': ['source.txt'], 'related_tests': [['fake-related']],
                'full_tests': [['fake-full']], 'default_test_mode': 'related',
                'max_repairs': 2, 'timeout_seconds': 60, 'ci_workflow': 'llmtool-ci.yml'}

    def pr(self):
        return {'state': 'open', 'merged': False, 'base': {'ref': 'main', 'sha': 'b' * 40,
                'repo': {'full_name': loop.IDENTITY}}, 'head': {'ref': 'feat/existing',
                'sha': HEAD, 'repo': {'full_name': loop.IDENTITY}}, 'body': 'Original description',
                'html_url': 'https://github.com/example/pull/46'}

    def test_open_pinned_pr_is_accepted(self):
        with patch.object(loop, 'run', return_value=json.dumps(self.pr())):
            self.assertEqual(loop.read_pr(Path('.'), self.task(), HEAD)['head']['sha'], HEAD)

    def test_closed_merged_or_moved_pr_stops(self):
        for changed in ('state', 'merged', 'head', 'base'):
            value = self.pr()
            if changed == 'state':
                value['state'] = 'closed'
            elif changed == 'merged':
                value['merged'] = True
            else:
                value[changed]['sha'] = 'c' * 40
            with patch.object(loop, 'run', return_value=json.dumps(value)):
                with self.assertRaises(loop.Stop):
                    loop.read_pr(Path('.'), self.task(), HEAD)

    def test_wrong_repo_or_branch_stops(self):
        for changed in ('repo', 'ref'):
            value = self.pr()
            if changed == 'repo':
                value['head']['repo']['full_name'] = 'someone/fork'
            else:
                value['head']['ref'] = 'feat/another'
            with patch.object(loop, 'run', return_value=json.dumps(value)):
                with self.assertRaises(loop.Stop):
                    loop.read_pr(Path('.'), self.task(), HEAD)

    def test_existing_branch_is_fast_forwarded_not_recreated(self):
        with patch.object(loop, 'git', return_value=HEAD) as git, \
             patch.object(loop, 'command', return_value=(0, '', '')):
            loop.prepare_existing_branch(Path('.'), self.task())
            args = [call.args[1:] for call in git.call_args_list]
            self.assertIn(('switch', '--no-overwrite-ignore', 'feat/existing'), args)
            self.assertIn(('merge', '--ff-only', '--no-overwrite-ignore', 'origin/feat/existing'), args)
            self.assertFalse(any(a[0] == 'switch' and '-c' in a for a in args))

    def test_precheck_does_not_switch_or_merge(self):
        with patch.object(loop, 'git', return_value=HEAD) as git, \
             patch.object(loop, 'command', return_value=(0, '', '')):
            loop.prepare_existing_branch(Path('.'), self.task(), check_only=True)
            self.assertFalse(any(call.args[1] in ('switch', 'merge') for call in git.call_args_list))

    def test_remote_head_drift_stops_before_switch(self):
        with patch.object(loop, 'git', return_value='c' * 40) as git:
            with self.assertRaises(loop.Stop):
                loop.prepare_existing_branch(Path('.'), self.task())
            self.assertFalse(any(call.args[1] == 'switch' for call in git.call_args_list))

    def test_divergent_local_branch_is_preserved(self):
        with patch.object(loop, 'git', return_value=HEAD) as git, \
             patch.object(loop, 'command', side_effect=[(0, '', ''), (0, '', ''), (1, '', '')]):
            with self.assertRaises(loop.Stop):
                loop.prepare_existing_branch(Path('.'), self.task())
            self.assertFalse(any(call.args[1] in ('switch', 'reset', 'merge') for call in git.call_args_list))

    def test_successful_existing_review_never_calls_codex_or_creates_pr(self):
        self.exercise_pipeline()

    def test_blocking_finding_is_repaired_then_rereviewed_on_same_pr(self):
        self.exercise_pipeline(failures=1)

    def test_two_repairs_exhausted_stops_without_publication(self):
        self.exercise_pipeline(failures=3, should_stop=True)

    def test_full_mode_reuses_initial_full_pass_on_unchanged_bytes(self):
        self.assertEqual(self.exercise_pipeline(test_mode='full'), ['full'])

    def test_full_mode_repairs_with_related_then_runs_final_full(self):
        self.assertEqual(self.exercise_pipeline(test_mode='full', failures=1), ['full', 'related', 'full-final'])

    def test_related_mode_never_runs_local_full(self):
        self.assertEqual(self.exercise_pipeline(failures=1), ['related', 'related'])

    def test_final_full_failure_returns_to_bounded_repair(self):
        self.assertEqual(self.exercise_pipeline(test_mode='full', failures=1, final_full_failures=1),
                         ['full', 'related', 'full-final', 'related', 'full-final'])

    def test_new_implementation_keeps_required_full_and_mac_in_related_mode(self):
        self.assertEqual(self.exercise_pipeline(new_task=True), ['related', 'full-final', 'mandatory-final'])

    def test_quota_resume_continues_candidate_then_runs_all_gates_and_one_draft_pr(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True),
                         ['related', 'full-final', 'mandatory-final'])

    def test_mac_failure_stops_new_task_without_push_pr_or_retry(self):
        self.exercise_pipeline(new_task=True, mandatory_failure=True, should_stop=True)

    def test_new_task_repair_still_creates_only_one_draft_pr(self):
        self.exercise_pipeline(new_task=True, failures=1)

    def test_publication_hook_cannot_change_tested_bytes_and_still_push(self):
        self.exercise_pipeline(hook_changes=True, should_stop=True)

    def test_initial_decision_publishes_checkpoint_then_stops_without_tests_pr_or_resume(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, decision=True, should_stop=True), [])

    def test_checkpoint_failure_keeps_primary_decision_and_local_work(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, decision=True,
            checkpoint_failure=True, should_stop=True), [])

    def exercise_pipeline(self, failures=0, should_stop=False, test_mode='related', final_full_failures=0,
                          new_task=False, mandatory_failure=False, hook_changes=False,
                          decision=False, checkpoint_failure=False, resume=False):
        with tempfile.TemporaryDirectory() as runtime, ExitStack() as stack:
            root = Path(runtime)
            gitdir = root / 'git-metadata-fixture'
            (gitdir / 'info').mkdir(parents=True)
            (root / 'tools/llmtool/.venv/bin').mkdir(parents=True)
            (root / 'tools/llmtool/.venv/bin/python').touch()
            (root / 'source.txt').write_text('unchanged existing implementation')
            task = self.task()
            if new_task:
                task.update(mode='implement_new_pr', pr_number=None, expected_head=task['base_sha'],
                    require_local_full=True, mandatory_tests=[['fixture mandatory']],
                    required_files=['source.txt', 'reports/validation.md'], mac_evidence='mac-containment',
                    acceptance_report='reports/validation.md', pr_title='fixture title')
                task['publish_decision_checkpoint'] = True
                task['allowed_files'].append('reports/validation.md')
            current_head = [HEAD]
            dirty = [False]
            review_calls = [0]
            final_full_calls = [0]
            suites = []
            commands = []
            committed = [{'source.txt': loop.digest(root / 'source.txt')}]
            pr_created = [False]

            def fake_scope(*args):
                if not new_task:
                    return ['source.txt'] if dirty[0] else []
                now = {p: loop.digest(root / p) for p in task['allowed_files'] if (root / p).exists()}
                return sorted(p for p, h in now.items() if committed[0].get(p) != h)

            def fake_git(cwd, *args):
                commands.append(args)
                if args == ('rev-parse', '--show-toplevel'):
                    return str(root)
                if args == ('rev-parse', '--absolute-git-dir'):
                    return str(gitdir)
                if args == ('remote', 'get-url', 'origin'):
                    return 'git@github.com:' + loop.IDENTITY + '.git'
                if args == ('rev-parse', 'origin/main'):
                    return task['base_sha']
                if args == ('branch', '--show-current'):
                    return task['branch']
                if args == ('rev-parse', 'HEAD'):
                    return current_head[0]
                if args == ('rev-parse', '--git-path', 'info/exclude'):
                    return str(gitdir / 'info/exclude')
                if args == ('diff', '--name-only', '-z', task['base_sha'], HEAD):
                    return 'source.txt'
                if args[0] == 'commit':
                    if hook_changes and args[-1].endswith(' automated handoff'):
                        (root / 'source.txt').write_text('untested hook mutation')
                    if dirty[0]:
                        current_head[0] = 'e' * 40
                        dirty[0] = False
                    else:
                        current_head[0] = 'c' * 40
                    committed[0] = {p: loop.digest(root / p) for p in task['allowed_files'] if (root / p).exists()}
                return ''

            def fake_tests(cwd, logs, metadata, round_no, suite):
                suites.append(suite)
                passed = True
                if suite == 'full-final':
                    final_full_calls[0] += 1
                    passed = final_full_calls[0] > final_full_failures
                if suite == 'mandatory-final':
                    passed = not mandatory_failure
                loop.save_json(loop.test_result_file(logs, round_no, suite),
                               [{'argv': ['fake-' + suite], 'exit_code': 0 if passed else 1}])
                return passed

            def fake_claude(*args):
                review_calls[0] += 1
                value = result('FAIL' if review_calls[0] <= failures else 'PASS')
                value['reviewed_head'] = current_head[0]
                if value['status'] == 'FAIL':
                    value['findings'] = [{'severity': 'High', 'blocking': True, 'file': 'source.txt',
                                          'detail': 'fixture regression', 'evidence': 'fixture test failure'}]
                return value

            def fake_codex(*args):
                if decision:
                    (root / 'reports').mkdir(exist_ok=True)
                    (root / 'reports/validation.md').write_text('NEEDS_DECISION; no implementation.\n')
                    return {'status': 'NEEDS_DECISION', 'summary': 'Continuity requires a decision.'}
                dirty[0] = True
                (root / 'source.txt').write_text('bounded repair ' + str(review_calls[0]))
                if new_task:
                    (root / 'reports').mkdir(exist_ok=True)
                    (root / 'reports/validation.md').write_text('Candidate report; runner gates pending.\n')
                return {'status': 'PASS', 'summary': 'fixed fixture regression'}

            def fake_run(argv, *args, **kwargs):
                if argv[:3] == ['git', 'ls-remote', '--heads']:
                    return ''
                if argv[:3] == ['gh', 'pr', 'create']:
                    pr_created[0] = True
                    return self.pr()['html_url']
                if argv[:2] == ['gh', 'api']:
                    return json.dumps([{'number': 47, 'draft': True}]) if 'state=open' in argv[2] and pr_created[0] else '[]'
                return 'version fixture'

            stack.enter_context(patch.object(loop, 'REPO', root))
            stack.enter_context(patch.object(loop, 'git', side_effect=fake_git))
            stack.enter_context(patch.object(loop, 'load_task', return_value=(task, 'review existing PR')))
            stack.enter_context(patch.object(loop, 'untracked', return_value={}))
            stack.enter_context(patch.object(loop.shutil, 'which', return_value='/fake/bin'))
            stack.enter_context(patch.object(loop, 'prepare_existing_branch'))
            prepare_new = stack.enter_context(patch.object(loop, 'prepare_new_branch'))
            if resume:
                stack.enter_context(patch.object(loop, 'prepare_quota_resume', return_value=({},
                    {'from_run': '20261007T075141Z-70725', 'partial_files':
                     {'source.txt': loop.digest(root / 'source.txt')}, 'inherited_validation': 'NONE'})))
            stack.enter_context(patch.object(loop, 'read_pr', return_value=self.pr()))
            stack.enter_context(patch.object(loop, 'check_scope', side_effect=fake_scope))
            stack.enter_context(patch.object(loop, 'validate_mac_evidence', return_value={'fixture': 'Mac evidence is mocked here'}))
            stack.enter_context(patch.object(loop, 'tests', side_effect=fake_tests))
            stack.enter_context(patch.object(loop, 'claude', side_effect=fake_claude))
            codex = stack.enter_context(patch.object(loop, 'codex', side_effect=fake_codex))
            checkpoint = stack.enter_context(patch.object(loop, 'publish_decision_checkpoint',
                side_effect=loop.Stop('checkpoint API unavailable') if checkpoint_failure else None,
                return_value={'url': 'https://github.com/fixture/checkpoint', 'status': 'NEEDS_DECISION'}))
            runner = stack.enter_context(patch.object(loop, 'run', side_effect=fake_run))
            stack.enter_context(patch.object(loop, 'wait_ci', return_value=[{'status': 'completed', 'conclusion': 'success'}]))
            argv = ['ai_loop.py', '--control-sha', 'd' * 40, '--' + test_mode]
            if resume:
                argv += ['--resume', '20261007T075141Z-70725']
            stack.enter_context(patch.object(sys, 'argv', argv))
            previous = Path.cwd()
            os.chdir(root)
            try:
                if should_stop:
                    with self.assertRaises(loop.Stop) as stopped:
                        loop.main()
                    if decision:
                        self.assertIn('Codex reported NEEDS_DECISION', str(stopped.exception))
                        self.assertEqual(checkpoint.call_count, 1)
                        self.assertEqual(review_calls[0], 0)
                        summary = json.loads(next((root / '.ai-loop').glob('*/summary.json')).read_text())
                        self.assertEqual(summary['status'], 'STOPPED')
                        self.assertEqual(summary['checkpoint_publication_error'],
                            'checkpoint API unavailable' if checkpoint_failure else None)
                        self.assertEqual((root / 'source.txt').read_text(), 'unchanged existing implementation')
                else:
                    loop.main()
            finally:
                os.chdir(previous)
            self.assertEqual(codex.call_count, min(failures + final_full_failures, 2) + int(new_task))
            if resume:
                prepare_new.assert_not_called()
                self.assertIn('Continue the existing partial implementation', codex.call_args.args[2])
                self.assertTrue(next((root / '.ai-loop').glob('*/recovery-inputs/source.txt')).is_file())
            self.assertEqual(sum(call.args[0][:3] == ['gh', 'pr', 'create'] for call in runner.call_args_list), int(new_task and not should_stop))
            if new_task and not should_stop:
                creation = next(c.args[0] for c in runner.call_args_list if c.args[0][:3] == ['gh', 'pr', 'create'])
                self.assertIn('--draft', creation)
            self.assertEqual(any(call.args[0][:3] == ['gh', 'pr', 'edit'] for call in runner.call_args_list), not should_stop)
            self.assertEqual(any(a[:2] == ('push', '-u') for a in commands), not should_stop)
            self.assertFalse(any(a[0] == 'switch' and '-c' in a for a in commands))
            if not failures and not new_task and not hook_changes:
                self.assertEqual((root / 'source.txt').read_text(), 'unchanged existing implementation')
            self.assertEqual((gitdir / 'ai-loop/pr46-review.completed.json').is_file(), not should_stop)
            return suites


if __name__ == '__main__':
    unittest.main()
