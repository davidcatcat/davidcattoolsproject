#!/usr/bin/env python3
"""Dispatch a background Claude Developer and print a JSON start receipt (Issue #365).

`claude --bg` exits 0 and prints "backgrounded" even when the session then
stops at a picker, so a CLI exit is not delivery. This helper:

* resolves the target to one full session UUID (exact full UUID or exact
  session name; short ids, prefixes and CLI fuzzy matches are refused) and
  refuses one that is busy right now;
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

``watch --name ROLE`` applies the same session rules while waiting for a report
file and resolves the explicitly named Worker role, just like ``dispatch``.
Each dispatch or watch run uses one account: ``CLAUDE_CONFIG_DIR`` takes
precedence over its configured role account, with ``~/.claude`` as the default
for Developer roles. A Claude Reviewer must have an account. Only transcript
files under that account's ``projects`` directory are read, never credentials.

Cross-account delivery (#367 slice 2) never launches anything. ``dispatch``
launches only under the resolved role account: a ``--worker-config-dir`` that
is not the resolved ``CLAUDE_CONFIG_DIR`` is refused with
``CROSS_ACCOUNT_LAUNCH_REFUSED`` before any gate or CLI call. Instead the PM
runs ``llmtool task assign`` and then ``receipt --assignment-id ID
--worker-config-dir DIR``, which reads the shared TaskStore and reports
DELIVERED only when the slice-1 claim receipt exists, its account label is
that directory's, and the claiming session's transcript for the D6 root is a
plain, unlinked file under that directory (read-only; transcript files only).
This is accountability bookkeeping, not authentication: every account runs as
the same OS user.

Exit codes: 0 delivered (or report present), 1 not delivered / session
failed / unclaimed, 2 invalid input or target.
"""
import argparse
import glob
import json
import os
import pwd
import re
import shutil
import stat
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
from llmtool import active_pm_inbox, developer_assignment_inbox  # noqa: E402
from llmtool import role_config  # noqa: E402
from llmtool.task_store import TaskStore  # noqa: E402

ASSIGNMENT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
RECEIPT_WAIT_MAX = 600
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
# Looks like a (partial or mis-cased) session id rather than a session name.
ID_SHAPED_RE = re.compile(r"[0-9a-fA-F-]{1,36}\Z")
ROLE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
CLAUDE_DEVELOPER_ROLE_RE = re.compile(
    r"claude-developer(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?\Z")
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
# #1019 row 4: a Claude Reviewer session starts without file-editing tools.
# Bash stays, because the Reviewer posts its verdict with gh; this is an accident
# guard, not a containment boundary.
REVIEWER_DISALLOWED_TOOLS_ARG = "--disallowedTools=Edit,Write,NotebookEdit"
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


def config_dir(role_settings=None):
    value = os.environ.get("CLAUDE_CONFIG_DIR")
    if not value and role_settings is not None:
        value = role_settings.get("config_dir")
    return Path(value or _default_config_dir())


def _default_config_dir():
    """Return the default Claude account using the OS user's home directory."""
    return os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".claude")


def _role_key(role):
    if role == "claude-reviewer":
        return "reviewer"
    if type(role) is str and CLAUDE_DEVELOPER_ROLE_RE.fullmatch(role):
        return "claude-developer"
    return role


def _account_environment(role, model=None):
    """Resolve one CLI account for a run, preserving environment precedence.

    An explicit ``model`` is the one-shot CLI layer of the role resolver, so it
    passes the same allowed-model check as every other layer.
    """
    if role == "codex-reviewer":
        raise DispatchError("ROLE_PROVIDER_UNSUPPORTED")
    environ = os.environ.copy()
    if model is None:
        configured = role_config.resolve_role(_role_key(role))
    else:
        configured = role_config.resolve_role(_role_key(role), cli={"model": model})
    if configured.get("provider") not in (None, "claude"):
        raise DispatchError("ROLE_PROVIDER_UNSUPPORTED")
    selected = environ.get("CLAUDE_CONFIG_DIR") or configured.get("config_dir")
    if role == "claude-reviewer" and not selected:
        raise DispatchError("REVIEWER_ACCOUNT_UNCONFIGURED")
    default_dir = _default_config_dir()
    resolved = os.path.realpath(selected or default_dir)
    if resolved == os.path.realpath(default_dir):
        environ["HOME"] = os.path.dirname(default_dir)
        environ.pop("CLAUDE_CONFIG_DIR", None)
    else:
        environ["CLAUDE_CONFIG_DIR"] = str(selected)
    return configured, environ


