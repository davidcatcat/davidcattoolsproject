#!/usr/bin/env python3
"""Read-only macOS/Linux process monitor for local AI agent jobs.

Shows OpenCode, Claude Code, Codex and llmtool processes, and the progress of
recent llmtool tasks (stage, model, result, tokens, tool calls with file paths)
read from the harness evidence and each job's own OpenCode database. No model calls,
repository mutation, credentials, or daemon. Network access is off unless --github
is explicitly supplied for read-only Activity headlines. This prototype
reports only OS-observed processes; it does NOT infer model steps or attest
task completion/review decisions. Its Activity view is limited to local task
evidence and does not invent cross-session dispatch, delivery, review or acceptance.

The office view (default screen, key 5) lists every Codex PM / developer /
reviewer / subagent and Claude session / subagent working in this repository,
plus the Roadmap Worker table, one row each with a live state such as 工作中,
待命, 完成, 暫停中 or 停工. It reads session metadata only, never prompts or
model text.

Also lists the other members of every observed agent process group, plus their
descendants, including zombies (state Z). A lingering member after its leader
exited is exactly what makes llmtool report CLEANUP_UNKNOWN. The Claude desktop
app is shown as one line, without its many helper processes.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time

# Intentionally exclude every raw process command line from rendered output.
PS_ARGS = ("/bin/ps", "-A", "-o", "pid=,ppid=,pgid=,etime=,stat=,command=")
LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s*(.*)$")
ALLOWED_STATE = re.compile(r"^[A-Za-z0-9+<>?!]{1,12}$")
MAX_ANCESTRY = 64
IGNORED = frozenset({"grep", "egrep", "fgrep", "pgrep", "ps"})
# Python options that consume the next argument (-c ends option parsing).
PYTHON_VALUE_OPTIONS = frozenset({"-X", "-W", "-Q"})
# Roles whose process groups are not expanded (GUI apps with many helpers).
NO_EXPAND = frozenset({"Claude Desktop", "Antigravity App (AGY)"})


@dataclass(frozen=True)
class Snapshot:
    processes: list
    watched_groups: dict   # pgid -> member PIDs seen while the group was agent-led
    lingering: list        # watched groups whose leader is gone/zombie (ps-based: POSSIBLE)
    hosts: frozenset = frozenset()  # (evidence dir, stage) of live llmtool harness hosts


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    pgid: int
    elapsed: str
    state: str
    role: str
    name: str = "?"   # executable basename only, never arguments
    owner: str = ""   # nearest agent this process belongs to / was started by


SAFE_NAME = re.compile(r"[^A-Za-z0-9._+-]")


def safe_name(command: str) -> str:
    """Executable basename only (no arguments, which may contain secrets)."""
    if command.startswith("(") and command.endswith(")"):
        command = command[1:-1]
    if command.strip() in ("<defunct>", "defunct"):
        return "zombie"  # macOS/Linux show zombies without their original name
    first = command.split()[0] if command.split() else ""
    name = SAFE_NAME.sub("", _base(first).lstrip("-"))[:24]
    return name or "?"


def _base(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _python_target(args: list[str]) -> tuple[str, str] | None:
    """Return ("module"|"script", name) for a python command line, else None."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-m":
            return ("module", args[i + 1]) if i + 1 < len(args) else None
        if arg == "-c" or arg == "-":
            return None
        if arg in PYTHON_VALUE_OPTIONS:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return ("script", _base(arg))
    return None


def _agent_role(exe: str, args: list[str], command: str) -> str | None:
    words = set(args)
    if exe in ("opencode", "opencode.exe"):
        if "serve" in words:
            return "OpenCode SERVER"
        if "run" in words:
            return "OpenCode CLIENT"
        return "OpenCode CLI"
    if exe == "claude":
        return "Claude Code CLI"
    if exe == "Claude":
        # The desktop app's helpers live under "Claude Helper.app".
        return None if "Helper" in command else "Claude Desktop"
    if exe == "agy":
        return "AGY CLI"
    if exe == "Antigravity":
        return None if "Helper" in command else "Antigravity App (AGY)"
    if exe == "codex-code-mode-host":
        return "Codex code-mode host"
    if exe == "codex":
        if "app-server" in words:
            return "Codex daemon" if "daemon" in words else "Codex APP-SERVER"
        return "Codex CLI"
    if exe == "llmtool":
        return "llmtool CLI"
    return None


def classify(command: str) -> str | None:
    """Never return the command text; it might contain secrets."""
    if command.startswith("(") and command.endswith(")"):
        command = command[1:-1]  # zombies are listed as "(name)"
    parts = command.split()
    if not parts:
        return None
    exe, args = _base(parts[0]), parts[1:]
    if exe in IGNORED:
        return None
    if exe == "node" and args:
        # npm launchers: "node .../bin/codex" etc.
        role = _agent_role(_base(args[0]), args[1:], command)
        return role + " (node launcher)" if role else None
    if exe.lower().startswith("python"):
        target = _python_target(args)
        if target == ("module", "llmtool"):
            return "llmtool CLI"
        if target is not None and target[0] == "script":
            name = target[1]
            if name in ("invoke_once.py", "live_once.py"):
                return "llmtool HOST"
            if name == "llmtool":
                return "llmtool CLI"
            if name in ("ai_loop.py", "ai-loop.sh"):
                return "ai-loop controller"
        return None
    return _agent_role(exe, args, command)


def _host_job(command: str) -> tuple[str, str] | None:
    """(evidence directory, stage) of a live invoke_once.py/live_once.py host."""
    parts = command.split()
    if not parts or not _base(parts[0]).lower().startswith("python"):
        return None
    args = parts[1:]
    for i, arg in enumerate(args):
        if _base(arg) in ("invoke_once.py", "live_once.py") and not arg.startswith("-"):
            stage = args[i + 1] if i + 1 < len(args) else ""
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", stage or "-"):
                return None
            return os.path.dirname(os.path.realpath(arg)), stage
    return None


def parse_processes(output: str, self_pid: int, remembered: dict | None = None) -> Snapshot:
    """remembered: {pgid: member PIDs} of agent-led groups seen in earlier polls.

    A leader that exited and was reaped leaves members whose only link is the
    PGID. Only PIDs already seen in that group while it was agent-led count as
    left behind, so an unrelated newer process reusing the PGID is not blamed.
    """
    remembered = remembered or {}
    rows = {}
    hosts = set()
    for line in output.splitlines():
        found = LINE.match(line)
        if found is None:
            continue
        pid, ppid, pgid = (int(v) for v in found.group(1, 2, 3))
        if pid == self_pid or ppid == self_pid:  # this monitor and its own ps
            continue
        elapsed = found.group(4)
        stat = found.group(5)
        role = classify(found.group(6))
        if role == "llmtool HOST":
            job = _host_job(found.group(6))
            if job:
                hosts.add(job)
        rows[pid] = (ppid, pgid,
                     elapsed if len(elapsed) <= 18 else "UNKNOWN",
                     stat if ALLOWED_STATE.fullmatch(stat) else "?",
                     role, safe_name(found.group(6)))
    matched = {pid for pid, row in rows.items()
               if row[4] is not None and row[4] not in NO_EXPAND}
    # Expand a whole process group only when the agent leads it (as llmtool's
    # start_new_session children do); otherwise unrelated apps would flood in.
    groups = {rows[pid][1] for pid in matched if rows[pid][1] == pid}
    orphans = {g: {pid for pid in members if pid in rows and rows[pid][1] == g}
               for g, members in remembered.items() if g not in groups}
    orphans = {g: members for g, members in orphans.items() if members}

    def label(pid: int) -> str:
        return f"{rows[pid][4]} {pid}"

    def agent_ancestor(pid: int) -> int | None:
        seen = 0
        parent = rows[pid][0]
        while parent in rows and seen < MAX_ANCESTRY:
            if parent in matched:
                return parent
            parent = rows[parent][0]
            seen += 1
        return None

    result = []
    for pid, (ppid, pgid, elapsed, stat, role, name) in rows.items():
        ancestor = agent_ancestor(pid)
        if role is None:
            if ancestor is not None:
                role, owner = "  child", label(ancestor)
            elif pgid in groups:
                role, owner = "  group member", label(pgid)
            elif pid in orphans.get(pgid, ()):
                role, owner = "  orphaned group member", f"group {pgid} (leader gone)"
            else:
                continue
        else:
            owner = label(ancestor) if ancestor is not None else ""
        if stat.startswith("Z"):
            role += " (zombie)"
        result.append(Process(pid, ppid, pgid, elapsed, stat, role, name, owner))
    watched = {g: {pid for pid, row in rows.items() if row[1] == g} for g in groups}
    watched.update(orphans)
    lingering = sorted(
        g for g, members in watched.items()
        if any(pid != g for pid in members) and (g not in rows or rows[g][3].startswith("Z")))
    # Group leaders first, then their members, within each process group.
    result.sort(key=lambda p: (p.pgid, p.pid != p.pgid, p.ppid, p.pid))
    return Snapshot(result, watched, lingering, frozenset(hosts))


def collect(remembered: dict | None = None) -> Snapshot:
    response = subprocess.run(PS_ARGS, check=False, capture_output=True, text=True,
                              timeout=4, shell=False)
    if response.returncode:
        raise RuntimeError(f"ps exited with status {response.returncode}")
    return parse_processes(response.stdout, os.getpid(), remembered)


def family(role: str) -> str:
    for name in ("OpenCode", "Claude", "Codex", "llmtool", "ai-loop"):
        if name in role:
            return name
    return "other"


def describe(p: Process) -> str:
    """Human-readable line: what the process is and which agent it belongs to."""
    zombie = ""
    if p.state.startswith("Z"):
        zombie = " (exited, not yet reaped)" if p.name == "zombie" else " [zombie: exited, not yet reaped]"
    if not p.role.startswith(" "):
        started = f"  (started by {p.owner})" if p.owner else ""
        return f"{p.role.replace(' (zombie)', '')}{zombie}  PID {p.pid}{started}"
    kind = p.role.strip().replace(" (zombie)", "")
    relation = {"child": "child of", "group member": "in process group of",
                "orphaned group member": "left behind in"}.get(kind, kind)
    return f"{p.name}{zombie}  PID {p.pid}  ({relation} {p.owner})"


def running_stages(tasks: list) -> int:
    return sum(1 for t in tasks for s in t.stages if stage_state(s) == "RUNNING")


