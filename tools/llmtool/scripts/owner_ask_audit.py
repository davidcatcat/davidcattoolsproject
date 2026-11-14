#!/usr/bin/env python3
"""Audit Owner decision requests across GitHub issue comments."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

MARKER = re.compile(r"<!--\s*activity:([^<>]{0,300}?)-->")
OWNER_FIELD = re.compile(
    r"(?:(?<=\s)|(?<=activity:))(type|actor|target|status)="
    r"([A-Za-z0-9_.#-]{1,64})(?![\w.#=-])"
)
REPO_SLUG = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
GATED_LINE = re.compile(r"<!-- owner-ask: v=1 reserved_row=(R-[A-Z]{2,12}) -->")


class AuditError(Exception):
    pass


def _run(argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuditError("command failed") from exc


def repository_slug(explicit: str | None = None) -> str:
    if explicit is not None:
        if not REPO_SLUG.fullmatch(explicit):
            raise AuditError("repository slug is invalid")
        return explicit
    result = _run(["git", "remote", "get-url", "origin"], timeout=10)
    if result.returncode:
        raise AuditError("cannot determine repository from origin remote")
    remote = result.stdout.decode("utf-8", "replace").strip()
    match = re.search(r"(?:github\.com[:/])([^/\s:]+/[^/\s]+?)(?:\.git)?\Z", remote)
    slug = match.group(1) if match else ""
    if not REPO_SLUG.fullmatch(slug):
        raise AuditError("origin remote is not a GitHub repository")
    return slug


def fetch_comments(repo: str, since: str) -> list[dict]:
    result = _run(["gh", "api", "--paginate", "--slurp",
                   f"repos/{repo}/issues/comments?since={since}&per_page=100"])
    if result.returncode:
        raise AuditError("gh failed to read repository data")
    try:
        payload = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError("gh returned invalid JSON") from exc
    if type(payload) is not list or any(type(page) is not list for page in payload):
        raise AuditError("gh returned unexpected JSON")
    return [item for page in payload for item in page]


def _first_line_marker(body: str) -> dict[str, str] | None:
    if not isinstance(body, str):
        return None
    lines = body.splitlines()
    if not lines or not MARKER.fullmatch(lines[0].strip()):
        return None
    fields = {}
    for key, value in OWNER_FIELD.findall(lines[0].strip()):
        fields.setdefault(key, value.rstrip("-"))
    return fields


def _issue_number(url: str) -> int:
    found = re.search(r"/(?:issues|pull)/(\d+)", url) if isinstance(url, str) else None
    return int(found.group(1)) if found else 0


def audit(comments: list[dict], since: str, until: str) -> dict:
    window_comments = [
        c for c in comments
        if isinstance(c, dict) and since <= str(c.get("created_at", "")) < until
    ]
    corrections, candidates, misused = [], [], 0
    for c in window_comments:
        marker = _first_line_marker(c.get("body", ""))
        if not marker:
            continue
        m_type, status = marker.get("type", ""), marker.get("status", "")
        if m_type == "waiting_owner" and status == "ANSWERED":
            misused += 1
        elif (m_type == "waiting_owner" and marker.get("actor") == "PM" and
              marker.get("target") == "Owner" and status in ("WAITING", "BLOCKED", "UNKNOWN")):
            candidates.append(c)
        elif m_type == "audit_correction":
            corrections.append(str(c.get("body", "")))

    counted, corrected = [], 0
    for c in candidates:
        if any(str(c.get("id", "")) in body for body in corrections):
            corrected += 1
        else:
            counted.append(c)

    rows: dict[str, int] = {}
    gated, ungated, items = 0, 0, []
    for c in counted:
        body = c.get("body", "")
        lines = body.splitlines() if isinstance(body, str) else []
        match = GATED_LINE.fullmatch(lines[1]) if len(lines) >= 2 else None
        if match:
            gated += 1
            row = match.group(1)
            rows[row] = rows.get(row, 0) + 1
        else:
            ungated += 1
            row = "UNGATED"
        items.append([c.get("created_at"), _issue_number(c.get("issue_url")), c.get("id"), row])

    items.sort()
    asks = len(counted)
    ratio = (rows.get("R-OTHER", 0) / asks) if asks else 0.0
    return {
        "window": [since, until], "comments": len(window_comments), "asks": asks,
        "misused": misused, "corrected": corrected, "gated": gated, "ungated": ungated,
        "rows": rows, "r_other_ratio": ratio, "r_other_over_limit": ratio > 0.10, "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--repo")
    try:
        args = parser.parse_args(argv)
        repo = repository_slug(args.repo)
        raw = fetch_comments(repo, args.since)
        result = audit(raw, args.since, args.until)
        summary = {k: v for k, v in result.items() if k != "items"}
        print(json.dumps(summary, sort_keys=True))
        for item in result["items"]:
            print("\t".join(str(part) for part in item))
        return 0
    except SystemExit:
        print("owner_ask_audit: invalid arguments", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"owner_ask_audit: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
