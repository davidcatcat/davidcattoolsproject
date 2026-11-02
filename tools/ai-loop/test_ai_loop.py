"""Offline fixture checks; no project clone or provider calls."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import unittest
from unittest.mock import patch, Mock
from contextlib import ExitStack, contextmanager
import os
import copy

spec = importlib.util.spec_from_file_location('ai_loop', Path(__file__).with_name('ai_loop.py'))
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)
HEAD = 'a' * 40
TASK_DIR = Path(__file__).parents[1] / 'automation/ai-loop'
HISTORICAL_TASK = TASK_DIR / 'history/20261008T100821Z-76644.task.json'


def historical_task():
    """Return fresh archived recovery data, independent of the mutable active task."""
    return json.loads(HISTORICAL_TASK.read_text())


def result(status='PASS', findings=None):
    return {'status': status, 'summary': 'checked', 'reviewed_head': HEAD,
            'findings': findings or []}


class C1SpecialistQAGatesTests(unittest.TestCase):
    """No PR/source mutation, separate reviewer sessions, no fake role PASS."""

    @contextmanager
    def qa_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            head = '67d02be6bc8cb3f237c5060edda3321f6d885bf3'
            base = '8e29024656f873431f6ad83e45015b57bfe381a9'
            branch = 'feat/llmtool-c1-test-excerpts-20261009'
            paths = [
                'tools/llmtool/llmtool/test_output_excerpts.py',
                'tools/llmtool/llmtool/cli.py',
                'tools/llmtool/tests/test_test_output_excerpts.py',
                'tools/llmtool/tests/test_cli.py',
                'tools/llmtool/README.md',
                'reports/ai-loop/C1-test-excerpts-20261009-20261008T192953Z-22876.md',
            ]
            for name in paths:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('safe, fixed synthetic source')
            logs = root / '.ai-loop' / '20261009T000000Z-123'
            logs.mkdir(parents=True)
            task = {'task_id': 'C1-PR55-Security-Functional-QA-20261009',
                    'pr_number': 55, 'branch': branch, 'base_sha': base,
                    'expected_head': head, 'allowed_files': paths,
                    'qa_gates': ['security', 'functional_qa'],
                    'timeout_seconds': 1800, 'claude_timeout_seconds': 2400,
                    'ci_workflow': 'llmtool-ci.yml'}
            def git(_root, *args):
                match = {
                    ('branch', '--show-current'): branch,
                    ('rev-parse', 'HEAD'): head,
                    ('diff', '--name-only', '-z', base, head): '\0'.join(paths) + '\0',
                    ('diff', '--no-ext-diff', '--no-textconv', base, head):
                        'diff --git a/safe.py b/safe.py\n+safe reviewer fixture',
                    ('status', '--porcelain'): '',
                }
                if args not in match:
                    raise AssertionError(args)
                return match[args]
            with patch.object(loop, 'git', side_effect=git), \
                 patch.object(loop, 'read_pr', return_value={'head': {'sha': head}, 'draft': True}), \
                 patch.object(loop, 'wait_ci', return_value=[{'conclusion': 'success'}]), \
                 patch.object(loop, 'check_scope', return_value=[]):
                yield root, logs, task, head

    def test_dual_gate_pass_posts_only_bounded_metadata_and_does_not_modify_source(self):
        with self.qa_fixture() as (root, logs, task, head):
            role_calls = []
            comments = []
            suites = []
            def fake_review(_root, _logs, prompt, round_no, timeout, tested_head):
                role_calls.append((round_no, prompt, tested_head))
                return {'status': 'PASS', 'reviewed_head': head,
                        'summary': 'PRIVATE-SUMMARY-DO-NOT-PUBLISH',
                        'findings': [{'severity': 'Low', 'blocking': False,
                            'file': task['allowed_files'][0],
                            'detail': 'PRIVATE-DETAIL-DO-NOT-PUBLISH',
                            'evidence': 'PRIVATE-EVIDENCE-DO-NOT-PUBLISH'}]}
            def fake_run(argv, _root):
                self.assertEqual(argv[:4], ['gh', 'api', '--method', 'POST'])
                comments.append(argv[-1])
                return '{"id":123}'
            def fake_tests(_root, _logs, _task, _round, suite):
                suites.append(suite)
                return True
            before = {p: (root / p).read_bytes() for p in task['allowed_files']}
            with patch.object(loop, 'tests', side_effect=fake_tests), \
                 patch.object(loop, 'claude', side_effect=fake_review), \
                 patch.object(loop, 'run', side_effect=fake_run):
                loop.independent_qa_pr55(root, logs, task, 'task evidence', {})
            self.assertEqual(suites, ['related', 'full-final'])
            self.assertEqual([x[0] for x in role_calls], [201, 202])
            self.assertIn('SECURITY gate:', role_calls[0][1])
            self.assertIn('FUNCTIONAL QA gate:', role_calls[1][1])
            for _, prompt, _ in role_calls:
                self.assertIn('AUTHORITATIVE CONTROL HANDOFF', prompt)
                self.assertIn('intentionally', prompt)
            self.assertEqual([x[2] for x in role_calls], [head, head])
            self.assertEqual(len(comments), 1)
            self.assertIn('body=', comments[0])
            for forbidden in ['PRIVATE-SUMMARY-', 'PRIVATE-DETAIL-', 'PRIVATE-EVIDENCE-']:
                self.assertNotIn(forbidden, comments[0])
            self.assertEqual(before, {p: (root / p).read_bytes() for p in task['allowed_files']})
            report = json.loads((logs / 'qa-pr55.json').read_text())
            self.assertEqual(report['qa_result'], 'PASS')
            self.assertFalse(report['merge_authorized'])
            self.assertEqual(len(report['roles']), 2)

    def test_first_specialist_blocker_never_calls_second_reviewer_or_claims_pass(self):
        with self.qa_fixture() as (root, logs, task, head):
            calls, public = [], []
            def bad_review(_root, _logs, prompt, round_no, timeout, sha):
                calls.append(round_no)
                return {'status': 'FAIL', 'reviewed_head': head,
                        'summary': 'PRIVATE-SECURITY-SUMMARY',
                        'findings': [{'severity': 'High', 'blocking': True,
                            'file': task['allowed_files'][0],
                            'detail': 'PRIVATE-ISSUE-DETAIL', 'evidence': 'secret-content'}]}
            with patch.object(loop, 'tests', return_value=True), \
                 patch.object(loop, 'claude', side_effect=bad_review), \
                 patch.object(loop, 'run', side_effect=lambda a, r: public.append(a[-1]) or '{"id":1}'):
                with self.assertRaisesRegex(loop.Stop, 'specialist review not PASS'):
                    loop.independent_qa_pr55(root, logs, task, 'task', {})
            self.assertEqual(calls, [201])
            self.assertEqual(len(public), 1)
            self.assertNotIn('PRIVATE-ISSUE-DETAIL', public[0])
            self.assertEqual(json.loads((logs / 'qa-pr55.json').read_text())['qa_result'], 'STOPPED')

    def test_medium_security_pass_forbids_second_reviewer_and_final_pass(self):
        with self.qa_fixture() as (root, logs, task, head):
            calls = []
            def medium_review(_root, _logs, prompt, round_no, timeout, sha):
                calls.append(round_no)
                return {'status': 'PASS', 'reviewed_head': head,
                        'summary': 'PRIVATE-MEDIUM-REVIEW',
                        'findings': [{'severity': 'Medium', 'blocking': False,
                            'file': task['allowed_files'][0],
                            'detail': 'PRIVATE-REGRESSION', 'evidence': 'PRIVATE-TRACE'}]}
            with patch.object(loop, 'tests', return_value=True), \
                 patch.object(loop, 'claude', side_effect=medium_review), \
                 patch.object(loop, 'run', return_value='{"id":1}'):
                with self.assertRaisesRegex(loop.Stop, 'specialist review not PASS'):
                    loop.independent_qa_pr55(root, logs, task, 'task', {})
            self.assertEqual(calls, [201], 'do not spend QA tokens after Security Medium')
            summary = json.loads((logs / 'qa-pr55.json').read_text())
            self.assertEqual(summary['qa_result'], 'STOPPED')
            self.assertFalse(summary['merge_authorized'])

    def test_new_postfix_head_and_seven_path_inventory_read_only(self):
        with self.qa_fixture() as (root, logs, task, original_head):
            new_head = 'd5ecfce9f7e28da2983affb235dd942d51388723'
            new_report = ('reports/ai-loop/'
                'C1-PR55-Medium-InputValidation-Fix-20261009-20261008T213245Z-37499.md')
            (root / new_report).write_text('FROZEN SOURCE REPORT')
            task['allowed_files'].append(new_report)
            task['expected_head'] = new_head
            task['task_id'] = 'C1-PR55-PostFix-Security-Functional-QA-20261009'
            def newgit(_root, *args):
                if args == ('branch', '--show-current'):
                    return task['branch']
                if args == ('rev-parse', 'HEAD'):
                    return new_head
                if args == ('status', '--porcelain'):
                    return ''
                if args == ('diff', '--name-only', '-z', task['base_sha'], new_head):
                    return '\0'.join(task['allowed_files']) + '\0'
                if args == ('diff', '--no-ext-diff', '--no-textconv', task['base_sha'], new_head):
                    return 'diff --git a/new_source b/new_source\n+new QA fixture'
                raise AssertionError(args)
            roles, publications = [], []
            def review(_root, _logs, prompt, round_no, timeout, sha):
                roles.append((round_no, sha, prompt))
                return {'status':'PASS', 'reviewed_head':new_head, 'summary':'OK', 'findings':[]}
            with patch.object(loop, 'git', side_effect=newgit), \
                 patch.object(loop, 'read_pr', return_value={'head':{'sha':new_head}, 'draft':True}), \
                 patch.object(loop, 'tests', return_value=True), \
                 patch.object(loop, 'claude', side_effect=review), \
                 patch.object(loop, 'run', side_effect=lambda args, cwd: publications.append(args) or '{"id":1}'):
                loop.independent_qa_pr55(root, logs, task, 'postfix task', {})
            self.assertEqual([r[0] for r in roles], [201, 202])
            self.assertEqual([r[1] for r in roles], [new_head, new_head])
            self.assertIn('catastrophic', roles[0][2])
            self.assertIn('subprocess-deadline', roles[1][2])
            self.assertIn(new_report, task['allowed_files'])
            self.assertEqual(len(publications), 1)
            self.assertEqual(json.loads((logs/'qa-pr55.json').read_text())['qa_result'], 'PASS')

    def test_no_models_on_wrong_revision_or_test_failure(self):
        with self.qa_fixture() as (root, logs, task, head):
            with patch.object(loop, 'claude') as review, \
                 patch.object(loop, 'tests') as tests_run:
                task['expected_head'] = 'f' * 40
                with self.assertRaisesRegex(loop.Stop, 'HEAD/branch mismatch'):
                    loop.independent_qa_pr55(root, logs, task, 'task', {})
                tests_run.assert_not_called()
                review.assert_not_called()
                task['expected_head'] = head
            with patch.object(loop, 'tests', return_value=False), \
                 patch.object(loop, 'claude') as review:
                with self.assertRaisesRegex(loop.Stop, 'related tests failed'):
                    loop.independent_qa_pr55(root, logs, task, 'task', {})
                review.assert_not_called()

class PR55FinalJWTReviewTests(unittest.TestCase):
    """Final source-revision QA is fresh, two-role and cannot inherit old PASS."""

    @contextmanager
    def qa_case(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            head='ee8180bc3ad5c455b63e493fee9f2955a90c5463'
            paths=[
                'tools/llmtool/llmtool/test_output_excerpts.py',
                'tools/llmtool/llmtool/cli.py',
                'tools/llmtool/tests/test_test_output_excerpts.py',
                'tools/llmtool/tests/test_cli.py',
                'tools/llmtool/README.md',
                'reports/ai-loop/C1-test-excerpts-20261009-20261008T192953Z-22876.md',
                'reports/ai-loop/C1-PR55-Medium-InputValidation-Fix-20261009-20261008T213245Z-37499.md',
                'reports/ai-loop/C1-PR55-SecurityFail-ResourceFix-20261009-20261009T043757Z-54800.md']
            for p in paths:
                file=root/p
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('fixed, private dummy file')
            logs=root/'.ai-loop'/'20261009T140000Z-4242'
            logs.mkdir(parents=True)
            task={
                'task_id':'C1-PR55-PostJWT-Security-Functional-QA-20261009',
                'mode':'review_existing_pr','pr_number':55,
                'branch':'feat/llmtool-c1-test-excerpts-20261009',
                'expected_head':head,
                'base_sha':'8e29024656f873431f6ad83e45015b57bfe381a9',
                'allowed_files':paths,
                'qa_gates':['security','functional_qa'], 'max_repairs':0,
                'timeout_seconds':1800,'ci_workflow':'llmtool-ci.yml'}
            before={p:(root/p).read_bytes() for p in paths}
            def git(_root,*args):
                table={
                    ('branch','--show-current'):task['branch'],
                    ('rev-parse','HEAD'):head,
                    ('status','--porcelain'):'',
                    ('diff','--name-only','-z',task['base_sha'],head):'\0'.join(paths)+'\0',
                    ('diff','--no-ext-diff','--no-textconv',task['base_sha'],head):
                        'diff --git a/test_output_excerpts.py b/test_output_excerpts.py\n+_sensitive_jwt'
                }
                if args not in table:
                    raise AssertionError(args)
                return table[args]
            with patch.object(loop,'git',side_effect=git), \
                 patch.object(loop,'check_scope',return_value=[]), \
                 patch.object(loop,'read_pr',return_value={'head':{'sha':head},'draft':True}), \
                 patch.object(loop,'wait_ci',return_value=[{'conclusion':'success'}]):
                yield root,logs,task,head,before

    def test_new_head_two_independent_reviews_have_specific_jwt_attack_rubric(self):
        with self.qa_case() as (root,logs,task,head,before):
            calls,public,suites=[],[],[]
            def reviewer(_root,_logs,prompt,number,timeout,reviewed):
                calls.append((number,prompt,reviewed))
                return {'status':'PASS','reviewed_head':head,
                        'summary':'PRIVATE-JWT-REVIEW','findings':[]}
            with patch.object(loop,'tests',side_effect=lambda *args:suites.append(args[4]) or True), \
                 patch.object(loop,'claude',side_effect=reviewer), \
                 patch.object(loop,'run',side_effect=lambda args,_root:public.append(args[-1]) or '{"id":1}'):
                loop.independent_qa_pr55(root,logs,task,'post-JWT authoritative contract',{})
            self.assertEqual(suites,['related','full-final'])
            self.assertEqual([n for n,_,_ in calls],[201,202])
            self.assertEqual([h for _,_,h in calls],[head,head])
            self.assertIn('MANDATORY FINAL JWT/URL SECURITY REGRESSION',calls[0][1])
            self.assertIn('original Medium blocking failure',calls[0][1])
            self.assertIn('MANDATORY FINAL JWT/URL FUNCTIONAL REGRESSION',calls[1][1])
            self.assertEqual(before,{p:(root/p).read_bytes() for p in task['allowed_files']})
            self.assertEqual(json.loads((logs/'qa-pr55.json').read_text())['qa_result'],'PASS')
            self.assertEqual(len(public),1)
            self.assertNotIn('PRIVATE-JWT-REVIEW',public[0])

    def test_new_head_medium_even_model_pass_blocks_functional_qa(self):
        with self.qa_case() as (root,logs,task,head,before):
            calls,public=[],[]
            def reviewer(_root,_logs,prompt,number,timeout,reviewed):
                calls.append(number)
                return {'status':'PASS','reviewed_head':head,
                    'summary':'PRIVATE-JWT-RISK-DETAIL',
                    'findings':[{'severity':'Medium','blocking':False,
                        'file':task['allowed_files'][0],
                        'detail':'PRIVATE-ISSUE-DETAIL',
                        'evidence':'PRIVATE-ISSUE-EVIDENCE'}]}
            with patch.object(loop,'tests',return_value=True), \
                 patch.object(loop,'claude',side_effect=reviewer), \
                 patch.object(loop,'run',side_effect=lambda args,_root:public.append(args[-1]) or '{"id":1}'):
                with self.assertRaisesRegex(loop.Stop,'specialist review not PASS'):
                    loop.independent_qa_pr55(root,logs,task,'postfix task',{})
            self.assertEqual(calls,[201])
            self.assertEqual(json.loads((logs/'qa-pr55.json').read_text())['qa_result'],'STOPPED')
            self.assertEqual(len(public),1)
            self.assertNotIn('PRIVATE-ISSUE-',public[0])
            self.assertNotIn('PRIVATE-JWT-RISK-',public[0])

    def test_older_head_draft_or_eight_path_inventory_drifts_before_models(self):
        with self.qa_case() as (root,logs,task,head,before):
            task['allowed_files']=task['allowed_files'][:-1]
            with patch.object(loop,'tests') as suites,patch.object(loop,'claude') as reviewer:
                with self.assertRaisesRegex(loop.Stop,'changed-file inventory'):
                    loop.independent_qa_pr55(root,logs,task,'bad inventory',{})
                suites.assert_not_called()
                reviewer.assert_not_called()

class PR55MediumRepairAuthorityTests(unittest.TestCase):
    """The one original private Medium finding is the ONLY new writer authority."""

    @contextmanager
    def evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / '.ai-loop' / '20261008T202910Z-31100'
            logs.mkdir(parents=True)
            head = '67d02be6bc8cb3f237c5060edda3321f6d885bf3'
            source = 'tools/llmtool/llmtool/test_output_excerpts.py'
            test = 'tools/llmtool/tests/test_test_output_excerpts.py'
            finding = {'severity': 'Medium', 'blocking': False, 'file': source,
                       'detail': 'PRIVATE-MEDIUM-REVIEW-DETAIL',
                       'evidence': 'PRIVATE-MEDIUM-REVIEW-EVIDENCE'}
            risk_sha = hashlib.sha256(json.dumps(
                finding, sort_keys=True, ensure_ascii=False,
                separators=(',', ':')).encode()).hexdigest()
            role = {'status': 'PASS', 'reviewed_head': head, 'summary': 'PRIVATE-ROLE-SUMMARY',
                    'findings': [finding] + [
                        {'severity': 'Low', 'blocking': False, 'file': source,
                         'detail': 'LOW', 'evidence': 'SOURCE'} for _ in range(2)]}
            task = {'pr_number': 55, 'base_sha': '8e29024656f873431f6ad83e45015b57bfe381a9',
                    'expected_head': head, 'branch': 'feat/llmtool-c1-test-excerpts-20261009',
                    'qa_medium_repair': {'run_id': logs.name, 'head': head,
                        'original_finding_sha256': risk_sha,
                        'risk_classification': 'INPUT_VALIDATION',
                        'risk_action': 'FIX_BEFORE_MERGE',
                        'source_file': source, 'test_file': test}}
            loop.save_json(logs / 'task.json', {
                'qa_gates': ['security', 'functional_qa'], 'expected_head': head,
                'pr_number': 55})
            loop.save_json(logs / 'qa-pr55.json', {
                'run_id': logs.name, 'head': head, 'pr': 55,
                'diff_sha256': 'f977cb5dcd8ab47dc787cb0d1f439e843d0bff99dd1f69e018157a28348c7ea8',
                'qa_result': 'PASS', 'merge_authorized': False,
                'roles': [{'role':'security', **role}, {'role':'functional_qa', 'status':'PASS'}]})
            loop.save_json(logs / 'claude-201.json', role)
            medium_dir = root / '.ai-loop' / 'qa-medium-issue56'
            medium_dir.mkdir()
            loop.save_json(medium_dir / 'assessment.json', {
                'reviewed_head': head, 'original_finding_sha256': risk_sha,
                'action': 'FIX_BEFORE_MERGE', 'category': 'INPUT_VALIDATION',
                'confidence': 'HIGH', 'source_path': source, 'line': 46,
                'private_reason': 'PRIVATE-INDEPENDENT-REASON'})
            def git(_, *args):
                values = {
                    ('rev-parse', 'HEAD'): head,
                    ('branch', '--show-current'): task['branch'],
                    ('status', '--porcelain'): '',
                    ('diff', '--no-ext-diff', '--no-textconv', task['base_sha'], head):
                        'frozen PR55 patch'}
                if args not in values:
                    raise AssertionError(args)
                return values[args]
            real_sha = hashlib.sha256
            def spoof_patch_hash(data):
                if data == b'frozen PR55 patch':
                    obj = Mock()
                    obj.hexdigest.return_value = 'f977cb5dcd8ab47dc787cb0d1f439e843d0bff99dd1f69e018157a28348c7ea8'
                    return obj
                return real_sha(data)
            with patch.object(loop, 'git', side_effect=git), \
                 patch.object(loop.hashlib, 'sha256', side_effect=spoof_patch_hash):
                yield root, task, logs, finding

    def test_exact_frozen_medium_authorizes_only_regression_scoped_reason(self):
        with self.evidence() as (root, task, logs, finding):
            with patch.object(loop, 'codex') as codex, patch.object(loop, 'claude') as claude:
                instruction = loop.verify_pr55_medium_repair(root, task)
                codex.assert_not_called()
                claude.assert_not_called()
            self.assertIn(finding['detail'], instruction)
            self.assertIn(finding['evidence'], instruction)
            self.assertIn('ONLY', instruction)
            self.assertIn(task['qa_medium_repair']['test_file'], instruction)
            self.assertIn('No prior tests/Claude QA results count as PASS', instruction)

    def test_medium_repair_rejects_symlinked_private_assessment_directory(self):
        with self.evidence() as (root, task, logs, finding):
            source = root / '.ai-loop' / 'qa-medium-issue56'
            destination = root / 'outside-private'
            source.rename(destination)
            source.symlink_to(destination, target_is_directory=True)
            with self.assertRaisesRegex(loop.Stop, 'private evidence absent or unsafe'):
                loop.verify_pr55_medium_repair(root, task)

    def test_medium_repair_rejects_tampered_action_head_finding_and_qa_gate(self):
        with self.evidence() as (root, task, logs, finding):
            assessment = root / '.ai-loop' / 'qa-medium-issue56' / 'assessment.json'
            data = json.loads(assessment.read_text())
            data['action'] = 'DEFER_WITH_EXPLICIT_RISK_ACCEPTANCE'
            loop.save_json(assessment, data)
            with self.assertRaisesRegex(loop.Stop, 'not proved'):
                loop.verify_pr55_medium_repair(root, task)
            data['action'] = 'FIX_BEFORE_MERGE'
            loop.save_json(assessment, data)
            task['qa_medium_repair']['original_finding_sha256'] = 'e'*64
            with self.assertRaisesRegex(loop.Stop, 'not proved'):
                loop.verify_pr55_medium_repair(root, task)
            task['qa_medium_repair']['original_finding_sha256'] = data['original_finding_sha256']
            evidence = logs / 'qa-pr55.json'
            report = json.loads(evidence.read_text())
            report['qa_result'] = 'STOPPED'
            loop.save_json(evidence, report)
            with self.assertRaisesRegex(loop.Stop, 'specialist evidence mismatch'):
                loop.verify_pr55_medium_repair(root, task)


class FailedCodexEditedCandidateResumeTests(unittest.TestCase):
    """Codex FAIL with edited source is never PASS; reuse exact bytes only for tests."""

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            logs=root/'.ai-loop'/'20261009T041323Z-53171'
            logs.mkdir(parents=True)
            files=[
                'tools/llmtool/llmtool/test_output_excerpts.py',
                'tools/llmtool/tests/test_test_output_excerpts.py']
            old_head='d5ecfce9f7e28da2983affb235dd942d51388723'
            control='423580283c35be98f1e8fa760a8dc27626c52da3'
            sha_report='3469bb8103584e61937205e8a80aaf170827c9facc6d47756742eaf1100705f4'
            contract={'task_id':'C1-PR55-SecurityFail-ResourceFix-20261009',
                'mode':'review_existing_pr','pr_number':55,
                'expected_head':old_head,
                'base_sha':'8e29024656f873431f6ad83e45015b57bfe381a9',
                'branch':'feat/llmtool-c1-test-excerpts-20261009',
                'allowed_files':files,'max_repairs':0}
            original=contract.copy()
            contents={}
            for i,p in enumerate(files):
                dest=root/p
                dest.parent.mkdir(parents=True,exist_ok=True)
                data=('candidate bytes, source '+str(i)).encode()
                dest.write_bytes(data)
                contents[p]=hashlib.sha256(data).hexdigest()
            contract['failed_codex_candidate_recovery']={
                'run_id':logs.name,'control_sha':control,
                'original_summary_sha256':sha_report,
                'source_sha256':contents}
            # Match the runner's actual STOPPED summary: run_id/task_id live
            # in provenance/task.json, not the STOPPED summary itself.
            summary={'status':'STOPPED','head':old_head,
                'branch':contract['branch'],'history':[],
                'reason':'Codex reported FAIL: Repair candidate prepared in exactly the two authorized files. Tests pending.'}
            oldcodex={'status':'FAIL','summary':
                'Repair candidate prepared in exactly the two authorized files. Tests pending.'}
            loop.save_json(logs/'task.json',original)
            loop.save_json(logs/'provenance.json',{
                'root':str(root),'run_id':logs.name,'control_sha':control,
                'base':contract['base_sha'],'start_head':old_head,
                'start_branch':contract['branch']})
            loop.save_json(logs/'codex-0.json',oldcodex)
            loop.save_json(logs/'summary.json',summary)
            (logs/'task.md').write_text('original frozen task')
            encoded=(logs/'summary.json').read_bytes()
            real_sha=hashlib.sha256
            def digest_sha(raw):
                if raw==encoded:
                    return Mock(hexdigest=Mock(return_value=sha_report))
                return real_sha(raw)
            def git(_root,*args):
                if args==('branch','--show-current'):
                    return contract['branch']
                if args==('rev-parse','HEAD'):
                    return old_head
                if args==('diff','--cached','--name-only'):
                    return ''
                if args==('diff','--name-only','-z','HEAD'):
                    return '\0'.join(files)+'\0'
                if args==('ls-files','--others','--exclude-standard','-z'):
                    return ''
                if len(args)==3 and args[0]=='hash-object':
                    return 'candidate-blob'
                if len(args)==2 and args[0]=='rev-parse' and args[1].startswith('HEAD:'):
                    return 'original-blob'
                raise AssertionError(args)
            with patch.object(loop,'git',side_effect=git), \
                 patch.object(loop,'load_task',return_value=(original,'original frozen task')), \
                 patch.object(loop,'read_pr',return_value={'head':{'sha':old_head},'draft':True}), \
                 patch.object(loop,'ensure_no_active_runner_codex',return_value=None), \
                 patch.object(loop.hashlib,'sha256',side_effect=digest_sha):
                yield root,logs,contract,files

    def test_fail_status_exact_modified_bytes_can_only_enter_fresh_validation(self):
        with self.fixture() as (root,logs,task,files):
            original,policy=loop.prepare_failed_codex_candidate_resume(
                root,task,logs.name,{},files)
            self.assertEqual(original,{})
            self.assertEqual(policy['kind'],'failed_codex_candidate')
            self.assertEqual(policy['previous_codex_status'],'FAIL')
            self.assertEqual(set(policy['partial_files']),set(files))
            self.assertEqual(policy['inherited_validation'],'NONE')
            with patch.object(loop,'codex') as codex:
                self.assertEqual(policy['previous_codex_status'],'FAIL')
                codex.assert_not_called()

    def test_tampered_candidate_file_and_unrelated_dirty_path_stop(self):
        with self.fixture() as (root,logs,task,files):
            (root/files[0]).write_text('UNAUTHORIZED changed bytes')
            with self.assertRaisesRegex(loop.Stop,'hash/symlink drift'):
                loop.prepare_failed_codex_candidate_resume(root,task,logs.name,{},files)
            with self.assertRaisesRegex(loop.Stop,'dirty-file scope'):
                loop.prepare_failed_codex_candidate_resume(root,task,logs.name,{},files[:1])

    def test_nonfail_codex_cannot_be_promoted_to_pass(self):
        with self.fixture() as (root,logs,task,files):
            saved=logs/'codex-0.json'
            record=json.loads(saved.read_text())
            record['status']='PASS'
            loop.save_json(saved,record)
            with self.assertRaisesRegex(loop.Stop,'original developer FAIL'):
                loop.prepare_failed_codex_candidate_resume(root,task,logs.name,{},files)

    def test_sha_contract_and_symlink_path_fail_closed(self):
        with self.fixture() as (root,logs,task,files):
            task['failed_codex_candidate_recovery']['source_sha256'][files[0]]='f'*64
            with self.assertRaisesRegex(loop.Stop,'hash/symlink drift'):
                loop.prepare_failed_codex_candidate_resume(root,task,logs.name,{},files)
            task['failed_codex_candidate_recovery']['source_sha256'][files[0]]=loop.digest(root/files[0])
            p=root/files[1]
            old=p.read_bytes()
            p.unlink()
            p.symlink_to(root/files[0])
            with self.assertRaisesRegex(loop.Stop,'hash/symlink drift'):
                loop.prepare_failed_codex_candidate_resume(root,task,logs.name,{},files)


class PR55BlockingResourceRepairTests(unittest.TestCase):
    """Authorization requires a real frozen FAIL, not an inferred QA PASS."""

    @contextmanager
    def evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / '.ai-loop' / '20261008T221753Z-44339'
            logs.mkdir(parents=True)
            head = 'd5ecfce9f7e28da2983affb235dd942d51388723'
            source = 'tools/llmtool/llmtool/test_output_excerpts.py'
            testfile = 'tools/llmtool/tests/test_test_output_excerpts.py'
            finding = {'severity': 'Medium', 'blocking': True, 'file': source,
                'detail': 'PRIVATE-FAIL-DETAIL',
                'evidence': 'PRIVATE-FAIL-ORIGINAL-EVIDENCE'}
            review = {'status':'FAIL', 'reviewed_head':head,
                'summary':'PRIVATE-SECURITY-FAIL', 'findings':[finding]}
            sha_finding = hashlib.sha256(json.dumps(
                finding, sort_keys=True, ensure_ascii=False,
                separators=(',', ':')).encode()).hexdigest()
            task = {'task_id': 'C1-PR55-SecurityFail-ResourceFix-20261009',
                'pr_number': 55,
                'expected_head': head,
                'base_sha': '8e29024656f873431f6ad83e45015b57bfe381a9',
                'branch': 'feat/llmtool-c1-test-excerpts-20261009',
                'qa_security_fail_repair': {
                    'head': head,
                    'run_id': logs.name,
                    'diff_sha256': '6662b084538b44f3ad9f7ee873f5c08d23d60cef2b4c49e849c6d0de0c1613f2',
                    'finding_sha256': sha_finding,
                    'category': 'RESOURCE_EXHAUSTION',
                    'action': 'FIX_BEFORE_MERGE',
                    'confidence': 'HIGH', 'line': 42,
                    'source_file': source, 'test_file': testfile}}
            loop.save_json(logs/'task.json', {
                'task_id':'C1-PR55-PostFix-Security-Functional-QA-20261009',
                'pr_number':55, 'expected_head':head,
                'qa_gates':['security','functional_qa']})
            loop.save_json(logs/'provenance.json',
                {'root':str(root), 'run_id':logs.name})
            loop.save_json(logs/'claude-201.json', review)
            loop.save_json(logs/'qa-pr55.json', {
                'run_id':logs.name, 'head':head, 'pr':55,
                'diff_sha256':task['qa_security_fail_repair']['diff_sha256'],
                'qa_result':'STOPPED','tests_related':'PASS',
                'tests_full_local':'PASS','merge_authorized':False,
                'roles':[{'role':'security', **review}]})
            result_dir = root/'.ai-loop'/'qa-postfix-security-fail-issue56'
            result_dir.mkdir()
            loop.save_json(result_dir/'assessment.json', {
                'reviewed_head':head, 'original_finding_sha256':sha_finding,
                'category':'RESOURCE_EXHAUSTION',
                'action':'FIX_BEFORE_MERGE', 'confidence':'HIGH',
                'source_path':source,'line':42,
                'private_reason':'PRIVATE-ROOT-CAUSE-TECHNICAL-JUSTIFICATION'})
            original_hash = hashlib.sha256
            diff = 'unchanged-pr55-fixture-patch'
            def mock_sha(content):
                if content == diff.encode():
                    return Mock(hexdigest=Mock(return_value=task['qa_security_fail_repair']['diff_sha256']))
                return original_hash(content)
            def git(_root, *args):
                values = {
                    ('rev-parse','HEAD'):head,
                    ('branch','--show-current'):task['branch'],
                    ('status','--porcelain'):'',
                    ('diff','--no-ext-diff','--no-textconv',task['base_sha'],head):diff}
                if args not in values:
                    raise AssertionError(args)
                return values[args]
            with patch.object(loop,'git',side_effect=git), \
                 patch.object(loop.hashlib,'sha256',side_effect=mock_sha):
                yield root,logs,task,finding

    def test_frozen_security_fail_can_authorize_only_source_and_test(self):
        with self.evidence() as (root,logs,task,finding):
            with patch.object(loop,'codex') as codex, \
                 patch.object(loop,'claude') as claude:
                reason=loop.verify_pr55_security_fail_repair(root,task)
                codex.assert_not_called()
                claude.assert_not_called()
            self.assertIn('RESOURCE_EXHAUSTION',reason)
            self.assertIn(finding['detail'],reason)
            self.assertIn(finding['evidence'],reason)
            self.assertIn(task['qa_security_fail_repair']['test_file'],reason)
            self.assertIn('CANNOT be inherited',reason)

    def test_modified_old_security_finding_or_false_pass_never_authorizes(self):
        with self.evidence() as (root,logs,task,finding):
            original=logs/'claude-201.json'
            a=json.loads(original.read_text())
            a['status']='PASS'
            loop.save_json(original,a)
            with self.assertRaises(loop.Stop):
                loop.verify_pr55_security_fail_repair(root,task)
            a['status']='FAIL'
            loop.save_json(original,a)
            task['qa_security_fail_repair']['finding_sha256']='f'*64
            with self.assertRaisesRegex(loop.Stop,'risk authority mismatch'):
                loop.verify_pr55_security_fail_repair(root,task)

    def test_wrong_independent_risk_decision_or_symlink_parent_stops(self):
        with self.evidence() as (root,logs,task,finding):
            resultdir=root/'.ai-loop'/'qa-postfix-security-fail-issue56'
            assessment=resultdir/'assessment.json'
            row=json.loads(assessment.read_text())
            row['action']='NEEDS_DEEPER_REVIEW'
            loop.save_json(assessment,row)
            with self.assertRaisesRegex(loop.Stop,'risk authority mismatch'):
                loop.verify_pr55_security_fail_repair(root,task)
            row['action']='FIX_BEFORE_MERGE'
            loop.save_json(assessment,row)
            renamed=root/'outside-risk-evidence'
            resultdir.rename(renamed)
            resultdir.symlink_to(renamed, target_is_directory=True)
            with self.assertRaisesRegex(loop.Stop,'private evidence missing/unsafe'):
                loop.verify_pr55_security_fail_repair(root,task)


class PostFixSecurityFailTriageTests(unittest.TestCase):
    """No untrusted Security FAIL text may reach GitHub or become merge PASS."""

    @contextmanager
    def evidence(self, *, assessor_error=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logs = root / '.ai-loop' / '20261008T221753Z-44339'
            logs.mkdir(parents=True)
            head = 'd5ecfce9f7e28da2983affb235dd942d51388723'
            filepath = 'tools/llmtool/llmtool/test_output_excerpts.py'
            task = {'task_id': 'C1-PR55-PostFix-Security-Functional-QA-20261009',
                    'pr_number':55, 'qa_gates':['security','functional_qa'],
                    'base_sha':'8e29024656f873431f6ad83e45015b57bfe381a9',
                    'branch':'feat/llmtool-c1-test-excerpts-20261009',
                    'expected_head':head, 'allowed_files':[filepath]}
            finding = {'severity':'High', 'blocking':True, 'file':filepath,
                       'detail':'PRIVATE-BLOCKING-FINDING-TEXT',
                       'evidence':'PRIVATE-SOURCE-LINE-EVIDENCE'}
            review = {'status':'FAIL', 'reviewed_head':head,
                      'summary':'PRIVATE-SECURITY-FAIL-SUMMARY', 'findings':[finding]}
            loop.save_json(logs / 'task.json', task)
            loop.save_json(logs / 'provenance.json',
                           {'run_id':logs.name, 'root':str(root)})
            loop.save_json(logs / 'qa-pr55.json', {
                'run_id':logs.name, 'head':head, 'pr':55,
                'diff_sha256':'6662b084538b44f3ad9f7ee873f5c08d23d60cef2b4c49e849c6d0de0c1613f2',
                'qa_result':'STOPPED', 'tests_related':'PASS', 'tests_full_local':'PASS',
                'merge_authorized':False, 'product_provider_egress_authorized':False,
                'roles':[{'role':'security', **review}]})
            loop.save_json(logs / 'claude-201.json', review)
            digest = hashlib.sha256(json.dumps(
                finding, sort_keys=True, ensure_ascii=False,
                separators=(',', ':')).encode()).hexdigest()
            structured = {'reviewed_head':head, 'original_finding_sha256':digest,
                          'category':'RESOURCE_EXHAUSTION',
                          'action':'FIX_BEFORE_MERGE', 'confidence':'HIGH',
                          'source_path':filepath, 'line':55,
                          'private_reason':'PRIVATE-ENGINEERING-ROOT-CAUSE'}
            posts, model_calls = [], []
            original_sha = hashlib.sha256
            def fake_sha(raw):
                if raw == b'frozen-diff-fixture':
                    result = Mock()
                    result.hexdigest.return_value = ('6662b084538b44f3ad9f7ee873f5c08d23d60cef2b4c49e849c6d0de0c1613f2')
                    return result
                return original_sha(raw)
            def fake_git(_root, *args):
                if args == ('branch','--show-current'):
                    return task['branch']
                if args == ('rev-parse','HEAD'):
                    return head
                if args == ('status','--porcelain'):
                    return ''
                if args == ('diff','--no-ext-diff','--no-textconv',task['base_sha'],head):
                    return 'frozen-diff-fixture'
                raise AssertionError(args)
            old_public = ('QA task/control run: ' + chr(96) + task['task_id']
                + chr(96) + ' / ' + chr(96) + logs.name
                + chr(96) + '\n- security: **FAIL**; 1 findings;')
            def fake_run(argv, _root, **kwargs):
                if argv and argv[0] == 'claude':
                    model_calls.append((argv,kwargs.get('stdin')))
                    kwargs['stdout_path'].write_text(json.dumps({
                        'subtype':'success', 'is_error':assessor_error,
                        'structured_output':structured}),encoding='utf-8')
                    return ''
                if argv[:2] == ['gh','api'] and '--method' not in argv:
                    return json.dumps([{'body':old_public}] + [
                        {'body':item.removeprefix('body=')} for item in posts])
                if argv[:4] == ['gh','api','--method','POST']:
                    posts.append(argv[-1])
                    return '{"id":987}'
                raise AssertionError(argv)
            with patch.object(loop,'git',side_effect=fake_git), \
                 patch.object(loop,'run',side_effect=fake_run), \
                 patch.object(loop,'read_pr',return_value={'head':{'sha':head},'draft':True}), \
                 patch.object(loop.hashlib,'sha256',side_effect=fake_sha):
                yield root,logs,task,finding,structured,posts,model_calls

    def test_single_source_bound_reviewer_publishes_enums_only_and_reuses(self):
        with self.evidence() as (root,logs,task,finding,structured,posts,calls):
            loop.assess_postfix_security_fail(root,logs,task)
            self.assertEqual(len(calls),1)
            self.assertIn('PRIVATE-BLOCKING-FINDING-TEXT',calls[0][1])
            self.assertIn('Read,Glob,Grep',calls[0][0])
            self.assertEqual(len(posts),1)
            self.assertIn('RESOURCE_EXHAUSTION',posts[0])
            self.assertIn('FIX_BEFORE_MERGE',posts[0])
            for secret in ('PRIVATE-BLOCKING','PRIVATE-SOURCE','PRIVATE-ENGINEERING'):
                self.assertNotIn(secret,posts[0])
            report = root/'.ai-loop'/'qa-postfix-security-fail-issue56'/'assessment.json'
            self.assertEqual(json.loads(report.read_text()),structured)
            loop.assess_postfix_security_fail(root,logs,task)
            self.assertEqual(len(calls),1)
            self.assertEqual(len(posts),1)

    def test_original_fail_and_frozen_sha_are_required_before_model_call(self):
        with self.evidence() as (root,logs,task,finding,structured,posts,calls):
            source=logs/'claude-201.json'
            old=json.loads(source.read_text())
            old['status']='PASS'
            loop.save_json(source,old)
            with self.assertRaises(loop.Stop):
                loop.assess_postfix_security_fail(root,logs,task)
            self.assertEqual(calls,[])
            self.assertEqual(posts,[])
            loop.save_json(source,{'status':'FAIL','reviewed_head':task['expected_head'],
                'summary':'untrusted','findings':[]})
            with self.assertRaises(loop.Stop):
                loop.assess_postfix_security_fail(root,logs,task)
            self.assertEqual(calls,[])

    def test_error_envelope_never_posts_or_creates_authority(self):
        with self.evidence(assessor_error=True) as (root,logs,task,finding,structured,posts,calls):
            with self.assertRaisesRegex(loop.Stop,'did not finish successfully'):
                loop.assess_postfix_security_fail(root,logs,task)
            self.assertEqual(len(calls),1)
            self.assertEqual(posts,[])
            self.assertFalse((root/'.ai-loop'/'qa-postfix-security-fail-issue56'/'assessment.json').exists())
            with self.assertRaisesRegex(loop.Stop,'did not finish successfully'):
                loop.assess_postfix_security_fail(root,logs,task)
            self.assertEqual(len(calls),1, 'existing failed raw output cannot trigger hidden replay')


class FrozenPR55MediumRiskTests(unittest.TestCase):
    """A separate bounded local classification, not a security approval."""

    @contextmanager
    def evidence(self, *, provider_error=False):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            logs = root / '.ai-loop' / '20261008T202910Z-31100'
            logs.mkdir(parents=True)
            head = '67d02be6bc8cb3f237c5060edda3321f6d885bf3'
            filepath = 'tools/llmtool/llmtool/test_output_excerpts.py'
            task = {'pr_number': 55, 'qa_gates': ['security', 'functional_qa'],
                'expected_head': head, 'branch': 'feat/llmtool-c1-test-excerpts-20261009',
                'base_sha': '8e29024656f873431f6ad83e45015b57bfe381a9',
                'allowed_files': [filepath]}
            medium = {'severity': 'Medium', 'blocking': False, 'file': filepath,
                'detail': 'PRIVATE-REVIEW-FINDING-DO-NOT-PUBLISH',
                'evidence': 'PRIVATE-REVIEW-CODE-EVIDENCE'}
            low = {'severity': 'Low', 'blocking': False, 'file': filepath,
                'detail': 'benign local review', 'evidence': 'fixed source line'}
            loop.save_json(logs / 'claude-201.json', {
                'status': 'PASS', 'reviewed_head': head, 'summary': 'private review',
                'findings': [medium, low, low]})
            digest = hashlib.sha256(json.dumps(medium, sort_keys=True,
                ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            response = {'reviewed_head': head, 'original_finding_sha256': digest,
                'category': 'DATA_EXPOSURE', 'action': 'FIX_BEFORE_MERGE',
                'confidence': 'MEDIUM', 'source_path': filepath, 'line': 123,
                'private_reason': 'PRIVATE-CLAUDE-CODE-DETAIL'}
            sent, reviews = [], []
            def fake_git(_root, *args):
                if args == ('branch', '--show-current'):
                    return task['branch']
                if args == ('rev-parse', 'HEAD'):
                    return head
                if args == ('status', '--porcelain'):
                    return ''
                raise AssertionError(args)
            def fake_run(argv, _root, **kwargs):
                if argv[0] == 'claude':
                    reviews.append((argv, kwargs.get('stdin')))
                    kwargs['stdout_path'].write_text(json.dumps(
                        {'subtype': 'success', 'is_error': provider_error,
                         'structured_output': response}), encoding='utf-8')
                    return ''
                if argv[:2] == ['gh', 'api'] and '--method' not in argv:
                    return json.dumps([{'body': x.removeprefix('body=')} for x in sent])
                if argv[:4] == ['gh', 'api', '--method', 'POST']:
                    sent.append(argv[-1])
                    return '{"id":789}'
                raise AssertionError(argv)
            with patch.object(loop, 'git', side_effect=fake_git), \
                 patch.object(loop, 'run', side_effect=fake_run), \
                 patch.object(loop, 'publish_frozen_qa_severity_inventory', return_value=None):
                yield root, logs, task, medium, response, sent, reviews

    def test_medium_reviewer_sanitizes_public_output_and_reuses_existing_result(self):
        with self.evidence() as (root, logs, task, medium, result, sent, reviews):
            loop.assess_frozen_pr55_medium(root, logs, task)
            self.assertEqual(len(reviews), 1)
            self.assertIn('PRIVATE-REVIEW-FINDING', reviews[0][1])
            self.assertIn('Read,Glob,Grep', reviews[0][0])
            self.assertEqual(len(sent), 1)
            self.assertIn('Category: DATA_EXPOSURE', sent[0])
            self.assertIn('Action: FIX_BEFORE_MERGE', sent[0])
            for private in ('PRIVATE-REVIEW', 'PRIVATE-CLAUDE'):
                self.assertNotIn(private, sent[0])
            path = root / '.ai-loop' / 'qa-medium-issue56' / 'assessment.json'
            self.assertEqual(json.loads(path.read_text()), result)
            loop.assess_frozen_pr55_medium(root, logs, task)
            self.assertEqual(len(reviews), 1)
            self.assertEqual(len(sent), 1, 'retry must not duplicate Git comment')

    def test_medium_review_blocks_on_original_finding_tamper(self):
        with self.evidence() as (root, logs, task, medium, result, sent, reviews):
            path = logs / 'claude-201.json'
            record = json.loads(path.read_text())
            record['findings'][0]['blocking'] = True
            loop.save_json(path, record)
            with self.assertRaisesRegex(loop.Stop, 'review claimed PASS with blocking findings'):
                loop.assess_frozen_pr55_medium(root, logs, task)
            self.assertEqual(reviews, [])
            self.assertEqual(sent, [])

    def test_medium_model_error_envelope_fails_closed_without_public_output(self):
        with self.evidence(provider_error=True) as (root, logs, task, medium, result, sent, reviews):
            with self.assertRaisesRegex(loop.Stop, 'did not complete successfully'):
                loop.assess_frozen_pr55_medium(root, logs, task)
            self.assertEqual(len(reviews), 1)
            self.assertEqual(sent, [])
            self.assertFalse((root / '.ai-loop' / 'qa-medium-issue56' / 'assessment.json').exists())

    def test_medium_schema_rejects_wrong_revision_and_untrusted_category(self):
        with self.evidence() as (root, logs, task, medium, result, sent, reviews):
            original = task['expected_head']
            task['expected_head'] = 'f' * 40
            # The real frozen inventory check would reject this before paid models.
            task['expected_head'] = original
            loop.assess_frozen_pr55_medium(root, logs, task)
            path = root / '.ai-loop' / 'qa-medium-issue56' / 'assessment.json'
            row = json.loads(path.read_text())
            row['category'] = 'PRIVATE-CLAUDE-CATEGORY'
            loop.save_json(path, row)
            with self.assertRaisesRegex(loop.Stop, 'schema violation'):
                loop.assess_frozen_pr55_medium(root, logs, task)
            self.assertEqual(len(sent), 1)
            self.assertEqual(len(reviews), 1)


class FrozenPR55SeverityInventoryTests(unittest.TestCase):
    """Only safe numeric metadata may cross the Mac-to-Git boundary."""

    @contextmanager
    def evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run_id = '20261008T202910Z-31100'
            logs = root / '.ai-loop' / run_id
            logs.mkdir(parents=True)
            head = '67d02be6bc8cb3f237c5060edda3321f6d885bf3'
            diff_hash = 'f977cb5dcd8ab47dc787cb0d1f439e843d0bff99dd1f69e018157a28348c7ea8'
            name = 'tools/llmtool/llmtool/test_output_excerpts.py'
            task = {'pr_number': 55, 'qa_gates': ['security', 'functional_qa'],
                'expected_head': head, 'branch': 'feat/llmtool-c1-test-excerpts-20261009',
                'base_sha': '8e29024656f873431f6ad83e45015b57bfe381a9',
                'allowed_files': [name]}
            reports = []
            for number, role, count in ((201, 'security', 3), (202, 'functional_qa', 5)):
                record = {'status': 'PASS', 'reviewed_head': head,
                    'summary': 'SECRET-PRIVATE-ROLE-SUMMARY',
                    'findings': [{'severity': 'Low', 'blocking': False, 'file': name,
                        'detail': 'SENSITIVE-PRIVATE-FINDING-DETAIL',
                        'evidence': 'SENSITIVE-PRIVATE-SOURCE-EVIDENCE'} for _ in range(count)]}
                loop.save_json(logs / ('claude-%d.json' % number), record)
                reports.append(dict(role=role, status='PASS', reviewed_head=head,
                    summary=record['summary'], findings=record['findings']))
            loop.save_json(logs / 'qa-pr55.json', {
                'run_id': run_id, 'head': head, 'pr': 55, 'diff_sha256': diff_hash,
                'qa_result': 'PASS', 'tests_related': 'PASS', 'tests_full_local': 'PASS',
                'merge_authorized': False, 'product_provider_egress_authorized': False,
                'roles': reports})
            loop.save_json(logs / 'provenance.json', {
                'run_id': run_id, 'root': str(root)})
            loop.save_json(logs / 'task.json', task)
            def fake_git(_root, *args):
                if args == ('branch', '--show-current'):
                    return task['branch']
                if args == ('rev-parse', 'HEAD'):
                    return head
                if args == ('status', '--porcelain'):
                    return ''
                if args[:2] == ('diff', '--no-ext-diff'):
                    return 'frozen diff'
                raise AssertionError(args)
            digest_fake = Mock()
            digest_fake.hexdigest.return_value = diff_hash
            published = []
            previous = []
            def fake_run(args, _root, **kwargs):
                if args[:2] == ['gh', 'api'] and '--method' not in args:
                    return json.dumps(previous)
                self.assertIn('POST', args)
                published.append(args[-1])
                return '{"id":1}'
            with patch.object(loop, 'git', side_effect=fake_git), \
                 patch.object(loop, 'read_pr', return_value={'head': {'sha': head}}), \
                 patch.object(loop, 'hashlib', Mock(sha256=Mock(return_value=digest_fake))), \
                 patch.object(loop, 'run', side_effect=fake_run):
                yield root, logs, task, reports, published, previous

    def test_severity_only_no_private_text_no_model_no_source_change(self):
        with self.evidence() as (root, logs, task, reports, published, previous):
            with patch.object(loop, 'claude') as no_models:
                loop.publish_frozen_qa_severity_inventory(root, logs, task)
                no_models.assert_not_called()
            self.assertEqual(len(published), 1)
            body = published[0]
            self.assertIn('Security PASS (3): Low=3, Medium=0', body)
            self.assertIn('Functional QA PASS (5): Low=5, Medium=0', body)
            for leak in ['SECRET-PRIVATE', 'SENSITIVE-PRIVATE']:
                self.assertNotIn(leak, body)
            self.assertNotIn('body=PRIVATE-', body)
            previous.append({'body': body.removeprefix('body=')})
            loop.publish_frozen_qa_severity_inventory(root, logs, task)
            self.assertEqual(len(published), 1, 'duplicate safe issue comment')

    def test_severity_inventory_rejects_private_drift_and_blocking_findings(self):
        with self.evidence() as (root, logs, task, reports, published, previous):
            review = logs / 'claude-201.json'
            modified = json.loads(review.read_text())
            modified['findings'][0]['severity'] = 'High'
            modified['findings'][0]['blocking'] = True
            loop.save_json(review, modified)
            with self.assertRaises(loop.Stop):
                loop.publish_frozen_qa_severity_inventory(root, logs, task)
            self.assertEqual(published, [])
            modified['findings'][0]['severity'] = 'Low'
            modified['findings'][0]['blocking'] = False
            loop.save_json(review, modified)
            task['expected_head'] = 'f' * 40
            with self.assertRaisesRegex(loop.Stop, 'clean pinned'):
                loop.publish_frozen_qa_severity_inventory(root, logs, task)
            self.assertEqual(published, [])


class InitialDecisionHandoffTests(unittest.TestCase):
    """A zero-edit Codex decision is a frozen control-branch handoff, not model approval."""

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.run(['git', *args], cwd=str(root), check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()
            git('init', '-q')
            (root / 'bootstrap.txt').write_text('pinned main content')
            git('add', 'bootstrap.txt')
            git('-c', 'user.name=Fixture', '-c',
                'user.email=fixture@example.invalid', 'commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            branch = 'feat/llmtool-c1-test-excerpts-20261009'
            git('switch', '-q', '-c', branch)
            run_id = '20261008T185112Z-21061'
            old_control = '9d6bbc3c578a3dca0c4d29026b1c64c89cb200bb'
            logs = root / '.ai-loop' / run_id
            logs.mkdir(parents=True)
            old_task = {'task_id': 'C1-test-excerpts-20261009',
                'mode': 'implement_new_pr', 'branch': branch,
                'base_sha': base, 'allowed_files': ['feature.py']}
            summary_reason = 'command failed (exit 1): claude; see local logs'
            reason = ('Checkout matches canonical path and pinned HEAD. '
                'Required automation/ai-loop/task.json and scripts/ai-loop.sh '
                'are absent. No files changed, tests run, or Git mutations performed.')
            codex = {'status': 'NEEDS_DECISION', 'summary': reason}
            loop.save_json(logs / 'codex-0.json', codex)
            source_sha = loop.digest(logs / 'codex-0.json')
            loop.save_json(logs / 'task.json', old_task)
            (logs / 'task.md').write_text('Frozen C1 task\n')
            loop.save_json(logs / 'provenance.json', {
                'root': str(root), 'run_id': run_id, 'control_sha': old_control,
                'start_branch': 'main', 'start_head': base, 'base': base})
            loop.save_json(logs / 'summary.json', {
                'branch': branch, 'head': base, 'status': 'STOPPED',
                'reason': summary_reason, 'history': []})
            loop.save_json(logs / 'failure-0.json', {
                'run_id': run_id, 'task_id': old_task['task_id'], 'head': base,
                'phase': 'developer', 'gate_waived': False})
            loop.save_json(logs / 'failure-0-claude.raw.json', {
                'subtype': 'success', 'is_error': True})
            task = {**old_task, 'initial_decision_recovery': {
                'run_id': run_id, 'control_sha': old_control,
                'codex_result_sha256': source_sha}}
            previous_run = loop.run
            def mocked_run(args, cwd, **kwargs):
                if args[:2] == ['gh', 'api']:
                    return '[]'
                if args[:3] == ['git', 'ls-remote', '--heads']:
                    return ''
                return previous_run(args, cwd, **kwargs)
            with patch.object(loop, 'run', side_effect=mocked_run), \
                 patch.object(loop, 'load_task', return_value=(old_task, 'Frozen C1 task')):
                yield root, task, run_id, logs

    def test_zero_edit_decision_recovery_reuses_exact_pinned_task_without_pass(self):
        with self.fixture() as (root, task, run_id, logs):
            original, recovery = loop.prepare_initial_decision_resume(
                root, task, run_id, {}, ())
            self.assertEqual(original, {})
            self.assertEqual(recovery['kind'], 'initial_decision')
            self.assertEqual(recovery['partial_files'], {})
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            self.assertIn('codex-0.json', recovery['private_evidence_files'])

    def test_initial_decision_recovery_rejects_bad_sha_and_tampered_evidence(self):
        with self.fixture() as (root, task, run_id, logs):
            task['initial_decision_recovery']['codex_result_sha256'] = 'e' * 64
            with self.assertRaisesRegex(loop.Stop, 'decision SHA differs'):
                loop.prepare_initial_decision_resume(root, task, run_id, {}, ())
            task['initial_decision_recovery']['codex_result_sha256'] = loop.digest(logs / 'codex-0.json')
            evidence = logs / 'summary.json'
            doc = json.loads(evidence.read_text())
            doc['history'] = [{'tests': 'PASS'}]
            loop.save_json(evidence, doc)
            with self.assertRaisesRegex(loop.Stop, 'provenance differs'):
                loop.prepare_initial_decision_resume(root, task, run_id, {}, ())

    def test_initial_decision_recovery_never_accepts_new_dirty_or_wrong_branch(self):
        with self.fixture() as (root, task, run_id, logs):
            with self.assertRaisesRegex(loop.Stop, 'clean tracked/index'):
                loop.prepare_initial_decision_resume(root, task, run_id, {}, ('bootstrap.txt',))
            with self.assertRaisesRegex(loop.Stop, 'candidate changed'):
                loop.prepare_initial_decision_resume(root, task, run_id, {'feature.py': 'a'*64}, ())
            with patch.object(loop, 'git', side_effect=lambda rt, *args: (
                    'unrelated' if args == ('branch', '--show-current')
                    else subprocess.run(['git', *args], cwd=str(rt), check=True,
                        stdout=subprocess.PIPE).stdout.decode().strip())):
                with self.assertRaisesRegex(loop.Stop, 'candidate changed'):
                    loop.prepare_initial_decision_resume(root, task, run_id, {}, ())

    def test_initial_decision_recovery_never_reuses_ambiguous_contract(self):
        with self.fixture() as (root, task, run_id, logs):
            task['task_id'] = 'other-task'
            with self.assertRaisesRegex(loop.Stop, 'identity mismatch'):
                loop.prepare_initial_decision_resume(root, task, run_id, {}, ())
            task['task_id'] = 'C1-test-excerpts-20261009'
            task['allowed_files'].append('unapproved.py')
            with self.assertRaisesRegex(loop.Stop, 'original frozen task'):
                loop.prepare_initial_decision_resume(root, task, run_id, {}, ())

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


class ActiveRunnerProcessTests(unittest.TestCase):
    root = Path('/fixture/repository')
    schema = '/fixture/repository/.ai-loop/20261007T113658Z-74880/codex-schema.json'
    output = '/fixture/repository/.ai-loop/20261007T113658Z-74880/codex-0.json'

    def inspect(self, rows, rc=0):
        pids = '\n'.join(row.split(None, 1)[0] for row in rows.splitlines())
        with patch.object(loop, 'command', side_effect=[(0, pids, ''), (rc, rows, '')]) as command:
            loop.ensure_no_active_runner_codex(self.root)
            self.assertEqual(command.call_args.args[0][:2], ['/bin/ps', '-ww'])

    def test_daemons_vscode_host_and_interactive_node_cli_do_not_block(self):
        self.inspect('28710 /Users/user/.vscode/extensions/openai.chatgpt/bin/codex app-server\n'
            '49642 /Users/user/.codex/packages/app-server-daemon/bin/codex app-server\n'
            '51565 /Users/user/.codex/packages/app-server-daemon/bin/codex-code-mode-host\n'
            '60808 /Users/user/.codex/packages/app-server-daemon/bin/codex app-server\n'
            '76261 node /Users/user/.nvm/bin/codex\n'
            '76262 /Users/user/.nvm/lib/node_modules/@openai/codex/vendor/codex/codex\n')

    def test_native_or_node_runner_exec_is_blocked_with_pid(self):
        for executable in ('/vendor/codex/codex', 'node /Users/user/.nvm/bin/codex'):
            with self.subTest(executable=executable), self.assertRaisesRegex(loop.Stop, 'PID 76262'):
                self.inspect('76262 ' + executable + ' -a never exec --output-schema '
                             + self.schema + ' --output-last-message ' + self.output + ' -\n')

    def test_equals_option_form_is_also_blocked(self):
        with self.assertRaisesRegex(loop.Stop, 'PID 42'):
            self.inspect('42 codex exec --output-schema=' + self.schema
                         + ' --output-last-message=' + self.output + '\n')

    def test_exec_for_other_repository_is_not_owned_by_this_runner(self):
        self.inspect('42 codex exec --output-schema /other/.ai-loop/run/codex-schema.json '
                     '--output-last-message /other/.ai-loop/run/codex-0.json\n')

    def test_ambiguous_or_inconsistent_repo_binding_stops(self):
        for suffix in ('', ' --output-last-message /outside/codex-0.json',
                       ' --output-last-message ' + self.output + ' --output-schema ' + self.schema):
            with self.subTest(suffix=suffix), self.assertRaises(loop.Stop):
                self.inspect('42 codex exec --output-schema ' + self.schema + suffix + '\n')

    def test_no_processes_or_all_exited_between_inspections_is_accepted(self):
        with patch.object(loop, 'command', return_value=(1, '', '')) as command:
            loop.ensure_no_active_runner_codex(self.root)
            self.assertEqual(command.call_count, 1)
        with patch.object(loop, 'command', side_effect=[(0, '42\n', ''), (1, '', '')]):
            loop.ensure_no_active_runner_codex(self.root)

    def test_inspection_failure_invalid_pids_or_malformed_rows_stops(self):
        for outputs in ([(2, '', '')], [(0, 'not-a-pid', '')],
                        [(0, '42', ''), (2, '', 'ps failed')],
                        [(0, '42', ''), (0, '99 codex exec', '')],
                        [(0, '42', ''), (0, '42 codex "unclosed', '')]):
            with self.subTest(outputs=outputs), patch.object(loop, 'command', side_effect=outputs), \
                 self.assertRaises(loop.Stop):
                loop.ensure_no_active_runner_codex(self.root)

    def test_multiple_active_entries_are_inspected_not_only_first_daemon(self):
        with self.assertRaisesRegex(loop.Stop, 'PID 2'):
            self.inspect('1 codex app-server\n2 codex exec --output-schema ' + self.schema
                         + ' --output-last-message ' + self.output + '\n')


class NativeResumeTests(unittest.TestCase):
    run_id = '20261007T123142Z-77009'

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as runtime, ExitStack() as stack:
            root = Path(runtime)
            old = root / '.ai-loop' / self.run_id
            old.mkdir(parents=True)
            (root / 'reports').mkdir()
            (root / 'source.py').write_text('committed candidate\n')
            (root / 'reports/validation.md').write_text('historical pending gates\n')
            (root / 'user.txt').write_text('unrelated preserved\n')
            baseline = {'mode': 'implement_new_pr', 'task_id': 'fixture', 'branch': 'feat/fixture',
                'base_sha': HEAD, 'expected_head': HEAD, 'max_repairs': 2,
                'allowed_files': ['source.py', 'reports/validation.md'], 'full_tests': [['full']],
                'initial_timeout_recovery': {'run_id': '20261007T113658Z-74880', 'control_sha': 'c'*40}}
            tested = {p: loop.digest(root/p) for p in baseline['allowed_files']}
            mac = {'platform': 'darwin', 'tested_sha256': loop.runtime_bytes(tested),
                   'failure': {'status': 'FAIL', 'scenario': 'sigint', 'owned_processes': [
                       {'pid': 321, 'host_identity': {'pid': 321}, 'supervisor': {'pid': 322}}]}}
            loop.save_json(old/'task.json', baseline)
            (old/'task.md').write_text('original requirements\n')
            loop.save_json(old/'provenance.json', {'root': str(root), 'run_id': self.run_id,
                'base': HEAD, 'control_sha': 'b'*40})
            loop.save_json(old/'summary.json', {'status': 'STOPPED', 'branch': baseline['branch'],
                'head': 'e'*40, 'reason': 'required Mac acceptance failed; probes/design need coordinator assessment, no automatic retry',
                'history': [{'head': 'e'*40, 'mandatory': {'status': 'FAIL'}, 'tested_sha256': tested}]})
            loop.save_json(old/'mac-containment-0.json', mac)
            task = copy.deepcopy(baseline)
            task.pop('initial_timeout_recovery')
            task['acceptance_failure_recovery'] = {'run_id': self.run_id, 'control_sha': 'b'*40,
                'expected_head': 'e'*40, 'summary_sha256': loop.digest(old/'summary.json'),
                'evidence_sha256': loop.digest(old/'mac-containment-0.json')}
            inventory = {'user.txt': loop.digest(root/'user.txt')}
            def fake_git(cwd, *args):
                if args == ('branch', '--show-current'): return task['branch']
                if args == ('rev-parse', 'HEAD'): return 'e'*40
                if args == ('diff', '--cached', '--name-only'): return ''
                if args[:3] == ('diff', '--name-only', '-z'): return 'source.py\0reports/validation.md\0'
                raise AssertionError(args)
            def fake_command(argv, *args, **kw):
                return (0, '', '') if argv[0] == 'git' else (1, '', '')
            stack.enter_context(patch.object(loop, 'load_task', return_value=(baseline, 'original requirements')))
            git_mock = stack.enter_context(patch.object(loop, 'git', side_effect=fake_git))
            run_mock = stack.enter_context(patch.object(loop, 'run', side_effect=['[]', '']))
            command = stack.enter_context(patch.object(loop, 'command', side_effect=fake_command))
            yield root, old, task, inventory, git_mock, run_mock, command

    def test_exact_committed_failure_continues_without_inheriting_pass(self):
        with self.fixture() as (root, old, task, inventory, *rest):
            original, recovery = loop.prepare_acceptance_resume(root, task, self.run_id, inventory)
            self.assertEqual(original, inventory)
            self.assertEqual(recovery['kind'], 'acceptance_failure')
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            logs = root/'.ai-loop/new-run'
            logs.mkdir()
            loop.snapshot_recovery(root, logs, recovery)
            self.assertEqual((logs/'recovery-inputs/source.py').read_bytes(), (root/'source.py').read_bytes())
            self.assertEqual((logs/'recovery-evidence/mac-containment-0.json').read_bytes(),
                             (old/'mac-containment-0.json').read_bytes())
            self.assertEqual((logs/'recovery-evidence/summary.json').read_bytes(), (old/'summary.json').read_bytes())

    def test_explicit_hung_failure_recovers_without_inheriting_signal_passes(self):
        with self.fixture() as (root, old, task, inventory, *rest):
            mac = json.loads((old/'mac-containment-0.json').read_text())
            mac['failure']['scenario'] = 'hung_supervisor'
            mac['scenarios'] = {name: {'status': 'PASS'} for name in
                                ('fd_inheritance', 'sigint', 'sigterm', 'sighup')}
            loop.save_json(old/'mac-containment-0.json', mac)
            policy = task['acceptance_failure_recovery']
            policy.update(failure_scenario='hung_supervisor', evidence_sha256=loop.digest(old/'mac-containment-0.json'))
            original, recovery = loop.prepare_acceptance_resume(root, task, self.run_id, inventory)
            self.assertEqual(original, inventory)
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            self.assertEqual(recovery['evidence_files']['mac-containment-0.json'], policy['evidence_sha256'])

    def setup_kqueue_recovery(self, root, old, task):
        test = 'tools/llmtool/tests/test_git_containment.py'
        path = root/test
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ['# synthetic kqueue source line %d' % i for i in range(1, 2351)]
        lines[2317] = '        for zombie_first in (False, True):'
        lines[2333] = '                note = kernel.ExitNote(process.pid)'
        path.write_text('\n'.join(lines) + '\n')
        baseline = loop.load_task.return_value[0]
        baseline['allowed_files'].append(test)
        task['allowed_files'].append(test)
        loop.save_json(old/'task.json', baseline)
        summary = json.loads((old/'summary.json').read_text())
        tested = summary['history'][0]['tested_sha256']
        tested[test] = loop.digest(path)
        summary['reason'] = 'command failed (exit 1): claude; see local logs'
        summary['history'][0]['review'] = {'status': 'PASS'}
        summary['history'][0]['final_full'] = {'status': 'PASS'}
        loop.save_json(old/'summary.json', summary)
        inventory = [{'pid': 90, 'host_identity': {'pid': 90},
                      'supervisor': {}, 'members': []}]
        good = ['fd_inheritance', 'sigint', 'sigterm', 'sighup',
                'hung_supervisor', 'leader_exit_descendant', 'identity_reuse',
                'macos_start_time']
        mac = {'platform': 'darwin', 'schema_version': 1,
               'tested_sha256': loop.runtime_bytes(tested),
               'scenarios': {x: {'status': 'PASS', 'skipped': 0} for x in good},
               'failure': {'status': 'FAIL', 'scenario': 'kqueue_non_reaping',
                   'subcase': 'kqueue_non_reaping', 'exception_type': 'ProcessLookupError',
                   'errno': 3,
                   'traceback': [
                       {'file': 'test_git_containment.py', 'function': 'kqueue_non_reaping',
                        'line': 2334},
                       {'file': '_git_process_identity.py', 'function': '__init__',
                        'line': 148}],
                   'owned_processes': [], 'fixture_cleanup': {'status': 'RESOLVED',
                       'original_owned_processes': inventory,
                       'evidence': {k: True for k in
                           ('decision_before_reap', 'host_pin_verified', 'host_reaped',
                            'host_absent', 'group_absent')}}}}
        loop.save_json(old/'mac-containment-0.json', mac)
        failure = {'run_id': self.run_id, 'task_id': task['task_id'], 'round': 0,
                   'phase': 'mandatory-final', 'head': 'e'*40, 'status': 'FAIL',
                   'gate_waived': False, 'source_sha256': tested,
                   'evidence': {'mac': mac}}
        loop.save_json(old/'failure-0.json', failure)
        loop.save_json(old/'failure-0-claude.raw.json',
                       {'subtype': 'success', 'is_error': True, 'result': 'LOCAL_PRIVATE'})
        task['acceptance_failure_recovery'].update(
            failure_scenario='kqueue_non_reaping',
            summary_sha256=loop.digest(old/'summary.json'),
            evidence_sha256=loop.digest(old/'mac-containment-0.json'),
            failure_evidence_sha256=loop.digest(old/'failure-0.json'),
            repair_files=[test])
        return inventory

    def test_kqueue_failure_recovery_pins_native_error_and_private_provider(self):
        with self.fixture() as (root, old, task, current, *rest):
            owned = self.setup_kqueue_recovery(root, old, task)
            with patch.object(loop, 'ensure_probe_inventory_absent') as absent:
                original, recovery = loop.prepare_acceptance_resume(
                    root, task, self.run_id, current)
            absent.assert_called_once_with(root, owned, allow_host_only=True)
            self.assertEqual(recovery['failure_scenario'], 'kqueue_non_reaping')
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            self.assertEqual(set(recovery['private_evidence_files']),
                             {'failure-0-claude.raw.json'})
            logs = root/'.ai-loop/new-run'; logs.mkdir()
            loop.snapshot_recovery(root, logs, recovery)
            self.assertFalse((logs/'recovery-evidence/failure-0-claude.raw.json').exists())
            self.assertEqual(loop.digest(old/'failure-0-claude.raw.json'),
                             recovery['private_evidence_files']['failure-0-claude.raw.json'])
            (old/'failure-0-claude.raw.json').write_text('changed')
            with self.assertRaises(loop.Stop):
                loop.snapshot_recovery(root, root/'.ai-loop/new-run-2', recovery)

    def test_kqueue_recovery_rejects_wrong_native_or_provider_evidence(self):
        for fault in ('errno', 'frames', 'cleanup', 'provider', 'source'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, current, *rest):
                self.setup_kqueue_recovery(root, old, task)
                if fault == 'provider':
                    loop.save_json(old/'failure-0-claude.raw.json',
                                   {'subtype': 'success', 'is_error': False})
                elif fault == 'source':
                    (root/'tools/llmtool/tests/test_git_containment.py').write_text('drift')
                else:
                    mac = json.loads((old/'mac-containment-0.json').read_text())
                    if fault == 'errno': mac['failure']['errno'] = 2
                    elif fault == 'frames': mac['failure']['traceback'][0]['line'] = 2335
                    elif fault == 'cleanup': mac['failure']['fixture_cleanup']['status'] = 'UNRESOLVED'
                    loop.save_json(old/'mac-containment-0.json', mac)
                    failure = json.loads((old/'failure-0.json').read_text())
                    failure['evidence']['mac'] = mac
                    loop.save_json(old/'failure-0.json', failure)
                    policy = task['acceptance_failure_recovery']
                    policy['evidence_sha256'] = loop.digest(old/'mac-containment-0.json')
                    policy['failure_evidence_sha256'] = loop.digest(old/'failure-0.json')
                with patch.object(loop, 'ensure_probe_inventory_absent'):
                    with self.assertRaises(loop.Stop):
                        loop.prepare_acceptance_resume(root, task, self.run_id, current)

    def test_kqueue_host_only_inventory_requires_explicit_permission_and_absence(self):
        inventory = [{'pid': 72468, 'host_identity': {'pid': 72468},
                      'supervisor': {}}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(loop.Stop):
                loop.ensure_probe_inventory_absent(root, inventory)
            with patch.object(loop, 'command', return_value=(1, '', '')) as check:
                loop.ensure_probe_inventory_absent(root, inventory, allow_host_only=True)
            self.assertEqual(check.call_args.args[0],
                ['/bin/ps', '-ww', '-p', '72468', '-o', 'pid='])
            with patch.object(loop, 'command', return_value=(0, '72468\n', '')):
                with self.assertRaises(loop.Stop):
                    loop.ensure_probe_inventory_absent(root, inventory, allow_host_only=True)
            bad = [{'pid': 72468, 'host_identity': {'pid': 72468},
                    'supervisor': {}, 'members': [{'pid': 72500}]}]
            with self.assertRaises(loop.Stop):
                loop.ensure_probe_inventory_absent(root, bad, allow_host_only=True)

    def test_kqueue_recovery_repair_scope_is_test_only(self):
        task = {'acceptance_report': 'reports/validation.md'}
        rec = {'kind': 'acceptance_failure', 'failure_scenario': 'kqueue_non_reaping',
               'repair_files': ['tools/llmtool/tests/test_git_containment.py']}
        path = rec['repair_files'][0]
        loop.ensure_review_repair_scope(task, rec, 0, [path])
        loop.ensure_review_repair_scope(task, rec, 1, [task['acceptance_report']])
        for paths in ([], [task['acceptance_report']],
                      [path, 'tools/llmtool/llmtool/_git_process_identity.py']):
            with self.subTest(paths=paths), self.assertRaises(loop.Stop):
                loop.ensure_review_repair_scope(task, rec, 0, paths)
        with self.assertRaises(loop.Stop):
            loop.ensure_review_repair_scope(task, rec, 1,
                ['tools/llmtool/llmtool/_git_process_identity.py'])

    def test_preflight_harness_failure_can_recover_without_native_pass_or_pid_cleanup(self):
        with self.fixture() as (root, old, task, inventory, *rest):
            tested = json.loads((old/'summary.json').read_text())['history'][0]['tested_sha256']
            mac = {'platform': 'darwin', 'schema_version': 1, 'tested_sha256': {}, 'scenarios': {},
                   'failure': {'status': 'FAIL', 'scenario': 'preflight',
                               'exception_type': 'ValueError', 'owned_processes': []}}
            loop.save_json(old/'mac-containment-0.json', mac)
            summary = json.loads((old/'summary.json').read_text())
            summary['reason'] = 'failure coordinator: MAC_EVIDENCE_INCOMPLETE; bounded test harness repair'
            loop.save_json(old/'summary.json', summary)
            failure = {'run_id': self.run_id, 'task_id': task['task_id'], 'round': 0,
                       'phase': 'mandatory-final', 'head': 'e'*40, 'source_sha256': tested,
                       'status': 'FAIL', 'gate_waived': False, 'evidence': {'mac': mac}}
            loop.save_json(old/'failure-0.json', failure)
            policy = task['acceptance_failure_recovery']
            policy.update(failure_scenario='preflight',
                summary_sha256=loop.digest(old/'summary.json'),
                evidence_sha256=loop.digest(old/'mac-containment-0.json'),
                failure_evidence_sha256=loop.digest(old/'failure-0.json'),
                repair_files=['source.py'])
            with patch.object(loop, 'ensure_failed_probe_pids_absent') as pid_check:
                original, recovery = loop.prepare_acceptance_resume(root, task, self.run_id, inventory)
            self.assertEqual(original, inventory)
            self.assertEqual(recovery['failure_scenario'], 'preflight')
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            self.assertEqual(recovery['evidence_files']['failure-0.json'], policy['failure_evidence_sha256'])
            pid_check.assert_not_called()

    def test_preflight_recovery_requires_exact_empty_native_state_and_failure_hash(self):
        for fault in ('scenarios', 'tested', 'owned', 'failure'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, inventory, *rest):
                tested = json.loads((old/'summary.json').read_text())['history'][0]['tested_sha256']
                mac = {'platform': 'darwin', 'schema_version': 1, 'tested_sha256': {}, 'scenarios': {},
                       'failure': {'status': 'FAIL', 'scenario': 'preflight',
                                   'exception_type': 'ValueError', 'owned_processes': []}}
                if fault == 'scenarios': mac['scenarios'] = {'sigint': {'status': 'PASS'}}
                if fault == 'tested': mac['tested_sha256'] = {'source.py': 'x'}
                if fault == 'owned': mac['failure']['owned_processes'] = [{'pid': 123}]
                loop.save_json(old/'mac-containment-0.json', mac)
                summary = json.loads((old/'summary.json').read_text())
                summary['reason'] = 'failure coordinator: MAC_EVIDENCE_INCOMPLETE; bounded test harness repair'
                loop.save_json(old/'summary.json', summary)
                failure = {'run_id': self.run_id, 'task_id': task['task_id'], 'round': 0,
                           'phase': 'mandatory-final', 'head': 'e'*40, 'source_sha256': tested,
                           'status': 'FAIL', 'gate_waived': False, 'evidence': {'mac': mac}}
                loop.save_json(old/'failure-0.json', failure)
                policy = task['acceptance_failure_recovery']
                policy.update(failure_scenario='preflight',
                    summary_sha256=loop.digest(old/'summary.json'),
                    evidence_sha256=loop.digest(old/'mac-containment-0.json'),
                    failure_evidence_sha256=loop.digest(old/'failure-0.json'),
                    repair_files=['source.py'])
                if fault == 'failure':
                    (old/'failure-0.json').write_text('{}')
                with self.assertRaises(loop.Stop):
                    loop.prepare_acceptance_resume(root, task, self.run_id, inventory)

    def test_explicit_scenario_mismatch_stops_even_with_repinned_hash(self):
        for expected, observed in (('hung_supervisor', 'sigint'), ('sigint', 'hung_supervisor'),
                                   ('hung_supervisor', 'parent_sigkill_plain')):
            with self.subTest(expected=expected, observed=observed), self.fixture() as (root, old, task, inventory, *rest):
                mac = json.loads((old/'mac-containment-0.json').read_text())
                mac['failure']['scenario'] = observed
                loop.save_json(old/'mac-containment-0.json', mac)
                task['acceptance_failure_recovery'].update(failure_scenario=expected,
                    evidence_sha256=loop.digest(old/'mac-containment-0.json'))
                with self.assertRaises(loop.Stop):
                    loop.prepare_acceptance_resume(root, task, self.run_id, inventory)

    def test_a_previous_native_policy_can_be_followed_without_gate_changes(self):
        with self.fixture() as (root, old, task, inventory, *rest):
            prior = json.loads((old/'task.json').read_text())
            prior.pop('initial_timeout_recovery')
            prior['acceptance_failure_recovery'] = {'run_id': '20261007T113658Z-74880',
                'control_sha': 'c'*40, 'expected_head': 'd'*40,
                'summary_sha256': '1'*64, 'evidence_sha256': '2'*64}
            loop.save_json(old/'task.json', prior)
            with patch.object(loop, 'load_task', return_value=(prior, 'original requirements')):
                _, recovery = loop.prepare_acceptance_resume(root, task, self.run_id, inventory)
            self.assertEqual(recovery['inherited_validation'], 'NONE')

    def test_scenario_schema_is_bounded_and_legacy_sigint_still_loads(self):
        task = historical_task()
        task.pop('sandbox_decision_recovery', None)
        task.pop('review_failure_recovery', None)
        task.pop('stopped_failure_recovery', None)
        task['acceptance_failure_recovery'] = {'run_id': self.run_id, 'control_sha': '1'*40,
            'expected_head': '2'*40, 'summary_sha256': '3'*64, 'evidence_sha256': '4'*64,
            'failure_scenario': 'hung_supervisor'}
        def load(value):
            with patch.object(loop, 'git', side_effect=[json.dumps(value), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        legacy = copy.deepcopy(task)
        legacy['acceptance_failure_recovery'].pop('failure_scenario', None)
        self.assertIn('acceptance_failure_recovery', load(legacy))
        for value in (None, True, 1, [], {}, 'unknown', 'SIGINT'):
            with self.subTest(value=value):
                bad = copy.deepcopy(task)
                bad['acceptance_failure_recovery']['failure_scenario'] = value
                with self.assertRaises(loop.Stop): load(bad)
        bad = copy.deepcopy(task)
        bad['acceptance_failure_recovery']['extra'] = 'unreviewed'
        with self.assertRaises(loop.Stop): load(bad)

    def test_preflight_recovery_schema_is_bounded_to_allowlisted_test_files(self):
        task = historical_task()
        task.pop('sandbox_decision_recovery', None)
        task.pop('review_failure_recovery', None)
        task.pop('stopped_failure_recovery', None)
        task['acceptance_failure_recovery'] = {
            'run_id': self.run_id, 'control_sha': '1'*40, 'expected_head': '2'*40,
            'summary_sha256': '3'*64, 'evidence_sha256': '4'*64,
            'failure_scenario': 'preflight', 'failure_evidence_sha256': '5'*64,
            'repair_files': ['tools/llmtool/tests/run_containment_acceptance.py']}
        def load(value):
            with patch.object(loop, 'git', side_effect=[json.dumps(value), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        self.assertEqual(load(task)['acceptance_failure_recovery']['failure_scenario'], 'preflight')
        for fault in ('missing_failure_hash', 'empty_files', 'production_file', 'extra'):
            with self.subTest(fault=fault):
                bad = copy.deepcopy(task)
                recovery = bad['acceptance_failure_recovery']
                if fault == 'missing_failure_hash': recovery.pop('failure_evidence_sha256')
                elif fault == 'empty_files': recovery['repair_files'] = []
                elif fault == 'production_file': recovery['repair_files'] = [bad['allowed_files'][0]]
                else: recovery['extra'] = True
                with self.assertRaises(loop.Stop): load(bad)

    def test_new_auto_policy_budget_can_follow_old_failure_without_scope_or_gate_changes(self):
        with self.fixture() as (root, old, task, inventory, *rest):
            task['failure_triage'] = {'max_reviews': 4, 'repeat_limit': 2, 'allow_mac_repair': True}
            task['max_repairs'] = 4
            _, recovery = loop.prepare_acceptance_resume(root, task, self.run_id, inventory)
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            task['full_tests'] = [['weakened']]
            rest[1].side_effect = ['[]', '']
            with self.assertRaises(loop.Stop): loop.prepare_acceptance_resume(root, task, self.run_id, inventory)

    def test_wrong_run_or_head_or_dirty_tree_stops(self):
        for fault in ('run', 'head', 'dirty'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, inventory, git_mock, *rest):
                if fault == 'head': task['acceptance_failure_recovery']['expected_head'] = 'f'*40
                with self.assertRaises(loop.Stop):
                    loop.prepare_acceptance_resume(root, task, 'other' if fault == 'run' else self.run_id,
                                                   inventory, ['source.py'] if fault == 'dirty' else [])

    def test_changed_evidence_or_source_cannot_resume(self):
        for name in ('summary.json', 'mac-containment-0.json', 'task.md', 'task.json', 'source.py'):
            with self.subTest(name=name), self.fixture() as (root, old, task, inventory, *rest):
                path = root/name if name == 'source.py' else old/name
                path.write_text(path.read_text()+(' changed' if name == 'task.md' else ' '))
                if name == 'task.json': path.write_text('{}')
                with self.assertRaises(loop.Stop):
                    loop.prepare_acceptance_resume(root, task, self.run_id, inventory)

    def test_scope_or_gates_cannot_change(self):
        for key, value in (('allowed_files', ['source.py']), ('full_tests', [['weaker']]), ('max_repairs', 1)):
            with self.subTest(key=key), self.fixture() as (root, old, task, inventory, *rest):
                task[key] = value
                with self.assertRaises(loop.Stop):
                    loop.prepare_acceptance_resume(root, task, self.run_id, inventory)

    def test_published_branch_or_pr_cannot_resume(self):
        for replies in (['[{"number":47}]'], ['[]', 'published']):
            with self.subTest(replies=replies), self.fixture() as (root, old, task, inventory, git_mock, run_mock, command):
                run_mock.side_effect = replies
                with self.assertRaises(loop.Stop):
                    loop.prepare_acceptance_resume(root, task, self.run_id, inventory)

    def test_symlink_evidence_and_untracked_candidate_are_rejected(self):
        for fault in ('symlink', 'collision'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, inventory, *rest):
                if fault == 'symlink':
                    path = old/'summary.json'
                    raw = path.read_bytes(); path.unlink()
                    target = root/'other.json'; target.write_bytes(raw); path.symlink_to(target)
                else:
                    inventory['source.py'] = loop.digest(root/'source.py')
                with self.assertRaises(loop.Stop):
                    loop.prepare_acceptance_resume(root, task, self.run_id, inventory)

    def test_retained_pid_or_inspection_error_stops_without_signaling(self):
        for reply in ((0, '321\n', ''), (2, '', 'failed'), (0, '999\n', ''), (0, '', '')):
            with self.subTest(reply=reply), self.fixture() as (root, old, task, inventory, git_mock, run_mock, command):
                command.side_effect = lambda argv, *a, **kw: reply if argv[0] == '/bin/ps' else (
                    (0, '', '') if argv[0] == 'git' else (1, '', ''))
                with patch.object(loop.os, 'kill') as kill, patch.object(loop.os, 'killpg') as killpg:
                    with self.assertRaises(loop.Stop):
                        loop.prepare_acceptance_resume(root, task, self.run_id, inventory)
                    kill.assert_not_called(); killpg.assert_not_called()

    def test_snapshot_rechecks_private_evidence_without_export_copy(self):
        with self.fixture() as (root, old, task, inventory, *rest):
            _, recovery = loop.prepare_acceptance_resume(root, task, self.run_id, inventory)
            private = old/'private-review.json'
            private.write_text('{"secret":"model-output"}')
            recovery['private_evidence_files'] = {'private-review.json': loop.digest(private)}
            logs = root/'.ai-loop/new-run'; logs.mkdir()
            loop.snapshot_recovery(root, logs, recovery)
            self.assertFalse((logs/'recovery-evidence/private-review.json').exists())
            private.write_text('changed')
            logs2 = root/'.ai-loop/new-run-2'; logs2.mkdir()
            with self.assertRaises(loop.Stop):
                loop.snapshot_recovery(root, logs2, recovery)

    def test_snapshot_rechecks_failure_bytes(self):
        with self.fixture() as (root, old, task, inventory, *rest):
            _, recovery = loop.prepare_acceptance_resume(root, task, self.run_id, inventory)
            (old/'mac-containment-0.json').write_text('{}')
            logs = root/'.ai-loop/new-run'; logs.mkdir()
            with self.assertRaises(loop.Stop): loop.snapshot_recovery(root, logs, recovery)

    def test_new_schema_binds_failure_and_allows_only_one_recovery_policy(self):
        task = historical_task()
        task.pop('sandbox_decision_recovery', None)
        task.pop('review_failure_recovery', None)
        task.pop('stopped_failure_recovery', None)
        task['acceptance_failure_recovery'] = {'run_id': self.run_id, 'control_sha': '1'*40,
            'expected_head': '2'*40, 'summary_sha256': '3'*64, 'evidence_sha256': '4'*64}
        def load(value):
            with patch.object(loop, 'git', side_effect=[json.dumps(value), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        self.assertIn('acceptance_failure_recovery', load(task))
        for fault in ('missing_hash', 'bad_head', 'two_policies', 'zero_repairs', 'wrong_mode'):
            with self.subTest(fault=fault):
                bad = copy.deepcopy(task)
                if fault == 'missing_hash': bad['acceptance_failure_recovery'].pop('evidence_sha256')
                if fault == 'bad_head': bad['acceptance_failure_recovery']['expected_head'] = 'nope'
                if fault == 'two_policies': bad['initial_quota_recovery'] = {'run_id': self.run_id, 'control_sha': HEAD}
                if fault == 'zero_repairs': bad['max_repairs'] = 0
                if fault == 'wrong_mode': bad.update(mode='review_existing_pr', pr_number=47)
                with self.assertRaises(loop.Stop): load(bad)

    def test_different_failure_or_provenance_does_not_gain_recovery(self):
        for fault in ('scenario', 'reason', 'provenance'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, inventory, *rest):
                name = 'mac-containment-0.json' if fault == 'scenario' else ('summary.json' if fault == 'reason' else 'provenance.json')
                row = json.loads((old/name).read_text())
                if fault == 'scenario': row['failure']['scenario'] = 'parent_sigkill_plain'
                if fault == 'reason': row['reason'] = 'command timed out: codex'
                if fault == 'provenance': row['control_sha'] = 'c'*40
                loop.save_json(old/name, row)
                # Even a coordinator repin of the byte digest cannot generalize
                # this specific recovery to a different failure category.
                key = 'evidence_sha256' if fault == 'scenario' else 'summary_sha256'
                if fault != 'provenance': task['acceptance_failure_recovery'][key] = loop.digest(old/name)
                with self.assertRaises(loop.Stop): loop.prepare_acceptance_resume(root, task, self.run_id, inventory)




class SandboxDecisionRecoveryTests(unittest.TestCase):
    run_id = '20261008T100821Z-76644'
    test_path = 'tools/llmtool/tests/test_git_containment.py'
    report_path = 'reports/validation.md'
    candidate_head = 'e'*40
    control_sha = 'b'*40
    native_sha = 'd'*64
    original_reason = ('No files changed. Candidate, contract and evidence hashes match, '
        'but the sandbox denied the required fresh read-only process-absence check. '
        'Recovery rules block edits on inspection errors. The outer runner must '
        'establish absence proof outside the sandbox before repair.')

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            logs = root/'.ai-loop'/self.run_id
            logs.mkdir(parents=True)
            (root/self.test_path).parent.mkdir(parents=True)
            (root/self.test_path).write_text('original kqueue test')
            (root/self.report_path).parent.mkdir(parents=True)
            (root/self.report_path).write_text('native still pending')
            (root/'personal.txt').write_text('preserve')
            allowed = [self.test_path, self.report_path]
            files = {p: loop.digest(root/p) for p in allowed}
            old_task = {
                'mode': 'implement_new_pr', 'task_id': 'fixture',
                'branch': 'feat/fixture', 'base_sha': HEAD, 'expected_head': HEAD,
                'allowed_files': allowed, 'acceptance_report': self.report_path,
                'max_repairs': 4, 'failure_triage': {'max_reviews': 4,
                    'repeat_limit': 2, 'allow_mac_repair': True},
                'acceptance_failure_recovery': {
                    'run_id': '20261008T064055Z-64433',
                    'control_sha': 'c'*40, 'repair_files': [self.test_path]}}
            task = copy.deepcopy(old_task)
            task.pop('acceptance_failure_recovery')
            task['sandbox_decision_recovery'] = {
                'run_id': self.run_id, 'control_sha': self.control_sha,
                'expected_head': self.candidate_head,
                'summary_sha256': '0'*64, 'codex_result_sha256': '0'*64,
                'native_evidence_sha256': '0'*64, 'repair_files': [self.test_path]}
            loop.save_json(logs/'task.json', old_task)
            (logs/'task.md').write_text('original review handoff\n')
            loop.save_json(logs/'provenance.json', {
                'root': str(root), 'run_id': self.run_id,
                'base': HEAD, 'start_head': self.candidate_head,
                'control_sha': self.control_sha})
            loop.save_json(logs/'summary.json', {
                'status': 'STOPPED', 'branch': old_task['branch'],
                'head': self.candidate_head, 'history': [],
                'reason': ('bounded recovery Codex edited outside authorized test files '
                           'or made no first-round test-harness repair')})
            loop.save_json(logs/'codex-0.json', {
                'status': 'NEEDS_DECISION', 'summary': self.original_reason})
            owned = [{'pid': 72468, 'host_identity': {'pid': 72468},
                'supervisor': {}}]
            native = {
                'platform': 'darwin', 'schema_version': 1,
                'tested_sha256': loop.runtime_bytes(files),
                'failure': {'status': 'FAIL', 'scenario': 'kqueue_non_reaping',
                    'owned_processes': [], 'fixture_cleanup': {
                        'status': 'RESOLVED', 'original_owned_processes': owned}}}
            prior = logs/'recovery-evidence/mac-containment-0.json'
            prior.parent.mkdir()
            loop.save_json(prior, native)
            previous = {
                'kind': 'acceptance_failure', 'failure_scenario': 'kqueue_non_reaping',
                'from_run': '20261008T064055Z-64433',
                'starting_head': self.candidate_head, 'old_control_sha': 'c'*40,
                'partial_files': files,
                'evidence_files': {'mac-containment-0.json': loop.digest(prior)}}
            loop.save_json(logs/'recovery.json', previous)
            for path in allowed:
                frozen = logs/'recovery-inputs'/path
                frozen.parent.mkdir(parents=True, exist_ok=True)
                frozen.write_bytes((root/path).read_bytes())
            policy = task['sandbox_decision_recovery']
            policy['summary_sha256'] = loop.digest(logs/'summary.json')
            policy['codex_result_sha256'] = loop.digest(logs/'codex-0.json')
            policy['native_evidence_sha256'] = loop.digest(prior)
            personal = {'personal.txt': loop.digest(root/'personal.txt')}

            def git(cwd, *args):
                if args == ('branch', '--show-current'): return task['branch']
                if args == ('rev-parse', 'HEAD'): return self.candidate_head
                if args == ('diff', '--cached', '--name-only'): return ''
                if args == ('diff', '--name-only', '-z', HEAD, self.candidate_head):
                    return self.test_path+'\0'+self.report_path+'\0'
                raise AssertionError(args)

            stack.enter_context(patch.object(loop, 'load_task',
                return_value=(old_task, 'original review handoff')))
            stack.enter_context(patch.object(loop, 'git', side_effect=git))
            stack.enter_context(patch.object(loop, 'run', side_effect=['[]', '']))
            stack.enter_context(patch.object(loop, 'command', return_value=(0, '', '')))
            stack.enter_context(patch.object(loop, 'ensure_no_active_runner_codex'))
            absence = stack.enter_context(patch.object(loop, 'ensure_probe_inventory_absent'))
            yield root, logs, task, personal, absence, owned

    def test_no_change_decision_recovers_after_fresh_outer_absence(self):
        with self.fixture() as (root, old, task, personal, absence, owned):
            initial, recovery = loop.prepare_sandbox_decision_resume(
                root, task, self.run_id, personal)
            self.assertEqual(initial, personal)
            absence.assert_called_once_with(root, owned, allow_host_only=True)
            self.assertEqual(recovery['kind'], 'acceptance_failure')
            self.assertEqual(recovery['origin_kind'], 'sandbox_decision')
            self.assertEqual(recovery['outer_absence_check']['authority'], 'MAC_RUNNER')
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            self.assertEqual(recovery['repair_files'], [self.test_path])
            self.assertEqual(recovery['private_evidence_files']['codex-0.json'],
                             task['sandbox_decision_recovery']['codex_result_sha256'])
            new = root/'.ai-loop/new-run'; new.mkdir()
            loop.snapshot_recovery(root, new, recovery)
            self.assertFalse((new/'recovery-evidence/codex-0.json').exists())
            self.assertEqual((new/'recovery-inputs'/self.test_path).read_text(),
                             (root/self.test_path).read_text())

    def test_no_change_decision_rejects_evidence_and_candidate_drift(self):
        for kind in ('summary', 'codex', 'native', 'source', 'snapshot', 'control'):
            with self.subTest(kind=kind), self.fixture() as (root, old, task, personal, absence, owned):
                if kind == 'summary':
                    value = json.loads((old/'summary.json').read_text())
                    value['reason'] = 'different reason'
                    loop.save_json(old/'summary.json', value)
                    task['sandbox_decision_recovery']['summary_sha256'] = loop.digest(old/'summary.json')
                elif kind == 'codex':
                    value = json.loads((old/'codex-0.json').read_text())
                    value['status'] = 'PASS'
                    loop.save_json(old/'codex-0.json', value)
                    task['sandbox_decision_recovery']['codex_result_sha256'] = loop.digest(old/'codex-0.json')
                elif kind == 'native':
                    value = json.loads((old/'recovery-evidence/mac-containment-0.json').read_text())
                    value['failure']['scenario'] = 'other'
                    loop.save_json(old/'recovery-evidence/mac-containment-0.json', value)
                    task['sandbox_decision_recovery']['native_evidence_sha256'] = loop.digest(
                        old/'recovery-evidence/mac-containment-0.json')
                    p = json.loads((old/'recovery.json').read_text())
                    p['evidence_files']['mac-containment-0.json'] = task['sandbox_decision_recovery']['native_evidence_sha256']
                    loop.save_json(old/'recovery.json', p)
                elif kind == 'source':
                    (root/self.test_path).write_text('tampered')
                elif kind == 'snapshot':
                    (old/'recovery-inputs'/self.test_path).write_text('tampered')
                elif kind == 'control':
                    value = json.loads((old/'provenance.json').read_text())
                    value['control_sha'] = 'f'*40
                    loop.save_json(old/'provenance.json', value)
                with self.assertRaises(loop.Stop):
                    loop.prepare_sandbox_decision_resume(root, task, self.run_id, personal)

    def test_no_change_decision_schema_requires_single_exact_test(self):
        task = historical_task()
        task.pop('acceptance_failure_recovery', None)
        task['sandbox_decision_recovery'] = {
            'run_id': self.run_id, 'control_sha': self.control_sha,
            'expected_head': self.candidate_head, 'summary_sha256': 'a'*64,
            'codex_result_sha256': 'b'*64, 'native_evidence_sha256': 'c'*64,
            'repair_files': [self.test_path]}
        def load(value):
            with patch.object(loop, 'git', side_effect=[json.dumps(value), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        self.assertIn('sandbox_decision_recovery', load(task))
        for fault in ('extra', 'wrong_repair', 'two_recoveries', 'missing_hash'):
            with self.subTest(fault=fault):
                bad = copy.deepcopy(task)
                policy = bad['sandbox_decision_recovery']
                if fault == 'extra': policy['other'] = True
                elif fault == 'wrong_repair':
                    policy['repair_files'] = ['tools/llmtool/llmtool/_git_process_identity.py']
                elif fault == 'two_recoveries':
                    bad['acceptance_failure_recovery'] = {'run_id': self.run_id}
                elif fault == 'missing_hash': policy.pop('codex_result_sha256')
                with self.assertRaises(loop.Stop): load(bad)


class ReviewFailureRecoveryTests(unittest.TestCase):
    run_id = '20261008T052643Z-60914'

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            old = root / '.ai-loop' / self.run_id
            old.mkdir(parents=True)
            tests_dir = root / 'tools/llmtool/tests'
            tests_dir.mkdir(parents=True)
            (tests_dir/'run_containment_acceptance.py').write_text('old pinned base\n')
            (tests_dir/'test_git_containment.py').write_text('old synthetic fixture\n')
            report = root / 'reports/validation.md'
            report.parent.mkdir()
            report.write_text('pending native gates\n')
            (root/'personal.txt').write_text('preserve\n')
            base, candidate, control = 'a'*40, 'b'*40, 'c'*40
            files = ['tools/llmtool/tests/run_containment_acceptance.py',
                     'tools/llmtool/tests/test_git_containment.py', 'reports/validation.md']
            baseline = {'mode': 'implement_new_pr', 'task_id': 'fixture',
                'branch': 'feat/fixture', 'base_sha': base, 'expected_head': base,
                'max_repairs': 4, 'allowed_files': files, 'acceptance_report': 'reports/validation.md',
                'full_tests': [['full']], 'failure_triage': {'max_reviews': 4,
                    'repeat_limit': 2, 'allow_mac_repair': True}}
            tested = {p: loop.digest(root/p) for p in files}
            review = {'status': 'FAIL', 'reviewed_head': candidate, 'summary': 'Two stale base pins.',
                'findings': [
                    {'severity': 'High', 'blocking': True, 'file': files[0],
                     'detail': 'Harness pins old base.', 'evidence': 'Review cites harness line 70.'},
                    {'severity': 'Medium', 'blocking': True, 'file': files[1],
                     'detail': 'Fixture pins old base.', 'evidence': 'Review cites fixture.'}]}
            failure = {'run_id': self.run_id, 'task_id': 'fixture', 'round': 0,
                'phase': 'review', 'head': candidate, 'status': 'FAIL', 'gate_waived': False,
                'source_sha256': tested, 'evidence': {'review': review}}
            loop.save_json(old/'task.json', baseline)
            (old/'task.md').write_text('original review contract\n')
            loop.save_json(old/'provenance.json', {'root': str(root), 'run_id': self.run_id,
                'control_sha': control, 'base': base})
            loop.save_json(old/'summary.json', {'status': 'STOPPED', 'branch': baseline['branch'],
                'head': candidate, 'history': [{'round': 0, 'review': review}]})
            loop.save_json(old/'failure-0.json', failure)
            task = copy.deepcopy(baseline)
            task['review_failure_recovery'] = {
                'run_id': self.run_id, 'control_sha': control,
                'expected_head': candidate, 'summary_sha256': loop.digest(old/'summary.json'),
                'failure_evidence_sha256': loop.digest(old/'failure-0.json'),
                'repair_files': files[:2]}
            initial = {'personal.txt': loop.digest(root/'personal.txt')}
            def git(cwd, *args):
                if args == ('branch', '--show-current'): return task['branch']
                if args == ('rev-parse', 'HEAD'): return candidate
                if args == ('diff', '--cached', '--name-only'): return ''
                if args == ('diff', '--name-only', '-z', base, candidate):
                    return '\0'.join(files)+'\0'
                raise AssertionError(args)
            stack.enter_context(patch.object(loop, 'load_task',
                return_value=(baseline, 'original review contract')))
            stack.enter_context(patch.object(loop, 'git', side_effect=git))
            stack.enter_context(patch.object(loop, 'run', side_effect=['[]', '']))
            stack.enter_context(patch.object(loop, 'command', return_value=(0, '', '')))
            stack.enter_context(patch.object(loop, 'ensure_no_active_runner_codex'))
            yield root, old, task, initial

    def test_exact_failed_review_authorizes_two_file_repair_not_old_pass(self):
        with self.fixture() as (root, old, task, initial):
            original, recovery = loop.prepare_review_failure_resume(
                root, task, self.run_id, initial)
            self.assertEqual(original, initial)
            self.assertEqual(recovery['kind'], 'review_failure')
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            self.assertEqual(set(recovery['repair_files']),
                             set(task['review_failure_recovery']['repair_files']))
            logs = root/'.ai-loop/new'; logs.mkdir()
            loop.snapshot_recovery(root, logs, recovery)
            self.assertTrue((logs/'recovery-evidence/failure-0.json').exists())

    def test_review_recovery_fails_on_candidate_or_review_drift(self):
        for fault in ('candidate', 'finding', 'provenance', 'summary'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, initial):
                if fault == 'candidate':
                    (root/'tools/llmtool/tests/test_git_containment.py').write_text('modified\n')
                elif fault == 'finding':
                    value = json.loads((old/'failure-0.json').read_text())
                    value['evidence']['review']['findings'][0]['file'] = 'other.py'
                    loop.save_json(old/'failure-0.json', value)
                    task['review_failure_recovery']['failure_evidence_sha256'] = loop.digest(old/'failure-0.json')
                elif fault == 'provenance':
                    value = json.loads((old/'provenance.json').read_text())
                    value['control_sha'] = 'd'*40
                    loop.save_json(old/'provenance.json', value)
                else:
                    (old/'summary.json').write_text('{}')
                with self.assertRaises(loop.Stop):
                    loop.prepare_review_failure_resume(root, task, self.run_id, initial)

    def test_review_recovery_only_two_tests_or_report(self):
        task = {'acceptance_report': 'reports/validation.md'}
        recovery = {'kind': 'review_failure', 'repair_files': [
            'tools/llmtool/tests/run_containment_acceptance.py',
            'tools/llmtool/tests/test_git_containment.py']}
        first = recovery['repair_files'][0]
        loop.ensure_review_repair_scope(task, recovery, 0, [first, task['acceptance_report']])
        for paths in ([], ['reports/validation.md'], [first, 'tools/llmtool/llmtool/_git_containment.py']):
            with self.subTest(paths=paths), self.assertRaises(loop.Stop):
                loop.ensure_review_repair_scope(task, recovery, 0, paths)
        # Follow-up bounded repairs still require ordinary evidence-bound review.
        loop.ensure_review_repair_scope(task, recovery, 1, [])

    def test_review_recovery_schema_rejects_extra_authority(self):
        task = historical_task()
        task.pop('sandbox_decision_recovery', None)
        task.pop('stopped_failure_recovery', None)
        task.pop('acceptance_failure_recovery', None)
        task['review_failure_recovery'] = {
            'run_id': self.run_id, 'control_sha': 'a'*40, 'expected_head': 'b'*40,
            'summary_sha256': 'c'*64, 'failure_evidence_sha256': 'd'*64,
            'repair_files': ['tools/llmtool/tests/run_containment_acceptance.py',
                             'tools/llmtool/tests/test_git_containment.py']}
        def load(value):
            with patch.object(loop, 'git', side_effect=[json.dumps(value), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        self.assertIn('review_failure_recovery', load(task))
        for fault in ('different_file', 'one_file', 'duplicate', 'extra', 'two_recoveries'):
            with self.subTest(fault=fault):
                bad = copy.deepcopy(task)
                policy = bad['review_failure_recovery']
                if fault == 'different_file':
                    policy['repair_files'][0] = 'tools/llmtool/llmtool/_git_containment.py'
                elif fault == 'one_file':
                    policy['repair_files'].pop()
                elif fault == 'duplicate':
                    policy['repair_files'][1] = policy['repair_files'][0]
                elif fault == 'extra':
                    policy['other'] = True
                else:
                    bad['stopped_failure_recovery'] = {'run_id': self.run_id}
                with self.assertRaises(loop.Stop): load(bad)


class StoppedFailureRecoveryTests(unittest.TestCase):
    run_id = '20261007T183516Z-7365'

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as runtime, ExitStack() as stack:
            root = Path(runtime)
            old = root / '.ai-loop' / self.run_id
            old.mkdir(parents=True)
            (root / 'source.py').write_text('candidate source\n')
            (root / 'reports').mkdir()
            (root / 'reports/validation.md').write_text('candidate report\n')
            (root / 'tests').mkdir()
            (root / 'tests/flaky.py').write_text('upstream fix\n')
            (root / 'user.txt').write_text('preserved\n')
            old_base, new_base, candidate, control = '1'*40, '2'*40, '3'*40, '4'*40
            baseline = {'mode': 'implement_new_pr', 'task_id': 'fixture',
                'branch': 'feat/fixture', 'base_sha': old_base, 'expected_head': old_base,
                'max_repairs': 4, 'allowed_files': ['source.py', 'reports/validation.md'],
                'full_tests': [['full']], 'failure_triage': {
                    'max_reviews': 4, 'repeat_limit': 2, 'allow_mac_repair': True}}
            tested = {p: loop.digest(root/p) for p in baseline['allowed_files']}
            loop.save_json(old/'task.json', baseline)
            (old/'task.md').write_text('original requirements\n')
            loop.save_json(old/'provenance.json', {'root': str(root), 'run_id': self.run_id,
                'base': old_base, 'control_sha': control})
            loop.save_json(old/'summary.json', {'status': 'STOPPED', 'branch': baseline['branch'],
                'head': candidate, 'history': [{'round': 2, 'head': candidate,
                    'final_full': {'status': 'FAIL'}}]})
            failure = {'run_id': self.run_id, 'task_id': baseline['task_id'], 'round': 2,
                'phase': 'full-final', 'head': candidate, 'status': 'FAIL', 'gate_waived': False,
                'source_sha256': tested}
            loop.save_json(old/'failure-2.json', failure)
            mac = {'platform': 'darwin', 'failure': {'owned_processes': [],
                'fixture_cleanup': {'status': 'RESOLVED', 'original_owned_processes': [{
                    'pid': 90, 'host_identity': {'pid': 90}, 'supervisor': {'pid': 91}, 'members': []}]}}}
            loop.save_json(old/'mac-containment-1.json', mac)
            task = copy.deepcopy(baseline)
            task.update(base_sha=new_base, expected_head=new_base)
            task['stopped_failure_recovery'] = {
                'run_id': self.run_id, 'control_sha': control, 'expected_head': candidate,
                'old_base_sha': old_base, 'summary_sha256': loop.digest(old/'summary.json'),
                'failure_round': 2, 'failure_evidence_sha256': loop.digest(old/'failure-2.json'),
                'failure_phase': 'full-final', 'classification_rejection': 'SUMMARY_TOO_LONG',
                'mac_evidence_sha256': loop.digest(old/'mac-containment-1.json'),
                'upstream_paths': ['tests/flaky.py']}
            inventory = {'user.txt': loop.digest(root/'user.txt')}

            def fake_git(cwd, *args):
                if args == ('branch', '--show-current'): return task['branch']
                if args == ('rev-parse', 'HEAD'): return candidate
                if args == ('diff', '--cached', '--name-only'): return ''
                if args == ('diff', '--name-only', '-z', old_base, candidate):
                    return 'source.py\0reports/validation.md\0'
                if args == ('diff', '--name-only', '-z', old_base, new_base):
                    return 'tests/flaky.py\0'
                raise AssertionError(args)

            stack.enter_context(patch.object(loop, 'load_task', return_value=(baseline, 'original requirements')))
            stack.enter_context(patch.object(loop, 'git', side_effect=fake_git))
            stack.enter_context(patch.object(loop, 'run', side_effect=['[]', '']))
            stack.enter_context(patch.object(loop, 'command', return_value=(0, '', '')))
            stack.enter_context(patch.object(loop, 'classification_rejection', return_value={
                'head': candidate, 'evidence_sha256': task['stopped_failure_recovery']['failure_evidence_sha256'],
                'reason_codes': ['SUMMARY_TOO_LONG'], 'recovery_authorized': False}))
            stack.enter_context(patch.object(loop, 'ensure_probe_inventory_absent'))
            stack.enter_context(patch.object(loop, 'ensure_no_active_runner_codex'))
            yield root, old, task, inventory

    def test_exact_stopped_failure_allows_validate_first_recovery(self):
        with self.fixture() as (root, old, task, inventory):
            original, recovery = loop.prepare_stopped_failure_resume(
                root, task, self.run_id, inventory)
            self.assertEqual(original, inventory)
            self.assertEqual(recovery['kind'], 'stopped_failure')
            self.assertTrue(recovery['validate_first'])
            self.assertEqual(recovery['old_base_sha'], '1'*40)
            self.assertEqual(recovery['upstream_base_sha'], '2'*40)
            self.assertEqual(recovery['upstream_paths'], ['tests/flaky.py'])
            self.assertEqual(recovery['inherited_validation'], 'NONE')

    def test_out_of_scope_test_bug_can_resume_only_after_exact_upstream_fix(self):
        with self.fixture() as (root, old, task, inventory):
            raw_name = 'failure-2-claude.raw.json'
            value = {'decision': 'NEEDS_DECISION', 'category': 'TEST_BUG',
                'reviewed_head': '3'*40,
                'reviewed_evidence_sha256': task['stopped_failure_recovery']['failure_evidence_sha256'],
                'summary': 'The blocking test bug is outside this task scope.',
                'findings': [{'severity': 'High', 'blocking': True, 'file': 'tests/flaky.py',
                              'detail': 'Repair the independent flaky test.',
                              'evidence': 'The exact failed full-suite evidence identifies this test.'},
                             {'severity': 'Medium', 'blocking': True, 'file': 'source.py',
                              'detail': 'Required validation remains pending. Nothing here needs a containment-file repair.',
                              'evidence': 'No fresh gate PASS exists on the final bytes.'}]}
            loop.save_json(old/raw_name, {'is_error': False, 'subtype': 'success',
                                         'structured_output': value})
            mac = {'platform': 'darwin', 'failure': {'status': 'FAIL', 'scenario': 'hung_supervisor',
                'owned_processes': [{'pid': 90, 'host_identity': {'pid': 90},
                                     'supervisor': {'pid': 91}, 'members': []}]}}
            loop.save_json(old/'mac-containment-0.json', mac)
            policy = task['stopped_failure_recovery']
            policy.pop('classification_rejection')
            policy.pop('mac_evidence_sha256')
            policy.update(classification_decision='NEEDS_DECISION',
                classification_category='TEST_BUG',
                classification_raw_sha256=loop.digest(old/raw_name),
                prior_mac_evidence_file='mac-containment-0.json',
                prior_mac_evidence_sha256=loop.digest(old/'mac-containment-0.json'),
                nonrepair_blocking_files=['source.py'])
            original, recovery = loop.prepare_stopped_failure_resume(
                root, task, self.run_id, inventory)
            self.assertEqual(original, inventory)
            self.assertEqual(recovery['upstream_paths'], ['tests/flaky.py'])
            self.assertEqual(recovery['private_evidence_files'],
                             {raw_name: policy['classification_raw_sha256']})
            self.assertEqual(recovery['inherited_validation'], 'NONE')

    def test_out_of_scope_recovery_rejects_wrong_blocker_or_changed_raw(self):
        for fault in ('blocker', 'raw'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, inventory):
                raw_name = 'failure-2-claude.raw.json'
                blocker = 'source.py' if fault == 'blocker' else 'tests/flaky.py'
                value = {'decision': 'NEEDS_DECISION', 'category': 'TEST_BUG',
                    'reviewed_head': '3'*40,
                    'reviewed_evidence_sha256': task['stopped_failure_recovery']['failure_evidence_sha256'],
                    'summary': 'Bounded classification.',
                    'findings': [{'severity': 'High', 'blocking': True, 'file': blocker,
                                  'detail': 'Blocking finding.', 'evidence': 'Exact evidence.'}]}
                loop.save_json(old/raw_name, {'is_error': False, 'subtype': 'success',
                                             'structured_output': value})
                mac = {'platform': 'darwin', 'failure': {'status': 'FAIL',
                    'scenario': 'hung_supervisor', 'owned_processes': [
                        {'pid': 90, 'host_identity': {'pid': 90},
                         'supervisor': {'pid': 91}, 'members': []}]}}
                loop.save_json(old/'mac-containment-0.json', mac)
                policy = task['stopped_failure_recovery']
                policy.pop('classification_rejection')
                policy.pop('mac_evidence_sha256')
                policy.update(classification_decision='NEEDS_DECISION',
                    classification_category='TEST_BUG',
                    classification_raw_sha256=loop.digest(old/raw_name),
                    prior_mac_evidence_file='mac-containment-0.json',
                    prior_mac_evidence_sha256=loop.digest(old/'mac-containment-0.json'),
                    nonrepair_blocking_files=[])
                if fault == 'raw':
                    (old/raw_name).write_text((old/raw_name).read_text() + ' ')
                with self.assertRaises(loop.Stop):
                    loop.prepare_stopped_failure_resume(root, task, self.run_id, inventory)

    def test_out_of_scope_recovery_schema_is_strict(self):
        task = historical_task()
        task.pop('sandbox_decision_recovery', None)
        task.pop('review_failure_recovery', None)
        task.pop('acceptance_failure_recovery', None)
        task.pop('stopped_failure_recovery', None)
        task['stopped_failure_recovery'] = {
            'run_id': self.run_id, 'control_sha': '1'*40, 'expected_head': '2'*40,
            'old_base_sha': '3'*40, 'summary_sha256': '4'*64, 'failure_round': 1,
            'failure_evidence_sha256': '5'*64, 'failure_phase': 'full-final',
            'classification_decision': 'NEEDS_DECISION', 'classification_category': 'TEST_BUG',
            'classification_raw_sha256': '6'*64,
            'prior_mac_evidence_file': 'mac-containment-0.json',
            'prior_mac_evidence_sha256': '7'*64,
            'nonrepair_blocking_files': [],
            'upstream_paths': ['tools/llmtool/tests/test_entrypoint_interrupt.py']}
        def load(value):
            with patch.object(loop, 'git', side_effect=[json.dumps(value), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        self.assertEqual(load(task)['stopped_failure_recovery']['classification_category'], 'TEST_BUG')
        for fault in ('decision', 'category', 'raw_hash', 'mac_name', 'overlap',
                      'nonrepair_outside', 'nonrepair_overlap', 'extra'):
            with self.subTest(fault=fault):
                bad = copy.deepcopy(task); recovery = bad['stopped_failure_recovery']
                if fault == 'decision': recovery['classification_decision'] = 'REPAIR'
                elif fault == 'category': recovery['classification_category'] = 'ENVIRONMENT'
                elif fault == 'raw_hash': recovery['classification_raw_sha256'] = 'bad'
                elif fault == 'mac_name': recovery['prior_mac_evidence_file'] = '../mac.json'
                elif fault == 'overlap': recovery['upstream_paths'] = [bad['allowed_files'][0]]
                elif fault == 'nonrepair_outside': recovery['nonrepair_blocking_files'] = ['tests/outside.py']
                elif fault == 'nonrepair_overlap':
                    recovery['nonrepair_blocking_files'] = [recovery['upstream_paths'][0]]
                else: recovery['extra'] = True
                with self.assertRaises(loop.Stop): load(bad)

    def test_wrong_rejection_or_upstream_paths_stop(self):
        for fault in ('rejection', 'upstream'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, inventory):
                if fault == 'rejection':
                    task['stopped_failure_recovery']['classification_rejection'] = 'OTHER'
                else:
                    task['stopped_failure_recovery']['upstream_paths'] = ['tests/other.py']
                with self.assertRaises(loop.Stop):
                    loop.prepare_stopped_failure_resume(root, task, self.run_id, inventory)

    def test_changed_candidate_or_failure_evidence_stops(self):
        for fault in ('source', 'failure'):
            with self.subTest(fault=fault), self.fixture() as (root, old, task, inventory):
                if fault == 'source':
                    (root/'source.py').write_text('changed')
                else:
                    row = json.loads((old/'failure-2.json').read_text())
                    row['phase'] = 'related'
                    loop.save_json(old/'failure-2.json', row)
                with self.assertRaises(loop.Stop):
                    loop.prepare_stopped_failure_resume(root, task, self.run_id, inventory)

    def test_stopped_recovery_schema_is_strict_and_mutually_exclusive(self):
        task = historical_task()
        task.pop('sandbox_decision_recovery', None)
        task.pop('review_failure_recovery', None)
        task.pop('acceptance_failure_recovery', None)
        task['stopped_failure_recovery'] = {
            'run_id': self.run_id, 'control_sha': '1'*40, 'expected_head': '2'*40,
            'old_base_sha': '3'*40, 'summary_sha256': '4'*64, 'failure_round': 2,
            'failure_evidence_sha256': '5'*64, 'failure_phase': 'full-final',
            'classification_rejection': 'SUMMARY_TOO_LONG', 'mac_evidence_sha256': '6'*64,
            'upstream_paths': ['tools/llmtool/tests/test_diagnostics_failure_matrix.py']}
        def load(value):
            with patch.object(loop, 'git', side_effect=[json.dumps(value), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        self.assertIn('stopped_failure_recovery', load(task))
        for fault in ('reason', 'path_overlap', 'two_policies', 'same_base'):
            with self.subTest(fault=fault):
                bad = copy.deepcopy(task)
                recovery = bad['stopped_failure_recovery']
                if fault == 'reason':
                    recovery['classification_rejection'] = 'OTHER'
                elif fault == 'path_overlap':
                    recovery['upstream_paths'] = [bad['allowed_files'][0]]
                elif fault == 'two_policies':
                    bad['acceptance_failure_recovery'] = {'run_id': self.run_id,
                        'control_sha': '1'*40, 'expected_head': '2'*40,
                        'summary_sha256': '3'*64, 'evidence_sha256': '4'*64}
                else:
                    recovery['old_base_sha'] = bad['base_sha']
                with self.assertRaises(loop.Stop):
                    load(bad)

    def test_integrate_recovery_base_creates_two_parent_merge_without_changing_candidate(self):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.email', 'fixture@example.invalid'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Fixture'], cwd=root, check=True)
            (root/'tests').mkdir()
            (root/'source.py').write_text('base\n')
            (root/'tests/flaky.py').write_text('old\n')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'base'], cwd=root, check=True)
            old_base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
            subprocess.run(['git', 'switch', '-qc', 'feat/fixture'], cwd=root, check=True)
            (root/'source.py').write_text('candidate\n')
            subprocess.run(['git', 'add', 'source.py'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'candidate'], cwd=root, check=True)
            candidate = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
            source_hash = loop.digest(root/'source.py')
            subprocess.run(['git', 'switch', '-q', 'main'], cwd=root, check=True)
            (root/'tests/flaky.py').write_text('fixed\n')
            subprocess.run(['git', 'add', 'tests/flaky.py'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'upstream'], cwd=root, check=True)
            new_base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
            subprocess.run(['git', 'switch', '-q', 'feat/fixture'], cwd=root, check=True)
            logs = root/'.ai-loop/new'
            logs.mkdir(parents=True)
            recovery = {'kind': 'stopped_failure', 'starting_head': candidate,
                'partial_files': {'source.py': source_hash},
                'upstream_paths': ['tests/flaky.py']}
            task = {'base_sha': new_base, 'allowed_files': ['source.py']}
            head = loop.integrate_recovery_base(root, task, logs, recovery)
            parents = subprocess.check_output(
                ['git', 'rev-list', '--parents', '-n', '1', head], cwd=root, text=True).split()
            self.assertEqual(set(parents[1:]), {candidate, new_base})
            self.assertEqual((root/'source.py').read_text(), 'candidate\n')
            self.assertEqual((root/'tests/flaky.py').read_text(), 'fixed\n')
            self.assertEqual(loop.git(root, 'diff', '--name-only', new_base, head), 'source.py')
            self.assertEqual(json.loads((logs/'recovery.json').read_text())['integrated_head'], head)



class DiagnosticsExportTests(unittest.TestCase):
    def test_visible_zip_contains_curated_evidence_and_source_but_no_provider_streams(self):
        import zipfile
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime); logs = root/'.ai-loop/run'; logs.mkdir(parents=True)
            (root/'source.py').write_text('candidate')
            loop.save_json(logs/'summary.json', {'status': 'STOPPED'})
            loop.save_json(logs/'mac-containment-0.json', {'failure': {'status': 'FAIL'}})
            (logs/'codex-0.stderr.log').write_text('private provider data')
            (logs/'claude-0.raw.json').write_text('private provider data')
            (logs/'tests-0-mandatory-final-0.stderr.log').write_text('probe diagnostic')
            path = loop.export_diagnostics(root, logs, {'allowed_files': ['source.py']})
            self.assertEqual(path.parent.name, 'ai-loop-diagnostics')
            with zipfile.ZipFile(path) as archive:
                self.assertIn('source.py', archive.namelist())
                self.assertIn('evidence/mac-containment-0.json', archive.namelist())
                self.assertIn('captured-sha256.json', archive.namelist())
                self.assertFalse(any('codex' in p or 'claude' in p for p in archive.namelist()))

    def test_native_failure_triage_is_local_and_provider_metadata_is_curated(self):
        import zipfile
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            run_id = '20261008T064055Z-64433'
            logs = root/'.ai-loop'/run_id
            logs.mkdir(parents=True)
            src = root/'tools/llmtool/tests/test_git_containment.py'
            src.parent.mkdir(parents=True)
            src.write_text('\n'.join('line-%s PRIVATE_SOURCE_TOKEN' % i for i in range(1, 25)))
            failure = {'schema_version': 1, 'platform': 'darwin',
                'failure': {'status': 'FAIL', 'scenario': 'kqueue_non_reaping',
                    'exception_type': 'ProcessLookupError',
                    'traceback': [{'file': src.name, 'function': 'kqueue_non_reaping',
                                   'line': 12}],
                    'fixture_cleanup': {'status': 'RESOLVED'}, 'owned_processes': []}}
            loop.save_json(logs/'mac-containment-0.json', failure)
            loop.save_json(logs/'failure-0-claude.raw.json', {
                'subtype': 'success', 'is_error': True,
                'result': 'PRIVATE_CLAUDE_PROVIDER_PAYLOAD'})
            task = {'allowed_files': ['tools/llmtool/tests/test_git_containment.py']}
            public = loop.record_diagnostic_triage(root, logs, task)
            self.assertEqual(public['native_failures'][0]['frames'][0]['line'], 12)
            self.assertEqual(public['provider_envelopes'][0]['state'], 'ERROR_ENVELOPE')
            self.assertEqual(public['provider_envelopes'][0]['subtype'], 'success')
            local = json.loads((logs/'triage-local.json').read_text())
            self.assertIn('PRIVATE_SOURCE_TOKEN', json.dumps(local))
            self.assertNotIn('PRIVATE_SOURCE_TOKEN', json.dumps(public))
            self.assertNotIn('PRIVATE_CLAUDE_PROVIDER_PAYLOAD', json.dumps(local))
            (logs/'summary.json').write_text('{"status":"STOPPED"}')
            archive_path = loop.export_diagnostics(root, logs, task)
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertIn('evidence/triage-public.json', names)
                self.assertNotIn('evidence/triage-local.json', names)
                raw_archive = b''.join(archive.read(name) for name in names)
                self.assertNotIn(b'PRIVATE_CLAUDE_PROVIDER_PAYLOAD', raw_archive)
                # Candidate source is retained only in the local ZIP and never Git published.
                self.assertIn(b'PRIVATE_SOURCE_TOKEN', raw_archive)

    def test_public_triage_validation_never_allows_source_or_provider_text(self):
        run_id = '20261008T064055Z-64433'
        task = {'allowed_files': ['tools/llmtool/tests/test_git_containment.py']}
        good = {'run_id': run_id, 'native_failures': [{
            'evidence_file': 'mac-containment-0.json', 'scenario': 'kqueue_non_reaping',
            'exception_type': 'ProcessLookupError', 'cleanup_status': 'RESOLVED',
            'owned_processes_retained': False,
            'frames': [{'file': task['allowed_files'][0], 'line': 2334,
                        'function': 'kqueue_non_reaping'}]}],
            'provider_envelopes': [{'file': 'failure-0-claude.raw.json',
                'subtype': 'success', 'is_error': True, 'state': 'ERROR_ENVELOPE'}],
            'contains_source_text': False, 'contains_provider_text': False,
            'repair_authorized': False, 'gates_inherited': False}
        self.assertEqual(loop.validate_public_triage(good, task, run_id), good)
        for fault in ('extra', 'source_text', 'wrong_path', 'wrong_line',
                      'provider_payload', 'authority', 'wrong_provider'):
            with self.subTest(fault=fault):
                bad = copy.deepcopy(good)
                if fault == 'extra': bad['extra'] = 'PRIVATE_SOURCE_TOKEN'
                elif fault == 'source_text': bad['contains_source_text'] = True
                elif fault == 'wrong_path':
                    bad['native_failures'][0]['frames'][0]['file'] = '../secrets.json'
                elif fault == 'wrong_line':
                    bad['native_failures'][0]['frames'][0]['line'] = '2334;source'
                elif fault == 'provider_payload':
                    bad['provider_envelopes'][0]['result'] = 'PRIVATE_CLAUDE_PROVIDER_PAYLOAD'
                elif fault == 'authority': bad['repair_authorized'] = True
                else: bad['provider_envelopes'][0]['file'] = 'credentials.json'
                with self.assertRaises(loop.Stop):
                    loop.validate_public_triage(bad, task, run_id)

    def test_codex_stopped_outcome_publishes_status_and_file_deltas_not_model_text(self):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            logs = root / '.ai-loop' / '20261008T100821Z-76644'
            logs.mkdir(parents=True)
            tests = root / 'tools/llmtool/tests/test_git_containment.py'
            tests.parent.mkdir(parents=True)
            tests.write_text('unchanged source')
            report = root / 'reports/probe.md'
            report.parent.mkdir(parents=True)
            report.write_text('pending gates')
            allowed = ['tools/llmtool/tests/test_git_containment.py', 'reports/probe.md']
            for name in allowed:
                target = logs / 'recovery-inputs' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((root / name).read_bytes())
            local = {'status': 'NEEDS_DECISION', 'summary': 'PRIVATE_CLAUDE_SECRET'}
            loop.save_json(logs / 'codex-0.json', local)
            task = {'allowed_files': allowed, 'acceptance_failure_recovery': {
                'repair_files': [allowed[0]]}}
            value = loop.diagnostic_codex_outcome(root, logs, task)
            self.assertEqual(value['developer_status'], 'NEEDS_DECISION')
            self.assertEqual(value['changed_allowed_files'], [])
            self.assertEqual(value['repair_files_changed'], [])
            self.assertEqual(value['result_sha256'], loop.digest(logs / 'codex-0.json'))
            self.assertNotIn('PRIVATE_CLAUDE_SECRET', json.dumps(value))
            report.write_text('updated report')
            changed = loop.diagnostic_codex_outcome(root, logs, task)
            self.assertEqual(changed['changed_allowed_files'], [allowed[1]])
            self.assertEqual(changed['repair_files_changed'], [])
            tests.write_text('fixed test')
            fixed = loop.diagnostic_codex_outcome(root, logs, task)
            self.assertEqual(fixed['repair_files_changed'], [allowed[0]])
            (logs / 'codex-0.json').write_text('{"status":"PASS","summary":"x","extra":"SECRET"}')
            with self.assertRaises(loop.Stop):
                loop.diagnostic_codex_outcome(root, logs, task)


    def test_new_task_codex_diagnostic_uses_pinned_git_without_recovery_snapshot(self):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            run_id = '20261008T185112Z-21061'
            logs = root / '.ai-loop' / run_id
            logs.mkdir(parents=True)
            base = '8' * 40
            (root / 'existing.py').write_text('original')
            loop.save_json(logs / 'provenance.json', {
                'root': str(root), 'run_id': run_id,
                'start_branch': 'main', 'start_head': base, 'base': base})
            loop.save_json(logs / 'codex-0.json', {
                'status': 'FAIL', 'summary': 'PRIVATE_PROVIDER_DETAILS'})
            task = {'mode': 'implement_new_pr', 'base_sha': base,
                    'branch': 'feat/llmtool-c1-test-excerpts-20261009',
                    'allowed_files': ['existing.py', 'brand_new.py']}
            old_blob = 'a' * 40
            state = {'existing_blob': old_blob, 'staged': False,
                     'head': base, 'tree_override': None}
            def fake_git(_root, *argv):
                calls = {
                    ('rev-parse', 'HEAD'): state['head'],
                    ('branch', '--show-current'): task['branch'],
                    ('diff', '--cached', '--name-only'): 'staged.py' if state['staged'] else '',
                    ('ls-tree', '-r', '-z', '--full-tree', base, '--', 'existing.py'):
                        (state['tree_override'] if state['tree_override'] is not None
                         else '100644 blob ' + old_blob + '\t' + 'existing.py' + '\0'),
                    ('ls-tree', '-r', '-z', '--full-tree', base, '--', 'brand_new.py'): '',
                    ('hash-object', '--', 'existing.py'): state['existing_blob'],
                    ('hash-object', '--', 'brand_new.py'): 'b' * 40}
                if argv not in calls:
                    raise AssertionError(argv)
                return calls[argv]
            with patch.object(loop, 'git', side_effect=fake_git):
                result = loop.diagnostic_codex_outcome(root, logs, task)
                self.assertEqual(result['developer_status'], 'FAIL')
                self.assertEqual(result['changed_allowed_files'], [])
                self.assertFalse(result['repair_authorized'])
                self.assertNotIn('PRIVATE_PROVIDER_DETAILS', json.dumps(result))
                (root / 'brand_new.py').write_text('new code')
                result = loop.diagnostic_codex_outcome(root, logs, task)
                self.assertEqual(result['changed_allowed_files'], ['brand_new.py'])
                state['existing_blob'] = 'changedblob'
                result = loop.diagnostic_codex_outcome(root, logs, task)
                self.assertEqual(result['changed_allowed_files'], ['brand_new.py', 'existing.py'])
                state['staged'] = True
                with self.assertRaisesRegex(loop.Stop, 'wrong Git provenance'):
                    loop.diagnostic_codex_outcome(root, logs, task)
                state['staged'] = False
                state['head'] = 'a' * 40
                with self.assertRaisesRegex(loop.Stop, 'wrong Git provenance'):
                    loop.diagnostic_codex_outcome(root, logs, task)
                state['head'] = base
                state['tree_override'] = '120000 blob ' + old_blob + '\t' + 'existing.py' + '\0'
                with self.assertRaisesRegex(loop.Stop, 'Git tree entry unsafe'):
                    loop.diagnostic_codex_outcome(root, logs, task)
                state['tree_override'] = '100644 blob ' + old_blob + '\t' + '../escape.py' + '\0'
                with self.assertRaisesRegex(loop.Stop, 'Git tree entry unsafe'):
                    loop.diagnostic_codex_outcome(root, logs, task)
                state['tree_override'] = None
                (root / 'brand_new.py').unlink()
                (root / 'brand_new.py').symlink_to(root / 'existing.py')
                with self.assertRaisesRegex(loop.Stop, 'candidate path unsafe'):
                    loop.diagnostic_codex_outcome(root, logs, task)

    def test_new_task_codex_diagnostic_real_git_missing_base_path(self):
        # Regression for the actual Mac STOP: 'git cat-file -e SHA:missing'
        # returns 128; a missing new C1 file must be reported as absent,
        # while bad Git objects must still fail closed.
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            def invoke(*argv):
                return subprocess.run(['git', *argv], cwd=str(root), check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()
            invoke('init', '-q')
            (root / 'existing.py').write_text('initial source')
            invoke('add', 'existing.py')
            invoke('-c', 'user.name=Fixture', '-c',
                   'user.email=fixture@example.invalid', 'commit', '-qm', 'base')
            base = invoke('rev-parse', 'HEAD')
            branch = 'feat/llmtool-c1-test-excerpts-20261009'
            invoke('switch', '-q', '-c', branch)
            logs = root / '.ai-loop' / '20261008T185112Z-21061'
            logs.mkdir(parents=True)
            loop.save_json(logs / 'codex-0.json',
                {'status': 'NEEDS_DECISION', 'summary': 'PRIVATE_PROVIDER_DATA'})
            loop.save_json(logs / 'provenance.json', {
                'root': str(root), 'run_id': logs.name, 'base': base,
                'start_head': base, 'start_branch': 'main'})
            task = {'mode': 'implement_new_pr', 'base_sha': base,
                    'branch': branch, 'allowed_files': ['existing.py', 'brand_new.py']}
            first = loop.diagnostic_codex_outcome(root, logs, task)
            self.assertEqual(first['changed_allowed_files'], [])
            self.assertFalse(first['repair_authorized'])
            self.assertNotIn('PRIVATE_PROVIDER_DATA', json.dumps(first))
            (root / 'brand_new.py').write_text('new C1 code')
            later = loop.diagnostic_codex_outcome(root, logs, task)
            self.assertEqual(later['changed_allowed_files'], ['brand_new.py'])
            (root / 'existing.py').write_text('changed source')
            changed = loop.diagnostic_codex_outcome(root, logs, task)
            self.assertEqual(changed['changed_allowed_files'],
                             ['brand_new.py', 'existing.py'])
            task['base_sha'] = 'f' * 40
            with self.assertRaises(loop.Stop):
                loop.diagnostic_codex_outcome(root, logs, task)

    def test_new_task_codex_diagnostic_never_infers_base_without_pinned_proof(self):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            logs = root / '.ai-loop' / '20261008T185112Z-21061'
            logs.mkdir(parents=True)
            loop.save_json(logs / 'codex-0.json', {
                'status': 'NEEDS_DECISION', 'summary': 'PRIVATE_AUTH'})
            task = {'mode': 'implement_new_pr', 'base_sha': 'b' * 40,
                    'branch': 'feat/example', 'allowed_files': ['missing.py']}
            with self.assertRaisesRegex(loop.Stop, 'provenance missing'):
                loop.diagnostic_codex_outcome(root, logs, task)
            task['mode'] = 'review_existing_pr'
            with self.assertRaisesRegex(loop.Stop, 'provenance missing'):
                loop.diagnostic_codex_outcome(root, logs, task)

    def test_private_codex_decision_only_reveals_verified_local_summary(self):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            logs = root / '.ai-loop' / '20261009T010203Z-123'
            logs.mkdir(parents=True)
            response = {'status': 'NEEDS_DECISION',
                        'summary': 'human approval needed; PRIVATE_LOCAL_TEXT'}
            loop.save_json(logs / 'codex-0.json', response)
            expected = loop.digest(logs / 'codex-0.json')
            metadata = {'developer_status': 'NEEDS_DECISION',
                'result_sha256': expected, 'changed_allowed_files': [],
                'repair_files_changed': [], 'repair_authorized': False,
                'test_gates_reexecuted': False}
            with patch.object(loop, 'diagnostic_codex_outcome', return_value=metadata) as inspect:
                got = loop.private_codex_decision(root, logs, {'allowed_files': []})
                self.assertEqual(got['summary'], response['summary'])
                self.assertEqual(got['sha256'], expected)
                self.assertFalse(got['repair_authorized'])
                self.assertFalse(got['review_pass'])
                self.assertTrue(got['decision_only'])
                inspect.assert_called_once()

    def test_private_codex_decision_fails_closed_on_mismatch_or_false_authority(self):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            logs = root / '.ai-loop' / '20261009T010203Z-123'
            logs.mkdir(parents=True)
            response = {'status': 'NEEDS_DECISION', 'summary': 'private'}
            loop.save_json(logs / 'codex-0.json', response)
            expected = loop.digest(logs / 'codex-0.json')
            base = {'developer_status': 'NEEDS_DECISION',
                'result_sha256': expected, 'changed_allowed_files': [],
                'repair_files_changed': [], 'repair_authorized': False,
                'test_gates_reexecuted': False}
            for field, changed in (
                ('developer_status', 'PASS'),
                ('changed_allowed_files', ['secret.txt']),
                ('repair_files_changed', ['test.py']),
                ('repair_authorized', True),
                ('test_gates_reexecuted', True)):
                with self.subTest(field=field):
                    invalid = {**base, field: changed}
                    with patch.object(loop, 'diagnostic_codex_outcome', return_value=invalid):
                        with self.assertRaises(loop.Stop):
                            loop.private_codex_decision(root, logs, {})
            with patch.object(loop, 'diagnostic_codex_outcome',
                              return_value={**base, 'result_sha256': '0'*64}):
                with self.assertRaisesRegex(loop.Stop, 'changed during inspection'):
                    loop.private_codex_decision(root, logs, {})
            (logs / 'codex-0.json').write_text('{"status":"NEEDS_DECISION","summary":"tampered","extra":"token"}')
            with patch.object(loop, 'diagnostic_codex_outcome',
                              return_value={**base, 'result_sha256': loop.digest(logs / 'codex-0.json')}):
                with self.assertRaises(loop.Stop):
                    loop.private_codex_decision(root, logs, {})
            (logs / 'codex-0.json').unlink()
            (logs / 'codex-0.json').symlink_to(logs / 'other-secret')
            with patch.object(loop, 'diagnostic_codex_outcome', return_value=base):
                with self.assertRaisesRegex(loop.Stop, 'path unsafe'):
                    loop.private_codex_decision(root, logs, {})

    def test_original_c1_private_decision_requires_exact_published_sha(self):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            logs = root / '.ai-loop' / '20261008T185112Z-21061'
            logs.mkdir(parents=True)
            loop.save_json(logs / 'codex-0.json',
                {'status': 'NEEDS_DECISION', 'summary': 'cannot be substituted'})
            metadata = {'developer_status': 'NEEDS_DECISION',
                'result_sha256': loop.digest(logs / 'codex-0.json'),
                'changed_allowed_files': [], 'repair_files_changed': [],
                'repair_authorized': False, 'test_gates_reexecuted': False}
            with patch.object(loop, 'diagnostic_codex_outcome', return_value=metadata):
                with self.assertRaisesRegex(loop.Stop, 'no longer matches Git diagnostic'):
                    loop.private_codex_decision(root, logs, {})

    def test_codex_recovery_disposition_is_specific_and_fail_closed(self):
        task = {'acceptance_report': 'reports/probe.md'}
        recovery = {'kind': 'acceptance_failure', 'failure_scenario': 'kqueue_non_reaping',
                    'repair_files': ['tools/llmtool/tests/test_git_containment.py']}
        test = recovery['repair_files'][0]
        loop.ensure_review_repair_scope(task, recovery, 0, [test], 'PASS')
        for status, paths, marker in [
                ('PASS', [], 'did not change'),
                ('NEEDS_DECISION', [], 'requested a decision'),
                ('FAIL', [], 'no authorized test change'),
                ('PASS', ['tools/llmtool/llmtool/_git_process_identity.py'], 'outside')]:
            with self.subTest(status=status, paths=paths):
                with self.assertRaises(loop.Stop) as err:
                    loop.ensure_review_repair_scope(task, recovery, 0, paths, status)
                self.assertIn(marker, str(err.exception))

    def test_diagnostic_triage_is_non_authoritative_and_rejects_unsafe_run(self):
        with tempfile.TemporaryDirectory() as runtime:
            root = Path(runtime)
            logs = root/'.ai-loop'/'not-a-run'
            logs.mkdir(parents=True)
            with self.assertRaises(loop.Stop):
                loop.diagnostic_triage(root, logs, {'allowed_files': []})

    def test_export_preserves_existing_zip_and_rejects_symlink_folder(self):
        for fault in ('existing', 'symlink'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as runtime:
                root = Path(runtime); logs = root/'.ai-loop/run'; logs.mkdir(parents=True)
                target = root/'ai-loop-diagnostics'
                if fault == 'symlink': target.symlink_to(logs, target_is_directory=True)
                else:
                    target.mkdir(); (target/'run.zip').write_bytes(b'preserved')
                with self.assertRaises(loop.Stop): loop.export_diagnostics(root, logs, {'allowed_files': []})
                if fault == 'existing': self.assertEqual((target/'run.zip').read_bytes(), b'preserved')


class DiagnosticPublicationTests(unittest.TestCase):
    run_id = '20261007T162747Z-96025'

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as runtime, ExitStack() as stack:
            root = Path(runtime); logs = root/'.ai-loop'/self.run_id
            logs.mkdir(parents=True); (root/'reports').mkdir()
            (root/'source.py').write_text('UNPUBLISHED_SOURCE_SENTINEL\n')
            (root/'reports/validation.md').write_text('# Native validation\nFAIL; pending repair.\n')
            task = {'task_id': 'fixture', 'branch': 'feat/fixture', 'base_sha': HEAD,
                    'allowed_files': ['source.py', 'reports/validation.md'],
                    'acceptance_report': 'reports/validation.md', 'mac_evidence': 'mac-containment'}
            loop.save_json(logs/'task.json', task)
            loop.save_json(logs/'provenance.json', {'root': str(root), 'run_id': self.run_id,
                                                  'control_sha': HEAD, 'base': HEAD})
            loop.save_json(logs/'summary.json', {'status': 'STOPPED', 'head': HEAD, 'branch': task['branch'],
                'reason': 'SENSITIVE_REASON_SENTINEL', 'history': [{'head': HEAD, 'round': 0,
                    'developer': {'status': 'PASS', 'summary': 'PRIVATE_DEVELOPER_SENTINEL'},
                    'review': {'status': 'PASS', 'reviewed_head': HEAD,
                               'findings': ['PRIVATE_REVIEW_SENTINEL']},
                    'mandatory': {'status': 'FAIL'}}]})
            loop.save_json(logs/'mac-containment-0.json', {'schema_version': 1, 'platform': 'darwin',
                'failure': {'scenario': 'hung_supervisor', 'status': 'FAIL'},
                'scenarios': {'sigint': {'status': 'PASS'}}})
            (logs/'tests-0-mandatory-final-0.stdout.log').write_text('[Native] hung_supervisor FAIL\n')
            (logs/'tests-0-mandatory-final-0.stderr.log').write_text('')
            (logs/'codex-0.stderr.log').write_text('PRIVATE_PROVIDER_SENTINEL')
            (logs/'claude-0.stdout.log').write_text('PRIVATE_PROVIDER_SENTINEL')
            loop.export_diagnostics(root, logs, task)
            stack.enter_context(patch.object(loop, 'load_task', return_value=(task, 'pinned task')))
            stack.enter_context(patch.object(loop.shutil, 'which', return_value='/fake/gh'))
            yield root, logs, task

    def fake_api(self, root, logs, existing=False, conflict=False, race=False, verify_bad=False, files=None, batch=None):
        selected = loop.diagnostic_payload(root, logs) if files is None else files
        prefix = 'reports/ai-loop/diagnostics/'+logs.name+'/' + (batch+'/' if batch else '')
        desired = {prefix+name: (hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
                                  '100644', 'blob') for name, raw in selected.items()}
        old_rows = {'reports/older.md': ('d'*40, '100644', 'blob')}
        if existing:
            old_rows.update(desired)
            if conflict: old_rows[prefix+'summary.json'] = ('e'*40, '100644', 'blob')
        state = {'head': 'a'*40 if existing else None, 'ref_reads': 0,
                 'rows': old_rows if existing else {}, 'new_rows': {}, 'calls': []}
        def tree_reply(rows):
            return {'truncated': False, 'tree': [{'path': p, 'sha': row[0], 'mode': row[1],
                                                 'type': row[2]} for p, row in rows.items()]}
        def api(cwd, directory, method, endpoint, payload=None, allow_missing=False):
            self.assertEqual((cwd, directory), (root, logs))
            state['calls'].append((method, endpoint, payload))
            if method == 'GET' and '/ref/heads/' in endpoint:
                state['ref_reads'] += 1
                if race and state['ref_reads'] == 2: return {'object': {'sha': 'f'*40}}
                return {'object': {'sha': state['head']}} if state['head'] else None
            if method == 'GET' and '/commits/' in endpoint:
                return {'tree': {'sha': 'b'*40 if endpoint.endswith('a'*40) else '9'*40}}
            if method == 'GET' and '/trees/' in endpoint:
                rows = state['rows'] if 'b'*40 in endpoint else state['new_rows']
                if verify_bad and state['head'] == 'c'*40:
                    rows = {**rows, 'unrelated.py': ('e'*40, '100644', 'blob')}
                return tree_reply(rows)
            if method == 'POST' and endpoint.endswith('/trees'):
                state['new_rows'] = dict(state['rows'])
                for row in payload['tree']:
                    raw = row['content'].encode('utf-8')
                    state['new_rows'][row['path']] = (
                        hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(), row['mode'], row['type'])
                return {'sha': '9'*40}
            if method == 'POST' and endpoint.endswith('/commits'): return {'sha': 'c'*40}
            if method in ('PATCH', 'POST') and '/refs' in endpoint:
                if method == 'PATCH': self.assertIs(payload['force'], False)
                else: self.assertEqual(payload['ref'], 'refs/heads/'+loop.DIAGNOSTICS_BRANCH)
                state['head'] = payload['sha']; return {'object': {'sha': state['head']}}
            raise AssertionError((method, endpoint))
        return state, api

    def test_custom_round_report_is_immutable_and_identical_repeat_is_read_only(self):
        with self.fixture() as (root, logs, task):
            files = {'index.md': b'# Failure\n', 'failure.json': b'{"status":"FAIL"}\n'}
            state, api = self.fake_api(root, logs, files=files, batch='round-0-failure')
            with patch.object(loop, 'diagnostic_api', side_effect=api):
                loop.publish_diagnostics(root, logs, files=files, batch='round-0-failure')
                state['calls'].clear()
                loop.publish_diagnostics(root, logs, files=files, batch='round-0-failure')
            self.assertTrue(all(c[0] == 'GET' for c in state['calls']))
            self.assertTrue((logs/'published-round-0-failure.json').is_file())

    def test_terminal_zip_publication_preserves_prior_round_reports(self):
        with self.fixture() as (root, logs, task):
            state, api = self.fake_api(root, logs, existing=True)
            prefix = 'reports/ai-loop/diagnostics/'+logs.name+'/'
            state['rows'] = {'reports/older.md': ('d'*40, '100644', 'blob'),
                prefix+'round-0-review/review.json': ('e'*40, '100644', 'blob')}
            with patch.object(loop, 'diagnostic_api', side_effect=api):
                loop.publish_diagnostics(root, logs)
            self.assertIn(prefix+'round-0-review/review.json', state['new_rows'])
            self.assertIn(prefix+'index.md', state['new_rows'])

    def test_bad_batch_arbitrary_file_and_size_fail_before_remote_write(self):
        with self.fixture() as (root, logs, task), patch.object(loop, 'diagnostic_api') as api:
            for files, batch in [({'index.md': b'index', 'failure.json': b'{}'}, '../failure'),
                ({'index.md': b'index', 'source.py': b'private source'}, 'round-0-failure'),
                ({'index.md': b'index', 'failure.json': b'x'*(128*1024+1)}, 'round-0-failure')]:
                with self.subTest(batch=batch), self.assertRaises(loop.Stop):
                    loop.publish_diagnostics(root, logs, files=files, batch=batch)
            api.assert_not_called()

    def test_payload_is_frozen_and_excludes_sources_provider_streams_and_free_summaries(self):
        with self.fixture() as (root, logs, task):
            (root/'source.py').write_text('later source changes\n')
            (root/'reports/validation.md').write_text('later report changes\n')
            payload = loop.diagnostic_payload(root, logs)
            whole = b''.join(payload.values())
            for sentinel in (b'UNPUBLISHED_SOURCE_SENTINEL', b'PRIVATE_PROVIDER_SENTINEL',
                             b'SENSITIVE_REASON_SENTINEL', b'PRIVATE_DEVELOPER_SENTINEL',
                             b'PRIVATE_REVIEW_SENTINEL', b'later report changes'):
                self.assertNotIn(sentinel, whole)
            self.assertIn(b'hung_supervisor', whole)
            self.assertIn(b'pending repair', payload['validation.md'])
            self.assertIn('tests-0-mandatory-final-0.stdout.log', payload)
            self.assertFalse(json.loads(payload['source-hashes.json'])['source_files_published'])

    def test_archive_hash_drift_symlink_missing_and_wrong_contract_stop_before_api(self):
        for fault in ('drift', 'symlink', 'missing', 'contract'):
            with self.subTest(fault=fault), self.fixture() as (root, logs, task), \
                    patch.object(loop, 'diagnostic_api') as api:
                archive = root/'ai-loop-diagnostics'/(logs.name+'.zip')
                if fault == 'missing': archive.unlink()
                elif fault == 'symlink':
                    target = root/'old.zip'; archive.rename(target); archive.symlink_to(target)
                elif fault == 'contract': task['branch'] = 'feat/changed'
                else:
                    with zipfile.ZipFile(archive) as source:
                        entries = {name: source.read(name) for name in source.namelist()}
                    entries['source.py'] = b'tampered'
                    with zipfile.ZipFile(archive, 'w') as output:
                        for name, raw in entries.items(): output.writestr(name, raw)
                with self.assertRaises(loop.Stop): loop.publish_diagnostics(root, logs)
                api.assert_not_called()

    def test_new_reports_only_branch_has_no_source_parents_or_local_git_writes(self):
        with self.fixture() as (root, logs, task):
            state, api = self.fake_api(root, logs)
            with patch.object(loop, 'diagnostic_api', side_effect=api), patch.object(loop, 'git') as git:
                url = loop.publish_diagnostics(root, logs)
            self.assertIn('/blob/'+('c'*40)+'/', url)
            self.assertTrue(url.endswith('/index.md'))
            commit = next(p for m, e, p in state['calls'] if m == 'POST' and e.endswith('/commits'))
            self.assertEqual(commit['parents'], [])
            self.assertFalse(any('refs/heads/main' in e or 'feat/fixture' in e for m, e, p in state['calls']))
            git.assert_not_called()
            self.assertEqual((root/'source.py').read_text(), 'UNPUBLISHED_SOURCE_SENTINEL\n')

    def test_append_preserves_prior_records_and_nonforce_publication(self):
        with self.fixture() as (root, logs, task):
            state, api = self.fake_api(root, logs)
            state['head'] = 'a'*40; state['rows'] = {'reports/older.md': ('d'*40, '100644', 'blob')}
            with patch.object(loop, 'diagnostic_api', side_effect=api): loop.publish_diagnostics(root, logs)
            self.assertEqual(state['new_rows']['reports/older.md'], ('d'*40, '100644', 'blob'))
            self.assertTrue(any(m == 'PATCH' and p['force'] is False for m, e, p in state['calls']))

    def test_identical_run_is_idempotent_conflict_never_overwrites(self):
        for conflict in (False, True):
            with self.subTest(conflict=conflict), self.fixture() as (root, logs, task):
                state, api = self.fake_api(root, logs, existing=True, conflict=conflict)
                with patch.object(loop, 'diagnostic_api', side_effect=api):
                    if conflict:
                        with self.assertRaisesRegex(loop.Stop, 'never overwrite'): loop.publish_diagnostics(root, logs)
                    else: loop.publish_diagnostics(root, logs)
                self.assertFalse(any(m != 'GET' for m, e, p in state['calls']))

    def test_concurrent_branch_change_and_bad_remote_verification_stop(self):
        for fault in ('race', 'verification'):
            with self.subTest(fault=fault), self.fixture() as (root, logs, task):
                state, api = self.fake_api(root, logs, race=fault=='race', verify_bad=fault=='verification')
                with patch.object(loop, 'diagnostic_api', side_effect=api), self.assertRaises(loop.Stop):
                    loop.publish_diagnostics(root, logs)
                if fault == 'race': self.assertFalse(any('/refs' in e for m, e, p in state['calls']))
                self.assertFalse((logs/'published-diagnostics.json').exists())

    def test_receipt_symlink_does_not_mutate_remote_or_target(self):
        with self.fixture() as (root, logs, task), patch.object(loop, 'diagnostic_api') as api:
            target = root/'user.txt'; target.write_text('preserved')
            (logs/'published-diagnostics.json').symlink_to(target)
            with self.assertRaises(loop.Stop): loop.publish_diagnostics(root, logs)
            api.assert_not_called(); self.assertEqual(target.read_text(), 'preserved')

    def test_missing_ref_is_404_only_and_structured_request_is_cleaned_up(self):
        with self.fixture() as (root, logs, task):
            for code in (404, 403):
                with patch.object(loop, 'command', return_value=(1, json.dumps({'message': 'Not Found', 'status': code}), '')):
                    if code == 404:
                        self.assertIsNone(loop.diagnostic_api(root, logs, 'GET', 'fixed', allow_missing=True))
                    else:
                        with self.assertRaises(loop.Stop): loop.diagnostic_api(root, logs, 'GET', 'fixed', allow_missing=True)
            payload = {'content': 'literal $() `command`\nsecond line'}
            def execute(argv, cwd, **kwargs):
                self.assertEqual(json.loads(Path(argv[-1]).read_text()), payload)
                self.assertNotIn(payload['content'], argv)
                return 0, '{}', ''
            with patch.object(loop, 'command', side_effect=execute):
                loop.diagnostic_api(root, logs, 'POST', 'fixed', payload)
            self.assertFalse(list(logs.glob('diagnostic-request-*')))

    def test_upload_failure_does_not_change_original_stopped_summary_or_zip(self):
        with self.fixture() as (root, logs, task):
            before = (logs/'summary.json').read_bytes()
            with patch.object(loop, 'export_diagnostics', return_value=root/'diagnostics.zip'), \
                    patch.object(loop, 'publish_diagnostics', side_effect=loop.Stop('auth unavailable')):
                loop.finish_diagnostics(root, logs, task)
            self.assertEqual((logs/'summary.json').read_bytes(), before)


class DiagnosticCLITests(unittest.TestCase):
    def test_upload_only_bypasses_task_models_tests_probe_recovery_and_dependencies(self):
        with tempfile.TemporaryDirectory() as runtime, ExitStack() as stack:
            root = Path(runtime); (root/'.git').mkdir()
            stack.enter_context(patch.object(loop, 'REPO', root))
            stack.enter_context(patch.object(Path, 'cwd', return_value=root))
            def git(cwd, *args):
                if args == ('rev-parse', '--show-toplevel'): return str(root)
                if args == ('rev-parse', '--absolute-git-dir'): return str(root/'.git')
                if args == ('remote', 'get-url', 'origin'): return 'git@github.com:'+loop.IDENTITY+'.git'
                raise AssertionError(args)
            stack.enter_context(patch.object(loop, 'git', side_effect=git))
            task = stack.enter_context(patch.object(loop, 'load_task'))
            runner = stack.enter_context(patch.object(loop, 'run'))
            publisher = stack.enter_context(patch.object(loop, 'publish_diagnostics'))
            stack.enter_context(patch.object(sys, 'argv', ['runner', '--control-sha', HEAD,
                '--publish-diagnostics', '20261007T162747Z-96025']))
            loop.main()
            task.assert_not_called(); runner.assert_not_called()
            publisher.assert_called_once_with(root, root/'.ai-loop/20261007T162747Z-96025')
            self.assertFalse(list((root/'.git/ai-loop').glob('resume-*')))

    def test_upload_cannot_be_combined_with_resume_check_install_or_test_flags(self):
        for flags in (['--resume', 'other'], ['--check'], ['--install-launcher'], ['--related'], ['--full']):
            with self.subTest(flags=flags), patch.object(sys, 'argv', ['runner', '--control-sha', HEAD,
                    '--publish-diagnostics', '20261007T162747Z-96025', *flags]), self.assertRaises(SystemExit):
                loop.main()


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

    @contextmanager
    def timeout_fixture(self):
        with self.fixture() as (root, old, task, inventory, runner):
            baseline = json.loads((old / 'task.json').read_text())
            baseline.update(timeout_seconds=1800, initial_quota_recovery={
                'run_id': '20261007T064116Z-68730', 'control_sha': 'c' * 40})
            loop.save_json(old / 'task.json', baseline)
            task.clear()
            task.update(copy.deepcopy(baseline))
            task.pop('initial_quota_recovery')
            task.update(initial_timeout_recovery={'run_id': self.run_id, 'control_sha': 'b' * 40},
                        codex_timeout_seconds=3600)
            summary = json.loads((old / 'summary.json').read_text())
            summary['reason'] = 'command timed out: codex'
            loop.save_json(old / 'summary.json', summary)
            loop.save_json(old / 'recovery.json', {'preserved_untracked': {'user.txt': inventory['user.txt']}})
            (old / 'codex-0.stderr.log').write_text('partial implementation patch output\n')
            with patch.object(loop, 'load_task', return_value=(baseline, 'pinned requirements')), \
                 patch.object(loop, 'command', return_value=(1, '', '')) as command:
                yield root, old, task, inventory, runner, command

    def test_timeout_recovery_accepts_prior_quota_policy_but_inherits_no_pass(self):
        with self.timeout_fixture() as (root, old, task, inventory, runner, command):
            original, recovery = loop.prepare_initial_resume(root, task, self.run_id, inventory)
            self.assertEqual(original, {'user.txt': inventory['user.txt']})
            self.assertEqual(recovery['inherited_validation'], 'NONE')
            self.assertEqual(command.call_args.args[0][:3], ['/usr/bin/pgrep', '-f', r'(^|/)codex([ .-]|$)'])

    def test_timeout_recovery_rejects_unchanged_or_reduced_timeout(self):
        for seconds in (1800, 900, 10801):
            with self.subTest(seconds=seconds), self.timeout_fixture() as (root, old, task, inventory, runner, command):
                task['codex_timeout_seconds'] = seconds
                with self.assertRaises(loop.Stop):
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)
                command.assert_not_called()

    def test_timeout_recovery_cannot_alter_full_tests_or_scope(self):
        for key, value in (('allowed_files', ['different.py']), ('timeout_seconds', 3600)):
            with self.subTest(key=key), self.timeout_fixture() as (root, old, task, inventory, runner, command):
                task[key] = value
                with self.assertRaises(loop.Stop):
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)

    def test_timeout_recovery_requires_unchanged_protected_files_and_no_new_outside_scope(self):
        for kind in ('modified', 'missing', 'extra', 'inventory_missing'):
            with self.subTest(kind=kind), self.timeout_fixture() as (root, old, task, inventory, runner, command):
                if kind == 'modified':
                    inventory['user.txt'] = 'changed'
                elif kind == 'missing':
                    inventory.pop('user.txt')
                elif kind == 'extra':
                    inventory['new-unrelated.txt'] = 'new'
                else:
                    (old / 'recovery.json').unlink()
                with self.assertRaises(loop.Stop):
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)
                command.assert_not_called()

    def test_timeout_recovery_rejects_active_codex_or_process_detection_failure(self):
        for rc in (0, 2):
            with self.subTest(rc=rc), self.timeout_fixture() as (root, old, task, inventory, runner, command):
                command.return_value = (rc, 'active process' if rc == 0 else '', '')
                with self.assertRaises(loop.Stop):
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)

    def test_timeout_policy_cannot_resume_a_quota_or_decision_stop(self):
        for reason in ('command failed (exit 1): codex; see local logs', 'Codex reported NEEDS_DECISION'):
            with self.subTest(reason=reason), self.timeout_fixture() as (root, old, task, inventory, runner, command):
                summary = json.loads((old / 'summary.json').read_text())
                summary['reason'] = reason
                loop.save_json(old / 'summary.json', summary)
                with self.assertRaises(loop.Stop):
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)

    def test_allowed_tracked_edit_is_adopted_and_snapshotted(self):
        with self.fixture() as (root, old, task, inventory, runner):
            task['allowed_files'].append('tracked.py')
            baseline = {k: v for k, v in task.items() if k != 'initial_quota_recovery'}
            loop.save_json(old / 'task.json', baseline)
            (root / 'tracked.py').write_text('allowed tracked change')
            with patch.object(loop, 'load_task', return_value=(baseline, 'pinned requirements')):
                _, recovery = loop.prepare_initial_resume(root, task, self.run_id, inventory, ['tracked.py'])
            logs = root / '.ai-loop/new-run'
            logs.mkdir()
            loop.snapshot_recovery(root, logs, recovery)
            self.assertEqual((logs / 'recovery-inputs/tracked.py').read_text(), 'allowed tracked change')

    def test_unrelated_tracked_edit_is_rejected_before_remote_checks(self):
        with self.fixture() as (root, old, task, inventory, runner):
            with self.assertRaises(loop.Stop):
                loop.prepare_initial_resume(root, task, self.run_id, inventory, ['user.txt'])
            runner.assert_not_called()

    def test_partial_files_adopted_unrelated_preserved_and_backed_up(self):
        with self.fixture() as (root, old, task, inventory, runner):
            original, recovery = loop.prepare_initial_resume(root, task, self.run_id, inventory)
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
                loop.prepare_initial_resume(root, task, 'other-run', inventory)
            runner.assert_not_called()

    def test_published_branch_or_existing_pr_is_rejected(self):
        for responses in (['[{"number":47}]'], ['[]', HEAD]):
            with self.subTest(responses=responses), self.fixture() as (root, old, task, inventory, runner):
                runner.side_effect = responses
                with self.assertRaises(loop.Stop):
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)

    def test_branch_or_head_drift_is_rejected(self):
        for responses in (['feat/unrelated'], [ 'feat/fixture', 'c' * 40]):
            with self.subTest(responses=responses), self.fixture() as (root, old, task, inventory, runner), \
                 patch.object(loop, 'git', side_effect=responses):
                with self.assertRaises(loop.Stop):
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)
                runner.assert_not_called()

    def test_changed_contract_is_rejected(self):
        with self.fixture() as (root, old, task, inventory, runner):
            task['max_repairs'] = 0
            with self.assertRaises(loop.Stop):
                loop.prepare_initial_resume(root, task, self.run_id, inventory)

    def test_only_initial_quota_stop_can_resume(self):
        for change in ({'history': [{'round': 0}]}, {'reason': 'Codex reported NEEDS_DECISION'},
                       {'head': 'c' * 40}, {'status': 'READY_FOR_FINAL_REVIEW'},
                       {'decision_checkpoint': {'url': 'published'}}):
            with self.subTest(change=change), self.fixture() as (root, old, task, inventory, runner):
                summary = json.loads((old / 'summary.json').read_text())
                summary.update(change)
                loop.save_json(old / 'summary.json', summary)
                with self.assertRaises(loop.Stop):
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)

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
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)

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
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)

    def test_snapshot_detects_candidate_drift_without_overwriting_user_file(self):
        with self.fixture() as (root, old, task, inventory, runner):
            _, recovery = loop.prepare_initial_resume(root, task, self.run_id, inventory)
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
                    loop.prepare_initial_resume(root, task, self.run_id, inventory)

    def test_resume_authorization_is_one_use_even_after_another_stop(self):
        with tempfile.TemporaryDirectory() as runtime:
            state = Path(runtime)
            recovery = {'from_run': self.run_id}
            loop.claim_initial_resume(state, recovery, 'new-run', HEAD)
            marker = state / ('resume-' + self.run_id + '.json')
            retained = marker.read_bytes()
            with self.assertRaises(loop.Stop):
                loop.claim_initial_resume(state, recovery, 'second-run', HEAD)
            self.assertEqual(marker.read_bytes(), retained)

    def test_resume_claim_rejects_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as runtime:
            state = Path(runtime)
            target = state / 'user-file'
            target.write_text('preserved')
            (state / ('resume-' + self.run_id + '.json')).symlink_to(target)
            with self.assertRaises(loop.Stop):
                loop.claim_initial_resume(state, {'from_run': self.run_id}, 'new-run', HEAD)
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
        metadata = historical_task()
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


class ProgressTests(unittest.TestCase):
    def test_progress_shows_phase_elapsed_limit_and_observed_activity(self):
        line = loop.progress_line('[Claude／獨立審查] 檢查程式差異與測試證據', 260, 10800, False)
        self.assertIn('00:04:20', line)
        self.assertIn('03:00:00', line)
        self.assertIn('暫無新增日誌；等待結果', line)
        self.assertIn('日誌有更新', loop.progress_line('[Codex／實作修正]', 20, 10800, True))
        self.assertIn('等待命令完成', loop.progress_line('[執行]', 20, 120))
        self.assertNotIn(' ...', line)

    def test_heartbeat_observes_log_growth_without_exposing_log_contents(self):
        with tempfile.TemporaryDirectory() as runtime:
            path = Path(runtime) / 'provider.log'
            proc = Mock(returncode=0, stdin=None, stdout=None, stderr=None)
            proc.poll.return_value = 0
            def communicate(*args, **kwargs):
                if not path.stat().st_size:
                    path.write_text('PRIVATE_PROMPT_AND_PROVIDER_OUTPUT')
                    raise subprocess.TimeoutExpired('codex', 20)
                return None, None
            proc.communicate.side_effect = communicate
            with patch.object(loop.subprocess, 'Popen', return_value=proc), \
                 patch.object(loop.time, 'monotonic', side_effect=[0, 0, 20, 20]), \
                 patch('builtins.print') as printer:
                self.assertEqual(loop.command(['codex'], Path(runtime), timeout=10800,
                    stderr_path=path, progress='[Codex／實作修正] 等待候選程式'), (0, '', ''))
            console = ' '.join(str(call.args) for call in printer.call_args_list)
            self.assertIn('00:00:20', console)
            self.assertIn('日誌有更新', console)
            self.assertNotIn('PRIVATE_PROMPT', console)

    def test_test_purpose_labels_related_full_and_mac_gates(self):
        self.assertIn('語法', loop.test_purpose(['make', 'syntax'], 'related'))
        self.assertIn('前置檢查', loop.test_purpose(['test_git_mutation.py'], 'related'))
        self.assertIn('Mac 系統 Python', loop.test_purpose(['make', 'check', 'PYTHON=/usr/bin/python3'], 'full-final'))
        self.assertIn('venv Python', loop.test_purpose(['make', 'check', 'PYTHON=.venv/bin/python'], 'full-final'))
        self.assertIn('parent SIGKILL', loop.test_purpose(['acceptance'], 'mandatory-final'))

    def test_model_timeout_schema_accepts_three_hours_and_rejects_unbounded_values(self):
        metadata = historical_task()
        def load(value):
            with patch.object(loop, 'git', side_effect=[json.dumps(value), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        metadata.update(codex_timeout_seconds=10800, claude_timeout_seconds=10800)
        self.assertEqual(load(metadata)['codex_timeout_seconds'], 10800)
        self.assertEqual(load(metadata)['claude_timeout_seconds'], 10800)
        self.assertEqual(load(metadata)['timeout_seconds'], 1800)
        for key in ('codex_timeout_seconds', 'claude_timeout_seconds'):
            for value in (True, 0, 10801, '10800'):
                with self.subTest(key=key, value=value), self.assertRaises(loop.Stop):
                    invalid = copy.deepcopy(metadata)
                    invalid[key] = value
                    load(invalid)


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

    def test_extended_codex_limit_does_not_extend_claude_or_remove_final_gates(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, codex_timeout=3600),
                         ['related', 'full-final', 'mandatory-final'])

    def test_both_ai_calls_allow_three_hours_while_all_test_gates_remain(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True,
            codex_timeout=10800, claude_timeout=10800), ['related', 'full-final', 'mandatory-final'])

    def test_native_resume_repairs_existing_candidate_then_runs_all_gates(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, native_resume=True),
                         ['related', 'full-final', 'mandatory-final'])

    def test_hung_resume_prompt_and_all_gates_use_the_explicit_failure(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, native_resume=True,
                         native_scenario='hung_supervisor'),
                         ['related', 'full-final', 'mandatory-final'])

    def test_native_resume_never_retries_failed_mac_gate_automatically(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, native_resume=True,
                         mandatory_failure=True, should_stop=True),
                         ['related', 'full-final', 'mandatory-final'])

    def test_native_resume_has_at_most_two_repair_calls(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, native_resume=True,
                         failures=2, should_stop=True), ['related', 'related'])

    def test_native_resume_cannot_rerun_probes_on_unchanged_bytes(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, native_resume=True,
                         unchanged_repair=True, should_stop=True), [])

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

    def test_auto_related_failure_gets_claude_guidance_then_codex(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, native_resume=True,
            auto=True, related_failures=1), ['related', 'related', 'full-final', 'mandatory-final'])

    def test_auto_full_failure_gets_claude_guidance_then_codex(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, native_resume=True,
            auto=True, final_full_failures=1), ['related', 'full-final', 'related', 'full-final', 'mandatory-final'])

    def test_auto_mac_bug_is_repaired_then_all_gates_rerun(self):
        self.assertEqual(self.exercise_pipeline(new_task=True, resume=True, native_resume=True,
            auto=True, mac_failures=1), ['related', 'full-final', 'mandatory-final', 'related', 'full-final', 'mandatory-final'])

    def test_repeated_mac_failure_stops_with_reports_and_no_feature_push(self):
        self.exercise_pipeline(new_task=True, resume=True, native_resume=True,
            auto=True, mandatory_failure=True, should_stop=True)

    def test_auto_review_blocker_goes_through_failure_coordination(self):
        self.exercise_pipeline(new_task=True, resume=True, native_resume=True, auto=True, failures=1)

    def test_auto_exact_head_ci_bug_repairs_same_draft_pr(self):
        self.exercise_pipeline(new_task=True, resume=True, native_resume=True, auto=True, ci_failures=1)

    def exercise_pipeline(self, failures=0, should_stop=False, test_mode='related', final_full_failures=0,
                          new_task=False, mandatory_failure=False, hook_changes=False,
                          decision=False, checkpoint_failure=False, resume=False, codex_timeout=None,
                          claude_timeout=None, native_resume=False, unchanged_repair=False,
                          native_scenario=None, auto=False, related_failures=0, mac_failures=0, ci_failures=0):
        with tempfile.TemporaryDirectory() as runtime, ExitStack() as stack:
            root = Path(runtime)
            gitdir = root / 'git-metadata-fixture'
            (gitdir / 'info').mkdir(parents=True)
            (root / 'tools/llmtool/.venv/bin').mkdir(parents=True)
            (root / 'tools/llmtool/.venv/bin/python').touch()
            (root / 'source.txt').write_text('unchanged existing implementation')
            task = self.task()
            if codex_timeout is not None:
                task['codex_timeout_seconds'] = codex_timeout
            if claude_timeout is not None:
                task['claude_timeout_seconds'] = claude_timeout
            if new_task:
                task.update(mode='implement_new_pr', pr_number=None, expected_head=task['base_sha'],
                    require_local_full=True, mandatory_tests=[['fixture mandatory']],
                    required_files=['source.txt', 'reports/validation.md'], mac_evidence='mac-containment',
                    acceptance_report='reports/validation.md', pr_title='fixture title')
                task['publish_decision_checkpoint'] = True
                task['allowed_files'].append('reports/validation.md')
            if native_resume:
                task['acceptance_failure_recovery'] = {'run_id': '20261007T075141Z-70725'}
                if native_scenario:
                    task['acceptance_failure_recovery']['failure_scenario'] = native_scenario
            if auto:
                task['failure_triage'] = {'max_reviews': 4, 'repeat_limit': 2, 'allow_mac_repair': True}
                task['max_repairs'] = 4
            current_head = [HEAD]
            dirty = [False]
            review_calls = [0]
            final_full_calls = [0]
            related_calls = [0]
            mac_calls = [0]
            developer_calls = [0]
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
                if suite == 'related':
                    related_calls[0] += 1
                    passed = related_calls[0] > related_failures
                if suite == 'mandatory-final':
                    mac_calls[0] += 1
                    passed = not mandatory_failure and mac_calls[0] > mac_failures
                    if not passed and auto:
                        loop.save_json(loop.mac_evidence_path(logs, round_no), {'schema_version': 1,
                            'platform': 'darwin', 'tested_sha256': loop.runtime_bytes({
                                p: loop.digest(root/p) for p in task['allowed_files'] if (root/p).is_file()}),
                            'failure': {'status': 'FAIL', 'scenario': 'hung_supervisor', 'owned_processes': [],
                                        'fixture_cleanup': {'status': 'RESOLVED', 'original_owned_processes': [
                    {'pid': 321, 'host_identity': {'pid': 321}, 'supervisor': {'pid': 322}}]}}})
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
                developer_calls[0] += 1
                if decision:
                    (root / 'reports').mkdir(exist_ok=True)
                    (root / 'reports/validation.md').write_text('NEEDS_DECISION; no implementation.\n')
                    return {'status': 'NEEDS_DECISION', 'summary': 'Continuity requires a decision.'}
                dirty[0] = True
                if not unchanged_repair:
                    (root / 'source.txt').write_text('bounded repair ' + str(developer_calls[0] if auto else review_calls[0]))
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
                stack.enter_context(patch.object(loop, 'prepare_acceptance_resume' if native_resume else 'prepare_initial_resume', return_value=({},
                    {'from_run': '20261007T075141Z-70725', 'partial_files':
                     {'source.txt': loop.digest(root / 'source.txt')}, 'inherited_validation': 'NONE',
                     'kind': 'acceptance_failure' if native_resume else 'initial'})))
            stack.enter_context(patch.object(loop, 'read_pr', return_value=self.pr()))
            stack.enter_context(patch.object(loop, 'check_scope', side_effect=fake_scope))
            stack.enter_context(patch.object(loop, 'validate_mac_evidence', return_value={'fixture': 'Mac evidence is mocked here'}))
            stack.enter_context(patch.object(loop, 'tests', side_effect=fake_tests))
            claude = stack.enter_context(patch.object(loop, 'claude', side_effect=fake_claude))
            codex = stack.enter_context(patch.object(loop, 'codex', side_effect=fake_codex))
            checkpoint = stack.enter_context(patch.object(loop, 'publish_decision_checkpoint',
                side_effect=loop.Stop('checkpoint API unavailable') if checkpoint_failure else None,
                return_value={'url': 'https://github.com/fixture/checkpoint', 'status': 'NEEDS_DECISION'}))
            runner = stack.enter_context(patch.object(loop, 'run', side_effect=fake_run))
            diagnostics = stack.enter_context(patch.object(loop, 'publish_diagnostics', return_value='https://github.com/diagnostics'))
            ci_success = [{'status': 'completed', 'conclusion': 'success'}]
            stack.enter_context(patch.object(loop, 'wait_ci', side_effect=[
                loop.CIFailure(HEAD, [{'databaseId': 7, 'conclusion': 'failure'}]) for _ in range(ci_failures)] + [ci_success]))
            if auto:
                stack.enter_context(patch.object(loop, 'ensure_failed_probe_pids_absent'))
                def failure_review(cwd, logs, metadata, text, head, evidence_path, sha, round_no):
                    value = FailureCoordinationTests().decision(head, sha)
                    value['findings'][0]['file'] = 'source.txt'
                    return value
                stack.enter_context(patch.object(loop, 'review_failure', side_effect=failure_review))
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
            expected_calls = (1 if unchanged_repair else min(failures + final_full_failures + 1, 2)) if native_resume else (
                min(failures + final_full_failures, 2) + int(new_task))
            if auto:
                expected_calls = 2 if should_stop and mandatory_failure else 1 + failures + final_full_failures + related_failures + mac_failures + ci_failures
                self.assertEqual(sum(c.kwargs.get('batch', '').endswith('-failure') for c in diagnostics.call_args_list),
                                 2 if should_stop and mandatory_failure else expected_calls-1)
                self.assertEqual(sum(not c.kwargs.get('batch') for c in diagnostics.call_args_list), int(should_stop))
            else:
                self.assertEqual(diagnostics.call_count, int(should_stop))
            self.assertEqual(codex.call_count, expected_calls)
            if codex_timeout is not None:
                self.assertEqual(codex.call_args.args[4], codex_timeout)
                self.assertTrue(all(c.args[4] == task.get('claude_timeout_seconds', task['timeout_seconds'])
                                    for c in claude.call_args_list))
            if resume:
                prepare_new.assert_not_called()
                label = native_scenario or 'SIGINT'
                expected_reason = 'coordinator-assessed ' + label + ' Mac failure' if native_resume and not failures else (
                    'Continue the existing partial implementation' if not native_resume else 'Repair only')
                self.assertIn('evidence-bound blocking' if auto and expected_calls > 1 else expected_reason, codex.call_args.args[2])
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


class FailureCoordinationTests(unittest.TestCase):
    def decision(self, head=HEAD, sha='a'*64, action='REPAIR'):
        return {'decision': action, 'category': {'REPAIR': 'CODE_BUG', 'NEEDS_DECISION': 'SCOPE_OR_DESIGN',
            'BLOCKED': 'ENVIRONMENT'}[action], 'reviewed_head': head, 'reviewed_evidence_sha256': sha,
            'summary': 'Concrete bounded disposition', 'findings': [{'severity': 'High', 'blocking': True,
                'file': 'source.py', 'detail': 'Correct the reproducible cleanup defect.',
                'evidence': 'Failed assertion in the fixed regression case.'}] if action == 'REPAIR' else []}

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp); logs = root/'.ai-loop/20261008T000000Z-1'; logs.mkdir(parents=True)
            (root/'source.py').write_text('original candidate')
            task = {'task_id': 'fixture', 'branch': 'feat/fixture', 'base_sha': HEAD,
                'allowed_files': ['source.py'], 'failure_triage': {'max_reviews': 4, 'repeat_limit': 2,
                'allow_mac_repair': True}}
            obj = loop.FailureCoordinator(root, logs, task, 'fixed scope', {})
            stack.enter_context(patch.object(loop, 'git', return_value='fixed status'))
            stack.enter_context(patch.object(loop, 'check_scope', return_value=[]))
            publisher = stack.enter_context(patch.object(loop, 'publish_diagnostics', return_value='https://github.com/report'))
            def decision(cwd, directory, metadata, text, head, path, sha, round_no):
                return self.decision(head, sha)
            reviewer = stack.enter_context(patch.object(loop, 'review_failure', side_effect=decision))
            stack.enter_context(patch.object(loop, 'ensure_failed_probe_pids_absent'))
            yield root, logs, task, obj, publisher, reviewer

    def test_failure_then_review_then_disposition_are_published_before_repair(self):
        with self.fixture() as (root, logs, task, obj, pub, review):
            order = []
            pub.side_effect = lambda *a, **kw: order.append(kw['batch']) or 'https://github.com/report'
            original = review.side_effect
            review.side_effect = lambda *a: order.append('CLAUDE') or original(*a)
            reason = obj.assess('related', HEAD, {'results': [{'argv': ['test'], 'exit_code': 1}]}, 0, 3)
            self.assertEqual(order, ['round-0-failure', 'CLAUDE', 'round-0-review', 'round-0-disposition'])
            self.assertIn('Repair only', reason)
            self.assertEqual(obj.records[0]['disposition'], 'REPAIR')
            self.assertEqual(json.loads((logs/'failure-0.json').read_text())['status'], 'FAIL')

    def test_unchanged_code_cannot_retry_and_changed_code_can(self):
        with self.fixture() as (root, logs, task, obj, *rest):
            obj.assess('related', HEAD, {}, 0, 3)
            with self.assertRaises(loop.Stop): obj.require_changed_repair()
            (root/'source.py').write_text('real source change')
            obj.require_changed_repair()
            self.assertIsNone(obj.required_change)

    def test_failed_candidate_bytes_are_retained_before_next_writer(self):
        with self.fixture() as (root, logs, task, obj, *rest):
            obj.assess('related', HEAD, {}, 0, 3)
            (root/'source.py').write_text('new repaired bytes')
            self.assertEqual((logs/'failure-inputs/0/source.py').read_text(), 'original candidate')
            row = json.loads((logs/'failure-0.json').read_text())
            self.assertEqual(row['source_sha256']['source.py'], loop.digest(logs/'failure-inputs/0/source.py'))

    def test_native_log_output_paths_cannot_evade_repeated_failure_bound(self):
        with self.fixture() as (root, logs, task, obj, *rest):
            obj.assess('related', HEAD, {'results': [{'argv': ['test', str(logs/'mac-containment-0.json')], 'exit_code': 1}]}, 0, 3)
            (root/'source.py').write_text('candidate changed')
            with self.assertRaisesRegex(loop.Stop, 'STALLED'):
                obj.assess('related', HEAD, {'results': [{'argv': ['test', str(logs/'mac-containment-1.json')], 'exit_code': 1}]}, 1, 3)

    def test_design_and_environment_decisions_stop_without_codex_instruction(self):
        for action in ('NEEDS_DECISION', 'BLOCKED'):
            with self.subTest(action=action), self.fixture() as (root, logs, task, obj, pub, review):
                review.side_effect = lambda *a: self.decision(a[4], a[6], action)
                with self.assertRaisesRegex(loop.Stop, action): obj.assess('related', HEAD, {}, 0, 3)
                self.assertEqual(obj.records[0]['disposition'], action)
                self.assertEqual(pub.call_count, 3)

    def test_budget_and_repeat_limit_remain_non_pass(self):
        with self.fixture() as (root, logs, task, obj, *rest):
            with self.assertRaisesRegex(loop.Stop, 'BUDGET_EXHAUSTED'):
                obj.assess('related', HEAD, {}, 0, 0)
        with self.fixture() as (root, logs, task, obj, *rest):
            obj.assess('related', HEAD, {}, 0, 3)
            (root/'source.py').write_text('changed once')
            with self.assertRaisesRegex(loop.Stop, 'STALLED'):
                obj.assess('related', HEAD, {}, 1, 3)
            self.assertEqual(obj.records[-1]['repeat_count'], 2)

    def test_malformed_optional_scope_and_wrong_evidence_are_rejected(self):
        task = {'allowed_files': ['source.py']}
        for fault in ('head', 'sha', 'scope', 'optional', 'unknown', 'type', 'oversize'):
            with self.subTest(fault=fault):
                value = self.decision()
                if fault == 'head': value['reviewed_head'] = 'f'*40
                if fault == 'sha': value['reviewed_evidence_sha256'] = 'f'*64
                if fault == 'scope': value['findings'][0]['file'] = 'ROADMAP.md'
                if fault == 'optional': value['findings'][0].update(severity='Low', blocking=False)
                if fault == 'unknown': value['extra'] = 'scope authority'
                if fault == 'type': value['decision'] = []
                if fault == 'oversize': value['summary'] = 'x'*2001
                with self.assertRaises(loop.Stop): loop.validate_failure_review(value, task, HEAD, 'a'*64)

    def test_public_report_omits_raw_provider_and_deterministic_logs(self):
        with self.fixture() as (root, logs, task, obj, pub, review):
            (logs/'tests-0-related-0.stderr.log').write_text('PRIVATE_LOG_SENTINEL')
            obj.assess('related', HEAD, {'provider': 'PRIVATE_PROVIDER_SENTINEL'}, 0, 3)
            public = b''.join(raw for call in pub.call_args_list for raw in call.kwargs['files'].values())
            self.assertNotIn(b'PRIVATE_LOG_SENTINEL', public)
            self.assertNotIn(b'PRIVATE_PROVIDER_SENTINEL', public)
            self.assertIn('PRIVATE_PROVIDER_SENTINEL', (logs/'failure-0.json').read_text())

    def test_review_must_not_modify_source_or_failed_evidence(self):
        for which in ('source', 'evidence'):
            with self.subTest(which=which), self.fixture() as (root, logs, task, obj, pub, review):
                original = review.side_effect
                def mutate(*a):
                    value = original(*a)
                    (root/'source.py' if which == 'source' else a[5]).write_text('changed by review')
                    return value
                review.side_effect = mutate
                with self.assertRaisesRegex(loop.Stop, 'changed candidate/evidence'):
                    obj.assess('related', HEAD, {}, 0, 3)
                self.assertEqual(pub.call_count, 1)

    def test_upload_failure_does_not_launch_claude_or_codex(self):
        with self.fixture() as (root, logs, task, obj, pub, review):
            pub.side_effect = loop.Stop('Git unavailable')
            with self.assertRaises(loop.Stop): obj.assess('related', HEAD, {}, 0, 3)
            review.assert_not_called()

    def test_missing_mac_proof_or_retained_pid_stops_without_any_signal(self):
        for fault in ('missing', 'hash', 'retained', 'unproven', 'not_authorized'):
            with self.subTest(fault=fault), self.fixture() as (root, logs, task, obj, pub, review), \
                    patch.object(loop.os, 'kill') as kill, patch.object(loop.os, 'killpg') as killpg:
                mac = {'platform': 'darwin', 'schema_version': 1, 'tested_sha256': loop.runtime_bytes(obj.source_bytes()),
                       'failure': {'status': 'FAIL', 'scenario': 'hung_supervisor', 'owned_processes': [],
                                   'fixture_cleanup': {'status': 'RESOLVED', 'original_owned_processes': [
                    {'pid': 321, 'host_identity': {'pid': 321}, 'supervisor': {'pid': 322}}]}}}
                if fault == 'missing': mac = None
                elif fault == 'hash': mac['tested_sha256'] = {}
                elif fault == 'retained': mac['failure']['owned_processes'] = [{'pid': 123}]
                elif fault == 'unproven': mac['failure'].pop('fixture_cleanup')
                else: task['failure_triage']['allow_mac_repair'] = False
                with patch.object(loop, 'ensure_failed_probe_pids_absent', side_effect=loop.Stop('still present')):
                    with self.assertRaises(loop.Stop): obj.assess('mandatory-final', HEAD, {'mac': mac}, 0, 3)
                kill.assert_not_called(); killpg.assert_not_called()

    def test_mac_preflight_test_bug_can_repair_without_native_scenario_evidence(self):
        with self.fixture() as (root, logs, task, obj, pub, review):
            test_path = 'tools/llmtool/tests/run_containment_acceptance.py'
            task['allowed_files'].append(test_path)
            original = review.side_effect
            def preflight_decision(*a):
                value = original(*a)
                value['category'] = 'TEST_BUG'
                value['findings'][0]['file'] = test_path
                return value
            review.side_effect = preflight_decision
            mac = {'platform': 'darwin', 'schema_version': 1, 'tested_sha256': {}, 'scenarios': {},
                   'failure': {'status': 'FAIL', 'scenario': 'preflight',
                               'exception_type': 'ValueError', 'owned_processes': []}}
            reason = obj.assess('mandatory-final', HEAD, {'mac': mac}, 0, 3)
            self.assertIn('Repair only', reason)
            self.assertEqual(obj.records[0]['disposition'], 'REPAIR')

    def test_mac_preflight_without_test_bug_classification_stays_blocked(self):
        with self.fixture() as (root, logs, task, obj, *rest):
            mac = {'platform': 'darwin', 'schema_version': 1, 'tested_sha256': {}, 'scenarios': {},
                   'failure': {'status': 'FAIL', 'scenario': 'preflight',
                               'exception_type': 'ValueError', 'owned_processes': []}}
            with self.assertRaisesRegex(loop.Stop, 'MAC_EVIDENCE_INCOMPLETE'):
                obj.assess('mandatory-final', HEAD, {'mac': mac}, 0, 3)

    def test_mac_proven_clean_failure_can_repair_on_changed_bytes(self):
        with self.fixture() as (root, logs, task, obj, *rest):
            mac = {'platform': 'darwin', 'schema_version': 1, 'tested_sha256': loop.runtime_bytes(obj.source_bytes()),
                   'failure': {'status': 'FAIL', 'scenario': 'hung_supervisor', 'owned_processes': [],
                               'fixture_cleanup': {'status': 'RESOLVED', 'original_owned_processes': [
                    {'pid': 321, 'host_identity': {'pid': 321}, 'supervisor': {'pid': 322}}]}}}
            self.assertIn('Repair only', obj.assess('mandatory-final', HEAD, {'mac': mac}, 0, 3))
            self.assertEqual(obj.records[0]['disposition'], 'REPAIR')

    def test_failure_schema_and_policy_bounds_are_strict(self):
        original = historical_task()
        original['failure_triage'] = {'max_reviews': 4, 'repeat_limit': 2, 'allow_mac_repair': True}
        original['max_repairs'] = 4
        def load(task):
            with patch.object(loop, 'git', side_effect=[json.dumps(task), 'task', '']):
                return loop.load_task(Path('.'), HEAD)[0]
        load(original)
        for key, value in (('max_reviews', True), ('max_reviews', 5), ('repeat_limit', 1), ('allow_mac_repair', 1), ('extra', 1)):
            bad = copy.deepcopy(original); bad['failure_triage'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(loop.Stop): load(bad)
        bad = copy.deepcopy(original); bad.pop('failure_triage')
        with self.assertRaises(loop.Stop): load(bad)

    def test_failure_claude_uses_only_read_tools_and_validated_bound_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); logs = root/'logs'; logs.mkdir()
            task = {'allowed_files': ['source.py'], 'base_sha': HEAD, 'timeout_seconds': 60}
            path = logs/'failure.json'; path.write_text('{}')
            def run(argv, cwd, **kw):
                self.assertNotIn('Bash', argv)
                self.assertIn('Read,Glob,Grep', argv)
                self.assertIn('dontAsk', argv)
                self.assertIn('--strict-mcp-config', argv)
                loop.save_json(kw['stdout_path'], {'is_error': False, 'subtype': 'success',
                    'structured_output': self.decision()})
            with patch.object(loop, 'run', side_effect=run), patch.object(loop, 'git', return_value='fixed diff'):
                value = loop.review_failure(root, logs, task, 'fixed contract', HEAD, path, 'a'*64, 0)
            self.assertEqual(value['decision'], 'REPAIR')


class ClassificationDiagnosticTests(unittest.TestCase):
    def artifacts(self, logs, task, value=None, round_no=2):
        path = logs / ('failure-%d.json' % round_no)
        loop.save_json(path, {'run_id': logs.name, 'task_id': task['task_id'], 'round': round_no,
            'phase': 'full-final', 'head': HEAD, 'status': 'FAIL', 'gate_waived': False,
            'test_log_paths': ['PRIVATE_TEST_LOG'], 'evidence': {'private': 'PRIVATE_EVIDENCE'}})
        sha = loop.digest(path)
        if value is None:
            value = FailureCoordinationTests().decision(HEAD, sha)
            value['summary'] = 'PRIVATE_MODEL_TEXT' * 150
        loop.save_json(logs / ('failure-%d-claude.raw.json' % round_no), {
            'is_error': False, 'subtype': 'success', 'structured_output': value,
            'result': 'PRIVATE_RESULT', 'session_id': 'PRIVATE_SESSION'})
        return sha

    def test_rejection_identifies_length_without_exporting_text(self):
        with DiagnosticPublicationTests().fixture() as (root, logs, task):
            sha = self.artifacts(logs, task)
            row = loop.classification_rejection(logs, task, 2)
            self.assertEqual(row['reason_codes'], ['SUMMARY_TOO_LONG'])
            self.assertEqual(row['evidence_sha256'], sha)
            self.assertTrue(row['checks']['reviewed_head_matches'])
            self.assertTrue(row['checks']['reviewed_evidence_sha256_matches'])
            self.assertEqual(row['checks']['summary_characters'], 2700)
            self.assertFalse(row['gate_waived'])
            self.assertFalse(row['recovery_authorized'])
            self.assertNotIn('PRIVATE_', json.dumps(row))

    def test_head_and_evidence_mismatch_are_separate_and_never_corrected(self):
        value = FailureCoordinationTests().decision('f'*40, 'f'*64)
        with DiagnosticPublicationTests().fixture() as (root, logs, task):
            self.artifacts(logs, task, value)
            before = (logs/'failure-2-claude.raw.json').read_bytes()
            row = loop.classification_rejection(logs, task, 2)
            self.assertEqual(row['reason_codes'], ['REVIEWED_HEAD_MISMATCH', 'REVIEWED_EVIDENCE_SHA256_MISMATCH'])
            self.assertEqual(before, (logs/'failure-2-claude.raw.json').read_bytes())
            with self.assertRaisesRegex(loop.FailureReviewError, 'REVIEWED_HEAD_MISMATCH'):
                loop.validate_failure_review(value, task, HEAD, row['evidence_sha256'])

    def test_schema_encodes_existing_limits_and_exact_review_identity(self):
        schema = loop.failure_schema(HEAD, 'a'*64)
        row = schema['properties']
        self.assertEqual(row['reviewed_head']['enum'], [HEAD])
        self.assertEqual(row['reviewed_evidence_sha256']['enum'], ['a'*64])
        self.assertEqual(row['summary']['maxLength'], 2000)
        self.assertEqual(row['findings']['maxItems'], 16)
        for key in ('file', 'detail', 'evidence'):
            self.assertEqual(row['findings']['items']['properties'][key]['maxLength'], 2000)
        self.assertNotIn('enum', loop.failure_schema()['properties']['reviewed_head'])
        self.assertEqual(schema['oneOf'], [
            {'properties': {'decision': {'const': 'REPAIR'},
                            'category': {'enum': ['CODE_BUG', 'TEST_BUG']}}},
            {'properties': {'decision': {'const': 'NEEDS_DECISION'},
                            'category': {'enum': ['CODE_BUG', 'TEST_BUG', 'SCOPE_OR_DESIGN']}}},
            {'properties': {'decision': {'const': 'BLOCKED'},
                            'category': {'const': 'ENVIRONMENT'}}}
        ])

    def test_validator_pair_matrix_matches_output_schema(self):
        task = {'allowed_files': ['source.py']}
        cases = [
            ('REPAIR', 'CODE_BUG', True), ('REPAIR', 'TEST_BUG', True),
            ('REPAIR', 'ENVIRONMENT', False), ('NEEDS_DECISION', 'SCOPE_OR_DESIGN', True),
            ('NEEDS_DECISION', 'TEST_BUG', True), ('NEEDS_DECISION', 'CODE_BUG', True),
            ('NEEDS_DECISION', 'ENVIRONMENT', False), ('BLOCKED', 'ENVIRONMENT', True),
            ('BLOCKED', 'CODE_BUG', False)]
        for decision, category, valid in cases:
            with self.subTest(decision=decision, category=category):
                value = FailureCoordinationTests().decision()
                value['decision'] = decision
                value['category'] = category
                if decision != 'REPAIR':
                    value['findings'] = []
                reasons = loop.failure_review_reasons(value, task, HEAD, 'a'*64)
                self.assertEqual('CATEGORY_DECISION_MISMATCH' not in reasons, valid)

    def test_malformed_envelope_and_unexpected_fields_do_not_leak(self):
        for raw in ('not JSON PRIVATE_SECRET', json.dumps({'is_error': True, 'result': 'PRIVATE_SECRET'}),
                    json.dumps({'is_error': False, 'subtype': 'success', 'structured_output': {'private': 'PRIVATE_SECRET'}})):
            with self.subTest(raw=raw), DiagnosticPublicationTests().fixture() as (root, logs, task):
                self.artifacts(logs, task)
                (logs/'failure-2-claude.raw.json').write_text(raw)
                row = loop.classification_rejection(logs, task, 2)
                self.assertIn(row['reason_codes'][0], ('CLAUDE_ENVELOPE_NOT_SUCCESS', 'CLASSIFICATION_SCHEMA'))
                self.assertNotIn('PRIVATE_SECRET', json.dumps(row))

    def test_valid_review_is_not_a_rejection(self):
        with DiagnosticPublicationTests().fixture() as (root, logs, task):
            self.artifacts(logs, task)
            sha = loop.digest(logs/'failure-2.json')
            value = FailureCoordinationTests().decision(HEAD, sha)
            value['findings'][0]['file'] = 'source.py'
            loop.save_json(logs/'failure-2-claude.raw.json', {'is_error': False, 'subtype': 'success',
                                                           'structured_output': value})
            self.assertIsNone(loop.classification_rejection(logs, task, 2))

    def test_post_stop_upload_uses_original_contract_and_no_model_test_signal(self):
        with DiagnosticPublicationTests().fixture() as (root, logs, task), \
                patch.object(loop, 'publish_diagnostics') as pub, patch.object(loop, 'run') as run, \
                patch.object(loop.os, 'kill') as kill, patch.object(loop.os, 'killpg') as killpg:
            self.artifacts(logs, task)
            with patch.object(loop, 'load_task', return_value=(task, 'original')) as load:
                loop.publish_classification_rejections(root, logs)
            load.assert_called_once_with(root, HEAD)
            pub.assert_called_once()
            self.assertEqual(pub.call_args.kwargs['batch'], 'round-2-rejection')
            self.assertEqual(set(pub.call_args.kwargs['files']), {'index.md', 'rejection.json'})
            self.assertNotIn(b'PRIVATE_', b''.join(pub.call_args.kwargs['files'].values()))
            run.assert_not_called(); kill.assert_not_called(); killpg.assert_not_called()

    def test_unsafe_oversized_wrong_contract_or_evidence_cannot_publish(self):
        for fault in ('symlink', 'oversize', 'contract', 'run', 'active'):
            with self.subTest(fault=fault), DiagnosticPublicationTests().fixture() as (root, logs, task), \
                    patch.object(loop, 'publish_diagnostics') as pub:
                self.artifacts(logs, task)
                path = logs/'failure-2-claude.raw.json'
                if fault == 'symlink':
                    path.unlink(); path.symlink_to(logs/'codex-0.stderr.log')
                elif fault == 'oversize': path.write_bytes(b'x' * (2*1024*1024+1))
                elif fault == 'contract': loop.save_json(logs/'task.json', {'task_id': 'wrong'})
                elif fault == 'run':
                    row = json.loads((logs/'failure-2.json').read_text()); row['run_id'] = 'wrong'
                    loop.save_json(logs/'failure-2.json', row)
                else: loop.save_json(logs/'summary.json', {'status': 'RUNNING'})
                with self.assertRaises(loop.Stop): loop.publish_classification_rejections(root, logs)
                pub.assert_not_called()

    def test_rejection_batch_is_immutable_and_idempotent(self):
        helper = DiagnosticPublicationTests()
        with helper.fixture() as (root, logs, task):
            files = {'index.md': b'# Rejection\n', 'rejection.json': b'{"gate_waived": false}\n'}
            state, api = helper.fake_api(root, logs, files=files, batch='round-2-rejection')
            with patch.object(loop, 'diagnostic_api', side_effect=api):
                loop.publish_diagnostics(root, logs, files=files, batch='round-2-rejection')
                count = sum(call[0] != 'GET' for call in state['calls'])
                loop.publish_diagnostics(root, logs, files=files, batch='round-2-rejection')
                self.assertEqual(sum(call[0] != 'GET' for call in state['calls']), count)
                with self.assertRaisesRegex(loop.Stop, 'never overwrite'):
                    loop.publish_diagnostics(root, logs, files={**files, 'rejection.json': b'changed'}, batch='round-2-rejection')

    def test_invalid_model_output_is_published_before_stopping_without_retry(self):
        with FailureCoordinationTests().fixture() as (root, logs, task, obj, pub, review):
            def invalid(*args):
                value = FailureCoordinationTests().decision(args[4], args[6])
                value['summary'] = 'x'*2001
                loop.save_json(logs/'failure-0-claude.raw.json', {'is_error': False, 'subtype': 'success',
                                                               'structured_output': value})
                return loop.validate_failure_review(value, task, args[4], args[6])
            review.side_effect = invalid
            with self.assertRaisesRegex(loop.FailureReviewError, 'SUMMARY_TOO_LONG'):
                obj.assess('full-final', HEAD, {}, 0, 3)
            self.assertEqual([call.kwargs['batch'] for call in pub.call_args_list], ['round-0-failure', 'round-0-rejection'])
            self.assertEqual(obj.records, [])
            self.assertIsNone(obj.required_change)
            self.assertEqual((root/'source.py').read_text(), 'original candidate')


class TaskFixtureTests(unittest.TestCase):
    def test_historical_fixture_returns_fresh_archived_data(self):
        archived = json.loads(HISTORICAL_TASK.read_text())
        task = historical_task()
        self.assertEqual(task, archived)
        self.assertIn('mac_evidence', task)
        self.assertIn('sandbox_decision_recovery', task)
        task['allowed_files'].clear()
        task['sandbox_decision_recovery']['repair_files'].clear()
        self.assertEqual(historical_task(), archived)

    def test_active_task_loads_independently_of_historical_recovery(self):
        root = Path(__file__).parents[1]
        active = json.loads((TASK_DIR / 'task.json').read_text())
        text = (TASK_DIR / 'task.md').read_text()
        with patch(__name__ + '.historical_task') as historical:
            loaded, loaded_text = loop.load_task(root, 'HEAD')
        historical.assert_not_called()
        self.assertEqual(loaded, active)
        self.assertEqual(loaded_text, text.strip())

    def test_historical_schema_checks_coexist_with_active_c1_without_reading_it(self):
        # Exercise positive setups and every existing negative assertion while
        # the real C1 contract remains active but unavailable as a fixture.
        cases = (
            (NativeResumeTests, 'test_scenario_schema_is_bounded_and_legacy_sigint_still_loads'),
            (NativeResumeTests, 'test_preflight_recovery_schema_is_bounded_to_allowlisted_test_files'),
            (NativeResumeTests, 'test_new_schema_binds_failure_and_allows_only_one_recovery_policy'),
            (SandboxDecisionRecoveryTests, 'test_no_change_decision_schema_requires_single_exact_test'),
            (ReviewFailureRecoveryTests, 'test_review_recovery_schema_rejects_extra_authority'),
            (StoppedFailureRecoveryTests, 'test_out_of_scope_recovery_schema_is_strict'),
            (StoppedFailureRecoveryTests, 'test_stopped_recovery_schema_is_strict_and_mutually_exclusive'),
            (DecisionCheckpointTests, 'test_task_contract_requires_boolean_and_report_for_decision_publication'),
            (ProgressTests, 'test_model_timeout_schema_accepts_three_hours_and_rejects_unbounded_values'),
            (FailureCoordinationTests, 'test_failure_schema_and_policy_bounds_are_strict'),
        )
        read_text = Path.read_text

        def read_fixture(path, *args, **kwargs):
            self.assertNotEqual(path, TASK_DIR / 'task.json',
                                'historical schema fixture read the active task')
            return read_text(path, *args, **kwargs)

        with patch.object(Path, 'read_text', autospec=True, side_effect=read_fixture):
            for case, method in cases:
                with self.subTest(case=case.__name__, method=method):
                    case(method).debug()


if __name__ == '__main__':
    unittest.main()
