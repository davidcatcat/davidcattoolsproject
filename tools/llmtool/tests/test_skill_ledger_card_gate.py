"""Ledger prepare, confirm and read-only verifiers (#1502 WP-F gate PR 1).

Test data rule (Owner #763 A): every value here is a fixed, non-secret test
label. The ledger home is a private temporary directory injected through
skill_ledger._path; the authenticated main read and the role-card build are
patched with unittest.mock, so no Git, network or provider is used.
File-system faults (a broken chain, a lock failure) are injected. No
production check is disabled. Stdlib unittest only.
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import skill_ledger, skill_manifest, task_completion

ROUTE = 'claude'
PIN = '1' * 40
OTHER_PIN = '2' * 40
REPO_ROOT = 'repo-root-read-only-by-fake-builder'
PREFIX = 'fixed role-card prefix used by ledger gate tests\n'
PREFIX_DIGEST = hashlib.sha256(PREFIX.encode('utf-8')).hexdigest()
CARDS = (
    {'path': 'skills/role-cards/common-core.md', 'sha256': 'a' * 64},
    {'path': 'skills/role-cards/developer.md', 'sha256': 'b' * 64},
)
BINDING = {
    'task_id': 'task-a', 'run_id': 'run-a', 'session_id': 'session-a',
    'agent_id': 'agent-a', 'role': 'developer', 'provider': 'claude',
    'model': 'model-a', 'repo': 'repo-a', 'head': 'head-a',
    'manifest_sha256': PREFIX_DIGEST,
}


class FakeCards:
    """Injected main read and card build; values are fixed test labels."""

    def __init__(self):
        self.main = PIN
        self.prefix = PREFIX
        self.commit = None  # None: report the requested commit


class Ledger:
    def __init__(self, path, fake):
        self.path = path
        self.anchor = Path(str(path) + '.head')
        self.fake = fake

    def snapshot(self):
        return tuple(p.read_bytes() if p.exists() else None
                     for p in (self.path, self.anchor))

    def rows(self):
        return [json.loads(line) for line in self.path.read_bytes().splitlines()]


def entry(**changes):
    value = dict(BINDING, route=ROUTE)
    value.update(changes)
    return value


def prepare(**changes):
    value = entry(**changes)
    skill_ledger.pin_dispatch(value['task_id'], value['run_id'], value['route'])
    return skill_ledger.prepare_card_delivery(value, REPO_ROOT)


def confirm(prepared_id, *, task_id='task-a', run_id='run-a', session_id='session-a'):
    return skill_ledger.confirm_card_delivery(
        prepared_id, task_id, run_id, session_id, REPO_ROOT)


def status_args(prepared_id, /, **changes):
    # prepared_id is positional-only so a 'prepared_id' override in changes
    # is accepted and applied by args.update instead of raising TypeError.
    args = {'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
            'route': ROUTE, 'session_id': 'session-a',
            'prefix_sha256': PREFIX_DIGEST, 'pin_commit': PIN,
            'prepared_id': prepared_id}
    args.update(changes)
    return args


def receipt_status(prepared_id, /, **changes):
    return skill_ledger.card_receipt_status(**status_args(prepared_id, **changes))


def preparation_status(prepared_id, /, **changes):
    return skill_ledger.card_preparation_status(**status_args(prepared_id, **changes))


def legacy_check(**changes):
    value = dict(BINDING)
    value.update(changes)
    return skill_ledger.check(*(value[key] for key in skill_ledger._BINDING_FIELDS))


def break_chain(ledger, row_index=-1):
    """Edit one stored field in place so its entry hash no longer matches."""
    lines = ledger.path.read_bytes().splitlines(keepends=True)
    row = json.loads(lines[row_index])
    row['agent_id'] = 'agent-edited'
    lines[row_index] = json.dumps(row, sort_keys=True).encode('utf-8') + b'\n'
    ledger.path.write_bytes(b''.join(lines))


class _FailingFlock:
    """Injected fcntl stand-in whose lock call fails like a file-system fault."""

    LOCK_EX = 2
    LOCK_UN = 8

    @staticmethod
    def flock(fd, operation):
        raise OSError('injected lock failure')


class SkillLedgerCardGateTest(unittest.TestCase):
    """Each test, and each subTest case, runs on its own private ledger home."""

    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self._root = Path(root.name)
        self._homes = 0
        self.ledger = None
        self._patch(skill_ledger, '_path', lambda: self.ledger.path)
        self._patch(task_completion, 'read_authenticated_main_sha',
                    lambda: self.ledger.fake.main)
        self._patch(skill_manifest, 'role_card_routes', lambda: frozenset((ROUTE,)))
        self._patch(skill_manifest, 'build_role_cards', self._build_role_cards)
        self._patch(skill_manifest, 'role_card_prefix',
                    lambda built: self.ledger.fake.prefix)
        self.fresh_ledger()

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build_role_cards(self, repo_root, base_commit, route, *, main_ref):
        fake = self.ledger.fake
        return {'route': route, 'commit': fake.commit or base_commit,
                'cards': [dict(card) for card in CARDS],
                'cards_sha256': 'c' * 64, 'prefix_size': len(fake.prefix)}

    def fresh_ledger(self):
        """Point the injected ledger home at a new, empty private directory."""
        self._homes += 1
        home = self._root / 'ledger-home-{}'.format(self._homes)
        home.mkdir(mode=0o700)
        os.chmod(home, 0o700)
        self.ledger = Ledger(home / 'skill-ledger.jsonl', FakeCards())
        return self.ledger

    def refused_code(self, call, *args, **kwargs):
        with self.assertRaises(skill_ledger.SkillLedgerError) as raised:
            call(*args, **kwargs)
        return raised.exception.skill_ledger_code

    # --- accept -------------------------------------------------------------

    def test_prepare_then_confirm_verifies(self):
        ledger = self.ledger
        prepared_id = prepare()
        self.assertEqual(preparation_status(prepared_id), {
            'status': 'PREPARED', 'reason': None, 'prepared_id': prepared_id})

        receipt_id = confirm(prepared_id)

        result = receipt_status(prepared_id)
        self.assertEqual(result, {'status': 'VERIFIED', 'reason': None,
                                  'receipt_id': receipt_id})
        prepared_row, receipt_row = ledger.rows()[-2:]
        self.assertEqual(prepared_row['entry_type'], skill_ledger.CARD_PREPARED)
        self.assertEqual(prepared_row['evidence_level'], 'UNVERIFIED')
        self.assertEqual(prepared_row['prepared_id'], prepared_id)
        self.assertEqual(prepared_row['receipt_id'], prepared_id)
        self.assertEqual(receipt_row['evidence_level'], skill_ledger.CONTROLLER_DELIVERED)
        self.assertEqual(receipt_row['prepared_id'], prepared_id)
        self.assertNotIn('entry_type', receipt_row)
        for key in skill_ledger._BINDING_FIELDS + ('route', 'base_commit', 'pin_id',
                                                   'cards', 'cards_sha256', 'prefix_size'):
            with self.subTest(key=key):
                self.assertEqual(receipt_row[key], prepared_row[key])
        self.assertEqual(receipt_row['base_commit'], PIN)

    def test_confirm_takes_binding_from_prepared_row(self):
        prepared_id = prepare(model='model-requested', head='head-requested')
        confirm(prepared_id)
        receipt_row = self.ledger.rows()[-1]
        self.assertEqual(receipt_row['model'], 'model-requested')
        self.assertEqual(receipt_row['head'], 'head-requested')

    # --- CARD_PREPARED alone --------------------------------------------------

    def test_card_prepared_alone_never_verifies(self):
        prepared_id = prepare()

        self.assertEqual(receipt_status(prepared_id), {
            'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_RECEIPT_MISSING'})
        self.assertEqual(legacy_check(), {'status': 'UNVERIFIED', 'reason': 'NOT_FOUND'})

    # --- confirm refusals -------------------------------------------------------

    def test_confirm_without_prepare_refuses_and_appends_nothing(self):
        ledger = self.ledger
        skill_ledger.pin_dispatch('task-a', 'run-a', ROUTE)
        skill_ledger.record_not_delivered({
            'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
            'route': 'gw-agent', 'reason_code': 'ROUTE_NOT_SUPPORTED'})
        pin_id, exemption_id = (row['receipt_id'] for row in ledger.rows())
        before = ledger.snapshot()

        for not_prepared in ('prepared-row-absent', pin_id, exemption_id):
            with self.subTest(prepared_id=not_prepared):
                self.assertEqual(self.refused_code(confirm, not_prepared),
                                 'SKILL_DELIVERY_PREPARATION_MISSING')
        self.assertEqual(ledger.snapshot(), before)

    def test_confirm_with_mismatched_binding_refuses(self):
        cases = [
            ({'task_id': 'task-b'}, 'SKILL_DELIVERY_PREPARATION_MISSING'),
            ({'run_id': 'run-b'}, 'SKILL_DELIVERY_WRONG_RUN'),
            ({'session_id': 'session-b'}, 'SKILL_DELIVERY_WRONG_SESSION'),
        ]
        for changes, code in cases:
            with self.subTest(changes=changes, code=code):
                ledger = self.fresh_ledger()
                prepared_id = prepare()
                before = ledger.snapshot()

                self.assertEqual(self.refused_code(confirm, prepared_id, **changes), code)
                self.assertEqual(ledger.snapshot(), before)
                self.assertEqual(receipt_status(prepared_id)['reason'],
                                 'SKILL_DELIVERY_RECEIPT_MISSING')

    def test_confirm_rederives_cards_and_refuses_changed_prefix(self):
        ledger = self.ledger
        prepared_id = prepare()
        ledger.fake.prefix = 'a different role-card prefix\n'
        before = ledger.snapshot()

        self.assertEqual(self.refused_code(confirm, prepared_id),
                         'SKILL_DELIVERY_DIGEST_MISMATCH')
        self.assertEqual(ledger.snapshot(), before)

    def test_confirm_refuses_cards_built_from_another_commit(self):
        ledger = self.ledger
        prepared_id = prepare()
        ledger.fake.commit = OTHER_PIN
        before = ledger.snapshot()

        self.assertEqual(self.refused_code(confirm, prepared_id),
                         'SKILL_DELIVERY_PIN_MISMATCH')
        self.assertEqual(ledger.snapshot(), before)

    def test_confirm_refuses_revoked_preparation(self):
        ledger = self.ledger
        prepared_id = prepare()
        skill_ledger.revoke(prepared_id, 'test revocation')
        before = ledger.snapshot()

        self.assertEqual(self.refused_code(confirm, prepared_id), 'SKILL_DELIVERY_REVOKED')
        self.assertEqual(ledger.snapshot(), before)
        self.assertEqual(preparation_status(prepared_id)['reason'], 'SKILL_DELIVERY_REVOKED')

    def test_repeat_confirm_appends_no_bytes(self):
        ledger = self.ledger
        prepared_id = prepare()
        receipt_id = confirm(prepared_id)
        before = ledger.snapshot()

        self.assertEqual(self.refused_code(confirm, prepared_id),
                         'SKILL_DELIVERY_ALREADY_CONFIRMED')
        self.assertEqual(ledger.snapshot(), before)
        self.assertEqual(receipt_status(prepared_id)['receipt_id'], receipt_id)

        skill_ledger.revoke(receipt_id, 'test revocation')
        after_revoke = ledger.snapshot()
        self.assertEqual(self.refused_code(confirm, prepared_id),
                         'SKILL_DELIVERY_ALREADY_CONFIRMED')
        self.assertEqual(ledger.snapshot(), after_revoke)

    # --- prepare refusals (today's record_card_delivery checks) ---------------

    def test_prepare_without_pin_refuses(self):
        self.assertEqual(
            self.refused_code(skill_ledger.prepare_card_delivery, entry(), REPO_ROOT),
            'DISPATCH_PIN_MISSING')
        self.assertEqual(self.ledger.snapshot(), (None, None))

    def test_prepare_refusals_append_nothing(self):
        cases = [
            ({'manifest_sha256': 'e' * 64}, 'MANIFEST_DIGEST_MISMATCH'),
            ({'role': 'reviewer'}, 'ROLE_MISMATCH'),
            ({'cards': list(CARDS)}, 'CALLER_DIGESTS_REFUSED'),
            ({'base_commit': PIN}, 'CALLER_BASE_REFUSED'),
            ({'evidence_level': 'CONTROLLER_DELIVERED'}, 'INVALID_EVIDENCE_LEVEL'),
            ({'unexpected_field': 'x'}, 'INVALID_ENTRY'),
        ]
        for changes, code in cases:
            with self.subTest(changes=changes, code=code):
                ledger = self.fresh_ledger()
                skill_ledger.pin_dispatch('task-a', 'run-a', ROUTE)
                before = ledger.snapshot()

                self.assertEqual(
                    self.refused_code(skill_ledger.prepare_card_delivery,
                                      entry(**changes), REPO_ROOT),
                    code)
                self.assertEqual(ledger.snapshot(), before)

    # --- verifier refusals ----------------------------------------------------

    def test_receipt_status_mismatch_refuses(self):
        cases = [
            ({'prefix_sha256': 'e' * 64}, 'SKILL_DELIVERY_DIGEST_MISMATCH'),
            ({'run_id': 'run-b'}, 'SKILL_DELIVERY_WRONG_RUN'),
            ({'session_id': 'session-b'}, 'SKILL_DELIVERY_WRONG_SESSION'),
            ({'role': 'reviewer'}, 'SKILL_DELIVERY_WRONG_ROLE'),
            ({'pin_commit': OTHER_PIN}, 'SKILL_DELIVERY_PIN_MISMATCH'),
            ({'route': 'other-route'}, 'SKILL_DELIVERY_PIN_MISMATCH'),
            ({'prepared_id': 'prepared-row-absent'}, 'SKILL_DELIVERY_RECEIPT_MISSING'),
            ({'task_id': 'task-b'}, 'SKILL_DELIVERY_RECEIPT_MISSING'),
            ({'session_id': ''}, 'SKILL_DELIVERY_RECEIPT_MISSING'),
        ]
        for changes, code in cases:
            with self.subTest(changes=changes, code=code):
                self.fresh_ledger()
                prepared_id = prepare()
                confirm(prepared_id)

                self.assertEqual(receipt_status(prepared_id, **changes),
                                 {'status': 'UNVERIFIED', 'reason': code})

    def test_preparation_status_mismatch_refuses(self):
        cases = [
            ({'prefix_sha256': 'e' * 64}, 'SKILL_DELIVERY_DIGEST_MISMATCH'),
            ({'run_id': 'run-b'}, 'SKILL_DELIVERY_WRONG_RUN'),
            ({'session_id': 'session-b'}, 'SKILL_DELIVERY_WRONG_SESSION'),
            ({'role': 'reviewer'}, 'SKILL_DELIVERY_WRONG_ROLE'),
            ({'pin_commit': OTHER_PIN}, 'SKILL_DELIVERY_PIN_MISMATCH'),
            ({'prepared_id': 'prepared-row-absent'}, 'SKILL_DELIVERY_PREPARATION_MISSING'),
            ({'task_id': 'task-b'}, 'SKILL_DELIVERY_PREPARATION_MISSING'),
        ]
        for changes, code in cases:
            with self.subTest(changes=changes, code=code):
                self.fresh_ledger()
                prepared_id = prepare()

                self.assertEqual(preparation_status(prepared_id, **changes),
                                 {'status': 'UNVERIFIED', 'reason': code})

    def test_another_runs_receipt_refuses(self):
        prepared_a = prepare()
        confirm(prepared_a)
        prepared_b = prepare(run_id='run-b')
        confirm(prepared_b, run_id='run-b')

        self.assertEqual(receipt_status(prepared_a, run_id='run-b')['reason'],
                         'SKILL_DELIVERY_WRONG_RUN')
        self.assertEqual(receipt_status(prepared_b)['reason'], 'SKILL_DELIVERY_WRONG_RUN')
        self.assertEqual(receipt_status(prepared_b, run_id='run-b')['status'], 'VERIFIED')

    def test_revoked_receipt_refuses(self):
        prepared_id = prepare()
        receipt_id = confirm(prepared_id)
        skill_ledger.revoke(receipt_id, 'test revocation')

        self.assertEqual(receipt_status(prepared_id), {
            'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_REVOKED'})

    def test_revoked_preparation_refuses_receipt(self):
        prepared_id = prepare()
        confirm(prepared_id)
        skill_ledger.revoke(prepared_id, 'test revocation')

        self.assertEqual(receipt_status(prepared_id)['reason'], 'SKILL_DELIVERY_REVOKED')

    def test_broken_chain_refuses_with_ledger_unreadable(self):
        ledger = self.ledger
        prepared_id = prepare()
        confirm(prepared_id)
        skill_ledger.record_not_delivered({
            'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
            'route': 'gw-agent', 'reason_code': 'ROUTE_NOT_SUPPORTED'})
        break_chain(ledger, row_index=1)
        before = ledger.snapshot()

        unreadable = {'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_LEDGER_UNREADABLE'}
        self.assertEqual(receipt_status(prepared_id), unreadable)
        self.assertEqual(preparation_status(prepared_id), unreadable)
        self.assertEqual(skill_ledger.not_delivered_status(
            'task-a', 'run-a', 'developer', 'gw-agent'), unreadable)
        self.assertEqual(ledger.snapshot(), before)

    def test_confirm_on_broken_chain_refuses(self):
        ledger = self.ledger
        prepared_id = prepare()
        break_chain(ledger)
        before = ledger.snapshot()

        self.assertEqual(self.refused_code(confirm, prepared_id),
                         'SKILL_DELIVERY_LEDGER_UNREADABLE')
        self.assertEqual(ledger.snapshot(), before)

    def test_lock_failure_refuses_with_ledger_unreadable(self):
        ledger = self.ledger
        prepared_id = prepare()
        confirm(prepared_id)
        before = ledger.snapshot()
        self._patch(skill_ledger, 'fcntl', _FailingFlock)

        unreadable = {'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_LEDGER_UNREADABLE'}
        self.assertEqual(receipt_status(prepared_id), unreadable)
        self.assertEqual(preparation_status(prepared_id), unreadable)
        self.assertEqual(skill_ledger.not_delivered_status(
            'task-a', 'run-a', 'developer', 'gw-agent'), unreadable)
        self.assertEqual(self.refused_code(confirm, prepared_id),
                         'SKILL_DELIVERY_LEDGER_UNREADABLE')
        self.assertEqual(ledger.snapshot(), before)

    # --- old-format receipts --------------------------------------------------

    def test_old_format_receipt_does_not_verify(self):
        skill_ledger.pin_dispatch('task-a', 'run-a', ROUTE)
        old_receipt = skill_ledger.record_card_delivery(
            entry(session_id='provider-session'), repo_root=REPO_ROOT)

        # record_card_delivery() and check() keep today's behaviour.
        self.assertEqual(legacy_check(session_id='provider-session')['status'], 'VERIFIED')
        self.assertNotIn('prepared_id', self.ledger.rows()[-1])
        # The old row has no prepared_id, so it never satisfies the gate.
        self.assertEqual(
            receipt_status(old_receipt, session_id='provider-session')['reason'],
            'SKILL_DELIVERY_RECEIPT_MISSING')

    def test_old_writer_row_does_not_satisfy_a_real_preparation(self):
        prepared_id = prepare()
        skill_ledger.record_card_delivery(entry(), repo_root=REPO_ROOT)

        self.assertEqual(receipt_status(prepared_id)['reason'],
                         'SKILL_DELIVERY_RECEIPT_MISSING')

    def test_record_still_refuses_controller_delivered(self):
        level = dict(BINDING, evidence_level='CONTROLLER_DELIVERED')
        self.assertEqual(self.refused_code(skill_ledger.record, level),
                         'CONTROLLER_DELIVERED_REFUSED')

    # --- exemption --------------------------------------------------------------

    def test_not_delivered_status(self):
        exemption_id = skill_ledger.record_not_delivered({
            'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
            'route': 'gw-agent', 'reason_code': 'ROUTE_NOT_SUPPORTED'})
        prepare()

        self.assertEqual(
            skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', 'gw-agent'),
            {'status': 'NOT_DELIVERED', 'reason': None, 'receipt_id': exemption_id,
             'reason_code': 'ROUTE_NOT_SUPPORTED'})
        missing = {'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_EXEMPTION_MISSING'}
        self.assertEqual(
            skill_ledger.not_delivered_status('task-a', 'run-b', 'developer', 'gw-agent'),
            missing)
        self.assertEqual(
            skill_ledger.not_delivered_status('task-a', 'run-a', 'reviewer', 'gw-agent'),
            missing)
        # A CARD_PREPARED row for another route is never an exemption.
        self.assertEqual(
            skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', ROUTE),
            missing)

        skill_ledger.revoke(exemption_id, 'test revocation')
        self.assertEqual(
            skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', 'gw-agent'),
            {'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_REVOKED'})

    # --- verifiers append nothing ----------------------------------------------

    def test_verifiers_append_nothing(self):
        ledger = self.ledger
        prepared_id = prepare()
        confirm(prepared_id)
        other_prepared = prepare(run_id='run-b')
        skill_ledger.record_not_delivered({
            'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
            'route': 'gw-agent', 'reason_code': 'ROUTE_NOT_SUPPORTED'})
        before = ledger.snapshot()

        calls = [
            lambda: receipt_status(prepared_id),
            lambda: receipt_status(prepared_id, session_id='session-b'),
            lambda: receipt_status(other_prepared, run_id='run-b'),
            lambda: receipt_status('prepared-row-absent'),
            lambda: preparation_status(prepared_id),
            lambda: preparation_status(other_prepared, run_id='run-b'),
            lambda: preparation_status(prepared_id, pin_commit=OTHER_PIN),
            lambda: skill_ledger.not_delivered_status(
                'task-a', 'run-a', 'developer', 'gw-agent'),
            lambda: skill_ledger.not_delivered_status(
                'task-a', 'run-z', 'developer', 'gw-agent'),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                call()
                call()
                self.assertEqual(ledger.snapshot(), before)

    def test_verifiers_on_empty_ledger_append_nothing(self):
        ledger = self.ledger
        self.assertEqual(receipt_status('prepared-row-absent')['reason'],
                         'SKILL_DELIVERY_RECEIPT_MISSING')
        self.assertEqual(preparation_status('prepared-row-absent')['reason'],
                         'SKILL_DELIVERY_PREPARATION_MISSING')
        self.assertEqual(
            skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', 'gw-agent')[
                'reason'],
            'SKILL_DELIVERY_EXEMPTION_MISSING')
        self.assertFalse(ledger.path.exists())
        self.assertFalse(ledger.anchor.exists())


if __name__ == '__main__':
    unittest.main()
