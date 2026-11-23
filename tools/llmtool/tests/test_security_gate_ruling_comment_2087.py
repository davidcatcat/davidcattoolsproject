"""#2087 A2-sec: the merge security gate skips well-formed dispute rulings.

A ruling comment carries an LLMTOOL-REVIEW-BINDING line but no Verdict: line. It
must not read as a possible security verdict (PR #2670 post-merge review F1). A
ruling claim in the Task record is likewise not a verdict. Everything that is not
a strictly well-formed ruling keeps the result it has on main. All ids, digests
and heads are placeholders; GitHub reads are injected.
"""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import dispute_arbiter, task_advance  # noqa: E402


HEAD = 'a' * 40
OLD_HEAD = 'c' * 40
OTHER_HEAD = 'b' * 40
MAIN_SHA = 'e' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
CODE_FILES = ['tools/llmtool/llmtool/module.py']
CREATED = '2026-11-24T00:00:00Z'
LATER = '2026-11-24T00:01:00Z'

UNKNOWN = 'MERGE_SECURITY_REVIEW_UNKNOWN'
NOT_PASS = 'MERGE_SECURITY_REVIEW_NOT_PASS'


def _verdict_comment(comment_id, status, head, *, version=91, actor='Codex',
                     created_at=CREATED, updated_at=None):
    lines = [
        f'<!-- activity: type=review_verdict actor={actor} target=PM status={status} -->',
        f'Verdict: {status} | Version: v{version} | HEAD: {head}',
    ]
    return {
        'id': comment_id,
        'body': '\n'.join(lines),
        'created_at': created_at,
        'updated_at': created_at if updated_at is None else updated_at,
    }


def _binding(head):
    return (f'LLMTOOL-REVIEW-BINDING v1 task={"1" * 32} run={"2" * 32} '
            f'contract={"3" * 64} head={head}')


def _ruling_comment(comment_id=500, *, header='versioned', head=HEAD, binding_head=None,
                    outcome='UPHELD', marker_status=None, extra_lines=(), order=(0, 1, 2),
                    created_at=CREATED, updated_at=None):
    marker = (f'<!-- activity: type=dispute_ruling actor=Codex target=PM '
              f'status={marker_status or outcome} -->')
    if header == 'versioned':
        header_line = f'Ruling: F1 {outcome} | PR: #17 | Version: 5 | HEAD: {head}'
    else:
        header_line = f'Ruling: F1 {outcome} | PR: #17 | HEAD: {head}'
    first_three = [marker, header_line, _binding(binding_head or head)]
    lines = [first_three[i] for i in order] + list(extra_lines)
    return {
        'id': comment_id,
        'body': '\n'.join(lines),
        'created_at': created_at,
        'updated_at': created_at if updated_at is None else updated_at,
    }


def _fake_github(comments=None):
    pr = {
        'number': 17,
        'head': {'sha': HEAD},
        'state': 'open',
        'merged': False,
        'mergeable': True,
        'labels': [{'name': 'mac-ci'}],
    }
    runs = [
        {'id': 1, 'name': LINUX, 'head_sha': HEAD, 'status': 'completed', 'conclusion': 'success'},
        {'id': 2, 'name': MACOS, 'head_sha': HEAD, 'status': 'completed', 'conclusion': 'success'},
    ]

    def github_get(path):
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return {'total_count': len(runs), 'check_runs': runs}
        if path == 'pulls/17/files?per_page=100':
            return [{'filename': item} for item in CODE_FILES]
        if path == 'pulls/17':
            return pr
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path == f'compare/{MAIN_SHA}...{HEAD}':
            return {'status': 'ahead'}
        if path == 'issues/17/comments?per_page=100':
            return [] if comments is None else copy.deepcopy(comments)
        raise AssertionError(f'unexpected read: {path}')

    return pr, github_get


