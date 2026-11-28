"""#2793 A13.2: remote validation derives test modules from the TestProfile.

Covers the regression table of docs/design/a13-remote-test-profile-boundary.md
for ``remote_test_part1`` (declared and stored-evidence derivation, required
coverage) and ``remote_test_part2.run_remote_test`` (admission and refusal).
"""

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import remote_test, task_advance, task_submission_cli, test_profile
from llmtool.test_profile import TestProfile, TestProfileError

try:
    from tests import test_remote_test as remote_fixtures
except ImportError:
    import test_remote_test as remote_fixtures

SHA = remote_fixtures.SHA
PR = 425
INVALID = 'TEST_PROFILE_INVALID'
DIR = 'tools/llmtool/tests/'
LONGEST = 'test_' + 'x' * 80

# Default-profile corpus and the only stems the previous hard-coded paths gave.
CORPUS = (
    DIR + 'test_beta_2793.py',
    DIR + 'test_alpha_2793.py',
    DIR + LONGEST + '.py',
    DIR + LONGEST + 'x.py',
    DIR + 'test_Upper_2793.py',
    DIR + 'test_.py',
    DIR + 'helper_2793.py',
    DIR + 'sub/test_nested_2793.py',
    DIR + 'test_suffix_2793.pyc',
    DIR + 'test_newline_2793.py\n',
    'prefix/' + DIR + 'test_prefixed_2793.py',
    'scripts/test_script_2793.py',
    'custom/tests/test_custom_2793.py',
)
CORPUS_STEMS = ('test_alpha_2793', 'test_beta_2793', LONGEST)

CUSTOM = TestProfile((
    (re.compile(r'custom/tools/test_[a-z0-9_]+\.py'), 'script'),
    (re.compile(r'custom/tests/test_[a-z0-9_]+\.py', re.IGNORECASE), 'unittest'),
))

# Overlapping entries: the script entry comes first and owns FIRST.
FIRST = 'custom/tests/test_first_2793.py'
SCRIPT_ENTRY = (re.compile(r'custom/tests/test_first_2793\.py'), 'script')
UNIT_ENTRY = (re.compile(r'custom/tests/test_[a-z0-9_]+\.py'), 'unittest')
SCRIPT_FIRST = TestProfile((SCRIPT_ENTRY, UNIT_ENTRY))
UNIT_FIRST = TestProfile((UNIT_ENTRY, SCRIPT_ENTRY))


def forged(entries):
    """A TestProfile-typed value that skipped constructor validation."""
    profile = object.__new__(TestProfile)
    object.__setattr__(profile, '_entries', entries)
    return profile


def missing_provider():
    class Missing:
        def __enter__(self):
            self.patcher = patch.object(test_profile, 'default_profile')
            self.patcher.start()
            del test_profile.default_profile

        def __exit__(self, *_args):
            self.patcher.stop()
    return Missing()


def unusable_cases():
    """(label, context manager) pairs, each making the default profile unusable."""
    def provider(**kwargs):
        return patch.object(test_profile, 'default_profile', **kwargs)

    pattern = re.compile(r'tools/llmtool/tests/test_[a-z0-9_]+\.py\Z')
    yield 'missing', missing_provider()
    yield 'raising', provider(side_effect=RuntimeError('profile unavailable'))
    yield 'invalid_construction', provider(side_effect=TestProfileError())
    yield 'none', provider(return_value=None)
    yield 'wrong_type', provider(return_value=object())
    yield 'unset_entries', provider(return_value=object.__new__(TestProfile))
    for label, entries in (
            ('empty', ()),
            ('shape', ((pattern,),)),
            ('bytes_pattern', ((re.compile(rb'x'), 'unittest'),)),
            ('text_pattern', (('x', 'unittest'),)),
            ('kind', ((pattern, 'unittest'), (pattern, 'pytest')))):
        yield label, provider(return_value=forged(entries))


def declared(*paths):
    return {'developer': {'read_paths': list(paths), 'write_paths': []}}


