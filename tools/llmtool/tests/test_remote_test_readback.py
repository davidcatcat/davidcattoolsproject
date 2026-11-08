"""#1257 R2a2: hosted remote-test job trust, artifact read-back and result mapping.

Every GitHub read goes through injected ``get`` / ``get_bytes`` fakes. The data
is synthetic: placeholder SHAs, ids and module names only.
"""

import copy
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch

from llmtool import github_identity, remote_test
from llmtool.errors import LLMToolError


SHA = 'a' * 40
WORKFLOW_SHA = 'b' * 40
NONCE = '0123456789abcdef0123456789abcdef'
OTHER_NONCE = 'fedcba9876543210fedcba9876543210'
MODULES = ('test_alpha',)
RUN_ID = 101
JOB_ID = 202
CHECK_ID = 303
ARTIFACT_ID = 404

JOBS_PATH = f'actions/runs/{RUN_ID}/jobs?filter=latest&per_page=100'
CHECK_PATH = f'check-runs/{CHECK_ID}'
ARTIFACTS_PATH = f'actions/runs/{RUN_ID}/artifacts?per_page=100'
ZIP_PATH = f'actions/artifacts/{ARTIFACT_ID}/zip'


def make_run(**changes):
    run = {'id': RUN_ID, 'run_attempt': 1, 'status': 'completed',
           'conclusion': 'success', 'head_sha': WORKFLOW_SHA,
           'path': '.github/workflows/llmtool-remote-test.yml'}
    run.update(changes)
    return run


def make_job(**changes):
    job = {'id': JOB_ID, 'run_id': RUN_ID, 'name': 'remote-test',
           'status': 'completed', 'conclusion': 'success',
           'head_sha': WORKFLOW_SHA, 'labels': ['ubuntu-24.04'],
           'runner_group_name': 'GitHub Actions',
           'check_run_url': ('https://api.github.com/repos/example/project/'
                             f'check-runs/{CHECK_ID}')}
    job.update(changes)
    return job


def make_check_run(**changes):
    check = {'id': CHECK_ID, 'head_sha': WORKFLOW_SHA, 'app': {'id': 15368}}
    check.update(changes)
    return check


def make_receipt(result='PASS'):
    receipt = {
        'result': 'PASS', 'exit_code': 0, 'source_sha': SHA, 'tree': 'c' * 40,
        'fetch_head': SHA, 'modules': list(MODULES), 'identity': {'uid': 65534},
        'started_at': '2026-11-09T00:00:00Z', 'ended_at': '2026-11-09T00:01:00Z',
        'elapsed_s': 1.5, 'ran_total': 3, 'final': ['OK'], 'failures': [],
        'log_bytes': 10, 'log_sha256': 'd' * 64, 'remote_log_path': None,
    }
    if result == 'FAIL':
        receipt.update(result='FAIL', exit_code=1, final=['FAILED (failures=1)'],
                       failures=['test_alpha'])
    elif result == 'TIMEOUT':
        receipt.update(result='TIMEOUT', exit_code=124, final=[],
                       failures=['test_alpha'])
    elif result == 'SHA_MISMATCH':
        receipt.update(result='SHA_MISMATCH', exit_code=41, fetch_head='e' * 40,
                       tree=None, ran_total=0, final=[])
    return receipt


def receipt_bytes(result='PASS', **changes):
    receipt = make_receipt(result)
    receipt.update(changes)
    return json.dumps(receipt, separators=(',', ':'), sort_keys=True).encode()


def make_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for member, data in members:
            archive.writestr(member, data)
    return buffer.getvalue()


def make_artifact(size, **changes):
    artifact = {'id': ARTIFACT_ID, 'name': 'remote-test-receipt-' + NONCE,
                'expired': False, 'size_in_bytes': size,
                'workflow_run': {'id': RUN_ID}}
    artifact.update(changes)
    return artifact


