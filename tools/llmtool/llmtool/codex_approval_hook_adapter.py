"""Task-scoped native command-hook candidate; no hook installation or execution.

Trusted hook definitions pin policy bytes, expected session and event. This
adapter never grants approval, spawns commands, or attests native coverage.
"""
import argparse
import hashlib
import json
import os
import sys
import time

from . import codex_approval_audit as audit

INPUT_SECONDS = 5.0
EVENTS = audit.TOOL_EVENTS + audit.END_EVENTS
DENY = {'hookSpecificOutput': {
    'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
    'permissionDecisionReason': 'Task audit policy denied this action.'}}


def read_input(fd=None):
    """Reuse the collector's capped, deadline-bound EOF reader."""
    raw = audit._read_pipe(sys.stdin.fileno() if fd is None else fd,
                           audit.MAX_EVENT_BYTES + 1, time.monotonic() + INPUT_SECONDS)
    if raw is None:
        raise audit.AuditError('hook input incomplete')
    return raw


def _policy(path, expected_digest):
    if (not os.path.isabs(path) or os.path.realpath(path) != path
            or not audit._DIGEST.fullmatch(expected_digest)):
        raise audit.AuditError('hook policy invalid')
    audit._check_private(os.stat(os.path.dirname(path)), True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        audit._check_private(os.fstat(fd), False)
        raw = audit._read_limited(fd, audit.MAX_POLICY_BYTES)
    finally:
        os.close(fd)
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise audit.AuditError('hook policy changed')
    policy = audit._policy_data(json.loads(raw.decode('utf-8')))
    if any(not p or p.startswith('/') or any(x in ('', '.', '..') for x in p.split('/'))
           for p in policy.allowed_paths):
        raise audit.AuditError('hook scope invalid')
    return policy


def handle(raw, *, event, session, store, policy):
    """Record first, then return deny only for existing hard no-go rules."""
    audit._check_policy(policy)
    forbidden = (policy.codex_root,) + tuple(policy.protected_roots)
    body, problem = audit._parse_event(raw)
    if problem:
        audit.record_event(store, raw, forbidden=forbidden)
        raise audit.AuditError('hook event invalid')
    if (event not in EVENTS or body['hook_event_name'] != event
            or body['session_id'] != session or not audit._ID.fullmatch(session)):
        raise audit.AuditError('hook binding invalid')
    decision = None
    if event == 'PreToolUse':
        classification = audit.classify(body['tool_name'], body['tool_input'], body['cwd'], policy)[1]
        if classification == audit.UNAUTHORIZED:
            decision = 'deny'
    audit.record_event(store, raw, decision=decision, forbidden=forbidden)
    return DENY if decision else None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise audit.AuditError('hook arguments invalid')


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--event', required=True, choices=EVENTS)
    parser.add_argument('--session', required=True)
    parser.add_argument('--store', required=True)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--policy-sha256', required=True)
    event = policy = None
    try:
        args = parser.parse_args(argv)
        event = args.event
        policy = _policy(args.policy, args.policy_sha256)
        response = handle(read_input(), event=event, session=args.session,
                          store=args.store, policy=policy)
    except Exception:
        if policy is not None:
            try:
                # Empty malformed record is a private failure marker, never a
                # fabricated native event or a successful denial observation.
                audit.record_event(args.store, b'', forbidden=(policy.codex_root,) +
                                   tuple(policy.protected_roots))
            except Exception:
                pass  # Broken stores remain incomplete; no unsafe fallback.
        # Never echo raw args, input, exception text or paths. Hook errors may
        # fail open, so a known PreToolUse emits a supported deny with exit 0.
        sys.stderr.write('codex-approval-hook: incomplete\n')
        if event != 'PreToolUse':
            return 1
        response = DENY
    if response is not None:
        sys.stdout.write(json.dumps(response, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
