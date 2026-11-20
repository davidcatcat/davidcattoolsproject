"""#2188 PR 1: the pure start-failure predicate and repair-packet binding.

Design: docs/design/start-failure-retry-2188.md sections 2 and 4. The D6 probe
and the route runner are injected. All text is plain or CJK prose (Owner #763 A).
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_repair, start_failure
from llmtool.developer_routes import ClaudeRoute, RouteError, base as route_base
from llmtool.task_submission_cli import _validate_repair_packet
from llmtool.errors import LLMToolError

D6 = 'executor-start-retry'
SOURCE = 'a' * 40
HEAD = 'c' * 40
ROOT_TASK = '1' * 32
REPAIR_TASK = '2' * 32
REVIEW_TEXT = '审查意见：请为边界情况补充一个测试，并说明修改原因。'
REPORT_TEXT = '根因分析：配置读取顺序错误，导致默认值覆盖了显式设置。'


def failed_developer(**overrides):
    developer = {
        'assignment_id': '3' * 32, 'selection_source': 'PM', 'state': 'BLOCKED',
        'provider_profile': 'claude', 'provider_type': 'claude-cli',
        'model': 'claude-test-1', 'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-first', 'source_sha': SOURCE,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['src.py'], 'write_paths': ['src.py'],
        'acceptance': ['the focused test passes'], 'egress_basis': 'Plain project text.',
        'artifact': None, 'artifact_sha256': None, 'usage': None,
        'error_code': 'CLAUDE_CONFIG_INVALID', 'execution': None,
        'owner_assignment_ref': None, 'executor_task_id': D6,
    }
    developer.update(overrides)
    return developer


def start_failure_record(**developer_overrides):
    return {
        'task_id': REPAIR_TASK,
        'developer': failed_developer(**developer_overrides),
        'completion': {'state': 'WAITING_EXECUTOR', 'executor_task_id': D6,
                       'evidence': {'source_sha': SOURCE, 'result_head': None}},
    }


class Probe:
    """An injected d6_unchanged probe that records its calls."""

    def __init__(self, result=True, error=None):
        self.result, self.error, self.calls = result, error, []

    def __call__(self, source_sha, write_paths=None):
        self.calls.append((source_sha, write_paths))
        if self.error is not None:
            raise self.error
        return self.result


class StartFailureClassifyTests(unittest.TestCase):
    def assertVerdict(self, verdict, check):
        if check is None:
            self.assertEqual(verdict, start_failure.Verdict(True, None))
        else:
            self.assertEqual(verdict, start_failure.Verdict(False, check))

    def test_codes_are_the_four_pre_spawn_codes(self):
        self.assertEqual(start_failure.START_FAILURE_CODES, frozenset((
            'CLAUDE_PROFILE_INVALID', 'CLAUDE_ENV_INVALID',
            'CLAUDE_REQUEST_INVALID', 'CLAUDE_CONFIG_INVALID')))
        self.assertFalse(start_failure.START_FAILURE_CODES &
                         start_failure.NEVER_START_FAILURE_CODES)

    def test_each_start_failure_code_passes_all_six_checks(self):
        for code in sorted(start_failure.START_FAILURE_CODES):
            with self.subTest(code=code):
                record = start_failure_record(error_code=code)
                probe = Probe()
                self.assertVerdict(start_failure.record_checks(record), None)
                self.assertVerdict(start_failure.classify(record, probe), None)
                self.assertEqual(probe.calls, [(SOURCE, ['src.py'])])

    def test_check_1_claude_cli_blocked_developer_only(self):
        cases = {
            'not a record': None,
            'no developer': {'developer': None},
            'other route': {'provider_profile': 'agy'},
            'record-only claude': {'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED'},
        }
        for state in ('DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING', 'DEVELOPER_RESPONDED',
                      'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OUTCOME_UNKNOWN',
                      'DEVELOPER_OUTPUT_REJECTED'):
            cases['state ' + state] = {'state': state}
        for name, change in cases.items():
            with self.subTest(case=name):
                if change is None:
                    record = ['not', 'a', 'record']
                elif 'developer' in change:
                    record = dict(start_failure_record(), **change)
                else:
                    record = start_failure_record(**change)
                probe = Probe()
                self.assertVerdict(start_failure.classify(record, probe), 1)
                self.assertEqual(probe.calls, [])

    def test_check_2_every_never_start_and_unknown_code_is_refused(self):
        codes = sorted(start_failure.NEVER_START_FAILURE_CODES) + [
            'CLAUDE_TIMEOUT', 'BLOCKED_WRITE_ISOLATION', None, 7]
        for code in codes:
            with self.subTest(code=code):
                record = start_failure_record(error_code=code)
                self.assertVerdict(start_failure.classify(record, Probe()), 2)

    def test_check_3_no_result_progress_or_diagnosis(self):
        record = start_failure_record()
        self.assertVerdict(start_failure.record_checks(record), None)
        for field in ('artifact', 'artifact_sha256', 'usage'):
            with self.subTest(set_field=field):
                self.assertVerdict(start_failure.record_checks(
                    start_failure_record(**{field: 'x'})), 3)
            with self.subTest(missing_field=field):
                record = start_failure_record()
                del record['developer'][field]
                self.assertVerdict(start_failure.record_checks(record), 3)
        for field in ('progress', 'no_changes_diagnosis', 'blocked_reason'):
            with self.subTest(present_field=field):
                self.assertVerdict(start_failure.record_checks(
                    start_failure_record(**{field: None})), 3)
        self.assertVerdict(start_failure.record_checks(
            start_failure_record(changed_files=[])), None)
        for value in (['src.py'], None, ()):
            with self.subTest(changed_files=value):
                self.assertVerdict(start_failure.record_checks(
                    start_failure_record(changed_files=value)), 3)

    def test_check_4_no_process_facts(self):
        clean = {'status': 'BLOCKED', 'native_session_id': None, 'process_exit_code': None,
                 'stdout_sha256': None, 'elapsed_ms': 5, 'cleanup_status': 'CONFIRMED',
                 'usage_source': 'UNKNOWN', 'source_bytes': None,
                 'source_file_count': None, 'source_manifest_sha256': None}
        self.assertVerdict(start_failure.record_checks(
            start_failure_record(execution=clean)), None)
        misses = {
            'native session': {'native_session_id': '0123456789abcdef0123'},
            'exit code': {'process_exit_code': 1},
            'stdout digest': {'stdout_sha256': 'd' * 64},
            'cleanup unknown': {'cleanup_status': 'UNKNOWN'},
        }
        for name, change in misses.items():
            with self.subTest(case=name):
                self.assertVerdict(start_failure.record_checks(
                    start_failure_record(execution=dict(clean, **change))), 4)
        partial = dict(clean)
        del partial['stdout_sha256']
        self.assertVerdict(start_failure.record_checks(
            start_failure_record(execution=partial)), 4)
        self.assertVerdict(start_failure.record_checks(
            start_failure_record(execution='DONE')), 4)
        record = start_failure_record()
        del record['developer']['execution']
        self.assertVerdict(start_failure.record_checks(record), 4)

    def test_check_5_waiting_executor_with_no_result_or_later_step(self):
        cases = {
            'no completion': lambda record: record.update(completion=None),
            'other state': lambda record: record['completion'].update(state='WAITING_PM'),
            'result head': lambda record: record['completion']['evidence'].update(
                result_head=HEAD),
            'no result head key': lambda record: record['completion']['evidence'].pop(
                'result_head'),
            'no evidence': lambda record: record['completion'].update(evidence=None),
            'completion event': lambda record: record.update(
                completion_events=[{'event_id': 'e'}]),
            'tombstone': lambda record: record.update(
                completion_event_tombstones=[{'event_id': 'e'}]),
            'repair claim': lambda record: record.update(
                review_repair_claims=[{'pr': 1}]),
            'merge attempt': lambda record: record.update(merge_attempt={'pr': 1}),
            'abandonment': lambda record: record.update(abandonment={'reason': 'stop'}),
            'published pr': lambda record: record.update(published_pr={'pr': 1}),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                record = start_failure_record()
                change(record)
                self.assertVerdict(start_failure.classify(record, Probe()), 5)
        record = start_failure_record()
        record.update(completion_events=[], merge_attempt=None, published_pr=None)
        self.assertVerdict(start_failure.classify(record, Probe()), None)

    def test_check_6_only_an_exact_true_probe_passes(self):
        for name, probe in (('false', Probe(False)), ('none', Probe(None)),
                            ('truthy', Probe(1)),
                            ('raising', Probe(error=LLMToolError('probe failed'))),
                            ('raising os', Probe(error=OSError('gone')))):
            with self.subTest(probe=name):
                self.assertVerdict(start_failure.classify(start_failure_record(), probe), 6)
                self.assertEqual(probe.calls, [(SOURCE, ['src.py'])])
        # record_checks never calls a probe: check 6 is the caller's live D6 check.
        self.assertVerdict(start_failure.record_checks(start_failure_record()), None)


class _Boundary:
    def prepare(self, root, paths, state_dirs=()):
        return (), 'e' * 64

    def verify(self, root, paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': 'e' * 64}


class RunRouteConfirmsPreSpawnCodesTests(unittest.TestCase):
    """Design section 2 check 2: each code is raised before the runner starts."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def _run(self, route_config, **request_changes):
        calls = []

        def runner(*args, **kwargs):
            calls.append(args)
            raise AssertionError('the provider process must not start')

        request = {'root': str(self.root), 'model': 'claude-test-1', 'effort': 'low',
                   'expected_branch': 'task-branch', 'expected_head': SOURCE,
                   'write_paths': ['src.py'], 'executable': '/usr/bin/true',
                   'prompt': 'Plain task text.'}
        request.update(request_changes)
        git = SimpleNamespace(branch='task-branch', head=SOURCE, clean=True)
        with patch.object(route_base, 'resolve_route_timeout', return_value=60), \
                patch.object(route_base.task_completion, 'collect_git_snapshot',
                             return_value=git), \
                patch.object(route_base, '_git_status_paths', return_value=()):
            with self.assertRaises(RouteError) as caught:
                route_base.run_route(ClaudeRoute(route_config), request,
                                     gate=lambda: True, runner=runner,
                                     boundary=_Boundary(),
                                     snapshot=lambda root: {'files': {}})
        self.assertEqual(calls, [])
        self.assertIsNone(caught.exception.execution_facts)
        return caught.exception.code

    def test_each_code_is_raised_before_the_runner(self):
        config = {'executable': '/usr/bin/true'}
        self.assertEqual(self._run(config, model='other-model'), 'CLAUDE_PROFILE_INVALID')
        self.assertEqual(self._run(config, executable='relative-name'),
                         'CLAUDE_REQUEST_INVALID')
        self.assertEqual(self._run(config, prompt='x' * 65537), 'CLAUDE_REQUEST_INVALID')
        self.assertEqual(self._run({}), 'CLAUDE_CONFIG_INVALID')
        self.assertEqual(self._run({'executable': '/usr/bin/true',
                                    'config_dir': 'relative-dir'}),
                         'CLAUDE_CONFIG_INVALID')
        with patch.dict(os.environ, {'HOME': 'relative-home'}):
            self.assertEqual(self._run(config), 'CLAUDE_ENV_INVALID')


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def review_packet():
    return {'task': ROOT_TASK, 'pr': 41, 'executor_task': D6, 'failed_head': HEAD,
            'version': 2, 'verdict_comment_id': 777, 'findings_count': 1,
            'reviewer_text_excerpt_untrusted_data': REVIEW_TEXT}