def summary(processes: list[Process], lingering: list) -> str:
    counts: dict[str, int] = {}
    for p in processes:
        if not p.role.startswith(" "):
            counts[family(p.role)] = counts.get(family(p.role), 0) + 1
    text = "  ".join(f"{k} {counts[k]}" for k in ("Claude", "Codex", "OpenCode", "llmtool", "ai-loop", "other")
                     if k in counts) or "no agent process"
    return text + (f"  ·  ⚠ possible leftover groups: {', '.join(map(str, lingering))}" if lingering else "")


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def table_lines(processes: list[Process]) -> list[str]:
    # Owner sits next to PID so it stays visible in an 80-column terminal.
    lines = [f"{'ROLE':<25} {'NAME':<12} {'PID':<7} {'OWNER':<30} {'PGID':<7} {'STATE':<5} ELAPSED"]
    lines += [f"{_fit(p.role, 25):<25} {_fit(p.name, 12):<12} {p.pid:<7} {_fit(p.owner or '-', 30):<30} "
              f"{p.pgid:<7} {p.state:<5} {p.elapsed}" for p in processes]
    return lines


class Monitor:
    """Polling state shared by every view. Never signals or modifies anything.

    Agents are logged as soon as they appear or disappear. Their helper
    processes are logged only once they have lived min_age seconds (or at once
    when they are zombies or left behind in a lingering group); shorter-lived
    helpers are counted and summarised every summary_every seconds.
    """

    def __init__(self, min_age: float = 3.0, show_all: bool = False,
                 summary_every: float = 30.0) -> None:
        self.min_age, self.show_all, self.summary_every = min_age, show_all, summary_every
        self.prev: dict[int, Process] = {}
        self.remembered: dict = {}
        self.hosts: frozenset = frozenset()
        self.processes: list[Process] = []
        self.lingering: list = []
        self.error: str | None = None
        self.polls = 0
        self.first_seen: dict[int, float] = {}
        self.announced: set[int] = set()
        self.short_lived: dict[str, dict[str, int]] = {}
        self.last_summary = time.monotonic()
        self.log: deque[tuple[str, str, str]] = deque(maxlen=2000)  # (time, kind, text)
        self.tasks: list = []
        self.task_map: dict = {}
        self.task_baseline = False   # a successful task scan has adopted the starting point
        self.github = None   # GitHubSource for the Activity view; None means no network at all
        self.office: list = []
        self.office_state: dict = {}

    def _flush_short_lived(self, stamp: str) -> list[tuple[str, str, str]]:
        out = []
        for owner, names in sorted(self.short_lived.items()):
            total = sum(names.values())
            detail = ", ".join(f"{n}×{c}" for n, c in sorted(names.items(), key=lambda kv: -kv[1])[:5])
            out.append((stamp, "i", f"{owner or 'unknown owner'}: {total} short-lived "
                                    f"helper{'s' if total != 1 else ''} ({detail})"))
        self.short_lived.clear()
        return out

    def poll(self) -> list[tuple[str, str, str]]:
        self.polls += 1
        now = time.monotonic()
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            result = collect(self.remembered)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            # On a failed read, do not generate fake departure events.
            message = type(exc).__name__ + ": " + str(exc)[:65]
            new = [] if message == self.error else [(stamp, "?", f"cannot read process table: {message}")]
            self.error = message
            self.log.extend(new)
            return new
        current = {p.pid: p for p in result.processes}
        new: list[tuple[str, str, str]] = []
        if self.polls == 1:
            new.append((stamp, "i", f"monitor started; {summary(result.processes, result.lingering)}"))
        for pid, p in current.items():
            if pid in self.announced:
                continue
            self.first_seen.setdefault(pid, now)
            # A brief zombie window is normal until the parent reaps; only a
            # lingering group (leader gone) is urgent.
            urgent = (self.show_all or self.polls == 1 or not p.role.startswith(" ")
                      or p.pgid in result.lingering)
            if urgent or now - self.first_seen[pid] >= self.min_age:
                self.announced.add(pid)
                new.append((stamp, "+", describe(p)))
        for pid, p in self.prev.items():
            if pid in current:
                continue
            if pid in self.announced:
                new.append((stamp, "-", describe(p) + "  — gone"))
            else:
                counts = self.short_lived.setdefault(p.owner, {})
                key = p.name.lower()
                counts[key] = counts.get(key, 0) + 1
            self.announced.discard(pid)
            self.first_seen.pop(pid, None)
        new += [(stamp, "!", f"process group {g}: POSSIBLE leftover members after the leader "
                             f"is gone/zombie (ps-based; llmtool would report cleanup UNKNOWN)")
                for g in result.lingering if g not in self.lingering]
        new += [(stamp, "ok", f"process group {g}: no members left behind any more")
                for g in self.lingering if g not in result.lingering]
        try:
            tasks = scan_tasks(hosts=result.hosts)
        except Exception:  # task evidence is optional; never break process monitoring
            tasks = self.tasks
            initial_tasks = self.polls == 1
        else:
            # A failed first scan leaves no baseline, so the next successful scan
            # must adopt what it sees instead of replaying historical stages.
            initial_tasks = not self.task_baseline
            self.task_baseline = True
        new += [(stamp, k, t) for k, t in task_events(self.task_map, tasks,
                                                      initial=initial_tasks)]
        if self.polls == 1 and tasks:
            latest = tasks[0]
            last = latest.stages[-1] if latest.stages else None
            new.append((stamp, "i", f"{len(tasks)} recent llmtool task(s); latest {latest.task_id}"
                                    + (f": {last.name} {stage_state(last)}" if last else ": not started")))
        self.tasks, self.task_map = tasks, {t.directory: t for t in tasks}
        try:
            office = office_rows(result.processes)
        except Exception:  # the office view is optional; never break process monitoring
            office = self.office
        state, changes = office_events(self.office_state, office)
        new += [(stamp, kind, text) for kind, text in changes]
        self.office, self.office_state = office, state
        if self.short_lived and now - self.last_summary >= self.summary_every:
            new += self._flush_short_lived(stamp)
            self.last_summary = now
        self.prev, self.remembered, self.hosts = current, result.watched_groups, result.hosts
        self.processes, self.lingering, self.error = result.processes, result.lingering, None
        self.log.extend(new)
        return new


# ---------------------------------------------------------------- task view
# Reads the llmtool foreground-harness evidence layout (manifest.json,
# <stage>-attempt.json, <stage>-result.json) plus each job's private OpenCode
# database, strictly read-only. SQL json_extract returns only identifiers,
# statuses, token counts, tool names and admitted file paths to this process;
# prompts, model text, reasoning and tool output are never returned to Python,
# rendered or logged. A database is opened only when its state root provably
# belongs to the evidence directory (see _verified_db); otherwise UNKNOWN.
import glob
import json
import sqlite3

TASK_GLOB = "/private/tmp/llmtool-*"
STAGE_FILE = re.compile(r"^([A-Za-z0-9_-]{1,32})-attempt\.json$")
CODE_TEXT = re.compile(r"^[A-Z0-9_]{1,64}$")
SHORT_MODEL = re.compile(r"(?:^|/)([a-z0-9]+)[a-z0-9.-]*$")
STATE_ROOT_NAME = re.compile(r"^opencode-[0-9a-f]{32}$")
FINAL_TOOL = frozenset({"completed", "error"})


@dataclass(frozen=True)
class Tool:
    key: str
    name: str
    path: str
    status: str


@dataclass(frozen=True)
class Stage:
    name: str
    model: str
    started: float
    result: str          # "" while running/unknown, else worker result code
    cleanup: str         # "ok", "UNKNOWN" or ""
    steps: int
    tokens_in: int
    tokens_out: int
    idle: str            # native session idle outcome, "" while busy
    tools: tuple
    live: bool = False   # a live llmtool host process for this exact stage is observed
    meta: str = "ok"     # "ok", "none" (no DB yet) or "UNKNOWN (...)" when unverifiable
    result_at: float = 0.0  # local result-record mtime, never a review/acceptance time


@dataclass(frozen=True)
class Task:
    directory: str
    task_id: str
    branch: str
    stages: tuple


def _read_json(path: str, limit: int = 1 << 20):
    try:
        with open(path, "rb") as handle:
            raw = handle.read(limit + 1)
        return json.loads(raw) if len(raw) <= limit else None
    except (OSError, ValueError):
        return None


def _model_short(model: str) -> str:
    found = SHORT_MODEL.search(model or "")
    return found.group(1) if found else "?"


def _relative(path: str, root: str) -> str:
    if root and path.startswith(root.rstrip("/") + "/"):
        path = path[len(root.rstrip("/")) + 1:]
    elif os.path.isabs(path):
        # A task database path outside its evidence root is not a safe reference;
        # never render an unredacted home or other absolute path.
        return "?"
    return SAFE_PATH.sub("", path)[:80] or "?"


def _safe_relative_reference(path: str) -> str:
    if os.path.isabs(path) or ".." in path.split("/"):
        return "?"
    return SAFE_PATH.sub("", path)[:80] or "?"


SAFE_PATH = re.compile(r"[^A-Za-z0-9._/+-]")


def _verified_db(state_root: str, directory: str) -> tuple[str | None, str]:
    """Path of the job's own OpenCode DB, or (None, reason).

    The attempt file is only a hint: the state root must be a direct
    opencode-<32 hex> child of this evidence directory with no symlink in
    between, and the DB must be a regular file owned by this user. Anything else
    (for example a hint pointing at the Owner's own OpenCode DB) is UNKNOWN.
    """
    if not state_root:
        return None, "none"
    try:
        real_dir = os.path.realpath(directory)
        real_root = os.path.realpath(state_root)
        if (os.path.normpath(state_root) != real_root or os.path.dirname(real_root) != real_dir
                or not STATE_ROOT_NAME.fullmatch(os.path.basename(real_root))):
            return None, "UNKNOWN (state root not owned by this task)"
        db_path = os.path.join(real_root, "data", "opencode", "opencode.db")
        if not os.path.lexists(db_path):
            return None, "none"
        info = os.lstat(db_path)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or os.path.realpath(db_path) != db_path or info.st_nlink != 1):
            return None, "UNKNOWN (unverified task database)"
        return db_path, "ok"
    except OSError:
        return None, "UNKNOWN (task database unreadable)"


TOKENS_SQL = ("SELECT json_extract(data,'$.tokens.input'), json_extract(data,'$.tokens.output'), "
              "json_extract(data,'$.tokens.reasoning'), json_extract(data,'$.tokens.cache.read') "
              "FROM session_message WHERE type='assistant' AND json_valid(data) ORDER BY seq")
TOOLS_SQL = ("SELECT json_extract(j.value,'$.id'), json_extract(j.value,'$.name'), "
             "json_extract(j.value,'$.state.status'), json_extract(j.value,'$.state.input.path') "
             "FROM (SELECT seq, data FROM session_message WHERE type='assistant' AND json_valid(data)) m, "
             "json_each(m.data,'$.content') j "
             "WHERE json_extract(j.value,'$.type')='tool' ORDER BY m.seq, j.key")


