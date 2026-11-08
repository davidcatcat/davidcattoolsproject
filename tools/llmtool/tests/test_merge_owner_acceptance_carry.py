"""task advance --merge --owner-acceptance URL: carried acceptance (#1237).

When the relay comment names an older head H0 of the same PR, the merge may
go on at the current head H1 only when the PR's own changes from H0 to H1
edit test files already in the PR and remove no assertion, skip or test
method and add no skip. H1 keeps its own bound PASS and exact-head CI.
All data here is in memory; nothing is read from GitHub.
"""

import unittest
from unittest.mock import patch

from llmtool import sensitive_paths
from llmtool import task_advance


H1 = 'a' * 40
H0 = 'c' * 40
THIRD = 'd' * 40
MAIN_SHA = 'e' * 40
SLUG = 'example-owner/example-repo'
COMMENT_ID = 9001
URL = f'https://github.com/{SLUG}/issues/42#issuecomment-{COMMENT_ID}'
MARKER = '<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->'
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
OWNER_LOGIN = 'example-owner-login'
AI_LOGIN = 'example-ai-login'
PERSON = {'login': OWNER_LOGIN, 'type': 'User'}
LATER = '2026-11-08T10:00:00Z'

PRODUCT = 'tools/llmtool/llmtool/chat_tui.py'
TEST = 'tools/llmtool/tests/test_example_widget.py'
NEW_TEST = 'tools/llmtool/tests/test_example_new.py'
WORKFLOW = '.github/workflows/example.yml'

COMMENT_PATH = f'issues/comments/{COMMENT_ID}'
H0_COMMIT = f'commits/{H0}'
S0_PATH = f'compare/{MAIN_SHA}...{H0}?per_page=100'
S1_PATH = f'compare/{MAIN_SHA}...{H1}?per_page=100'
D_PATH = f'compare/{H0}...{H1}?per_page=100'

OK_PATCH = ('@@ -10,3 +10,4 @@ class WidgetTests(unittest.TestCase):\n'
            '     def test_widget(self):\n'
            '-        value = build(1)\n'
            '+        value = build(2)\n'
            '+        value = value + 0\n')


def run(name, conclusion='success'):
    return {'name': name, 'head_sha': H1, 'status': 'completed', 'conclusion': conclusion}


GREEN = [run(LINUX), run(MACOS)]


def body(*heads):
    named = ' and '.join(heads or (H0,))
    return f'{MARKER}\nOwner accepts PR #17 at head {named}, relayed by PM.'


def comment(text=None, *, created=LATER):
    return {'id': COMMENT_ID, 'body': body() if text is None else text,
            'html_url': URL, 'created_at': created, 'updated_at': created,
            'user': PERSON}


def commit(sha, *, author='2026-11-08T08:00:00Z', committer='2026-11-08T09:00:00Z'):
    return {'sha': sha, 'commit': {'author': {'date': author},
                                   'committer': {'date': committer}}}


def entry(path, status='modified', *, patch_text=OK_PATCH, additions=2, deletions=1,
          **extra):
    item = {'filename': path, 'status': status, 'additions': additions,
            'deletions': deletions, **extra}
    if patch_text is not None:
        item['patch'] = patch_text
    return item


def compare(files, *, status='ahead', base=MAIN_SHA):
    return {'status': status, 'merge_base_commit': {'sha': base}, 'files': files}


def own_change(patch_text):
    """A D answer whose only own change is TEST with this patch."""
    return compare([entry(TEST, patch_text=patch_text)], base=H0)


def open_pr():
    return {'number': 17, 'head': {'sha': H1}, 'state': 'open', 'merged': False,
            'mergeable': True, 'labels': [{'name': 'mac-ci'}]}


