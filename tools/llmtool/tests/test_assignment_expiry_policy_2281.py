"""#2281 row 18 (basis #819): the inbox default Developer assignment expiry.

The row 18 policy key ``developer_assignment_expiry_seconds`` was merged in
PR #2479, but the Developer assignment inbox still kept its own hard-coded
expiry; an assignment that uses the default now follows the policy key:
A1: with the repository tools/llmtool/policy.json (key absent) the inbox's
    default assignment expiry is unchanged: an ``assign`` without an explicit
    ``expires_in`` expires ``DEFAULT_EXPIRY_SECONDS`` (600) after
    ``assigned_at``, and the loader default equals it.
A2: a policy that sets the key to another allowed value: an assignment that
    uses the default expiry expires the configured seconds after
    ``assigned_at``, not after 600.
A3: a configured value above the inbox's allowed expiry maximum, or an
    invalid policy, ends in a clear refusal (ASSIGNMENT_EXPIRY_INVALID or
    POLICY_INVALID); nothing falls back silently to the old hard-coded
    value, and a refusal writes nothing.
A4: a caller that passes an explicit expiry keeps that value (even when the
    policy is unreadable); an explicit expiry above the maximum is still
    refused.
The dispatch gate and the D6 registration are injected in-process (no git,
no D6 registry, no credentials) and the policy is given through a temporary
policy file; nothing here touches the network.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from llmtool import repair_policy as rp
from llmtool.developer_assignment_inbox import (AssignmentRefused,
                                                DEFAULT_EXPIRY_SECONDS,
                                                assign)
from llmtool.errors import LLMToolError
from llmtool.task_store import ASSIGNMENT_EXPIRY_MAX_SECONDS

# The repository limits before #2281 L0b (#2367 values); the row 18 key is
# optional and omitted, exactly as in the repository policy.json.
BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}
# #2281 row 18 (basis #819): Developer assignment expiry in seconds.
ROW18_KEY = 'developer_assignment_expiry_seconds'
# The repository policy.json (A1), read in-process; no network.
REPO_POLICY = Path(__file__).resolve().parents[1] / 'policy.json'
_NOW = datetime(2024, 5, 1, tzinfo=timezone.utc)
_STAMP = '%Y-%m-%dT%H:%M:%SZ'


def _parse(stamp):
    return datetime.strptime(stamp, _STAMP).replace(tzinfo=timezone.utc)


class _Registration:
    """The registered D6 the assign gate reads (ACTIVE, at its HEAD)."""

    root = '/tmp/d6-root'
    result_sha = '0' * 40


class _Store:
    """A recording task store: the assign writes one ASSIGNED record."""

    def __init__(self):
        self.records = []

    def record_developer_assignment(self, *, worker, d6_task_id, common_dir,
                                    pm_session_id, task_id, owner_assignment_ref,
                                    assigned_at, expires_at, gate):
        # ``assign``'s gate raises on denial and returns one payload dict on
        # success (the (entry, denied) tuple protocol is ``claim``'s).
        gate()
        record = {'worker': worker, 'd6_task_id': d6_task_id,
                  'assigned_at': assigned_at, 'expires_at': expires_at,
                  'state': 'ASSIGNED'}
        self.records.append(record)
        return 'assign-%d' % len(self.records)


def _gates():
    """Allow the dispatch gate and the D6 registration in-process; the real
    ``assign`` flow still runs (expiry resolution, validation, stamping)."""
    entry = {'decision': 'ALLOWED', 'reason_code': None,
             'policy_sha256': None}
    return (mock.patch('llmtool.developer_assignment_inbox._decision',
                       return_value=(entry, None)),
            mock.patch('llmtool.developer_assignment_inbox._registration',
                       return_value=_Registration()))


class AssignmentExpiryPolicyTest(unittest.TestCase):
    """A1-A4 for the inbox default assignment expiry (#2281 row 18)."""

    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    def _expiry_seconds(self, policy_path, expires_in=None):
        """The seconds an assignment that uses the expiry waits past
        ``assigned_at`` (the record the refusal-free assign wrote)."""
        store = _Store()
        first, second = _gates()
        with first, second:
            assign(store, worker='w', d6_task_id='D6-1', common_dir='/tmp/git',
                   pm_session_key='pm', policy_path=policy_path, now=_NOW,
                   expires_in=expires_in)
        self.assertEqual(len(store.records), 1)
        record = store.records[0]
        return (_parse(record['expires_at'])
                - _parse(record['assigned_at'])).total_seconds()

    def _assign_refused(self, policy_path, expires_in=None):
        """Run a refusal-expected assign; the store must stay empty."""
        store = _Store()
        first, second = _gates()
        with first, second:
            with self.assertRaises(AssignmentRefused) as caught:
                assign(store, worker='w', d6_task_id='D6-1',
                       common_dir='/tmp/git', pm_session_key='pm',
                       policy_path=policy_path, now=_NOW,
                       expires_in=expires_in)
        self.assertEqual(store.records, [])
        return caught.exception

    # A1
    def test_absent_key_keeps_the_inbox_default_expiry(self):
        # The repository policy.json omits the row 18 key...
        with open(REPO_POLICY, 'rb') as stream:
            self.assertNotIn(ROW18_KEY,
                             json.loads(stream.read().decode('utf-8')))
        # ...so the loader default is the unchanged hard-coded expiry, and an
        # assignment that uses the default waits exactly DEFAULT_EXPIRY_SECONDS.
        self.assertEqual(rp.DEVELOPER_ASSIGNMENT_EXPIRY_SECONDS,
                         DEFAULT_EXPIRY_SECONDS)
        self.assertEqual(rp.assignment_expiry_seconds(REPO_POLICY),
                         DEFAULT_EXPIRY_SECONDS)
        self.assertEqual(self._expiry_seconds(self._write(BASE)),
                         DEFAULT_EXPIRY_SECONDS)

    # A2
    def test_configured_value_moves_the_default_expiry(self):
        for value in (1, 120, ASSIGNMENT_EXPIRY_MAX_SECONDS):
            self.assertEqual(
                self._expiry_seconds(self._write(dict(BASE,
                                                      **{ROW18_KEY: value}))),
                value)
        # 120, not the old 600: the default followed the policy.
        self.assertNotEqual(
            self._expiry_seconds(self._write(dict(BASE, **{ROW18_KEY: 120}))),
            DEFAULT_EXPIRY_SECONDS)

    # A3
    def test_value_above_the_maximum_refuses(self):
        path = self._write(dict(BASE,
                                **{ROW18_KEY: ASSIGNMENT_EXPIRY_MAX_SECONDS + 1}))
        exc = self._assign_refused(path)
        self.assertEqual(exc.assignment_code, 'ASSIGNMENT_EXPIRY_INVALID')

    def test_invalid_policy_refuses(self):
        # A configured value that is not a positive whole number...
        bad = dict(BASE, **{ROW18_KEY: 0})
        with self.assertRaises(LLMToolError) as caught:
            self._assign_refused_bad(bad)
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')
        # ...as is a policy that misses a required key; neither falls back
        # to the old hard-coded expiry.
        missing = dict(BASE)
        del missing['review_fail_max']
        with self.assertRaises(LLMToolError) as caught:
            self._assign_refused_bad(dict(missing, **{ROW18_KEY: 300}))
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    def _assign_refused_bad(self, policy):
        store = _Store()
        path = self._write(policy)
        first, second = _gates()
        with first, second:
            with self.assertRaises(LLMToolError):
                assign(store, worker='w', d6_task_id='D6-1',
                       common_dir='/tmp/git', pm_session_key='pm',
                       policy_path=path, now=_NOW)
        self.assertEqual(store.records, [])

    # A4
    def test_explicit_expiry_keeps_its_value(self):
        path = self._write(dict(BASE, **{ROW18_KEY: 300}))
        self.assertEqual(self._expiry_seconds(path, expires_in=120), 120)
        # Even with an unreadable policy: the explicit expiry is the caller's.
        self.assertEqual(self._expiry_seconds(self._write('not a policy'),
                                              expires_in=120), 120)
        # An explicit expiry above the inbox maximum is still refused.
        exc = self._assign_refused(path, expires_in=ASSIGNMENT_EXPIRY_MAX_SECONDS + 1)
        self.assertEqual(exc.assignment_code, 'ASSIGNMENT_EXPIRY_INVALID')


if __name__ == '__main__':
    unittest.main()
