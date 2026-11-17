"""Claude Code Stop hook: check PM chat asks for a valid Owner-ask marker.

The hook screens the last assistant message for a possible Owner decision
request, then verifies the single Issue-comment URL the reply cites. It reuses
the marker constants and parsers in ``scripts/pm_owner_marker.py`` and
``scripts/owner_ask_audit.py``.

Observe mode is the default and never blocks. Enforce mode is an explicit
switch (``LLMTOOL_OWNER_ASK_GATE_MODE=enforce``). Every non-PASS stop appends
exactly one JSON line to the state log. The log holds time, session, Issue or
NONE, outcome code and the SHA-256 of the reply; it never holds reply text,
comment bodies or tokens. A PASS writes no log line.

The default fetcher performs read-only GitHub API reads: every comment page of
the cited Issue, oldest first. Only a short page (fewer than ``FETCH_PAGE_SIZE``
comments) proves the list is complete; a failed or malformed page, or more pages
than ``FETCH_MAX_PAGES``, fails closed as HOOK_ERROR and never yields PASS.
Tests inject a fake fetcher and never use the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_script(name):
    module = sys.modules.get(name)
    if module is not None:
        return module
    path = _SCRIPTS / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pm_owner_marker = _load_script("pm_owner_marker")
owner_ask_audit = _load_script("owner_ask_audit")

MARKER_ASK = pm_owner_marker.MARKER_ASK
MARKER_ANSWER = pm_owner_marker.MARKER_ANSWER
RESERVED_ROWS = pm_owner_marker.RESERVED_ROWS
OWNER_SETTLED = pm_owner_marker.OWNER_SETTLED
TRUSTED_ASSOCIATION = pm_owner_marker.TRUSTED_ASSOCIATION
ISSUE_COMMENT_URL = pm_owner_marker.ISSUE_COMMENT_URL
GATED_LINE = owner_ask_audit.GATED_LINE
WAIT_HORIZON = owner_ask_audit.WAIT_HORIZON
CHAT_ASK = owner_ask_audit.CHAT_ASK

MODE_ENV = "LLMTOOL_OWNER_ASK_GATE_MODE"
LOG_ENV = "LLMTOOL_OWNER_ASK_GATE_LOG"
FETCH_TIMEOUT = 30
FETCH_PAGE_SIZE = 100
FETCH_MAX_PAGES = 50
BLOCK_REASON = (
    "owner_ask_gate: this reply asks the Owner for a decision but cites no valid "
    "owner-ask marker. Act under your own PM authority (CI FAIL -> repair; caps "
    "exhausted -> D3 REPLAN/DROP) or post an ask with pm_owner_marker.py ask and "
    "restate the request with its URL."
)

_OPTION_LINE = re.compile(r"^\s*[A-Za-z][.)]\s+\S", re.MULTILINE)
_OWNER = re.compile(r"(?<![A-Za-z0-9_])Owner(?![A-Za-z0-9_])", re.IGNORECASE)
_ROW_TERM = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(map(re.escape, RESERVED_ROWS)) +
    r")(?![A-Za-z0-9_-])")
_COMMENT_ID = re.compile(r"issuecomment-([1-9][0-9]{0,19})\Z")


class HookError(Exception):
    pass


def _mode():
    return "enforce" if os.environ.get(MODE_ENV) == "enforce" else "observe"


def _log_path(explicit=None):
    if explicit is not None:
        return Path(explicit)
    configured = os.environ.get(LOG_ENV)
    if configured:
        return Path(configured)
    return Path.home() / ".local" / "state" / "llmtool" / "owner-ask-gate.jsonl"


def _sha256(message):
    if type(message) is not str:
        message = ""
    return hashlib.sha256(message.encode("utf-8", "replace")).hexdigest()


def detect(message):
    """Screen only: a potential Owner ask, never a pass/fail by itself."""
    if type(message) is not str:
        return False
    if CHAT_ASK in message:
        return True
    if _ROW_TERM.search(message):
        return True
    return bool(_OWNER.search(message) and len(_OPTION_LINE.findall(message)) >= 2)


def _parse_ref(url):
    base, _, fragment = url.partition("#")
    parts = base.split("/")
    match = _COMMENT_ID.fullmatch(fragment)
    if (len(parts) != 7 or parts[5] != "issues" or match is None
            or not parts[3] or not parts[4] or not parts[6].isdigit()):
        raise HookError("NO_COMMENT_URL")
    return parts[3] + "/" + parts[4], int(parts[6]), int(match.group(1))


def _fetch_page(repo, issue, page):
    """One read-only GitHub API read of one Issue comment page."""
    try:
        result = subprocess.run(
            ["gh", "api",
             f"repos/{repo}/issues/{issue}/comments"
             f"?per_page={FETCH_PAGE_SIZE}&page={page}"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=FETCH_TIMEOUT,
            check=False, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HookError("FETCH_ERROR") from exc
    if result.returncode:
        raise HookError("FETCH_ERROR")
    try:
        comments = json.loads(result.stdout)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HookError("FETCH_ERROR") from exc
    if type(comments) is not list:
        raise HookError("FETCH_ERROR")
    return comments


def _default_fetch(url):
    """All read-only comment pages of the cited Issue, oldest first.

    A short page proves the end of the list. Anything short of that proof fails
    closed, so the settlement check always sees every comment.
    """
    repo, issue, _ = _parse_ref(url)
    comments = []
    for page in range(1, FETCH_MAX_PAGES + 1):
        batch = _fetch_page(repo, issue, page)
        comments.extend(batch)
        if len(batch) < FETCH_PAGE_SIZE:
            return comments
    raise HookError("FETCH_INCOMPLETE")


def _is_tool_result(entry):
    message = entry.get("message")
    content = message.get("content") if type(message) is dict else None
    return (type(content) is list and any(
        type(part) is dict and part.get("type") == "tool_result"
        for part in content))


def _first_assistant_time(path):
    """Epoch seconds of this turn's first assistant entry, or None (UNKNOWN)."""
    if type(path) is not str or not path:
        return None
    last_user = None
    assistant = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    entry = json.loads(raw)
                except (ValueError, RecursionError):
                    continue
                if type(entry) is not dict:
                    continue
                when = owner_ask_audit._seconds(
                    str(entry.get("timestamp", ""))[:19] + "Z")
                if when is None:
                    continue
                if entry.get("type") == "user" and not _is_tool_result(entry):
                    last_user = when if last_user is None else max(last_user, when)
                elif entry.get("type") == "assistant":
                    assistant.append(when)
    except OSError:
        return None
    if not assistant:
        return None
    if last_user is not None:
        after = [when for when in assistant if when >= last_user]
        return min(after) if after else None
    return min(assistant)


