"""Closed synchronous test host. Never imported by product entrypoints.

The only extra process option is the contract's explicit FD-inheritance stress.
No callbacks, providers, native workers, custom reapers or command input. The
manifest is pinned before the critical window. This is engineering fixture
binding, not approval issuance or durable product audit authorization.
"""
import argparse
from dataclasses import asdict
import os
from pathlib import Path
import select
import signal
import sys

from llmtool import _git_containment as harness
from llmtool import _git_containment_protocol as wire
from llmtool._git_process_identity import NotSupported


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=sorted(wire.CASES), default='exit')
    parser.add_argument('--hold', action='store_true')
    parser.add_argument('--popen', action='store_true')
    parser.add_argument('--timing', choices=('ordinary', 'pre_pipe', 'spawn', 'cleanup'),
                        default='ordinary')
    parser.add_argument('--timeout', type=float, default=2)
    args = parser.parse_args()
    package = Path(wire.__file__).resolve().parent
    pinned = {name: wire.file_hash(package / name) for name in wire.ARTIFACTS}
    host = harness._Host(pinned, wire.file_hash(Path(__file__)))
    previous = {number: signal.getsignal(number)
                for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        outcome = harness._run(host, args.case, args.timeout,
            hold_terminal=args.hold, inheritance_stress=args.popen, timing=args.timing)
        restored = all(signal.getsignal(number) == handler for number, handler in previous.items())
        harness._event('handlers_restored', restored=restored)
        harness._event('result', **asdict(outcome))
        return 0 if outcome.containment_confirmed and restored else 79
    except NotSupported:
        harness._event('unsupported')
        return 78
    except harness.ContainmentUnresolved as exc:
        anchor = exc.ownership['anchor']
        harness._event('unresolved', primary=exc.primary, outcome='state-unknown',
            user_action_required=True, containment_confirmed=False,
            supervisor=anchor.bound.data() if anchor else {},
            child=exc.ownership['child'],
            diagnostics=exc.diagnostics,
            cleanup_faults=exc.ownership.get('cleanup_faults', []) +
                          (anchor.signal_faults if anchor else []))
        # F1 deliberately retains handlers/FDs/unreaped ownership. The outer
        # operator owns resolution. No regular return, guessed group kill,
        # retry or periodic spawning is permitted from this unresolved state.
        while True:
            select.select([], [], [], 1)


if __name__ == '__main__':
    raise SystemExit(main())