def evidence_only(*paths):
    return {'developer': {}, 'completion': {'evidence': {
        'tests': [{'path': path} for path in paths]}}}


def changed_files(*entries):
    """A GitHub reader for one open PR at SHA; entries are (filename, status)."""
    def get(path):
        if path == f'pulls/{PR}':
            return {'number': PR, 'head': {'sha': SHA}, 'state': 'open',
                    'merged': False, 'changed_files': len(entries)}
        if path.startswith(f'pulls/{PR}/files?per_page='):
            return [{'filename': name, 'status': status} for name, status in entries]
        return []
    return get


def receipt_bytes(modules, *, skipped=0):
    final = f'OK (skipped={skipped})' if skipped else 'OK'
    return json.dumps({
        'result': 'PASS', 'exit_code': 0, 'source_sha': SHA, 'tree': 'c' * 40,
        'fetch_head': SHA, 'modules': list(modules), 'identity': {'uid': 65534},
        'started_at': '2026-11-29T00:00:00Z', 'ended_at': '2026-11-29T00:00:01Z',
        'elapsed_s': 1.0, 'ran_total': len(modules), 'final': [final] * len(modules),
        'failures': [], 'log_bytes': 0, 'log_sha256': 'd' * 64,
        'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
        'module_results': [dict(module=name, ran=1, skipped=skipped, final=final)
                           for name in modules],
    }, separators=(',', ':')).encode()


class DefaultProfileDerivationTests(unittest.TestCase):
    def test_declared_paths_keep_the_previous_results(self):
        self.assertEqual(remote_test._task_path_modules(declared(*CORPUS)), CORPUS_STEMS)
        self.assertEqual(remote_test._admitted_modules(declared(*CORPUS)), CORPUS_STEMS)
        self.assertEqual(remote_test.remote_required_modules(declared(*CORPUS)),
                         CORPUS_STEMS)

    def test_write_paths_and_non_text_entries_are_handled_as_before(self):
        task = {'developer': {'read_paths': [None, 7, DIR + 'test_alpha_2793.py'],
                              'write_paths': [DIR + 'test_alpha_2793.py',
                                              DIR + 'test_beta_2793.py']}}
        self.assertEqual(remote_test._task_path_modules(task),
                         ('test_alpha_2793', 'test_beta_2793'))

    def test_stored_evidence_paths_keep_the_previous_results(self):
        task = evidence_only(*CORPUS, None, '')
        self.assertEqual(remote_test._admitted_modules(task), CORPUS_STEMS)
        # The evidence fallback admits modules but never adds required coverage.
        self.assertEqual(remote_test.remote_required_modules(task), ())

    def test_callback_evidence_is_still_preferred(self):
        task = evidence_only(DIR + 'test_alpha_2793.py')
        task['developer'] = {'direct_result_callback': {
            'state': 'PM_EVIDENCE_READY',
            'evidence': {'tests': [{'path': DIR + 'test_beta_2793.py'}]}}}
        self.assertEqual(remote_test._admitted_modules(task), ('test_beta_2793',))