def _reviewer_model(requested):
    """#1502 WP-E R1: the Claude Reviewer model, admitted by the shared registry check.

    The explicit ``--model`` wins, else the resolved role model; anything the
    check refuses (including no model at all) stops with ROLE_MODEL_NOT_ALLOWED.
    """
    try:
        return role_config.check_registry_model("claude-reviewer", requested)
    except LLMToolError:
        raise DispatchError("ROLE_MODEL_NOT_ALLOWED") from None


def _worker_key(role):
    """Return the dispatch-gated Worker key for a supported Claude role."""
    if role == "claude-reviewer":
        return "claude-reviewer"
    if type(role) is str and CLAUDE_DEVELOPER_ROLE_RE.fullmatch(role):
        return "claude-developer"
    raise DispatchError("WORKER_UNKNOWN")


def _run(argv, *, cwd=None, timeout=CLI_TIMEOUT, env=None):
    try:
        proc = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DispatchError("CLAUDE_CLI_UNAVAILABLE", type(exc).__name__)
    if len(proc.stdout) > OUTPUT_LIMIT:
        raise DispatchError("CLAUDE_CLI_OUTPUT_TOO_LARGE")
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def list_agents(claude, *, include_done=False, env=None):
    argv = [claude, "agents", "--json"] + (["--all"] if include_done else [])
    code, out = _run(argv, env=env)
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


def transcript_paths(session_id, role_settings=None):
    return sorted(glob.glob(os.path.join(glob.escape(str(config_dir(role_settings) / "projects")), "*",
                                         glob.escape(session_id) + ".jsonl")))


def resolve_session(target, rows, role_settings=None):
    """One full UUID for an exact full UUID or exact session name; never a guess.

    Short ids, UUID prefixes and any other id-shaped value (hex digits and
    dashes, any case) that is not a full lowercase UUID are refused: a prefix
    can match a different session than the one the PM meant.
    """
    if UUID_RE.fullmatch(target):
        if any(row["sessionId"] == target for row in rows) or transcript_paths(target, role_settings):
            return target
        raise DispatchError("SESSION_NOT_FOUND", target)
    if ID_SHAPED_RE.fullmatch(target):
        raise DispatchError("SESSION_ID_NOT_FULL", target)
    matches = {row["sessionId"] for row in rows if row.get("name") == target}
    if len(matches) > 1:
        raise DispatchError("SESSION_AMBIGUOUS", ",".join(sorted(matches)))
    if not matches:
        raise DispatchError("SESSION_NOT_FOUND", target)
    return matches.pop()


def session_cwd(session_id, rows, role_settings=None):
    for row in rows:
        if row["sessionId"] == session_id and type(row.get("cwd")) is str:
            return row["cwd"]
    for path in transcript_paths(session_id, role_settings):
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


def first_action_since(session_id, since_ms, offsets=None, paths=None):
    """The first tool call the session made at or after ``since_ms``, else None.

    Pass the same ``offsets`` dict on each poll to read only appended lines.
    ``paths`` replaces the caller-account transcript lookup (used read-only for
    an explicit worker account directory).
    """
    offsets = {} if offsets is None else offsets
    for path in (transcript_paths(session_id) if paths is None else paths):
        for entry in _new_entries(path, offsets):
            if entry.get("type") != "assistant":
                continue
            at = _entry_ms(entry)
            if at is None or at < since_ms:
                continue
            message = entry.get("message")
            content = message.get("content") if type(message) is dict else None
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


def stop_session(claude, row_or_id, *, env=None):
    ref = row_or_id["sessionId"] if type(row_or_id) is dict else row_or_id
    try:
        code, _ = _run([claude, "stop", ref], timeout=15, env=env)
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


def worker_config_dir(value):
    """An explicit worker account directory: absolute, this user's, not a symlink.

    Only ``lstat`` is used here; nothing inside the directory is opened.
    """
    if type(value) is not str or not value or not os.path.isabs(value):
        raise DispatchError("WORKER_CONFIG_DIR_INVALID")
    path = os.path.normpath(value)
    try:
        info = os.lstat(path)
    except OSError:
        raise DispatchError("WORKER_CONFIG_DIR_INVALID")
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise DispatchError("WORKER_CONFIG_DIR_INVALID")
    return path