def fake_github(**overrides):
    """Return an in-memory github_get and the paths it was asked for.

    overrides maps a read path to its answer; an exception answer is raised.
    """
    reads = []
    answers = {
        f'commits/{H1}/check-runs?per_page=100': {
            'total_count': len(GREEN),
            'check_runs': [dict(item, id=index + 1) for index, item in enumerate(GREEN)]},
        'pulls/17/files?per_page=100': [{'filename': PRODUCT}, {'filename': TEST}],
        'pulls/17': open_pr(),
        'branches/main': {'commit': {'sha': MAIN_SHA}},
        f'compare/{MAIN_SHA}...{H1}': {'status': 'ahead'},
        COMMENT_PATH: comment(),
        H0_COMMIT: commit(H0),
        f'commits/{H1}': commit(H1),
        S0_PATH: compare([entry(PRODUCT), entry(TEST, 'added')]),
        S1_PATH: compare([entry(PRODUCT), entry(TEST, 'added')]),
        D_PATH: compare([entry(TEST)], base=H0),
    }
    answers.update(overrides)

    def github_get(path):
        reads.append(path)
        if path not in answers:
            raise AssertionError(f'unexpected read: {path}')
        value = answers[path]
        if isinstance(value, Exception):
            raise value
        return value

    return github_get, reads