def _run_merge_once(comments=None, *, extra_claims=(), formal_version=2):
    pr, github_get = _fake_github(comments=comments)
    claim = {
        'pr': 17,
        'head_sha': HEAD,
        'version': formal_version,
        'state': 'COMPLETED',
        'review_result': {'verdict': 'PASS'},
    }
    event = {
        'source': 'REVIEWER',
        'source_sha': HEAD,
        'version': formal_version,
        'outcome': 'REVIEW_PASS',
    }
    record = {
        'task_id': '1' * 32,
        'record_sha256': 'r' * 64,
        'review_dispatch_claim': [claim, *copy.deepcopy(list(extra_claims))],
    }
    updates = []
    calls = []

    class Store:
        def compare_and_update(self, *args, **kwargs):
            updates.append((args, kwargs))

    def gh_runner(_root, args):
        calls.append(list(args))
        return type('Result', (), {'returncode': 0})()

    result = task_advance._merge_once(
        store=Store(),
        record=record,
        number=17,
        head=HEAD,
        pr=pr,
        events=[event],
        github_get=github_get,
        gh_runner=gh_runner,
        root='/repo',
    )
    return result, calls, updates


def _ruling_claim(*, version=92, purpose='dispute_ruling', state='COMPLETED'):
    """A COMPLETED RULING claim in the shape the A1 store accepts (placeholder values)."""
    claim = {
        'executor_task': '4' * 32, 'pr': 17, 'head_sha': HEAD, 'version': version,
        'state': state, 'reviewer_task': '5' * 32,
        'native_session_id': '6' * 32,
        'account_mode': 'ROLES', 'account': 'reviewer-b',
        'rules_sha256': '7' * 64,
        'claimed_at': CREATED, 'updated_at': LATER,
        'purpose': purpose,
        'dispute': {
            'pr': 17, 'finding_id': 'F1', 'head_sha': HEAD,
            'review_version': 1, 'dispute_text_sha256': '8' * 64,
        },
        'reviewer': 'codex-reviewer',
    }
    if state == 'COMPLETED':
        claim['review_result'] = {
            'task_id': '1' * 32, 'run_id': '2' * 32, 'contract_digest': '3' * 64,
            'head': HEAD, 'verdict': 'RULING', 'outcome': 'UPHELD',
            'review_text': _ruling_comment()['body'],
            'source': {'kind': 'CODEX_JSONL', 'output_sha256': '9' * 64},
        }
    return claim


def _security_claim(verdict):
    return {
        'pr': 17, 'head_sha': HEAD, 'version': 91, 'review_kind': 'security',
        'state': 'COMPLETED', 'review_result': {'verdict': verdict, 'head': HEAD},
    }


class _GateAsserts:
    def assertMerged(self, outcome):
        result, calls, updates = outcome
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)

    def assertRefused(self, outcome, code):
        result, calls, updates = outcome
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], [code])
        self.assertEqual([call for call in calls if call[:2] == ['pr', 'merge']], [])
        self.assertEqual(updates, [])


