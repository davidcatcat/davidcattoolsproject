"""#2087 A1d: an arbiter WITHDRAWN ruling is scoped to one formal review version.

withdrawn_findings(task, pr, head, version) counts a RULED WITHDRAWN entry only
for the review version it ruled on, and fails closed on any version that is
not a non-bool int. _review_gate runs for real with the fixed repair policy and
explicit counts. Task records are plain dicts with placeholder ids and digests
only; no test data here is credential-shaped (Owner #763 A).
"""

import unittest

from llmtool import repair_policy, review_repair
from llmtool.errors import LLMToolError

import worker_policy_fixture

TASK, HEAD, OTHER_HEAD, PR = 'a' * 32, 'c' * 40, 'd' * 40, 42
BODY = 'Finding: F1 [High] a.py:1 the guard is missing\n'
COUNTS = {'review_repairs': 0, 'review_heads': {}}
FAMILY = f'pr-{PR}'
# Placeholder arbitrate-ruling evidence fields (plain records, never validated here).
ARBITRATE = {'path': 'arbitrate', 'finding_id': 'F1', 'arbiter_claim_version': 1,
             'rules_sha256': '1' * 64, 'review_text_sha256': '2' * 64,
             'claim_sha256': '3' * 64, 'source_kind': 'CODEX_JSONL',
             'output_sha256': '4' * 64, 'comment_id': 5, 'contract_digest': '6' * 64,
             'run_id': 'b' * 32}


_LEGACY = object()  # arbitrate default: a legacy ruling with no arbitrate key set


def _entry(finding_id='F1', *, version=1, state='RULED', outcome='WITHDRAWN', arbitrate=_LEGACY,
           pr=PR, head=HEAD, drop=()):
    """arbitrate is the ruling's review_version; any value, None included, adds the arbitrate keys."""
    entry = {'finding_id': finding_id, 'pr': pr, 'head_sha': head, 'review_version': version,
             'severity': 'High', 'state': state}
    if outcome is not None:
        ruling = {'outcome': outcome, 'head_sha': head, 'reviewer': 'codex-reviewer'}
        if arbitrate is not _LEGACY:
            ruling.update(ARBITRATE, finding_id=finding_id, review_version=arbitrate)
        entry['ruling'] = ruling
    for key in drop:
        entry.pop(key)
    return entry


def _task(*entries):
    return {'task_id': TASK, 'review_disputes': list(entries)}


class WithdrawnFindingsVersionTests(unittest.TestCase):
    def withdrawn(self, entry, version, *, pr=PR, head=HEAD):
        return review_repair.withdrawn_findings(_task(entry), pr, head, version)

    def test_a_legacy_entry_withdraws_only_its_own_version(self):
        entry = _entry(version=3)
        self.assertEqual(self.withdrawn(entry, 3), frozenset({'F1'}))
        for version in (1, 2, 4):
            with self.subTest(version=version):
                self.assertEqual(self.withdrawn(entry, version), frozenset())

    def test_an_arbitrate_entry_withdraws_only_its_own_version(self):
        entry = _entry(version=3, arbitrate=3)
        self.assertEqual(self.withdrawn(entry, 3), frozenset({'F1'}))
        self.assertEqual(self.withdrawn(entry, 4), frozenset())

    def test_the_same_pr_and_head_are_still_required(self):
        entry = _entry()
        self.assertEqual(self.withdrawn(entry, 1, pr=PR + 1), frozenset())
        self.assertEqual(self.withdrawn(entry, 1, head=OTHER_HEAD), frozenset())

    def test_a_ruling_on_another_head_withdraws_nothing(self):
        entry = _entry()
        entry['ruling']['head_sha'] = OTHER_HEAD
        self.assertEqual(self.withdrawn(entry, 1), frozenset())

    def test_a_version_that_is_not_a_plain_int_withdraws_nothing(self):
        for entry in (_entry(version=1), _entry(version=1, arbitrate=1)):
            for version in (None, True, False, '1', 1.0, [1], {}, object()):
                with self.subTest(arbitrate='path' in entry['ruling'], version=version):
                    self.assertEqual(self.withdrawn(entry, version), frozenset())

    def test_a_bool_version_does_not_match_the_int_one(self):
        self.assertEqual(self.withdrawn(_entry(version=1), True), frozenset())

    def test_an_entry_without_a_plain_int_review_version_withdraws_nothing(self):
        self.assertEqual(self.withdrawn(_entry(drop=('review_version',)), 1), frozenset())
        for bad in (None, True, '1', 1.0):
            with self.subTest(review_version=bad):
                self.assertEqual(self.withdrawn(_entry(version=bad), 1), frozenset())
                self.assertEqual(self.withdrawn(_entry(version=bad), bad), frozenset())

    def test_an_arbitrate_ruling_of_another_version_withdraws_nothing(self):
        entry = _entry(version=1, arbitrate=2)
        for version in (1, 2):
            with self.subTest(version=version):
                self.assertEqual(self.withdrawn(entry, version), frozenset())

    def test_an_arbitrate_ruling_without_a_plain_int_version_withdraws_nothing(self):
        for bad in (None, True, '1', 1.0):
            with self.subTest(ruling_version=bad):
                self.assertEqual(self.withdrawn(_entry(version=1, arbitrate=bad), 1), frozenset())
        entry = _entry(version=1, arbitrate=1)
        del entry['ruling']['review_version']
        self.assertEqual(self.withdrawn(entry, 1), frozenset())

    def test_other_states_and_outcomes_withdraw_nothing_at_any_version(self):
        cases = (
            _entry(outcome='UPHELD'),
            _entry(outcome='UPHELD', arbitrate=1),
            _entry(state='OPEN', outcome=None),
            _entry(state='DEFERRED', outcome=None),
            _entry(state='SUPERSEDED', outcome=None),
            _entry(state='WITHDRAWN', outcome=None),
            _entry(state='OPEN'),            # a ruling on a state that is not RULED
            _entry(state='SUPERSEDED'),
            _entry(state='WITHDRAWN'),
        )
        for entry in cases:
            for version in (0, 1, 2):
                with self.subTest(state=entry['state'], ruling=entry.get('ruling'), version=version):
                    self.assertEqual(self.withdrawn(entry, version), frozenset())

    def test_a_malformed_list_or_record_withdraws_nothing(self):
        self.assertEqual(review_repair.withdrawn_findings({'review_disputes': 'x'}, PR, HEAD, 1),
                         frozenset())
        self.assertEqual(review_repair.withdrawn_findings(None, PR, HEAD, 1), frozenset())
        self.assertEqual(review_repair.withdrawn_findings(_task('x', _entry()), PR, HEAD, 1),
                         frozenset({'F1'}))


