"""#2087 (Advisor slice A2-iii-2): unit tests for task dispute arbitrate launch and Codex completion."""

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
    cli,
    review_dispatch,
    task_completion,
    task_store,
    task_submission_cli,
)
from llmtool.errors import LLMToolError  # noqa: E402

import review_trust_fixture  # noqa: E402
import worker_policy_fixture  # noqa: E402


TASK = 'a' * 32
RUN = 'b' * 32
_REQUEST = 'Fix tools/a.py'
_REQUEST_SHA256 = hashlib.sha256(_REQUEST.encode('utf-8')).hexdigest()
_TASK_CONTRACT = task_submission_cli._contract(TASK, _REQUEST_SHA256)
CONTRACT = _TASK_CONTRACT.binding(RUN).contract_digest
_DIFF = 'diff --git a/tools/a.py b/tools/a.py\n'
_DIFF_SHA256 = hashlib.sha256(_DIFF.encode('utf-8')).hexdigest()
HEAD = 'd' * 40
NEW_HEAD = 'e' * 40
EXECUTOR = 'exec-d6-1'
PM_SESSION = 'pm-session-1'
THREAD_ID = '0123456789abcdef0123456789abcdef'
CLAUDE_SESSION = '11111111-1111-4111-8111-111111111111'
PR = 42
FINDING = 'F1'
REVIEW_VERSION = 1
DISPUTE_COMMENT_ID = 1001
REVIEW_LOGIN = 'review-account'
REPOSITORY = 'davidcatcat/davidcattoolsproject'
EVIDENCE_REF = f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-{DISPUTE_COMMENT_ID}'
BINDING = f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}'
FINDINGS = (
    'Finding: F1 [High] tools/a.py:10 missing guard\n'
    '  detail line for F1\n'
    'Finding: F2 [Low] tools/b.py:20 naming\n'
)


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


def _formal_claim(*, version=REVIEW_VERSION, state='COMPLETED', actor='Claude',
                  verdict='FAIL', head=HEAD, account_mode='ROLES',
                  reviewer_task='reviewer-11111111111111111111',
                  session='review-session-1'):
    claim = {
        'executor_task': EXECUTOR,
        'pr': PR,
        'head_sha': head,
        'version': version,
        'state': state,
        'reviewer_task': reviewer_task,
        'native_session_id': None if state == 'CLAIMED' else session,
        'account_mode': account_mode,
        'claimed_at': '2026-11-24T00:00:00Z',
        'updated_at': '2026-11-24T00:05:00Z',
    }
    if state == 'COMPLETED':
        claim['review_result'] = {
            'task_id': TASK,
            'run_id': RUN,
            'contract_digest': CONTRACT,
            'head': head,
            'verdict': verdict,
            'review_text': _verdict_text(actor=actor, status=verdict, head=head, version=version),
        }
    return claim