def _int(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def _native(state_root: str, root: str, directory: str) -> tuple:
    """(steps, tokens_in, tokens_out, idle_outcome, tools, meta) from the job's own DB."""
    db_path, meta = _verified_db(state_root, directory)
    if db_path is None:
        return 0, 0, 0, "", (), meta
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2)
    except sqlite3.Error:
        return 0, 0, 0, "", (), "UNKNOWN (task database unreadable)"
    try:
        steps = tin = tout = 0
        idle = ""
        for (outcome,) in db.execute("SELECT idle_outcome FROM session_v2 ORDER BY time_created"):
            idle = outcome if isinstance(outcome, str) and CODE_TEXT.fullmatch(outcome.upper()) else idle
        for i, o, r, c in db.execute(TOKENS_SQL):
            steps += 1
            tin += _int(i) + _int(c)
            tout += _int(o) + _int(r)
        tools = []
        for key, name, status, path in db.execute(TOOLS_SQL):
            tools.append(Tool(SAFE_NAME.sub("", str(key or len(tools)))[:64],
                              SAFE_NAME.sub("", str(name or "?"))[:16] or "?",
                              _relative(str(path or ""), root),
                              SAFE_NAME.sub("", str(status or "?"))[:12] or "?"))
        return steps, tin, tout, idle, tuple(tools), "ok"
    except sqlite3.Error:
        return 0, 0, 0, "", (), "UNKNOWN (task database unreadable)"
    finally:
        db.close()


def scan_tasks(pattern: str = TASK_GLOB, window_hours: float = 12.0,
               hosts: frozenset = frozenset()) -> list[Task]:
    """Recently active llmtool harness evidence directories, newest first.

    hosts: (evidence dir, stage) pairs of live harness processes; a stage
    without a result is RUNNING only when its own host is observed alive.
    """
    cutoff = time.time() - window_hours * 3600
    tasks = []
    for directory in glob.glob(pattern):
        manifest_path = os.path.join(directory, "manifest.json")
        try:
            if not os.path.isfile(manifest_path) or os.path.getmtime(directory) < cutoff:
                continue
        except OSError:
            continue
        manifest = _read_json(manifest_path) or {}
        root = str(manifest.get("root") or "")
        stages = []
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for entry in names:
            found = STAGE_FILE.match(entry)
            if not found:
                continue
            stage = found.group(1)
            attempt_path = os.path.join(directory, entry)
            attempt = _read_json(attempt_path) or {}
            result_path = os.path.join(directory, f"{stage}-result.json")
            result = _read_json(result_path) or {}
            try:
                result_at = os.path.getmtime(result_path) if result else 0.0
            except OSError:
                result_at = 0.0
            code = str(result.get("code") or "")
            code = code if CODE_TEXT.fullmatch(code) else ("?" if result else "")
            cleanup = "" if not result else ("ok" if result.get("cleanup_confirmed") is True else "UNKNOWN")
            state_root = str(attempt.get("state_root") or "")
            steps, tin, tout, idle, tools, meta = _native(state_root, root, directory)
            live = (os.path.realpath(directory), stage) in hosts
            try:
                started = os.path.getmtime(attempt_path)
            except OSError:
                started = 0.0
            stages.append(Stage(stage, _model_short(str(attempt.get("model") or "")), started,
                                code, cleanup, steps, tin, tout, idle, tools, live, meta, result_at))
        stages.sort(key=lambda s: s.started)
        task_id = SAFE_NAME.sub("", str(manifest.get("task_id") or os.path.basename(directory)))[:48]
        branch = SAFE_PATH.sub("", str(manifest.get("branch") or ""))[:60]
        tasks.append(Task(directory, task_id or "?", branch, tuple(stages)))
    tasks.sort(key=lambda t: max([s.started for s in t.stages] or [0]), reverse=True)
    return tasks


def stage_state(stage: Stage) -> str:
    """Never claims liveness from missing data: RUNNING needs an observed host."""
    if stage.result:
        return stage.result
    if stage.live:
        return "RUNNING"
    return "UNKNOWN (no result, no live host)"


def task_events(old: dict, new: list[Task], *, initial: bool = False) -> list[tuple[str, str]]:
    """(kind, text) for new stages, new tool calls and finished stages."""
    out = []
    for task in new:
        before = old.get(task.directory)
        known = {s.name: s for s in before.stages} if before else {}
        for stage in task.stages:
            prior = known.get(stage.name)
            if before is None and initial:
                continue  # first sight of a task: summarised, not replayed
            if prior is None:
                out.append(("▶", f"{task.task_id}: stage {stage.name} started ({stage.model})"))
            # Report a tool call once, when it has a final status (its path and
            # outcome are only known then); in-flight calls are not logged yet.
            seen = {t.key for t in prior.tools if t.status in FINAL_TOOL} if prior else set()
            for tool in stage.tools:
                if tool.key not in seen and tool.status in FINAL_TOOL:
                    out.append(("→", f"{task.task_id} {stage.name}: {tool.name} {tool.path}"
                                     f"{'' if tool.status == 'completed' else ' [' + tool.status + ']'}"))
            if stage.result and (prior is None or not prior.result):
                kind = "■" if stage.result.endswith("RETURNED") and stage.cleanup == "ok" else "!"
                out.append((kind, f"{task.task_id}: stage {stage.name} finished: {stage.result}, "
                                  f"cleanup {stage.cleanup}, {stage.steps} steps, "
                                  f"{stage.tokens_in / 1000:.1f}k in / {stage.tokens_out / 1000:.1f}k out"))
    return out


def task_lines(tasks: list[Task], limit: int = 6) -> list[str]:
    if not tasks:
        return ["No recent llmtool task evidence under /private/tmp/llmtool-* (last 12 h)."]
    lines = []
    for task in tasks[:limit]:
        lines.append(f"TASK {task.task_id}   branch {task.branch or '-'}")
        if not task.stages:
            lines.append("    (prepared, no model stage started yet)")
        for stage in task.stages:
            when = datetime.fromtimestamp(stage.started).strftime("%H:%M:%S") if stage.started else "--:--:--"
            lines.append(f"    {when}  {stage.name:<8} {stage.model:<9} {stage_state(stage):<28} "
                         f"cleanup {stage.cleanup or '-':<7} steps {stage.steps:<3} "
                         f"{stage.tokens_in / 1000:.1f}k in / {stage.tokens_out / 1000:.1f}k out")
            if stage.meta not in ("ok", "none"):
                lines.append(f"        task metadata {stage.meta}")
            for tool in stage.tools[-6:]:
                mark = "✓" if tool.status == "completed" else tool.status
                lines.append(f"        {tool.name:<6} {_safe_relative_reference(tool.path)}  {mark}")
            if len(stage.tools) > 6:
                lines.append(f"        … {len(stage.tools) - 6} earlier tool calls")
        lines.append("")
    return lines


@dataclass(frozen=True)
class ActivityEvent:
    """A small, typed event made only from sanitized monitor evidence."""
    occurred_at: float
    event_type: str
    task_id: str
    stage: str
    status: str
    reference: str
    cleanup: str = ""
    actor: str = ""      # optional allowlisted actor/target (GitHub activity markers)
    target: str = ""
    summary: str = ""    # sanitized one-line GitHub headline/title, never a body


ACTIVITY_TYPES = frozenset({
    "review_dispatch", "review_verdict", "undelivered_reply", "waiting_owner",
    "worker_result", "stage_attempt", "stage_live", "stage_unknown",
    "github_comment", "pr_opened", "pr_merged", "pr_closed",
    "developer_dispatch", "developer_progress", "test_result",
})
ACTIVITY_STATUS = {
    "review_dispatch": frozenset({"DISPATCHED", "UNKNOWN"}),
    "review_verdict": frozenset({"PASS", "FAIL", "FIX", "UNKNOWN"}),
    "undelivered_reply": frozenset({"NOT_DELIVERED", "UNKNOWN"}),
    "waiting_owner": frozenset({"BLOCKED", "WAITING", "UNKNOWN"}),
    "worker_result": None,
    "stage_attempt": frozenset({"OBSERVED"}),
    "stage_live": frozenset({"RUNNING"}),
    "stage_unknown": frozenset({"UNKNOWN"}),
    "github_comment": frozenset({"POSTED"}),
    "pr_opened": frozenset({"OPENED"}),
    "pr_merged": frozenset({"MERGED"}),
    "pr_closed": frozenset({"CLOSED"}),
    "developer_dispatch": frozenset({"ASSIGNED", "UNKNOWN"}),
    "developer_progress": frozenset({"ACTIVE", "BLOCKED", "WAITING", "UNKNOWN"}),
    "test_result": frozenset({"PASS", "FAIL", "UNKNOWN"}),
}
ACTIVITY_RESULT_CODES = frozenset({
    "OPENCODE_OUTPUT_RETURNED", "OPENCODE_PROVIDER_FAILED", "OPENCODE_CLEANUP_UNKNOWN",
    "OPENCODE_RUNTIME_UNKNOWN", "OPENCODE_SOURCE_UNKNOWN", "OPENCODE_BUDGET_BLOCKED",
    "OPENCODE_AUTH_REQUIRED", "OPENCODE_NOT_AUTHORIZED", "OPENCODE_TIMEOUT",
    "OPENCODE_OUTPUT_INCOMPLETE", "OPENCODE_OUTPUT_INVALID", "OPENCODE_USAGE_UNKNOWN",
    "RETURNED", "FAILED", "BLOCKED", "UNKNOWN",
})
ACTIVITY_RESULT_LABELS = {
    "OPENCODE_OUTPUT_RETURNED": "完成，已拿到輸出",
    "OPENCODE_PROVIDER_FAILED": "失敗：模型呼叫失敗",
    "OPENCODE_CLEANUP_UNKNOWN": "結果未知：程序清理沒確認",
    "OPENCODE_RUNTIME_UNKNOWN": "結果未知：執行狀態沒確認",
    "OPENCODE_SOURCE_UNKNOWN": "結果未知：原始碼狀態沒確認",
    "OPENCODE_BUDGET_BLOCKED": "被擋下：超出預算",
    "OPENCODE_AUTH_REQUIRED": "被擋下：缺少登入/授權",
    "OPENCODE_NOT_AUTHORIZED": "被擋下：沒有授權",
    "OPENCODE_TIMEOUT": "逾時",
    "OPENCODE_OUTPUT_INCOMPLETE": "輸出不完整",
    "OPENCODE_OUTPUT_INVALID": "輸出無效",
    "OPENCODE_USAGE_UNKNOWN": "結果未知：用量沒確認",
    "RETURNED": "完成，已拿到結果",
    "FAILED": "失敗",
    "BLOCKED": "被擋下",
    "UNKNOWN": "結果未知",
}
# Marker actors/targets are finite per event type; invalid values fall back to generic comments.
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,48}")
STAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
LOCAL_TYPES = frozenset({"worker_result", "stage_attempt", "stage_live", "stage_unknown"})


