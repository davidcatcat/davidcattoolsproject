"""Ledger prepare, confirm and read-only verifiers (#1502 WP-F gate PR 1).

Test data rule (Owner #763 A): every value here is a fixed, non-secret test
label. The ledger home is a private temporary directory injected through
skill_ledger._path; the authenticated main read and the role-card build are
monkeypatched, so no Git, network or provider is used. File-system faults
(a broken chain, a lock failure) are injected. No production check is
disabled.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

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


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    home = tmp_path / 'ledger-home'
    home.mkdir(mode=0o700)
    os.chmod(home, 0o700)
    path = home / 'skill-ledger.jsonl'
    monkeypatch.setattr(skill_ledger, '_path', lambda: path)
    fake = FakeCards()

    def read_main():
        return fake.main

    def build_role_cards(repo_root, base_commit, route, *, main_ref):
        return {'route': route, 'commit': fake.commit or base_commit,
                'cards': [dict(card) for card in CARDS],
                'cards_sha256': 'c' * 64, 'prefix_size': len(fake.prefix)}

    monkeypatch.setattr(task_completion, 'read_authenticated_main_sha', read_main)
    monkeypatch.setattr(skill_manifest, 'role_card_routes', lambda: frozenset((ROUTE,)))
    monkeypatch.setattr(skill_manifest, 'build_role_cards', build_role_cards)
    monkeypatch.setattr(skill_manifest, 'role_card_prefix', lambda built: fake.prefix)
    return Ledger(path, fake)


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


def status_args(prepared_id, **changes):
    args = {'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
            'route': ROUTE, 'session_id': 'session-a',
            'prefix_sha256': PREFIX_DIGEST, 'pin_commit': PIN,
            'prepared_id': prepared_id}
    args.update(changes)
    return args


def receipt_status(prepared_id, **changes):
    return skill_ledger.card_receipt_status(**status_args(prepared_id, **changes))


def preparation_status(prepared_id, **changes):
    return skill_ledger.card_preparation_status(**status_args(prepared_id, **changes))


def legacy_check(**changes):
    value = dict(BINDING)
    value.update(changes)
    return skill_ledger.check(*(value[key] for key in skill_ledger._BINDING_FIELDS))


def refused_code(call, *args, **kwargs):
    with pytest.raises(skill_ledger.SkillLedgerError) as raised:
        call(*args, **kwargs)
    return raised.value.skill_ledger_code


def break_chain(ledger, row_index=-1):
    """Edit one stored field in place so its entry hash no longer matches."""
    lines = ledger.path.read_bytes().splitlines(keepends=True)
    row = json.loads(lines[row_index])
    row['agent_id'] = 'agent-edited'
    lines[row_index] = json.dumps(row, sort_keys=True).encode('utf-8') + b'\n'
    ledger.path.write_bytes(b''.join(lines))


# --- accept -----------------------------------------------------------------

def test_prepare_then_confirm_verifies(ledger):
    prepared_id = prepare()
    assert preparation_status(prepared_id) == {
        'status': 'PREPARED', 'reason': None, 'prepared_id': prepared_id}

    receipt_id = confirm(prepared_id)

    result = receipt_status(prepared_id)
    assert result == {'status': 'VERIFIED', 'reason': None, 'receipt_id': receipt_id}
    prepared_row, receipt_row = ledger.rows()[-2:]
    assert prepared_row['entry_type'] == skill_ledger.CARD_PREPARED
    assert prepared_row['evidence_level'] == 'UNVERIFIED'
    assert prepared_row['prepared_id'] == prepared_row['receipt_id'] == prepared_id
    assert receipt_row['evidence_level'] == skill_ledger.CONTROLLER_DELIVERED
    assert receipt_row['prepared_id'] == prepared_id
    assert 'entry_type' not in receipt_row
    for key in skill_ledger._BINDING_FIELDS + ('route', 'base_commit', 'pin_id',
                                               'cards', 'cards_sha256', 'prefix_size'):
        assert receipt_row[key] == prepared_row[key]
    assert receipt_row['base_commit'] == PIN


def test_confirm_takes_binding_from_prepared_row(ledger):
    prepared_id = prepare(model='model-requested', head='head-requested')
    confirm(prepared_id)
    receipt_row = ledger.rows()[-1]
    assert receipt_row['model'] == 'model-requested'
    assert receipt_row['head'] == 'head-requested'


# --- CARD_PREPARED alone ----------------------------------------------------

def test_card_prepared_alone_never_verifies(ledger):
    prepared_id = prepare()

    assert receipt_status(prepared_id) == {
        'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_RECEIPT_MISSING'}
    assert legacy_check() == {'status': 'UNVERIFIED', 'reason': 'NOT_FOUND'}


# --- confirm refusals ---------------------------------------------------------

def test_confirm_without_prepare_refuses_and_appends_nothing(ledger):
    skill_ledger.pin_dispatch('task-a', 'run-a', ROUTE)
    skill_ledger.record_not_delivered({
        'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
        'route': 'gw-agent', 'reason_code': 'ROUTE_NOT_SUPPORTED'})
    pin_id, exemption_id = (row['receipt_id'] for row in ledger.rows())
    before = ledger.snapshot()

    for not_prepared in ('prepared-row-absent', pin_id, exemption_id):
        assert refused_code(confirm, not_prepared) == 'SKILL_DELIVERY_PREPARATION_MISSING'
    assert ledger.snapshot() == before


@pytest.mark.parametrize('changes, code', [
    ({'task_id': 'task-b'}, 'SKILL_DELIVERY_PREPARATION_MISSING'),
    ({'run_id': 'run-b'}, 'SKILL_DELIVERY_WRONG_RUN'),
    ({'session_id': 'session-b'}, 'SKILL_DELIVERY_WRONG_SESSION'),
])
def test_confirm_with_mismatched_binding_refuses(ledger, changes, code):
    prepared_id = prepare()
    before = ledger.snapshot()

    assert refused_code(confirm, prepared_id, **changes) == code
    assert ledger.snapshot() == before
    assert receipt_status(prepared_id)['reason'] == 'SKILL_DELIVERY_RECEIPT_MISSING'


def test_confirm_rederives_cards_and_refuses_changed_prefix(ledger):
    prepared_id = prepare()
    ledger.fake.prefix = 'a different role-card prefix\n'
    before = ledger.snapshot()

    assert refused_code(confirm, prepared_id) == 'SKILL_DELIVERY_DIGEST_MISMATCH'
    assert ledger.snapshot() == before


def test_confirm_refuses_cards_built_from_another_commit(ledger):
    prepared_id = prepare()
    ledger.fake.commit = OTHER_PIN
    before = ledger.snapshot()

    assert refused_code(confirm, prepared_id) == 'SKILL_DELIVERY_PIN_MISMATCH'
    assert ledger.snapshot() == before


def test_confirm_refuses_revoked_preparation(ledger):
    prepared_id = prepare()
    skill_ledger.revoke(prepared_id, 'test revocation')
    before = ledger.snapshot()

    assert refused_code(confirm, prepared_id) == 'SKILL_DELIVERY_REVOKED'
    assert ledger.snapshot() == before
    assert preparation_status(prepared_id)['reason'] == 'SKILL_DELIVERY_REVOKED'


def test_repeat_confirm_appends_no_bytes(ledger):
    prepared_id = prepare()
    receipt_id = confirm(prepared_id)
    before = ledger.snapshot()

    assert refused_code(confirm, prepared_id) == 'SKILL_DELIVERY_ALREADY_CONFIRMED'
    assert ledger.snapshot() == before
    assert receipt_status(prepared_id)['receipt_id'] == receipt_id

    skill_ledger.revoke(receipt_id, 'test revocation')
    after_revoke = ledger.snapshot()
    assert refused_code(confirm, prepared_id) == 'SKILL_DELIVERY_ALREADY_CONFIRMED'
    assert ledger.snapshot() == after_revoke


# --- prepare refusals (today's record_card_delivery checks) -----------------

def test_prepare_without_pin_refuses(ledger):
    assert refused_code(skill_ledger.prepare_card_delivery, entry(), REPO_ROOT) == \
        'DISPATCH_PIN_MISSING'
    assert ledger.snapshot() == (None, None)


@pytest.mark.parametrize('changes, code', [
    ({'manifest_sha256': 'e' * 64}, 'MANIFEST_DIGEST_MISMATCH'),
    ({'role': 'reviewer'}, 'ROLE_MISMATCH'),
    ({'cards': list(CARDS)}, 'CALLER_DIGESTS_REFUSED'),
    ({'base_commit': PIN}, 'CALLER_BASE_REFUSED'),
    ({'evidence_level': 'CONTROLLER_DELIVERED'}, 'INVALID_EVIDENCE_LEVEL'),
    ({'unexpected_field': 'x'}, 'INVALID_ENTRY'),
])
def test_prepare_refusals_append_nothing(ledger, changes, code):
    skill_ledger.pin_dispatch('task-a', 'run-a', ROUTE)
    before = ledger.snapshot()

    assert refused_code(skill_ledger.prepare_card_delivery,
                        entry(**changes), REPO_ROOT) == code
    assert ledger.snapshot() == before


# --- verifier refusals ------------------------------------------------------

@pytest.mark.parametrize('changes, code', [
    ({'prefix_sha256': 'e' * 64}, 'SKILL_DELIVERY_DIGEST_MISMATCH'),
    ({'run_id': 'run-b'}, 'SKILL_DELIVERY_WRONG_RUN'),
    ({'session_id': 'session-b'}, 'SKILL_DELIVERY_WRONG_SESSION'),
    ({'role': 'reviewer'}, 'SKILL_DELIVERY_WRONG_ROLE'),
    ({'pin_commit': OTHER_PIN}, 'SKILL_DELIVERY_PIN_MISMATCH'),
    ({'route': 'other-route'}, 'SKILL_DELIVERY_PIN_MISMATCH'),
    ({'prepared_id': 'prepared-row-absent'}, 'SKILL_DELIVERY_RECEIPT_MISSING'),
    ({'task_id': 'task-b'}, 'SKILL_DELIVERY_RECEIPT_MISSING'),
    ({'session_id': ''}, 'SKILL_DELIVERY_RECEIPT_MISSING'),
])
def test_receipt_status_mismatch_refuses(ledger, changes, code):
    prepared_id = prepare()
    confirm(prepared_id)

    assert receipt_status(prepared_id, **changes) == {
        'status': 'UNVERIFIED', 'reason': code}


@pytest.mark.parametrize('changes, code', [
    ({'prefix_sha256': 'e' * 64}, 'SKILL_DELIVERY_DIGEST_MISMATCH'),
    ({'run_id': 'run-b'}, 'SKILL_DELIVERY_WRONG_RUN'),
    ({'session_id': 'session-b'}, 'SKILL_DELIVERY_WRONG_SESSION'),
    ({'role': 'reviewer'}, 'SKILL_DELIVERY_WRONG_ROLE'),
    ({'pin_commit': OTHER_PIN}, 'SKILL_DELIVERY_PIN_MISMATCH'),
    ({'prepared_id': 'prepared-row-absent'}, 'SKILL_DELIVERY_PREPARATION_MISSING'),
    ({'task_id': 'task-b'}, 'SKILL_DELIVERY_PREPARATION_MISSING'),
])
def test_preparation_status_mismatch_refuses(ledger, changes, code):
    prepared_id = prepare()

    assert preparation_status(prepared_id, **changes) == {
        'status': 'UNVERIFIED', 'reason': code}


def test_another_runs_receipt_refuses(ledger):
    prepared_a = prepare()
    confirm(prepared_a)
    prepared_b = prepare(run_id='run-b')
    confirm(prepared_b, run_id='run-b')

    assert receipt_status(prepared_a, run_id='run-b')['reason'] == 'SKILL_DELIVERY_WRONG_RUN'
    assert receipt_status(prepared_b)['reason'] == 'SKILL_DELIVERY_WRONG_RUN'
    assert receipt_status(prepared_b, run_id='run-b')['status'] == 'VERIFIED'


def test_revoked_receipt_refuses(ledger):
    prepared_id = prepare()
    receipt_id = confirm(prepared_id)
    skill_ledger.revoke(receipt_id, 'test revocation')

    assert receipt_status(prepared_id) == {
        'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_REVOKED'}


def test_revoked_preparation_refuses_receipt(ledger):
    prepared_id = prepare()
    confirm(prepared_id)
    skill_ledger.revoke(prepared_id, 'test revocation')

    assert receipt_status(prepared_id)['reason'] == 'SKILL_DELIVERY_REVOKED'


def test_broken_chain_refuses_with_ledger_unreadable(ledger):
    prepared_id = prepare()
    confirm(prepared_id)
    skill_ledger.record_not_delivered({
        'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
        'route': 'gw-agent', 'reason_code': 'ROUTE_NOT_SUPPORTED'})
    break_chain(ledger, row_index=1)
    before = ledger.snapshot()

    unreadable = {'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_LEDGER_UNREADABLE'}
    assert receipt_status(prepared_id) == unreadable
    assert preparation_status(prepared_id) == unreadable
    assert skill_ledger.not_delivered_status(
        'task-a', 'run-a', 'developer', 'gw-agent') == unreadable
    assert ledger.snapshot() == before


def test_confirm_on_broken_chain_refuses(ledger):
    prepared_id = prepare()
    break_chain(ledger)
    before = ledger.snapshot()

    assert refused_code(confirm, prepared_id) == 'SKILL_DELIVERY_LEDGER_UNREADABLE'
    assert ledger.snapshot() == before


class _FailingFlock:
    """Injected fcntl stand-in whose lock call fails like a file-system fault."""

    LOCK_EX = 2
    LOCK_UN = 8

    @staticmethod
    def flock(fd, operation):
        raise OSError('injected lock failure')


def test_lock_failure_refuses_with_ledger_unreadable(ledger, monkeypatch):
    prepared_id = prepare()
    confirm(prepared_id)
    before = ledger.snapshot()
    monkeypatch.setattr(skill_ledger, 'fcntl', _FailingFlock)

    unreadable = {'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_LEDGER_UNREADABLE'}
    assert receipt_status(prepared_id) == unreadable
    assert preparation_status(prepared_id) == unreadable
    assert skill_ledger.not_delivered_status(
        'task-a', 'run-a', 'developer', 'gw-agent') == unreadable
    assert refused_code(confirm, prepared_id) == 'SKILL_DELIVERY_LEDGER_UNREADABLE'
    assert ledger.snapshot() == before


# --- old-format receipts ----------------------------------------------------

def test_old_format_receipt_does_not_verify(ledger):
    skill_ledger.pin_dispatch('task-a', 'run-a', ROUTE)
    old_receipt = skill_ledger.record_card_delivery(
        entry(session_id='provider-session'), repo_root=REPO_ROOT)

    # record_card_delivery() and check() keep today's behaviour.
    assert legacy_check(session_id='provider-session')['status'] == 'VERIFIED'
    assert 'prepared_id' not in ledger.rows()[-1]
    # The old row has no prepared_id, so it never satisfies the gate.
    assert receipt_status(old_receipt, session_id='provider-session')['reason'] == \
        'SKILL_DELIVERY_RECEIPT_MISSING'


def test_old_writer_row_does_not_satisfy_a_real_preparation(ledger):
    prepared_id = prepare()
    skill_ledger.record_card_delivery(entry(), repo_root=REPO_ROOT)

    assert receipt_status(prepared_id)['reason'] == 'SKILL_DELIVERY_RECEIPT_MISSING'


def test_record_still_refuses_controller_delivered(ledger):
    level = dict(BINDING, evidence_level='CONTROLLER_DELIVERED')
    assert refused_code(skill_ledger.record, level) == 'CONTROLLER_DELIVERED_REFUSED'


# --- exemption ----------------------------------------------------------------

def test_not_delivered_status(ledger):
    exemption_id = skill_ledger.record_not_delivered({
        'task_id': 'task-a', 'run_id': 'run-a', 'role': 'developer',
        'route': 'gw-agent', 'reason_code': 'ROUTE_NOT_SUPPORTED'})
    prepare()

    assert skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', 'gw-agent') == {
        'status': 'NOT_DELIVERED', 'reason': None, 'receipt_id': exemption_id,
        'reason_code': 'ROUTE_NOT_SUPPORTED'}
    missing = {'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_EXEMPTION_MISSING'}
    assert skill_ledger.not_delivered_status('task-a', 'run-b', 'developer', 'gw-agent') == missing
    assert skill_ledger.not_delivered_status('task-a', 'run-a', 'reviewer', 'gw-agent') == missing
    # A CARD_PREPARED row for another route is never an exemption.
    assert skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', ROUTE) == missing

    skill_ledger.revoke(exemption_id, 'test revocation')
    assert skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', 'gw-agent') == {
        'status': 'UNVERIFIED', 'reason': 'SKILL_DELIVERY_REVOKED'}


# --- verifiers append nothing ------------------------------------------------

def test_verifiers_append_nothing(ledger):
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
        lambda: skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', 'gw-agent'),
        lambda: skill_ledger.not_delivered_status('task-a', 'run-z', 'developer', 'gw-agent'),
    ]
    for call in calls:
        call()
        call()
        assert ledger.snapshot() == before


def test_verifiers_on_empty_ledger_append_nothing(ledger):
    assert receipt_status('prepared-row-absent')['reason'] == 'SKILL_DELIVERY_RECEIPT_MISSING'
    assert preparation_status('prepared-row-absent')['reason'] == \
        'SKILL_DELIVERY_PREPARATION_MISSING'
    assert skill_ledger.not_delivered_status('task-a', 'run-a', 'developer', 'gw-agent')[
        'reason'] == 'SKILL_DELIVERY_EXEMPTION_MISSING'
    assert not ledger.path.exists()
    assert not ledger.anchor.exists()
