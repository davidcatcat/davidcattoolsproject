#!/usr/bin/env -S python3 -IB
"""Foreground structure-only census; pin DATA never grants private reads.

Future canonical invocation (NOT live authorization):
  <root>/tools/llmtool/.venv/bin/python -B -I <this-file> \
    --pin-file <private-0600-file> --pin-sha256 <sha256> --json

No defaults, environment overrides, queue/RPC/model calls or child processes.
An external 60s envelope must prove actual exit, including blocked stdout.
Production roots are fixed in the module; no CLI test bypass exists.
"""
import sys

try:
    # Reject unsafe flags before importing dependencies (and possible pyc writes).
    if sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1:
        raise RuntimeError("RUNTIME_MISMATCH")
    from llmtool.rollout_census import main
except BaseException:
    # Import failures must not expose private paths/tracebacks. This literal
    # fallback works even when the census/decoder dependency cannot import.
    try:
        sys.stdout.write('{"schema_version":1,"kind":"NATIVE_CENSUS_STRUCTURE_ONLY",'
                         '"status":"BLOCKED","reasons":["RUNTIME_MISMATCH"]}\n')
        sys.stdout.flush()
    except BaseException:
        pass
    raise SystemExit(1)

if __name__ == '__main__':
    raise SystemExit(main())