def activity_events(tasks: list[Task]) -> list[ActivityEvent]:
    """Build time-ordered Activity from existing local task evidence only.

    Cross-session dispatch/verdict/reply events never come from here: no local
    source proves them. They appear only from the opt-in GitHub source below.
    """
    events = []
    for task in tasks:
        task_id = task.task_id if TASK_ID.fullmatch(task.task_id or "") else "?"
        for stage in task.stages:
            stage_name = stage.name if STAGE_ID.fullmatch(stage.name or "") else "?"
            reference = f"{task_id}/{stage_name}"
            if stage.started:
                events.append(ActivityEvent(stage.started, "stage_attempt", task_id,
                                            stage_name, "OBSERVED", reference))
            if stage.result and stage.result_at:
                events.append(ActivityEvent(stage.result_at, "worker_result", task_id,
                                            stage_name,
                                            stage.result if stage.result in ACTIVITY_RESULT_CODES else "UNKNOWN",
                                            reference,
                                            stage.cleanup if stage.cleanup in ("ok", "UNKNOWN") else "UNKNOWN"))
            elif not stage.result:
                kind, status = (("stage_live", "RUNNING") if stage.live else
                                ("stage_unknown", "UNKNOWN"))
                events.append(ActivityEvent(stage.started, kind, task_id, stage_name,
                                            status, reference))
    events = [event for event in events
              if event.event_type in ACTIVITY_TYPES
              and (event.status in ACTIVITY_STATUS[event.event_type]
                   if ACTIVITY_STATUS[event.event_type] is not None
                   else bool(CODE_TEXT.fullmatch(event.status)))]
    return sorted(events, key=lambda event: (event.occurred_at, event.reference,
                                              event.event_type, event.stage, event.status))


# ------------------------------------------------------------ GitHub source
# Opt-in with --github. Used only by Activity/--once. Read-only `gh api` GETs of recent issue/PR comments
# and pull requests; nothing is posted, cached on disk or sent anywhere else.
# Only a sanitized first line (or PR title) is kept, never a comment body.
# Cross-session events are typed ONLY from explicit marker lines an agent writes
# at the very top of a comment (a marker quoted later in the text is ignored):
#   <!-- activity: type=undelivered_reply actor=Claude target=PM status=NOT_DELIVERED -->
#   <!-- activity: type=developer_dispatch actor=PM target=Codex status=ASSIGNED -->
#   <!-- activity: type=developer_progress actor=OpenCode target=Reviewer status=WAITING -->
#   <!-- activity: type=test_result actor=Codex target=Tests status=PASS -->
# Unmarked comments are shown as plain "GitHub 留言", with no inferred actor.
GITHUB_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
MARKER = re.compile(r"<!--\s*activity:([^<>]{0,300}?)-->")
MARKER_LIKE = re.compile(r"^<!--\s*activity\b")
MARKER_FIELD = re.compile(r"([a-z_]{1,16})=([A-Za-z0-9_-]{1,32})\Z")
MAX_MARKERS = 4
MARKER_TYPES = frozenset({
    "review_dispatch", "review_verdict", "undelivered_reply", "waiting_owner",
    "developer_dispatch", "developer_progress", "test_result",
})
MARKER_FIELDS = frozenset({"type", "actor", "target", "status"})
MARKER_ACTOR_TARGETS = {
    "review_dispatch": (frozenset({"PM", "Integrator", "Reviewer", "Claude", "Codex", "OpenCode"}),
                        frozenset({"PM", "Integrator", "Reviewer", "Claude", "Codex", "OpenCode"})),
    "review_verdict": (frozenset({"Reviewer", "Claude", "Codex", "OpenCode", "AGY"}),
                       frozenset({"PM", "Integrator"})),
    "undelivered_reply": (frozenset({"Reviewer", "Claude", "Codex", "OpenCode", "AGY"}),
                          frozenset({"PM", "Integrator"})),
    "waiting_owner": (frozenset({"Integrator", "PM"}), frozenset({"Owner"})),
    "developer_dispatch": (frozenset({"PM", "Integrator"}),
                           frozenset({"Codex", "OpenCode", "CompanyGW"})),
    "developer_progress": (frozenset({"Codex", "OpenCode", "CompanyGW"}),
                            frozenset({"PM", "Integrator", "Reviewer", "Owner", "CI"})),
    "test_result": (frozenset({"Codex", "OpenCode", "CompanyGW"}),
                    frozenset({"Tests", "CI"})),
}
MARKER_NAMES = {"CompanyGW": "company gw", "Tests": "tests"}
# Redact every POSIX absolute path, including mounted volumes and system paths.
# URLs are removed first, so their scheme slashes do not reach this pattern.
PRIVATE_PATH = re.compile(r"(?<![\w])/(?:[^\s/]+/)*[^\s/]*")
# Credential-like or opaque strings are hidden even in a one-line headline.
SECRETISH = re.compile(
    r"(?i)(?:gh[pousr]_[A-Za-z0-9]{8,}|github_pat_\w{8,}|sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{8,}"
    r"|xox[abpr]-[A-Za-z0-9-]{8,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9._-]+"
    r"|\b(?:bearer|basic)\s+\S+"
    r"|\b(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|credential)s?\s*[:=]\s*\S+"
    r"|\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_-]{40,}={0,2})")
# Markers are honoured only from accounts with write access; on a shared account
# they remain the poster's own claim, so typed lines are labelled 自述.
TRUSTED_ASSOCIATION = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
URL = re.compile(r"https?://\S+")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
GITHUB_TIMEOUT = 20
DEFAULT_GITHUB_REPO = "davidcatcat/davidcattoolsproject"
GITHUB_REFRESH = 30.0
GITHUB_LIMIT = 4 << 20


def _github_time(value) -> float:
    if type(value) is not str:
        return 0.0
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _headline(text) -> str:
    """First meaningful line of untrusted Markdown, reduced to plain short text."""
    if type(text) is not str:
        return ""
    safe_lines = []
    for line in text.splitlines():
        candidate = line.strip()
        while candidate.startswith(">"):
            candidate = candidate[1:].lstrip()
        if MARKER_LIKE.match(candidate):
            continue
        safe_lines.append(line)
    text = "\n".join(safe_lines)
    text = MARKER.sub("", text)
    for line in text.splitlines():
        line = MD_LINK.sub(r"\1", line)
        line = PRIVATE_PATH.sub("[路徑]", URL.sub("[連結]", line))
        line = SECRETISH.sub("[已遮蔽]", line)
        line = "".join(char for char in line if char.isprintable())
        line = re.sub(r"[#*`>_|~]+", " ", line)
        line = " ".join(line.split())
        if line:
            return line[:72] + ("…" if len(line) > 72 else "")
    return ""


def _issue_number(url) -> str:
    found = re.search(r"/(?:issues|pull)/(\d{1,7})$", url) if type(url) is str else None
    return found.group(1) if found else ""


def _marker_events(body: str, when: float, number: str) -> list[ActivityEvent]:
    """Type only complete, authorized marker blocks; any invalid marker fails closed."""
    leading = []
    for line in body.splitlines() if type(body) is str else []:
        # Marker lines must start in column zero. Stripping indentation would
        # turn a Markdown code example into a live activity claim.
        found = MARKER.fullmatch(line)
        if found is None:
            if MARKER_LIKE.match(line.lstrip()):
                return []
            break
        leading.append(found)
    if not leading:
        return []
    if len(leading) > MAX_MARKERS:
        return []
    events = []
    for found in leading:
        fields = {}
        for token in found.group(1).split():
            match = MARKER_FIELD.fullmatch(token)
            if match is None or match.group(1) in fields:
                return []
            fields[match.group(1)] = match.group(2)
        if set(fields) != MARKER_FIELDS:
            return []
        kind, actor, target, status = (fields[key] for key in ("type", "actor", "target", "status"))
        if kind not in MARKER_TYPES:
            return []
        if status not in ACTIVITY_STATUS[kind]:
            return []
        allowed_actors, allowed_targets = MARKER_ACTOR_TARGETS[kind]
        if actor not in allowed_actors or target not in allowed_targets:
            return []
        if kind == "review_dispatch" and actor not in {"PM", "Integrator"}:
            return []
        if actor == target:
            return []
        events.append(ActivityEvent(when, kind, "", "", status, f"#{number}",
                                    actor=actor, target=target, summary=_headline(body)))
    return events


def github_events(comments, pulls, since: float) -> list[ActivityEvent]:
    """Turn already-fetched GitHub JSON into typed events (pure; no I/O)."""
    events = []
    for comment in comments if type(comments) is list else []:
        if type(comment) is not dict:
            continue
        when = _github_time(comment.get("created_at"))
        number = _issue_number(comment.get("issue_url"))
        if when < since or not number:
            continue
        body = comment.get("body")
        association = comment.get("author_association")
        trusted = type(association) is str and association in TRUSTED_ASSOCIATION
        typed = _marker_events(body, when, number) if trusted else []
        if not typed:
            headline = _headline(body)
            typed = [ActivityEvent(when, "github_comment", "", "", "POSTED", f"#{number}",
                                   summary=headline)]
        events.extend(typed)
    for pull in pulls if type(pulls) is list else []:
        if type(pull) is not dict or type(pull.get("number")) is not int:
            continue
        ref, title = f"#{pull['number']}", _headline(pull.get("title"))
        merged, closed = _github_time(pull.get("merged_at")), _github_time(pull.get("closed_at"))
        opened = _github_time(pull.get("created_at"))
        if opened >= since:
            events.append(ActivityEvent(opened, "pr_opened", "", "", "OPENED", ref, summary=title))
        if merged >= since:
            events.append(ActivityEvent(merged, "pr_merged", "", "", "MERGED", ref, summary=title))
        elif closed >= since:
            events.append(ActivityEvent(closed, "pr_closed", "", "", "CLOSED", ref, summary=title))
    return events