class RulingHeaderPatternTests(unittest.TestCase):
    def test_versioned_header_pattern(self):
        match = dispute_arbiter.RULING_HEADER_VERSIONED.fullmatch(
            f'Ruling: F2 WITHDRAWN | PR: #17 | Version: 5 | HEAD: {HEAD}')
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ('F2', 'WITHDRAWN', '17', '5', HEAD))
        for bad in (
            f'Ruling: F2 UPHELD | PR: #17 | HEAD: {HEAD}',
            f'Ruling: F2 UPHELD | PR: #17 | Version: v5 | HEAD: {HEAD}',
            f'Ruling: F2 PASS | PR: #17 | Version: 5 | HEAD: {HEAD}',
            f'Ruling: F2 UPHELD | PR: #17 | Version: 5 | HEAD: {HEAD[:39]}',
            f'Ruling: F2 UPHELD | PR: #17 | Version: 5 | HEAD: {HEAD} x',
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(dispute_arbiter.RULING_HEADER_VERSIONED.fullmatch(bad))


class RulingCommentSkippedTests(_GateAsserts, unittest.TestCase):
    """A4 (a): a valid ruling comment is skipped and the merge proceeds."""

    def test_valid_ruling_then_formal_pass_merges(self):
        formal = _verdict_comment(1, 'PASS', HEAD, version=2)
        for header in ('versioned', 'legacy'):
            for ruling_head in (HEAD, OLD_HEAD):
                for outcome in ('UPHELD', 'WITHDRAWN'):
                    with self.subTest(header=header, head=ruling_head, outcome=outcome):
                        ruling = _ruling_comment(header=header, head=ruling_head, outcome=outcome)
                        self.assertMerged(_run_merge_once([ruling, formal]))
                        self.assertMerged(_run_merge_once([formal, ruling]))

    def test_valid_ruling_with_free_text_after_third_line_merges(self):
        ruling = _ruling_comment(extra_lines=('Reasons: the finding does not apply.',))
        self.assertMerged(_run_merge_once([ruling]))

    def test_valid_ruling_with_crlf_lines_merges(self):
        ruling = _ruling_comment()
        ruling['body'] = ruling['body'].replace('\n', '\r\n')
        self.assertMerged(_run_merge_once([ruling]))

    def test_skipped_ruling_is_not_a_security_pass(self):
        # A ruling never counts as a security PASS: the security check result is
        # the same None as with no security verdict, and a FAIL still refuses.
        ruling = _ruling_comment()
        self.assertIsNone(task_advance._security_comment_code([ruling], HEAD))
        self.assertIsNone(task_advance._security_comment_code([], HEAD))
        self.assertEqual(
            task_advance._security_comment_code([ruling, _verdict_comment(2, 'FAIL', HEAD)], HEAD),
            NOT_PASS)


class RulingCommentNotSkippedTests(_GateAsserts, unittest.TestCase):
    """A4 (b)-(d), (f) and A2: anything not strictly well formed keeps the main result."""

    def test_edited_ruling_not_skipped(self):
        for header in ('versioned', 'legacy'):
            with self.subTest(header=header):
                ruling = _ruling_comment(header=header, updated_at=LATER)
                self.assertRefused(_run_merge_once([ruling]), UNKNOWN)

    def test_ruling_with_missing_or_blank_timestamps_not_skipped(self):
        for label, change in (
            ('no_updated_at', lambda c: c.pop('updated_at')),
            ('no_created_at', lambda c: c.pop('created_at')),
            ('blank_times', lambda c: c.update(created_at='', updated_at='')),
            ('non_str_times', lambda c: c.update(created_at=1, updated_at=1)),
        ):
            with self.subTest(label=label):
                ruling = _ruling_comment()
                change(ruling)
                self.assertRefused(_run_merge_once([ruling]), UNKNOWN)

    def test_ruling_with_added_verdict_line_not_skipped(self):
        cases = [
            ('verdict_pass_v91', f'Verdict: PASS | Version: v91 | HEAD: {HEAD}', UNKNOWN),
            ('verdict_fail_v91', f'Verdict: FAIL | Version: v91 | HEAD: {HEAD}', NOT_PASS),
            ('verdict_fix_v91', f'Verdict: FIX | Version: v91 | HEAD: {HEAD}', NOT_PASS),
            ('bare_verdict', 'Verdict: PASS', UNKNOWN),
            ('indented_verdict', f'  Verdict: PASS | Version: v91 | HEAD: {HEAD}', UNKNOWN),
        ]
        for label, line, code in cases:
            with self.subTest(label=label):
                ruling = _ruling_comment(extra_lines=(line,))
                self.assertRefused(_run_merge_once([ruling]), code)

    def test_ruling_with_formal_version_verdict_line_keeps_main_result(self):
        # On main a verdict line below v91 is not a security verdict, so the gate
        # passes. The comment is classified by the main path, not skipped.
        line = f'Verdict: PASS | Version: v2 | HEAD: {HEAD}'
        ruling = _ruling_comment(extra_lines=(line,))
        self.assertIsNone(task_advance._security_comment_code([ruling], HEAD))
        self.assertMerged(_run_merge_once([ruling]))

    def test_ruling_with_verdict_version_head_line_not_skipped_on_any_line(self):
        line = f'Verdict: FAIL | Version: v91 | HEAD: {HEAD}'
        ruling = _ruling_comment(extra_lines=('Reasons follow.', line))
        self.assertRefused(_run_merge_once([ruling]), NOT_PASS)

    def test_ruling_with_added_review_verdict_marker_line_not_skipped(self):
        # A review_verdict marker line anywhere in a ruling, with no Verdict: line,
        # is a malformed mixed comment. On main its binding line gives UNKNOWN.
        for actor in ('Codex', 'Claude'):
            for status in ('PASS', 'FIX', 'FAIL'):
                marker = (f'<!-- activity: type=review_verdict actor={actor} '
                          f'target=PM status={status} -->')
                for label, lines in (
                    ('after_binding', (marker,)),
                    ('after_free_text', ('Reasons follow.', marker)),
                    ('indented', (f'  {marker}',)),
                    ('trailing_text', (f'{marker} extra',)),
                ):
                    with self.subTest(actor=actor, status=status, label=label):
                        ruling = _ruling_comment(extra_lines=lines)
                        self.assertFalse(task_advance._is_ruling_comment(ruling, ruling['body']))
                        self.assertRefused(_run_merge_once([ruling]), UNKNOWN)

    def test_binding_head_differs_from_header_head_not_skipped(self):
        for header in ('versioned', 'legacy'):
            with self.subTest(header=header):
                ruling = _ruling_comment(header=header, head=HEAD, binding_head=OTHER_HEAD)
                self.assertRefused(_run_merge_once([ruling]), UNKNOWN)
                ruling = _ruling_comment(header=header, head=OLD_HEAD, binding_head=HEAD)
                self.assertRefused(_run_merge_once([ruling]), UNKNOWN)

    def test_other_malformed_ruling_variants_not_skipped(self):
        long_text = 'x' * 66000
        cases = [
            ('binding_second', _ruling_comment(order=(0, 2, 1))),
            ('header_first', _ruling_comment(order=(1, 0, 2))),
            ('binding_first', _ruling_comment(order=(2, 0, 1))),
            ('status_mismatch', _ruling_comment(outcome='UPHELD', marker_status='WITHDRAWN')),
            ('second_ruling_line', _ruling_comment(
                extra_lines=(f'Ruling: F2 UPHELD | PR: #17 | HEAD: {HEAD}',))),
            ('second_ruling_line_free', _ruling_comment(extra_lines=('Ruling: I disagree',))),
            ('oversized', _ruling_comment(extra_lines=(long_text,))),
        ]
        for label, ruling in cases:
            with self.subTest(label=label):
                self.assertRefused(_run_merge_once([ruling]), UNKNOWN)

    def test_ruling_without_binding_line_is_not_skipped_by_this_rule(self):
        # Without the binding line the comment is not a ruling comment under A1.
        # It is then classified as on main (no verdict, no binding: not a security
        # comment), so the visible result does not change either.
        ruling = _ruling_comment()
        ruling['body'] = '\n'.join(ruling['body'].split('\n')[:2])
        self.assertFalse(task_advance._is_ruling_comment(ruling, ruling['body']))
        self.assertIsNone(task_advance._security_comment_code([ruling], HEAD))

    def test_malformed_security_verdict_without_ruling_lines_still_refuses(self):
        cases = [
            ('short_pass', {'id': 1, 'body': f'Verdict: PASS | Version: v91 | HEAD: {HEAD}'}),
            ('binding_only', {'id': 1, 'body': _binding(HEAD), 'created_at': CREATED,
                              'updated_at': CREATED}),
            ('marker_only', {'id': 1, 'body': (
                '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->')}),
        ]
        for label, comment in cases:
            with self.subTest(label=label):
                self.assertRefused(_run_merge_once([comment]), UNKNOWN)

    def test_unreadable_or_malformed_comment_entries_still_refuse(self):
        for label, payload in (
            ('non_list', {'message': 'bad'}),
            ('non_dict_item', ['corrupt']),
            ('missing_body', [{'id': 1}]),
            ('non_str_body', [{'id': 1, 'body': 123}]),
        ):
            with self.subTest(label=label):
                self.assertRefused(_run_merge_once(payload), UNKNOWN)


class RulingNeverHidesSecurityVerdictTests(_GateAsserts, unittest.TestCase):
    """A4 (e): a valid ruling never hides a security FAIL or FIX in another comment."""

    def test_security_fail_and_valid_ruling_refuse(self):
        ruling = _ruling_comment()
        formal = _verdict_comment(1, 'PASS', HEAD, version=2)
        for status in ('FAIL', 'FIX', 'BLOCKED'):
            sec = _verdict_comment(2, status, HEAD, version=91)
            for label, comments in (
                ('ruling_first', [ruling, sec, formal]),
                ('ruling_last', [sec, formal, ruling]),
                ('ruling_between', [sec, ruling, formal]),
            ):
                with self.subTest(status=status, order=label):
                    self.assertRefused(_run_merge_once(comments), NOT_PASS)

    def test_security_pass_and_valid_ruling_merge(self):
        sec = _verdict_comment(2, 'PASS', HEAD, version=91)
        self.assertMerged(_run_merge_once([_ruling_comment(), sec]))


class RulingClaimSkippedTests(_GateAsserts, unittest.TestCase):
    """A3: a ruling claim in the Task record neither refuses nor satisfies the gate."""

    def test_ruling_claim_v92_at_head_does_not_refuse(self):
        baseline = _run_merge_once([])
        self.assertMerged(baseline)
        with_claim = _run_merge_once([], extra_claims=[_ruling_claim(version=92)])
        self.assertMerged(with_claim)
        self.assertEqual(baseline[0]['step'], with_claim[0]['step'])

    def test_ruling_claim_directly_in_security_claim_reader(self):
        events = []
        for claims in ([_ruling_claim()], [_ruling_claim(), _ruling_claim(version=93)]):
            record = {'review_dispatch_claim': claims}
            self.assertIsNone(task_advance._merge_security_claim(
                record, 17, HEAD, events, github_get=_fake_github([])[1]))
        # The same record without the ruling claim gives the same result.
        self.assertIsNone(task_advance._merge_security_claim(
            {'review_dispatch_claim': []}, 17, HEAD, events, github_get=_fake_github([])[1]))

    def test_any_purpose_value_and_state_is_left_out(self):
        for purpose in ('other', 'dispute_ruling', ''):
            for state in ('COMPLETED', 'CLAIMED', 'DISPATCHED'):
                with self.subTest(purpose=purpose, state=state):
                    claim = _ruling_claim(purpose=purpose, state=state)
                    self.assertMerged(_run_merge_once([], extra_claims=[claim]))

    def test_ruling_claim_does_not_satisfy_a_missing_security_pass(self):
        # A ruling claim is not a security verdict: a record whose only security
        # claim is a FAIL still refuses, with or without the ruling claim.
        for claims in ([_security_claim('FAIL'), _ruling_claim()],
                       [_ruling_claim(), _security_claim('FAIL')]):
            with self.subTest(order=[c.get('purpose', 'security') for c in claims]):
                self.assertRefused(_run_merge_once([], extra_claims=claims), NOT_PASS)

    def test_security_fix_blocked_and_unknown_claims_still_refuse_with_ruling_claim(self):
        for verdict, code in (('FIX', NOT_PASS), ('BLOCKED', NOT_PASS), ('MAYBE', UNKNOWN)):
            with self.subTest(verdict=verdict):
                claims = [_ruling_claim(), _security_claim(verdict)]
                self.assertRefused(_run_merge_once([], extra_claims=claims), code)

    def test_security_pass_claim_with_ruling_claim_merges(self):
        claims = [_ruling_claim(), _security_claim('PASS')]
        self.assertMerged(_run_merge_once([], extra_claims=claims))

    def test_non_dict_claim_still_refuses(self):
        record = {'review_dispatch_claim': [_ruling_claim(), 'corrupt']}
        self.assertEqual(
            task_advance._merge_security_claim(
                record, 17, HEAD, [], github_get=_fake_github([])[1]),
            UNKNOWN)

    def test_formal_claim_without_purpose_key_is_not_skipped(self):
        # A claim with no purpose key is handled as on main: an unknown review kind refuses.
        claim = _ruling_claim()
        claim.pop('purpose')
        self.assertRefused(_run_merge_once([], extra_claims=[claim]), UNKNOWN)

    def test_ruling_claim_does_not_hide_security_reviewer_event(self):
        record = {'review_dispatch_claim': [_ruling_claim()]}
        events = [{'source': 'REVIEWER', 'source_sha': HEAD, 'review_kind': 'security',
                   'outcome': 'REVIEW_FAIL'}]
        self.assertEqual(
            task_advance._merge_security_claim(
                record, 17, HEAD, events, github_get=_fake_github([])[1]),
            NOT_PASS)


if __name__ == '__main__':
    unittest.main()
