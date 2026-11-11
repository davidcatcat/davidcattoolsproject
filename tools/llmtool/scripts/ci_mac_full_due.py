#!/usr/bin/env python3
"""Gate a main FULL Mac run on the SHA it checked out (Owner #1485).

DUE when that SHA has no trusted FULL success (a new or failed SHA); SKIP when it
already passed. A GitHub read failure prints neither and exits 1. The current run
is left out of the lookup, so a GitHub re-run stays possible and keeps its run id.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import mac_full_due  # noqa: E402


def is_due(event, sha, github_get, run_id=None):
    """Return True (DUE) unless sha already has a trusted FULL success."""
    if event != "workflow_dispatch":
        raise ValueError(f"unexpected event {event!r}; expected 'workflow_dispatch'")
    if not mac_full_due.SHA.match(sha):
        raise ValueError("sha must be a full 40-character commit id")
    runs = mac_full_due.full_runs(github_get)
    return mac_full_due.sha_result(github_get, runs, sha, skip_id=run_id) != "PASS"


def _reader(repo):
    env = dict(os.environ, GH_TOKEN=os.environ.get("GH_API_VALUE", ""))

    def get(path):
        result = subprocess.run(["gh", "api", f"repos/{repo}/{path}"], env=env, check=True,
                                capture_output=True, text=True, timeout=60)
        return json.loads(result.stdout)
    return get


def main(argv: list[str] | None = None, github_get=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repository")
    parser.add_argument("--sha", required=True, help="the SHA this run checked out")
    parser.add_argument(
        "--event",
        required=True,
        choices=("workflow_dispatch",),
        help="workflow event; only workflow_dispatch is supported",
    )
    parser.add_argument("--run-id", type=int, help="this run's id, left out of the lookup")
    args = parser.parse_args(argv)
    try:
        due = is_due(args.event, args.sha,
                     _reader(args.repo) if github_get is None else github_get, args.run_id)
    except mac_full_due.StateError:
        print("ERROR: main FULL state unavailable", file=sys.stderr)
        return 1
    print("DUE" if due else "SKIP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
