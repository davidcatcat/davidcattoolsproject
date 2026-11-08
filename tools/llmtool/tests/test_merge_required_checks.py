"""#1182: a skipped required exact-head check never counts as SUCCESS."""

import unittest

from llmtool import task_advance


HEAD = 'a' * 40
MAIN_SHA = 'e' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
DOCS = 'check (docs)'
CODE_FILES = ['src/module.py']
DOCS_FILES = ['docs/guide.md']
SCRIPTS_FILES = ['scripts/llmtool_monitor.py', 'scripts/tool.sh']


def run(name, conclusion='success', status='completed', head=HEAD):
    return {'name': name, 'head_sha': head, 'status': status, 'conclusion': conclusion}


def fake_github(paths, runs, pr=None):
    """Return an in-memory github_get and the list of paths it was asked for."""
    reads = []
    pr = pr if pr is not None else open_pr()

    def github_get(path):
        reads.append(path)
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            numbered = [dict(item, id=index + 1) for index, item in enumerate(runs)]
            return {'total_count': len(numbered), 'check_runs': numbered}
        if path == 'pulls/17/files?per_page=100':
            return [{'filename': item} for item in paths]
        if path == 'pulls/17':
            return pr
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path == f'compare/{MAIN_SHA}...{HEAD}':
            return {'status': 'ahead'}
        if path.endswith('/comments?per_page=100'):
            return []
        raise AssertionError(f'unexpected read: {path}')

    return github_get, reads


def open_pr(labels=None):
    return {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
            'mergeable': True,
            'labels': [{'name': 'mac-ci'}] if labels is None else labels}


def merge_checks(paths, runs, *, allow_missing_macos=False):
    github_get, _reads = fake_github(paths, runs)
    return task_advance._merge_checks({}, 17, HEAD, open_pr(), github_get,
                                      allow_missing_macos=allow_missing_macos)


