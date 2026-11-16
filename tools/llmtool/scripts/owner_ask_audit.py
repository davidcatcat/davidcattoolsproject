#!/usr/bin/env python3
"""Audit Owner decision requests across GitHub issue comments."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

MARKER = re.compile(r"<!--\s*activity:([^<>]{0,300}?)-->")
OWNER_FIELD = re.compile(
    r"(?:(?<=\s)|(?<=activity:))(type|actor|target|status)="
    r"([A-Za-z0-9_.#-]{1,64})(?![\w.#=-])"
)
REPO_SLUG = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
GATED_LINE = re.compile(r"<!-- owner-ask: v=1 reserved_row=(R-[A-Z]{2,12}) -->")
# #1943 P0: bounded day-window fetch, ask classes and the PM chat ask source.
DAY = 86400
PAGE_TIMEOUT = 30
MAX_PAGES_PER_DAY = 50
WAIT_HORIZON = 7 * DAY
NOTICE_STATUSES = ("NOTICE", "ANSWERED")
# PM chat ask prefix (ni xu yao zuo + fullwidth colon) and the nothing-to-do text.
CHAT_ASK = "".join(map(chr, (0x4F60, 0x9700, 0x8981, 0x505A, 0xFF1A)))
CHAT_NOTHING = "".join(map(chr, (0x76EE, 0x524D, 0x6C92, 0x6709)))
CHAT_WINDOW = 3600
MAX_TRANSCRIPT_FILES = 2000
MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
NOT_COUNTED = ("claude_web_chat", "sub_pm_relay_chat")



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


def _fetch_page(repo: str, start: str, page: int, timeout: int) -> list[dict]:
    """#1943 P0: one page of issue comments updated since start, oldest created first."""
    result = _run(["gh", "api",
                   f"repos/{repo}/issues/comments?since={start}&sort=created&direction=asc"
                   f"&per_page=100&page={page}"], timeout=timeout)
    if result.returncode:
        raise AuditError("gh failed to read repository data")
    try:
        payload = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError("gh returned invalid JSON") from exc
    if type(payload) is not list or any(type(item) is not dict for item in payload):
        raise AuditError("gh returned unexpected JSON")
    return payload


def _stamp(seconds: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_comments(repo: str, since: str, until: str) -> list[dict]:
    """#1943 P0: comments created from since on, one UTC day window at a time.

    Each gh call reads one page with its own timeout, so a 7-day range is many short
    calls instead of one long paginate. The range runs WAIT_HORIZON past until, but
    never past now, so later answers still count for owner_wait. Deduped by
    (issue, comment id).
    """
    start, end = _seconds(since), _seconds(until)
    if start is None or end is None or end <= start:
        raise AuditError("window is invalid")
    end = min(end + WAIT_HORIZON, int(time.time()))
    seen, comments, day = set(), [], start
    while day < end:
        day_end = min(day + DAY, end)
        for page in range(1, MAX_PAGES_PER_DAY + 1):
            items = _fetch_page(repo, _stamp(day), page, PAGE_TIMEOUT)
            for item in items:
                key = (_issue_number(item.get("issue_url")), item.get("id"))
                created = _seconds(item.get("created_at"))
                if key not in seen and created is not None and day <= created < day_end:
                    seen.add(key)
                    comments.append(item)
            last = _seconds(items[-1].get("created_at")) if items else None
            if len(items) < 100 or last is None or last >= day_end:
                break
        else:
            raise AuditError("too many comments in one day")
        day = day_end
    return comments


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


def audit(comments: list[dict], since: str, until: str, chat=None, effective_tasks=None) -> dict:
    comments = _unique(comments)
    window_comments = [
        c for c in comments
        if isinstance(c, dict) and since <= str(c.get("created_at", "")) < until
    ]
    corrections, candidates, misused, notices = [], [], 0, 0
    for c in window_comments:
        marker = _first_line_marker(c.get("body", ""))
        if not marker:
            continue
        m_type, status = marker.get("type", ""), marker.get("status", "")
        if m_type == "waiting_owner" and status == "ANSWERED":
            misused += 1
        elif (marker.get("actor") == "PM" and
              marker.get("target") == "Owner" and status in ("WAITING", "BLOCKED", "UNKNOWN")):
            candidates.append(c)
        elif marker.get("target") == "Owner" and status in NOTICE_STATUSES:
            notices += 1
        elif m_type == "audit_correction":
            corrections.append(str(c.get("body", "")))

    counted, corrected, keys, duplicates = [], 0, set(), 0
    for c in sorted(candidates, key=lambda item: str(item.get("created_at", ""))):
        if any(str(c.get("id", "")) in body for body in corrections):
            corrected += 1
        elif _ask_key(c) in keys:
            duplicates += 1
        else:
            keys.add(_ask_key(c))
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
        "owner_wait": owner_wait(comments, counted), "notices": notices,
        "duplicates": duplicates, "chat": chat_summary(chat, counted),
        "metrics": metrics(counted, gated, ungated, rows, chat, effective_tasks),
    }


