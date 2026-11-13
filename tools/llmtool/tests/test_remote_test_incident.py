"""#1706 R9: EC2 remote test of a main incident's failing modules.

Every test uses a real ``main_incident_store`` in a temporary directory and a
fake SSH transport; nothing reaches EC2, Git, GitHub or CI.
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
        excerpt = 'AssertionError: 1 != 2'
        stdout = self.receipt(result='FAIL', failure_excerpt=excerpt)
        with patch.object(remote_test, '_emitted_sensitive', return_value=True) as screen:
            refused = self.run_it(transport=self.transport(stdout))
        screen.assert_called_once_with(excerpt)
        self.assertEqual((refused['result'], refused['code']), ('UNKNOWN', 'RECEIPT_INVALID'))
        self.assertIsNone(refused['receipt'])

        kept = self.run_it(transport=self.transport(stdout))
        self.assertEqual(kept['result'], 'FAIL')
        self.assertEqual(kept['receipt']['failure_excerpt'], excerpt)

    # output screening before recording (PR #1763, CWE-532) -----------------

    def test_final_lines_outside_the_summary_grammar_are_redacted(self):
        cases = (
            ('PASS', ['OK', 'OK (skipped=2)', 'loader note OK'],
             ['OK', 'OK (skipped=2)', remote_test.FINAL_REDACTED]),
            ('FAIL', ['FAILED (failures=1, errors=2)', 'NO_FINAL',
                      'ERROR: test_one (test_alpha.Case.test_one)'],
             ['FAILED (failures=1, errors=2)', 'NO_FINAL', remote_test.FINAL_REDACTED]),
        )
        for result_name, finals, expected in cases:
            with self.subTest(result=result_name):
                stdout = self.receipt(result=result_name, final=finals)
                # The shared Task-path validator is unchanged and keeps raw lines.
                self.assertEqual(remote_test._validate_remote_receipt(
                    stdout, sha=HEAD, modules=('test_alpha',))['final'], finals)
                with patch.object(remote_test, '_emitted_sensitive',
                                  wraps=remote_test._emitted_sensitive) as screen:
                    result = self.run_it(transport=self.transport(stdout))
                screened = [call.args[0] for call in screen.call_args_list]
                for line in finals:
                    self.assertIn(line, screened)
                self.assertEqual((result['result'], result['code']), (result_name, None))
                self.assertTrue(result['persisted'])
                receipt = self.stored()[incident_store.REMOTE_TESTS][-1]['receipt']
                self.assertEqual(receipt['final'], expected)
                self.assertEqual(receipt['receipt_digest'],
                                 incident_store.remote_test_digest(receipt))

    def test_flagged_final_line_refuses_the_receipt(self):
        flagged = 'FAILED (errors=7)'
        stdout = self.receipt(result='FAIL', final=[flagged])
        with patch.object(remote_test, '_emitted_sensitive',
                          side_effect=lambda value: value == flagged) as screen:
            result = self.run_it(transport=self.transport(stdout))
        screen.assert_any_call(flagged)
        self.assertEqual((result['result'], result['code']),
                         ('UNKNOWN', remote_test.INCIDENT_RECEIPT_SENSITIVE))
        self.assertIsNone(result['receipt'])
        self.assertTrue(result['persisted'])
        [entry] = self.stored()[incident_store.REMOTE_TESTS]
        self.assertIsNone(entry['receipt'])
        self.assertNotIn(flagged, self.record_path().read_text())

    def test_failure_excerpt_is_screened_again_before_recording(self):
        excerpt = 'AssertionError: 3 != 4'
        stdout = self.receipt(result='FAIL', failure_excerpt=excerpt)
        seen = []

        def screen(value):
            # The shared validator passes it; the incident screen flags it.
            seen.append(value)
            return value == excerpt and seen.count(excerpt) > 1

        with patch.object(remote_test, '_emitted_sensitive', side_effect=screen):
            result = self.run_it(transport=self.transport(stdout))
        self.assertEqual(seen, [excerpt, 'FAILED (failures=1)', excerpt])
        self.assertEqual((result['result'], result['code']),
                         ('UNKNOWN', remote_test.INCIDENT_RECEIPT_SENSITIVE))
        self.assertIsNone(result['receipt'])
        self.assertNotIn(excerpt, self.record_path().read_text())

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

    def receipt_entry(self, **receipt_changes):
        receipt = {'result': 'PASS', 'source_sha': HEAD, 'modules': ['test_alpha'],
                   'incident_id': self.incident, 'ran_total': 2}
        receipt['receipt_digest'] = incident_store.remote_test_digest(receipt)
        receipt.update(receipt_changes)
        return self.entry(result='PASS', code=None, receipt=receipt)

    def append(self, entry):
        current = incident_store.get(self.incident, self.root)
        return incident_store.append_remote_test(
            self.incident, current['record_sha256'], entry, self.root)

    def assert_invalid(self, entry):
        before = incident_store.get(self.incident, self.root)['record_sha256']
        with self.assertRaises(LLMToolError) as caught:
            self.append(entry)
        self.assertEqual(caught.exception.incident_code, incident_store.INCIDENT_RECORD_INVALID)
        self.assertEqual(incident_store.get(self.incident, self.root)['record_sha256'], before)

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
            'receipt digest': self.receipt_entry(ran_total=3),
            'receipt other incident': self.receipt_entry(incident_id='mi-' + OUTSIDE),
            'receipt with code': dict(self.receipt_entry(), code='SSH_FAILED'),
        }
        for name, entry in cases.items():
            with self.subTest(name):
                self.assert_invalid(entry)

    def output_entry(self, final=('FAILED (failures=1)',), excerpt='AssertionError: 1 != 2'):
        receipt = {'result': 'FAIL', 'source_sha': HEAD, 'modules': ['test_alpha'],
                   'incident_id': self.incident, 'final': final if type(final) is str
                   else list(final), 'failure_excerpt': excerpt}
        receipt['receipt_digest'] = incident_store.remote_test_digest(receipt)
        return self.entry(result='FAIL', code=None, receipt=receipt)

    def test_receipt_output_fields_go_through_the_screen(self):
        for flagged in ('FAILED (failures=1)', 'AssertionError: 1 != 2'):
            with self.subTest(flagged=flagged):
                with patch.object(incident_store.task_store, '_emitted_sensitive',
                                  side_effect=lambda value: value == flagged) as screen:
                    self.assert_invalid(self.output_entry())
                screen.assert_any_call(flagged)
        self.assert_invalid(self.output_entry(final='FAILED (failures=1)'))
        self.assert_invalid(self.output_entry(final=[7]))
        self.assert_invalid(self.output_entry(excerpt=None))

        saved = self.append(self.output_entry())
        self.assertEqual(saved[incident_store.REMOTE_TESTS][0]['receipt']['final'],
                         ['FAILED (failures=1)'])

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
