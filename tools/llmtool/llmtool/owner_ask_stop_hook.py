#!/usr/bin/env python3
"""Claude Code Stop hook for the Owner Ask Output Gate (#2285 Phase B).

Checks the PM's final assistant reply (`last_assistant_message`) against the
structured Owner Ask record (`pm_owner_marker.py ask`). Runs in observe mode
by default (logs `UNGATED_ALERTED`, never blocks). In enforce mode, blocks at
most once per reply hash / turn so the PM can handle the matter under its own
authority or post a real `pm_owner_marker.py ask` and restate the ask with its
URL.

Scope limit: natural-language Owner decisions without a detectable ask line
are not gated here and remain covered by `owner_ask_audit.py --chat-transcripts`.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable


def _import_scripts():
    """Import sibling scripts whether invoked from repo root or tools/llmtool."""
    try:
        from scripts import owner_ask_audit, pm_owner_marker
        return owner_ask_audit, pm_owner_marker
    except ImportError:
        try:
            from llmtool.scripts import owner_ask_audit, pm_owner_marker
            return owner_ask_audit, pm_owner_marker
        except ImportError:
            pkg_dir = Path(__file__).resolve().parent.parent
            if str(pkg_dir) not in sys.path:
                sys.path.insert(0, str(pkg_dir))
            from scripts import owner_ask_audit, pm_owner_marker
            return owner_ask_audit, pm_owner_marker


owner_ask_audit, pm_owner_marker = _import_scripts()

DEFAULT_LOG_PATH = Path.home() / ".local" / "state" / "llmtool" / "owner-ask-gate.jsonl"
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
VALID_MODES = frozenset({MODE_OBSERVE, MODE_ENFORCE})
MAX_LOG_LINE_BYTES = 65536
FETCH_TIMEOUT_SECONDS = 15

ISSUE_COMMENT_URL_CAPTURE = re.compile(
    r"(?<![A-Za-z0-9_])https://github\.com/"
    r"([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})"
    r"/issues/([1-9][0-9]{0,6})#issuecomment-([1-9][0-9]{0,19})(?![A-Za-z0-9_])"
)
ISSUE_API_URL_RE = re.compile(
    r"https://api\.github\.com/repos/"
    r"([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})/issues/([1-9][0-9]{0,6})\Z"
)
ISSUE_REF_RE = re.compile(r"(?<![A-Za-z0-9_&#])#([1-9][0-9]{0,6})(?![A-Za-z0-9_])")
OWNER_ADDRESS_RE = re.compile(r"(?:\bowner\b|\u4e3b\u4eba)", re.IGNORECASE)
LETTERED_OPTION_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\(([A-Za-z])\)|([A-Za-z])[.)])\s+\S",
    re.MULTILINE,
)
RESERVED_ROW_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    + "|".join(re.escape(row) for row in pm_owner_marker.RESERVED_ROWS)
    + r")(?![A-Za-z0-9_-])"
)

BLOCK_REASON = (
    "Owner Ask Output Gate: this reply appears to ask the Owner without a valid "
    "Owner Ask record. Either handle the matter under PM authority (CI FAIL means "
    "repair; repair caps exhausted means D3 REPLAN/DROP) or post a real "
    "`pm_owner_marker.py ask` and restate the ask with its comment URL."
)


def _reply_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _utc_iso(ts: float | None = None) -> str:
    when = time.time() if ts is None else ts
    return datetime.fromtimestamp(when, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_seconds(value: Any) -> int | None:
    """Parse an ISO-8601 UTC/offset timestamp into integer epoch seconds."""
    if type(value) is not str or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return int(dt.timestamp())


def _resolve_mode(mode: str | None) -> str:
    raw = os.environ.get("LLMTOOL_OWNER_ASK_GATE_MODE", MODE_OBSERVE) if mode is None else mode
    if type(raw) is str and raw.strip().lower() == MODE_ENFORCE:
        return MODE_ENFORCE
    return MODE_OBSERVE


def _resolve_log_path(log_path: str | Path | None) -> Path:
    if log_path is not None:
        return Path(log_path)
    env_path = os.environ.get("LLMTOOL_OWNER_ASK_GATE_LOG", "").strip()
    if env_path:
        return Path(env_path)
    return DEFAULT_LOG_PATH


def detect_ask(text: str) -> bool:
    """Cheap structural screen for a likely Owner-decision ask in a reply.

    Detection only decides whether to verify a marker; it never passes or fails
    a reply by keyword alone.
    """
    if not text:
        return False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(owner_ask_audit.CHAT_ASK) and owner_ask_audit.CHAT_NOTHING not in line:
            return True
    if OWNER_ADDRESS_RE.search(text):
        letters = {m.group(1) or m.group(2) for m in LETTERED_OPTION_RE.finditer(text)}
        if len({ch.upper() for ch in letters}) >= 2:
            return True
    if RESERVED_ROW_RE.search(text):
        return True
    return False


def _is_tool_result_user_entry(entry: dict[str, Any]) -> bool:
    message = entry.get("message")
    if type(message) is not dict:
        return False
    content = message.get("content")
    if type(content) is not list or not content:
        return False
    return all(type(part) is dict and part.get("type") == "tool_result" for part in content)


def transcript_turn_start(transcript_path: str | Path | None) -> int | None:
    """Return epoch seconds of the first assistant entry in the current turn.

    Validates timestamps on every record before skipping non-user/assistant
    record types. Returns None (`ORDER_UNKNOWN`) if the transcript is missing,
    unreadable, malformed, or any record lacks a valid timestamp.
    """
    if not transcript_path:
        return None
    path = Path(transcript_path)
    try:
        if not path.is_file():
            return None
        turn_start: int | None = None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except (ValueError, RecursionError):
                    return None
                if type(entry) is not dict:
                    return None
                ts = _parse_iso_seconds(entry.get("timestamp"))
                if ts is None:
                    return None
                kind = entry.get("type")
                if kind == "user":
                    if not _is_tool_result_user_entry(entry):
                        turn_start = None
                elif kind == "assistant":
                    if turn_start is None:
                        turn_start = ts
        return turn_start
    except OSError:
        return None


def default_fetcher(repo: str, issue_number: int, _comment_id: int) -> list[dict[str, Any]]:
    """Fetch comments on the cited issue via `gh api`."""
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/issues/{issue_number}/comments?per_page=100",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=FETCH_TIMEOUT_SECONDS,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError("gh api failed")
    payload = json.loads(result.stdout)
    if type(payload) is not list:
        raise RuntimeError("unexpected gh payload")
    return payload


def _valid_comment_issue(comment: dict[str, Any], expected_repo: str, expected_issue: int) -> bool:
    issue_url = comment.get("issue_url")
    if type(issue_url) is not str:
        return False
    match = ISSUE_API_URL_RE.fullmatch(issue_url)
    if not match:
        return False
    return match.group(1) == expected_repo and int(match.group(2)) == expected_issue


def _parse_leading_markers(body: str) -> list[dict[str, str]]:
    """Parse consecutive leading activity markers (up to 4 lines), mirroring pm_owner_marker."""
    markers: list[dict[str, str]] = []
    for line in body.splitlines()[:4]:
        stripped = line.strip()
        if pm_owner_marker.MARKER.fullmatch(stripped) is None:
            break
        fields: dict[str, str] = {}
        for key, value in pm_owner_marker.OWNER_FIELD.findall(stripped):
            cleaned = value.rstrip("-")
            fields.setdefault(key, cleaned.casefold() if key in ("actor", "target") else cleaned)
        markers.append(fields)
    return markers


def _marker_settles(fields: dict[str, str]) -> bool:
    kind = fields.get("type", "")
    actor = fields.get("actor", "")
    target = fields.get("target", "")
    status = fields.get("status", "")
    if status.upper() in pm_owner_marker.OWNER_INFORMATION_STATUSES and actor != "owner":
        return False
    settles = actor == "owner" or (
        target == "owner" and status.upper() in pm_owner_marker.OWNER_SETTLED
    )
    if settles and kind.casefold() == "waiting_owner":
        allowed = pm_owner_marker.WAITING_OWNER_DIRECTIONS.get((actor, target), frozenset())
        if status.upper() not in allowed:
            return False
    return settles


def _is_settled(
    comments: list[dict[str, Any]],
    repo: str,
    issue_number: int,
    ask_when: int,
    ask_id: int,
) -> bool:
    for comment in comments:
        if type(comment) is not dict:
            continue
        association = comment.get("author_association")
        if type(association) is not str or association not in pm_owner_marker.TRUSTED_ASSOCIATION:
            continue
        if not _valid_comment_issue(comment, repo, issue_number):
            continue
        cid = comment.get("id")
        if type(cid) is not int or cid == ask_id:
            continue
        when = _parse_iso_seconds(comment.get("created_at"))
        if when is None or (when, cid) < (ask_when, ask_id):
            continue
        body = comment.get("body")
        if type(body) is not str:
            continue
        for fields in _parse_leading_markers(body):
            if _marker_settles(fields):
                return True
    return False


def _verify_cited_marker(
    reply: str,
    transcript_path: str | Path | None,
    fetcher: Callable[[str, int, int], list[dict[str, Any]]],
    now: float,
) -> tuple[bool, str, str]:
    """Verify a cited Owner Ask marker against all §3 rules.

    Returns `(passed, detail_code, issue_label)`.
    """
    matches = list(ISSUE_COMMENT_URL_CAPTURE.finditer(reply))
    if not matches:
        return False, "MISSING_MARKER_URL", "NONE"

    turn_start = transcript_turn_start(transcript_path)
    if turn_start is None:
        return False, "ORDER_UNKNOWN", matches[0].group(2)

    named_issues = {int(m.group(1)) for m in ISSUE_REF_RE.finditer(reply)}

    for match in matches:
        repo = match.group(1)
        issue_number = int(match.group(2))
        comment_id = int(match.group(3))
        issue_label = str(issue_number)

        if named_issues and issue_number not in named_issues:
            return False, "ISSUE_MISMATCH", issue_label

        comments = fetcher(repo, issue_number, comment_id)
        if type(comments) is not list:
            return False, "FETCH_INVALID", issue_label

        target_comment = next(
            (c for c in comments if type(c) is dict and c.get("id") == comment_id),
            None,
        )
        if target_comment is None:
            return False, "MARKER_NOT_FOUND", issue_label

        if not _valid_comment_issue(target_comment, repo, issue_number):
            return False, "INVALID_ISSUE_URL", issue_label

        association = target_comment.get("author_association")
        if type(association) is not str or association not in pm_owner_marker.TRUSTED_ASSOCIATION:
            return False, "UNTRUSTED_AUTHOR", issue_label

        created = _parse_iso_seconds(target_comment.get("created_at"))
        if created is None:
            return False, "INVALID_CREATED_AT", issue_label

        if created >= turn_start:
            return False, "MARKER_AFTER_REPLY", issue_label

        if now - created >= owner_ask_audit.WAIT_HORIZON:
            return False, "MARKER_EXPIRED", issue_label

        body = target_comment.get("body")
        if type(body) is not str:
            return False, "INVALID_MARKER_BODY", issue_label

        lines = body.splitlines()
        line1 = owner_ask_audit._first_line_marker(body)
        if (
            not line1
            or line1.get("type") != "waiting_owner"
            or line1.get("actor", "").casefold() != "pm"
            or line1.get("target", "").casefold() != "owner"
            or line1.get("status", "").upper() != "WAITING"
        ):
            return False, "INVALID_LINE1_MARKER", issue_label

        gated = owner_ask_audit.GATED_LINE.fullmatch(lines[1].strip()) if len(lines) >= 2 else None
        if not gated or gated.group(1) not in pm_owner_marker.RESERVED_ROWS:
            return False, "INVALID_LINE2_GATED", issue_label

        if _is_settled(comments, repo, issue_number, created, comment_id):
            return False, "MARKER_SETTLED", issue_label

    return True, "PASS", matches[0].group(2)


def _was_already_blocked(log_path: Path, session_id: str, reply_sha256: str) -> bool:
    """Stream the entire log in bounded line memory to check prior BLOCK records."""
    if not log_path.is_file():
        return False
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            while True:
                line = handle.readline(MAX_LOG_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LOG_LINE_BYTES:
                    # Drain oversized line without buffering it.
                    while len(line) > MAX_LOG_LINE_BYTES and not line.endswith("\n"):
                        line = handle.readline(MAX_LOG_LINE_BYTES + 1)
                    continue
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except (ValueError, RecursionError):
                    continue
                if type(record) is not dict:
                    continue
                if (
                    record.get("outcome") == "BLOCK"
                    and record.get("session") == session_id
                    and record.get("reply_sha256") == reply_sha256
                ):
                    return True
    except OSError:
        return False
    return False


def _append_log(
    log_path: Path,
    *,
    ts: float,
    session_id: str,
    issue: str,
    outcome: str,
    detail: str,
    reply_sha256: str,
    mode: str,
) -> None:
    record = {
        "ts": _utc_iso(ts),
        "session": session_id,
        "issue": issue,
        "outcome": outcome,
        "detail": detail,
        "reply_sha256": reply_sha256,
        "mode": mode,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def evaluate(
    payload: dict[str, Any],
    *,
    fetcher: Callable[[str, int, int], list[dict[str, Any]]] | None = None,
    log_path: str | Path | None = None,
    mode: str | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Evaluate a Stop hook payload. Returns a block dict or None to stop."""
    resolved_mode = _resolve_mode(mode)
    resolved_log = _resolve_log_path(log_path)
    ts = time.time() if now is None else float(now)

    if type(payload) is not dict:
        _append_log(
            resolved_log,
            ts=ts,
            session_id="UNKNOWN",
            issue="NONE",
            outcome="HOOK_ERROR",
            detail="INVALID_PAYLOAD",
            reply_sha256=_reply_hash(""),
            mode=resolved_mode,
        )
        return None

    session_id = payload.get("session_id")
    session_label = session_id if type(session_id) is str and session_id else "UNKNOWN"
    reply = payload.get("last_assistant_message")
    if type(reply) is not str:
        _append_log(
            resolved_log,
            ts=ts,
            session_id=session_label,
            issue="NONE",
            outcome="HOOK_ERROR",
            detail="MISSING_ASSISTANT_MESSAGE",
            reply_sha256=_reply_hash(""),
            mode=resolved_mode,
        )
        return None

    reply_sha = _reply_hash(reply)
    if not detect_ask(reply):
        _append_log(
            resolved_log,
            ts=ts,
            session_id=session_label,
            issue="NONE",
            outcome="NO_ASK",
            detail="NO_ASK",
            reply_sha256=reply_sha,
            mode=resolved_mode,
        )
        return None

    active_fetcher = default_fetcher if fetcher is None else fetcher
    try:
        passed, detail, issue_label = _verify_cited_marker(
            reply,
            payload.get("transcript_path"),
            active_fetcher,
            ts,
        )
    except Exception:
        _append_log(
            resolved_log,
            ts=ts,
            session_id=session_label,
            issue="NONE",
            outcome="HOOK_ERROR",
            detail="FETCH_OR_VERIFY_ERROR",
            reply_sha256=reply_sha,
            mode=resolved_mode,
        )
        return None

    if passed:
        return None

    stop_hook_active = bool(payload.get("stop_hook_active"))
    already_blocked = _was_already_blocked(resolved_log, session_label, reply_sha)
    if resolved_mode == MODE_ENFORCE and not stop_hook_active and not already_blocked:
        _append_log(
            resolved_log,
            ts=ts,
            session_id=session_label,
            issue=issue_label,
            outcome="BLOCK",
            detail=detail,
            reply_sha256=reply_sha,
            mode=resolved_mode,
        )
        return {"decision": "block", "reason": BLOCK_REASON}

    _append_log(
        resolved_log,
        ts=ts,
        session_id=session_label,
        issue=issue_label,
        outcome="UNGATED_ALERTED",
        detail=detail,
        reply_sha256=reply_sha,
        mode=resolved_mode,
    )
    return None


