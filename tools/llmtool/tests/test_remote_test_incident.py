"""#1706 R9: EC2 remote test of a main incident's failing modules.

Every test uses a real ``main_incident_store`` in a temporary directory and a
fake SSH transport; nothing reaches EC2, Git, GitHub or CI.

The output-screening tests (PR #1763, CWE-532) run the real sensitive screen.
Their only credential-shaped value is ``synthetic_credential()``: labelled
synthetic under the #763/#981 sensitive-detector exception, decoded at runtime
from hex (so the source holds no credential-shaped literal or fragments of
one), never a real credential, and subject to independent review.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import main_incident as mi
from llmtool import main_incident_store as incident_store
from llmtool import remote_test
from llmtool.errors import LLMToolError

GOOD = '1' * 40
MID = '2' * 40
HEAD = '3' * 40
OUTSIDE = '4' * 40
OTHER_GOOD = '5' * 40
SESSION = 'pm-incident-test-session'
STAMP = '2026-11-13T00:00:00Z'
TESTS = ('test_alpha.AlphaTests.test_one', 'test_beta.BetaTests.test_two',
         'test_alpha.AlphaTests.test_three')
NOW = 1_763_000_000


# SYNTHETIC (#763/#981): hex of the label-and-prefix part of the fixture. The
# source holds neither a credential-shaped literal nor joinable fragments of one.
_SYNTHETIC_PREFIX_HEX = '746f6b656e3d736b2d'


def synthetic_credential():
    """SYNTHETIC (#763/#981): a credential-shaped value built at runtime, never real.

    Decodes to the same runtime value as the Task-path excerpt test in
    test_remote_test (label, prefix and a run of one letter);
    ``test_synthetic_fixture_is_flagged_by_the_real_screen`` proves the real
    screen flags it, so a test that passes with it exercises real refusals.
    """
    return bytes.fromhex(_SYNTHETIC_PREFIX_HEX).decode('ascii') + 'a' * 16


def observation(tests, *, observed_at=STAMP, merges=(MID, HEAD)):
    rows = [{'pr': 10 + index} for index, _ in enumerate(merges)]
    return {
        'observed_at': observed_at, 'signal': [mi.LINUX], 'failing_head': merges[-1],
        'failing_runs': [{'kind': mi.LINUX, 'id': 101, 'conclusion': 'failure'}],
        'class': mi.CODE,
        'evidence': [{'code': 'TEST_FAILURE', 'kind': mi.LINUX, 'run': 101,
                      'tests': list(tests)}],
        'range': [{'sha': sha, 'pr': 10 + index} for index, sha in enumerate(merges)],
        'candidate_rows': rows,
        'attribution': {'attribution': mi.BATCH, 'rows': rows},
        'closure_available': False,
    }


class IncidentRemoteTestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.root = base / 'state' / 'tasks'
        self.config = base / 'remote-test.json'
        self.config.write_text(json.dumps({
            'host': 'ec2.example.org', 'user': 'runner',
            'repo_path': '/home/runner/project', 'python': '/usr/bin/python3',
        }))
        self.config.chmod(0o600)
        self.calls = []
        self.record = self.make_incident(TESTS)
        self.incident = self.record['incident_id']

    # helpers -------------------------------------------------------------

    def make_incident(self, tests, last_good=GOOD):
        body = incident_store.new_record(pm_session_id=SESSION, observation=observation(tests),
                                         last_good_head=last_good, created_at=STAMP)
        return incident_store.create(body, self.root)

    def stored(self, incident=None):
        return incident_store.get(incident or self.incident, self.root)

    def record_path(self, incident=None):
        return self.root.parent / incident_store.DIRECTORY_NAME / (
            (incident or self.incident) + '.json')

    def receipt(self, *, sha=HEAD, modules=('test_alpha',), result='PASS', **changes):
        value = {
            'result': result, 'exit_code': 0 if result == 'PASS' else 1,
            'source_sha': sha, 'tree': 'c' * 40, 'fetch_head': sha,
            'modules': list(modules), 'identity': {'uid': 65534},
            'started_at': '2026-11-13T00:00:00Z', 'ended_at': '2026-11-13T00:00:01Z',
            'elapsed_s': 1.0, 'ran_total': 3,
            'final': ['OK'] * len(modules) if result == 'PASS' else ['FAILED (failures=1)'],
            'failures': [] if result == 'PASS' else [modules[0]],
            'log_bytes': 10, 'log_sha256': 'd' * 64,
            'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
        }
        value.update(changes)
        return json.dumps(value, separators=(',', ':')).encode()

    def transport(self, stdout=None, *, returncode=0, error=None, during=None):
        def run(argv, script, timeout):
            self.calls.append((argv, script, timeout))
            if during is not None:
                during()
            if error is not None:
                raise error
            out = self.receipt(sha=argv[8], modules=argv[12:]) if stdout is None else stdout
            return SimpleNamespace(stdout=out, stderr=b'', returncode=returncode)
        return run

    def refusing_transport(self, *args):
        self.fail('transport called')

    def run_it(self, *, incident=None, sha=HEAD, modules=('test_alpha',), transport=None):
        return remote_test.run_incident_remote_test(
            incident or self.incident, sha=sha, modules=modules, root=self.root,
            config_path=self.config, transport=transport or self.transport(), now=NOW)

    def assert_denied(self, result, code):
        self.assertEqual(result['result'], 'ADMISSION_DENIED')
        self.assertEqual(result['code'], code)
        self.assertFalse(result['persisted'])

    # admission and recording ---------------------------------------------

    def test_admitted_pass_is_recorded_on_the_incident(self):
        result = self.run_it()

        self.assertEqual(result['result'], 'PASS')
        self.assertTrue(result['persisted'])
        self.assertEqual((result['sha'], result['modules'], result['where'], result['code']),
                         (HEAD, ['test_alpha'], 'EC2', None))
        self.assertEqual(len(self.calls), 1)
        argv, script, timeout = self.calls[0]
        config = remote_test._config(self.config)
        # Same session as the Task path; the SHA itself is the fetched ref.
        self.assertEqual(argv, remote_test._ssh_argv(config, HEAD, HEAD, ('test_alpha',)))
        self.assertEqual(argv[:5], ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10'])
        self.assertIs(script, remote_test._REMOTE_SCRIPT)
        self.assertEqual(timeout, remote_test._SSH_TIMEOUT)

        stored = self.stored()
        self.assertEqual(result['record_sha256'], stored['record_sha256'])
        [entry] = stored[incident_store.REMOTE_TESTS]
        self.assertEqual(entry, {key: result[key] for key in incident_store.REMOTE_TEST_FIELDS})
        self.assertEqual(entry['recorded_at'], incident_store.utc_stamp(NOW))
        receipt = entry['receipt']
        self.assertEqual(receipt['incident_id'], self.incident)
        self.assertEqual(receipt['command_digest'], remote_test._command_digest(argv))
        self.assertEqual(receipt['receipt_digest'], incident_store.remote_test_digest(receipt))
        for key in ('observations', 'confirmations', 'outcome_events', 'last_good_head',
                    'outcome', 'pm_session_id'):
            self.assertEqual(stored[key], self.record[key])

    def test_failing_last_good_and_range_shas_are_admitted(self):
        for sha in (GOOD, MID, HEAD):
            with self.subTest(sha=sha):
                result = self.run_it(sha=sha)
                self.assertEqual(result['result'], 'PASS')
                self.assertTrue(result['persisted'])
                self.assertEqual(self.calls[-1][0][8:10], [sha, sha])
        entries = self.stored()[incident_store.REMOTE_TESTS]
        self.assertEqual([entry['sha'] for entry in entries], [GOOD, MID, HEAD])

    def test_no_modules_selects_every_failing_module(self):
        result = self.run_it(modules=())

        self.assertEqual(result['modules'], ['test_alpha', 'test_beta'])
        self.assertEqual(self.calls[0][0][12:], ['test_alpha', 'test_beta'])

    def test_fail_receipt_is_recorded_with_its_receipt(self):
        result = self.run_it(transport=self.transport(self.receipt(result='FAIL')))

        self.assertEqual((result['result'], result['code']), ('FAIL', None))
        [entry] = self.stored()[incident_store.REMOTE_TESTS]
        self.assertEqual(entry['receipt']['failures'], ['test_alpha'])

    def test_runs_without_a_valid_receipt_are_recorded_as_outcomes(self):
        cases = (
            ({'error': subprocess.TimeoutExpired('ssh', 1)}, 'TIMEOUT', 'SSH_TIMEOUT'),
            ({'error': OSError('down')}, 'UNAVAILABLE', 'SSH_UNAVAILABLE'),
            ({'stdout': b'', 'returncode': 75}, 'UNAVAILABLE', 'REMOTE_BUSY'),
            ({'stdout': b'', 'returncode': 76}, 'UNAVAILABLE', 'REMOTE_LOCK_UNSAFE'),
            ({'stdout': b'', 'returncode': 255}, 'UNAVAILABLE', 'SSH_FAILED'),
            ({'stdout': b'not json'}, 'UNKNOWN', 'RECEIPT_INVALID'),
            ({'stdout': self.receipt(sha=OUTSIDE)}, 'UNKNOWN', 'RECEIPT_INVALID'),
            ({'stdout': self.receipt(modules=('test_beta',))}, 'UNKNOWN', 'RECEIPT_INVALID'),
        )
        for options, result_name, code in cases:
            with self.subTest(code=code, options=sorted(options)):
                result = self.run_it(transport=self.transport(**options))
                self.assertEqual((result['result'], result['code']), (result_name, code))
                self.assertIsNone(result['receipt'])
                self.assertTrue(result['persisted'])
        self.assertEqual(len(self.stored()[incident_store.REMOTE_TESTS]), len(cases))

    def test_failure_excerpt_goes_through_the_output_screen(self):
        # Real screen, no patched predicate: the shared validator refuses it.
        flagged = synthetic_credential()
        stdout = self.receipt(result='FAIL', failure_excerpt=flagged)
        refused = self.run_it(transport=self.transport(stdout))
        self.assertEqual((refused['result'], refused['code']), ('UNKNOWN', 'RECEIPT_INVALID'))
        self.assertIsNone(refused['receipt'])
        self.assertTrue(refused['persisted'])
        self.assertNotIn(flagged, json.dumps(refused))
        self.assertNotIn(flagged, self.record_path().read_text())

        excerpt = 'AssertionError: 1 != 2'
        kept = self.run_it(transport=self.transport(
            self.receipt(result='FAIL', failure_excerpt=excerpt)))
        self.assertEqual(kept['result'], 'FAIL')
        self.assertEqual(kept['receipt']['failure_excerpt'], excerpt)

    # output screening before recording (PR #1763, CWE-532) -----------------

    def test_synthetic_fixture_is_flagged_by_the_real_screen(self):
        flagged = synthetic_credential()
        self.assertTrue(remote_test._emitted_sensitive(flagged))
        self.assertTrue(incident_store.task_store._emitted_sensitive(flagged))
        self.assertIs(remote_test._emitted_sensitive,
                      incident_store.task_store._emitted_sensitive)
        for benign in ('FAILED (failures=1)', 'AssertionError: 1 != 2',
                       incident_store.FINAL_REDACTED):
            self.assertFalse(remote_test._emitted_sensitive(benign))

    def test_final_lines_outside_the_summary_grammar_are_redacted(self):
        cases = (
            ('PASS', ['OK', 'OK (skipped=2)', 'loader note OK'],
             ['OK', 'OK (skipped=2)', incident_store.FINAL_REDACTED]),
            ('FAIL', ['FAILED (failures=1, errors=2)', 'NO_FINAL',
                      'ERROR: test_one (test_alpha.Case.test_one)'],
             ['FAILED (failures=1, errors=2)', 'NO_FINAL', incident_store.FINAL_REDACTED]),
        )
        # Two modules, so three final lines stay within two per module.
        modules = ('test_alpha', 'test_beta')
        for result_name, finals, expected in cases:
            with self.subTest(result=result_name):
                stdout = self.receipt(result=result_name, modules=modules, final=finals)
                # The shared Task-path validator is unchanged and keeps raw lines.
                self.assertEqual(remote_test._validate_remote_receipt(
                    stdout, sha=HEAD, modules=modules)['final'], finals)
                with patch.object(remote_test, '_emitted_sensitive',
                                  wraps=remote_test._emitted_sensitive) as screen:
                    result = self.run_it(modules=modules, transport=self.transport(stdout))
                screened = [call.args[0] for call in screen.call_args_list]
                for line in finals:
                    self.assertIn(line, screened)
                self.assertEqual((result['result'], result['code']), (result_name, None))
                self.assertTrue(result['persisted'])
                receipt = self.stored()[incident_store.REMOTE_TESTS][-1]['receipt']
                self.assertEqual(receipt['final'], expected)
                self.assertEqual(receipt['receipt_digest'],
                                 incident_store.remote_test_digest(receipt))

    def test_credential_shaped_final_line_refuses_the_receipt(self):
        # Real screen, no patched predicate. The shared Task-path validator
        # does not screen final lines, so this is the incident screen's refusal.
        flagged = synthetic_credential()
        for final in ([flagged], ['FAILED (failures=1)', flagged]):
            with self.subTest(final=len(final)):
                stdout = self.receipt(result='FAIL', final=final)
                self.assertIsNotNone(remote_test._validate_remote_receipt(
                    stdout, sha=HEAD, modules=('test_alpha',)))
                result = self.run_it(transport=self.transport(stdout))
                self.assertEqual((result['result'], result['code']),
                                 ('UNKNOWN', remote_test.INCIDENT_RECEIPT_SENSITIVE))
                self.assertIsNone(result['receipt'])
                self.assertTrue(result['persisted'])
                self.assertIsNone(self.stored()[incident_store.REMOTE_TESTS][-1]['receipt'])
                self.assertNotIn(flagged, json.dumps(result))
        self.assertNotIn(flagged, self.record_path().read_text())

    def test_incident_screen_refuses_credential_shaped_output_fields(self):
        # The second screen, run directly with the real predicate: a value the
        # shared validator might pass is still refused in final and in excerpt.
        flagged = synthetic_credential()
        parsed = json.loads(self.receipt(result='FAIL', failure_excerpt='AssertionError'))
        self.assertEqual(remote_test._screen_incident_receipt(parsed)['failure_excerpt'],
                         'AssertionError')
        for field, value in (('final', [flagged]), ('final', ['NO_FINAL', flagged]),
                             ('failure_excerpt', flagged)):
            with self.subTest(field=field, lines=len(value) if type(value) is list else 1):
                self.assertIsNone(remote_test._screen_incident_receipt(
                    dict(parsed, **{field: value})))

    # result invariants before recording (Codex v1 F1 on PR #1769) -----------

    def test_receipt_contradicting_its_result_is_recorded_without_it(self):
        cases = {
            'TIMEOUT with exit_code 1': self.receipt(result='TIMEOUT'),
            'SHA_MISMATCH with exit_code 1': self.receipt(
                result='SHA_MISMATCH', fetch_head=MID, tree=None, ran_total=0, final=[],
                failures=[]),
            'FAIL at another FETCH_HEAD': self.receipt(result='FAIL', fetch_head=MID, tree=None),
            'failure without a final line': self.receipt(result='FAIL', final=[]),
        }
        for name, stdout in cases.items():
            with self.subTest(name):
                # The unchanged Task-path validator accepts it; the incident
                # path refuses to record it as a receipt.
                self.assertIsNotNone(remote_test._validate_remote_receipt(
                    stdout, sha=HEAD, modules=('test_alpha',)))
                result = self.run_it(transport=self.transport(stdout))
                self.assertEqual((result['result'], result['code']),
                                 ('UNKNOWN', remote_test.INCIDENT_RECEIPT_INCONSISTENT))
                self.assertIsNone(result['receipt'])
                self.assertTrue(result['persisted'])
                self.assertIsNone(self.stored()[incident_store.REMOTE_TESTS][-1]['receipt'])

        kept = self.run_it(transport=self.transport(self.receipt(result='TIMEOUT', exit_code=124)))
        self.assertEqual((kept['result'], kept['code']), ('TIMEOUT', None))
        self.assertTrue(kept['persisted'])
        self.assertTrue(incident_store.receipt_consistent(kept['receipt']))

    def test_no_git_ci_or_github_call_is_made(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('outside call')
        with patch.object(subprocess, 'run', side_effect=forbidden), \
                patch.object(subprocess, 'Popen', side_effect=forbidden), \
                patch.object(remote_test, '_github_get', side_effect=forbidden), \
                patch.object(remote_test, '_hosted_post', side_effect=forbidden), \
                patch.object(remote_test, '_hosted_dispatch', side_effect=forbidden):
            result = self.run_it()
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(len(self.calls), 1)

    # refusals ------------------------------------------------------------

    def test_refusals_run_nothing_and_record_nothing(self):
        cases = (
            ({'incident': 'mi-' + 'f' * 40}, remote_test.INCIDENT_UNKNOWN),
            ({'incident': '../elsewhere'}, remote_test.INCIDENT_UNKNOWN),
            ({'sha': OUTSIDE}, remote_test.INCIDENT_SHA_NOT_RECORDED),
            ({'sha': 'main'}, remote_test.INCIDENT_ARGUMENT_INVALID),
            ({'modules': ('test_gamma',)}, remote_test.INCIDENT_MODULE_NOT_FAILING),
            ({'modules': ('test_alpha', 'test_gamma')}, remote_test.INCIDENT_MODULE_NOT_FAILING),
            ({'modules': ('not a module',)}, remote_test.INCIDENT_ARGUMENT_INVALID),
            ({'modules': 'test_alpha'}, remote_test.INCIDENT_ARGUMENT_INVALID),
        )
        before = self.stored()['record_sha256']
        for options, code in cases:
            with self.subTest(code=code, options=options):
                result = self.run_it(transport=self.refusing_transport, **options)
                self.assert_denied(result, code)
        self.assertEqual(self.stored()['record_sha256'], before)
        self.assertNotIn(incident_store.REMOTE_TESTS, self.stored())

    def test_unsafe_incident_id_is_not_echoed(self):
        result = self.run_it(incident='../elsewhere', transport=self.refusing_transport)
        self.assertIsNone(result['incident_id'])

    def test_unreadable_incident_is_refused(self):
        path = self.record_path()
        data = json.loads(path.read_text())
        data['observations'][0]['class'] = mi.UNKNOWN  # digest no longer matches
        path.write_text(json.dumps(data))

        result = self.run_it(transport=self.refusing_transport)
        self.assert_denied(result, remote_test.INCIDENT_UNREADABLE)

    def test_closed_incident_is_refused(self):
        incident_store.advance_outcome(self.incident, self.record['record_sha256'],
                                       incident_store.RESOLVED, at=STAMP, root=self.root)
        result = self.run_it(transport=self.refusing_transport)
        self.assert_denied(result, remote_test.INCIDENT_NOT_OPEN)

    def test_more_modules_than_the_cap_are_refused(self):
        many = ['test_m%d' % index for index in range(remote_test._MAX_REMOTE_MODULES + 1)]
        record = self.make_incident([name + '.Case.test_x' for name in many], OTHER_GOOD)
        incident = record['incident_id']
        for modules in (many, ()):
            with self.subTest(modules=modules):
                result = self.run_it(incident=incident, modules=modules,
                                     transport=self.refusing_transport)
                self.assert_denied(result, remote_test.INCIDENT_MODULE_CAP)

        at_cap = self.run_it(incident=incident, modules=many[:-1])
        self.assertEqual(at_cap['result'], 'PASS')
        self.assertEqual(len(self.calls[0][0][12:]), remote_test._MAX_REMOTE_MODULES)

    def test_invalid_config_is_refused(self):
        self.config.chmod(0o644)
        result = self.run_it(transport=self.refusing_transport)
        self.assert_denied(result, remote_test.INCIDENT_CONFIG_INVALID)

    # recording races -----------------------------------------------------

    def test_incident_closed_during_the_run_is_not_written(self):
        def close():
            incident_store.advance_outcome(self.incident, self.record['record_sha256'],
                                           incident_store.RESOLVED, at=STAMP, root=self.root)
        result = self.run_it(transport=self.transport(during=close))

        self.assertEqual(result['result'], 'PASS')
        self.assertFalse(result['persisted'])
        self.assertEqual(result['record_code'], incident_store.INCIDENT_CLOSED)
        self.assertNotIn(incident_store.REMOTE_TESTS, self.stored())

    def test_new_observation_during_the_run_is_kept(self):
        def observe():
            incident_store.append_observation(
                self.incident, self.record['record_sha256'],
                observation(TESTS, observed_at='2026-11-13T00:05:00Z'), self.root)
        result = self.run_it(transport=self.transport(during=observe))

        self.assertTrue(result['persisted'])
        stored = self.stored()
        self.assertEqual(len(stored['observations']), 2)
        self.assertEqual(len(stored[incident_store.REMOTE_TESTS]), 1)

    def test_record_conflict_rereads_and_appends(self):
        real = incident_store.append_remote_test
        attempts = []

        def conflict_once(*args, **kwargs):
            attempts.append(args)
            if len(attempts) == 1:
                error = LLMToolError(incident_store.INCIDENT_RECORD_CONFLICT)
                error.incident_code = incident_store.INCIDENT_RECORD_CONFLICT
                raise error
            return real(*args, **kwargs)

        with patch.object(incident_store, 'append_remote_test', side_effect=conflict_once):
            result = self.run_it()
        self.assertTrue(result['persisted'])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.stored()[incident_store.REMOTE_TESTS]), 1)


class IncidentStoreRemoteTestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'state' / 'tasks'
        body = incident_store.new_record(pm_session_id=SESSION, observation=observation(TESTS),
                                         last_good_head=GOOD, created_at=STAMP)
        self.record = incident_store.create(body, self.root)
        self.incident = self.record['incident_id']

    def entry(self, **changes):
        value = {'sha': HEAD, 'modules': ['test_alpha'], 'result': 'UNAVAILABLE',
                 'code': 'SSH_FAILED', 'receipt': None, 'where': 'EC2', 'recorded_at': STAMP}
        value.update(changes)
        return value

    def stored_receipt(self, *, result='PASS', drop=(), reseal=True, **changes):
        """A full stored receipt, as run_incident_remote_test builds it.

        ``changes`` are applied before the digest when ``reseal`` (so only the
        changed field can be the reason for a refusal), else after it.
        """
        passed = result == 'PASS'
        receipt = {
            'result': result, 'exit_code': 0 if passed else 1, 'source_sha': HEAD,
            'tree': 'c' * 40, 'fetch_head': HEAD, 'modules': ['test_alpha'],
            'identity': {'uid': 65534}, 'started_at': STAMP, 'ended_at': STAMP,
            'elapsed_s': 1.0, 'ran_total': 2,
            'final': ['OK'] if passed else ['FAILED (failures=1)'],
            'failures': [] if passed else ['test_alpha'],
            'log_bytes': 10, 'log_sha256': 'd' * 64,
            'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
            'incident_id': self.incident, 'command_digest': 'e' * 64,
        }
        if reseal:
            receipt.update(changes)
        for key in drop:
            receipt.pop(key)
        receipt['receipt_digest'] = incident_store.remote_test_digest(receipt)
        if not reseal:
            receipt.update(changes)
        return receipt

    def receipt_entry(self, *, result='PASS', modules=None, **receipt_changes):
        entry_changes = {}
        if modules is not None:
            receipt_changes['modules'] = entry_changes['modules'] = list(modules)
        receipt = self.stored_receipt(result=result, **receipt_changes)
        return self.entry(result=result, code=None, receipt=receipt, **entry_changes)

    def append(self, entry):
        current = incident_store.get(self.incident, self.root)
        return incident_store.append_remote_test(
            self.incident, current['record_sha256'], entry, self.root)

    def assert_refused(self, entry, code):
        before = incident_store.get(self.incident, self.root)['record_sha256']
        with self.assertRaises(LLMToolError) as caught:
            self.append(entry)
        self.assertEqual(caught.exception.incident_code, code)
        self.assertEqual(incident_store.get(self.incident, self.root)['record_sha256'], before)
        self.assertNotIn(incident_store.REMOTE_TESTS, incident_store.get(self.incident, self.root))

    def assert_invalid(self, entry):
        self.assert_refused(entry, incident_store.INCIDENT_RECORD_INVALID)

    def reseal_on_disk(self, change):
        """Apply change(data) to the stored record and reseal its digests."""
        path = self.root.parent / incident_store.DIRECTORY_NAME / (self.incident + '.json')
        data = json.loads(path.read_text())
        change(data)
        receipt = data[incident_store.REMOTE_TESTS][0]['receipt']
        if receipt is not None:
            receipt['receipt_digest'] = incident_store.remote_test_digest(receipt)
        data['record_sha256'] = incident_store.record_digest(data)
        path.write_text(json.dumps(data))
        return path

    def test_new_and_older_records_have_no_remote_tests_and_still_read(self):
        self.assertNotIn(incident_store.REMOTE_TESTS, self.record)
        self.assertEqual(incident_store.get(self.incident, self.root), self.record)

    def test_create_refuses_a_record_with_remote_tests(self):
        body = incident_store.new_record(pm_session_id=SESSION, observation=observation(TESTS),
                                         last_good_head=OTHER_GOOD, created_at=STAMP)
        body[incident_store.REMOTE_TESTS] = []
        with self.assertRaises(LLMToolError) as caught:
            incident_store.create(body, self.root)
        self.assertEqual(caught.exception.incident_code, incident_store.INCIDENT_OUTCOME_INVALID)

    def test_valid_entries_append(self):
        self.append(self.entry())
        saved = self.append(self.receipt_entry())
        self.assertEqual(len(saved[incident_store.REMOTE_TESTS]), 2)

    def test_entries_not_bound_to_the_incident_are_refused(self):
        cases = {
            'sha outside': self.entry(sha=OUTSIDE),
            'module not failing': self.entry(modules=['test_gamma']),
            'modules unsorted': self.entry(modules=['test_beta', 'test_alpha']),
            'no modules': self.entry(modules=[]),
            'where': self.entry(where='LOCAL'),
            'outcome without code': self.entry(code=None),
            'receipt result on outcome': self.entry(result='PASS'),
            'extra field': dict(self.entry(), note='x'),
            'receipt digest': self.receipt_entry(reseal=False, ran_total=3),
            'receipt other incident': self.receipt_entry(incident_id='mi-' + OUTSIDE),
            'receipt other sha': self.receipt_entry(source_sha=MID),
            'receipt with code': dict(self.receipt_entry(), code='SSH_FAILED'),
        }
        for name, entry in cases.items():
            with self.subTest(name):
                self.assert_invalid(entry)

    # exact receipt allowlist (Codex v1 F1 on PR #1763) ---------------------

    def test_receipt_allowlist_matches_the_remote_receipt(self):
        bound = frozenset(('incident_id', 'command_digest', 'receipt_digest'))
        self.assertEqual(incident_store.REMOTE_RECEIPT_FIELDS,
                         remote_test._RECEIPT_FIELDS | bound)
        self.assertEqual(incident_store.REMOTE_RECEIPT_FIELDS | incident_store.REMOTE_RECEIPT_OPTIONAL,
                         remote_test._RECEIPT_FIELDS_WITH_EXCERPT | bound)

    def test_receipt_with_a_field_outside_the_allowlist_is_refused(self):
        code = incident_store.INCIDENT_RECEIPT_FIELDS_INVALID
        cases = {
            'extra field': self.receipt_entry(note='x'),
            'extra output field': self.receipt_entry(result='FAIL', stdout='FAILED'),
            'task binding field': self.receipt_entry(task_id='t-1'),
            'missing field': self.receipt_entry(drop=('log_sha256',)),
            'missing digest': self.receipt_entry(),
            'receipt not a dict': self.entry(result='PASS', code=None, receipt=['PASS']),
        }
        cases['missing digest']['receipt'].pop('receipt_digest')
        for name, entry in cases.items():
            with self.subTest(name):
                self.assert_refused(entry, code)
        self.assertTrue(incident_store.receipt_fields_allowed(self.stored_receipt()))
        self.assertTrue(incident_store.receipt_fields_allowed(
            self.stored_receipt(result='FAIL', failure_excerpt='AssertionError')))

    def test_extra_receipt_field_on_disk_fails_read(self):
        self.append(self.receipt_entry())
        self.reseal_on_disk(
            lambda data: data[incident_store.REMOTE_TESTS][0]['receipt'].update(note='x'))
        with self.assertRaises(LLMToolError) as caught:
            incident_store.get(self.incident, self.root)
        self.assertEqual(caught.exception.incident_code, incident_store.INCIDENT_RECORD_INVALID)

    def test_structured_receipt_fields_must_match_their_grammar(self):
        cases = {
            'log path free text': {'remote_log_path': '/tmp/elsewhere/notes.txt'},
            'stamp free text': {'started_at': 'yesterday'},
            'tree free text': {'tree': 'not a sha'},
            'identity extra key': {'identity': {'uid': 1, 'user': 'runner'}},
            'failures not admitted': {'failures': ['test_beta']},
            'log digest free text': {'log_sha256': 'not a digest'},
            'command digest free text': {'command_digest': 'x'},
            'exit code text': {'exit_code': 'zero'},
        }
        for name, changes in cases.items():
            with self.subTest(name):
                self.assert_invalid(self.receipt_entry(**changes))

    # output screening in the store (PR #1763 CWE-532; Codex v1 F2) ---------

    def test_credential_shaped_output_fields_are_refused_by_the_real_screen(self):
        # No patched predicate: the store's own call to the real screen refuses.
        flagged = synthetic_credential()
        cases = {
            'final': {'final': [flagged]},
            'final after a summary line': {'final': ['FAILED (failures=1)', flagged]},
            'failure_excerpt': {'failure_excerpt': flagged},
        }
        for name, changes in cases.items():
            with self.subTest(name):
                self.assert_invalid(self.receipt_entry(result='FAIL', **changes))
        path = self.root.parent / incident_store.DIRECTORY_NAME / (self.incident + '.json')
        self.assertNotIn(flagged, path.read_text())

    def test_credential_shaped_output_on_disk_fails_read(self):
        flagged = synthetic_credential()
        self.append(self.receipt_entry(result='FAIL', failure_excerpt='AssertionError'))
        path = self.root.parent / incident_store.DIRECTORY_NAME / (self.incident + '.json')
        original = path.read_text()
        for field, value in (('final', [flagged]), ('failure_excerpt', flagged)):
            with self.subTest(field):
                path.write_text(original)
                incident_store.get(self.incident, self.root)  # the untouched record reads

                def change(data, field=field, value=value):
                    data[incident_store.REMOTE_TESTS][0]['receipt'][field] = value
                self.reseal_on_disk(change)
                with self.assertRaises(LLMToolError) as caught:
                    incident_store.get(self.incident, self.root)
                self.assertEqual(caught.exception.incident_code,
                                 incident_store.INCIDENT_RECORD_INVALID)

    def test_receipt_output_fields_keep_their_shape(self):
        cases = {
            'final not a list': {'final': 'FAILED (failures=1)'},
            'final not text': {'final': [7]},
            'final outside the summary grammar': {'final': ['loader note FAILED']},
            'excerpt not text': {'failure_excerpt': None},
            'excerpt too long': {'failure_excerpt': 'x' * (incident_store.MAX_EXCERPT_BYTES + 1)},
        }
        for name, changes in cases.items():
            with self.subTest(name):
                self.assert_invalid(self.receipt_entry(result='FAIL', **changes))

        saved = self.append(self.receipt_entry(
            result='FAIL', modules=('test_alpha', 'test_beta'),
            final=['FAILED (failures=1)', 'NO_FINAL', incident_store.FINAL_REDACTED],
            failure_excerpt='AssertionError: 1 != 2'))
        receipt = saved[incident_store.REMOTE_TESTS][0]['receipt']
        self.assertEqual(receipt['final'],
                         ['FAILED (failures=1)', 'NO_FINAL', incident_store.FINAL_REDACTED])
        self.assertEqual(receipt['failure_excerpt'], 'AssertionError: 1 != 2')

    # result invariants in the store (Codex v1 F1 on PR #1769) ----------------

    # Changes that make a stored_receipt() consistent with each result.
    CONSISTENT = {
        'PASS': {},
        'FAIL': {},
        'TIMEOUT': {'exit_code': incident_store.TIMEOUT_EXIT_CODE},
        'SHA_MISMATCH': {'exit_code': incident_store.SHA_MISMATCH_EXIT_CODE, 'fetch_head': MID,
                         'tree': None, 'ran_total': 0, 'final': [], 'failures': []},
    }

    def result_entry(self, result, **changes):
        """A receipt entry consistent with ``result`` except for ``changes``."""
        return self.receipt_entry(result=result, **dict(self.CONSISTENT[result], **changes))

    def test_consistent_receipts_of_every_result_append(self):
        results = sorted(incident_store.REMOTE_RECEIPT_RESULTS)
        for result in results:
            saved = self.append(self.result_entry(result))
        entries = saved[incident_store.REMOTE_TESTS]
        self.assertEqual([entry['result'] for entry in entries], results)
        for entry in entries:
            self.assertTrue(incident_store.receipt_consistent(entry['receipt']))

    def test_receipt_contradicting_its_result_is_refused_on_append(self):
        # Each case is consistent but for its own change and otherwise well
        # formed, so the refusal is the result invariant, not another check.
        two = ('test_alpha', 'test_beta')
        cases = {
            'PASS with non-zero exit_code': ('PASS', {'exit_code': 1}),
            'PASS with failure_excerpt': ('PASS', {'failure_excerpt': 'AssertionError'}),
            'PASS with failures': ('PASS', {'failures': ['test_alpha']}),
            'PASS that ran no test': ('PASS', {'ran_total': 0}),
            'PASS at another FETCH_HEAD': ('PASS', {'fetch_head': MID, 'tree': None}),
            'PASS without tree': ('PASS', {'tree': None}),
            'PASS as root': ('PASS', {'identity': {'uid': 0}}),
            'PASS without uid': ('PASS', {'identity': {'uid': None}}),
            'PASS missing a final line': ('PASS', {'modules': two, 'final': ['OK']}),
            'PASS with a failing final line': ('PASS', {'final': ['FAILED (failures=1)']}),
            'PASS with NO_FINAL': ('PASS', {'final': ['NO_FINAL']}),
            'FAIL with exit_code 0': ('FAIL', {'exit_code': 0}),
            'FAIL at another FETCH_HEAD': ('FAIL', {'fetch_head': MID, 'tree': None}),
            'TIMEOUT with exit_code 0': ('TIMEOUT', {'exit_code': 0}),
            'TIMEOUT with exit_code 1': ('TIMEOUT', {'exit_code': 1}),
            'TIMEOUT at another FETCH_HEAD': ('TIMEOUT', {'fetch_head': MID, 'tree': None}),
            'SHA_MISMATCH with exit_code 0': ('SHA_MISMATCH', {'exit_code': 0}),
            'SHA_MISMATCH with exit_code 1': ('SHA_MISMATCH', {'exit_code': 1}),
            'SHA_MISMATCH at the source SHA': ('SHA_MISMATCH', {'fetch_head': HEAD}),
            'SHA_MISMATCH with tree': ('SHA_MISMATCH', {'tree': 'c' * 40}),
            'SHA_MISMATCH that ran tests': ('SHA_MISMATCH', {'ran_total': 1}),
            'SHA_MISMATCH with a final line': ('SHA_MISMATCH', {'final': ['NO_FINAL']}),
            'SHA_MISMATCH with failures': ('SHA_MISMATCH', {'final': ['NO_FINAL'],
                                                            'failures': ['test_alpha']}),
            'failure repeated': ('FAIL', {'final': ['FAILED (failures=1)', 'NO_FINAL'],
                                          'failures': ['test_alpha', 'test_alpha']}),
            'failures out of module order': ('FAIL', {
                'modules': two, 'final': ['FAILED (failures=1)', 'FAILED (errors=1)'],
                'failures': ['test_beta', 'test_alpha']}),
            'failure without a final line': ('FAIL', {'final': []}),
            'more than two final lines per module': ('FAIL', {'final': ['NO_FINAL'] * 3}),
            'tree without FETCH_HEAD': ('FAIL', {'fetch_head': None}),
        }
        for name, (result, changes) in cases.items():
            with self.subTest(name):
                entry = self.result_entry(result, **changes)
                self.assertFalse(incident_store.receipt_consistent(entry['receipt']))
                self.assert_refused(entry, incident_store.INCIDENT_RECEIPT_INCONSISTENT)

    def test_receipt_contradicting_its_result_fails_read_with_recomputed_digests(self):
        self.append(self.receipt_entry())
        path = self.root.parent / incident_store.DIRECTORY_NAME / (self.incident + '.json')
        original = path.read_text()
        failed = {'final': ['FAILED (failures=1)'], 'failures': ['test_alpha']}
        cases = {
            'PASS with non-zero exit_code': {'exit_code': 1},
            'PASS with failure_excerpt': {'failure_excerpt': 'AssertionError'},
            'PASS with failures': {'failures': ['test_alpha']},
            'PASS that ran no test': {'ran_total': 0},
            'FAIL with exit_code 0': dict(failed, result='FAIL'),
            'TIMEOUT with exit_code 0': dict(failed, result='TIMEOUT'),
            'SHA_MISMATCH with exit_code 0': {'result': 'SHA_MISMATCH', 'fetch_head': MID,
                                              'tree': None, 'ran_total': 0, 'final': []},
        }
        for name, changes in cases.items():
            with self.subTest(name):
                path.write_text(original)
                incident_store.get(self.incident, self.root)  # the untouched record reads

                def change(data, changes=changes):
                    entry = data[incident_store.REMOTE_TESTS][0]
                    entry['receipt'].update(changes)
                    entry['result'] = entry['receipt']['result']
                self.reseal_on_disk(change)
                data = json.loads(path.read_text())
                receipt = data[incident_store.REMOTE_TESTS][0]['receipt']
                self.assertEqual(receipt['receipt_digest'],
                                 incident_store.remote_test_digest(receipt))
                self.assertEqual(data['record_sha256'], incident_store.record_digest(data))
                with self.assertRaises(LLMToolError) as caught:
                    incident_store.get(self.incident, self.root)
                self.assertEqual(caught.exception.incident_code,
                                 incident_store.INCIDENT_RECORD_INVALID)

    # read path: a stored receipt contradicting its result (#1706 R9) ---------

    # (name, result of the appended consistent receipt, on-disk change)
    CONTRADICTIONS = (
        ('PASS with exit_code 1', 'PASS', {'exit_code': 1}),
        ('PASS with exit_code 124', 'PASS', {'exit_code': incident_store.TIMEOUT_EXIT_CODE}),
        ('PASS with exit_code 41', 'PASS',
         {'exit_code': incident_store.SHA_MISMATCH_EXIT_CODE}),
        ('FAIL with exit_code 0', 'FAIL', {'exit_code': 0}),
    )

    def record_path(self):
        return self.root.parent / incident_store.DIRECTORY_NAME / (self.incident + '.json')

    def contradicted_on_disk(self, base, result, changes):
        """Append a consistent ``result`` receipt, then contradict it on disk.

        ``base`` is the record text before any remote test. Both the receipt
        and the record digest are recomputed, and every other receipt check
        still passes, so the result invariant is the only reason to refuse.
        Returns the tampered file text.
        """
        path = self.record_path()
        path.write_text(base)
        self.append(self.result_entry(result))
        incident_store.get(self.incident, self.root)  # the consistent record reads
        self.reseal_on_disk(
            lambda data: data[incident_store.REMOTE_TESTS][0]['receipt'].update(changes))
        text = path.read_text()
        data = json.loads(text)
        entry = data[incident_store.REMOTE_TESTS][0]
        receipt = entry['receipt']
        self.assertEqual((entry['result'], receipt['result']), (result, result))
        self.assertEqual(receipt['receipt_digest'], incident_store.remote_test_digest(receipt))
        self.assertEqual(data['record_sha256'], incident_store.record_digest(data))
        self.assertTrue(incident_store._well_formed_receipt(receipt, entry, self.incident))
        self.assertFalse(incident_store.receipt_consistent(receipt))
        return text

    def assert_invalid_code(self, caught):
        self.assertEqual(caught.exception.incident_code, incident_store.INCIDENT_RECORD_INVALID)

    def test_contradicting_receipt_on_disk_fails_every_read(self):
        base = self.record_path().read_text()
        for name, result, changes in self.CONTRADICTIONS:
            with self.subTest(name):
                self.contradicted_on_disk(base, result, changes)
                for read in (lambda: incident_store.get(self.incident, self.root),
                             lambda: incident_store.list_records(self.root),
                             lambda: incident_store.find_open(self.root)):
                    with self.assertRaises(LLMToolError) as caught:
                        read()
                    self.assert_invalid_code(caught)

    def test_contradicting_receipt_on_disk_blocks_every_update(self):
        base = self.record_path().read_text()
        for name, result, changes in self.CONTRADICTIONS:
            with self.subTest(name):
                text = self.contradicted_on_disk(base, result, changes)
                digest = json.loads(text)['record_sha256']
                updates = {
                    'append_remote_test': lambda: incident_store.append_remote_test(
                        self.incident, digest, self.entry(), self.root),
                    'append_observation': lambda: incident_store.append_observation(
                        self.incident, digest,
                        observation(TESTS, observed_at='2026-11-13T00:05:00Z'), self.root),
                    'advance_outcome': lambda: incident_store.advance_outcome(
                        self.incident, digest, incident_store.RESOLVED, at=STAMP,
                        root=self.root),
                }
                for update_name, update in updates.items():
                    with self.subTest(update_name):
                        with self.assertRaises(LLMToolError) as caught:
                            update()
                        self.assert_invalid_code(caught)
                        self.assertEqual(self.record_path().read_text(), text)

    def test_contradicting_receipt_on_disk_refuses_incident_remote_test(self):
        base = self.record_path().read_text()

        def transport(*args):
            self.fail('transport called')

        for name, result, changes in self.CONTRADICTIONS:
            with self.subTest(name):
                text = self.contradicted_on_disk(base, result, changes)
                denied = remote_test.run_incident_remote_test(
                    self.incident, sha=HEAD, modules=('test_alpha',), root=self.root,
                    config_path=None, transport=transport, now=NOW)
                self.assertEqual((denied['result'], denied['code']),
                                 ('ADMISSION_DENIED', remote_test.INCIDENT_UNREADABLE))
                self.assertFalse(denied['persisted'])
                self.assertEqual(self.record_path().read_text(), text)

    def test_tampered_entry_fails_read_even_with_a_resealed_record(self):
        self.append(self.entry())
        path = self.root.parent / incident_store.DIRECTORY_NAME / (self.incident + '.json')
        data = json.loads(path.read_text())
        data[incident_store.REMOTE_TESTS][0]['sha'] = OUTSIDE
        data['record_sha256'] = incident_store.record_digest(data)
        path.write_text(json.dumps(data))

        with self.assertRaises(LLMToolError) as caught:
            incident_store.get(self.incident, self.root)
        self.assertEqual(caught.exception.incident_code, incident_store.INCIDENT_RECORD_INVALID)

    def test_remote_tests_are_forward_only(self):
        saved = self.append(self.entry())

        def drop(body):
            body.pop(incident_store.REMOTE_TESTS)
            return body

        def rewrite(body):
            body[incident_store.REMOTE_TESTS][0]['code'] = 'REMOTE_BUSY'
            return body

        for mutate in (drop, rewrite):
            with self.subTest(mutate.__name__):
                with self.assertRaises(LLMToolError) as caught:
                    incident_store._commit(self.incident, saved['record_sha256'], mutate,
                                           self.root)
                self.assertEqual(caught.exception.incident_code,
                                 incident_store.INCIDENT_RECORD_IMMUTABLE)

    def test_full_list_is_refused(self):
        for _ in range(incident_store.MAX_REMOTE_TESTS):
            self.append(self.entry())
        with self.assertRaises(LLMToolError) as caught:
            self.append(self.entry())
        self.assertEqual(caught.exception.incident_code, incident_store.INCIDENT_RECORD_FULL)

    def test_admitted_shas_and_failing_modules(self):
        self.assertEqual(incident_store.admitted_shas(self.record), {GOOD, MID, HEAD})
        self.assertEqual(incident_store.failing_modules(self.record),
                         {'test_alpha', 'test_beta'})

    def test_failing_test_module_forms(self):
        cases = {
            'test_alpha.AlphaTests.test_one': 'test_alpha',
            'tools.llmtool.tests.test_alpha.Case.test_x': 'test_alpha',
            'tools/llmtool/tests/test_gamma.py': 'test_gamma',
            'test_delta': 'test_delta',
            '__main__.Case.test_one': None,
            'other/place/test_x.py': None,
            'Case.check': None,
            '': None,
            None: None,
        }
        for test, module in cases.items():
            with self.subTest(test=test):
                self.assertEqual(incident_store.failing_test_module(test), module)


if __name__ == '__main__':
    unittest.main()