class CoverageUnionTests(unittest.TestCase):
    def test_declared_and_pr_changed_modules_form_the_required_set(self):
        task = declared(DIR + 'test_alpha_2793.py')
        extra = remote_test._pr_changed_test_modules_for_admission
        with patch.object(remote_test, '_github_get',
                          changed_files((DIR + 'test_beta_2793.py', 'added'))):
            modules = extra(PR, SHA)
        self.assertEqual(modules, ('test_beta_2793',))
        self.assertEqual(remote_test.remote_required_modules(task, modules),
                         ('test_alpha_2793', 'test_beta_2793'))

    def test_removed_file_adds_nothing_but_declared_stem_stays_required(self):
        get = changed_files((DIR + 'test_alpha_2793.py', 'removed'),
                            (DIR + 'test_beta_2793.py', 'removed'))
        with patch.object(remote_test, '_github_get', get):
            extra = remote_test._pr_changed_test_modules_for_admission(PR, SHA)
        self.assertEqual(extra, ())
        self.assertEqual(remote_test.remote_required_modules(
            declared(DIR + 'test_alpha_2793.py'), extra), ('test_alpha_2793',))

    def test_darwin_only_module_is_excluded_from_required_coverage(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'test_mac_2793.py').write_text(
                'import sys\nimport unittest\n\n'
                "@unittest.skipUnless(sys.platform == 'darwin', 'Darwin only')\n"
                'class MacTests(unittest.TestCase):\n'
                '    def test_one(self):\n        pass\n')
            manifest = root / 'mac-modules.txt'
            manifest.write_text('test_mac_2793.MacTests\n')
            task = declared(DIR + 'test_mac_2793.py', DIR + 'test_alpha_2793.py')
            with patch.object(remote_test, '_MAC_MANIFEST', manifest), \
                    patch.object(remote_test, '_TESTS_DIR', root):
                self.assertEqual(remote_test.remote_required_modules(task),
                                 ('test_alpha_2793',))
                self.assertEqual(remote_test._task_path_modules(task),
                                 ('test_alpha_2793', 'test_mac_2793'))


class NonDefaultProfileTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(test_profile, 'default_profile', return_value=CUSTOM)
        patcher.start()
        self.addCleanup(patcher.stop)

    PATHS = (
        'custom/tests/test_alpha_2793.py',
        'CUSTOM/tests/test_flagged_2793.py',
        'custom/tests/test_Upper_2793.py',
        'custom/tests/test_whole_2793.py.bak',
        'prefix/custom/tests/test_prefixed_2793.py',
        'custom/tools/test_script_2793.py',
        DIR + 'test_default_2793.py',
    )
    STEMS = ('test_alpha_2793', 'test_flagged_2793')

    def test_modules_move_to_the_profile_directory(self):
        self.assertEqual(remote_test._task_path_modules(declared(*self.PATHS)), self.STEMS)
        self.assertEqual(remote_test._admitted_modules(evidence_only(*self.PATHS)),
                         self.STEMS)
        self.assertEqual(remote_test.remote_required_modules(declared(*self.PATHS)),
                         self.STEMS)

    def test_validation_and_admission_agree_on_the_same_paths(self):
        get = changed_files(*((path, 'added') for path in self.PATHS))
        modules, code = task_advance._pr_changed_test_modules(
            PR, SHA, get, get(f'pulls/{PR}'))
        self.assertIsNone(code)
        self.assertEqual(modules, remote_test._task_path_modules(declared(*self.PATHS)))

    def test_script_only_profile_admits_no_module(self):
        scripts = TestProfile(((re.compile(r'.*\.py'), 'script'),))
        with patch.object(test_profile, 'default_profile', return_value=scripts):
            self.assertEqual(remote_test._admitted_modules(
                evidence_only(DIR + 'test_alpha_2793.py')), ())
            self.assertEqual(remote_test.remote_required_modules(
                declared(DIR + 'test_alpha_2793.py')), ())

    def test_profiles_compare_by_pattern_source_flags_kind_and_order(self):
        script, unit = CUSTOM.entries
        copy = tuple((re.compile(p.pattern, p.flags), kind) for p, kind in CUSTOM.entries)
        self.assertEqual(TestProfile(copy), CUSTOM)
        for label, entries in (
                ('source', (script, (re.compile(r'other\.py', re.IGNORECASE), 'unittest'))),
                ('flags', (script, (re.compile(unit[0].pattern), 'unittest'))),
                ('kind', (script, (unit[0], 'script'))),
                ('order', (unit, script))):
            with self.subTest(label):
                self.assertNotEqual(TestProfile(entries), CUSTOM)

    def test_entry_order_and_whole_path_matching(self):
        self.assertEqual(SCRIPT_FIRST.kind_for(FIRST), 'script')
        self.assertEqual(UNIT_FIRST.kind_for(FIRST), 'unittest')
        self.assertIsNone(SCRIPT_FIRST.kind_for(FIRST + '.bak'))
        self.assertIsNone(SCRIPT_FIRST.kind_for('prefix/' + FIRST))
        other = 'custom/tests/test_other_2793.py'
        paths = (FIRST, other, FIRST + '.bak', 'prefix/' + FIRST)
        get = changed_files(*((path, 'added') for path in paths))
        # The first matching entry decides in every remote derivation.
        for label, profile, stems in (
                ('script_first', SCRIPT_FIRST, ('test_other_2793',)),
                ('unit_first', UNIT_FIRST, ('test_first_2793', 'test_other_2793'))):
            with self.subTest(label), \
                    patch.object(test_profile, 'default_profile', return_value=profile), \
                    patch.object(remote_test, '_github_get', get):
                self.assertEqual(remote_test._task_path_modules(declared(*paths)), stems)
                self.assertEqual(remote_test._admitted_modules(declared(*paths)), stems)
                self.assertEqual(remote_test._admitted_modules(evidence_only(*paths)),
                                 stems)
                self.assertEqual(remote_test.remote_required_modules(declared(*paths)),
                                 stems)
                self.assertEqual(
                    remote_test._pr_changed_test_modules_for_admission(PR, SHA), stems)

    def test_removed_unittest_path_does_not_admit_a_script_path_of_the_same_stem(self):
        get = changed_files((FIRST, 'added'),
                            ('custom/tests/sub/test_first_2793.py', 'removed'))
        nested = (re.compile(r'custom/tests/(?:sub/)?test_[a-z0-9_]+\.py'), 'unittest')
        with patch.object(test_profile, 'default_profile',
                          return_value=TestProfile((SCRIPT_ENTRY, nested))), \
                patch.object(remote_test, '_github_get', get):
            self.assertEqual(
                remote_test._pr_changed_test_modules_for_admission(PR, SHA), ())