def _seconds(value) -> int | None:
    from datetime import datetime, timezone
    try:
        return int(datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def owner_wait(comments: list[dict], asks: list[dict]) -> dict:
    """#1943: Owner wait per ask, from the ask to the next ANSWERED record on its Issue."""
    answers = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        marker = _first_line_marker(c.get("body", ""))
        if (marker and marker.get("target") == "Owner" and marker.get("status") == "ANSWERED"
                and marker.get("type") != "waiting_owner"):
            when = _seconds(c.get("created_at"))
            if when is not None:
                answers.append((_issue_number(c.get("issue_url")), when))
    waits, unanswered = [], 0
    for ask in asks:
        number, start = _issue_number(ask.get("issue_url")), _seconds(ask.get("created_at"))
        later = [when for issue, when in answers
                 if start is not None and issue == number and when >= start]
        if later:
            waits.append(min(later) - start)
        else:
            unanswered += 1
    total = sum(waits)
    return {"answered": len(waits), "unanswered": unanswered, "total_seconds": total,
            "mean_seconds": (total / len(waits)) if waits else None}


def _unique(comments: list[dict]) -> list[dict]:
    """#1943 P0: one copy of each comment, by (issue, comment id)."""
    seen, unique = set(), []
    for c in comments:
        key = (_issue_number(c.get("issue_url")), c.get("id")) if isinstance(c, dict) else None
        if key is None or key not in seen:
            unique.append(c)
        if key is not None:
            seen.add(key)
    return unique


def _ask_key(comment: dict) -> tuple:
    """#1943 P0: one Owner ask per Issue per clock hour, across markers and entries."""
    return (_issue_number(comment.get("issue_url")), str(comment.get("created_at", ""))[:13])


def _chat_line_asks(raw: str, start: int, end: int) -> list[int]:
    try:
        entry = json.loads(raw)
    except (ValueError, RecursionError):
        return []
    message = entry.get("message") if type(entry) is dict else None
    if type(message) is not dict or entry.get("type") != "assistant":
        return []
    when = _seconds(str(entry.get("timestamp", ""))[:19] + "Z")
    content = message.get("content")
    if when is None or not start <= when < end or type(content) is not list:
        return []
    texts = [part.get("text") for part in content if type(part) is dict]
    lines = [line.strip() for text in texts if type(text) is str for line in text.splitlines()]
    asks = [line for line in lines if line.startswith(CHAT_ASK) and CHAT_NOTHING not in line]
    return [when] * len(asks)


def chat_asks(directory: str, since: str, until: str) -> list[int] | None:
    """#1943 P0: PM chat lines that ask the Owner, from local Claude Code transcripts.

    Returns the sorted epoch seconds of assistant text lines that start with CHAT_ASK
    and do not say CHAT_NOTHING. Returns None (UNKNOWN, never zero) when the source
    cannot be read in full. Only counts and times leave this function, never text.
    """
    from pathlib import Path
    start, end, root = _seconds(since), _seconds(until), Path(directory)
    if start is None or end is None or not root.is_dir():
        return None
    try:
        files = sorted(root.rglob("*.jsonl"))
        if len(files) > MAX_TRANSCRIPT_FILES:
            return None
        found = []
        for path in files:
            if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
                return None
            with path.open(encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    found.extend(_chat_line_asks(raw, start, end))
    except OSError:
        return None
    return sorted(found)


def chat_summary(chat: list[int] | None, counted: list[dict]) -> dict:
    """#1943 P0: chat asks with or without a GitHub ask marker within CHAT_WINDOW."""
    if chat is None:
        return {"status": "UNKNOWN"}
    marked = [when for when in map(_seconds, (c.get("created_at") for c in counted)) if when]
    near = [when for when in chat if any(abs(mark - when) <= CHAT_WINDOW for mark in marked)]
    result = {"status": "COUNTED", "asks": len(chat), "with_marker": len(near)}
    result["without_marker"] = len(chat) - len(near)
    return result


def metrics(counted, gated, ungated, rows, chat, effective_tasks) -> dict:
    """#1943 P0 metrics. A value that needs a missing source is UNKNOWN, never zero.

    owner_decisions counts GitHub asks plus chat asks without a marker. Gate coverage
    is over the GitHub entry, which llmtool controls. Invalid escalations are asks
    without a reserved row. Owner wait time is the separate owner_wait block.
    """
    chat_only = chat_summary(chat, counted).get("without_marker", 0)
    decisions = len(counted) + chat_only
    result = {"owner_decisions": decisions, "complete": chat is not None}
    result["gate_coverage"] = round(gated / len(counted), 4) if counted else None
    invalid = ungated + chat_only
    result["invalid_escalation_rate"] = round(invalid / decisions, 4) if decisions else None
    result["true_reserved"] = sum(n for row, n in rows.items() if row != "R-OTHER")
    per_task = "UNKNOWN"
    if type(effective_tasks) is int and effective_tasks > 0:
        per_task = round(decisions * 100 / effective_tasks, 2)
    result["per_100_effective_tasks"] = per_task
    not_counted = [{"entry": name, "count": "UNKNOWN"} for name in NOT_COUNTED]
    if chat is None:
        not_counted.insert(0, {"entry": "pm_chat", "count": "UNKNOWN"})
    result["not_counted"] = not_counted
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--chat-transcripts")
    parser.add_argument("--effective-tasks", type=int)
    try:
        args = parser.parse_args(argv)
        repo = repository_slug(args.repo)
        raw = fetch_comments(repo, args.since, args.until)
        chat = None
        if args.chat_transcripts:
            chat = chat_asks(args.chat_transcripts, args.since, args.until)
        result = audit(raw, args.since, args.until, chat, args.effective_tasks)
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