class GitHubSource:
    """Rate-limited, read-only GitHub activity. Failures become a status line."""

    def __init__(self, repo: str, window_hours: float = 12.0, runner=None, clock=time.time) -> None:
        if not GITHUB_REPO.fullmatch(repo or ""):
            raise ValueError("--github expects OWNER/REPO")
        self.repo, self.window = repo, window_hours * 3600
        self.runner = runner or subprocess.run
        self.clock = clock
        self.events: list[ActivityEvent] = []
        self.status = "GitHub：尚未讀取"
        self.fetched = None   # monotonic time of the last attempt

    def _get(self, path: str):
        gh = shutil.which("gh")
        if not gh:
            raise RuntimeError("找不到 gh 指令")
        done = self.runner([gh, "api", "-X", "GET", path], capture_output=True,
                           timeout=GITHUB_TIMEOUT, check=False)
        if done.returncode != 0:
            raise RuntimeError(f"gh api 失敗（exit {done.returncode}）")
        if len(done.stdout) > GITHUB_LIMIT:
            raise RuntimeError("回應過大")
        return json.loads(done.stdout)

    def refresh_in_background(self) -> None:
        """Start one daemon fetch when due, so the screen never waits on the network."""
        thread = getattr(self, "_thread", None)
        if thread is not None and thread.is_alive():
            return
        if self.fetched is not None and time.monotonic() - self.fetched < GITHUB_REFRESH:
            return
        self.fetched = time.monotonic()
        self._thread = threading.Thread(target=self.refresh, kwargs={"force": True},
                                        name="monitor-github", daemon=True)
        self._thread.start()

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self.fetched is not None and now - self.fetched < GITHUB_REFRESH:
            return
        self.fetched = now
        since = self.clock() - self.window
        stamp = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            comments = self._get(f"repos/{self.repo}/issues/comments?since={stamp}"
                                 "&sort=created&direction=desc&per_page=50")
            pulls = self._get(f"repos/{self.repo}/pulls?state=all&sort=updated"
                              "&direction=desc&per_page=30")
            self.events = github_events(comments, pulls, since)   # one atomic swap
            self.status = (f"GitHub {self.repo}：最近 {int(self.window // 3600)} 小時，"
                           f"{datetime.now():%H:%M:%S} 更新（每 {int(GITHUB_REFRESH)} 秒）")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            # Keep the previous events; say plainly that they may be stale.
            detail = str(exc)[:40] if isinstance(exc, RuntimeError) else type(exc).__name__
            self.status = f"GitHub：讀取失敗（{detail}），以下 GitHub 項目可能過時"


def _when(seconds: float) -> str:
    if not seconds:
        return "--:--:--"
    moment = datetime.fromtimestamp(seconds)
    return moment.strftime("%H:%M:%S" if moment.date() == datetime.now().date() else "%m/%d %H:%M")


def _activity_line(event: ActivityEvent) -> str:
    stage_target = None
    if event.event_type in LOCAL_TYPES:
        safe_task = event.task_id if TASK_ID.fullmatch(event.task_id or "") else "?"
        safe_stage = event.stage if STAGE_ID.fullmatch(event.stage or "") else "?"
        stage_target = f"任務 {safe_task} · 階段 {safe_stage}"
    actors = {
        "review_dispatch": ("PM", "Reviewer", {"DISPATCHED": "派出審查任務", "UNKNOWN": "派送狀態未知"}),
        "review_verdict": ("Reviewer", "PM", {"PASS": "審查通過", "FIX": "要求修正後再通過",
                                              "FAIL": "審查未通過", "UNKNOWN": "審查結果未知"}),
        "undelivered_reply": ("Reviewer", "PM", {"NOT_DELIVERED": "回覆沒送到（改看 GitHub）",
                                                 "UNKNOWN": "回覆有沒有送到：未知"}),
        "waiting_owner": ("Integrator", "Owner", {"BLOCKED": "卡住，等你決定", "WAITING": "等你決定",
                                                  "UNKNOWN": "狀態未知"}),
        "developer_dispatch": ("PM", "Codex", {"ASSIGNED": "自述已指派 Developer 任務",
                                                  "UNKNOWN": "任務指派狀態未知"}),
        "developer_progress": ("Codex", "PM", {"ACTIVE": "回報進度：處理中（不代表模型正在生成）",
                                                  "WAITING": "等待下一步",
                                                  "BLOCKED": "受阻，等待協助",
                                                  "UNKNOWN": "工作狀態未知"}),
        "test_result": ("Codex", "Tests", {"PASS": "自述測試通過（不代表審查或接受）",
                                             "FAIL": "自述測試失敗",
                                             "UNKNOWN": "測試結果未知"}),
        "worker_result": ("llmtool", stage_target, ACTIVITY_RESULT_LABELS),
        "stage_attempt": ("llmtool", stage_target, {"OBSERVED": "開始一次執行嘗試"}),
        "stage_live": ("llmtool", stage_target, {"RUNNING": "執行中（看得到對應程序，不代表快完成）"}),
        "stage_unknown": ("llmtool", stage_target, {"UNKNOWN": "狀態未知（沒有結果，也沒看到程序）"}),
        "github_comment": ("GitHub", None, {"POSTED": "新留言"}),
        "pr_opened": ("GitHub", None, {"OPENED": "開了 PR"}),
        "pr_merged": ("GitHub", None, {"MERGED": "PR 已合併"}),
        "pr_closed": ("GitHub", None, {"CLOSED": "PR 已關閉（沒合併）"}),
    }
    if event.event_type not in ACTIVITY_TYPES:
        return ""
    allowed = ACTIVITY_STATUS[event.event_type]
    if (allowed is not None and event.status not in allowed) or (
            event.event_type == "worker_result" and event.status not in ACTIVITY_RESULT_CODES):
        return ""
    actor, target, outcomes = actors[event.event_type]
    if event.event_type in MARKER_TYPES:
        allowed_actors, allowed_targets = MARKER_ACTOR_TARGETS[event.event_type]
        if (event.actor not in allowed_actors or event.target not in allowed_targets
                or event.actor == event.target
                or event.status not in ACTIVITY_STATUS[event.event_type]):
            return ""
        if not re.fullmatch(r"#\d{1,7}", event.reference or ""):
            return ""
        actor = MARKER_NAMES.get(event.actor, event.actor)
        target = MARKER_NAMES.get(event.target, event.target)
    if event.event_type == "developer_progress":
        outcomes = {
            "ACTIVE": f"回報進度：處理中（下一步通知 {target}；不代表模型正在生成）",
            "WAITING": f"等待 {target} 的下一步",
            "BLOCKED": f"受阻，等待 {target} 協助",
            "UNKNOWN": f"工作狀態未知；下一步交由 {target}",
        }
    ref = event.reference if re.fullmatch(r"#\d{1,7}", event.reference or "") else ""
    outcome = outcomes[event.status]
    if event.event_type == "worker_result":
        outcome += "；程序已清乾淨" if event.cleanup == "ok" else "；程序清理沒確認"
    who = f"{actor} → {target}" if target else actor
    if ref and target is None:
        who = f"{actor} {ref}"
    elif ref and event.event_type in MARKER_TYPES:
        outcome += f"（{ref}・自述）"
    elif ref:
        outcome += f"（{ref}）"
    summary = _headline(event.summary) if event.summary else ""
    line = f"{_when(event.occurred_at)}  {who}：{outcome}"
    return f"{line} — {summary}" if summary else line


def render_activity(events: list[ActivityEvent], limit: int = 60,
                    github: "GitHubSource | None" = None) -> list[str]:
    events = [event for event in events if type(event) is ActivityEvent]
    if github is not None:
        events += [event for event in github.events if type(event) is ActivityEvent]
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.reference,
                                                 event.event_type, event.stage, event.status))
    lines = [line for line in (_activity_line(event) for event in ordered[-max(0, limit):]) if line]
    if not lines:
        lines.append("目前沒有可顯示的動態。")
    if github is None:
        lines.append("GitHub 動態未啟用（預設零網路；加 --github 才讀取），跨工作階段的標記沒有顯示。")
    else:
        lines.append(github.status + "；「自述」= 有權限的發文者自己標記，未經驗證。")
    return lines


def activity_lines(tasks: list[Task], limit: int = 60,
                   github: "GitHubSource | None" = None, background: bool = False) -> list[str]:
    if github is not None:
        github.refresh_in_background() if background else github.refresh()
    return render_activity(activity_events(tasks), limit, github)


# ---------------------------------------------------------------- office view
# One row per worker / agent / subagent, like looking across an office floor:
# who is working, idle, paused or stopped, and on what. Local and read-only.
# Only session *metadata* is read: Codex `session_meta` (cwd, subagent
# nickname and agent_path) plus task_started/task_complete event types; the
# Claude `ai-title` line and `cwd`; Claude subagent `.meta.json` descriptions;
# the dated Worker table in the Roadmap. Prompts, model text, reasoning and
# tool input/output are never returned or rendered. Labels are reduced with the
# same redaction as Activity headlines and private paths are never shown.
import unicodedata

CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")
CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
SCRIPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICE_WINDOW = 6 * 3600       # sessions untouched for longer are left out
OFFICE_DONE_WINDOW = 30 * 60   # finished subagent work stays visible this long
OFFICE_BUSY = 90.0             # seconds since the last write that still mean "working"
OFFICE_STALL = 15 * 60         # started but silent for longer: 無回應
OFFICE_HEAD = 1 << 20
OFFICE_TAIL = 512 << 10
AGENT_PATH = re.compile(r"[A-Za-z0-9_-]{1,48}")
NICKNAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,23}")
WORKING, IDLE, DONE, STALLED, PAUSED, STOPPED, UNCHECKED = (
    "工作中", "待命", "完成", "無回應", "暫停中", "停工", "未確認")


@dataclass(frozen=True)
class OfficeRow:
    name: str
    state: str
    detail: str
    age: float | None   # seconds since last observed activity; None = not observed
    order: int
    key: str = ""       # identity other rows can name as their group
    group: str = ""     # key of the agent that started this one ("" = top level)
    kind: str = "agent"   # agent | subagent | worker


def canonical_root(start: str = SCRIPT_ROOT) -> str:
    """The canonical checkout, also when the monitor runs from a linked worktree."""
    marker = os.path.join(start, ".git")
    try:
        if os.path.isfile(marker):
            with open(marker, encoding="utf-8") as handle:
                found = re.match(r"gitdir:\s*(.+)", handle.read(4096))
            if found:
                common = os.path.realpath(os.path.join(start, found.group(1).strip()))
                # <canonical>/.git/worktrees/<name> -> <canonical>
                return os.path.dirname(os.path.dirname(os.path.dirname(common)))
    except OSError:
        pass
    return os.path.realpath(start)


def _place(cwd, canonical: str) -> tuple[str, str] | None:
    """('pm'|'task', task id) for a cwd inside this repository's roots, else None."""
    if type(cwd) is not str or not cwd:
        return None
    real = os.path.realpath(cwd)
    if real == canonical or real.startswith(canonical + os.sep):
        return "pm", ""
    parts = real.split(os.sep)
    name = os.path.basename(canonical)
    for i in range(len(parts) - 2):
        if parts[i] == "llmtool-dev-workspaces" and parts[i + 1] == name:
            rest = parts[i + 2:]
            if len(rest) >= 2 and rest[0] == "tasks" and AGENT_PATH.fullmatch(rest[1]):
                return "task", rest[1]
            if rest and AGENT_PATH.fullmatch(rest[0]):
                return "task", rest[0]
    return None