def refuse_cross_account_launch(worker_dir, *, account_env=None):
    """A launch acts as the resolved Worker account, so only that account is allowed."""
    if worker_dir is None:
        return
    effective_env = os.environ if account_env is None else account_env
    selected = effective_env.get("CLAUDE_CONFIG_DIR") or _default_config_dir()
    if not os.path.isabs(worker_dir) or os.path.realpath(worker_dir) != os.path.realpath(selected):
        raise DispatchError("CROSS_ACCOUNT_LAUNCH_REFUSED")


def dispatch(*, claude, target, role, task_id, prompt, repo_root, timeout, poll_interval,
             new_session=False, owner_assignment_ref=None, worker_dir=None, model=None):
    is_claude_developer_role = (type(role) is str and
                                CLAUDE_DEVELOPER_ROLE_RE.fullmatch(role) is not None)
    if not is_claude_developer_role and not ROLE_RE.fullmatch(role or ""):
        raise DispatchError("ROLE_NAME_INVALID")
    try:
        role_settings, account_env = _account_environment(role, model)
    except LLMToolError as exc:
        raise DispatchError(getattr(exc, "role_config_code", "ROLE_CONFIG_INVALID")) from None
    refuse_cross_account_launch(worker_dir, account_env=account_env)
    worker_key = _worker_key(role)
    decision = worker_dispatch.evaluate(worker_key, owner_assignment_ref, task_id)
    if not decision.allowed:
        raise DispatchError(decision.reason_code)
    if not prompt or len(prompt.encode("utf-8")) > PROMPT_LIMIT or prompt.startswith("-"):
        raise DispatchError("PROMPT_INVALID")
    # #1502 WP-E R1: the launch model is fixed here, before any CLI call, and is
    # passed explicitly with --model to new and resumed sessions alike.
    launch_model = None
    if role == "claude-reviewer":
        launch_model = _reviewer_model(model if model is not None else role_settings.get("model"))
    elif model is not None:
        # The resolver's CLI layer already checked it against the route.
        if role_settings.get("model") != model:
            raise DispatchError("ROLE_MODEL_NOT_ALLOWED")
        launch_model = model
    registration = _registration(task_id, repo_root)
    rows = list_agents(claude, include_done=True, env=account_env)
    if new_session:
        expected = str(uuid.uuid4())
        cwd = registration.root
        session_args = ["--session-id", expected]
    else:
        expected = resolve_session(target, rows, role_settings)
        current = _row(list_agents(claude, env=account_env), expected)
        if current is not None and current.get("status") == "busy":
            # Resuming a busy session starts a second writer for the same task.
            raise DispatchError("SESSION_BUSY", expected)
        cwd = session_cwd(expected, rows, role_settings)
        session_args = ["--resume", expected]
    before_ids = {row["sessionId"] for row in list_agents(claude, env=account_env)}
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
    tool_limit = [REVIEWER_DISALLOWED_TOOLS_ARG] if role == "claude-reviewer" else []
    model_args = ["--model", launch_model] if launch_model is not None else []
    argv = ([claude, "--bg"] + session_args + model_args + tool_limit +
            # `--add-dir` is variadic: `--` ends it so the prompt stays the prompt.
            ["-n", role, "--add-dir", registration.root, "--", prompt])
    launch_ms = _now_ms()
    receipt["launched_at"] = _iso(launch_ms)
    launch_env = account_env.copy()
    if launch_model is not None:
        # An inherited ANTHROPIC_MODEL never differs from the explicit model.
        launch_env["ANTHROPIC_MODEL"] = launch_model
    elif not launch_env.get("ANTHROPIC_MODEL") and role_settings.get("model"):
        launch_env["ANTHROPIC_MODEL"] = role_settings["model"]
    code, output = _run(argv, cwd=cwd, env=launch_env)
    if code != 0:
        receipt["reason"] = "LAUNCH_FAILED"
        return receipt
    no_prompt = NO_PROMPT_MARKER in output
    deadline = time.monotonic() + timeout
    started = None
    offsets = {}
    while True:
        rows = list_agents(claude, env=account_env)
        if started is None:
            found = identify_started(rows, expected=expected, before_ids=before_ids,
                                     launch_ms=launch_ms, launch_output=output)
            if found is not None:
                started = found["sessionId"]
                _record_identity(receipt, found, expected, registration)
        row = None if started is None else _row(rows, started)
        if started is not None and row is None:
            receipt["reason"] = "SESSION_GONE"
            receipt["stop"] = stop_session(claude, started, env=account_env)
            return receipt
        if row is not None:
            receipt["status"], receipt["state"] = row.get("status"), row.get("state")
            if row.get("state") == "done":
                # A finished session (often `status` null) is checked before the
                # failure rules: it was delivered if it acted after launch, and
                # there is nothing left to stop either way.
                action = first_action_since(started, launch_ms, offsets,
                                            paths=transcript_paths(started, role_settings))
                if action is not None:
                    receipt.update(result="DELIVERED", reason=None, first_action=action,
                                   receipt_at=_iso(_now_ms()))
                else:
                    receipt["reason"] = "SESSION_DONE"
                return receipt
            reason = "PROMPT_NOT_DELIVERED" if no_prompt else _failure_reason(row)
            if reason is not None:
                receipt["reason"] = reason
                receipt["stop"] = stop_session(claude, row, env=account_env)
                return receipt
            if row.get("status") in RECEIPT_STATUSES:
                action = first_action_since(started, launch_ms, offsets,
                                            paths=transcript_paths(started, role_settings))
                if action is not None:
                    receipt.update(result="DELIVERED", reason=None, first_action=action,
                                   receipt_at=_iso(_now_ms()))
                    return receipt
        if time.monotonic() >= deadline:
            if row is not None:
                action = first_action_since(started, launch_ms, offsets,
                                            paths=transcript_paths(started, role_settings))
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