class CarryTestCase(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(task_advance.task_completion, '_trusted_slug', return_value=SLUG),
            patch.object(task_advance.github_identity, 'owner_login',
                         return_value=OWNER_LOGIN),
            patch.object(task_advance.github_identity, 'ai_login', return_value=AI_LOGIN),
            # The rule list is injected so these tests do not depend on it.
            patch.object(sensitive_paths, 'is_sensitive',
                         side_effect=lambda path: path.startswith('.github/')),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def checks(self, **overrides):
        github_get, reads = fake_github(**overrides)
        out = {}
        code = task_advance._merge_checks({}, 17, H1, open_pr(), github_get,
                                          owner_acceptance=URL, acceptance_out=out)
        return code, reads, out

    def merge_once(self, *, review_pass=True, review_head=H1, **overrides):
        review = {'pr': 17, 'head_sha': review_head, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS' if review_pass else 'FAIL'}}
        event = {'source': 'REVIEWER', 'source_sha': review_head, 'version': 1,
                 'outcome': 'REVIEW_PASS' if review_pass else 'REVIEW_FAIL'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}
        calls = []

        class Store:
            def compare_and_update(self, *args, **kwargs):
                pass

        def gh_runner(_root, args):
            calls.append(list(args))
            return type('R', (), {'returncode': 0})()

        github_get, reads = fake_github(**overrides)
        result = task_advance._merge_once(
            store=Store(), record=record, number=17, head=H1, pr=open_pr(),
            events=[event], github_get=github_get, gh_runner=gh_runner, root='/repo',
            owner_acceptance=URL)
        return result, calls, reads

    def assert_code(self, expected, **overrides):
        code, reads, out = self.checks(**overrides)
        self.assertEqual(code, expected)
        self.assertEqual(out, {})
        return reads


EXPECTED_CHECKS = {'descendant': 'PASS', 'non_test': 'PASS', 'new_file': 'PASS',
                   'test_deleted': 'PASS', 'assertion_removed': 'PASS',
                   'skip_added': 'PASS'}


class CarryAllowedTests(CarryTestCase):
    def test_test_only_change_passes_with_evidence(self):
        code, reads, out = self.checks()
        self.assertIsNone(code)
        for path in (H0_COMMIT, 'branches/main', S0_PATH, S1_PATH, D_PATH):
            self.assertIn(path, reads)
        self.assertNotIn(f'commits/{H0}/check-runs?per_page=100', reads)
        self.assertEqual(out, {
            'original': URL, 'h0': H0, 'h1': H1,
            'files': [{'path': TEST, 'additions': 2, 'deletions': 1}],
            'checks': EXPECTED_CHECKS,
            'h1_checks': [f'{LINUX}=success', f'{MACOS}=success'],
        })

    def test_carry_merges_with_its_provenance_line(self):
        result, calls, _reads = self.merge_once()
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(result['codes'], [])
        self.assertEqual(result['result']['owner_acceptance'], URL)
        self.assertEqual(result['result']['owner_acceptance_carry'], {
            'original': URL, 'h0': H0, 'h1': H1,
            'files': [{'path': TEST, 'additions': 2, 'deletions': 1}],
            'checks': EXPECTED_CHECKS,
            'h1_checks': [f'{LINUX}=success', f'{MACOS}=success'],
            'h1_review_version': 1,
        })
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)
        provenance = [call[-1] for call in calls if call[:2] == ['pr', 'comment']]
        self.assertEqual(len(provenance), 1)
        self.assertEqual(provenance[0], (
            f'llmtool task advance merge provenance: task={"1" * 32} '
            f'head={H1} review_version=1 checks=exact-head-success-or-skipped'
            f' owner_acceptance={URL}'
            f' owner_acceptance_carry: original={URL} h0={H0} h1={H1}'
            f' files={TEST}(+2/-1)'
            ' checks=descendant:PASS,non_test:PASS,new_file:PASS,test_deleted:PASS,'
            'assertion_removed:PASS,skip_added:PASS'
            f' h1_review_version=1 h1_checks={LINUX}=success,{MACOS}=success'))

    def test_main_only_files_are_ignored(self):
        bad_patch = '@@ -1 +1 @@\n-    self.assertTrue(done)\n+    pass\n'
        d = compare([entry(TEST),
                     entry('tools/llmtool/llmtool/other.py'),
                     entry('.github/workflows/other.yml', 'added'),
                     entry('tools/llmtool/tests/test_other.py', patch_text=bad_patch),
                     entry('tools/llmtool/tests/test_gone.py', 'removed')], base=H0)
        code, _reads, out = self.checks(**{D_PATH: d})
        self.assertIsNone(code)
        self.assertEqual(out['files'], [{'path': TEST, 'additions': 2, 'deletions': 1}])

    def test_moved_test_method_without_assertions_passes(self):
        moved = ('@@ -1,2 +1,2 @@\n-    def test_widget(self):\n'
                 '+    def test_widget(self):  # moved\n')
        self.assertIsNone(self.checks(**{D_PATH: own_change(moved)})[0])

    def test_diverged_main_to_h0_is_allowed(self):
        s0 = compare([entry(PRODUCT), entry(TEST, 'added')], status='diverged')
        self.assertIsNone(self.checks(**{S0_PATH: s0})[0])


class ExactHeadUnchangedTests(CarryTestCase):
    def test_exact_head_comment_makes_no_compare_read(self):
        value = comment(body(H1, H0))
        code, reads, out = self.checks(**{COMMENT_PATH: value})
        self.assertIsNone(code)
        self.assertEqual(out, {})
        self.assertIn(f'commits/{H1}', reads)
        self.assertFalse(any(path.startswith('compare/') for path in reads))
        self.assertNotIn(H0_COMMIT, reads)
        result, calls, reads = self.merge_once(**{COMMENT_PATH: value})
        self.assertEqual(result['result'], {'pr': 17, 'head': H1, 'owner_acceptance': URL})
        self.assertFalse(any(path.endswith('?per_page=100') and path.startswith('compare/')
                             for path in reads))
        provenance = [call[-1] for call in calls if call[:2] == ['pr', 'comment']]
        self.assertTrue(provenance[0].endswith(' owner_acceptance=' + URL))

    def test_no_full_sha_keeps_the_head_mismatch(self):
        reads = self.assert_code('MERGE_OWNER_ACCEPTANCE_HEAD_MISMATCH',
                                 **{COMMENT_PATH: comment(body(H0[:12]))})
        self.assertNotIn(H0_COMMIT, reads)

    def test_carry_needs_an_evidence_dict(self):
        github_get, reads = fake_github()
        code = task_advance._merge_checks({}, 17, H1, open_pr(), github_get,
                                          owner_acceptance=URL)
        self.assertEqual(code, 'MERGE_OWNER_ACCEPTANCE_HEAD_MISMATCH')
        self.assertNotIn(H0_COMMIT, reads)


class CarryModeRefusalTests(CarryTestCase):
    def test_two_distinct_shas_are_ambiguous(self):
        reads = self.assert_code('MERGE_ACCEPTANCE_CARRY_HEAD_AMBIGUOUS',
                                 **{COMMENT_PATH: comment(body(H0, THIRD))})
        self.assertFalse(any(path.startswith(('commits/' + H0, 'compare/'))
                             for path in reads))

    def test_same_sha_twice_is_one_sha(self):
        self.assertIsNone(self.checks(**{COMMENT_PATH: comment(body(H0, H0))})[0])

    def test_stale_comment(self):
        for created in ('2026-11-08T09:00:00Z', '2026-11-08T08:30:00Z'):
            with self.subTest(created=created):
                reads = self.assert_code('MERGE_OWNER_ACCEPTANCE_STALE',
                                         **{COMMENT_PATH: comment(created=created)})
                self.assertNotIn(S0_PATH, reads)

    def test_unreadable_h0_commit_or_date(self):
        for value in (RuntimeError('read failed'), {'sha': H0},
                      commit(H0, committer='yesterday')):
            with self.subTest(value=value):
                self.assert_code('MERGE_OWNER_ACCEPTANCE_UNAVAILABLE', **{H0_COMMIT: value})

    def test_non_descendant_h0(self):
        for value in (compare([entry(TEST)], status='diverged', base=H0),
                      compare([entry(TEST)], base=THIRD),
                      compare([entry(TEST)], status='behind', base=H0)):
            with self.subTest(value=value):
                self.assert_code('MERGE_ACCEPTANCE_CARRY_NOT_DESCENDANT', **{D_PATH: value})

    def test_read_error_or_bad_answer_is_diff_unknown(self):
        cases = (
            {'branches/main': RuntimeError('read failed')},
            {'branches/main': {'commit': {'sha': 'not-a-sha'}}},
            {S0_PATH: RuntimeError('read failed')},
            {S1_PATH: ['not', 'a', 'dict']},
            {D_PATH: RuntimeError('read failed')},
            {D_PATH: {'status': 'ahead', 'merge_base_commit': {'sha': H0}}},
            {D_PATH: compare([{'filename': TEST}], base=H0)},
            {D_PATH: compare([{'status': 'modified'}], base=H0)},
            {S1_PATH: compare([entry(TEST, previous_filename=7)])},
            {S0_PATH: compare([entry(f'tools/llmtool/tests/test_n{index}.py')
                               for index in range(100)])},
            {S0_PATH: compare([entry(TEST, 'added')], status='behind')},
            {S0_PATH: compare([entry(TEST, 'added')], status='identical')},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_code('MERGE_ACCEPTANCE_CARRY_DIFF_UNKNOWN', **overrides)


class CarryPathRefusalTests(CarryTestCase):
    def test_product_file_change(self):
        d = compare([entry(TEST), entry(PRODUCT)], base=H0)
        self.assert_code('MERGE_ACCEPTANCE_CARRY_NON_TEST', **{D_PATH: d})

    def test_new_non_test_file(self):
        new = 'tools/llmtool/llmtool/example_new.py'
        s1 = compare([entry(PRODUCT), entry(TEST, 'added'), entry(new, 'added')])
        d = compare([entry(TEST), entry(new, 'added')], base=H0)
        self.assert_code('MERGE_ACCEPTANCE_CARRY_NON_TEST', **{S1_PATH: s1, D_PATH: d})

    def test_github_change(self):
        s1 = compare([entry(PRODUCT), entry(TEST, 'added'), entry(WORKFLOW, 'added')])
        d = compare([entry(TEST), entry(WORKFLOW, 'added')], base=H0)
        self.assert_code('MERGE_ACCEPTANCE_CARRY_NON_TEST', **{S1_PATH: s1, D_PATH: d})

    def test_sensitive_or_unknown_test_path_is_non_test(self):
        for effect in (lambda path: path == TEST, RuntimeError('list unreadable')):
            with self.subTest(effect=effect), \
                    patch.object(sensitive_paths, 'is_sensitive', side_effect=effect):
                self.assert_code('MERGE_ACCEPTANCE_CARRY_NON_TEST')

    def test_deeper_test_directory_is_non_test(self):
        deep = 'tools/llmtool/tests/sub/test_example_deep.py'
        s0 = compare([entry(PRODUCT), entry(deep, 'added')])
        d = compare([entry(deep)], base=H0)
        self.assert_code('MERGE_ACCEPTANCE_CARRY_NON_TEST',
                         **{S0_PATH: s0, S1_PATH: s0, D_PATH: d})

    def test_new_test_file(self):
        s1 = compare([entry(PRODUCT), entry(TEST, 'added'), entry(NEW_TEST, 'added')])
        d = compare([entry(TEST), entry(NEW_TEST, 'added')], base=H0)
        self.assert_code('MERGE_ACCEPTANCE_CARRY_NEW_FILE', **{S1_PATH: s1, D_PATH: d})

    def test_removed_test_file(self):
        s1 = compare([entry(PRODUCT)])
        d = compare([entry(TEST, 'removed', patch_text=None)], base=H0)
        self.assert_code('MERGE_ACCEPTANCE_CARRY_TEST_DELETED', **{S1_PATH: s1, D_PATH: d})

    def test_missing_patch_or_bad_counts(self):
        for item in (entry(TEST, patch_text=None), entry(TEST, additions=None),
                     entry(TEST, deletions=True), entry(TEST, 'changed')):
            with self.subTest(item=item):
                self.assert_code('MERGE_ACCEPTANCE_CARRY_DIFF_UNKNOWN',
                                 **{D_PATH: compare([item], base=H0)})


class CarryLineRefusalTests(CarryTestCase):
    def test_removed_assertion(self):
        for line in ('-        self.assertEqual(value, 1)',
                     '-        assert value',
                     '-    @unittest.expectedFailure',
                     '-    @unittest.SKIPIF(flag, "later")'):
            with self.subTest(line=line):
                text = f'@@ -1,2 +1,2 @@\n{line}\n+        value = 1\n'
                self.assert_code('MERGE_ACCEPTANCE_CARRY_ASSERTION_REMOVED',
                                 **{D_PATH: own_change(text)})

    def test_moved_assertion_also_refuses(self):
        text = ('@@ -1,3 +1,3 @@\n-        self.assertTrue(ok)\n'
                '         value = 1\n+        self.assertTrue(ok)\n')
        self.assert_code('MERGE_ACCEPTANCE_CARRY_ASSERTION_REMOVED',
                         **{D_PATH: own_change(text)})

    def test_added_skip(self):
        for line in ('+    @unittest.skip("later")',
                     '+        raise unittest.SkipTest("later")',
                     '+    @unittest.expectedFailure'):
            with self.subTest(line=line):
                text = f'@@ -1 +1,2 @@\n     def test_widget(self):\n{line}\n'
                self.assert_code('MERGE_ACCEPTANCE_CARRY_SKIP_ADDED',
                                 **{D_PATH: own_change(text)})

    def test_deleted_or_renamed_test_method(self):
        for added in ('', '+    def test_widget_renamed(self):\n'):
            with self.subTest(added=added):
                text = f'@@ -1,2 +1,1 @@\n-    def test_widget(self):\n{added}'
                self.assert_code('MERGE_ACCEPTANCE_CARRY_TEST_DELETED',
                                 **{D_PATH: own_change(text)})

    def test_deleted_method_wins_over_removed_assertion(self):
        text = ('@@ -1,2 +0,0 @@\n-    def test_widget(self):\n'
                '-        self.assertTrue(ok)\n')
        self.assert_code('MERGE_ACCEPTANCE_CARRY_TEST_DELETED',
                         **{D_PATH: own_change(text)})

    def test_hunk_header_is_skipped(self):
        text = '@@ -1 +1 @@ def test_gone(self): assert skip\n-        x = 1\n+        x = 2\n'
        self.assertIsNone(self.checks(**{D_PATH: own_change(text)})[0])


class ExistingGatesStayTests(CarryTestCase):
    def test_missing_h1_pass_refuses(self):
        result, calls, reads = self.merge_once(review_pass=False)
        self.assertEqual(result, {'step': 'MERGE_REFUSED',
                                  'codes': ['MERGE_REVIEW_NOT_PASS'], 'result': None})
        self.assertEqual(calls, [])
        self.assertEqual(reads, [])

    def test_old_head_pass_does_not_count(self):
        result, calls, _reads = self.merge_once(review_head=H0)
        self.assertEqual(result['codes'], ['MERGE_REVIEW_NOT_PASS'])
        self.assertEqual(calls, [])

    def test_h1_exact_head_ci_is_still_required(self):
        runs = [run(LINUX, 'failure'), run(MACOS)]
        checks = {'total_count': 2,
                  'check_runs': [dict(item, id=index + 1) for index, item in enumerate(runs)]}
        reads = self.assert_code('MERGE_CHECKS_NOT_GREEN',
                                 **{f'commits/{H1}/check-runs?per_page=100': checks})
        self.assertNotIn(COMMENT_PATH, reads)

    def test_carry_refusal_blocks_the_merge(self):
        text = '@@ -1 +1 @@\n-        self.assertTrue(ok)\n+        pass\n'
        result, calls, _reads = self.merge_once(**{D_PATH: own_change(text)})
        self.assertEqual(result, {'step': 'MERGE_REFUSED',
                                  'codes': ['MERGE_ACCEPTANCE_CARRY_ASSERTION_REMOVED'],
                                  'result': None})
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
