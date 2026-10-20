"""Notification policy/ledger fixtures with no transport or native runtime."""

import tempfile
from pathlib import Path

from llmtool.action_contract import TaskBinding
from llmtool import authorization_ledger as ledger
from llmtool import authorization_policy as policy


class NotificationIssuer(policy.HostIssuer):
    """Test-only vouching; never registers or starts an Agent Session."""
    issuer_class = policy.IssuerClass.NOTIFICATION
    capability_id = 'notification-test-host'

    def __init__(self, binding):
        self.binding = binding
        self.revoked = False

    def vouches_for(self, digest):
        return not self.revoked and digest == self.binding.digest


class NotificationFixture:
    def __init__(self, *, audit_only=False):
        self.tmp = tempfile.TemporaryDirectory(prefix='notification-fixture-')
        self.path = str(Path(self.tmp.name) / 'notification.sqlite')
        self.ledger = ledger.ReceiptLedger.create(
            self.path, clock=self.clock, kind='NOTIFICATION',
            notification_layout=2 if audit_only else 1)
        self.binding = policy.NotificationBinding(
            task=TaskBinding('notification-ready', '1'*32, 1, '2'*64),
            acceptance_task=TaskBinding('notification-final', '3'*32, 1, '4'*64),
            source_sha='1'*40, base_sha='2'*40, tree_sha='3'*40,
            packet_digest='1'*64, host_pin='2'*64, route_digest='3'*64,
            scope_digest='4'*64, authors_digest='5'*64, reviewer_digest='6'*64,
            author_session='writer-session', reviewer_session='reviewer-session',
            epoch=7, sequence=4, event_id='event-4', source_ref='source-reference',
            actor_ref='approved-actor', grant_digest='a'*64,
            capability_id=NotificationIssuer.capability_id, attempt_id='a'*32,
            ledger_nonce=self.ledger.creation_nonce,
            not_before=self.clock()-1, expires_at=self.clock()+240,
            tool_mode='AUDIT_ONLY_TRIAL' if audit_only else 'ENFORCED_NO_TOOLS',
            risk_acceptance_digest=('d' if audit_only else '0')*64,
            coverage_digest=('e' if audit_only else '0')*64)
        self.issuer = NotificationIssuer(self.binding)

    @staticmethod
    def clock():
        return 1760313600

    def close(self):
        self.ledger.close()
        self.tmp.cleanup()