def _jsonl_head(path: str):
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.readline(OFFICE_HEAD))
    except (OSError, ValueError):
        return None


def _jsonl_tail(path: str) -> list:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - OFFICE_TAIL))
            data = handle.read(OFFICE_TAIL)
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > OFFICE_TAIL:
        lines = lines[1:]   # the first line is cut
    out = []
    for raw in lines:
        if raw.strip():
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if type(item) is dict:
                out.append(item)
    return out


def _label(text, limit: int = 40) -> str:
    line = _headline(text) if type(text) is str else ""
    return line[:limit] + ("…" if len(line) > limit else "")


def _humanize(path_stem: str) -> str:
    return " ".join(path_stem.replace("-", "_").split("_"))


def _codex_row(path: str, now: float, canonical: str):
    meta = _jsonl_head(path)
    payload = meta.get("payload") if type(meta) is dict else None
    if type(payload) is not dict:
        return None
    place = _place(payload.get("cwd"), canonical)
    if place is None:
        return None
    try:
        age = max(0.0, now - os.path.getmtime(path))
    except OSError:
        return None
    last = None
    for item in _jsonl_tail(path):
        inner = item.get("payload")
        kind = inner.get("type") if type(inner) is dict else None
        if item.get("type") == "event_msg" and kind in ("task_started", "task_complete",
                                                       "turn_aborted"):
            last = kind
    subagent = payload.get("thread_source") == "subagent"
    if last in ("task_complete", "turn_aborted"):
        state = DONE if subagent else IDLE
    elif last == "task_started":
        state = WORKING if age <= OFFICE_STALL else STALLED
    else:
        state = WORKING if age <= OFFICE_BUSY else IDLE
    if subagent:
        # Codex names a spawned agent "/root/<short_task_name>"; anything else
        # could carry a private path, so it is shown as unnamed.
        found = re.fullmatch(r"/root/([A-Za-z0-9_-]{1,48})", str(payload.get("agent_path") or ""))
        stem = found.group(1) if found else ""
        nick = payload.get("agent_nickname")
        nick = nick if type(nick) is str and NICKNAME.fullmatch(nick) else ""
        role = ("reviewer" if "review" in stem.lower() else
                "稽核" if "audit" in stem.lower() else "developer")
        if state == DONE and age > OFFICE_DONE_WINDOW:
            return None
        return ("sub", payload.get("parent_thread_id"),
                OfficeRow(f"codex {role} {nick}".strip(), state,
                          _humanize(stem) if stem else "（未命名工作）", age, 20,
                          key=f"codex:{payload.get('id')}",
                          group=f"codex:{payload.get('parent_thread_id')}", kind="subagent"))
    key = f"codex:{payload.get('id')}"
    if place[0] == "task":
        return ("main", payload.get("id"),
                OfficeRow("codex developer", state, place[1], age, 10, key=key))
    return ("main", payload.get("id"), OfficeRow("codex PM", state, "", age, 0, key=key))


def codex_rows(now: float, root: str | None = None,
               canonical: str | None = None) -> list[OfficeRow]:
    root = CODEX_SESSIONS if root is None else root
    canonical = canonical or canonical_root()
    days = {time.strftime("%Y/%m/%d", time.localtime(now - d * 86400)) for d in (0, 1)}
    paths = []
    for day in days:
        paths += glob.glob(os.path.join(root, day, "rollout-*.jsonl"))
    mains, subs = {}, []
    for path in sorted(paths):
        try:
            if now - os.path.getmtime(path) > OFFICE_WINDOW:
                continue
        except OSError:
            continue
        found = _codex_row(path, now, canonical)
        if found is None:
            continue
        kind, key, row = found
        if kind == "main":
            mains[key] = row
        else:
            subs.append((key, row))
    rows = []
    for key, row in mains.items():
        if row.name == "codex PM":
            busy = [sub.detail for parent, sub in subs if parent == key and sub.state == WORKING]
            detail = (f"指揮 {len(busy)} 個 subagent：" + "、".join(busy[:3]) if busy
                      else "處理中" if row.state == WORKING else "等待下一個指示")
            row = OfficeRow(row.name, WORKING if busy else row.state, detail, row.age, row.order,
                            key=row.key)
        rows.append(row)
    return rows + [row for _, row in subs]


def claude_rows(now: float, root: str | None = None,
                canonical: str | None = None) -> list[OfficeRow]:
    root = CLAUDE_PROJECTS if root is None else root
    canonical = canonical or canonical_root()
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        try:
            age = max(0.0, now - os.path.getmtime(path))
        except OSError:
            continue
        if age > OFFICE_WINDOW:
            continue
        tail = _jsonl_tail(path)
        cwd = next((item.get("cwd") for item in reversed(tail)
                    if type(item.get("cwd")) is str), None)
        place = _place(cwd, canonical)
        if place is None:
            continue
        title = next((item.get("aiTitle") for item in reversed(tail)
                      if item.get("type") == "ai-title"), None)
        detail = _label(title) or (place[1] if place[0] == "task" else "（無標題）")
        reviewing = place[0] == "pm" or re.search(r"(?i)review|審", detail) is not None
        name = "claude reviewer" if reviewing else "claude developer"
        key = f"claude:{os.path.basename(path)}"
        one_shot = any(item.get("entrypoint") == "sdk-cli" for item in tail)
        if one_shot:
            # `claude -p` started by another agent: it exits when its one task is done.
            state = WORKING if age <= OFFICE_BUSY else DONE
            if state == DONE and age > OFFICE_DONE_WINDOW:
                continue
            rows.append(OfficeRow(name, state, detail, age, 30, key=key,
                                  group="codex-launched", kind="subagent"))
            continue
        rows.append(OfficeRow(name, WORKING if age <= OFFICE_BUSY else IDLE, detail, age,
                              30 if place[0] == "pm" else 10, key=key))
        session_dir = path[:-len(".jsonl")]
        for meta_path in sorted(glob.glob(os.path.join(session_dir, "subagents",
                                                       "agent-*.meta.json"))):
            log_path = meta_path[:-len(".meta.json")] + ".jsonl"
            try:
                sub_age = max(0.0, now - os.path.getmtime(log_path))
                with open(meta_path, "rb") as handle:
                    meta = json.loads(handle.read(64 << 10))
            except (OSError, ValueError):
                continue
            if type(meta) is not dict:
                continue
            if sub_age <= OFFICE_BUSY:
                state = WORKING
            elif meta.get("finished") is True:
                state = DONE
            else:
                state = WORKING if sub_age <= OFFICE_STALL else STALLED
            if (state == DONE and sub_age > OFFICE_DONE_WINDOW) or sub_age > OFFICE_WINDOW:
                continue
            rows.append(OfficeRow("claude subagent", state,
                                  _label(meta.get("description")) or "（未命名工作）",
                                  sub_age, 40, group=key, kind="subagent"))
    return rows


OPENCODE_DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
# Only these session columns are read; the database's account/credential tables
# are never queried.
OPENCODE_SESSIONS_SQL = ("SELECT directory, title, time_updated, time_idle FROM session_v2 "
                         "WHERE parent_id IS NULL AND time_updated >= ? "
                         "ORDER BY time_updated DESC LIMIT 200")
# Outcome of the newest finished top-level session: only the typed error code
# and HTTP status are extracted, never the error message or any content.
OPENCODE_OUTCOME_SQL = (
    "SELECT s.time_updated, s.idle_outcome, "
    "(SELECT json_extract(m.data, '$.error.type') FROM session_message m "
    " WHERE m.session_id = s.id AND m.type = 'idle' ORDER BY m.seq DESC LIMIT 1), "
    "(SELECT json_extract(m.data, '$.error.status') FROM session_message m "
    " WHERE m.session_id = s.id AND m.type = 'idle' ORDER BY m.seq DESC LIMIT 1) "
    "FROM session_v2 s WHERE s.parent_id IS NULL AND s.time_idle IS NOT NULL "
    "ORDER BY s.time_updated DESC LIMIT 1")
OPENCODE_EVIDENCE_WINDOW = 24 * 3600
ERROR_TYPE = re.compile(r"[a-z][a-z0-9._-]{0,47}")
OPENCODE_ROLES = ("OpenCode CLIENT", "OpenCode CLI")
_cwd_cache: dict = {}


def process_cwd(pid: int) -> str | None:
    """Current directory of one process (lsof, read-only), cached per PID."""
    if pid in _cwd_cache:
        return _cwd_cache[pid]
    found = None
    try:
        out = subprocess.run(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             capture_output=True, text=True, timeout=2, check=False).stdout
        found = next((line[1:] for line in out.splitlines() if line.startswith("n/")), None)
    except (OSError, subprocess.SubprocessError):
        pass
    _cwd_cache[pid] = found
    return found


