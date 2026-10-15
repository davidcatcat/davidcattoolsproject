#!/usr/bin/env -S python3 -IB
"""Foreground structure-only census; pin DATA never grants private reads.

Future canonical invocation (NOT live authorization):
  <root>/tools/llmtool/.venv/bin/python -B -I <this-file> \
    --pin-file <private-0600-file> --pin-sha256 <sha256> --json

No defaults, environment overrides, queue/RPC/model calls or child processes.
An external 60s envelope must prove actual exit, including blocked stdout.
Production roots are fixed in the module; no CLI test bypass exists.
"""
from llmtool.rollout_census import main

if __name__ == '__main__':
    raise SystemExit(main())
