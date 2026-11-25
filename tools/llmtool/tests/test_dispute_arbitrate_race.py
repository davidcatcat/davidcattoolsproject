"""#2087 (PR A3): race, freshness-check, and CAS-loser tests for task dispute arbitrate."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
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
NEWER_VERSION = 3
CODEX_THREAD = '0123456789abcdef0123456789abcdef'
CLAUDE_SESSION = '00000000-0000-4000-8000-000000000001'
ARBITER_TASK = 'reviewer-0123456789abcdef0123'
RULING_COMMENT_ID = 2002
REVIEW_LOGIN = 'review-account'
REPOSITORY = 'davidcatcat/davidcattoolsproject'
EVIDENCE_REF = f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-{tdrcr.PR}001'
BINDING = f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _ruling_text(*, actor='Codex', outcome='WITHDRAWN'):
    return (
        f'<!-- activity: type=dispute_ruling actor={actor} target=PM status={outcome} -->\n'
        f'Ruling: {FINDING} {outcome} | PR: #{PR} | Version: {REVIEW_VERSION} | HEAD: {HEAD}\n'
        f'{BINDING}\n'
        'Arbiter reasoning.'
    )


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


def _completion(*, root, common_dir):
    return {
        'state': 'WAITING_EXECUTOR',
        'executor_task_id': EXECUTOR,
        'reviewer_task_id': None,
        'evidence': {
            'task_id': TASK, 'run_id': RUN, 'contract_digest': CONTRACT,
            'execution_mode': 'DIRECT_D6', 'root': str(root),
            'common_dir': str(common_dir), 'branch': 'llmtool/' + EXECUTOR,
            'source_sha': HEAD, 'executor_identity': 'agy',
            'executor_session_ref': 'ref-1',
            'executor_native_session_id': 'exec-session-1',
            'executor_native_session': 'VERIFIED',
        },
        'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
        'pm_response': None, 'pm_recommendation': None,
        'pm_usage': None, 'pm_error_code': None,
    }


class DisputeArbitrateRaceTests(unittest.TestCase):
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
        self.lister_calls = 0
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
        self.d6_records = {}
        self.snapshots = {}

        def fake_github_get(path):
            if path == f'issues/comments/{RULING_COMMENT_ID}':
                return {
                    'id': RULING_COMMENT_ID, 'body': self.posted[0]['body'],
                    'issue_url': f'https://api.github.com/repos/{REPOSITORY}/issues/{PR}',
                    'created_at': '2026-11-23T00:05:00Z', 'updated_at': '2026-11-23T00:05:00Z',
                }
            raise AssertionError(f'unexpected github_get: {path}')

        self.fake_github_get = fake_github_get
        real_session_state = review_transcript.session_state
        patches = (
            patch.object(task_completion, '_github_get', side_effect=fake_github_get),
            patch.object(task_completion, '_trusted_slug', return_value=REPOSITORY),
            patch.object(task_completion, 'read_d6_registration',
                         side_effect=lambda tid, *_a, **_kw: self.d6_records[tid]),
            patch.object(task_completion, 'collect_git_snapshot',
                         side_effect=lambda root: self.snapshots[str(root)]),
            patch.object(review_dispatch, '_resolve_github_login', return_value=REVIEW_LOGIN),
            patch.object(role_config, 'resolve_role',
                         side_effect=lambda k, **_kw: dict(self.role_map['claude-reviewer'])),
            patch.object(dispute_arbiter.review_transcript, 'session_state',
                         side_effect=lambda sid, cdir, **kw: real_session_state(
                             sid, cdir,
                             run=lambda *_a, **_kw: SimpleNamespace(
                                 returncode=0,
                                 stdout=json.dumps([{'sessionId': CLAUDE_SESSION, 'state': 'done'}]).encode('utf-8'),
                                 stderr=b''),
                             claude='/usr/bin/claude')),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _seed_arbiter(self, *, arbiter='codex-reviewer', state='COMPLETED', outcome='WITHDRAWN'):
        temp_store = tempfile.TemporaryDirectory()
        self.addCleanup(temp_store.cleanup)
        self.store = task_store.TaskStore(Path(temp_store.name).resolve() / 'tasks')
        self.posted.clear()
        self.poster_calls = 0
        self.lister_calls = 0
        self.pr_head = HEAD

        base, cd = tdrcr._base_task_record()
        first_actor = 'Claude' if arbiter == 'codex-reviewer' else 'Codex'
        dev_profile = 'agy' if arbiter == 'codex-reviewer' else 'codex'
        base['developer'] = {
            'assignment_id': 'f' * 32, 'selection_source': 'PM',
            'state': 'DEVELOPER_RESPONDED', 'provider_profile': dev_profile,
            'provider_type': f'{dev_profile}-cli', 'model': 'gemini-2.5-pro' if dev_profile == 'agy' else 'gpt-6.1-sol',
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + ('1' * 32), 'source_sha': HEAD,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['tools/a.py'], 'write_paths': ['tools/a.py'],
            'acceptance': ['A1'], 'context_sha256': '1' * 64,
            'egress_basis': 'bounded', 'input_bytes': 10, 'max_tokens': None,
            'artifact_format': 'unified_diff', 'artifact': 'diff --git a/tools/a.py b/tools/a.py\n',
            'artifact_sha256': '1' * 64, 'usage': None, 'error_code': None,
            'created_at': STAMP_1, 'executor_task_id': EXECUTOR,
            **({'owner_assignment_ref': None} if dev_profile == 'agy' else {}),
        }
        base['completion'] = _completion(root=self.exec_root, common_dir=self.common_dir)
        base['review_dispatch_claim'] = [tdrcr._formal_claim(cd, version=REVIEW_VERSION, verdict='FAIL', actor=first_actor)]
        rec = self.store.create(base)

        entry = dict(tdrcr._dispute_entry(), evidence_ref=EVIDENCE_REF)
        rec = self.store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_disputes=[entry])

        actor = 'Codex' if arbiter == 'codex-reviewer' else 'Claude'
        session = CODEX_THREAD if arbiter == 'codex-reviewer' else CLAUDE_SESSION
        ruling_text = _ruling_text(actor=actor, outcome=outcome)
        ac = tdrcr._arbiter_claim(
            cd, state=state, version=ARBITER_VERSION, reviewer=arbiter, outcome=outcome)
        ac['reviewer_task'] = ARBITER_TASK
        if state != 'CLAIMED':
            ac['native_session_id'] = session
        if state == 'COMPLETED':
            ac['review_result']['review_text'] = ruling_text
            ac['review_result']['source'] = {
                'kind': 'CODEX_JSONL' if arbiter == 'codex-reviewer' else 'CLAUDE_TRANSCRIPT',
                'output_sha256': tdrcr.OUTPUT_DIGEST,
            }
        ac['account_mode'] = 'ROLES'
        ac['account'] = 'codex-acc' if arbiter == 'codex-reviewer' else 'claude-acc'

        claimed = {k: v for k, v in ac.items() if k != 'review_result'}
        claimed['state'] = 'CLAIMED'
        claimed['native_session_id'] = None
        entry_ac = dict(
            entry,
            arbiter_claim={'reviewer_task': ARBITER_TASK, 'version': ARBITER_VERSION},
            history=[{
                'event': 'ARBITER_CLAIMED', 'at': STAMP_1,
                'actor': f'pm:{PM_SESSION}', 'head_sha': HEAD,
                'refs': {'reviewer_task': ARBITER_TASK, 'version': ARBITER_VERSION},
            }],
        )
        cur = list(rec['review_dispatch_claim'])
        rec = self.store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=cur + [claimed], review_disputes=[entry_ac])
        if arbiter == 'claude-reviewer':
            disp = dict(claimed, state='DISPATCHED', native_session_id=session, updated_at=STAMP_2)
            entry_disp = dict(
                entry_ac,
                history=entry_ac['history'] + [{
                    'event': 'ARBITER_DISPATCHED', 'at': STAMP_2,
                    'actor': 'llmtool', 'head_sha': HEAD,
                    'refs': {'reviewer_task': ARBITER_TASK, 'version': ARBITER_VERSION},
                }],
            )
            rec = self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=cur + [disp], review_disputes=[entry_disp])
            proj_dir = self.claude_home / 'projects' / f'proj-{ARBITER_TASK}'
            proj_dir.mkdir(parents=True, exist_ok=True)
            (proj_dir / f'{session}.jsonl').write_bytes(_transcript_bytes([_assistant_entry(ruling_text)]))
        if state == 'COMPLETED':
            rec = self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=cur + [ac])

        self.d6_records[ARBITER_TASK] = SimpleNamespace(
            task_id=ARBITER_TASK, trusted_agent_id=arbiter, native_session_id=session,
            result_sha=HEAD, root=str(self.rev_root), common_dir=str(self.common_dir),
            branch='llmtool/' + ARBITER_TASK, github_login=REVIEW_LOGIN,
        )
        self.snapshots[str(self.rev_root)] = SimpleNamespace(
            head=HEAD, root=str(self.rev_root), common_dir=str(self.common_dir),
            branch='llmtool/' + ARBITER_TASK, clean=True,
        )
        return rec, ac

    def _write_newer_verdict(self, form):
        rec = self.store.get(TASK)
        claims = list(rec['review_dispatch_claim'])
        _, cd = tdrcr._base_task_record()
        if form == 'completed':
            newer = tdrcr._formal_claim(
                cd, version=NEWER_VERSION, verdict='PASS', actor='Codex',
                reviewer_task=tdrcr.FORMAL_REVIEWER_2)
            claimed = dict(newer, state='CLAIMED', native_session_id=None)
            claimed.pop('review_result', None)
            rec = self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=claims + [claimed])
            self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=claims + [newer])
        elif form == 'dispatched_with_event':
            newer = tdrcr._formal_claim(
                cd, version=NEWER_VERSION, verdict='PASS', actor='Claude',
                reviewer_task=tdrcr.FORMAL_REVIEWER_2)
            newer.pop('review_result', None)
            claimed = dict(newer, state='CLAIMED', native_session_id=None)
            disp = dict(newer, state='DISPATCHED', native_session_id='review-session-3')
            rec = self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=claims + [claimed])
            self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=claims + [disp])
            self.store.record_completion_event(
                TASK, event_id='9' * 32, run_id=RUN,
                session_id='review-session-3', source='REVIEWER',
                source_sha=HEAD, sequence=1, payload_sha256='8' * 64)
        elif form == 'claimed':
            newer = tdrcr._formal_claim(
                cd, version=NEWER_VERSION, verdict='PASS', actor='Codex',
                reviewer_task=tdrcr.FORMAL_REVIEWER_2)
            newer.pop('review_result', None)
            claimed = dict(newer, state='CLAIMED', native_session_id=None)
            self.store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=claims + [claimed])
        else:
            raise AssertionError(form)

    def _lister(self, _pr):
        self.lister_calls += 1
        return [dict(c) for c in self.posted]

    def _poster(self, _root, pr_num, body):
        self.poster_calls += 1
        comment = {'id': RULING_COMMENT_ID, 'user': {'login': REVIEW_LOGIN}, 'body': body, 'pr': pr_num}
        self.posted.append(comment)
        return comment

    def _record_bytes(self):
        return (self.store.root / f'{TASK}.json').read_bytes()

    def test_newer_verdict_before_replay_and_during_posting_and_between_f3_and_cas(self):
        for arbiter in ('codex-reviewer', 'claude-reviewer'):
            for form in ('completed', 'dispatched_with_event', 'claimed'):
                with self.subTest(phase='before_replay', arbiter=arbiter, form=form):
                    self._seed_arbiter(arbiter=arbiter, state='COMPLETED')
                    self._write_newer_verdict(form)
                    before = self._record_bytes()
                    with self.assertRaises(LLMToolError) as caught:
                        task_submission_cli.dispute_arbitrate(
                            self.store, TASK, pr=PR, finding_id=FINDING,
                            current_head=lambda _r, _p: HEAD,
                            github_get=self.fake_github_get,
                            comment_lister=self._lister,
                            comment_poster=self._poster,
                            login_resolver=lambda: REVIEW_LOGIN,
                        )
                    self.assertEqual(caught.exception.dispute_code, 'DISPUTE_VERDICT_NEWER')
                    self.assertEqual(self.lister_calls, 0)
                    self.assertEqual(self.poster_calls, 0)
                    self.assertEqual(self._record_bytes(), before)

                with self.subTest(phase='before_fence', arbiter=arbiter, form=form):
                    self._seed_arbiter(arbiter=arbiter, state='COMPLETED')

                    def lister_writes_newer(_pr):
                        self.lister_calls += 1
                        self._write_newer_verdict(form)
                        return []

                    with self.assertRaises(LLMToolError) as caught:
                        task_submission_cli.dispute_arbitrate(
                            self.store, TASK, pr=PR, finding_id=FINDING,
                            current_head=lambda _r, _p: HEAD,
                            github_get=self.fake_github_get,
                            comment_lister=lister_writes_newer,
                            comment_poster=self._poster,
                            login_resolver=lambda: REVIEW_LOGIN,
                        )
                    self.assertEqual(caught.exception.dispute_code, 'DISPUTE_VERDICT_NEWER')
                    self.assertEqual(self.poster_calls, 0)
                    self.assertEqual(self.store.get(TASK)['review_disputes'][0]['state'], 'OPEN')
                    post_dir = review_dispatch._verdict_post_dir(self.store)
                    receipt = review_dispatch._read_receipt(post_dir, f'{TASK}-{ARBITER_TASK}')
                    self.assertEqual(receipt['state'], 'FAILED')
                    self.assertEqual(receipt['code'], 'VERDICT_POST_NOT_ALLOWED')

                with self.subTest(phase='during_post', arbiter=arbiter, form=form):
                    self._seed_arbiter(arbiter=arbiter, state='COMPLETED')

                    def poster_writes_newer(_root, pr_num, body):
                        self._write_newer_verdict(form)
                        return self._poster(_root, pr_num, body)

                    with self.assertRaises(LLMToolError) as caught:
                        task_submission_cli.dispute_arbitrate(
                            self.store, TASK, pr=PR, finding_id=FINDING,
                            current_head=lambda _r, _p: HEAD,
                            github_get=self.fake_github_get,
                            comment_lister=self._lister,
                            comment_poster=poster_writes_newer,
                            login_resolver=lambda: REVIEW_LOGIN,
                        )
                    self.assertEqual(caught.exception.dispute_code, 'DISPUTE_VERDICT_NEWER')
                    self.assertEqual(self.poster_calls, 1)
                    self.assertEqual(self.store.get(TASK)['review_disputes'][0]['state'], 'OPEN')
                    # Replay refuses at F1 and never calls poster again
                    with self.assertRaises(LLMToolError) as replay_err:
                        task_submission_cli.dispute_arbitrate(
                            self.store, TASK, pr=PR, finding_id=FINDING,
                            current_head=lambda _r, _p: HEAD,
                            github_get=self.fake_github_get,
                            comment_lister=self._lister,
                            comment_poster=self._poster,
                            login_resolver=lambda: REVIEW_LOGIN,
                        )
                    self.assertEqual(replay_err.exception.dispute_code, 'DISPUTE_VERDICT_NEWER')
                    self.assertEqual(self.poster_calls, 1)

                with self.subTest(phase='between_f3_and_cas', arbiter=arbiter, form=form):
                    self._seed_arbiter(arbiter=arbiter, state='COMPLETED')
                    head_calls = [0]

                    def head_writes_at_f3(_r, _p):
                        head_calls[0] += 1
                        # Call 1 = replay top-level, 2 = F1, 3 = F2, 4 = F3 round 1
                        if head_calls[0] == 4:
                            self._write_newer_verdict(form)
                        return HEAD

                    with self.assertRaises(LLMToolError) as caught:
                        task_submission_cli.dispute_arbitrate(
                            self.store, TASK, pr=PR, finding_id=FINDING,
                            current_head=head_writes_at_f3,
                            github_get=self.fake_github_get,
                            comment_lister=self._lister,
                            comment_poster=self._poster,
                            login_resolver=lambda: REVIEW_LOGIN,
                        )
                    self.assertEqual(caught.exception.dispute_code, 'DISPUTE_VERDICT_NEWER')
                    self.assertEqual(self.store.get(TASK)['review_disputes'][0]['state'], 'OPEN')

        # Claude DISPATCHED claim: collection writes COMPLETED, then F1 refuses DISPUTE_VERDICT_NEWER
        self._seed_arbiter(arbiter='claude-reviewer', state='DISPATCHED')
        self._write_newer_verdict('completed')
        with self.assertRaises(LLMToolError) as caught_c:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self._lister,
                comment_poster=self._poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_c.exception.dispute_code, 'DISPUTE_VERDICT_NEWER')
        self.assertEqual(self.poster_calls, 0)
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'COMPLETED')
        self.assertEqual(stored['review_disputes'][0]['state'], 'OPEN')

    def test_newer_verdict_after_ruled_and_version_scope(self):
        self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED', outcome='WITHDRAWN')
        res = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=self.fake_github_get,
            comment_lister=self._lister,
            comment_poster=self._poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )
        self.assertEqual(res['state'], 'RULED')
        entry_before = self.store.get(TASK)['review_disputes'][0]

        # Newer formal FAIL at N+2 reusing F1
        rec = self.store.get(TASK)
        _, cd = tdrcr._base_task_record()
        newer_fail = tdrcr._formal_claim(
            cd, version=REVIEW_VERSION + 2, verdict='FAIL', actor='Codex',
            reviewer_task=tdrcr.FORMAL_REVIEWER_2)
        claimed = dict(newer_fail, state='CLAIMED', native_session_id=None)
        claimed.pop('review_result', None)
        rec = self.store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=list(rec['review_dispatch_claim']) + [claimed])
        self.store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=list(rec['review_dispatch_claim'])[:-1] + [newer_fail])

        replay = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=self.fake_github_get,
            comment_lister=self._lister,
            comment_poster=self._poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )
        self.assertEqual(replay['state'], 'RULED')
        self.assertEqual(replay['outcome'], 'WITHDRAWN')
        self.assertEqual(self.poster_calls, 1)
        after = self.store.get(TASK)
        self.assertEqual(after['review_disputes'][0], entry_before)
        self.assertEqual(review_repair.withdrawn_findings(after, PR, HEAD, REVIEW_VERSION + 2), frozenset())

    def test_cas_loser_reevaluates_and_concurrent_calls_and_three_conflicts(self):
        # 1. Unrelated write between F3 read and CAS -> second round wins
        self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED')
        head_calls = [0]

        def head_bumps_unrelated(_r, _p):
            head_calls[0] += 1
            if head_calls[0] == 4:
                r = self.store.get(TASK)
                self.store.compare_and_update(
                    TASK, expected={'record_sha256': r['record_sha256']},
                    pm_error_code='UNRELATED_WRITE')
            return HEAD

        res = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=head_bumps_unrelated,
            github_get=self.fake_github_get,
            comment_lister=self._lister,
            comment_poster=self._poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )
        self.assertEqual(res['state'], 'RULED')
        self.assertEqual(self.poster_calls, 1)

        # 2. Two racing calls: poster called once, one RULED written, loser returns winner's result
        self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED')
        results = []
        errors = []
        barrier = threading.Barrier(2)
        lock = threading.Lock()

        def locked_lister(_pr):
            with lock:
                return [dict(c) for c in self.posted]

        def locked_poster(_root, pr_num, body):
            with lock:
                self.poster_calls += 1
                c = {'id': RULING_COMMENT_ID, 'user': {'login': REVIEW_LOGIN}, 'body': body, 'pr': pr_num}
                self.posted.append(c)
                return c

        def worker():
            try:
                barrier.wait(timeout=5)
                r = task_submission_cli.dispute_arbitrate(
                    self.store, TASK, pr=PR, finding_id=FINDING,
                    current_head=lambda _r, _p: HEAD,
                    github_get=self.fake_github_get,
                    comment_lister=locked_lister,
                    comment_poster=locked_poster,
                    login_resolver=lambda: REVIEW_LOGIN,
                )
                results.append(r)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['state'], 'RULED')
        self.assertEqual(self.poster_calls, 1)

        # 3. Three conflicts in a row -> DISPUTE_RULE_CAS_CONFLICT, then replay rules without second post
        self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED')
        head_calls2 = [0]

        def head_bumps_three_times(_r, _p):
            head_calls2[0] += 1
            if head_calls2[0] in (4, 5, 6):
                r = self.store.get(TASK)
                self.store.compare_and_update(
                    TASK, expected={'record_sha256': r['record_sha256']},
                    pm_error_code=f'CONFLICT_{head_calls2[0]}')
            return HEAD

        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=head_bumps_three_times,
                github_get=self.fake_github_get,
                comment_lister=self._lister,
                comment_poster=self._poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_RULE_CAS_CONFLICT')
        self.assertEqual(self.poster_calls, 1)
        self.assertEqual(self.store.get(TASK)['review_disputes'][0]['state'], 'OPEN')

        replay = task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=self.fake_github_get,
            comment_lister=self._lister,
            comment_poster=self._poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )
        self.assertEqual(replay['state'], 'RULED')
        self.assertEqual(self.poster_calls, 1)

    def test_fresh_reads_order_and_event_has_no_version(self):
        self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED')
        events = []
        real_get = self.store.get

        def wrapped_get(tid):
            events.append('get')
            return real_get(tid)

        def wrapped_head(_r, _p):
            events.append('head')
            return HEAD

        with patch.object(self.store, 'get', side_effect=wrapped_get):
            res = task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=wrapped_head,
                github_get=self.fake_github_get,
                comment_lister=self._lister,
                comment_poster=self._poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(res['state'], 'RULED')
        # Top-level (get, head), F1 (get, head), F2 (get, head), F3 (get, head)
        self.assertEqual(events, ['get', 'head', 'get', 'head', 'get', 'head', 'get', 'head'])

        # REVIEWER event stored via record_completion_event has no 'version' key
        self.store.record_completion_event(
            TASK, event_id='9' * 32, run_id=RUN, session_id='review-session-1',
            source='REVIEWER', source_sha=HEAD, sequence=1, payload_sha256='8' * 64)
        stored_ev = self.store.get(TASK)['completion_events'][-1]
        self.assertNotIn('version', stored_ev)

    def test_head_move_withdraw_and_abandon_during_posting_and_ruling_check_writes_nothing(self):
        # 1. Head move at F3
        self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED')
        head_calls = [0]

        def head_moves_at_f3(_r, _p):
            head_calls[0] += 1
            return NEW_HEAD if head_calls[0] >= 4 else HEAD

        with self.assertRaises(LLMToolError) as caught_stale:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=head_moves_at_f3,
                github_get=self.fake_github_get,
                comment_lister=self._lister,
                comment_poster=self._poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_stale.exception.dispute_code, 'DISPUTE_STALE')
        self.assertEqual(self.store.get(TASK)['review_disputes'][0]['state'], 'OPEN')

        # 2. Dispute withdrawn during posting -> F3 refuses DISPUTE_NOT_OPEN
        self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED')

        def poster_withdraws(_root, pr_num, body):
            task_submission_cli.dispute_withdraw(self.store, TASK, pr=PR, finding_id=FINDING, head=HEAD)
            return self._poster(_root, pr_num, body)

        with self.assertRaises(LLMToolError) as caught_w:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self._lister,
                comment_poster=poster_withdraws,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_w.exception.dispute_code, 'DISPUTE_NOT_OPEN')

        # 3. Abandoned during posting -> F3 refuses DISPUTE_TASK_ABANDONED
        self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED')

        def poster_abandons(_root, pr_num, body):
            self.store.abandon(TASK, pm_session_id=PM_SESSION, reason='abandoned during post')
            return self._poster(_root, pr_num, body)

        with self.assertRaises(LLMToolError) as caught_ab:
            task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=self.fake_github_get,
                comment_lister=self._lister,
                comment_poster=poster_abandons,
                login_resolver=lambda: REVIEW_LOGIN,
            )
        self.assertEqual(caught_ab.exception.dispute_code, 'DISPUTE_TASK_ABANDONED')
        self.assertEqual(self.store.get(TASK)['review_disputes'][0]['state'], 'OPEN')

        # 4. _ruling_check writes nothing and matches refusal codes (including SUPERSEDED)
        _, ac = self._seed_arbiter(arbiter='codex-reviewer', state='COMPLETED')
        identity = {'pr': PR, 'head_sha': HEAD, 'review_version': REVIEW_VERSION, 'finding_id': FINDING}
        arbiter_claim = {'reviewer_task': ARBITER_TASK, 'version': ARBITER_VERSION}
        claim_sha = task_store._canonical_claim_sha256(ac)
        before = self._record_bytes()
        rec = self.store.get(TASK)

        superseded_rec = dict(rec, review_disputes=[dict(rec['review_disputes'][0], state='SUPERSEDED')])
        with self.assertRaises(LLMToolError) as caught_sup:
            task_submission_cli._ruling_check(
                superseded_rec, identity=identity, arbiter_claim=arbiter_claim,
                claim_sha256=claim_sha, current_head=lambda _r, _p: HEAD)
        self.assertEqual(caught_sup.exception.dispute_code, 'DISPUTE_SUPERSEDED')

        with self.assertRaises(LLMToolError) as caught_st:
            task_submission_cli._ruling_check(
                rec, identity=identity, arbiter_claim=arbiter_claim,
                claim_sha256=claim_sha, current_head=lambda _r, _p: NEW_HEAD)
        self.assertEqual(caught_st.exception.dispute_code, 'DISPUTE_STALE')
        self.assertEqual(self._record_bytes(), before)


if __name__ == '__main__':
    unittest.main()