def _settled_later(comments, cited, issue):
    base = owner_ask_audit._seconds(cited.get("created_at"))
    cited_id = cited.get("id") if type(cited.get("id")) is int else 0
    for comment in comments:
        if comment is cited or type(comment) is not dict:
            continue
        if owner_ask_audit._issue_number(comment.get("issue_url")) != issue:
            continue
        when = owner_ask_audit._seconds(comment.get("created_at"))
        other_id = comment.get("id") if type(comment.get("id")) is int else 0
        if when is None or base is None or (when, other_id) <= (base, cited_id):
            continue
        body = comment.get("body")
        if type(body) is not str:
            continue
        lines = body.splitlines()
        if lines and lines[0].strip() == MARKER_ANSWER:
            return "MARKER_ANSWER"
        marker = owner_ask_audit._first_line_marker(body)
        if marker is None:
            continue
        status = marker.get("status", "")
        actor = marker.get("actor", "")
        target = marker.get("target", "")
        actor = actor.casefold() if type(actor) is str else ""
        target = target.casefold() if type(target) is str else ""
        if status in OWNER_SETTLED and (actor == "owner" or target == "owner"):
            return "OWNER_SETTLED"
    return None


def _verify(payload, fetch, now):
    message = payload.get("last_assistant_message")
    refs = (list(dict.fromkeys(ISSUE_COMMENT_URL.findall(message)))
            if type(message) is str else [])
    if not refs:
        return "NO_COMMENT_URL", None
    if len(refs) != 1:
        return "MULTIPLE_COMMENT_URLS", None
    try:
        _, issue, comment_id = _parse_ref(refs[0])
    except (HookError, ValueError):
        return "NO_COMMENT_URL", None
    comments = fetch(refs[0])
    if type(comments) is not list:
        return "HOOK_ERROR", issue
    cited = next((item for item in comments
                  if type(item) is dict and item.get("id") == comment_id), None)
    if cited is None:
        return "HOOK_ERROR", issue
    if owner_ask_audit._issue_number(cited.get("issue_url")) != issue:
        return "ISSUE_MISMATCH", issue
    if cited.get("author_association") not in TRUSTED_ASSOCIATION:
        return "UNTRUSTED_AUTHOR", issue
    body = cited.get("body")
    if type(body) is not str:
        return "WRONG_MARKER", issue
    lines = body.splitlines()
    if not lines or lines[0].strip() != MARKER_ASK:
        return "WRONG_MARKER", issue
    if len(lines) < 2:
        return "MISSING_GATED_LINE", issue
    gated = GATED_LINE.fullmatch(lines[1])
    if gated is None:
        return "MISSING_GATED_LINE", issue
    if gated.group(1) not in RESERVED_ROWS:
        return "ROW_UNKNOWN", issue
    created = owner_ask_audit._seconds(cited.get("created_at"))
    if created is None:
        return "UNKNOWN", issue
    if now - created >= WAIT_HORIZON:
        return "TOO_OLD", issue
    first = _first_assistant_time(payload.get("transcript_path"))
    if first is None:
        return "ORDER_UNKNOWN", issue
    if created >= first:
        return "ORDER_VIOLATION", issue
    settled = _settled_later(comments, cited, issue)
    if settled is not None:
        return settled, issue
    return "PASS", issue


