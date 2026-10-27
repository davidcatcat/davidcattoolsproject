#!/usr/bin/env python3
"""Decide whether the scheduled full Mac suite needs to run for main."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.request import Request, urlopen


WORKFLOW = "llmtool-mac-ci.yml"
API_ROOT = "https://api.github.com"
TIMEOUT_SECONDS = 20


def latest_successful_sha(repo: str, token: str) -> str | None:
    """Return the newest successful schedule/dispatch head SHA, if available."""
    url = (
        f"{API_ROOT}/repos/{repo}/actions/workflows/{WORKFLOW}/runs"
        "?branch=main&per_page=100"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return latest_successful_sha_from_payload(payload)


def latest_successful_sha_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        raise ValueError("Actions API response is not an object")
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("Actions API response has no workflow_runs list")
    candidates = [
        run for run in runs
        if isinstance(run, dict)
        and run.get("event") in {"schedule", "workflow_dispatch"}
        and run.get("head_branch") == "main"
        and run.get("conclusion") == "success"
        and isinstance(run.get("head_sha"), str)
    ]
    if not candidates:
        return None
    newest = max(candidates, key=lambda run: str(run.get("created_at", "")))
    return newest["head_sha"]


def is_due(
    repo: str,
    current_sha: str,
    token: str | None,
    event: str,
    fetch_latest=latest_successful_sha,
) -> bool:
    if event == "workflow_dispatch":
        return True
    if event != "schedule" or not re.fullmatch(r"[0-9a-f]{40}", current_sha):
        return True
    if not token:
        return True
    try:
        previous_sha = fetch_latest(repo, token)
    except Exception:
        return True
    return previous_sha is None or previous_sha != current_sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repository")
    parser.add_argument("--sha", required=True, help="current main commit SHA")
    parser.add_argument("--event", required=True, choices=("schedule", "workflow_dispatch"))
    args = parser.parse_args(argv)
    print("DUE" if is_due(args.repo, args.sha, os.environ.get("GITHUB_TOKEN"), args.event) else "SKIP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
