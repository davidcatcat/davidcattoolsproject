#!/usr/bin/env python3
"""Dispatch a background Claude Developer and print a JSON start receipt (Issue #365).

`claude --bg` exits 0 and prints "backgrounded" even when the session then
stops at a picker, so a CLI exit is not delivery. This helper:

* resolves the target to one full session UUID (exact name, short id or UUID;
  no CLI fuzzy match) and refuses one that is busy right now;
* always passes ``-n ROLE`` and ``--add-dir`` with the registered D6 root,
  then ``--`` so the variadic ``--add-dir`` cannot swallow the prompt;
* polls ``claude agents --json`` for a bounded time until the started session
  has a transcript tool call made after launch (history copied by ``--resume``
  is older, so it never counts); a live row without a receipt status keeps
  polling, while explicit failed or blocked states run ``claude stop`` with
  the full session id and exit 1; at timeout it reports without stopping, and
  a post-launch action already present then counts as delivered;
* adopts only the target itself or a copy the launch output names; a new
  session that merely appeared nearby is never treated as this dispatch;
* reports the actual session id when a copy was started or the name differs,
  and when the D6 ``session_ref`` must be updated (the lifecycle has no update
  entrypoint, so it never edits the registry).

``watch`` applies the same session rules while the PM waits for a report file.
The account is ``CLAUDE_CONFIG_DIR`` (default ``~/.claude``); only transcript
files under its ``projects`` directory are read, never credentials.

Exit codes: 0 delivered (or report present), 1 not delivered / session
failed, 2 invalid input or target.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_completion import read_d6_registration  # noqa: E402
from llmtool import worker_dispatch  # noqa: E402

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
SHORT_RE = re.compile(r"[0-9a-f]{8}\Z")
ROLE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
MENTIONED_RE = re.compile(
    r"\b([0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?)\b")
DISPATCH_STATES = ("ACTIVE", "REPAIR", "REVIEW_PENDING")
# `status` can be temporarily absent while a session is working. Only explicit
# blocked/waiting or stopped/failed state values are terminal; other live rows
# keep polling until a receipt arrives or the dispatch times out.
RECEIPT_STATUSES = frozenset(("busy", "idle"))
FAILED_STATES = frozenset(("blocked", "waiting", "failed", "stopped", "error", "killed"))
FAILED_STATUSES = frozenset(("blocked", "waiting", "exited"))
# `claude --bg` prints this when the session started without a task.
NO_PROMPT_MARKER = "send a prompt to start"
CLI_TIMEOUT = 30
OUTPUT_LIMIT = 4 * 1024 * 1024
PROMPT_LIMIT = 64 * 1024
# There is no backward clock tolerance: the launch time is taken before
# `claude` runs, so every event of this dispatch is stamped at or after it,
# and anything earlier (e.g. resumed history) could be mistaken for a receipt.


class DispatchError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _now_ms():
    return int(time.time() * 1000)


def config_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


def _run(argv, *, cwd=None, timeout=CLI_TIMEOUT):
    try:
        proc = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DispatchError("CLAUDE_CLI_UNAVAILABLE", type(exc).__name__)
    if len(proc.stdout) > OUTPUT_LIMIT:
        raise DispatchError("CLAUDE_CLI_OUTPUT_TOO_LARGE")
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def list_agents(claude, *, include_done=False):
    argv = [claude, "agents", "--json"] + (["--all"] if include_done else [])
    code, out = _run(argv)
    if code != 0:
        raise DispatchError("CLAUDE_AGENTS_FAILED", "exit %d" % code)
    try:
        rows = json.loads(out)
    except ValueError:
        raise DispatchError("CLAUDE_AGENTS_INVALID")
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        raise DispatchError("CLAUDE_AGENTS_INVALID")
    return [row for row in rows
            if type(row.get("sessionId")) is str and UUID_RE.fullmatch(row["sessionId"])]


def transcript_paths(session_id):
    return sorted(glob.glob(os.path.join(glob.escape(str(config_dir() / "projects")), "*",
                                         glob.escape(session_id) + ".jsonl")))


def resolve_session(target, rows):
    """One full UUID for an exact UUID, short id or session name; never a guess."""
    if UUID_RE.fullmatch(target):
        if any(row["sessionId"] == target for row in rows) or transcript_paths(target):
            return target
        raise DispatchError("SESSION_NOT_FOUND", target)
    matches = {row["sessionId"] for row in rows
               if target in (row.get("name"), row.get("id"))}
    if not matches and SHORT_RE.fullmatch(target):
        pattern = os.path.join(glob.escape(str(config_dir() / "projects")), "*",
                               glob.escape(target) + "*.jsonl")
        matches = {Path(path).stem for path in glob.glob(pattern)
                   if UUID_RE.fullmatch(Path(path).stem)}
    if len(matches) > 1:
        raise DispatchError("SESSION_AMBIGUOUS", ",".join(sorted(matches)))
    if not matches:
        raise DispatchError("SESSION_NOT_FOUND", target)
    return matches.pop()


def session_cwd(session_id, rows):
    for row in rows:
        if row["sessionId"] == session_id and type(row.get("cwd")) is str:
            return row["cwd"]
    for path in transcript_paths(session_id):
        cwd = None
        for entry in _entries(path):
            if type(entry.get("cwd")) is str:
                cwd = entry["cwd"]
        if cwd:
            return cwd
    raise DispatchError("SESSION_CWD_UNKNOWN", session_id)


def _entries(path):
    try:
        with open(path, "rb") as stream:
            for raw in stream:
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if type(entry) is dict:
                    yield entry
    except OSError:
        return


def _entry_ms(entry):
    stamp = entry.get("timestamp")
    if type(stamp) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp() * 1000)


def _new_entries(path, offsets):
    """Complete lines appended since the last call for ``path``."""
    try:
        with open(path, "rb") as stream:
            stream.seek(offsets.get(path, 0))
            data = stream.read()
    except OSError:
        return
    end = data.rfind(b"\n") + 1
    offsets[path] = offsets.get(path, 0) + end
    for raw in data[:end].splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if type(entry) is dict:
            yield entry


def first_action_since(session_id, since_ms, offsets=None):
    """The first tool call the session made at or after ``since_ms``, else None.

    Pass the same ``offsets`` dict on each poll to read only appended lines.
    """
    offsets = {} if offsets is None else offsets
    for path in transcript_paths(session_id):
        for entry in _new_entries(path, offsets):
            if entry.get("type") != "assistant":
                continue
            at = _entry_ms(entry)
            if at is None or at < since_ms:
                continue
            content = (entry.get("message") or {}).get("content")
            if type(content) is not list:
                continue
            for item in content:
                if type(item) is dict and item.get("type") == "tool_use":
                    name = item.get("name")
                    return {"tool": name if type(name) is str else "UNKNOWN",
                            "timestamp": _iso(at)}
    return None


def _row(rows, session_id):
    return next((row for row in rows if row["sessionId"] == session_id), None)


def _started_after(row, launch_ms):
    started = row.get("startedAt")
    return type(started) in (int, float) and started >= launch_ms


def identify_started(rows, *, expected, before_ids, launch_ms, launch_output):
    """The session the launch actually started: the target itself or a copy.

    A copy counts only when the launch output names it; a new session that
    merely appeared nearby may be someone else's, so it is never adopted.
    """
    row = _row(rows, expected)
    if row is not None and (expected not in before_ids or _started_after(row, launch_ms)):
        return row
    fresh = [row for row in rows
             if row["sessionId"] != expected and _started_after(row, launch_ms) and
             (row["sessionId"] not in before_ids)]
    mentioned = set(MENTIONED_RE.findall(launch_output))
    named = [row for row in fresh
             if row["sessionId"] in mentioned or row.get("id") in mentioned]
    return named[0] if len(named) == 1 else None


def stop_session(claude, row_or_id):
    ref = row_or_id["sessionId"] if type(row_or_id) is dict else row_or_id
    try:
        code, _ = _run([claude, "stop", ref], timeout=15)
    except DispatchError as exc:
        return {"attempted": True, "session": ref, "exit": None, "error": exc.code}
    return {"attempted": True, "session": ref, "exit": code}


def _session_code(value):
    if type(value) is str and re.fullmatch(r"[a-z_]{1,32}", value):
        return "SESSION_" + value.upper()
    return "SESSION_STATE_UNKNOWN"


def _failure_reason(row):
    """Why this session cannot be (or stay) a healthy receipt, else None.

    Callers check ``state == "done"`` first: a session that already acted and
    finished was delivered.
    """
    status, state = row.get("status"), row.get("state")
    if state in FAILED_STATES:
        return _session_code(state)
    if status in FAILED_STATUSES:
        return _session_code(status)
    return None


def dispatch(*, claude, target, role, task_id, prompt, repo_root, timeout, poll_interval,
             new_session=False, owner_assignment_ref=None):
    if not ROLE_RE.fullmatch(role or ""):
        raise DispatchError("ROLE_NAME_INVALID")
    worker_key = {"claude-reviewer": "claude-reviewer",
                  "claude-developer": "claude-developer",
                  "codex-reviewer": "codex-reviewer"}.get(role)
    if worker_key is None:
        raise DispatchError("WORKER_UNKNOWN")
    decision = worker_dispatch.evaluate(worker_key, owner_assignment_ref, task_id)
    if not decision.allowed:
        raise DispatchError(decision.reason_code)
    if not prompt or len(prompt.encode("utf-8")) > PROMPT_LIMIT or prompt.startswith("-"):
        raise DispatchError("PROMPT_INVALID")
    registration = _registration(task_id, repo_root)
    rows = list_agents(claude, include_done=True)
    if new_session:
        expected = str(uuid.uuid4())
        cwd = registration.root
        session_args = ["--session-id", expected]
    else:
        expected = resolve_session(target, rows)
        current = _row(list_agents(claude), expected)
        if current is not None and current.get("status") == "busy":
            # Resuming a busy session starts a second writer for the same task.
            raise DispatchError("SESSION_BUSY", expected)
        cwd = session_cwd(expected, rows)
        session_args = ["--resume", expected]
    before_ids = {row["sessionId"] for row in list_agents(claude)}
    receipt = {
        "schema_version": 1, "result": "NOT_DELIVERED", "reason": None,
        "requested_session": None if new_session else target,
        "resolved_session_id": expected, "actual_session_id": None, "actual_short_id": None,
        "copy_started": False, "expected_name": role, "actual_name": None, "name_ok": False,
        "d6_task_id": task_id, "d6_root": registration.root,
        "d6_session_ref": registration.session_ref, "session_ref_update_required": None,
        "launched_at": None, "receipt_at": None, "status": None, "state": None,
        "first_action": None, "stop": {"attempted": False}, "warnings": [],
    }
    argv = ([claude, "--bg"] + session_args +
            # `--add-dir` is variadic: `--` ends it so the prompt stays the prompt.
            ["-n", role, "--add-dir", registration.root, "--", prompt])
    launch_ms = _now_ms()
    receipt["launched_at"] = _iso(launch_ms)
    code, output = _run(argv, cwd=cwd)
    if code != 0:
        receipt["reason"] = "LAUNCH_FAILED"
        return receipt
    no_prompt = NO_PROMPT_MARKER in output
    deadline = time.monotonic() + timeout
    started = None
    offsets = {}
    while True:
        rows = list_agents(claude)
        if started is None:
            found = identify_started(rows, expected=expected, before_ids=before_ids,
                                     launch_ms=launch_ms, launch_output=output)
            if found is not None:
                started = found["sessionId"]
                _record_identity(receipt, found, expected, registration)
        row = None if started is None else _row(rows, started)
        if started is not None and row is None:
            receipt["reason"] = "SESSION_GONE"
            receipt["stop"] = stop_session(claude, started)
            return receipt
        if row is not None:
            receipt["status"], receipt["state"] = row.get("status"), row.get("state")
            if row.get("state") == "done":
                # A finished session (often `status` null) is checked before the
                # failure rules: it was delivered if it acted after launch, and
                # there is nothing left to stop either way.
                action = first_action_since(started, launch_ms, offsets)
                if action is not None:
                    receipt.update(result="DELIVERED", reason=None, first_action=action,
                                   receipt_at=_iso(_now_ms()))
                else:
                    receipt["reason"] = "SESSION_DONE"
                return receipt
            reason = "PROMPT_NOT_DELIVERED" if no_prompt else _failure_reason(row)
            if reason is not None:
                receipt["reason"] = reason
                receipt["stop"] = stop_session(claude, row)
                return receipt
            if row.get("status") in RECEIPT_STATUSES:
                action = first_action_since(started, launch_ms, offsets)
                if action is not None:
                    receipt.update(result="DELIVERED", reason=None, first_action=action,
                                   receipt_at=_iso(_now_ms()))
                    return receipt
        if time.monotonic() >= deadline:
            if row is not None:
                action = first_action_since(started, launch_ms, offsets)
                if action is not None:
                    receipt.update(result="DELIVERED", reason=None, first_action=action,
                                   receipt_at=_iso(_now_ms()))
                    return receipt
            receipt["reason"] = ("NO_NEW_ACTION" if row is not None and
                                 row.get("status") in RECEIPT_STATUSES else
                                 "SESSION_TIMEOUT" if row is not None else
                                 "PROMPT_NOT_DELIVERED" if no_prompt else "SESSION_NOT_STARTED")
            return receipt
        time.sleep(poll_interval)


def _record_identity(receipt, row, expected, registration):
    actual = row["sessionId"]
    receipt["actual_session_id"] = actual
    receipt["actual_short_id"] = row.get("id")
    receipt["actual_name"] = row.get("name")
    receipt["name_ok"] = row.get("name") == receipt["expected_name"]
    if actual != expected:
        receipt["copy_started"] = True
        receipt["warnings"].append("COPY_STARTED")
    if not receipt["name_ok"]:
        receipt["warnings"].append("NAME_MISMATCH")
    if registration.session_ref != actual:
        receipt["session_ref_update_required"] = {
            "task_id": registration.task_id, "from": registration.session_ref, "to": actual}
        receipt["warnings"].append("SESSION_REF_UPDATE_REQUIRED")


def _report_signature(path):
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return (info.st_ino, info.st_size, info.st_mtime_ns)


def watch(*, claude, session_id, report_file, timeout, poll_interval, since_ms=None):
    """Wait for a new report file while the session stays healthy.

    With ``since_ms`` (the receipt's ``launched_at``) a report counts when it
    was written at or after it; without it, only a file that differs from
    what was there when watching began counts.
    """
    if not UUID_RE.fullmatch(session_id or ""):
        raise DispatchError("SESSION_ID_INVALID")
    result = {"schema_version": 1, "result": "WAITING", "reason": None,
              "session_id": session_id, "report_file": str(report_file),
              "status": None, "state": None, "stop": {"attempted": False}}
    initial = _report_signature(report_file)

    def fresh_report():
        current = _report_signature(report_file)
        if current is None or current[1] == 0:
            return False
        if since_ms is not None:
            return current[2] >= since_ms * 1000000
        return current != initial

    deadline = time.monotonic() + timeout
    while True:
        if fresh_report():
            result["result"] = "REPORT_PRESENT"
            return result
        row = _row(list_agents(claude), session_id)
        if row is None or row.get("state") == "done":
            if fresh_report():  # it may have written the report and then finished
                result["result"] = "REPORT_PRESENT"
                return result
            result.update(result="FAILED",
                          reason="SESSION_GONE" if row is None else "SESSION_DONE")
            return result
        result["status"], result["state"] = row.get("status"), row.get("state")
        reason = _failure_reason(row)
        if reason is not None:
            result.update(result="FAILED", reason=reason, stop=stop_session(claude, row))
            return result
        if time.monotonic() >= deadline:
            result.update(result="FAILED", reason="REPORT_TIMEOUT")
            return result
        time.sleep(poll_interval)


def _registration(task_id, repo_root):
    try:
        common = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], stdin=subprocess.DEVNULL, capture_output=True,
            timeout=CLI_TIMEOUT, check=True).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        raise DispatchError("D6_REPOSITORY_INVALID")
    try:
        return read_d6_registration(task_id, os.path.realpath(common),
                                    allowed_states=DISPATCH_STATES)
    except LLMToolError as exc:
        raise DispatchError("D6_REGISTRATION_INVALID",
                            getattr(exc, "completion_code", "") or str(exc))


def _bounded(value, low, high, name):
    if not low <= value <= high:
        raise DispatchError("ARGUMENT_INVALID", name)
    return value


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--claude", default=shutil.which("claude") or "claude")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("dispatch")
    target = start.add_mutually_exclusive_group(required=True)
    target.add_argument("--session", help="full UUID, short id or exact session name")
    target.add_argument("--new", action="store_true", help="start a new session")
    start.add_argument("--name", required=True, help="role name passed as -n")
    start.add_argument("--task-id", required=True, help="registered D6 task id")
    start.add_argument("--owner-assignment-ref", help="exact Owner assignment reference")
    prompt = start.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    start.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[3]))
    start.add_argument("--timeout", type=float, default=120.0)
    wait = sub.add_parser("watch")
    wait.add_argument("--session-id", required=True)
    wait.add_argument("--report-file", required=True)
    wait.add_argument("--timeout", type=float, default=3600.0)
    wait.add_argument("--since", help="the dispatch receipt's launched_at")
    args = parser.parse_args(argv)
    try:
        poll = _bounded(args.poll_interval, 0.01, 60, "--poll-interval")
        if args.command == "dispatch":
            text = args.prompt
            if args.prompt_file is not None:
                try:
                    with open(args.prompt_file, "rb") as stream:
                        text = stream.read(PROMPT_LIMIT + 1).decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    raise DispatchError("PROMPT_INVALID")
            result = dispatch(
                claude=args.claude, target=args.session, role=args.name,
                task_id=args.task_id, prompt=text, repo_root=args.repo_root,
                timeout=_bounded(args.timeout, 1, 600, "--timeout"), poll_interval=poll,
                new_session=args.new, owner_assignment_ref=args.owner_assignment_ref)
            ok = result["result"] == "DELIVERED"
        else:
            since_ms = None
            if args.since is not None:
                since_ms = _entry_ms({"timestamp": args.since})
                if since_ms is None:
                    raise DispatchError("ARGUMENT_INVALID", "--since")
            result = watch(claude=args.claude, session_id=args.session_id,
                           report_file=args.report_file,
                           timeout=_bounded(args.timeout, 1, 86400, "--timeout"),
                           poll_interval=poll, since_ms=since_ms)
            ok = result["result"] == "REPORT_PRESENT"
    except DispatchError as exc:
        print(json.dumps({"schema_version": 1, "result": "REJECTED", "reason": exc.code,
                          "detail": exc.detail}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main())