def watch(*, claude, session_id, report_file, timeout, poll_interval, role, since_ms=None):
    """Wait for a new report file while the session stays healthy.

    With ``since_ms`` (the receipt's ``launched_at``) a report counts when it
    was written at or after it; without it, only a file that differs from
    what was there when watching began counts.
    """
    if not UUID_RE.fullmatch(session_id or ""):
        raise DispatchError("SESSION_ID_INVALID")
    try:
        _, account_env = _account_environment(role)
    except LLMToolError as exc:
        raise DispatchError(getattr(exc, "role_config_code", "ROLE_CONFIG_INVALID")) from None
    _worker_key(role)
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
        row = _row(list_agents(claude, env=account_env), session_id)
        if row is None or row.get("state") == "done":
            if fresh_report():  # it may have written the report and then finished
                result["result"] = "REPORT_PRESENT"
                return result
            if row is None:
                # Same rule as dispatch: a vanished session is stopped by full id
                # in case it is still running outside the agents list.
                result.update(result="FAILED", reason="SESSION_GONE",
                              stop=stop_session(claude, session_id, env=account_env))
            else:
                result.update(result="FAILED", reason="SESSION_DONE")
            return result
        result["status"], result["state"] = row.get("status"), row.get("state")
        reason = _failure_reason(row)
        if reason is not None:
            result.update(result="FAILED", reason=reason,
                          stop=stop_session(claude, row, env=account_env))
            return result
        if time.monotonic() >= deadline:
            result.update(result="FAILED", reason="REPORT_TIMEOUT")
            return result
        time.sleep(poll_interval)


def _utc_now():
    return datetime.now(timezone.utc)


def _assignment(store, assignment_id):
    try:
        items = store.get_developer_assignments()
    except LLMToolError as exc:
        raise DispatchError("ASSIGNMENTS_UNAVAILABLE",
                            developer_assignment_inbox.refusal_code(exc) or "")
    item = next((item for item in items if item["assignment_id"] == assignment_id), None)
    if item is None:
        raise DispatchError("ASSIGNMENT_NOT_FOUND", assignment_id)
    return item