class UnusableProfileDerivationTests(unittest.TestCase):
    def test_nothing_is_derived_and_default_paths_are_no_fallback(self):
        path = DIR + 'test_alpha_2793.py'
        for label, unusable in unusable_cases():
            with self.subTest(label), unusable:
                self.assertEqual(remote_test._task_path_modules(declared(path)), ())
                self.assertEqual(remote_test._admitted_modules(declared(path)), ())
                self.assertEqual(remote_test._admitted_modules(evidence_only(path)), ())
                self.assertIsNone(remote_test.remote_required_modules(declared(path)))
                self.assertIsNone(remote_test.remote_required_modules(
                    declared(), ('test_alpha_2793',)))
                self.assertEqual(task_submission_cli._remote_receipt_test_policy(
                    declared(path), read_paths=(path,)), 'LOCAL')

    def test_invalid_entries_are_refused_at_construction(self):
        pattern = re.compile(r'x')
        for entries in ((), ((pattern,),), (('x', 'unittest'),), ((pattern, 'pytest'),)):
            with self.subTest(entries=len(entries)):
                with self.assertRaises(TestProfileError) as caught:
                    TestProfile(entries)
                self.assertEqual(str(caught.exception), INVALID)


class RunRemoteTestProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / 'remote-test.json'
        self.config.write_text(json.dumps({
            'host': 'ec2.example.org', 'user': 'runner',
            'repo_path': '/home/runner/project', 'python': '/usr/bin/python3',
        }))
        self.config.chmod(0o600)
        self.store = remote_fixtures.Store()
        self.store.task['developer']['read_paths'] = [DIR + 'test_alpha_2793.py']
        self.executed = []
        self.skipped = 0

    def transport(self, argv, _script, _timeout):
        modules = argv[14:]
        self.executed.append(modules)
        return SimpleNamespace(stdout=receipt_bytes(modules, skipped=self.skipped),
                               stderr=b'', returncode=0)

    def run_it(self, modules=(), *, pr=None, get=None, hosted=False):
        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)), \
                patch.object(remote_test, '_github_get',
                             changed_files() if get is None else get):
            return remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=SHA,
                ref=f'refs/pull/{PR}/head', modules=modules, pr=pr, store=self.store,
                config_path=self.config, transport=self.transport, hosted=hosted)

    def test_declared_subset_runs_without_widening(self):
        get = changed_files((DIR + 'test_beta_2793.py', 'added'))
        with patch.object(remote_test, '_pr_changed_test_modules_for_admission') as widen:
            result = self.run_it(('test_alpha_2793',), pr=PR, get=get)
        widen.assert_not_called()
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(self.executed, [['test_alpha_2793']])

    def test_pr_changed_module_widens_admission_when_needed(self):
        get = changed_files((DIR + 'test_beta_2793.py', 'added'))
        result = self.run_it(('test_alpha_2793', 'test_beta_2793'), pr=PR, get=get)
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(self.executed, [['test_alpha_2793', 'test_beta_2793']])

    def test_unmatched_path_is_not_admitted(self):
        self.store.task['developer']['read_paths'].append('docs/test_unmatched_2793.py')
        result = self.run_it(('test_unmatched_2793',))
        self.assertEqual((result['result'], result['code']),
                         ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
        self.assertEqual(self.executed, [])

    def test_non_default_profile_runs_only_its_directory(self):
        self.store.task['developer']['read_paths'].append(
            'custom/tests/test_beta_2793.py')
        with patch.object(test_profile, 'default_profile', return_value=CUSTOM):
            refused = self.run_it(('test_alpha_2793',))
            result = self.run_it()
        self.assertEqual(refused['code'], 'MODULE_NOT_ADMITTED')
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(self.executed, [['test_beta_2793']])

    def test_script_first_path_is_never_run(self):
        other = 'custom/tests/test_other_2793.py'
        get = changed_files((FIRST, 'added'), (other, 'modified'))
        with patch.object(test_profile, 'default_profile', return_value=SCRIPT_FIRST):
            for read_paths in ([FIRST, other], [other]):
                self.store.task['developer']['read_paths'] = read_paths
                for pr in (None, PR):
                    with self.subTest(declared=len(read_paths), pr=pr):
                        refused = self.run_it(('test_first_2793',), pr=pr, get=get)
                        self.assertEqual((refused['result'], refused['code']),
                                         ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
            self.assertEqual(self.executed, [])
            result = self.run_it(pr=PR, get=get)
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(self.executed, [['test_other_2793']])

    def test_skipped_only_run_is_never_pass(self):
        self.skipped = 1
        result = self.run_it()
        self.assertEqual((result['result'], result['code']), ('UNKNOWN', 'RECEIPT_INVALID'))
        self.assertEqual(self.store.task['remote_test_receipts'], [])

    def test_unusable_profile_refuses_without_running_or_replaying(self):
        self.assertEqual(self.run_it()['result'], 'PASS')
        self.executed.clear()
        for options in ({}, {'pr': PR}, {'hosted': True},
                        {'modules': ('test_alpha_2793',)}):
            for label, unusable in unusable_cases():
                with self.subTest(label, options=sorted(options)), unusable, \
                        patch.object(remote_test, '_run_hosted') as hosted:
                    result = self.run_it(**options)
                    self.assertEqual((result['result'], result['code']),
                                     ('UNKNOWN', INVALID))
                    self.assertNotIn('receipt_digest', result)
                    hosted.assert_not_called()
        self.assertEqual(self.executed, [])
        self.assertEqual(len(self.store.task['remote_test_receipts']), 1)
        self.assertEqual({item['result'] for item in
                          self.store.task['remote_test_outcomes']}, {'UNKNOWN'})

    def test_profile_failing_during_widening_is_not_an_admission_denial(self):
        self.store.task['developer']['read_paths'] = []
        self.store.task['developer'].pop('direct_result_callback')
        failure = RuntimeError('profile unavailable')
        with patch.object(test_profile, 'default_profile', side_effect=[
                test_profile.default_profile(), failure, failure]):
            result = self.run_it(pr=PR, get=changed_files(
                (DIR + 'test_beta_2793.py', 'added')))
        self.assertEqual((result['result'], result['code']), ('UNKNOWN', INVALID))
        self.assertEqual(self.executed, [])


if __name__ == '__main__':
    unittest.main()