def _seed_task(*, developer=None, claims=None, disputes=None, completion_events=None):
    record = task_submission_cli._record(
        TASK, RUN, PM_SESSION, 'claude', _REQUEST,
        state='PM_RESPONDED', provider_type='claude-cli',
        model='claude-sonnet-4-6', backend='NO_NATIVE_SESSION_ID_EXPOSED',
        request_sha=_REQUEST_SHA256, contract=_TASK_CONTRACT)
    record['response'] = '{"schema_version": 1, "summary": "ok"}'
    record['developer'] = developer or {
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
        'created_at': '2026-11-24T00:00:00Z',
        'owner_assignment_ref': None,
        'executor_task_id': EXECUTOR,
    }
    record['completion'] = {
        'state': 'WAITING_EXECUTOR',
        'executor_task_id': EXECUTOR,
        'reviewer_task_id': None,
        'evidence': {
            'task_id': TASK,
            'run_id': RUN,
            'contract_digest': CONTRACT,
            'execution_mode': 'DIRECT_D6',
            'root': '/tmp/fake-exec-root',
            'common_dir': '/tmp/fake-repo/.git',
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
    record['review_dispatch_claim'] = [_formal_claim()] if claims is None else claims
    record['review_disputes'] = [{
        'finding_id': FINDING,
        'pr': PR,
        'head_sha': HEAD,
        'review_version': REVIEW_VERSION,
        'severity': 'High',
        'state': 'OPEN',
        'evidence_ref': EVIDENCE_REF,
        'opened_at': '2026-11-24T00:10:00Z',
    }] if disputes is None else disputes
    if completion_events is not None:
        record['completion_events'] = completion_events
    return record


def _dispute_comment(*, body='PM dispute explanation.', created='2026-11-24T00:09:00Z',
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
        self.store_dir = Path(self.temp.name) / 'tasks'
        self.store = task_store.TaskStore(self.store_dir)
        self.repo_root = Path(self.temp.name) / 'repo'
        self.common_dir = self.repo_root / '.git'
        self.common_dir.mkdir(parents=True)
        self.exec_root = Path(self.temp.name) / 'exec'
        self.exec_root.mkdir(parents=True)
        self.rev_root = Path(self.temp.name) / 'rev'
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

        def fake_bind(**kwargs):
            self.bound_sessions.append(kwargs)

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

    def _seed(self, **kwargs):
        record = _seed_task(**kwargs)
        record['completion']['evidence']['common_dir'] = str(self.common_dir)
        record['completion']['evidence']['root'] = str(self.exec_root)
        return self.store._write(record, create=not self.store._path(TASK).exists())

    def _record_bytes(self):
        return self.store._path(TASK).read_bytes()


class ArbitrateParserTests(unittest.TestCase):
    def test_parser_rejects_reviewer_session_outcome_or_evidence_options(self):
        parser = cli.build_parser()
        args = parser.parse_args(['task', 'dispute', 'arbitrate', TASK, '--pr', '42', '--finding', 'F1'])
        self.assertEqual(args.dispute_action, 'arbitrate')
        for bad_opt in ('--reviewer', '--reviewer-task', '--reviewer-session',
                        '--outcome', '--evidence-ref'):
            with patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args([
                        'task', 'dispute', 'arbitrate', TASK,
                        '--pr', '42', '--finding', 'F1', bad_opt, 'val',
                    ])
                self.assertEqual(caught.exception.code, 2)


class ArbitrateLaunchTests(_ArbitrateLaunchCase):
    def test_disallowed_policy_refuses_dispute_arbiter_unavailable_before_any_write(self):
        # Claude Developer + Codex Reviewer -> chooses claude-reviewer; pause claude-reviewer.
        self._seed(
            developer={
                'assignment_id': 'f' * 32, 'selection_source': 'PM',
                'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude',
                'provider_type': None, 'model': None,
                'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
                'session_id': 'dev-' + ('1' * 32), 'source_sha': HEAD,
                'source_sha_binding': 'D6_REGISTRY_OBSERVED',
                'read_paths': ['tools/a.py'], 'write_paths': ['tools/a.py'],
                'acceptance': ['A1'], 'context_sha256': '1' * 64,
                'egress_basis': 'bounded', 'input_bytes': 10, 'max_tokens': None,
                'artifact_format': 'unified_diff', 'artifact': None,
                'artifact_sha256': None, 'usage': None, 'error_code': None,
                'created_at': '2026-11-24T00:00:00Z',
                'owner_assignment_ref': None, 'executor_task_id': EXECUTOR,
                'claude_result': {
                    'executor_task_id': EXECUTOR, 'head_sha': HEAD,
                    'recorded_at': '2026-11-24T00:00:00Z',
                },
            },
            claims=[_formal_claim(actor='Codex', account_mode='CODEX_CLI')],
        )
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
                'created_at': '2026-11-24T00:00:00Z', 'executor_task_id': EXECUTOR,
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
        claim = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 2,
            'state': 'CLAIMED', 'reviewer_task': 'reviewer-22222222222222222222',
            'native_session_id': None, 'account_mode': 'ROLES',
            'claimed_at': '2026-11-24T00:15:00Z', 'updated_at': '2026-11-24T00:15:00Z',
            'purpose': 'dispute_ruling',
            'dispute': {
                'pr': PR, 'finding_id': FINDING, 'head_sha': HEAD,
                'review_version': REVIEW_VERSION, 'dispute_text_sha256': '8' * 64,
            },
            'reviewer': 'codex-reviewer',
        }
        self._seed(
            claims=[_formal_claim(), claim],
            disputes=[{
                'finding_id': FINDING, 'pr': PR, 'head_sha': HEAD,
                'review_version': REVIEW_VERSION, 'severity': 'High',
                'state': 'OPEN', 'evidence_ref': EVIDENCE_REF,
                'opened_at': '2026-11-24T00:10:00Z',
                'arbiter_claim': {'reviewer_task': claim['reviewer_task'], 'version': 2},
                'history': [{'event': 'ARBITER_CLAIMED', 'at': '2026-11-24T00:15:00Z',
                             'actor': f'pm:{PM_SESSION}', 'head_sha': HEAD,
                             'refs': {'reviewer_task': claim['reviewer_task'], 'version': 2}}],
            }],
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
        record = self._seed()
        with patch.object(
            task_completion, 'read_d6_registration',
            return_value=SimpleNamespace(
                task_id='arbiter-d6', trusted_agent_id='codex-reviewer',
                native_session_id=THREAD_ID, result_sha=NEW_HEAD,
                root=str(self.rev_root), common_dir=str(self.common_dir),
                branch='llmtool/arbiter-d6', github_login=REVIEW_LOGIN,
            ),
        ):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._dispute_arbiter_registration(
                    record, reviewer='codex-reviewer', reviewer_task='arbiter-d6',
                    reviewer_session=THREAD_ID, head=HEAD)
            self.assertEqual(caught.exception.dispute_code, 'DISPUTE_ARBITER_UNVERIFIED')

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
            _dispute_comment(updated='2026-11-24T00:09:30Z'),
            _dispute_comment(created='2026-11-24T00:11:00Z'),
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
        formal_text = _verdict_text(actor='Codex', status='PASS', head=HEAD, version=1)
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