def review_claim(packet, **overrides):
    claim = {'executor_task': D6, 'pr': 41, 'head_sha': HEAD, 'version': 2,
             'attempt': 1, 'state': 'DISPATCHED', 'comment_id': 777,
             'packet_sha256': sha256_text(start_failure.canonical(packet)),
             'claimed_at': '2026-11-17T00:00:00Z', 'family': 'pr-41',
             'repair_task_id': REPAIR_TASK}
    claim.update(overrides)
    return claim


def high_debug_entry(report, **overrides):
    entry = {'family': 'pr-41', 'status': 'RCA_CONFIRMED', 'examined_sha': HEAD,
             'report': report, 'report_sha256': sha256_text(report)}
    entry.update(overrides)
    return entry


class RepairPacketBindingTests(unittest.TestCase):
    def test_review_packet_binds_identity_and_base_digest(self):
        packet = review_packet()
        claim = review_claim(packet)
        root = {'task_id': ROOT_TASK}
        self.assertTrue(start_failure.repair_packet_matches(packet, claim, root))
        misses = {
            'base digest': dict(packet, reviewer_text_excerpt_untrusted_data=REVIEW_TEXT + '。'),
            'task': dict(packet, task='4' * 32),
            'pr': dict(packet, pr=42),
            'executor task': dict(packet, executor_task='other-d6'),
            'failed head': dict(packet, failed_head='d' * 40),
            'version': dict(packet, version=3),
            'comment': dict(packet, verdict_comment_id=778),
        }
        for name, other in misses.items():
            with self.subTest(miss=name):
                # The claim digest is recomputed for identity misses, so only
                # the identity check can refuse them.
                rebound = (claim if name == 'base digest' else
                           dict(claim, packet_sha256=sha256_text(start_failure.canonical(other))))
                self.assertFalse(start_failure.repair_packet_matches(other, rebound, root))
        self.assertFalse(start_failure.repair_packet_matches(
            packet, dict(claim, packet_sha256='0' * 64), root))
        no_comment_key = dict(claim)
        del no_comment_key['comment_id']
        self.assertFalse(start_failure.repair_packet_matches(packet, no_comment_key, root))
        # A High Debug claim needs a matching root entry; a malformed list has none.
        report_sha = sha256_text(REPORT_TEXT)
        with_debug = dict(packet, high_debug_report_untrusted_data=REPORT_TEXT,
                          high_debug_report_sha256=report_sha)
        self.assertFalse(start_failure.repair_packet_matches(
            with_debug, dict(claim, attempt=2, high_debug_report_sha256=report_sha),
            {'task_id': ROOT_TASK, 'high_debug': 'not a list'}))

    def test_ec2_test_packet_uses_zero_version_and_comment(self):
        packet = dict(review_packet(), version=0, verdict_comment_id=0, findings_count=0,
                      reviewer_text_excerpt_untrusted_data='测试失败：断言不成立。')
        claim = review_claim(packet, comment_id=None, version=1, family='pr-41-ec2')
        root = {'task_id': ROOT_TASK}
        self.assertTrue(start_failure.repair_packet_matches(packet, claim, root))
        for change in ({'version': 1}, {'verdict_comment_id': 5}):
            with self.subTest(change=change):
                other = dict(packet, **change)
                self.assertFalse(start_failure.repair_packet_matches(
                    other, dict(claim, packet_sha256=sha256_text(
                        start_failure.canonical(other))), root))

    def _ci(self, **failure_changes):
        failure = {'head': HEAD, 'check_name': review_repair.CI_REPAIR_CHECKS[0],
                   'check_run_id': 9001, 'conclusion': 'failure',
                   'excerpt': 'FAILED test_config_order: values differ'}
        failure.update(failure_changes)
        return review_repair._ci_failure({'task_id': ROOT_TASK}, executor_task=D6,
                                         pr_number=41, ci_failure=failure)

    def test_ci_packet_binds_the_rebuilt_identity_digest(self):
        packet, identity_sha = self._ci()
        self.assertTrue(packet['reviewer_text_excerpt_untrusted_data'].startswith(
            'CI_CHECK_FAILURE '))
        claim = review_claim(packet, comment_id=None, version=1, family='pr-41-ec2',
                             packet_sha256=identity_sha)
        root = {'task_id': ROOT_TASK}
        self.assertTrue(start_failure.repair_packet_matches(packet, claim, root))
        # The identity binds the header, not the untrusted excerpt body.
        body_changed = dict(packet, reviewer_text_excerpt_untrusted_data=(
            packet['reviewer_text_excerpt_untrusted_data'] + '\n更多输出'))
        self.assertTrue(start_failure.repair_packet_matches(body_changed, claim, root))
        header, _, rest = packet['reviewer_text_excerpt_untrusted_data'].partition('\n')
        misses = {
            'run id': header.replace('run=9001', 'run=9002'),
            'check name': header.replace(review_repair.CI_REPAIR_CHECKS[0], 'check (other)'),
            'head': header.replace('head=' + HEAD, 'head=' + 'd' * 40),
            'extra text': header + ' extra',
            'leading zero': header.replace('run=9001', 'run=09001'),
        }
        for name, bad_header in misses.items():
            with self.subTest(miss=name):
                other = dict(packet, reviewer_text_excerpt_untrusted_data=bad_header + '\n' + rest)
                self.assertFalse(start_failure.repair_packet_matches(other, claim, root))
        # A CI packet is never bound by the plain base digest.
        plain = dict(claim, packet_sha256=sha256_text(start_failure.canonical(packet)))
        self.assertFalse(start_failure.repair_packet_matches(packet, plain, root))
        other_identity = self._ci(check_run_id=9002)[1]
        self.assertFalse(start_failure.repair_packet_matches(
            packet, dict(claim, packet_sha256=other_identity), root))

    def test_repair_two_binds_the_high_debug_entry_and_text(self):
        report = REPORT_TEXT * 300  # longer than the 6000-character excerpt
        self.assertGreater(len(report), 6000)
        base = review_packet()
        entry = high_debug_entry(report)
        packet = dict(base, high_debug_report_untrusted_data=report[:6000],
                      high_debug_report_sha256=entry['report_sha256'])
        claim = review_claim(base, attempt=2,
                             high_debug_report_sha256=entry['report_sha256'])
        root = {'task_id': ROOT_TASK, 'high_debug': [entry]}
        self.assertTrue(start_failure.repair_packet_matches(packet, claim, root))
        misses = {
            'high debug digest': (dict(packet, high_debug_report_sha256='0' * 64), claim, root),
            'report text': (dict(packet, high_debug_report_untrusted_data=report[:5999]),
                            claim, root),
            'missing entry': (packet, claim, {'task_id': ROOT_TASK}),
            'entry not confirmed': (packet, claim, dict(root, high_debug=[
                high_debug_entry(report, status='RCA_UNCONFIRMED')])),
            'entry other head': (packet, claim, dict(root, high_debug=[
                high_debug_entry(report, examined_sha='d' * 40)])),
            'entry other family': (packet, claim, dict(root, high_debug=[
                high_debug_entry(report, family='pr-99')])),
            'entry no pr': (packet, claim, dict(root, high_debug=[
                high_debug_entry(report, no_pr=True)])),
            'packet without high debug': (base, claim, root),
            'claim without high debug': (packet, review_claim(base, attempt=2), root),
            'base digest': (packet, dict(claim, packet_sha256=sha256_text(
                start_failure.canonical(packet))), root),
        }
        for name, (other_packet, other_claim, other_root) in misses.items():
            with self.subTest(miss=name):
                self.assertFalse(start_failure.repair_packet_matches(
                    other_packet, other_claim, other_root))

    def test_malformed_inputs_never_match(self):
        packet = review_packet()
        claim = review_claim(packet)
        for args in ((None, claim, {'task_id': ROOT_TASK}),
                     (packet, None, {'task_id': ROOT_TASK}),
                     (packet, claim, None),
                     (dict(packet, findings_count='1'), claim, {'task_id': ROOT_TASK})):
            with self.subTest(args=args):
                self.assertFalse(start_failure.repair_packet_matches(*args))


