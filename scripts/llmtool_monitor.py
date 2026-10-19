#!/usr/bin/env python3
"""Read-only macOS/Linux process monitor for local AI agent jobs.

Shows OpenCode, Claude Code, Codex and llmtool processes, and the progress of
recent llmtool tasks (stage, model, result, tokens, tool calls with file paths)
read from the harness evidence and each job's own OpenCode database. No model calls,
repository mutation, credentials, network requests or daemon. This prototype
reports only OS-observed processes; it does NOT infer model steps or attest
task completion/review decisions.

Also lists the other members of every observed agent process group, plus their
descendants, including zombies (state Z). A lingering member after its leader
exited is exactly what makes llmtool report CLEANUP_UNKNOWN. The Claude desktop
app is shown as one line, without its many helper processes.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import os
import re
import stat
import subprocess
import sys
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
NO_EXPAND = frozenset({"Claude Desktop"})


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
        new += [(stamp, k, t) for k, t in task_events(self.task_map, tasks)]
        if self.polls == 1 and tasks:
            latest = tasks[0]
            last = latest.stages[-1] if latest.stages else None
            new.append((stamp, "i", f"{len(tasks)} recent llmtool task(s); latest {latest.task_id}"
                                    + (f": {last.name} {stage_state(last)}" if last else ": not started")))
        self.tasks, self.task_map = tasks, {t.directory: t for t in tasks}
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
            result = _read_json(os.path.join(directory, f"{stage}-result.json")) or {}
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
                                code, cleanup, steps, tin, tout, idle, tools, live, meta))
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


def task_events(old: dict, new: list[Task]) -> list[tuple[str, str]]:
    """(kind, text) for new stages, new tool calls and finished stages."""
    out = []
    for task in new:
        before = old.get(task.directory)
        known = {s.name: s for s in before.stages} if before else {}
        for stage in task.stages:
            prior = known.get(stage.name)
            if before is None:
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
                lines.append(f"        {tool.name:<6} {tool.path}  {mark}")
            if len(stage.tools) > 6:
                lines.append(f"        … {len(stage.tools) - 6} earlier tool calls")
        lines.append("")
    return lines


MARK = {"+": "+", "-": "-", "!": "!", "ok": "✓", "?": "?", "i": "·", "▶": "▶", "→": "→", "■": "■"}
FOOTNOTE = "running != model generating; gone != task done"


def run_once() -> int:
    mon = Monitor()
    mon.poll()
    print(f"AI agents · {datetime.now():%Y-%m-%d %H:%M:%S} · {summary(mon.processes, mon.lingering)}")
    if mon.error:
        print(f"STATUS: UNKNOWN (unable to read process table: {mon.error})")
        return 1
    print("\n".join(table_lines(mon.processes)))
    print("\nRECENT LLMTOOL TASKS")
    print("\n".join(task_lines(mon.tasks, 3)))
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
    view, offset, next_poll = "log", 0, 0.0

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
            hint = "Tab/1/2/3: log·tasks·processes  ↑↓ PgUp/PgDn: scroll  End: follow  q: quit"
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
            hint = "Tab/1/2/3: log·tasks·processes  ↑↓ PgUp/PgDn: scroll  q: quit"
        else:
            lines = table_lines(mon.processes) if not mon.error else ["(process table unavailable)"]
            offset = max(0, min(offset, len(lines) - body))
            for row, line in enumerate(lines[offset:offset + body]):
                put(3 + row, line, curses.A_BOLD if offset + row == 0 else 0)
            hint = "Tab/1/2/3: log·tasks·processes  ↑↓ PgUp/PgDn: scroll  q: quit"
        put(h - 1, f"{hint}  ·  {FOOTNOTE}", curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return
        views = ("log", "tasks", "table")
        if key == 9:  # Tab cycles log -> tasks -> processes
            view, offset = views[(views.index(view) + 1) % 3], 0
        elif key in (ord("1"), ord("2"), ord("3")):
            view, offset = views[key - ord("1")], 0
        elif key == curses.KEY_UP:
            offset += 1 if view == "log" else -1
        elif key == curses.KEY_DOWN:
            offset -= 1 if view == "log" else -1
        elif key == curses.KEY_PPAGE:
            offset += body if view == "log" else -body
        elif key == curses.KEY_NPAGE:
            offset -= body if view == "log" else -body
        elif key in (curses.KEY_END, ord("G")):
            offset = 0
        offset = max(0, offset)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between snapshots (default 1; minimum 0.5)")
    parser.add_argument("--once", action="store_true", help="print one process table and exit")
    parser.add_argument("--plain", action="store_true",
                        help="append-only event lines instead of the interactive screen")
    parser.add_argument("--no-color", action="store_true", help="disable colors")
    parser.add_argument("--all", action="store_true",
                        help="log every helper process immediately (no short-lived filtering)")
    parser.add_argument("--min-age", type=float, default=3.0,
                        help="seconds a helper must live before it is logged (default 3)")
    args = parser.parse_args(argv)
    if not 0.5 <= args.interval <= 60:
        parser.error("--interval must be between 0.5 and 60 seconds")
    if args.once:
        return run_once()
    mon = Monitor(min_age=max(0.0, args.min_age), show_all=args.all)
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