def delivery_receipt(*, assignment_id, worker_dir, store=None, wait=False, timeout=0,
                     poll_interval=2.0, clock=_utc_now):
    """Account-aware delivery evidence for a ``task assign`` assignment; never launches.

    DELIVERED needs all of: the slice-1 claim receipt (state CLAIMED), its
    ``account_dir_label`` equal to ``worker_dir``'s, and the claiming
    session's transcript for the D6 root as a plain, unlinked file of this
    user under ``worker_dir``. ``first_action`` (a tool call at or after the
    claim) is reported when present but is not required.
    """
    if type(assignment_id) is not str or not ASSIGNMENT_ID_RE.fullmatch(assignment_id):
        raise DispatchError("ASSIGNMENT_ID_INVALID")
    worker_dir = worker_config_dir(worker_dir)
    environ = {"CLAUDE_CONFIG_DIR": worker_dir}
    label = developer_assignment_inbox.account_dir_label(environ)
    store = TaskStore() if store is None else store
    deadline = time.monotonic() + timeout
    while True:
        item = _assignment(store, assignment_id)
        result = {
            "schema_version": 1, "result": "NOT_DELIVERED", "reason": None,
            "assignment_id": assignment_id, "worker": item["worker"],
            "d6_task_id": item["d6_task_id"], "d6_root": item["d6_root"],
            "source_sha": item["source_sha"], "expires_at": item["expires_at"],
            "state": item["state"], "claim": item["claim"], "account_match": None,
            "transcript_verified": False, "first_action": None, "launched": False,
        }
        if item["state"] == "CLAIMED":
            break
        expired = item["state"] == "EXPIRED" or clock() >= datetime.strptime(
            item["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if expired:
            result.update(result="UNCLAIMED", reason="ASSIGNMENT_UNCLAIMED")
            return result
        if not wait or time.monotonic() >= deadline:
            result["reason"] = "ASSIGNMENT_NOT_CLAIMED"
            return result
        time.sleep(max(0.0, min(poll_interval, deadline - time.monotonic())))
    claim = item["claim"]
    result["account_match"] = claim["account_dir_label"] == label
    if not result["account_match"]:
        result["reason"] = "WORKER_ACCOUNT_MISMATCH"
        return result
    try:
        active_pm_inbox.observe_claude_session(claim["session_id"], item["d6_root"], environ)
    except LLMToolError:
        result["reason"] = "WORKER_TRANSCRIPT_INVALID"
        return result
    result["transcript_verified"] = True
    path = active_pm_inbox.claude_session_file(claim["session_id"], item["d6_root"], environ)
    since_ms = _entry_ms({"timestamp": claim["claimed_at"]})
    result["first_action"] = first_action_since(claim["session_id"], since_ms,
                                                paths=[str(path)])
    result["result"] = "DELIVERED"
    return result


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
    target.add_argument("--session", help="full session UUID or exact session name")
    target.add_argument("--new", action="store_true", help="start a new session")
    start.add_argument("--name", required=True, help="role name passed as -n")
    start.add_argument("--task-id", required=True, help="registered D6 task id")
    start.add_argument("--owner-assignment-ref", help="exact Owner assignment reference")
    start.add_argument("--model", help="explicit model; must pass the role's allowed-model check")
    prompt = start.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    start.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[3]))
    start.add_argument("--timeout", type=float, default=120.0)
    start.add_argument("--worker-config-dir",
                       help="must match the resolved Worker account; another "
                            "account is refused (use task assign + receipt)")
    proof = sub.add_parser("receipt", help="cross-account delivery evidence; never launches")
    proof.add_argument("--assignment-id", required=True, help="id printed by task assign")
    proof.add_argument("--worker-config-dir", required=True,
                       help="the Developer account's Claude config directory (read-only)")
    proof.add_argument("--wait", action="store_true", help="wait for the claim (bounded)")
    proof.add_argument("--timeout", type=float, default=540.0)
    wait = sub.add_parser("watch")
    wait.add_argument("--session-id", required=True)
    wait.add_argument("--name", required=True, help="Worker role whose account owns the session")
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
                new_session=args.new, owner_assignment_ref=args.owner_assignment_ref,
                worker_dir=args.worker_config_dir, model=args.model)
            ok = result["result"] == "DELIVERED"
        elif args.command == "receipt":
            result = delivery_receipt(
                assignment_id=args.assignment_id, worker_dir=args.worker_config_dir,
                wait=args.wait,
                timeout=_bounded(args.timeout, 0, RECEIPT_WAIT_MAX, "--timeout"),
                poll_interval=poll)
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
                           poll_interval=poll, since_ms=since_ms, role=args.name)
            ok = result["result"] == "REPORT_PRESENT"
    except DispatchError as exc:
        print(json.dumps({"schema_version": 1, "result": "REJECTED", "reason": exc.code,
                          "detail": exc.detail}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main())