class RepairPacketEncodingTests(unittest.TestCase):
    def test_shape_rules_equal_the_delegate_validator(self):
        base = review_packet()
        report_sha = sha256_text(REPORT_TEXT)
        samples = [
            base,
            dict(base, high_debug_report_untrusted_data=REPORT_TEXT,
                 high_debug_report_sha256=report_sha),
            dict(base, high_debug_report_untrusted_data=REPORT_TEXT),
            dict(base, high_debug_report_untrusted_data='x' * 6001,
                 high_debug_report_sha256=report_sha),
            dict(base, high_debug_report_untrusted_data=REPORT_TEXT,
                 high_debug_report_sha256='Z' * 64),
            dict(base, reviewer_text_excerpt_untrusted_data='x' * 2000),
            dict(base, reviewer_text_excerpt_untrusted_data='x' * 2001),
            dict(base, failed_head='c' * 39),
            dict(base, pr=True),
            dict(base, pr=None),
            dict(base, pr=0),
            dict(base, extra='plain'),
            {key: value for key, value in base.items() if key != 'findings_count'},
            ['not', 'a', 'packet'],
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                try:
                    _validate_repair_packet(sample)
                    expected = True
                except LLMToolError:
                    expected = False
                self.assertEqual(start_failure.repair_packet_shape_ok(sample), expected)

    def test_decode_accepts_only_the_canonical_text(self):
        packet = dict(review_packet(), reviewer_text_excerpt_untrusted_data='单独代理项\ud800之后')
        text = start_failure.canonical(packet)
        self.assertTrue(text.isascii())
        self.assertIn('\\ud800', text)
        self.assertEqual(start_failure.decode_repair_packet(text), packet)
        for name, other in (
                ('spacing', json.dumps(packet, ensure_ascii=True, sort_keys=True)),
                ('order', json.dumps(dict(reversed(sorted(packet.items()))),
                                     ensure_ascii=True, separators=(',', ':'))),
                ('non ascii', json.dumps(dict(packet, reviewer_text_excerpt_untrusted_data=REVIEW_TEXT),
                                         ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'))),
                ('upper escape', text.replace('\\ud800', '\\uD800')),
                ('duplicate key', '{"pr":41,' + text[1:]),
                ('shape', start_failure.canonical(dict(packet, extra='plain'))),
                ('not json', 'plain text'),
                ('object', packet)):
            with self.subTest(case=name):
                self.assertIsNone(start_failure.decode_repair_packet(other))


if __name__ == '__main__':
    unittest.main()