def _append_log(log_path, payload, issue, outcome, sha, now=None):
    when = (datetime.fromtimestamp(now, timezone.utc) if now is not None
            else datetime.now(timezone.utc))
    session = payload.get("session_id") if type(payload) is dict else ""
    record = {
        "time": when.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "session": session if type(session) is str else "",
        "issue": issue if issue is not None else "NONE",
        "outcome": outcome,
        "reply_sha256": sha,
    }
    path = _log_path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
    except OSError:
        pass


def _already_blocked(log_path, sha):
    path = _log_path(log_path)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    record = json.loads(raw)
                except (ValueError, RecursionError):
                    continue
                if (type(record) is dict and record.get("reply_sha256") == sha
                        and record.get("outcome") == "UNGATED_BLOCKED"):
                    return True
    except OSError:
        return False
    return False


def handle(payload, fetch=None, now=None, log_path=None):
    """Return the hook response; an empty dict lets the turn stop."""
    if type(payload) is not dict:
        _append_log(log_path, {}, None, "HOOK_ERROR", _sha256(None), now)
        return {}
    message = payload.get("last_assistant_message")
    sha = _sha256(message)
    if type(message) is not str:
        _append_log(log_path, payload, None, "UNKNOWN", sha, now)
        return {}
    try:
        if not detect(message):
            _append_log(log_path, payload, None, "NO_ASK", sha, now)
            return {}
        outcome, issue = _verify(
            payload, fetch or _default_fetch, now if now is not None else int(time.time()))
        if outcome == "PASS":
            return {}
        if outcome in ("HOOK_ERROR", "UNKNOWN", "ORDER_UNKNOWN"):
            _append_log(log_path, payload, issue, outcome, sha, now)
            return {}
        if payload.get("stop_hook_active") is True or _already_blocked(log_path, sha):
            _append_log(log_path, payload, issue, "UNGATED_ALERTED", sha, now)
            return {}
        if _mode() == "enforce":
            _append_log(log_path, payload, issue, "UNGATED_BLOCKED", sha, now)
            return {"decision": "block", "reason": BLOCK_REASON}
        _append_log(log_path, payload, issue, outcome, sha, now)
        return {}
    except (HookError, OSError, ValueError, TypeError, subprocess.SubprocessError):
        _append_log(log_path, payload, None, "HOOK_ERROR", sha, now)
        return {}
    except Exception:
        _append_log(log_path, payload, None, "UNKNOWN", sha, now)
        return {}


def main(argv=None):
    del argv
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = None
    try:
        response = handle(payload)
    except Exception:
        response = {}
    if response:
        try:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
