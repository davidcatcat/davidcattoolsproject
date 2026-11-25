"""#2087 (PR A3): unit tests for task dispute arbitrate step 12 (posting, verification, RULED)."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import (  # noqa: E402
    dispute_arbiter,
    review_dispatch,
    review_repair,
    review_transcript,
    role_config,
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
HEAD = tdrcr.HEAD
NEW_HEAD = 'd' * 40
EXECUTOR = tdrcr.EXECUTOR
PM_SESSION = tdrcr.PM_SESSION
STAMP_1 = tdrcr.STAMP_1
STAMP_2 = tdrcr.STAMP_2
_, CONTRACT = tdrcr._base_task_record()
PR = tdrcr.PR
FINDING = 'F1'
REVIEW_VERSION = 1
ARBITER_VERSION = 2
CODEX_THREAD = '0123456789abcdef0123456789abcdef'
CLAUDE_SESSION = '00000000-0000-4000-8000-000000000001'
ARBITER_TASK = 'reviewer-0123456789abcdef0123'
DISPUTE_COMMENT_ID = 1001
RULING_COMMENT_ID = 2002
REVIEW_LOGIN = 'review-account'
REPOSITORY = 'davidcatcat/davidcattoolsproject'
EVIDENCE_REF = f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-{DISPUTE_COMMENT_ID}'
BINDING = f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}'
_DIFF = 'diff --git a/tools/a.py b/tools/a.py\n'
_DIFF_SHA256 = hashlib.sha256(_DIFF.encode('utf-8')).hexdigest()


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _ruling_text(*, actor='Codex', outcome='WITHDRAWN', finding=FINDING,
                 pr=PR, version=REVIEW_VERSION, head=HEAD, binding=BINDING, extra=''):
    return (
        f'<!-- activity: type=dispute_ruling actor={actor} target=PM status={outcome} -->\n'
        f'Ruling: {finding} {outcome} | PR: #{pr} | Version: {version} | HEAD: {head}\n'
        f'{binding}\n'
        f'Arbiter reasoning.{extra}'
    )


def _verdict_text(*, actor='Claude', status='FAIL', version=REVIEW_VERSION,
                  head=HEAD, extra=''):
    marker = 'FIX' if status == 'FAIL' else status
    return (
        f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker} -->\n'
        f'Verdict: {status} | Version: v{version} | HEAD: {head}\n'
        f'{BINDING}\n'
        f'Finding: F1 [High] tools/a.py:1 missing guard\n{extra}'
    )


def _codex_jsonl(payload, *, thread_id=CODEX_THREAD):
    events = [
        {'type': 'thread.started', 'thread_id': thread_id},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(payload)}},
    ]
    return ('\n'.join(json.dumps(e) for e in events) + '\n').encode('utf-8')


def _transcript_bytes(entries):
    return ('\n'.join(json.dumps(e) for e in entries) + '\n').encode('utf-8')


def _assistant_entry(text):
    return {
        'type': 'assistant',
        'message': {
            'role': 'assistant',
            'stop_reason': 'end_turn',
            'content': [{'type': 'text', 'text': text}],
        },
    }


def _developer(profile='agy', model='gemini-2.5-pro', native_session='exec-session-1'):
    return {
        'assignment_id': 'f' * 32,
        'selection_source': 'PM',
        'state': 'DEVELOPER_RESPONDED',
        'provider_profile': profile,
        'provider_type': f'{profile}-cli',
        'model': model,
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
        **({'owner_assignment_ref': None} if profile in ('agy', 'claude') else {}),
        'executor_task_id': EXECUTOR,
    }


def _completion(*, root, common_dir, executor_identity='agy'):
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
            'executor_identity': executor_identity,
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


class DisputeArbitrateRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.repo_root = base / 'repo'
        self.common_dir = self.repo_root / '.git'
        self.common_dir.mkdir(parents=True)
        self.exec_root = base / 'exec'
        self.exec_root.mkdir(parents=True)
        self.rev_root = base / 'rev'
        self.rev_root.mkdir(parents=True)
        self.claude_home = base / 'claude-home'
        self.claude_home.mkdir(parents=True)
        self.policy_path = worker_policy_fixture.install(self)

        self.pr_head = HEAD
        self.posted = []
        self.poster_calls = 0
        self.comment_times = {'created_at': '2026-11-23T00:05:00Z',
                              'updated_at': '2026-11-23T00:05:00Z'}
        self.d6_records = {
            EXECUTOR: SimpleNamespace(
                task_id=EXECUTOR, trusted_agent_id='agy', native_session_id='exec-session-1',
                result_sha=HEAD, root=str(self.exec_root), common_dir=str(self.common_dir),
                branch='llmtool/' + EXECUTOR, github_login=REVIEW_LOGIN,
            ),
        }
        self.snapshots = {
            str(self.exec_root): SimpleNamespace(
                head=HEAD, root=str(self.exec_root), common_dir=str(self.common_dir),
                branch='llmtool/' + EXECUTOR, clean=True,
            ),
        }
        self.role_map = {
            'codex-reviewer': {
                'source': 'roles', 'provider': 'codex', 'model': 'gpt-6.1-sol',
                'effort': 'high', 'config_dir': str(base / 'codex-home'), 'account': 'codex-acc',
            },
            'claude-reviewer': {
                'source': 'roles', 'provider': 'claude', 'model': 'claude-sonnet-4-6',
                'config_dir': str(self.claude_home), 'account': 'claude-acc',
            },
        }
        self.agents_rows = [{'sessionId': CLAUDE_SESSION, 'state': 'done'}]

        def fake_github_get(path):
            if path == f'issues/comments/{DISPUTE_COMMENT_ID}':
                return {
                    'id': DISPUTE_COMMENT_ID, 'body': 'Dispute reasoning for F1.',
                    'issue_url': f'https://api.github.com/repos/{REPOSITORY}/issues/{PR}',
                    'created_at': '2026-11-22T23:59:00Z', 'updated_at': '2026-11-22T23:59:00Z',
                }
            if path == f'issues/comments/{RULING_COMMENT_ID}':
                return {
                    'id': RULING_COMMENT_ID, 'body': self.posted[0]['body'],
                    'issue_url': f'https://api.github.com/repos/{REPOSITORY}/issues/{PR}',
                    'created_at': self.comment_times['created_at'],
                    'updated_at': self.comment_times['updated_at'],
                }
            raise AssertionError(f'unexpected github_get: {path}')

        def fake_create_d6(**kwargs):
            tid = kwargs['task_id']
            branch = 'llmtool/' + tid
            self.d6_records[tid] = SimpleNamespace(
                task_id=tid, trusted_agent_id=kwargs['reviewer_key'],
                native_session_id=None, result_sha=kwargs['base_sha'],
                root=str(self.rev_root), common_dir=str(self.common_dir),
                branch=branch, github_login=REVIEW_LOGIN,
            )
            self.snapshots[str(self.rev_root)] = SimpleNamespace(
                head=kwargs['base_sha'], root=str(self.rev_root),
                common_dir=str(self.common_dir), branch=branch, clean=True,
            )

        def fake_bind(**kwargs):
            rec = self.d6_records.get(kwargs['task_id'])
            if rec is not None:
                rec.native_session_id = kwargs['session_id']

        self.fake_github_get = fake_github_get
        self.fake_create_d6 = fake_create_d6
        self.fake_bind = fake_bind
        self.fake_lister = lambda _pr: [dict(c) for c in self.posted]

        def fake_poster(_root, pr_num, body):
            self.poster_calls += 1
            comment = {
                'id': RULING_COMMENT_ID,
                'user': {'login': REVIEW_LOGIN},
                'body': body,
                'pr': pr_num,
            }
            self.posted.append(comment)
            return comment

        self.fake_poster = fake_poster

        real_session_state = review_transcript.session_state
        patches = (
            patch.object(task_completion, '_github_get', side_effect=fake_github_get),
            patch.object(task_completion, '_trusted_slug', return_value=REPOSITORY),
            patch.object(task_completion, 'read_d6_registration',
                         side_effect=lambda tid, *_a, **_kw: self.d6_records[tid]),
            patch.object(task_completion, 'collect_git_snapshot',
                         side_effect=lambda root: self.snapshots[str(root)]),
            patch.object(task_submission_cli, 'result_head_admitted', return_value=True),
            patch.object(review_dispatch, '_review_rules',
                         return_value=('RULES\nRULES_END\n\n', '9' * 64)),
            patch.object(review_dispatch, '_resolve_github_login', return_value=REVIEW_LOGIN),
            patch.object(review_dispatch, '_resolve_reviewer_role',
                         side_effect=lambda k: dict(self.role_map[k])),
            patch.object(role_config, 'resolve_role',
                         side_effect=lambda k, **_kw: dict(self.role_map['claude-reviewer'])),
            patch.object(review_dispatch, '_admit_review_model', side_effect=lambda _k, m: m),
            patch.object(review_dispatch, '_review_default_model',
                         side_effect=lambda k: 'gpt-6.1-sol' if k == 'codex-reviewer' else 'claude-sonnet-4-6'),
            patch.object(dispute_arbiter.review_transcript, 'session_state',
                         side_effect=lambda sid, cdir, **kw: real_session_state(
                             sid, cdir,
                             run=lambda *_a, **_kw: SimpleNamespace(
                                 returncode=0,
                                 stdout=json.dumps(self.agents_rows).encode('utf-8'),
                                 stderr=b''),
                             claude='/usr/bin/claude')),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _seed(self, *, developer=None, claims=None, disputes=None, account_mode='ROLES'):
        temp_store = tempfile.TemporaryDirectory()
        self.addCleanup(temp_store.cleanup)
        self.store = task_store.TaskStore(Path(temp_store.name).resolve() / 'tasks')
        base, cd = tdrcr._base_task_record()
        base['developer'] = _developer() if developer is None else developer
        base['completion'] = _completion(root=self.exec_root, common_dir=self.common_dir)
        formal = tdrcr._formal_claim(cd, version=REVIEW_VERSION, verdict='FAIL', actor='Claude')
        formal['account_mode'] = account_mode
        raw_claims = [formal] if claims is None else list(claims)
        formal_claims = [c for c in raw_claims if 'purpose' not in c]
        ruling_claims = [c for c in raw_claims if 'purpose' in c]
        if formal_claims:
            base['review_dispatch_claim'] = formal_claims
        rec = self.store.create(base)
        default_dispute = dict(tdrcr._dispute_entry(), evidence_ref=EVIDENCE_REF)
        dispute_list = [default_dispute] if disputes is None else disputes
        if dispute_list:
            initial = [{k: v for k, v in d.items() if k not in ('arbiter_claim', 'history')}
                       for d in dispute_list]
            rec = self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=initial)
        for ac in ruling_claims:
            claimed = {k: v for k, v in ac.items() if k not in ('review_result',)}
            claimed['state'] = 'CLAIMED'
            claimed['native_session_id'] = None
            cur = list(rec.get('review_dispatch_claim') or [])
            updates = {'review_dispatch_claim': cur + [claimed]}
            if any('arbiter_claim' in d for d in dispute_list):
                updates['review_disputes'] = dispute_list
            rec = self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']}, **updates)
            if ac['state'] == 'DISPATCHED':
                rec = self.store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=cur + [ac])
            elif ac['state'] == 'COMPLETED':
                if ac.get('reviewer') == 'claude-reviewer':
                    disp = dict(claimed, state='DISPATCHED', native_session_id=ac['native_session_id'])
                    rec = self.store.compare_and_update(
                        TASK, expected={'record_sha256': rec['record_sha256']},
                        review_dispatch_claim=cur + [disp])
                rec = self.store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=cur + [ac])
        return rec

    def _record_bytes(self):
        return (self.store.root / f'{TASK}.json').read_bytes()

    def _seed_completed_codex(self, *, outcome='WITHDRAWN', account_mode='ROLES',
                              reviewer_task=ARBITER_TASK, session=CODEX_THREAD,
                              extra_claim=None):
        _, cd = tdrcr._base_task_record()
        ruling_text = _ruling_text(actor='Codex', outcome=outcome)
        ac = tdrcr._arbiter_claim(
            cd, state='COMPLETED', version=ARBITER_VERSION,
            reviewer='codex-reviewer', outcome=outcome)
        ac['reviewer_task'] = reviewer_task
        ac['native_session_id'] = session
        ac['review_result']['review_text'] = ruling_text
        ac['account_mode'] = account_mode
        if account_mode == 'CODEX_CLI':
            ac.pop('account', None)
        else:
            ac['account'] = 'codex-acc'
        if extra_claim:
            ac.update(extra_claim)
        entry = dict(
            tdrcr._dispute_entry(), evidence_ref=EVIDENCE_REF,
            arbiter_claim={'reviewer_task': ac['reviewer_task'], 'version': ac['version']},
            history=[{
                'event': 'ARBITER_CLAIMED', 'at': STAMP_1,
                'actor': f'pm:{PM_SESSION}', 'head_sha': HEAD,
                'refs': {'reviewer_task': ac['reviewer_task'], 'version': ac['version']},
            }],
        )
        self._seed(claims=[tdrcr._formal_claim(cd), ac], disputes=[entry])
        self.d6_records[reviewer_task] = SimpleNamespace(
            task_id=reviewer_task, trusted_agent_id='codex-reviewer',
            native_session_id=session, result_sha=HEAD,
            root=str(self.rev_root), common_dir=str(self.common_dir),
            branch='llmtool/' + reviewer_task, github_login=REVIEW_LOGIN,
        )
        self.snapshots[str(self.rev_root)] = SimpleNamespace(
            head=HEAD, root=str(self.rev_root), common_dir=str(self.common_dir),
            branch='llmtool/' + reviewer_task, clean=True,
        )
        return ac, ruling_text

    def test_full_arbitrate_run_codex_roles_and_cli_and_claude_roles_and_explicit(self):
        # Codex ROLES and CODEX_CLI
        for mode, source in (('ROLES', 'roles'), ('CODEX_CLI', 'env')):
            with self.subTest(codex_mode=mode):
                self.posted.clear()
                self.poster_calls = 0
                self.role_map['codex-reviewer']['source'] = source
                self._seed()
                ruling_text = _ruling_text(actor='Codex', outcome='WITHDRAWN')
                raw_jsonl = _codex_jsonl({
                    'task_id': TASK, 'run_id': RUN, 'head': HEAD,
                    'outcome': 'WITHDRAWN', 'ruling': ruling_text,
                })
                with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/codex'), \
                     patch.object(review_dispatch.subprocess, 'run',
                                  return_value=SimpleNamespace(returncode=0, stdout=raw_jsonl, stderr=b'')), \
                     patch('llmtool.codex_direct_developer.validate_profile'):
                    res = task_submission_cli.dispute_arbitrate(
                        self.store, TASK, pr=PR, finding_id=FINDING,
                        current_head=lambda _r, _p: HEAD,
                        github_get=self.fake_github_get,
                        create_d6=self.fake_create_d6,
                        bind_session=self.fake_bind,
                        comment_lister=self.fake_lister,
                        comment_poster=self.fake_poster,
                        login_resolver=lambda: REVIEW_LOGIN,
                    )
                self.assertEqual(res['state'], 'RULED')
                self.assertEqual(res['outcome'], 'WITHDRAWN')
                self.assertTrue(res['formal_pass_required'])
                self.assertEqual(self.poster_calls, 1)
                self.assertEqual(self.posted[0]['body'], ruling_text)
                self.assertIn(f'Version: {REVIEW_VERSION}', self.posted[0]['body'])
                stored = self.store.get(TASK)
                entry = stored['review_disputes'][0]
                ruling = entry['ruling']
                self.assertEqual(ruling['path'], 'arbitrate')
                self.assertEqual(ruling['finding_id'], FINDING)
                self.assertEqual(ruling['review_version'], REVIEW_VERSION)
                self.assertEqual(ruling['comment_id'], RULING_COMMENT_ID)
                self.assertEqual(ruling['source_kind'], 'CODEX_JSONL')
                ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
                self.assertEqual(ruling['claim_sha256'], task_store._canonical_claim_sha256(ac))
                self.assertEqual(entry['history'][-1]['event'], 'RULED')

        # Claude ROLES and EXPLICIT (collected by A2b then ruled by step 12)
        for mode, source in (('ROLES', 'roles'), ('EXPLICIT', 'env')):
            with self.subTest(claude_mode=mode):
                self.posted.clear()
                self.poster_calls = 0
                self.role_map['claude-reviewer']['source'] = source
                self._seed(
                    developer=_developer(profile='codex', model='gpt-6.1-sol'),
                    claims=[tdrcr._formal_claim(CONTRACT, actor='Codex')],
                )
                ruling_text = _ruling_text(actor='Claude', outcome='UPHELD')
                raw_transcript = _transcript_bytes([_assistant_entry(ruling_text)])
                dispatched_res = task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=self.fake_github_get,
                    create_d6=self.fake_create_d6,
                    dispatch_session=lambda **_kw: CLAUDE_SESSION,
                    bind_session=self.fake_bind,
                )
                self.assertEqual(dispatched_res['code'], 'ARBITER_PENDING')
                rtask = dispatched_res['reviewer_task']
                proj_dir = self.claude_home / 'projects' / f'proj-{rtask}'
                proj_dir.mkdir(parents=True, exist_ok=True)
                (proj_dir / f'{CLAUDE_SESSION}.jsonl').write_bytes(raw_transcript)

                res = task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=self.fake_github_get,
                    comment_lister=self.fake_lister,
                    comment_poster=self.fake_poster,
                    login_resolver=lambda: REVIEW_LOGIN,
                )
                self.assertEqual(res['state'], 'RULED')
                self.assertEqual(res['outcome'], 'UPHELD')
                self.assertEqual(self.poster_calls, 1)
                self.assertEqual(self.posted[0]['body'], ruling_text)
                stored = self.store.get(TASK)
                self.assertEqual(stored['review_disputes'][0]['ruling']['source_kind'], 'CLAUDE_TRANSCRIPT')

    def test_reviewer_mismatch_and_formal_claim_reviewer_unchanged(self):
        ac, _ = self._seed_completed_codex()
        # Recorded reviewer differs from chosen arbiter -> DISPUTE_RULING_CLAIM_INVALID
        stored = self.store.get(TASK)
        wrong_rev_claim = dict(ac, reviewer='claude-reviewer')
        rec_wrong = dict(stored, review_dispatch_claim=[stored['review_dispatch_claim'][0], wrong_rev_claim])
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._dispute_ruling_claim(
                rec_wrong, stored['review_disputes'][0], pr=PR, head=HEAD,
                arbiter={'reviewer': 'codex-reviewer'},
                reviewer_task=ARBITER_TASK, reviewer_session=CODEX_THREAD,
                arbiter_claim={'reviewer_task': ARBITER_TASK, 'version': ARBITER_VERSION},
            )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_RULING_CLAIM_INVALID')

        # Marker actor differs from recorded reviewer -> DISPUTE_RULING_EVIDENCE_INVALID / OUTPUT_INVALID
        bad_actor_text = _ruling_text(actor='Claude', outcome='WITHDRAWN')
        self.posted = [{'id': RULING_COMMENT_ID, 'user': {'login': REVIEW_LOGIN}, 'body': bad_actor_text}]
        with self.assertRaises(LLMToolError) as caught2:
            task_submission_cli._dispute_ruling_comment(
                stored, stored['review_disputes'][0], pr=PR, head=HEAD,
                finding_id=FINDING, outcome='WITHDRAWN', reviewer='codex-reviewer',
                review_text=bad_actor_text,
                evidence_ref=f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-{RULING_COMMENT_ID}',
                repository=REPOSITORY, github_get=self.fake_github_get,
                review_version=REVIEW_VERSION,
            )
        self.assertEqual(caught2.exception.dispute_code, 'DISPUTE_RULING_EVIDENCE_INVALID')

        # Formal claim still goes through claim_reviewer unchanged
        formal = stored['review_dispatch_claim'][0]
        self.assertEqual(review_repair.claim_reviewer(formal), 'claude-reviewer')

    def test_missing_or_mismatched_claim_identity_fields_refuse(self):
        ac, _ = self._seed_completed_codex()
        stored = self.store.get(TASK)
        entry = stored['review_disputes'][0]
        for key in ('executor_task', 'pr', 'head_sha'):
            bad = dict(ac)
            bad.pop(key)
            rec = dict(stored, review_dispatch_claim=[stored['review_dispatch_claim'][0], bad])
            with self.assertRaises(LLMToolError):
                task_submission_cli._dispute_ruling_claim(
                    rec, entry, pr=PR, head=HEAD, arbiter={'reviewer': 'codex-reviewer'},
                    reviewer_task=ARBITER_TASK, reviewer_session=CODEX_THREAD,
                    arbiter_claim={'reviewer_task': ARBITER_TASK, 'version': ARBITER_VERSION},
                )
        for field, val in (('pr', PR + 1), ('head_sha', NEW_HEAD),
                           ('review_version', REVIEW_VERSION + 1), ('finding_id', 'F2')):
            bad = dict(ac, dispute=dict(ac['dispute'], **{field: val}))
            rec = dict(stored, review_dispatch_claim=[stored['review_dispatch_claim'][0], bad])
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._dispute_ruling_claim(
                    rec, entry, pr=PR, head=HEAD, arbiter={'reviewer': 'codex-reviewer'},
                    reviewer_task=ARBITER_TASK, reviewer_session=CODEX_THREAD,
                    arbiter_claim={'reviewer_task': ARBITER_TASK, 'version': ARBITER_VERSION},
                )
            self.assertEqual(caught.exception.dispute_code, 'DISPUTE_RULING_CLAIM_INVALID')

    def test_receipt_identity_mismatch_refuses_and_formal_unchanged(self):
        ac, ruling_text = self._seed_completed_codex()
        identity = review_dispatch._receipt_identity(TASK, ac, ruling_text)
        self.assertEqual(identity['dispute'], {
            'pr': PR, 'head_sha': HEAD, 'review_version': REVIEW_VERSION, 'finding_id': FINDING,
        })
        # Formal receipt identity unchanged
        formal = self.store.get(TASK)['review_dispatch_claim'][0]
        formal_id = review_dispatch._receipt_identity(TASK, formal, 'formal text')
        self.assertNotIn('dispute', formal_id)

        # Stored receipt for another review_version or finding_id refuses VERDICT_POST_RECEIPT_UNAVAILABLE -> ARBITER_POST_PENDING
        post_dir = review_dispatch._verdict_post_dir(self.store)
        name = review_dispatch._verdict_post_name(TASK, ac)
        for bad_field, bad_val in (('review_version', REVIEW_VERSION + 1), ('finding_id', 'F2')):
            bad_id = dict(identity, dispute=dict(identity['dispute'], **{bad_field: bad_val}))
            review_dispatch._write_receipt(post_dir, name, dict(
                bad_id, state='POSTED', owner='dispatch', attempt='1' * 32,
                started_at=1000.0, updated_at=1000.0, comment_id=RULING_COMMENT_ID,
            ))
            before = self._record_bytes()
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=self.fake_github_get,
                    comment_lister=self.fake_lister,
                    comment_poster=self.fake_poster,
                    login_resolver=lambda: REVIEW_LOGIN,
                )
            self.assertEqual(caught.exception.dispute_code, 'ARBITER_POST_PENDING')
            self.assertEqual(self.poster_calls, 0)
            self.assertEqual(self._record_bytes(), before)

    def test_post_pending_states_write_nothing_and_replay_rules_without_second_post(self):
        ac, ruling_text = self._seed_completed_codex()
        # 1. IN_PROGRESS from caller-owned POSTING receipt
        post_dir = review_dispatch._verdict_post_dir(self.store)
        name = review_dispatch._verdict_post_name(TASK, ac)
        identity = review_dispatch._receipt_identity(TASK, ac, ruling_text)
        review_dispatch._write_receipt(post_dir, name, dict(
            identity, state='POSTING', owner='caller', attempt='1' * 32,
            started_at=1000.0, updated_at=1000.0,
        ))
        before = self._record_bytes()
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self.fake_lister,
                comment_poster=self.fake_poster,
                login_resolver=lambda: REVIEW_LOGIN,
                post_clock=lambda: 1005.0,
            )
        self.assertEqual(caught.exception.dispute_code, 'ARBITER_POST_PENDING')
        self.assertEqual(self._record_bytes(), before)
        self.assertEqual(self.poster_calls, 0)

        # 2. POSTED_UNVERIFIED / missing comment_id on first post -> ARBITER_POST_PENDING, then replay rules without second post
        (post_dir / f'{name}.json').unlink()

        def unverified_poster(_root, pr_num, body):
            self.poster_calls += 1
            # Comment is created on PR (with wrong user login in response so verified=False, no id)
            self.posted.append({
                'id': RULING_COMMENT_ID, 'user': {'login': REVIEW_LOGIN},
                'body': body, 'pr': pr_num,
            })
            return {'body': body, 'user': {'login': 'other-login'}}

        with self.assertRaises(LLMToolError) as caught2:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self.fake_lister,
                comment_poster=unverified_poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught2.exception.dispute_code, 'ARBITER_POST_PENDING')
        self.assertEqual(self._record_bytes(), before)
        self.assertEqual(self.poster_calls, 1)

        # Replay finds comment via lister -> ALREADY_POSTED -> RULED, no second post
        replay = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=self.fake_github_get,
            comment_lister=self.fake_lister,
            comment_poster=unverified_poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )
        self.assertEqual(replay['state'], 'RULED')
        self.assertEqual(self.poster_calls, 1)

    def test_trusted_evidence_ref_and_placeholder_id_screen_regression(self):
        ac, _ = self._seed_completed_codex(reviewer_task=ARBITER_TASK, session=CODEX_THREAD)
        self.assertTrue(task_store.D6_TASK_ID.fullmatch(ARBITER_TASK))
        self.assertTrue(task_store.valid_review_dispute_session(CODEX_THREAD))
        self.assertTrue(task_store.valid_review_dispute_session(CLAUDE_SESSION))
        res = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=self.fake_github_get,
            comment_lister=self.fake_lister,
            comment_poster=self.fake_poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )
        self.assertEqual(res['state'], 'RULED')
        stored = self.store.get(TASK)
        expected_ref = f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-{RULING_COMMENT_ID}'
        self.assertEqual(stored['review_disputes'][0]['ruling']['evidence_ref'], expected_ref)
        self.assertNotEqual(expected_ref, EVIDENCE_REF)

    def test_cas_loss_type_and_non_cas_store_refusal(self):
        self._seed_completed_codex()
        rec = self.store.get(TASK)
        # Direct TaskStore conflict raises TaskStoreConflict specifically
        self.store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            pm_error_code='OTHER_UPDATE')
        with self.assertRaises(task_store.TaskStoreConflict):
            self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                pm_error_code='SECOND')

        # Non-CAS LLMToolError from store is not retried and reports DISPUTE_RULE_WRITE_UNKNOWN
        calls = []

        def fail_non_cas(*_a, **_kw):
            calls.append(1)
            err = LLMToolError('REVIEW_DISPUTE_TRANSITION_INVALID')
            err.task_store_error_code = 'REVIEW_DISPUTE_TRANSITION_INVALID'
            raise err

        with patch.object(self.store, 'compare_and_update', side_effect=fail_non_cas):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=self.fake_github_get,
                    comment_lister=self.fake_lister,
                    comment_poster=self.fake_poster,
                    login_resolver=lambda: REVIEW_LOGIN,
                )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_RULE_WRITE_UNKNOWN')
        self.assertEqual(len(calls), 1)

    def test_disputed_fail_present_ruling_aware_matching(self):
        _, ruling_text = self._seed_completed_codex()
        other_comments = [
            {'id': 10, 'user': {'login': REVIEW_LOGIN}, 'body': _verdict_text(version=REVIEW_VERSION)},
            {'id': 11, 'user': {'login': REVIEW_LOGIN}, 'body': _verdict_text(version=REVIEW_VERSION + 1)},
            {'id': 12, 'user': {'login': REVIEW_LOGIN},
             'body': _verdict_text(version=REVIEW_VERSION, extra='Ruling: F1 UPHELD in prose')},
            {'id': 13, 'user': {'login': REVIEW_LOGIN},
             'body': _ruling_text(actor='Codex', outcome='UPHELD', finding='F2')},
            {'id': 14, 'user': {'login': REVIEW_LOGIN},
             'body': _ruling_text(actor='Codex', outcome='UPHELD', finding=FINDING, version=REVIEW_VERSION + 1)},
        ]
        self.posted.extend(other_comments)

        match0, conflict0, dup0 = review_dispatch._find_posted_ruling(
            self.posted, REVIEW_LOGIN, ruling_text, BINDING,
            finding_id=FINDING, pr=PR, head=HEAD, review_version=REVIEW_VERSION)
        self.assertEqual((match0, conflict0, dup0), (None, False, False))

        # Formal matcher treats same list as VERDICT_POST_CONFLICT
        f_match, f_conflict, f_dup = review_dispatch._find_posted_verdict(
            self.posted, REVIEW_LOGIN, 'formal verdict', BINDING)
        self.assertIsNone(f_match)
        self.assertTrue(f_conflict)
        self.assertFalse(f_dup)

        res = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=self.fake_github_get,
            comment_lister=self.fake_lister,
            comment_poster=self.fake_poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )
        self.assertEqual(res['state'], 'RULED')
        self.assertEqual(self.poster_calls, 1)

        match1, conflict1, dup1 = review_dispatch._find_posted_ruling(
            self.posted, REVIEW_LOGIN, ruling_text, BINDING,
            finding_id=FINDING, pr=PR, head=HEAD, review_version=REVIEW_VERSION)
        self.assertEqual(match1['id'], RULING_COMMENT_ID)
        self.assertFalse(conflict1)
        self.assertFalse(dup1)

        # Direct _ensure_verdict_posted replay returns ALREADY_POSTED with same id
        ac = [c for c in self.store.get(TASK)['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        post_res = review_dispatch._ensure_verdict_posted(
            store=self.store, task=self.store.get(TASK), task_id=TASK, claim=ac, text=ruling_text,
            login_resolver=lambda: REVIEW_LOGIN, root=str(self.exec_root),
            lister=self.fake_lister, poster=self.fake_poster, clock=lambda: 1000.0,
            allowed=lambda: True,
        )
        self.assertEqual(post_res, {'state': 'ALREADY_POSTED', 'comment_id': RULING_COMMENT_ID})
        self.assertEqual(self.poster_calls, 1)

    def test_conflicts_and_duplicates_within_ruling_identity(self):
        legacy_same = (
            '<!-- activity: type=dispute_ruling actor=Codex target=PM status=UPHELD -->\n'
            f'Ruling: {FINDING} UPHELD | PR: #{PR} | HEAD: {HEAD}\n'
            f'{BINDING}\n'
        )
        unparsable = (
            '<!-- activity: type=dispute_ruling actor=Codex target=PM status=UPHELD -->\n'
            'Ruling: malformed line\n'
            f'{BINDING}\n'
        )
        different_same_id = _ruling_text(actor='Codex', outcome='UPHELD')
        for bad_comment in (different_same_id, legacy_same, unparsable):
            with self.subTest(bad_comment=bad_comment[:40]):
                self.posted = [{'id': 99, 'user': {'login': REVIEW_LOGIN}, 'body': bad_comment}]
                self.poster_calls = 0
                self._seed_completed_codex()
                before = self._record_bytes()
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli.dispute_arbitrate(
                        self.store, TASK, pr=PR, finding_id=FINDING,
                        current_head=lambda _r, _p: HEAD,
                        github_get=self.fake_github_get,
                        comment_lister=self.fake_lister,
                        comment_poster=self.fake_poster,
                        login_resolver=lambda: REVIEW_LOGIN,
                    )
                self.assertEqual(caught.exception.dispute_code, 'DISPUTE_RULING_POST_CONFLICT')
                self.assertEqual(self.poster_calls, 0)
                self.assertEqual(self._record_bytes(), before)

        # Duplicate exact copies in list
        self._seed_completed_codex()
        ruling_text = _ruling_text(actor='Codex', outcome='WITHDRAWN')
        self.posted = [
            {'id': 91, 'user': {'login': REVIEW_LOGIN}, 'body': ruling_text},
            {'id': 92, 'user': {'login': REVIEW_LOGIN}, 'body': ruling_text},
        ]
        self.poster_calls = 0
        before = self._record_bytes()
        with self.assertRaises(LLMToolError) as caught_dup:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self.fake_lister,
                comment_poster=self.fake_poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_dup.exception.dispute_code, 'DISPUTE_RULING_POST_CONFLICT')
        self.assertEqual(self.poster_calls, 0)
        self.assertEqual(self._record_bytes(), before)

    def test_edited_or_early_comment_and_session_d6_checks_and_withdrawal_scope(self):
        # Edited comment refuses DISPUTE_RULING_EVIDENCE_INVALID
        self._seed_completed_codex()
        self.comment_times = {'created_at': '2026-11-23T00:05:00Z',
                              'updated_at': '2026-11-23T00:06:00Z'}
        with self.assertRaises(LLMToolError) as caught_ed:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self.fake_lister,
                comment_poster=self.fake_poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_ed.exception.dispute_code, 'DISPUTE_RULING_EVIDENCE_INVALID')

        # Comment posted before opened_at refuses DISPUTE_RULING_EVIDENCE_INVALID
        self.posted.clear()
        self._seed_completed_codex()
        self.comment_times = {'created_at': '2026-11-22T23:50:00Z',
                              'updated_at': '2026-11-22T23:50:00Z'}
        with self.assertRaises(LLMToolError) as caught_early:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self.fake_lister,
                comment_poster=self.fake_poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_early.exception.dispute_code, 'DISPUTE_RULING_EVIDENCE_INVALID')

        # Developer session refuses DISPUTE_ARBITER_NOT_INDEPENDENT
        self.posted.clear()
        self.comment_times = {'created_at': '2026-11-23T00:05:00Z',
                              'updated_at': '2026-11-23T00:05:00Z'}
        self._seed_completed_codex(session='exec-session-1')
        with self.assertRaises(LLMToolError) as caught_dev:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self.fake_lister,
                comment_poster=self.fake_poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_dev.exception.dispute_code, 'DISPUTE_ARBITER_NOT_INDEPENDENT')

        # First-review session refuses DISPUTE_ARBITER_NOT_FRESH
        self.posted.clear()
        self._seed_completed_codex(session=f'formal-session-{REVIEW_VERSION}')
        with self.assertRaises(LLMToolError) as caught_first:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self.fake_lister,
                comment_poster=self.fake_poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_first.exception.dispute_code, 'DISPUTE_ARBITER_NOT_FRESH')

        # Withdrawal version scope and formal_pass_required
        self.posted.clear()
        self._seed_completed_codex(outcome='WITHDRAWN')
        self.assertEqual(review_repair.withdrawn_findings(self.store.get(TASK), PR, HEAD, REVIEW_VERSION), frozenset())
        res = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=self.fake_github_get,
            comment_lister=self.fake_lister,
            comment_poster=self.fake_poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )
        self.assertTrue(res['formal_pass_required'])
        after = self.store.get(TASK)
        self.assertEqual(review_repair.withdrawn_findings(after, PR, HEAD, REVIEW_VERSION), frozenset({FINDING}))
        self.assertEqual(review_repair.withdrawn_findings(after, PR, HEAD, REVIEW_VERSION + 1), frozenset())


if __name__ == '__main__':
    unittest.main()