def main(
    *,
    stdin=None,
    stdout=None,
    fetcher: Callable[[str, int, int], list[dict[str, Any]]] | None = None,
    log_path: str | Path | None = None,
    mode: str | None = None,
    now: float | None = None,
) -> int:
    """CLI entrypoint for the Claude Code Stop hook. Always exits 0."""
    in_stream = sys.stdin if stdin is None else stdin
    out_stream = sys.stdout if stdout is None else stdout
    resolved_mode = _resolve_mode(mode)
    resolved_log = _resolve_log_path(log_path)
    ts = time.time() if now is None else float(now)

    try:
        raw = in_stream.read()
        payload = json.loads(raw)
    except Exception:
        _append_log(
            resolved_log,
            ts=ts,
            session_id="UNKNOWN",
            issue="NONE",
            outcome="HOOK_ERROR",
            detail="MALFORMED_STDIN",
            reply_sha256=_reply_hash(""),
            mode=resolved_mode,
        )
        return 0

    try:
        decision = evaluate(
            payload,
            fetcher=fetcher,
            log_path=resolved_log,
            mode=resolved_mode,
            now=ts,
        )
    except Exception:
        _append_log(
            resolved_log,
            ts=ts,
            session_id="UNKNOWN",
            issue="NONE",
            outcome="HOOK_ERROR",
            detail="UNHANDLED_ERROR",
            reply_sha256=_reply_hash(""),
            mode=resolved_mode,
        )
        return 0

    if decision is not None:
        out_stream.write(json.dumps(decision) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