class FakeGet:
    """Serve exact API paths from a dict; unknown paths raise like an unreadable read."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class HostedConstantsTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(remote_test._HOSTED_JOB_NAME, 'remote-test')
        self.assertEqual(remote_test._HOSTED_LABELS, ('ubuntu-24.04',))
        self.assertEqual(remote_test._HOSTED_RUNNER_GROUP, 'GitHub Actions')
        self.assertEqual(remote_test._HOSTED_APP_ID, 15368)
        self.assertEqual(remote_test._HOSTED_ARTIFACT_PREFIX, 'remote-test-receipt-')
        self.assertEqual(remote_test._HOSTED_WORKFLOW_PATH,
                         '.github/workflows/llmtool-remote-test.yml')
        self.assertEqual(remote_test._HOSTED_ZIP_LIMIT, 65536)
        self.assertEqual(remote_test._HOSTED_MEMBER_LIMIT, 32768)
        self.assertIsNotNone(remote_test._NONCE.fullmatch(NONCE))
        self.assertIsNone(remote_test._NONCE.fullmatch(NONCE.upper()))
        self.assertIsNone(remote_test._NONCE.fullmatch(NONCE[:-1]))

    def test_actors_come_from_github_identity(self):
        self.assertEqual(remote_test._HOSTED_ACTORS,
                         (github_identity.owner_login(), github_identity.ai_login()))


class HostedGetBytesTest(unittest.TestCase):
    def run_helper(self, script):
        real_popen = subprocess.Popen
        calls = []

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return real_popen([sys.executable, '-I', '-c', script], **kwargs)

        with patch.dict(os.environ, {}), \
                patch.object(remote_test.shutil, 'which', return_value='/opt/example/gh'), \
                patch.object(remote_test, '_trusted_slug', return_value='example/project'), \
                patch.object(remote_test.subprocess, 'Popen', side_effect=fake_popen):
            os.environ.pop('GH_CONFIG_DIR', None)
            try:
                return remote_test._hosted_get_bytes(ZIP_PATH), calls
            except LLMToolError as exc:
                return exc, calls

    def test_fixed_argv_env_and_closed_stdin(self):
        raw, calls = self.run_helper('import sys; sys.stdout.buffer.write(b"PK-small")')
        self.assertEqual(raw, b'PK-small')
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv, [
            '/opt/example/gh', 'api', '--method', 'GET',
            '-H', 'Accept: application/vnd.github+json',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            'repos/example/project/' + ZIP_PATH])
        self.assertIs(kwargs['shell'], False)
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(set(kwargs['env']), {
            'PATH', 'HOME', 'LC_ALL', 'GH_PROMPT_DISABLED',
            'GH_NO_UPDATE_NOTIFIER', 'NO_COLOR'})
        self.assertEqual(kwargs['env']['PATH'], remote_test._GH_PATH)

    def test_output_is_bounded_to_limit_plus_one(self):
        raw, _ = self.run_helper('import sys; sys.stdout.buffer.write(b"x" * 200000)')
        self.assertEqual(len(raw), remote_test._HOSTED_ZIP_LIMIT + 1)

    def test_failed_command_raises(self):
        result, _ = self.run_helper('import sys; sys.stdout.buffer.write(b"PK"); sys.exit(3)')
        self.assertIsInstance(result, LLMToolError)

    def test_only_artifact_zip_paths_are_accepted(self):
        with patch.object(remote_test.subprocess, 'Popen') as popen:
            for path in ('actions/artifacts/12/zip?x=1', 'actions/runs/1/artifacts',
                         'actions/artifacts/abc/zip', '../actions/artifacts/1/zip',
                         'actions/artifacts//zip', 'actions/artifacts/1/zip/', None, 12):
                with self.assertRaises(ValueError, msg=path):
                    remote_test._hosted_get_bytes(path)
            popen.assert_not_called()


class HostedJobTest(unittest.TestCase):
    def fake(self, jobs=None, check=None):
        return FakeGet({
            JOBS_PATH: {'total_count': 1, 'jobs': [make_job()] if jobs is None else jobs},
            CHECK_PATH: make_check_run() if check is None else check,
        })

    def test_trusted_completed_job(self):
        get = self.fake()
        self.assertEqual(remote_test._hosted_job(make_run(), get=get), make_job())
        self.assertEqual(get.calls, [JOBS_PATH, CHECK_PATH])

    def test_pending_job_is_returned_without_trust_read(self):
        pending = make_job(status='queued', conclusion=None, runner_group_name=None)
        get = self.fake(jobs=[pending])
        self.assertEqual(remote_test._hosted_job(make_run(status='queued'), get=get), pending)
        self.assertEqual(get.calls, [JOBS_PATH])

    def test_other_jobs_are_ignored(self):
        get = self.fake(jobs=[make_job(id=9, name='setup'), make_job()])
        self.assertEqual(remote_test._hosted_job(make_run(), get=get), make_job())

    def test_missing_job(self):
        cases = {
            'none': self.fake(jobs=[]),
            'other name only': self.fake(jobs=[make_job(name='remote-test-x')]),
            'two named': self.fake(jobs=[make_job(), make_job(id=203)]),
            'wrong run': self.fake(jobs=[make_job(run_id=999)]),
            'bool run id': self.fake(jobs=[make_job(run_id=True)]),
            'bad job id': self.fake(jobs=[make_job(id='202')]),
            'non-dict job': self.fake(jobs=[make_job(), 'job']),
            'unreadable': FakeGet({JOBS_PATH: LLMToolError('unreadable')}),
            'not a dict': FakeGet({JOBS_PATH: ['jobs']}),
            'no jobs key': FakeGet({JOBS_PATH: {'total_count': 0}}),
        }
        for name, get in cases.items():
            with self.subTest(name):
                self.assertEqual(remote_test._hosted_job(make_run(), get=get),
                                 'HOSTED_JOB_MISSING')
        for run in (None, make_run(id=0), make_run(id=True), make_run(head_sha='short')):
            with self.subTest(run=run):
                self.assertEqual(remote_test._hosted_job(run, get=self.fake()),
                                 'HOSTED_JOB_MISSING')

    def test_untrusted_runner(self):
        cases = {
            'self-hosted label': self.fake(jobs=[make_job(labels=['self-hosted'])]),
            'extra label': self.fake(jobs=[make_job(labels=['ubuntu-24.04', 'linux'])]),
            'other image': self.fake(jobs=[make_job(labels=['ubuntu-22.04'])]),
            'labels text': self.fake(jobs=[make_job(labels='ubuntu-24.04')]),
            'group': self.fake(jobs=[make_job(runner_group_name='Default')]),
            'no group': self.fake(jobs=[make_job(runner_group_name=None)]),
            'job head': self.fake(jobs=[make_job(head_sha='f' * 40)]),
            'no check url': self.fake(jobs=[make_job(check_run_url=None)]),
            'odd check url': self.fake(jobs=[make_job(
                check_run_url='https://example.invalid/check-runs/303')]),
            'app': self.fake(check=make_check_run(app={'id': 999})),
            'app bool': self.fake(check=make_check_run(app={'id': True})),
            'no app': self.fake(check=make_check_run(app=None)),
            'check head': self.fake(check=make_check_run(head_sha='f' * 40)),
            'check id': self.fake(check=make_check_run(id=304)),
            'check unreadable': FakeGet({
                JOBS_PATH: {'jobs': [make_job()]},
                CHECK_PATH: LLMToolError('unreadable')}),
        }
        for name, get in cases.items():
            with self.subTest(name):
                self.assertEqual(remote_test._hosted_job(make_run(), get=get),
                                 'HOSTED_RUNNER_UNTRUSTED')


class HostedArtifactTest(unittest.TestCase):
    def fakes(self, zipped=None, artifacts=None, **changes):
        zipped = make_zip([('receipt.json', receipt_bytes())]) if zipped is None else zipped
        if artifacts is None:
            artifacts = [make_artifact(len(zipped), **changes)]
        get = FakeGet({ARTIFACTS_PATH: {'total_count': len(artifacts), 'artifacts': artifacts}})
        get_bytes = FakeGet({ZIP_PATH: zipped})
        return get, get_bytes

    def read(self, get, get_bytes, run_id=RUN_ID, nonce=NONCE):
        return remote_test._hosted_artifact(run_id, nonce, get=get, get_bytes=get_bytes)

    def test_valid_artifact_is_read_in_memory(self):
        get, get_bytes = self.fakes()
        with patch('builtins.open', side_effect=AssertionError('disk write')), \
                patch('os.open', side_effect=AssertionError('disk write')):
            raw = self.read(get, get_bytes)
        self.assertEqual(raw, receipt_bytes())
        self.assertEqual(get.calls, [ARTIFACTS_PATH])
        self.assertEqual(get_bytes.calls, [ZIP_PATH])

    def test_listing_refusals(self):
        size = len(make_zip([('receipt.json', receipt_bytes())]))
        cases = {
            'none': self.fakes(artifacts=[]),
            'misnamed': self.fakes(name='remote-test-receipt-' + OTHER_NONCE),
            'prefix only': self.fakes(name='remote-test-receipt-'),
            'two named': self.fakes(artifacts=[make_artifact(size),
                                               make_artifact(size, id=405)]),
            'expired': self.fakes(expired=True),
            'expired unknown': self.fakes(expired=None),
            'oversized listing': self.fakes(size_in_bytes=65537),
            'zero size': self.fakes(size_in_bytes=0),
            'bad id': self.fakes(id='404'),
            'other run': self.fakes(workflow_run={'id': 999}),
            'no run': self.fakes(workflow_run=None),
            'non-dict entry': self.fakes(artifacts=['artifact']),
        }
        for name, (get, get_bytes) in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.read(get, get_bytes))
                self.assertEqual(get_bytes.calls, [])

    def test_unreadable_reads(self):
        get, _ = self.fakes()
        self.assertIsNone(self.read(FakeGet({ARTIFACTS_PATH: LLMToolError('x')}),
                                    FakeGet({})))
        self.assertIsNone(self.read(FakeGet({ARTIFACTS_PATH: ['x']}), FakeGet({})))
        self.assertIsNone(self.read(get, FakeGet({ZIP_PATH: LLMToolError('x')})))

    def test_invalid_arguments_make_no_read(self):
        get, get_bytes = self.fakes()
        for run_id, nonce in ((0, NONCE), (True, NONCE), ('101', NONCE),
                              (RUN_ID, NONCE.upper()), (RUN_ID, None)):
            with self.subTest(run_id=run_id, nonce=nonce):
                self.assertIsNone(self.read(get, get_bytes, run_id=run_id, nonce=nonce))
        self.assertEqual(get.calls, [])

    def test_oversized_download(self):
        zipped = make_zip([('receipt.json', receipt_bytes())])
        get, _ = self.fakes(zipped=zipped)
        oversized = zipped + b'\0' * (65537 - len(zipped))
        self.assertIsNone(self.read(get, FakeGet({ZIP_PATH: oversized})))
        self.assertIsNone(self.read(get, FakeGet({ZIP_PATH: b''})))
        self.assertIsNone(self.read(get, FakeGet({ZIP_PATH: 'text'})))

    def test_member_refusals(self):
        symlink = io.BytesIO()
        with zipfile.ZipFile(symlink, 'w') as archive:
            info = zipfile.ZipInfo('receipt.json')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b'target')
        cases = {
            'two members': make_zip([('receipt.json', receipt_bytes()),
                                     ('extra.json', b'{}')]),
            'misnamed member': make_zip([('other.json', receipt_bytes())]),
            'nested member': make_zip([('dir/receipt.json', receipt_bytes())]),
            'directory': make_zip([('receipt.json/', b'')]),
            'empty member': make_zip([('receipt.json', b'')]),
            'member too large': make_zip([('receipt.json', b' ' * 32769)]),
            'symlink': symlink.getvalue(),
            'not a zip': b'not a zip archive',
            'no members': make_zip([]),
        }
        for name, zipped in cases.items():
            with self.subTest(name):
                get, get_bytes = self.fakes(zipped=zipped)
                self.assertIsNone(self.read(get, get_bytes))

    def test_member_at_limit_is_accepted(self):
        data = b' ' * 32768
        get, get_bytes = self.fakes(zipped=make_zip([('receipt.json', data)]))
        self.assertEqual(self.read(get, get_bytes), data)

    def test_regular_file_mode_is_accepted(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            info = zipfile.ZipInfo('receipt.json')
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, receipt_bytes())
        get, get_bytes = self.fakes(zipped=buffer.getvalue())
        self.assertEqual(self.read(get, get_bytes), receipt_bytes())


class HostedOutcomeTest(unittest.TestCase):
    def outcome(self, run, job, raw):
        return remote_test._hosted_outcome(run, job, raw, sha=SHA, modules=MODULES)

    def completed(self, conclusion, job_conclusion=None):
        return (make_run(conclusion=conclusion),
                make_job(conclusion=job_conclusion or conclusion))

    def test_success_with_pass_receipt_is_the_only_pass(self):
        run, job = self.completed('success')
        kind, receipt = self.outcome(run, job, receipt_bytes('PASS'))
        self.assertEqual(kind, 'RECEIPT')
        self.assertEqual(receipt['result'], 'PASS')
        self.assertEqual(receipt['source_sha'], SHA)

    def test_agreeing_non_pass_receipts(self):
        for conclusion, result in (('failure', 'FAIL'), ('failure', 'SHA_MISMATCH'),
                                   ('failure', 'TIMEOUT'), ('timed_out', 'TIMEOUT')):
            with self.subTest(conclusion=conclusion, result=result):
                run, job = self.completed(conclusion)
                kind, receipt = self.outcome(run, job, receipt_bytes(result))
                self.assertEqual(kind, 'RECEIPT')
                self.assertEqual(receipt['result'], result)

    def test_pending_runs(self):
        for status in ('queued', 'in_progress', 'waiting', 'requested', 'pending'):
            for job in ('HOSTED_JOB_MISSING', make_job(status=status, conclusion=None)):
                with self.subTest(status=status, job=job):
                    self.assertEqual(
                        self.outcome(make_run(status=status, conclusion=None), job, None),
                        ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING'))

    def test_timed_out_without_valid_receipt(self):
        run, job = self.completed('timed_out')
        for raw in (None, b'{}', receipt_bytes('TIMEOUT', source_sha='f' * 40)):
            with self.subTest(raw=raw):
                self.assertEqual(self.outcome(run, job, raw),
                                 ('OUTCOME', 'TIMEOUT', 'HOSTED_TIMEOUT'))

    def test_inconclusive_conclusions(self):
        for conclusion in ('cancelled', 'skipped', 'neutral', 'stale',
                           'action_required', 'startup_failure'):
            with self.subTest(conclusion):
                run, job = self.completed(conclusion)
                self.assertEqual(
                    self.outcome(run, job, receipt_bytes('PASS')),
                    ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_' + conclusion.upper()))

    def test_missing_or_invalid_receipt(self):
        for conclusion in ('success', 'failure'):
            run, job = self.completed(conclusion)
            for raw in (None, b'', b'not json', receipt_bytes('PASS', source_sha='f' * 40),
                        json.dumps(make_receipt('PASS'), sort_keys=True)):
                with self.subTest(conclusion=conclusion, raw=raw):
                    self.assertEqual(self.outcome(run, job, raw),
                                     ('OUTCOME', 'UNKNOWN', 'RECEIPT_INVALID'))
        run, job = self.completed('success')
        other_modules = remote_test._hosted_outcome(
            run, job, receipt_bytes('PASS'), sha=SHA, modules=('test_beta',))
        self.assertEqual(other_modules, ('OUTCOME', 'UNKNOWN', 'RECEIPT_INVALID'))

    def test_receipt_conflicts(self):
        cases = (
            ('success', 'success', 'FAIL'),
            ('success', 'success', 'TIMEOUT'),
            ('success', 'success', 'SHA_MISMATCH'),
            ('failure', 'failure', 'PASS'),
            ('timed_out', 'timed_out', 'PASS'),
            ('timed_out', 'timed_out', 'FAIL'),
            ('success', 'failure', 'PASS'),
            ('failure', 'success', 'PASS'),
            ('success', 'failure', 'FAIL'),
        )
        for conclusion, job_conclusion, result in cases:
            with self.subTest(conclusion=conclusion, job=job_conclusion, result=result):
                run, job = self.completed(conclusion, job_conclusion)
                self.assertEqual(self.outcome(run, job, receipt_bytes(result)),
                                 ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT'))

    def test_untrusted_job(self):
        run, _ = self.completed('success')
        for job in ('HOSTED_RUNNER_UNTRUSTED', make_job(labels=['self-hosted']),
                    make_job(runner_group_name='Default')):
            with self.subTest(job=job):
                self.assertEqual(self.outcome(run, job, receipt_bytes('PASS')),
                                 ('OUTCOME', 'UNKNOWN', 'HOSTED_RUNNER_UNTRUSTED'))
        self.assertEqual(
            self.outcome(make_run(status='queued', conclusion=None),
                         'HOSTED_RUNNER_UNTRUSTED', None),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUNNER_UNTRUSTED'))

    def test_anything_else_is_unknown(self):
        unknown = ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN')
        good = receipt_bytes('PASS')
        cases = {
            'run not a dict': (None, make_job(), good),
            'odd status': (make_run(status='mystery'), make_job(), good),
            'no conclusion': (make_run(conclusion=None), make_job(), good),
            'odd conclusion': (make_run(conclusion='mystery'), make_job(), good),
            'missing job': (make_run(), 'HOSTED_JOB_MISSING', good),
            'missing job timed out': (make_run(conclusion='timed_out'),
                                      'HOSTED_JOB_MISSING', None),
            'job none': (make_run(), None, good),
            'job still running': (make_run(), make_job(status='in_progress'), good),
            'job inconclusive': (make_run(), make_job(conclusion='cancelled'), good),
        }
        for name, (run, job, raw) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.outcome(run, job, raw), unknown)


class HostedActionsBlockTest(unittest.TestCase):
    def test_valid_block(self):
        self.assertEqual(remote_test._hosted_actions_block(make_run(), make_job(), NONCE), {
            'run_id': RUN_ID, 'run_attempt': 1, 'job_id': JOB_ID,
            'check_run_id': CHECK_ID,
            'workflow_path': '.github/workflows/llmtool-remote-test.yml',
            'workflow_sha': WORKFLOW_SHA, 'labels': ['ubuntu-24.04'],
            'runner_group': 'GitHub Actions', 'nonce': NONCE})

    def test_missing_or_malformed_fields(self):
        cases = {
            'run id bool': (make_run(id=True), make_job()),
            'run id missing': (make_run(id=None), make_job()),
            'attempt zero': (make_run(run_attempt=0), make_job()),
            'attempt text': (make_run(run_attempt='1'), make_job()),
            'job id text': (make_run(), make_job(id='202')),
            'job of other run': (make_run(), make_job(run_id=999)),
            'check url': (make_run(), make_job(check_run_url='check-runs/303')),
            'path': (make_run(path=None), make_job()),
            'empty path': (make_run(path=''), make_job()),
            'workflow sha': (make_run(head_sha='b' * 39), make_job()),
            'labels text': (make_run(), make_job(labels='ubuntu-24.04')),
            'labels empty': (make_run(), make_job(labels=[])),
            'label not text': (make_run(), make_job(labels=[24])),
            'group': (make_run(), make_job(runner_group_name=None)),
            'run not dict': (None, make_job()),
            'job not dict': (make_run(), 'HOSTED_JOB_MISSING'),
        }
        for name, (run, job) in cases.items():
            with self.subTest(name):
                self.assertIsNone(remote_test._hosted_actions_block(run, job, NONCE))
        for nonce in (None, NONCE.upper(), NONCE[:-1]):
            with self.subTest(nonce=nonce):
                self.assertIsNone(
                    remote_test._hosted_actions_block(make_run(), make_job(), nonce))


class HostedCommandDigestTest(unittest.TestCase):
    ARGS = dict(workflow_path='.github/workflows/llmtool-remote-test.yml',
                workflow_sha=WORKFLOW_SHA, sha=SHA, ref='refs/pull/7/head',
                modules=('test_alpha', 'test_beta'), nonce=NONCE)

    def test_digest_is_sha256_of_compact_sorted_json(self):
        expected_json = (
            '{"inputs":{"modules":"test_alpha test_beta","nonce":"' + NONCE +
            '","ref":"refs/pull/7/head","sha":"' + SHA + '"},'
            '"workflow_path":".github/workflows/llmtool-remote-test.yml",'
            '"workflow_sha":"' + WORKFLOW_SHA + '"}')
        self.assertEqual(remote_test._hosted_command_digest(**self.ARGS),
                         hashlib.sha256(expected_json.encode()).hexdigest())
        self.assertEqual(remote_test._hosted_command_digest(
            **dict(self.ARGS, modules=['test_alpha', 'test_beta'])),
            hashlib.sha256(expected_json.encode()).hexdigest())

    def test_every_field_changes_the_digest(self):
        base = remote_test._hosted_command_digest(**self.ARGS)
        for name, value in (('workflow_path', '.github/workflows/other.yml'),
                            ('workflow_sha', 'c' * 40), ('sha', 'c' * 40),
                            ('ref', 'main'), ('modules', ('test_alpha',)),
                            ('nonce', OTHER_NONCE)):
            with self.subTest(name):
                self.assertNotEqual(
                    remote_test._hosted_command_digest(**dict(self.ARGS, **{name: value})),
                    base)

    def test_malformed_arguments_are_refused(self):
        for name, value in (('sha', None), ('modules', 'test_alpha'),
                            ('modules', ('test_alpha', 3)), ('nonce', 12)):
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    remote_test._hosted_command_digest(**dict(self.ARGS, **{name: value}))


if __name__ == '__main__':
    unittest.main()