class MergeRequiredCheckSkippedTests(unittest.TestCase):
    def test_code_pr_with_skipped_linux_and_green_macos_is_refused(self):
        runs = [run(LINUX, 'skipped'), run(MACOS)]
        self.assertEqual(merge_checks(CODE_FILES, runs), 'MERGE_REQUIRED_CHECK_SKIPPED')
        self.assertEqual(merge_checks(CODE_FILES, runs, allow_missing_macos=True),
                         'MERGE_REQUIRED_CHECK_SKIPPED')

    def test_docs_only_pr_with_skipped_docs_check_is_refused(self):
        self.assertEqual(merge_checks(DOCS_FILES, [run(DOCS, 'skipped')]),
                         'MERGE_REQUIRED_CHECK_SKIPPED')
        self.assertEqual(merge_checks(DOCS_FILES, [run(DOCS, 'skipped')],
                                      allow_missing_macos=True),
                         'MERGE_REQUIRED_CHECK_SKIPPED')

    def test_docs_only_pr_with_green_docs_check_is_allowed(self):
        self.assertIsNone(merge_checks(DOCS_FILES, [run(DOCS)]))

    def test_code_pr_with_green_linux_and_macos_is_allowed(self):
        self.assertIsNone(merge_checks(CODE_FILES, [run(LINUX), run(MACOS)]))

    def test_any_exact_head_success_run_of_the_required_name_is_enough(self):
        runs = [run(LINUX, 'skipped'), run(LINUX), run(MACOS)]
        self.assertIsNone(merge_checks(CODE_FILES, runs))

    def test_scripts_only_pr_with_skipped_linux_is_refused(self):
        # #1208: a scripts/-only PR is code and needs a Linux success run.
        runs = [run(LINUX, 'skipped'), run(MACOS)]
        self.assertEqual(merge_checks(SCRIPTS_FILES, runs), 'MERGE_REQUIRED_CHECK_SKIPPED')
        self.assertEqual(merge_checks(SCRIPTS_FILES, runs, allow_missing_macos=True),
                         'MERGE_REQUIRED_CHECK_SKIPPED')

    def test_scripts_only_pr_without_linux_is_refused(self):
        self.assertEqual(merge_checks(SCRIPTS_FILES, [run(MACOS)]),
                         'MERGE_REQUIRED_CHECK_MISSING')
        self.assertEqual(merge_checks(SCRIPTS_FILES, [run(MACOS)], allow_missing_macos=True),
                         'MERGE_REQUIRED_CHECK_MISSING')
        self.assertEqual(merge_checks(SCRIPTS_FILES, [run(LINUX, head='b' * 40), run(MACOS)]),
                         'MERGE_CHECKS_NOT_GREEN')

    def test_scripts_only_pr_with_green_linux_and_macos_is_allowed(self):
        self.assertIsNone(merge_checks(SCRIPTS_FILES, [run(LINUX), run(MACOS)]))
        self.assertIsNone(merge_checks(SCRIPTS_FILES, [run(LINUX, 'skipped'), run(LINUX),
                                                       run(MACOS)]))

    def test_extra_skipped_check_that_is_not_required_is_allowed(self):
        runs = [run(LINUX), run(MACOS), run('lint (optional)', 'skipped'),
                run(DOCS, 'skipped')]
        self.assertIsNone(merge_checks(CODE_FILES, runs))
        self.assertIsNone(merge_checks(DOCS_FILES, [run(DOCS), run(LINUX, 'skipped')]))

    def test_cancelled_failed_pending_or_unknown_runs_stay_not_green(self):
        for label, bad in (('cancelled', run(LINUX, 'cancelled')),
                           ('failed', run(LINUX, 'failure')),
                           ('pending', run(LINUX, None, 'in_progress')),
                           ('unknown', run(LINUX, 'neutral'))):
            with self.subTest(label):
                self.assertEqual(merge_checks(CODE_FILES, [bad, run(MACOS)]),
                                 'MERGE_CHECKS_NOT_GREEN')
        self.assertEqual(merge_checks(CODE_FILES, [run(LINUX, 'cancelled'),
                                                   run(LINUX, 'skipped'), run(MACOS)]),
                         'MERGE_CHECKS_NOT_GREEN')

    def test_missing_check_keeps_its_code_ahead_of_the_skipped_rule(self):
        self.assertEqual(merge_checks(CODE_FILES, [run(LINUX, 'skipped')]),
                         'MERGE_REQUIRED_CHECK_MISSING')
        self.assertEqual(merge_checks(CODE_FILES, [run(MACOS)]),
                         'MERGE_REQUIRED_CHECK_MISSING')
        self.assertEqual(merge_checks(DOCS_FILES, [run(LINUX)]),
                         'MERGE_REQUIRED_CHECK_MISSING')

    def test_skipped_rule_runs_before_owner_acceptance(self):
        tui = ['tools/llmtool/llmtool/chat_tui.py']
        self.assertEqual(merge_checks(tui, [run(LINUX, 'skipped'), run(MACOS)]),
                         'MERGE_REQUIRED_CHECK_SKIPPED')
        self.assertEqual(merge_checks(tui, [run(LINUX), run(MACOS)]),
                         'MERGE_OWNER_ACCEPTANCE_MANUAL')

    def test_macos_rules_are_unchanged(self):
        self.assertEqual(merge_checks(CODE_FILES, [run(LINUX), run(MACOS, 'skipped')]),
                         'MERGE_MACOS_CHECK_REQUIRED')
        self.assertEqual(merge_checks(CODE_FILES, [run(LINUX), run(MACOS, 'skipped')],
                                      allow_missing_macos=True),
                         'MERGE_MACOS_CHECK_SKIPPED')
        self.assertEqual(merge_checks(CODE_FILES, [run(LINUX)], allow_missing_macos=True),
                         'MERGE_MACOS_CHECK_MISSING')
        self.assertEqual(merge_checks(SCRIPTS_FILES, [run(LINUX), run(MACOS, 'skipped')]),
                         'MERGE_MACOS_CHECK_REQUIRED')
        self.assertIsNone(merge_checks(CODE_FILES, [run(LINUX), run(MACOS),
                                                    run(MACOS, 'skipped')]))


class MergeOnceRequiredCheckSkippedTests(unittest.TestCase):
    def _merge_once(self, runs, *, labels=None):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}
        updates, calls = [], []

        class Store:
            def compare_and_update(self, *args, **kwargs):
                updates.append((args, kwargs))

        def gh_runner(_root, args):
            calls.append(list(args))
            return type('R', (), {'returncode': 0})()

        pr = open_pr(labels)
        github_get, _reads = fake_github(CODE_FILES, runs, pr)
        result = task_advance._merge_once(store=Store(), record=record, number=17,
            head=HEAD, pr=pr, events=[event], github_get=github_get,
            gh_runner=gh_runner, root='/repo')
        return result, calls, updates

    def test_skipped_linux_is_refused_without_calling_pr_merge(self):
        for labels in (None, []):
            with self.subTest(labels=labels):
                result, calls, updates = self._merge_once(
                    [run(LINUX, 'skipped'), run(MACOS)], labels=labels)
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertEqual(result['codes'], ['MERGE_REQUIRED_CHECK_SKIPPED'])
                self.assertFalse(any(call[:2] == ['pr', 'merge'] for call in calls))
                self.assertEqual(calls, [])
                self.assertEqual(updates, [])

    def test_green_linux_and_macos_still_merge(self):
        result, calls, _updates = self._merge_once([run(LINUX), run(MACOS)])
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)


if __name__ == '__main__':
    unittest.main()