class ReviewGateVersionTests(unittest.TestCase):
    def setUp(self):
        worker_policy_fixture.install(self)
        self.policy = repair_policy.load_policy()

    def gate(self, entries, **packet):
        base = {'reviewer_text_excerpt_untrusted_data': BODY, 'failed_head': HEAD, 'pr': PR}
        base.update(packet)
        return review_repair._review_gate(_task(*entries), [], base, FAMILY, self.policy,
                                          counts=COUNTS)

    def refused(self, entries, code='REPAIR_NO_BLOCKING_FINDING', **packet):
        with self.assertRaises(LLMToolError) as caught:
            self.gate(entries, **packet)
        self.assertEqual(caught.exception.review_repair_code, code)

    def test_the_ruled_version_is_refused_as_before(self):
        self.refused([_entry(version=1)], version=1)
        self.refused([_entry(version=1, arbitrate=1)], version=1)

    def test_a_newer_verdict_that_reuses_the_finding_id_is_judged_on_its_own(self):
        for entry in (_entry(version=1), _entry(version=1, arbitrate=1)):
            with self.subTest(arbitrate='path' in entry['ruling']):
                # Not refused on the withdrawal ground; it goes on to the limit checks.
                self.assertIsNone(self.gate([entry], version=2))

    def test_the_limit_checks_still_apply_to_the_newer_verdict(self):
        spent = {'review_repairs': self.policy['repair_max'], 'review_heads': {OTHER_HEAD: 'FAIL'}}
        with self.assertRaises(LLMToolError) as caught:
            review_repair._review_gate(
                _task(_entry(version=1)), [],
                {'reviewer_text_excerpt_untrusted_data': BODY, 'failed_head': HEAD,
                 'pr': PR, 'version': 2},
                FAMILY, self.policy, counts=spent)
        self.assertNotEqual(caught.exception.review_repair_code, 'REPAIR_NO_BLOCKING_FINDING')

    def test_a_packet_without_a_plain_int_version_withdraws_nothing(self):
        entries = [_entry(version=1)]
        self.assertIsNone(self.gate(entries))                       # no version key
        for version in (None, True, '1', 1.0):
            with self.subTest(version=version):
                self.assertIsNone(self.gate(entries, version=version))

    def test_a_ruling_of_another_version_than_its_entry_withdraws_nothing(self):
        self.assertIsNone(self.gate([_entry(version=1, arbitrate=2)], version=1))

    def test_other_entries_withdraw_nothing_at_any_version(self):
        for entry in (_entry(outcome='UPHELD'), _entry(state='OPEN', outcome=None),
                      _entry(state='DEFERRED', outcome=None),
                      _entry(state='SUPERSEDED', outcome=None),
                      _entry(state='WITHDRAWN', outcome=None)):
            for version in (1, 2):
                with self.subTest(state=entry['state'], version=version):
                    self.assertIsNone(self.gate([entry], version=version))


if __name__ == '__main__':
    unittest.main()