def opencode_sessions(now: float, db: str | None = None) -> list[tuple]:
    """[(directory, title, updated_s, idle_s | None)] for recent top-level sessions."""
    db = OPENCODE_DB if db is None else db
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(OPENCODE_SESSIONS_SQL, (int((now - OFFICE_WINDOW) * 1000),)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out = []
    for directory, title, updated, idle in rows:
        if type(directory) is str and type(updated) is int:
            out.append((directory, title, updated / 1000,
                        idle / 1000 if type(idle) is int else None))
    return out


def opencode_outcome(now: float, db: str | None = None):
    """(state, detail, age) from the newest finished OpenCode session, or None."""
    db = OPENCODE_DB if db is None else db
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(OPENCODE_OUTCOME_SQL).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row or type(row[0]) is not int:
        return None
    age = max(0.0, now - row[0] / 1000)
    if age > OPENCODE_EVIDENCE_WINDOW:
        return None
    kind = row[2] if type(row[2]) is str and ERROR_TYPE.fullmatch(row[2]) else None
    status = row[3] if type(row[3]) is int and 100 <= row[3] <= 599 else None
    if kind is None and status is None:
        return (IDLE, "最近一次執行沒有錯誤", age) if row[1] != "failed" else (
            UNCHECKED, "最近一次執行失敗（原因不明）", age)
    if kind == "provider.quota" or status == 429:
        return STOPPED, "最近一次：免費額度用完（429）", age
    if kind == "provider.auth" or status in (401, 403):
        return STOPPED, f"最近一次：認證失敗（{status or kind}），需要重新登入", age
    return STOPPED, f"最近一次執行失敗（{kind or status}）", age


def _where(cwd, canonical: str) -> str:
    place = _place(cwd, canonical)
    if place is None:
        return "其他資料夾" if cwd else "位置不明"
    return "本 repo" if place[0] == "pm" else f"task {place[1]}"


def _elapsed_seconds(text: str) -> float | None:
    found = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", text or "")
    if not found:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return float(((days * 24 + hours) * 60 + minutes) * 60 + seconds)


def opencode_rows(processes: list[Process], now: float, canonical: str,
                  db: str | None = None, cwd_of=process_cwd) -> list[OfficeRow]:
    """One row per running OpenCode CLI/TUI or `run` client."""
    procs = sorted((p for p in processes if p.role in OPENCODE_ROLES), key=lambda p: p.pid)
    alive = {p.pid for p in processes if p.role in OPENCODE_ROLES + ("AGY CLI",)}
    for pid in [pid for pid in _cwd_cache if pid not in alive]:
        _cwd_cache.pop(pid, None)
    if not procs:
        return []
    sessions = opencode_sessions(now, db)
    rows = []
    for number, proc in enumerate(procs, 1):
        cwd = cwd_of(proc.pid)
        real = os.path.realpath(cwd) if cwd else None
        latest = next((item for item in sessions if real and os.path.realpath(item[0]) == real),
                      None)
        busy = (latest is not None and now - latest[2] <= OFFICE_STALL and
                (latest[3] is None or latest[2] - latest[3] > 2.0))
        where = _where(cwd, canonical)
        name = f"opencode {'worker' if proc.role == 'OpenCode CLIENT' else 'cli'} #{number}"
        if busy:
            detail = f"{where}：{_label(latest[1], 28) or '執行中'}"
            rows.append(OfficeRow(name, WORKING, detail, max(0.0, now - latest[2]), 50 + number,
                                  kind="worker"))
        else:
            opened = _elapsed_seconds(proc.elapsed)
            since = f"，已開 {_ago(opened)[:-1]}" if opened is not None and opened >= 60 else ""
            rows.append(OfficeRow(name, IDLE, f"{where}：已開啟，沒有執行中的任務{since}",
                                  opened if opened is not None else 0.0, 50 + number,
                                  kind="worker"))
    return rows


AGY_BUSY_CPU = 3.0   # %CPU at or above which an open AGY CLI is treated as working


def process_cpu(pid: int) -> float | None:
    try:
        out = subprocess.run(["/bin/ps", "-o", "%cpu=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=2, check=False).stdout.strip()
        return float(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def agy_rows(processes: list[Process], canonical: str, cwd_of=process_cwd,
             cpu_of=process_cpu) -> list[OfficeRow]:
    """One row per open AGY CLI. AGY keeps no local activity record we can read,
    so busy/idle is estimated from CPU use and labelled as such."""
    rows = []
    procs = sorted((p for p in processes if p.role == "AGY CLI"), key=lambda p: p.pid)
    for number, proc in enumerate(procs, 1):
        where = _where(cwd_of(proc.pid), canonical)
        cpu = cpu_of(proc.pid)
        opened = _elapsed_seconds(proc.elapsed)
        if cpu is None:
            state, note = UNCHECKED, "CLI 已開啟，無法判斷是否在工作"
        elif cpu >= AGY_BUSY_CPU:
            state, note = WORKING, f"CLI 執行中（依 CPU {cpu:.0f}% 推算）"
        else:
            state, note = IDLE, "CLI 已開啟，閒置（依 CPU 推算）"
        rows.append(OfficeRow(f"agy cli #{number}", state, f"{where}：{note}",
                              opened if opened is not None else 0.0, 60 + number, kind="worker"))
    return rows


ROADMAP_WORKERS = (   # (row prefix in the Roadmap Worker table, display name, sort order)
    ("Codex Reviewer", "codex reviewer agent", 25),
    ("OpenCode", "opencode developer", 50),
    ("AGY", "agy", 60),
    ("Company", "公司 gw", 70),
)
# Owner dispatch switches (Owner 2026-10-23): one record per Worker, kept apart
# from live technical state. A PAUSED switch holds until the Owner changes it,
# whatever quota, login or app state is observed.
DISPATCH_RELATIVE = os.path.join("tools", "llmtool", "worker-dispatch.json")
DISPATCH_KEYS = {"codex reviewer agent": "codex-reviewer-agent",
                 "opencode developer": "opencode-developer", "agy": "agy",
                 "公司 gw": "company-gw"}
DISPATCH_VALUES = frozenset({"ALLOWED", "PAUSED", "OWNER_ASSIGNMENT_ONLY"})
DECIDERS = frozenset({"Owner", "PM"})


def worker_dispatch(path: str) -> dict | None:
    """{worker key: (dispatch, decided_by, MM-DD)}; None when the file is absent/invalid."""
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read(256 << 10))
    except (OSError, ValueError):
        return None
    workers = data.get("workers") if type(data) is dict and data.get("schema_version") == 1 else None
    if type(workers) is not dict:
        return None
    found = {}
    for key, item in workers.items():
        if type(key) is not str or type(item) is not dict:
            continue
        dispatch, who, when = item.get("dispatch"), item.get("decided_by"), item.get("decided_at")
        if (dispatch in DISPATCH_VALUES and who in DECIDERS and type(when) is str and
                re.fullmatch(r"\d{4}-\d{2}-\d{2}", when)):
            found[key] = (dispatch, who, when[5:])
    return found


def roadmap_workers(path: str | None = None) -> dict[str, str]:
    """{display name: live status text} from the dated Worker availability table."""
    path = path or os.path.join(canonical_root(), "tools", "llmtool", "ROADMAP.md")
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read(OFFICE_HEAD)
    except (OSError, UnicodeDecodeError):
        return {}
    found, inside = {}, False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = "worker availability" in line.lower()
            continue
        if not inside or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        worker = re.sub(r"[`*]", "", cells[0])
        for prefix, name, _ in ROADMAP_WORKERS:
            if worker.startswith(prefix) and name not in found:
                found[name] = cells[1]
    return found


def _worker_state(status: str) -> tuple[str, str]:
    plain = re.sub(r"[`*]", "", status)
    upper = plain.upper()
    if upper.startswith("PAUSED") and ("429" in plain or "USAGELIMIT" in upper.replace(" ", "")):
        return STOPPED, "免費額度用完（429），等額度恢復"
    if upper.startswith("PAUSED"):
        return PAUSED, "Owner 暫停中"
    if upper.startswith("NO ACTIVE") or "尚未" in plain:
        return PAUSED, "尚未指派工作"
    if upper.startswith("NOT RECHECKED"):
        return UNCHECKED, ("上次免費額度用完（429），尚未重新確認" if "429" in plain
                           else "尚未重新確認")
    if "PASS" in upper or upper.startswith("AVAILABLE"):
        return IDLE, "可用，目前沒有工作"
    return UNCHECKED, _label(plain, 36)


def office_rows(processes: list[Process], now: float | None = None, *,
                codex_root: str | None = None, claude_root: str | None = None,
                roadmap: str | None = None, canonical: str | None = None,
                opencode_db: str | None = None, cwd_of=process_cwd,
                cpu_of=process_cpu, dispatch: str | None = None) -> list[OfficeRow]:
    now = time.time() if now is None else now
    canonical = canonical or canonical_root()
    rows = codex_rows(now, codex_root, canonical) + claude_rows(now, claude_root, canonical)
    reviewing = any(row.name.startswith("codex reviewer") and row.state == WORKING for row in rows)
    # A resident OpenCode SERVER alone is not a member; each CLI/TUI or `run`
    # client is. Without any, the Roadmap row says why OpenCode is not here.
    opencode = opencode_rows(processes, now, canonical, opencode_db, cwd_of)
    rows += opencode
    agy_open = any(p.role == "Antigravity App (AGY)" for p in processes)
    agy = agy_rows(processes, canonical, cwd_of, cpu_of)
    rows += agy
    switches = worker_dispatch(dispatch or os.path.join(canonical, DISPATCH_RELATIVE))
    table = {} if switches is not None else roadmap_workers(
        roadmap or os.path.join(canonical, "tools", "llmtool", "ROADMAP.md"))
    for _, name, order in ROADMAP_WORKERS:
        if name == "codex reviewer agent" and reviewing:
            continue
        live = opencode if name == "opencode developer" else agy if name == "agy" else []
        switch = (switches or {}).get(DISPATCH_KEYS[name])
        paused = switch is not None and switch[0] == "PAUSED"
        if live and not paused:
            continue   # the live CLI rows say everything
        notes, state, age = [], None, None
        if name == "opencode developer" and not live:
            outcome = opencode_outcome(now, opencode_db)
            if outcome is not None:
                state, age = outcome[0], outcome[2]
                notes.append(outcome[1])
        if name == "agy" and agy_open and not live:
            notes.append("App 開著")
        if live:
            notes.append(f"有 {len(live)} 個 CLI 開著（非派工）")
        if switch is not None:
            label = {"PAUSED": f"{switch[1]} 暫停派工（{switch[2]}）",
                     "OWNER_ASSIGNMENT_ONLY": "僅 Owner 指定時派工",
                     "ALLOWED": "可派工"}[switch[0]]
            if paused:
                state, age = PAUSED, None
            elif state is None:
                state = IDLE
                notes.append("目前沒有工作")
            rows.append(OfficeRow(name, state, "；".join([label] + notes), age, order,
                                  kind="worker"))
        elif name in table:
            table_state, detail = _worker_state(table[name])
            parts = notes if state is not None else [detail] + notes
            rows.append(OfficeRow(name, state or table_state, "；".join(parts), age, order,
                                  kind="worker"))
    # One-shot Claude runs are started by Codex; hang them under the most
    # recently active Codex PM (the launcher is not recorded more precisely).
    pms = sorted((row for row in rows if row.name == "codex PM"),
                 key=lambda row: row.age if row.age is not None else 1e12)
    launcher = pms[0].key if pms else ""
    rows = [OfficeRow(row.name, row.state, row.detail, row.age, row.order, row.key,
                      launcher if row.group == "codex-launched" else row.group, row.kind)
            for row in rows]
    return sorted(rows, key=lambda row: (row.order, row.age if row.age is not None else 1e12,
                                         row.name))


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _ago(age: float | None) -> str:
    if age is None:
        return "依派工紀錄"
    if age < 60:
        return "剛剛"
    if age < 3600:
        return f"{int(age // 60)} 分鐘前"
    return f"{int(age // 3600)} 小時前"


def _tree(rows: list[OfficeRow]) -> list[tuple[str, OfficeRow]]:
    """(prefix, row) in display order: each agent followed by what it started."""
    keys = {row.key for row in rows if row.key}
    children: dict[str, list[OfficeRow]] = {}
    for row in rows:
        if row.group in keys:
            children.setdefault(row.group, []).append(row)
    out: list[tuple[str, OfficeRow]] = []

    def walk(row: OfficeRow, depth: int) -> None:
        out.append(("  " * (depth - 1) + "└ " if depth else "", row))
        for child in children.get(row.key, []) if row.key and depth < 3 else []:
            walk(child, depth + 1)

    for row in rows:
        if row.group not in keys and row.kind != "worker":
            walk(row, 0)
    workers = [row for row in rows if row.kind == "worker" and row.group not in keys]
    if workers:
        out.append(("—", OfficeRow("外部 Worker", "", "", None, 0)))
        out += [("", row) for row in workers]
    return out


def office_lines(rows: list[OfficeRow]) -> list[str]:
    working = sum(row.state == WORKING for row in rows)
    counts = {kind: sum(row.kind == kind for row in rows) for kind in ("agent", "subagent", "worker")}
    lines = [f"辦公室 · agent {counts['agent']} · subagent {counts['subagent']} · "
             f"外部 worker {counts['worker']} · {working} 位工作中"]
    if not rows:
        return lines + ["目前沒有觀察到任何 agent 或 worker。"]
    tree = _tree(rows)
    width = max(_width(prefix + row.name) for prefix, row in tree if prefix != "—") + 2
    for prefix, row in tree:
        if prefix == "—":
            lines.append(f"── {row.name} ──")
            continue
        detail = f"「{row.detail}」" if row.detail else ""
        lines.append(f"{_pad(prefix + row.name, width)}{_pad(f'({row.state})', 9)}"
                     f"{_pad(detail, 46)} · {_ago(row.age)}")
    lines.append("狀態依本機紀錄推算：工作中 = 最近有動作，不代表模型正在產生內容。")
    return lines


def office_events(old: dict, rows: list[OfficeRow]) -> tuple[dict, list[tuple[str, str]]]:
    """Log lines for members whose state changed since the previous poll."""
    new = {f"{row.name}|{row.detail}": row.state for row in rows}
    events = []
    for key, state in new.items():
        before = old.get(key)
        if before is not None and before != state:
            name, detail = key.split("|", 1)
            kind = "+" if state == WORKING else "!" if state in (STALLED, STOPPED) else "i"
            events.append((kind, f"{name}「{detail}」：{before} → {state}"))
    return new, events


MARK = {"+": "+", "-": "-", "!": "!", "ok": "✓", "?": "?", "i": "·", "▶": "▶", "→": "→", "■": "■"}
FOOTNOTE = "running != model generating; gone != task done"
FOLLOW_VIEWS = frozenset({"log", "activity"})   # newest at the bottom; offset counts back


def run_once(github: "GitHubSource | None" = None) -> int:
    mon = Monitor()
    mon.poll()
    print(f"AI agents · {datetime.now():%Y-%m-%d %H:%M:%S} · {summary(mon.processes, mon.lingering)}")
    if mon.error:
        print(f"STATUS: UNKNOWN (unable to read process table: {mon.error})")
        return 1
    print("\n".join(office_lines(mon.office)))
    print()
    print("\n".join(table_lines(mon.processes)))
    print("\nRECENT LLMTOOL TASKS")
    print("\n".join(task_lines(mon.tasks, 3)))
    print("\nRECENT ACTIVITY")
    print("\n".join(activity_lines(mon.tasks, github=github)))
    print(f"Note: {FOOTNOTE}.")
    return 0


def run_plain(interval: float, mon: "Monitor") -> int:
    """Append-only stream: one line per change, never a repainted screen."""
    print(f"AI agents · read-only process monitor · {FOOTNOTE} · Ctrl+C to quit", flush=True)
    try:
        while True:
            for stamp, kind, text in mon.poll():
                print(f"{stamp} {MARK.get(kind, ' ')} {text}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped; no worker was signalled or modified.")
        return 0


def run_curses(stdscr, interval: float, use_color: bool, mon: "Monitor") -> None:
    import curses
    curses.curs_set(0)
    stdscr.timeout(100)
    stdscr.keypad(True)
    colors = {}
    if use_color and curses.has_colors():
        curses.use_default_colors()
        for n, (key, fg) in enumerate((("title", curses.COLOR_CYAN), ("!", curses.COLOR_YELLOW),
                                       ("+", curses.COLOR_GREEN), ("-", curses.COLOR_MAGENTA),
                                       ("?", curses.COLOR_RED), ("ok", curses.COLOR_GREEN),
                                       ("▶", curses.COLOR_CYAN), ("→", curses.COLOR_BLUE),
                                       ("■", curses.COLOR_GREEN)), start=1):
            curses.init_pair(n, fg, -1)
            colors[key] = curses.color_pair(n)
    view, offset, next_poll = "office", 0, 0.0

    def put(y: int, text: str, attr: int = 0) -> None:
        h, w = stdscr.getmaxyx()
        if 0 <= y < h and w > 1:
            try:
                stdscr.addnstr(y, 0, text, w - 1, attr)
            except curses.error:
                pass

    while True:
        if time.monotonic() >= next_poll:
            mon.poll()
            if mon.github is not None:
                mon.github.refresh_in_background()
            next_poll = time.monotonic() + interval
        h, w = stdscr.getmaxyx()
        body = max(1, h - 4)
        stdscr.erase()
        put(0, f"AI agents · read-only process monitor · {datetime.now():%H:%M:%S} · polls {mon.polls}",
            colors.get("title", 0) | curses.A_BOLD)
        status = (f"STATUS UNKNOWN: {mon.error}" if mon.error
                  else summary(mon.processes, mon.lingering))
        running = running_stages(mon.tasks)
        status += f"  ·  llmtool stages running: {running}" if running else ""
        put(1, status, colors.get("!", 0) if (mon.error or mon.lingering) else 0)
        if view == "log":
            entries = list(mon.log)
            offset = max(0, min(offset, len(entries) - body))
            end = len(entries) - offset
            for row, (stamp, kind, text) in enumerate(entries[max(0, end - body):end]):
                put(3 + row, f"{stamp} {MARK.get(kind, ' ')} {text}", colors.get(kind, 0))
            hint = "Tab/1-5: log·tasks·processes·Activity·辦公室  ↑↓ PgUp/PgDn: scroll  End: follow  q: quit"
            if offset:
                hint = f"[scrolled back {offset}]  " + hint
        elif view == "tasks":
            lines = task_lines(mon.tasks)
            offset = max(0, min(offset, len(lines) - body))
            for row, line in enumerate(lines[offset:offset + body]):
                attr = curses.A_BOLD if line.startswith("TASK") else 0
                if "RUNNING" in line:
                    attr |= colors.get("+", 0)
                elif "UNKNOWN" in line or "TIMEOUT" in line or "BLOCKED" in line or " error" in line:
                    attr |= colors.get("!", 0)
                put(3 + row, line, attr)
            hint = "Tab/1-5: log·tasks·processes·Activity·辦公室  ↑↓ PgUp/PgDn: scroll  q: quit"
        elif view == "activity":
            lines = activity_lines(mon.tasks, github=mon.github, background=True)
            offset = max(0, min(offset, len(lines) - body))
            end = len(lines) - offset
            for row, line in enumerate(lines[max(0, end - body):end]):
                attr = colors.get("!", 0) if "未知" in line or "UNKNOWN" in line or "沒送到" in line else 0
                put(3 + row, line, attr)
            hint = "Tab/1-5: log·tasks·processes·Activity·辦公室  ↑↓ PgUp/PgDn: scroll  End: follow  q: quit"
            if offset:
                hint = f"[scrolled back {offset}]  " + hint
        elif view == "office":
            lines = office_lines(mon.office)
            offset = max(0, min(offset, len(lines) - body))
            for row, line in enumerate(lines[offset:offset + body]):
                attr = curses.A_BOLD if offset + row == 0 else 0
                if f"({WORKING})" in line:
                    attr |= colors.get("+", 0)
                elif f"({STOPPED})" in line or f"({STALLED})" in line:
                    attr |= colors.get("!", 0)
                elif f"({PAUSED})" in line or f"({DONE})" in line:
                    attr |= curses.A_DIM
                put(3 + row, line, attr)
            hint = "Tab/1-5: log·tasks·processes·Activity·辦公室  ↑↓ PgUp/PgDn: scroll  q: quit"
        else:
            lines = table_lines(mon.processes) if not mon.error else ["(process table unavailable)"]
            offset = max(0, min(offset, len(lines) - body))
            for row, line in enumerate(lines[offset:offset + body]):
                put(3 + row, line, curses.A_BOLD if offset + row == 0 else 0)
            hint = "Tab/1-5: log·tasks·processes·Activity·辦公室  ↑↓ PgUp/PgDn: scroll  q: quit"
        put(h - 1, f"{hint}  ·  {FOOTNOTE}", curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return
        views = ("log", "tasks", "table", "activity", "office")
        if key == 9:  # Tab cycles log -> tasks -> processes -> Activity -> office
            view, offset = views[(views.index(view) + 1) % len(views)], 0
        elif key in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
            view, offset = views[key - ord("1")], 0
        elif key == curses.KEY_UP:
            offset += 1 if view in FOLLOW_VIEWS else -1
        elif key == curses.KEY_DOWN:
            offset -= 1 if view in FOLLOW_VIEWS else -1
        elif key == curses.KEY_PPAGE:
            offset += body if view in FOLLOW_VIEWS else -body
        elif key == curses.KEY_NPAGE:
            offset -= body if view in FOLLOW_VIEWS else -body
        elif key in (curses.KEY_END, ord("G")):
            offset = 0
        offset = max(0, offset)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between snapshots (default 1; minimum 0.5)")
    parser.add_argument("--once", action="store_true",
                        help="print the office roster, process table, task list and Activity view, then exit")
    parser.add_argument("--plain", action="store_true",
                        help="append-only event lines instead of the interactive screen")
    parser.add_argument("--no-color", action="store_true", help="disable colors")
    parser.add_argument("--all", action="store_true",
                        help="log every helper process immediately (no short-lived filtering)")
    parser.add_argument("--min-age", type=float, default=3.0,
                        help="seconds a helper must live before it is logged (default 3)")
    parser.add_argument("--github", action="store_true",
                        help=f"opt in to read-only gh api Activity on {DEFAULT_GITHUB_REPO}")
    parser.add_argument("--no-github", action="store_true",
                        help="compatibility option; GitHub Activity is already off by default")
    args = parser.parse_args(argv)
    if not 0.5 <= args.interval <= 60:
        parser.error("--interval must be between 0.5 and 60 seconds")
    github = GitHubSource(DEFAULT_GITHUB_REPO) if args.github and not args.no_github else None
    if args.once:
        return run_once(github)
    mon = Monitor(min_age=max(0.0, args.min_age), show_all=args.all)
    mon.github = github
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "dumb") != "dumb"
    if args.plain or not interactive:
        return run_plain(args.interval, mon)
    import curses
    use_color = not args.no_color and not os.environ.get("NO_COLOR")
    try:
        curses.wrapper(run_curses, args.interval, use_color, mon)
    except KeyboardInterrupt:
        pass
    print("Monitor stopped; no worker was signalled or modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
