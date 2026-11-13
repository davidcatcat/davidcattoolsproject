"""Tests for ``llmtool ci main-incident scan|show|confirm`` (#1706 R3). No network.

GitHub is a fake read-only ``github_get``; HOME and the state root are a temp
directory. The #1126 fence lookup is mocked (fences have their own tests); the
caller resolution and the ownership check are the real ``pm_caller`` code.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import cli  # noqa: E402
from llmtool import mac_full_due  # noqa: E402
from llmtool import main_incident as mi  # noqa: E402
from llmtool import main_incident_cli as mic  # noqa: E402
from llmtool import main_incident_store as store  # noqa: E402
from llmtool import pm_caller  # noqa: E402

A, B, C, D = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
TEST_ID = 'tests.test_mod.Case.test_x'
FAIL_LINE = 'FAIL: test_x (tests.test_mod.Case)'
CHANGED = 'tools/llmtool/llmtool/x.py'
OWNER_NATIVE = '00000000-0000-4000-8000-000000000001'
OTHER_NATIVE = '00000000-0000-4000-8000-000000000002'
NOW = 1794528000  # 2026-11-13T00:00:00Z
READ_PREFIXES = ('commits', 'actions/workflows/', 'actions/runs/', 'actions/jobs/',
                 'check-runs/', 'pulls/')


class FakeGitHub:
    """A read-only GitHub: every call is one GET path; unknown paths fail."""

    def __init__(self, commits):
        self.commits = list(commits)  # newest first
        self.linux = {}
        self.mac = []  # newest first, as GitHub lists them
        self.jobs = {}
        self.pulls = {}
        self.files = {}
        self.annotations = {}
        self.calls = []
        self._next = 1

    def _id(self):
        self._next += 1
        return self._next

    def linux_run(self, sha, conclusion, *, tests=False):
        run_id = self._id()
        self.linux.setdefault(sha, []).append(
            {'id': run_id, 'name': mi.LINUX_CHECK, 'head_sha': sha, 'status': 'completed',
             'conclusion': conclusion})
        if tests:
            self.annotations[run_id] = [{'message': FAIL_LINE}]
        return run_id

    def mac_run(self, sha, conclusion, *, tests=False):
        run_id = 1000 + self._id()
        self.mac.insert(0, {'id': run_id, 'head_sha': sha, 'head_branch': 'main',
                            'event': 'workflow_dispatch', 'status': 'completed',
                            'conclusion': conclusion})
        jobs = []
        for index, name in enumerate(mac_full_due.JOBS):
            failed = conclusion != 'success' and index == 0
            jobs.append({'id': run_id * 10 + index, 'name': name,
                         'conclusion': 'failure' if failed else 'success',
                         'steps': [{'name': 'Run tests',
                                    'conclusion': 'failure' if failed else 'success'}]})
            if failed and tests:
                self.annotations[run_id * 10 + index] = [{'message': FAIL_LINE}]
        self.jobs[run_id] = jobs
        return run_id

    def passed(self, sha):
        self.linux_run(sha, 'success')
        self.mac_run(sha, 'success')

    def code_failure(self, sha):
        self.linux_run(sha, 'failure', tests=True)
        self.mac_run(sha, 'failure', tests=True)

    def merged(self, sha, pr, files=(CHANGED,)):
        self.pulls[sha] = [{'number': pr, 'merge_commit_sha': sha}]
        self.files[pr] = [{'filename': name} for name in files]

    def _route(self, path):
        if path == mic.COMMITS:
            return [{'sha': sha} for sha in self.commits]
        if path == mac_full_due.RUNS:
            return {'workflow_runs': self.mac}
        for sha in self.commits:
            if path == mic.LINUX_RUNS.format(sha=sha):
                return {'check_runs': self.linux.get(sha, [])}
            if path == f'commits/{sha}/pulls':
                return self.pulls[sha]
        for run_id, jobs in self.jobs.items():
            if path == f'actions/runs/{run_id}/jobs?per_page=100':
                return {'jobs': jobs}
        for pr, files in self.files.items():
            if path == f'pulls/{pr}/files?per_page={mic.MAX_FILES}':
                return files
        for check_id, items in self.annotations.items():
            if path == f'check-runs/{check_id}/annotations?per_page=100':
                return items
        raise KeyError(path)

    def __call__(self, path):
        self.calls.append(path)
        return copy.deepcopy(self._route(path))


def broken(path):
    raise OSError('api down')


def observation(cls, head=C, pr=12):
    rows = [{'pr': pr, 'merge_sha': head}]
    return {
        'observed_at': store.utc_stamp(NOW), 'signal': [mi.LINUX], 'failing_head': head,
        'failing_runs': [{'kind': mi.LINUX, 'id': 5, 'conclusion': 'failure'}],
        'class': cls, 'evidence': [{'code': 'TEST_FAILURE', 'tests': [TEST_ID]}],
        'range': [{'sha': head, 'pr': pr}], 'candidate_rows': rows,
        'attribution': mi.attribute(rows), 'closure_available': False,
    }


class MainIncidentCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = mock.patch.dict(os.environ, {'HOME': self.tmp.name})
        home.start()
        self.addCleanup(home.stop)
        fence = mock.patch.object(pm_caller, 'fence_refusal_code', return_value=None)
        self.fence = fence.start()
        self.addCleanup(fence.stop)
        self.root = Path(self.tmp.name) / 'state' / 'tasks'
        self.state_root = Path(self.tmp.name) / 'pm-state'
        self.owner = pm_caller.resolve_caller_session(OWNER_NATIVE, {})
        self.other = pm_caller.resolve_caller_session(OTHER_NATIVE, {})

    def run_cli(self, argv, github=None, *, session=OWNER_NATIVE, environ=None, now=NOW,
                read_texts=None):
        if session is not None and argv[0] in ('scan', 'confirm'):
            argv = argv + ['--pm-session', session]
        args = cli.parser_for_cli().parse_args(['ci', 'main-incident'] + argv)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = mic.run_main_incident(args, github_get=github, root=self.root,
                                           environ={} if environ is None else environ,
                                           state_root=self.state_root, now=now,
                                           read_texts=read_texts)
        payload = json.loads(out.getvalue())
        if status:
            self.assertEqual(err.getvalue(), f'llmtool: {payload["code"]}\n')
        return status, payload

    def confirm(self, incident, scope='12', reason='revert the failing merge', **kwargs):
        return self.run_cli(['confirm', '--incident', incident, '--scope', scope,
                             '--reason', reason], **kwargs)

    def assertRefused(self, result, code):
        status, payload = result
        self.assertEqual((status, payload['code'], payload['refused']), (1, code, True))

    def single(self):
        github = FakeGitHub([C, A])
        github.passed(A)
        github.code_failure(C)
        github.merged(C, 12)
        return github

    def batch(self):
        github = FakeGitHub([C, B, A])
        github.passed(A)
        github.code_failure(C)
        github.merged(B, 12)
        github.merged(C, 13)
        return github

    def scanned(self, github):
        status, payload = self.run_cli(['scan'], github)
        self.assertEqual((status, payload['code']), (0, mic.CREATED))
        return payload['incident_id']

    def seed(self, cls):
        body = store.new_record(pm_session_id=self.owner, observation=observation(cls),
                                last_good_head=A, created_at=store.utc_stamp(NOW))
        return store.create(body, self.root)['incident_id']

    # scan ------------------------------------------------------------------

    def test_scan_creates_one_code_incident_from_reads_only(self):
        github = self.single()
        status, payload = self.run_cli(['scan'], github)
        self.assertEqual(status, 0)
        self.assertEqual(payload, {
            'code': mic.CREATED, 'incident_id': 'mi-' + A, 'outcome': store.OPEN,
            'class': mi.CODE, 'attribution': mi.SINGLE, 'basis': 'ONLY_CANDIDATE',
            'failing_head': C, 'last_good_head': A})
        record = store.get('mi-' + A, self.root)
        self.assertEqual(record['pm_session_id'], self.owner)
        self.assertEqual(record['signal'], [mi.FULL_MAC, mi.LINUX])
        self.assertEqual((record['confirmations'], record['outcome_events']), ([], []))
        latest, = record['observations']
        self.assertEqual(latest['observed_at'], store.utc_stamp(NOW))
        self.assertEqual(latest['failing_head'], C)
        self.assertEqual(sorted(run['kind'] for run in latest['failing_runs']),
                         [mi.FULL_MAC, mi.LINUX])
        self.assertIn({'code': 'REPRODUCED', 'test': TEST_ID,
                       'runs': sorted(run['id'] for run in latest['failing_runs'])},
                      latest['evidence'])
        self.assertEqual(latest['range'], [{'sha': C, 'pr': 12}])
        row, = latest['candidate_rows']
        self.assertEqual((row['pr'], row['merge_sha'], row['files']), (12, C, [CHANGED]))
        # No file texts were injected: the closure is unknown, never False.
        self.assertIsNone(row['touches_import_closure'])
        self.assertFalse(latest['closure_available'])
        self.assertEqual(latest['attribution']['pr'], 12)
        # Only GET reads went through github_get; nothing else touched GitHub.
        self.assertTrue(github.calls)
        self.assertTrue(all(path.startswith(READ_PREFIXES) for path in github.calls))

    def test_scan_uses_injected_file_texts_for_the_import_closure(self):
        texts = {'tools/llmtool/tests/__init__.py': '',
                 'tools/llmtool/tests/test_mod.py': 'from llmtool import x\n',
                 'tools/llmtool/llmtool/__init__.py': '',
                 CHANGED: 'VALUE = 1\n'}
        seen = []

        def read_texts(sha):
            seen.append(sha)
            return texts

        self.run_cli(['scan'], self.single(), read_texts=read_texts)
        latest = store.get('mi-' + A, self.root)['observations'][0]
        self.assertEqual(seen, [C])
        self.assertTrue(latest['closure_available'])
        self.assertIs(latest['candidate_rows'][0]['touches_import_closure'], True)

    def test_scan_updates_the_same_incident_then_reports_unchanged(self):
        github = FakeGitHub([C, A])
        github.passed(A)
        github.linux_run(C, 'failure')  # no annotations: test IDs unknown
        github.merged(C, 12)
        incident = self.scanned(github)
        first = store.get(incident, self.root)
        self.assertEqual(first['observations'][0]['class'], mi.UNKNOWN)
        github.annotations[github.linux[C][0]['id']] = [{'message': FAIL_LINE}]
        github.mac_run(C, 'failure', tests=True)
        status, payload = self.run_cli(['scan'], github, now=NOW + 60)
        self.assertEqual((status, payload['code'], payload['incident_id'], payload['class']),
                         (0, mic.UPDATED, incident, mi.CODE))
        updated = store.get(incident, self.root)
        self.assertEqual(len(updated['observations']), 2)
        self.assertEqual(updated['observations'][0], first['observations'][0])
        self.assertEqual(updated['observations'][1]['observed_at'], store.utc_stamp(NOW + 60))
        status, payload = self.run_cli(['scan'], github, now=NOW + 120)
        self.assertEqual((status, payload['code'], payload['incident_id']),
                         (0, mic.UNCHANGED, incident))
        self.assertEqual(store.get(incident, self.root), updated)
        self.assertEqual(len(store.list_records(self.root)), 1)

    def test_scan_with_green_main_records_nothing(self):
        github = FakeGitHub([A])
        github.passed(A)
        status, payload = self.run_cli(['scan'], github)
        self.assertEqual((status, payload['code'], payload['last_good_head']), (0, mic.NONE, A))
        self.assertEqual(store.list_records(self.root), [])

    def test_scan_resolves_on_a_newer_trusted_pass(self):
        github = self.single()
        incident = self.scanned(github)
        github.passed(C)  # green reruns on the failing head
        status, payload = self.run_cli(['scan'], github)
        self.assertEqual((status, payload['code'], payload['outcome'], payload['resolved_head']),
                         (0, mic.RESOLVED, store.RESOLVED, C))
        record = store.get(incident, self.root)
        self.assertEqual(record['outcome_events'],
                         [{'outcome': store.RESOLVED, 'at': store.utc_stamp(NOW), 'head': C}])
        self.assertRefused(self.confirm(incident), mic.NOT_OPEN)

    def test_scan_supersedes_when_main_fails_after_a_newer_pass(self):
        github = self.single()
        old = self.scanned(github)
        github.passed(C)
        github.commits.insert(0, D)
        github.code_failure(D)
        github.merged(D, 14)
        status, payload = self.run_cli(['scan'], github)
        self.assertEqual((status, payload['code'], payload['incident_id'], payload['superseded']),
                         (0, mic.CREATED, 'mi-' + C, old))
        self.assertEqual(store.get(old, self.root)['outcome'], store.SUPERSEDED)
        self.assertEqual([item['incident_id'] for item in store.find_open(self.root)], ['mi-' + C])

    def test_scan_unavailable_github_writes_nothing(self):
        status, payload = self.run_cli(['scan'], broken)
        self.assertEqual((status, payload['code']), (2, mic.UNAVAILABLE))
        self.assertEqual(store.list_records(self.root), [])

    def test_scan_by_another_session_cannot_touch_the_incident(self):
        github = self.single()
        incident = self.scanned(github)
        before = store.get(incident, self.root)
        self.assertRefused(self.run_cli(['scan'], github, session=OTHER_NATIVE),
                           pm_caller.NOT_TASK_OWNER)
        github.linux_run(C, 'failure', tests=True)  # new evidence
        self.assertRefused(self.run_cli(['scan'], github, session=OTHER_NATIVE),
                           pm_caller.NOT_TASK_OWNER)
        github.passed(C)
        self.assertRefused(self.run_cli(['scan'], github, session=OTHER_NATIVE),
                           pm_caller.NOT_TASK_OWNER)
        self.assertEqual(store.get(incident, self.root), before)

    def test_scan_caller_comes_from_the_runtime_or_is_refused(self):
        github = self.single()
        self.assertRefused(self.run_cli(['scan'], github, session=None),
                           pm_caller.SESSION_MISSING)
        self.assertEqual(store.list_records(self.root), [])
        status, payload = self.run_cli(['scan'], github, session=None,
                                       environ={'CLAUDE_CODE_SESSION_ID': OWNER_NATIVE})
        self.assertEqual((status, payload['code']), (0, mic.CREATED))
        self.assertEqual(store.get(payload['incident_id'], self.root)['pm_session_id'],
                         self.owner)

    def test_fenced_caller_is_refused(self):
        github = self.single()
        incident = self.scanned(github)
        self.fence.return_value = pm_caller.SESSION_FENCED
        self.assertRefused(self.run_cli(['scan'], github), pm_caller.SESSION_FENCED)
        self.assertRefused(self.confirm(incident), pm_caller.SESSION_FENCED)
        self.assertEqual(store.get(incident, self.root)['confirmations'], [])

    # show ------------------------------------------------------------------

    def test_show_prints_the_record_as_json(self):
        incident = self.scanned(self.single())
        status, payload = self.run_cli(['show', '--incident', incident])
        self.assertEqual(status, 0)
        self.assertEqual(payload, store.get(incident, self.root))

    def test_show_unknown_or_unsafe_incident_is_refused(self):
        self.assertRefused(self.run_cli(['show', '--incident', 'mi-' + D]),
                           store.INCIDENT_RECORD_UNAVAILABLE)
        self.assertRefused(self.run_cli(['show', '--incident', '../escape']),
                           store.INCIDENT_RECORD_INVALID)

    # confirm ---------------------------------------------------------------

    def test_confirm_records_the_owner_confirmation(self):
        incident = self.scanned(self.single())
        before = store.get(incident, self.root)
        status, payload = self.confirm(incident, scope='#12', reason='  revert the merge  ',
                                       now=NOW + 30)
        self.assertEqual((status, payload['code'], payload['incident_id']),
                         (0, mic.CONFIRMED, incident))
        expected = {'pm_session_id': self.owner, 'confirmed_at': store.utc_stamp(NOW + 30),
                    'scope': [12], 'reason': 'revert the merge', 'observation_index': 0,
                    'evidence_sha256': store.observation_digest(before['observations'][0])}
        self.assertEqual(payload['confirmation'], expected)
        record = store.get(incident, self.root)
        self.assertEqual(record['confirmations'], [expected])
        self.assertEqual(payload['record_sha256'], record['record_sha256'])
        self.assertEqual(record['observations'], before['observations'])
        self.assertEqual(record['outcome'], store.OPEN)

    def test_confirm_refuses_unknown_class(self):
        github = FakeGitHub([C, A])
        github.passed(A)
        github.linux_run(C, 'failure')  # no test IDs
        github.merged(C, 12)
        incident = self.scanned(github)
        self.assertEqual(store.get(incident, self.root)['observations'][0]['class'], mi.UNKNOWN)
        self.assertRefused(self.confirm(incident), mic.REVERT_CLASS_NOT_CODE)
        self.assertEqual(store.get(incident, self.root)['confirmations'], [])

    def test_confirm_refuses_env_class(self):
        github = FakeGitHub([C, A])
        github.passed(A)
        github.linux_run(C, 'timed_out')
        github.merged(C, 12)
        incident = self.scanned(github)
        self.assertEqual(store.get(incident, self.root)['observations'][0]['class'], mi.ENV)
        self.assertRefused(self.confirm(incident), mic.REVERT_CLASS_NOT_CODE)
        self.assertEqual(store.get(incident, self.root)['confirmations'], [])

    def test_confirm_refuses_flaky_class(self):
        # scan never claims FLAKY (commits carry no ``changed`` flag), so seed it.
        incident = self.seed(mi.FLAKY)
        self.assertRefused(self.confirm(incident), mic.REVERT_CLASS_NOT_CODE)
        self.assertEqual(store.get(incident, self.root)['confirmations'], [])

    def test_confirm_refuses_one_pr_of_an_unbisected_batch(self):
        incident = self.scanned(self.batch())
        latest = store.get(incident, self.root)['observations'][0]
        self.assertEqual((latest['class'], latest['attribution']['attribution']),
                         (mi.CODE, mi.BATCH))
        self.assertRefused(self.confirm(incident, scope='13'), mic.REVERT_ATTRIBUTION_UNPROVEN)
        self.assertRefused(self.confirm(incident, scope='12'), mic.REVERT_ATTRIBUTION_UNPROVEN)
        self.assertEqual(store.get(incident, self.root)['confirmations'], [])
        status, payload = self.confirm(incident, scope='13, 12')
        self.assertEqual((status, payload['confirmation']['scope']), (0, [12, 13]))

    def test_confirm_refuses_unknown_or_malformed_scope(self):
        incident = self.scanned(self.single())
        self.assertRefused(self.confirm(incident, scope='12,99'), mic.REVERT_SCOPE_UNKNOWN_PR)
        self.assertRefused(self.confirm(incident, scope='twelve'), mic.REVERT_SCOPE_INVALID)
        self.assertRefused(self.confirm(incident, scope=''), mic.REVERT_SCOPE_INVALID)
        self.assertEqual(store.get(incident, self.root)['confirmations'], [])

    def test_confirm_refuses_a_missing_reason(self):
        incident = self.scanned(self.single())
        self.assertRefused(self.confirm(incident, reason=''), mic.REVERT_REASON_MISSING)
        self.assertRefused(self.confirm(incident, reason='   '), mic.REVERT_REASON_MISSING)
        self.assertRefused(self.confirm(incident, reason='line\x07bell'),
                           mic.REVERT_REASON_INVALID)
        self.assertEqual(store.get(incident, self.root)['confirmations'], [])

    def test_confirm_refuses_another_pm_session(self):
        incident = self.scanned(self.single())
        self.assertRefused(self.confirm(incident, session=OTHER_NATIVE),
                           pm_caller.NOT_TASK_OWNER)
        self.assertRefused(self.confirm(incident, session=None,
                                        environ={'CLAUDE_CODE_SESSION_ID': OTHER_NATIVE}),
                           pm_caller.NOT_TASK_OWNER)
        self.assertRefused(self.confirm(incident, session=None), pm_caller.SESSION_MISSING)
        self.assertRefused(self.confirm('mi-' + D), pm_caller.TASK_RECORD_UNAVAILABLE)
        self.assertEqual(store.get(incident, self.root)['confirmations'], [])

    def test_confirmations_and_observations_are_forward_only(self):
        github = self.single()
        incident = self.scanned(github)
        self.assertEqual(self.confirm(incident)[0], 0)
        first = store.get(incident, self.root)
        github.linux_run(C, 'failure', tests=True)  # a failing rerun: new evidence
        status, payload = self.run_cli(['scan'], github, now=NOW + 60)
        self.assertEqual(payload['code'], mic.UPDATED)
        status, payload = self.confirm(incident, reason='confirmed again', now=NOW + 90)
        self.assertEqual((status, payload['confirmation']['observation_index']), (0, 1))
        record = store.get(incident, self.root)
        self.assertEqual(record['observations'][0], first['observations'][0])
        self.assertEqual(record['confirmations'][0], first['confirmations'][0])
        self.assertEqual(len(record['confirmations']), 2)
        for field in store.IMMUTABLE_FIELDS:
            self.assertEqual(record[field], first[field])
        self.assertEqual(record['confirmations'][1]['evidence_sha256'],
                         store.observation_digest(record['observations'][1]))

    # wiring ----------------------------------------------------------------

    def test_ci_parser_and_dispatch_keep_full_mac_due(self):
        parser = cli.parser_for_cli()
        self.assertEqual(parser.parse_args(['ci', 'full-mac-due']).action, 'full-mac-due')
        args = parser.parse_args(['ci', 'main-incident', 'confirm', '--incident', 'mi-' + A,
                                  '--scope', '12', '--reason', 'r'])
        self.assertEqual((args.action, args.incident_action, args.pm_session),
                         ('main-incident', 'confirm', None))
        with mock.patch.object(mic, 'run_main_incident', return_value=0) as incident, \
                mock.patch.object(mac_full_due, 'run_ci', return_value=0) as full:
            self.assertEqual(cli.main(['ci', 'main-incident', 'show', '--incident', 'mi-' + A]), 0)
            self.assertEqual(incident.call_count, 1)
            self.assertEqual(full.call_count, 0)
            self.assertEqual(cli.main(['ci', 'full-mac-due']), 0)
            self.assertEqual((incident.call_count, full.call_count), (1, 1))


if __name__ == '__main__':
    unittest.main()
