"""#2087 (Advisor slice A2-iii-2): unit tests for task dispute arbitrate launch and Codex completion."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import (  # noqa: E402
    review_dispatch,
    task_completion,
    task_store,
    task_submission_cli,
)
from llmtool.errors import LLMToolError  # noqa: E402

import review_trust_fixture  # noqa: E402
import test_dispute_ruling_claim_readers as tdrcr  # noqa: E402
import worker_policy_fixture  # noqa: E402


TASK = tdrcr.TASK
RUN = tdrcr.RUN
_DIFF = 'diff --git a/tools/a.py b/tools/a.py\n'
_DIFF_SHA256 = hashlib.sha256(_DIFF.encode('utf-8')).hexdigest()
HEAD = tdrcr.HEAD
NEW_HEAD = 'd' * 40
EXECUTOR = tdrcr.EXECUTOR
PM_SESSION = tdrcr.PM_SESSION
STAMP_1 = tdrcr.STAMP_1
_, CONTRACT = tdrcr._base_task_record()
THREAD_ID = '0123456789abcdef0123456789abcdef'
CLAUDE_SESSION = '11111111-1111-4111-8111-111111111111'
PR = tdrcr.PR
FINDING = 'F1'
REVIEW_VERSION = 1
DISPUTE_COMMENT_ID = 1001
REVIEW_LOGIN = 'review-account'
REPOSITORY = 'davidcatcat/davidcattoolsproject'
EVIDENCE_REF = f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-{DISPUTE_COMMENT_ID}'
BINDING = f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}'
FINDINGS = 'Finding: F1 [High] tools/a.py:1 missing guard\n'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _verdict_text(*, actor='Claude', status='FAIL', head=HEAD, version=REVIEW_VERSION,
                  findings=FINDINGS):
    marker = 'FIX' if status == 'FAIL' else status
    return (
        f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker} -->\n'
        f'Verdict: {status} | Version: v{version} | HEAD: {head}\n'
        f'{BINDING}\n'
        f'{findings}'
    )


def _ruling_text(*, actor='Codex', marker_status='UPHELD', header_outcome='UPHELD',
                 finding=FINDING, pr=PR, version=REVIEW_VERSION, head=HEAD,
                 binding=BINDING, extra=''):
    return (
        f'<!-- activity: type=dispute_ruling actor={actor} target=PM status={marker_status} -->\n'
        f'Ruling: {finding} {header_outcome} | PR: #{pr} | Version: {version} | HEAD: {head}\n'
        f'{binding}\n'
        f'Arbiter reasoning.{extra}'
    )


def _codex_jsonl(payload, *, thread_id=THREAD_ID, extra_threads=0):
    events = [{'type': 'thread.started', 'thread_id': thread_id}]
    for _ in range(extra_threads):
        events.append({'type': 'thread.started', 'thread_id': thread_id})
    text = payload if type(payload) is str else json.dumps(payload)
    events.append({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}})
    return ('\n'.join(json.dumps(e) for e in events) + '\n').encode('utf-8')


def _formal_claim(contract_digest=CONTRACT, *, version=REVIEW_VERSION, state='COMPLETED',
                  actor='Claude', verdict='FAIL', account_mode='ROLES',
                  reviewer_task=tdrcr.FORMAL_REVIEWER):
    claim = tdrcr._formal_claim(
        contract_digest, version=version, verdict=verdict,
        reviewer_task=reviewer_task, actor=actor)
    claim['account_mode'] = account_mode
    if state != 'COMPLETED':
        claim['state'] = state
        claim.pop('review_result', None)
        if state == 'CLAIMED':
            claim['native_session_id'] = None
    return claim


def _default_developer():
    return {
        'assignment_id': 'f' * 32,
        'selection_source': 'PM',
        'state': 'DEVELOPER_RESPONDED',
        'provider_profile': 'agy',
        'provider_type': 'agy-cli',
        'model': 'gemini-2.5-pro',
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-' + ('1' * 32),
        'source_sha': HEAD,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['tools/a.py'],
        'write_paths': ['tools/a.py'],
        'acceptance': ['A1'],
        'context_sha256': '1' * 64,
        'egress_basis': 'bounded test',
        'input_bytes': 10,
        'max_tokens': None,
        'artifact_format': 'unified_diff',
        'artifact': _DIFF,
        'artifact_sha256': _DIFF_SHA256,
        'usage': None,
        'error_code': None,
        'created_at': STAMP_1,
        'owner_assignment_ref': None,
        'executor_task_id': EXECUTOR,
    }


def _default_completion(*, root, common_dir):
    return {
        'state': 'WAITING_EXECUTOR',
        'executor_task_id': EXECUTOR,
        'reviewer_task_id': None,
        'evidence': {
            'task_id': TASK,
            'run_id': RUN,
            'contract_digest': CONTRACT,
            'execution_mode': 'DIRECT_D6',
            'root': str(root),
            'common_dir': str(common_dir),
            'branch': 'llmtool/' + EXECUTOR,
            'source_sha': HEAD,
            'executor_identity': 'agy',
            'executor_session_ref': 'ref-1',
            'executor_native_session_id': 'exec-session-1',
            'executor_native_session': 'VERIFIED',
        },
        'missing_gates': [],
        'pm_dispatch': 'NOT_STARTED',
        'pm_response': None,
        'pm_recommendation': None,
        'pm_usage': None,
        'pm_error_code': None,
    }


def _dispute_comment(*, body='PM dispute explanation.', created='2026-11-22T23:59:00Z',
                     updated=None, pr=PR, comment_id=DISPUTE_COMMENT_ID):
    return {
        'id': comment_id,
        'body': body,
        'issue_url': f'https://api.github.com/repos/{REPOSITORY}/issues/{pr}',
        'created_at': created,
        'updated_at': created if updated is None else updated,
    }


class _ArbitrateLaunchCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base_dir = Path(self.temp.name).resolve()
        self.store_dir = base_dir / 'tasks'
        self.store = task_store.TaskStore(self.store_dir)
        self.repo_root = base_dir / 'repo'
        self.common_dir = self.repo_root / '.git'
        self.common_dir.mkdir(parents=True)
        self.exec_root = base_dir / 'exec'
        self.exec_root.mkdir(parents=True)
        self.rev_root = base_dir / 'rev'
        self.rev_root.mkdir(parents=True)
        self.policy_path = worker_policy_fixture.install(self)

        self.pr_head = HEAD
        self.d6_records = {
            EXECUTOR: SimpleNamespace(
                task_id=EXECUTOR,
                trusted_agent_id='agy',
                native_session_id='exec-session-1',
                result_sha=HEAD,
                root=str(self.exec_root),
                common_dir=str(self.common_dir),
                branch='llmtool/' + EXECUTOR,
                github_login=REVIEW_LOGIN,
            ),
        }
        self.snapshots = {
            str(self.exec_root): SimpleNamespace(
                head=HEAD,
                root=str(self.exec_root),
                common_dir=str(self.common_dir),
                branch='llmtool/' + EXECUTOR,
                clean=True,
            ),
        }
        self.created_d6 = []
        self.bound_sessions = []
        self.github_calls = []

        def fake_github_get(path):
            self.github_calls.append(path)
            if path == f'pulls/{PR}':
                return {
                    'number': PR,
                    'state': 'open',
                    'head': {'sha': self.pr_head},
                    'base': {'sha': '0' * 40},
                }
            if path == f'issues/comments/{DISPUTE_COMMENT_ID}':
                return _dispute_comment()
            raise AssertionError(f'unexpected github_get: {path}')

        def fake_read_d6(task_id, common_dir, allowed_states=None):
            if task_id in self.d6_records:
                return self.d6_records[task_id]
            return SimpleNamespace(
                task_id=task_id,
                trusted_agent_id='codex-reviewer',
                native_session_id=None,
                result_sha=HEAD,
                root=str(self.rev_root),
                common_dir=str(self.common_dir),
                branch='llmtool/' + task_id,
                github_login=REVIEW_LOGIN,
            )

        def fake_snapshot(root):
            return self.snapshots[str(root)]

        def fake_create_d6(**kwargs):
            self.created_d6.append(kwargs)
            task_id = kwargs['task_id']
            branch = 'llmtool/' + task_id
            self.d6_records[task_id] = SimpleNamespace(
                task_id=task_id,
                trusted_agent_id=kwargs['reviewer_key'],
                native_session_id=None,
                result_sha=kwargs['base_sha'],
                root=str(self.rev_root),
                common_dir=str(self.common_dir),
                branch=branch,
                github_login=REVIEW_LOGIN,
            )
            self.snapshots[str(self.rev_root)] = SimpleNamespace(
                head=kwargs['base_sha'],
                root=str(self.rev_root),
                common_dir=str(self.common_dir),
                branch=branch,
                clean=True,
            )

        def fake_bind(**kwargs):
            self.bound_sessions.append(kwargs)
            rec = self.d6_records.get(kwargs['task_id'])
            if rec is not None:
                rec.native_session_id = kwargs['session_id']

        self.fake_github_get = fake_github_get
        self.fake_create_d6 = fake_create_d6
        self.fake_bind = fake_bind

        patches = (
            patch.object(task_completion, '_github_get', side_effect=fake_github_get),
            patch.object(task_completion, '_trusted_slug', return_value=REPOSITORY),
            patch.object(task_completion, 'read_d6_registration', side_effect=fake_read_d6),
            patch.object(task_completion, 'collect_git_snapshot', side_effect=fake_snapshot),
            patch.object(task_submission_cli, 'result_head_admitted', return_value=True),
            patch.object(review_dispatch, '_review_rules',
                         return_value=('RULES\nRULES_END\n\n', '9' * 64)),
            patch.object(review_dispatch, '_resolve_github_login',
                         return_value=REVIEW_LOGIN),
            patch.object(review_dispatch, '_resolve_reviewer_role',
                         side_effect=self._resolve_role),
            patch.object(review_dispatch, '_admit_review_model',
                         side_effect=lambda _k, m: m),
            patch.object(review_dispatch, '_review_default_model',
                         side_effect=lambda k: 'gpt-6.1-sol' if k == 'codex-reviewer' else 'claude-sonnet-4-6'),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _resolve_role(self, reviewer_key):
        if reviewer_key == 'codex-reviewer':
            return {
                'source': 'roles',
                'provider': 'codex',
                'model': 'gpt-6.1-sol',
                'effort': 'high',
                'config_dir': str(self.repo_root / 'codex-home'),
                'account': 'codex-acc',
            }
        return {
            'source': 'roles',
            'provider': 'claude',
            'model': 'claude-sonnet-4-6',
            'config_dir': str(self.repo_root / 'claude-home'),
            'account': 'claude-acc',
        }

    def _task_path(self):
        return self.store.root / f'{TASK}.json'

    def _seed(self, *, developer=None, claims=None, disputes=None):
        temp_store = tempfile.TemporaryDirectory()
        self.addCleanup(temp_store.cleanup)
        self.store = task_store.TaskStore(Path(temp_store.name).resolve() / 'tasks')
        base, contract_digest = tdrcr._base_task_record()
        base['developer'] = _default_developer() if developer is None else developer
        base['completion'] = _default_completion(root=self.exec_root, common_dir=self.common_dir)
        raw_claims = [_formal_claim(contract_digest)] if claims is None else list(claims)
        formal_claims = [c for c in raw_claims if 'purpose' not in c]
        ruling_claims = [c for c in raw_claims if 'purpose' in c]
        if formal_claims:
            base['review_dispatch_claim'] = formal_claims
        rec = self.store.create(base)
        default_dispute = dict(tdrcr._dispute_entry(), evidence_ref=EVIDENCE_REF)
        dispute_list = [default_dispute] if disputes is None else disputes
        if dispute_list:
            initial_disputes = [
                {k: v for k, v in d.items() if k not in ('arbiter_claim', 'history')}
                for d in dispute_list
            ]
            rec = self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=initial_disputes)
        for arbiter in ruling_claims:
            claimed = {k: v for k, v in arbiter.items() if k != 'review_result'}
            claimed['state'] = 'CLAIMED'
            claimed['native_session_id'] = None
            current_claims = list(rec.get('review_dispatch_claim') or [])
            updates = {'review_dispatch_claim': current_claims + [claimed]}
            if dispute_list and any('arbiter_claim' in d for d in dispute_list):
                updates['review_disputes'] = dispute_list
            rec = self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                **updates)
            if arbiter['state'] != 'CLAIMED':
                rec = self.store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=current_claims + [arbiter])
        return rec

    def _record_bytes(self):
        return self._task_path().read_bytes()


class ArbitrateParserTests(unittest.TestCase):
    def test_parser_rejects_reviewer_session_outcome_or_evidence_options(self):
        parser = argparse.ArgumentParser()
        task_submission_cli.add_task_submission_parsers(parser.add_subparsers(dest='action'))
        args = parser.parse_args(['dispute', 'arbitrate', TASK, '--pr', '42', '--finding', 'F1'])
        self.assertEqual(args.dispute_action, 'arbitrate')
        for bad_opt in ('--reviewer', '--reviewer-task', '--reviewer-session',
                        '--outcome', '--evidence-ref'):
            with patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args([
                        'dispute', 'arbitrate', TASK,
                        '--pr', '42', '--finding', 'F1', bad_opt, 'val',
                    ])
                self.assertEqual(caught.exception.code, 2)


class ArbitrateLaunchTests(_ArbitrateLaunchCase):
    def test_disallowed_policy_refuses_dispute_arbiter_unavailable_before_any_write(self):
        # AGY (Gemini) Developer + Codex Reviewer -> chooses claude-reviewer; pause claude-reviewer.
        self._seed(claims=[_formal_claim(actor='Codex', account_mode='CODEX_CLI')])
        before = self._record_bytes()
        worker_policy_fixture.update(self.policy_path, 'claude-reviewer', 'PAUSED')
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                create_d6=self.fake_create_d6,
                bind_session=self.fake_bind,
            )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_ARBITER_UNAVAILABLE')
        self.assertEqual(self._record_bytes(), before)
        self.assertEqual(self.created_d6, [])
        self.assertEqual(self.bound_sessions, [])

    def test_bound_codex_output_succeeds_and_stores_claim_fields(self):
        self._seed()
        ruling_text = _ruling_text(actor='Codex', marker_status='WITHDRAWN', header_outcome='WITHDRAWN')
        raw_jsonl = _codex_jsonl({
            'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'outcome': 'WITHDRAWN', 'ruling': ruling_text,
        })
        expected_sha = hashlib.sha256(raw_jsonl).hexdigest()
        gh_reads = []

        def comment_get(path):
            gh_reads.append(path)
            return _dispute_comment(body='Dispute body for F1')

        with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/codex'), \
             patch.object(review_dispatch.subprocess, 'run',
                          return_value=SimpleNamespace(returncode=0, stdout=raw_jsonl, stderr=b'')), \
             patch('llmtool.codex_direct_developer.validate_profile'):
            res = task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=comment_get,
                create_d6=self.fake_create_d6,
                bind_session=self.fake_bind,
            )

        self.assertEqual(res['state'], 'COMPLETED')
        self.assertEqual(res['verdict'], 'RULING')
        self.assertEqual(res['outcome'], 'WITHDRAWN')
        self.assertEqual(res['reviewer'], 'codex-reviewer')
        self.assertEqual(res['reviewer_session'], THREAD_ID)
        self.assertEqual(gh_reads, [f'issues/comments/{DISPUTE_COMMENT_ID}'])
        self.assertEqual(len(self.created_d6), 1)
        self.assertEqual(self.created_d6[0]['base_sha'], HEAD)
        self.assertEqual(self.created_d6[0]['reviewer_key'], 'codex-reviewer')
        self.assertEqual(len(self.bound_sessions), 1)
        self.assertEqual(self.bound_sessions[0]['session_id'], THREAD_ID)

        stored = self.store.get(TASK)
        arbiter_claims = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling']
        self.assertEqual(len(arbiter_claims), 1)
        ac = arbiter_claims[0]
        self.assertEqual(ac['executor_task'], EXECUTOR)
        self.assertEqual(ac['pr'], PR)
        self.assertEqual(ac['head_sha'], HEAD)
        self.assertEqual(ac['reviewer'], 'codex-reviewer')
        self.assertEqual(ac['account_mode'], 'ROLES')
        self.assertEqual(ac['account'], 'codex-acc')
        self.assertGreater(ac['version'], REVIEW_VERSION)
        self.assertEqual(ac['native_session_id'], THREAD_ID)
        self.assertEqual(ac['dispute']['dispute_text_sha256'],
                         hashlib.sha256(b'Dispute body for F1').hexdigest())
        self.assertEqual(ac['review_result']['verdict'], 'RULING')
        self.assertEqual(ac['review_result']['outcome'], 'WITHDRAWN')
        self.assertEqual(ac['review_result']['source'], {
            'kind': 'CODEX_JSONL', 'output_sha256': expected_sha,
        })
        entry = stored['review_disputes'][0]
        self.assertEqual(entry['state'], 'OPEN')
        self.assertEqual(entry['arbiter_claim'], {'reviewer_task': ac['reviewer_task'], 'version': ac['version']})
        self.assertEqual([e['event'] for e in entry['history']], ['ARBITER_CLAIMED', 'ARBITER_COMPLETED'])
        self.assertEqual(entry['history'][0]['actor'], f'pm:{PM_SESSION}')
        self.assertEqual(entry['history'][1]['actor'], 'llmtool')

        # Replay on COMPLETED RULING returns recorded result with no second launch
        replay = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=comment_get,
            create_d6=self.fake_create_d6,
            bind_session=self.fake_bind,
        )
        self.assertEqual(replay['state'], 'COMPLETED')
        self.assertEqual(replay['outcome'], 'WITHDRAWN')
        self.assertEqual(len(self.created_d6), 1)

    def test_refused_codex_output_cases_record_unknown_and_leave_dispute_open(self):
        ruling_ok = _ruling_text(actor='Codex')
        bad_cases = [
            ('VERDICT_WRONG_FIELDS', _codex_jsonl({
                'task_id': TASK, 'run_id': RUN, 'head': HEAD,
                'contract_digest': CONTRACT, 'outcome': 'UPHELD', 'ruling': ruling_ok,
            })),
            ('VERDICT_WRONG_FIELDS', _codex_jsonl({
                'task_id': TASK, 'run_id': RUN, 'head': HEAD, 'outcome': 'UPHELD',
            })),
            ('THREAD_ID_INVALID', _codex_jsonl({
                'task_id': TASK, 'run_id': RUN, 'head': HEAD,
                'outcome': 'UPHELD', 'ruling': ruling_ok,
            }, extra_threads=1)),
            ('OUTPUT_NOT_JSONL', (
                b'{"type":"thread.started","thread_id":"' + THREAD_ID.encode() +
                b'","thread_id":"' + THREAD_ID.encode() + b'"}\n'
            )),
            ('VERDICT_BINDING_MISMATCH', _codex_jsonl({
                'task_id': 'f' * 32, 'run_id': RUN, 'head': HEAD,
                'outcome': 'UPHELD', 'ruling': ruling_ok,
            })),
            ('REVIEW_HEADER_MISMATCH', _codex_jsonl({
                'task_id': TASK, 'run_id': RUN, 'head': HEAD,
                'outcome': 'WITHDRAWN', 'ruling': ruling_ok,
            })),
            ('REVIEW_HEADER_MISMATCH', _codex_jsonl({
                'task_id': TASK, 'run_id': RUN, 'head': HEAD,
                'outcome': 'UPHELD', 'ruling': _ruling_text(actor='Claude'),
            })),
            ('REVIEW_HEADER_MISMATCH', _codex_jsonl({
                'task_id': TASK, 'run_id': RUN, 'head': HEAD,
                'outcome': 'UPHELD', 'ruling': _ruling_text(version=REVIEW_VERSION + 1),
            })),
            ('REVIEW_HEADER_MISMATCH', _codex_jsonl({
                'task_id': TASK, 'run_id': RUN, 'head': HEAD,
                'outcome': 'UPHELD',
                'ruling': (
                    '<!-- activity: type=dispute_ruling actor=Codex target=PM status=UPHELD -->\n'
                    f'Ruling: {FINDING} UPHELD | PR: #{PR} | HEAD: {HEAD}\n'
                    f'{BINDING}\n'
                ),
            })),
            ('REVIEW_HEADER_MISMATCH', _codex_jsonl({
                'task_id': TASK, 'run_id': RUN, 'head': HEAD,
                'outcome': 'UPHELD',
                'ruling': _ruling_text(extra='\nVerdict: PASS | Version: v1 | HEAD: ' + HEAD),
            })),
        ]
        for expected_reason, raw_jsonl in bad_cases:
            with self.subTest(expected_reason=expected_reason):
                self._seed()
                with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/codex'), \
                     patch.object(review_dispatch.subprocess, 'run',
                                  return_value=SimpleNamespace(returncode=0, stdout=raw_jsonl, stderr=b'')), \
                     patch('llmtool.codex_direct_developer.validate_profile'):
                    with self.assertRaises(LLMToolError):
                        task_submission_cli.dispute_arbitrate(
                            self.store, TASK, pr=PR, finding_id=FINDING,
                            current_head=lambda _r, _p: HEAD,
                            github_get=lambda _p: _dispute_comment(),
                            create_d6=self.fake_create_d6,
                            bind_session=self.fake_bind,
                        )
                stored = self.store.get(TASK)
                ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
                self.assertEqual(ac['state'], 'UNKNOWN')
                self.assertEqual(ac['failure_reason'], expected_reason)
                self.assertEqual(stored['review_disputes'][0]['state'], 'OPEN')
                # Replay on UNKNOWN reports ARBITER_OUTCOME_UNKNOWN
                with self.assertRaises(LLMToolError) as replay_err:
                    task_submission_cli.dispute_arbitrate(
                        self.store, TASK, pr=PR, finding_id=FINDING,
                        current_head=lambda _r, _p: HEAD,
                        github_get=lambda _p: _dispute_comment(),
                        create_d6=self.fake_create_d6,
                        bind_session=self.fake_bind,
                    )
                self.assertEqual(replay_err.exception.dispute_code, 'ARBITER_OUTCOME_UNKNOWN')

    def test_claude_launch_to_dispatched_and_replay_pending(self):
        self._seed(
            developer={
                'assignment_id': 'f' * 32, 'selection_source': 'PM',
                'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'codex',
                'provider_type': 'codex-cli', 'model': 'gpt-6.1-sol',
                'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
                'session_id': 'dev-' + ('1' * 32), 'source_sha': HEAD,
                'source_sha_binding': 'D6_REGISTRY_OBSERVED',
                'read_paths': ['tools/a.py'], 'write_paths': ['tools/a.py'],
                'acceptance': ['A1'], 'context_sha256': '1' * 64,
                'egress_basis': 'bounded', 'input_bytes': 10, 'max_tokens': None,
                'artifact_format': 'unified_diff', 'artifact': _DIFF,
                'artifact_sha256': _DIFF_SHA256, 'usage': None, 'error_code': None,
                'created_at': STAMP_1, 'executor_task_id': EXECUTOR,
            },
            claims=[_formal_claim(actor='Codex', account_mode='CODEX_CLI')],
        )
        res = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=lambda _p: _dispute_comment(),
            create_d6=self.fake_create_d6,
            dispatch_session=lambda **_kw: CLAUDE_SESSION,
            bind_session=self.fake_bind,
        )
        self.assertEqual(res['state'], 'DISPATCHED')
        self.assertEqual(res['code'], 'ARBITER_PENDING')
        self.assertEqual(res['reviewer'], 'claude-reviewer')
        self.assertEqual(res['reviewer_session'], CLAUDE_SESSION)
        self.assertEqual(self.bound_sessions[0]['session_id'], CLAUDE_SESSION)
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'DISPATCHED')
        # Replay reports ARBITER_PENDING without launching again
        replay = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=lambda _p: _dispute_comment(),
            create_d6=self.fake_create_d6,
            dispatch_session=lambda **_kw: self.fail('must not relaunch'),
            bind_session=self.fake_bind,
        )
        self.assertEqual(replay['code'], 'ARBITER_PENDING')

    def test_replay_claimed_becomes_unknown(self):
        claim = tdrcr._arbiter_claim(CONTRACT, state='CLAIMED')
        self._seed(
            claims=[_formal_claim(), claim],
            disputes=[dict(
                tdrcr._dispute_entry(),
                evidence_ref=EVIDENCE_REF,
                arbiter_claim={'reviewer_task': claim['reviewer_task'], 'version': claim['version']},
                history=[{'event': 'ARBITER_CLAIMED', 'at': tdrcr.STAMP_2,
                          'actor': f'pm:{PM_SESSION}', 'head_sha': HEAD,
                          'refs': {'reviewer_task': claim['reviewer_task'], 'version': claim['version']}}],
            )],
        )
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=lambda _p: _dispute_comment(),
            )
        self.assertEqual(caught.exception.dispute_code, 'ARBITER_OUTCOME_UNKNOWN')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'UNKNOWN')
        self.assertEqual(stored['review_disputes'][0]['history'][-1]['event'], 'ARBITER_UNKNOWN')

    def test_newer_verdict_before_launch_refuses_before_any_write(self):
        cases = [
            ('completed', [_formal_claim(), _formal_claim(version=2, state='COMPLETED', verdict='PASS')], None),
            ('dispatched_with_event', [_formal_claim(), _formal_claim(version=2, state='DISPATCHED')], True),
            ('dispatched_no_event', [_formal_claim(), _formal_claim(version=2, state='DISPATCHED')], None),
            ('claimed', [_formal_claim(), _formal_claim(version=2, state='CLAIMED')], None),
        ]
        for label, claims, add_event in cases:
            with self.subTest(label=label):
                self._seed(claims=claims)
                if add_event:
                    self.store.record_completion_event(
                        TASK, event_id='9' * 32, run_id=RUN,
                        session_id='review-session-2', source='REVIEWER',
                        source_sha=HEAD, sequence=1, payload_sha256='8' * 64)
                before = self._record_bytes()
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli.dispute_arbitrate(
                        self.store, TASK, pr=PR, finding_id=FINDING,
                        current_head=lambda _r, _p: HEAD,
                        github_get=lambda _p: _dispute_comment(),
                        create_d6=self.fake_create_d6,
                    )
                self.assertEqual(caught.exception.dispute_code, 'DISPUTE_VERDICT_NEWER')
                self.assertEqual(self._record_bytes(), before)
                self.assertEqual(self.created_d6, [])

    def test_event_attribution_allows_unversioned_own_event_and_refuses_legacy_newer(self):
        self._seed()
        self.store.record_completion_event(
            TASK, event_id='9' * 32, run_id=RUN,
            session_id='review-session-1', source='REVIEWER',
            source_sha=HEAD, sequence=1, payload_sha256='8' * 64)
        record = self.store.get(TASK)
        # Disputed verdict's own REVIEWER event (no version key) does not refuse
        claim = task_submission_cli._check_newer_formal_outcome(
            record, pr=PR, head_sha=HEAD, review_version=REVIEW_VERSION)
        self.assertEqual(claim['version'], REVIEW_VERSION)
        # Legacy event with version > review_version or non-int refuses DISPUTE_VERDICT_NEWER
        for bad_ver in (REVIEW_VERSION + 1, '2', True):
            bad_record = dict(record, completion_events=[{
                'source': 'REVIEWER', 'source_sha': HEAD, 'version': bad_ver,
            }])
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._check_newer_formal_outcome(
                    bad_record, pr=PR, head_sha=HEAD, review_version=REVIEW_VERSION)
            self.assertEqual(caught.exception.dispute_code, 'DISPUTE_VERDICT_NEWER')

    def test_head_move_before_launch_refuses_dispute_stale(self):
        self._seed()
        self.pr_head = NEW_HEAD
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=lambda _p: _dispute_comment(),
                create_d6=self.fake_create_d6,
            )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_STALE')

    def test_d6_registration_head_mismatch_refuses_dispute_arbiter_unverified(self):
        def mismatch_create_d6(**kwargs):
            self.fake_create_d6(**kwargs)
            self.d6_records[kwargs['task_id']].result_sha = NEW_HEAD

        # Codex launch path refuses DISPUTE_ARBITER_UNVERIFIED and records UNKNOWN (not COMPLETED)
        self._seed()
        ruling_text = _ruling_text(actor='Codex')
        raw_jsonl = _codex_jsonl({
            'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'outcome': 'UPHELD', 'ruling': ruling_text,
        })
        with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/codex'), \
             patch.object(review_dispatch.subprocess, 'run',
                          return_value=SimpleNamespace(returncode=0, stdout=raw_jsonl, stderr=b'')), \
             patch('llmtool.codex_direct_developer.validate_profile'):
            out = io.StringIO()
            rc = task_submission_cli.run_task_dispute(
                SimpleNamespace(
                    task_id=TASK, pm_session=PM_SESSION,
                    dispute_action='arbitrate', pr=PR, finding=FINDING,
                ),
                store=self.store,
                stdout=out,
                current_head=lambda _r, _p: HEAD,
                github_get=lambda _p: _dispute_comment(),
                create_d6=mismatch_create_d6,
                bind_session=self.fake_bind,
            )
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out.getvalue())['code'], 'DISPUTE_ARBITER_UNVERIFIED')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'UNKNOWN')
        self.assertNotIn('review_result', ac)
        self.assertEqual(stored['review_disputes'][0]['state'], 'OPEN')
        self.assertEqual(stored['review_disputes'][0]['history'][-1]['event'], 'ARBITER_UNKNOWN')

        # Claude launch path refuses DISPUTE_ARBITER_UNVERIFIED and records UNKNOWN (not DISPATCHED)
        self._seed(
            developer={
                'assignment_id': 'f' * 32, 'selection_source': 'PM',
                'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'codex',
                'provider_type': 'codex-cli', 'model': 'gpt-6.1-sol',
                'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
                'session_id': 'dev-' + ('1' * 32), 'source_sha': HEAD,
                'source_sha_binding': 'D6_REGISTRY_OBSERVED',
                'read_paths': ['tools/a.py'], 'write_paths': ['tools/a.py'],
                'acceptance': ['A1'], 'context_sha256': '1' * 64,
                'egress_basis': 'bounded', 'input_bytes': 10, 'max_tokens': None,
                'artifact_format': 'unified_diff', 'artifact': _DIFF,
                'artifact_sha256': _DIFF_SHA256, 'usage': None, 'error_code': None,
                'created_at': STAMP_1, 'executor_task_id': EXECUTOR,
            },
            claims=[_formal_claim(actor='Codex', account_mode='CODEX_CLI')],
        )
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=lambda _p: _dispute_comment(),
                create_d6=mismatch_create_d6,
                dispatch_session=lambda **_kw: CLAUDE_SESSION,
                bind_session=self.fake_bind,
            )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_ARBITER_UNVERIFIED')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'UNKNOWN')
        self.assertEqual(stored['review_disputes'][0]['state'], 'OPEN')
        self.assertEqual(stored['review_disputes'][0]['history'][-1]['event'], 'ARBITER_UNKNOWN')

    def test_screen_refusal_on_codex_ruling_records_blocked_with_empty_text(self):
        self._seed()
        ruling_text = _ruling_text(actor='Codex')
        raw_jsonl = _codex_jsonl({
            'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'outcome': 'UPHELD', 'ruling': ruling_text,
        })

        def screen_selective(text, limit):
            if text == ruling_text:
                raise LLMToolError('WITHHELD')
            return text

        with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/codex'), \
             patch.object(review_dispatch.subprocess, 'run',
                          return_value=SimpleNamespace(returncode=0, stdout=raw_jsonl, stderr=b'')), \
             patch('llmtool.codex_direct_developer.validate_profile'), \
             patch.object(task_submission_cli, '_screen_text', side_effect=screen_selective):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=lambda _p: _dispute_comment(),
                    create_d6=self.fake_create_d6,
                    bind_session=self.fake_bind,
                )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_RULING_WITHHELD')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'COMPLETED')
        self.assertEqual(ac['review_result']['verdict'], 'BLOCKED')
        self.assertEqual(ac['review_result']['review_text'], '')
        self.assertNotIn('outcome', ac['review_result'])
        self.assertEqual(stored['review_disputes'][0]['state'], 'OPEN')

    def test_dispute_text_comment_checks_and_prompt_fencing(self):
        self._seed()
        before = self._record_bytes()
        for bad_comment in (
            _dispute_comment(updated='2026-11-22T23:59:30Z'),
            _dispute_comment(created='2026-11-23T00:01:00Z'),
        ):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=lambda _p, c=bad_comment: c,
                    create_d6=self.fake_create_d6,
                )
            self.assertEqual(caught.exception.dispute_code, 'DISPUTE_TEXT_INVALID')
            self.assertEqual(self._record_bytes(), before)
            self.assertEqual(self.created_d6, [])

        with patch.object(task_submission_cli, '_screen_text', side_effect=LLMToolError('SENSITIVE')):
            with self.assertRaises(LLMToolError):
                task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=lambda _p: _dispute_comment(),
                    create_d6=self.fake_create_d6,
                )
        self.assertEqual(self._record_bytes(), before)

    def test_abandoned_during_run_writes_nothing_and_refuses_dispute_task_abandoned(self):
        self._seed()
        ruling_text = _ruling_text(actor='Codex')
        raw_jsonl = _codex_jsonl({
            'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'outcome': 'UPHELD', 'ruling': ruling_text,
        })

        def abandon_and_return(*_args, **_kwargs):
            self.store.abandon(TASK, pm_session_id=PM_SESSION, reason='cancelled during run')
            return SimpleNamespace(returncode=0, stdout=raw_jsonl, stderr=b'')

        with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/codex'), \
             patch.object(review_dispatch.subprocess, 'run', side_effect=abandon_and_return), \
             patch('llmtool.codex_direct_developer.validate_profile'):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=lambda _p: _dispute_comment(),
                    create_d6=self.fake_create_d6,
                    bind_session=self.fake_bind,
                )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_TASK_ABANDONED')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'CLAIMED')
        with self.assertRaises(LLMToolError) as replay_err:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=lambda _p: _dispute_comment(),
            )
        self.assertEqual(replay_err.exception.dispute_code, 'DISPUTE_TASK_ABANDONED')

    def test_formal_codex_unchanged(self):
        self._seed(claims=[])
        formal_text = (
            '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v1 | HEAD: {HEAD}\n'
            f'{task_completion._FORMAL_ACCEPT}\n'
            f'{BINDING}\n'
        )
        raw_jsonl = _codex_jsonl({
            'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'verdict': 'PASS', 'review': formal_text,
        })
        with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/codex'), \
             patch.object(review_dispatch.subprocess, 'run',
                          return_value=SimpleNamespace(returncode=0, stdout=raw_jsonl, stderr=b'')), \
             patch('llmtool.codex_direct_developer.validate_profile'):
            res = review_dispatch.dispatch_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, version=1, reviewer='codex-reviewer',
                create_d6=self.fake_create_d6, bind_session=self.fake_bind,
                prompt_renderer=lambda *_a: 'PROMPT',
            )
        self.assertEqual(res['verdict'], 'PASS')
        self.assertEqual(res['state'], 'COMPLETED')


if __name__ == '__main__':
    unittest.main()
