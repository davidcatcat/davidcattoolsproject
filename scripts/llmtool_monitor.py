#!/usr/bin/env python3
"""Read-only macOS/Linux process monitor for local AI agent jobs.

Shows OpenCode, Kilo, Claude Code, Codex and llmtool processes, and the progress of
recent llmtool tasks (stage, model, result, tokens, tool calls with file paths)
read from the harness evidence and each job's own OpenCode database. No model calls,
repository mutation, stored credentials, or daemon. The only network access is read-only
GitHub API GETs for Activity headlines (on by default since Owner 2026-10-24;
--no-github turns it off): the running screen sends them to api.github.com itself
with the token `gh auth token` prints, kept in memory only and never shown or
logged, because each `gh api` process costs ~0.15 s CPU (Issue #625); --once,
--gh-cli, or any trouble reaching GitHub that way use `gh api` instead. This prototype
reports only OS-observed processes; it does NOT infer model steps or attest
task completion/review decisions. Its Activity view is limited to local task
evidence and does not invent cross-session dispatch, delivery, review or acceptance.

The office view (default screen, key 5) lists every Codex PM / developer /
reviewer / subagent and Claude session / subagent working in this repository,
plus each Worker's dispatch switch and every self-hosted CI runner (this Mac's
from its processes and logs, any other from GitHub's runner list alone), one
row each with a live state such as
工作中, 待命, 完成, 暫停中 or 停工. It parses local session files but shows only
labels, titles and verdict words (see the office view section); Codex work
labels use only local Task id, developer provider/state/creation/session,
request, and JSON summary fields; a role is
named only when a record binds it, never guessed. A session untouched for 6 hours is left out,
and finished subagent work for 30 minutes; office data is rebuilt at most every
5 seconds, and a rebuild reads again only the session, registry, runner-log and
llmtool records whose file changed (Issue #625); GitHub is read every 30 seconds,
later while gh keeps failing. The process table comes from the kernel's process
list each poll and from `ps -A` every 15 seconds (it prints the same rows but
costs ~0.1 s CPU a call): only the R/S letter in STAT, and a new root or system
daemon process, may lag by that. The EC2 remote-test worker gets one row: the Task, SHA and modules of a
running `llmtool task remote-test`, else its newest local receipt or outcome.
The work board PR section uses the already-read pulls and workflow-runs pages,
verdict comments, live D6 review tasks and bounded local Task receipts/outcomes.
Stuck alerts use only local Task id, request, developer executor task id/state,
remote-test receipts/outcomes, live office rows, registry details, and already-read
CI runs and branch PRs.

Also lists the other members of every observed agent process group, plus their
descendants, including zombies (state Z). A lingering member after its leader
exited is exactly what makes llmtool report CLEANUP_UNKNOWN. The Claude desktop
app is shown as one line, without its many helper processes.
"""
from __future__ import annotations

import argparse
import ctypes
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
import math
import os
import platform
import re
import select
import shutil
import stat
import struct
import urllib.parse
import functools
import hashlib
import http.client
import importlib.util
import subprocess
import sys
import tempfile
import threading
import time
import typing

# Intentionally exclude every raw process command line from rendered output.
PS_COLUMNS = "pid=,ppid=,pgid=,etime=,stat=,command="   # what LINE / ps_rows() parse
PS_ARGS = ("/bin/ps", "-A", "-o", PS_COLUMNS)
LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s*(.*)$")
ALLOWED_STATE = re.compile(r"^[A-Za-z0-9+<>?!]{1,12}$")
MAX_ANCESTRY = 64
IGNORED = frozenset({"grep", "egrep", "fgrep", "pgrep", "ps"})
# Python options that consume the next argument (-c ends option parsing).
PYTHON_VALUE_OPTIONS = frozenset({"-X", "-W", "-Q"})
# Roles whose process groups are not expanded (GUI apps with many helpers).
NO_EXPAND = frozenset({"Claude Desktop", "Antigravity App (AGY)"})
DISK_REFRESH_SECONDS = 60.0
DISK_WARNING_PERCENT = 95
DISK_ATTENTION_PERCENT = 95
RULE_ALERT_FILE = os.path.join(os.path.expanduser("~"), ".local/state/llmtool/rule-file-alerts.json")


def _load_rule_alerts(path):
    """Load the small local rule-alert record; return None when it is absent."""
    try:
        with open(path, "rb") as stream:
            raw = stream.read()
    except FileNotFoundError:
        return None
    value = json.loads(raw.decode("utf-8"))
    if (type(value) is not dict or set(value) != {"schema_version", "baseline", "checked", "alerts"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["baseline"]) is not int or type(value["checked"]) is not list
            or type(value["alerts"]) is not list):
        raise ValueError("invalid rule-alert record")
    if any(type(number) is not int for number in value["checked"]):
        raise ValueError("invalid checked PR")
    alerts = value["alerts"]
    for alert in alerts:
        if (type(alert) is not dict or set(alert) != {"pr", "title", "paths"}
                or type(alert["pr"]) is not int or type(alert["title"]) is not str
                or type(alert["paths"]) is not list
                or any(type(path) is not str for path in alert["paths"])):
            raise ValueError("invalid rule alert")
    return {"schema_version": 1, "baseline": value["baseline"],
            "checked": value["checked"][-500:], "alerts": alerts}


def _save_rule_alerts(path, state):
    """Atomically write the rule-alert record with private file and directory modes."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    os.chmod(parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".rule-file-alerts-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def rule_alert_lines(path=None) -> list[tuple[str, str]]:
    if path is None:
        path = RULE_ALERT_FILE
    try:
        state = _load_rule_alerts(path)
    except Exception:
        return [("規則檔告警紀錄無法讀取", "!")]
    if state is None or not state["alerts"]:
        return []
    alerts = list(reversed(state["alerts"]))
    lines = [(f"規則檔已變更：PR #{alert['pr']}，{len(alert['paths'])} 個檔（看過後請 PM 清除）", "!")
             for alert in alerts[:5]]
    if len(alerts) > 5:
        lines.append((f"另有 {len(alerts) - 5} 件規則檔告警", "!"))
    return lines


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
    job: tuple = ()   # a remote-test run: (source, task, sha, modules), validated tokens only
    codex_exec: bool = False   # a Codex CLI started as `codex exec` (a one-shot run)
    # A company LLM run (#763): ("gw", work folder, audit file) of a gw-agent
    # gateway; checked tokens only.
    gw: tuple = ()


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


CLAUDE_COMMANDS = frozenset({"agents", "mcp", "config", "doctor", "update", "install",
                             "plugin", "auth", "setup-token", "migrate-installer"})


def _agent_role(exe: str, args: list[str], command: str) -> str | None:
    words = set(args)
    if exe in ("opencode", "opencode.exe"):
        if "serve" in words:
            return "OpenCode SERVER"
        if "run" in words:
            return "OpenCode CLIENT"
        return "OpenCode CLI"
    if exe in ("kilo", ".kilo", "kilocode", "kilo.exe"):
        # Kilo CLI is an OpenCode fork; the npm launcher starts a ".kilo" binary.
        if "serve" in words:
            return "Kilo SERVER"
        if "run" in words:
            return "Kilo CLIENT"
        return "Kilo CLI"
    if exe == "claude":
        # `claude agents --json`, `claude --version`, ...: a quick management
        # command that starts no model session (a PM polls `agents` often).
        if args and (args[0] in CLAUDE_COMMANDS or args[0] in ("--version", "-v", "--help", "-h")):
            return "Claude Code CLI command"
        # The background-session daemon and its terminal hosts run no model
        # session of their own.
        if args and args[0] in ("daemon", "bg-pty-host"):
            return "Claude Code daemon"
        # A pre-started background session: it names its session in the CLI
        # registry once a session is moved into it, and is idle until then.
        if args and args[0] == "bg-spare":
            return "Claude Code background session"
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
    if exe == "Runner.Listener":
        return "Actions runner"
    if exe == "Runner.Worker":
        return "Actions runner job"
    return None


def _codex_exec_command(command: str) -> bool:
    """Whether a Codex CLI command line runs `codex exec`. Only this one bit
    is kept, never the arguments. Not cached itself: a cache keyed by the
    command line would retain prompts and other argv text (see _row_facts)."""
    return "exec" in command.split()[1:]


# The gw-agent route runs `python -B -P -m api_agent_gateway run ...`
# (tools/llmtool/llmtool/developer_routes/gw_agent.py, build_argv).
GW_AGENT_ROLE = "gw agent run"


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
        if target == ("module", "api_agent_gateway") and "run" in args:
            return GW_AGENT_ROLE
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


HEX_SHA = re.compile(r"[0-9a-f]{40}")
TEST_MODULE = re.compile(r"test_[a-z0-9_]{1,80}")


def _remote_test_job(command: str) -> tuple:
    """(source, short task, short SHA, modules) of an `llmtool task remote-test`
    run ("cli") or of the ssh it starts ("ssh"); "" where unknown. Only tokens
    matching their strict shape and position are kept, so the host, user, ref
    and paths in the command line are never returned."""
    parts = command.split()
    if not parts or (_base(parts[0]) != "ssh" and "remote-test" not in parts):
        return ()
    if _base(parts[0]) == "ssh":
        # ssh ... python3 - <sha> <ref> <repo> <python> <module>...: the
        # modules start after the python path; the ref and paths are skipped.
        for i in range(1, len(parts) - 4):
            if (parts[i] == "-" and _base(parts[i - 1]).startswith("python")
                    and HEX_SHA.fullmatch(parts[i + 1])):
                modules = tuple(m for m in parts[i + 5:] if TEST_MODULE.fullmatch(m))
                return ("ssh", "", parts[i + 1][:7], modules)
        return ()
    if classify(command) != "llmtool CLI":
        return ()
    for i in range(len(parts) - 1):
        if parts[i] == "task" and parts[i + 1] == "remote-test":
            args = parts[i + 2:]
            break
    else:
        return ()
    task, sha, modules, j = "", "", [], 0
    while j < len(args):
        arg = args[j]
        name, eq, value = arg.partition("=")
        if arg.startswith("--") and not eq and j + 1 < len(args):
            value, j = args[j + 1], j + 1
        if name == "--sha" and HEX_SHA.fullmatch(value):
            sha = value[:7]
        elif name == "--module" and TEST_MODULE.fullmatch(value):
            modules.append(value)
        elif not arg.startswith("-") and not task and re.fullmatch(r"[0-9a-f]{8,64}", arg):
            task = arg[:8]
        j += 1
    return ("cli", task, sha, tuple(modules))


GW_PATH = re.compile(r"/[A-Za-z0-9_.+/-]{0,1023}")


def _gw_run_job(command: str, role: str | None) -> tuple:
    """What a company LLM run's command line says (see Process.gw). A path is
    kept only when it is a plain absolute path; nothing else of argv is."""
    if role != GW_AGENT_ROLE:
        return ()
    parts = command.split()
    found = dict.fromkeys(("--workdir", "--audit-file"), "")
    for flag, value in zip(parts, parts[1:]):
        if (flag in found and not found[flag] and GW_PATH.fullmatch(value)
                and ".." not in value.split("/")):
            found[flag] = value
    audit = found["--audit-file"]
    return ("gw", found["--workdir"], audit if audit.endswith("/audit.jsonl") else "")


# What snapshot_from derives from a command line (role, executable name,
# remote-test job, `codex exec` bit), computed once per distinct command and
# reused on every later poll (~500 rows a second). The key is a keyed BLAKE2b
# digest of the command, never the command itself: argv may hold prompts or
# secrets, and a cache keyed by raw text (as lru_cache was) kept them in memory
# long after the process exited. The random key, made per monitor run, means a
# short, guessable argv cannot be confirmed by hashing candidates offline. The
# cache is rebuilt from each snapshot's rows, so it holds only commands still
# running (exited processes drop out on the next poll) and is capped as well.
_ARGV_KEY = os.urandom(16)
ROW_FACTS_MAX = 8192
_row_facts_cache: dict = {}


def _argv_digest(command: str) -> bytes:
    return hashlib.blake2b(command.encode("utf-8", "surrogatepass"), digest_size=16,
                           key=_ARGV_KEY).digest()


def _row_facts(command: str) -> tuple:
    """(role, safe name, remote-test job, codex exec, gw run) of a command
    line; only these sanitized results are ever stored."""
    role = classify(command)
    job = _remote_test_job(command)
    return (role, safe_name(command), job,
            role is not None and role.startswith("Codex CLI") and _codex_exec_command(command),
            _gw_run_job(command, role))


def parse_processes(output: str, self_pid: int, remembered: dict | None = None) -> Snapshot:
    """remembered: {pgid: member PIDs} of agent-led groups seen in earlier polls.

    A leader that exited and was reaped leaves members whose only link is the
    PGID. Only PIDs already seen in that group while it was agent-led count as
    left behind, so an unrelated newer process reusing the PGID is not blamed.
    """
    return snapshot_from(ps_rows(output), self_pid, remembered)


def ps_rows(output: str) -> list[tuple]:
    """(pid, ppid, pgid, etime, stat, command) for each line of ps output."""
    rows = []
    for line in output.splitlines():
        found = LINE.match(line)
        if found is not None:
            rows.append((int(found.group(1)), int(found.group(2)), int(found.group(3)),
                         found.group(4), found.group(5), found.group(6)))
    return rows


def snapshot_from(entries, self_pid: int, remembered: dict | None = None) -> Snapshot:
    """entries: (pid, ppid, pgid, etime, stat, command); etime may also be a
    callable returning it, formatted only for the processes kept."""
    global _row_facts_cache
    remembered = remembered or {}
    rows = {}
    hosts = set()
    previous, seen = _row_facts_cache, {}
    for pid, ppid, pgid, elapsed, stat, command in entries:
        if pid == self_pid or ppid == self_pid:  # this monitor and its own ps
            continue
        digest = _argv_digest(command)
        facts = seen.get(digest) or previous.get(digest)
        if facts is None:
            facts = _row_facts(command)
        seen[digest] = facts
        role = facts[0]
        if role == "llmtool HOST":
            job = _host_job(command)
            if job:
                hosts.add(job)
        rows[pid] = (ppid, pgid, elapsed, stat if ALLOWED_STATE.fullmatch(stat) else "?", role, facts)
    # Only commands in this snapshot are kept: an exited process's entry goes now.
    _row_facts_cache = seen if len(seen) <= ROW_FACTS_MAX else {}
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
    for pid, (ppid, pgid, elapsed, stat, role, facts) in rows.items():
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
        if callable(elapsed):
            elapsed = elapsed()
        elapsed = elapsed if len(elapsed) <= 18 else "UNKNOWN"
        if stat.startswith("Z"):
            role += " (zombie)"
        result.append(Process(pid, ppid, pgid, elapsed, stat, role, facts[1], owner, facts[2],
                              facts[3], facts[4]))
    watched = {g: {pid for pid, row in rows.items() if row[1] == g} for g in groups}
    watched.update(orphans)
    lingering = sorted(
        g for g, members in watched.items()
        if any(pid != g for pid in members) and (g not in rows or rows[g][3].startswith("Z")))
    # Group leaders first, then their members, within each process group.
    result.sort(key=lambda p: (p.pgid, p.pid != p.pgid, p.ppid, p.pid))
    return Snapshot(result, watched, lingering, frozenset(hosts))


def run_ps() -> str:
    # errors="replace": in a UTF-8 locale ps passes invalid UTF-8 bytes of a
    # command through, which a strict decode turned into a crash of the poll.
    response = subprocess.run(PS_ARGS, check=False, capture_output=True, text=True,
                              errors="replace", timeout=4, shell=False)
    if response.returncode:
        raise RuntimeError(f"ps exited with status {response.returncode}")
    return response.stdout


def run_ps_users(uids) -> str:
    """The same columns for these accounts' processes only: ~0.005 s CPU
    instead of ~0.1 s for `ps -A`. Nothing matched or a failed ps is ""."""
    try:
        response = subprocess.run(
            ("/bin/ps", "-U", ",".join(str(uid) for uid in sorted(uids)), "-o", PS_COLUMNS),
            check=False, capture_output=True, text=True, errors="replace", timeout=4,
            shell=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return response.stdout if response.returncode == 0 else ""


# `ps -A` costs ~0.1 s CPU per call on the Owner's Mac (it is setuid root and
# walks every thread of every process), which alone is >10% of a core at the
# 1 s refresh (Issue #625). Between real ps runs the same lines are rebuilt
# from one sysctl(KERN_PROC_ALL) call: pid, ppid, pgid, etime and every STAT
# flag follow ps's own rules exactly, the command is read once per new process
# (KERN_PROCARGS2), and only STAT's R/S/U letter is the one ps last printed.
# A command ps would print differently (another account's, which only root
# may read, or one with bytes ps escapes) is taken from `ps -U` for that real
# uid, once per process; root and system daemons wait for the next `ps -A`.
PS_FULL_EVERY = 15.0   # seconds: `ps -A` at least this often (STAT, root processes' commands)
PS_MIN_GAP = 4.0       # seconds: `ps -U` no more often than this for another account's newcomer
KINFO_SIZE = 648       # sizeof(struct kinfo_proc) on 64-bit macOS
# Upper bounds checked before any buffer is allocated: a size the kernel (or
# a broken sysctl) reports beyond them means "use ps", never a huge buffer.
KINFO_MAX_PROCS = 32768                          # kern.maxproc is a few thousand on a Mac
KINFO_MAX_BYTES = KINFO_SIZE * KINFO_MAX_PROCS   # ~21 MB
PROCARGS_MAX = 1 << 18                           # bytes of one process's KERN_PROCARGS2
SZOMB, SSTOP = 5, 4    # extern_proc.p_stat values
P_CONTROLT, P_PPWAIT, P_SYSTEM, P_TRACED, P_WEXIT = 0x2, 0x10, 0x200, 0x800, 0x2000
EPROC_SLEADER = 0x2


class KProc(typing.NamedTuple):
    pid: int
    ppid: int
    pgid: int
    start: int    # seconds since the epoch
    start_us: int
    pstat: int
    comm: bytes
    uid: int
    flag: int = 0
    eflag: int = 0
    tpgid: int = 0
    nice: int = 0
    ruid: int | None = None   # real uid, as `ps -U` selects; None = same as uid


# struct kinfo_proc fields read, by offset: p_starttime, p_flag, p_stat, p_pid,
# p_nice, p_comm, e_pcred.p_ruid, e_ucred.cr_uid, e_ppid, e_pgid, e_tpgid, e_flag.
_KINFO = struct.Struct("<qi20xib3xi198xb17s132xI24xI136xii8xi32xi32x")


class _Sysctl:
    """libc sysctl through ctypes; every method returns None when unavailable."""

    def __init__(self) -> None:
        import ctypes
        self.ctypes = ctypes
        self.libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        self.libc.sysctl.argtypes = (ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t)
        self.args_buf = ctypes.create_string_buffer(PROCARGS_MAX)

    def _call(self, mib: tuple, buf, size: int):
        ctypes = self.ctypes
        length = ctypes.c_size_t(size)
        if self.libc.sysctl((ctypes.c_int * len(mib))(*mib), len(mib), buf, ctypes.byref(length), None, 0):
            return None
        return length.value

    def processes(self) -> list[KProc] | None:
        size = self._call((1, 14, 0), None, 0)   # CTL_KERN, KERN_PROC, KERN_PROC_ALL
        # Room for processes started between the two calls, within the cap.
        if not size or size > KINFO_MAX_BYTES:
            return None
        buf = self.ctypes.create_string_buffer(min(size + 64 * KINFO_SIZE, KINFO_MAX_BYTES))
        size = self._call((1, 14, 0), buf, len(buf))
        if not size or size > len(buf) or size % KINFO_SIZE:
            return None
        return [KProc(pid, ppid, pgid, start, start_us, pstat, comm, uid, flag, eflag, tpgid, nice,
                      ruid)
                for start, start_us, flag, pstat, pid, nice, comm, ruid, uid, ppid, pgid, tpgid, eflag
                in _KINFO.iter_unpack(memoryview(buf)[:size])]

    def command(self, pid: int) -> str | None:
        """argv joined by spaces, as ps prints it; None when the kernel refuses
        (another account's process: ps reads those as root)."""
        size = self._call((1, 49, pid), None, 0)   # KERN_PROCARGS2: how much there is
        if not size or size < 4 or size > len(self.args_buf):
            return None   # unreadable, or longer than the cap: ps prints it
        size = self._call((1, 49, pid), self.args_buf, len(self.args_buf))
        if not size or size < 4 or size > len(self.args_buf):
            return None
        raw = self.args_buf.raw[:size]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        rest = raw[4:]
        rest = rest[rest.find(b"\0"):].lstrip(b"\0")   # skip the executable path
        argv = rest.split(b"\0")[:argc]
        if argc < 1 or not argv:
            return None   # ps prints "(name)": take its line
        command = b" ".join(argv)
        if not PS_PLAIN.fullmatch(command):
            return None   # ps escapes these bytes (vis(3), per locale): take its line
        return command.decode("ascii")


PS_PLAIN = re.compile(rb"[\x20-\x7e]*")   # a command ps prints byte for byte


def _ps_elapsed(seconds: int) -> str:
    """ps's etime format: [[dd-]hh:]mm:ss. ps on macOS takes
    time(NULL) - p_starttime.tv_sec, both whole seconds (checked on the
    Owner's Mac: a process 0.6 s old that started at .70 shows 00:01), so the
    caller passes int(now) - start, never a fractional difference."""
    days, seconds = divmod(max(0, seconds), 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days:02d}-{hours:02d}:{minutes:02d}:{seconds:02d}"
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _ps_state(k: KProc, letter: str | None) -> str:
    """ps's STAT for k. Everything after the first letter is ps's own rule over
    kernel flags (print.c state(); equal to ps for all ~490 processes on the
    Owner's Mac); Z and T come from p_stat. Only R/S/U/I comes from Mach
    thread states that ps (root) reads: letter, the one ps last printed, or
    S (most processes) for one it has not printed yet."""
    if k.pstat == SZOMB:
        state = "Z"
    elif k.pstat == SSTOP:
        state = "T"
    else:
        state = letter if letter and letter not in "ZT" else "S"
    state += "<" if k.nice < 0 else "N" if k.nice > 0 else ""
    state += "X" if k.flag & P_TRACED else ""
    state += "E" if k.flag & P_WEXIT and k.pstat != SZOMB else ""
    state += "V" if k.flag & P_PPWAIT else ""
    state += "L" if k.flag & P_SYSTEM else ""
    state += "s" if k.eflag & EPROC_SLEADER else ""
    state += "+" if k.flag & P_CONTROLT and k.pgid == k.tpgid else ""
    return state


def _watched_uids() -> frozenset:
    """Other accounts whose new processes are worth an early `ps -U`: people
    (uid 500+) and the Actions runner accounts (this account is always
    included by the caller). System daemons wait for the next `ps -A`."""
    try:
        import pwd
        found = {p.pw_uid for p in pwd.getpwall()
                 if 500 <= p.pw_uid < 65534 or RUNNER_ACCOUNT.fullmatch(p.pw_name)}
    except Exception:
        found = set()
    return frozenset(found - {os.getuid()})


class ProcessTable:
    """`ps -A -o pid=,ppid=,pgid=,etime=,stat=,command=` text, mostly without ps."""

    def __init__(self, ps=run_ps, kernel=None, clock=time.monotonic, wall=time.time,
                 watched=None, ps_users=run_ps_users) -> None:
        self.ps, self.ps_users, self.clock, self.wall = ps, ps_users, clock, wall
        self.kernel = kernel
        self.watched = watched
        self.last_ps = self.last_users = -math.inf
        # (pid, start, start_us, comm) -> (STAT letter ps printed or None, command);
        # None = not readable here, shown once a ps prints it
        self.known: dict = {}
        self.asked: set = set()   # keys a `ps -U` was already run for since the last `ps -A`
        self.ps_runs = self.user_runs = 0

    def _processes(self) -> list[KProc] | None:
        if self.kernel is None:
            try:
                self.kernel = _Sysctl() if sys.platform == "darwin" else False
            except (OSError, AttributeError):
                self.kernel = False
        if not self.kernel:
            return None
        try:
            found = self.kernel.processes()
        except Exception:
            found = None
        # The layout is checked against this process before it is trusted.
        if found is not None and not any(k.pid == os.getpid() and k.ppid == os.getppid()
                                         for k in found):
            self.kernel, found = False, None
        return found

    @staticmethod
    def _adopt(known: dict, procs: list[KProc], rows: list[tuple]) -> None:
        """Remember what ps printed for each process it shares with procs."""
        keys = {k.pid: (k.pid, k.start, k.start_us, k.comm) for k in procs}
        known.update((keys[row[0]], (row[4][:1], row[5])) for row in rows if row[0] in keys)

    def _real(self, now: float, procs: list[KProc] | None) -> list[tuple]:
        rows = ps_rows(self.ps())
        self.last_ps, self.ps_runs = now, self.ps_runs + 1
        if procs is not None:
            self.known, self.asked = {}, set()
            self._adopt(self.known, procs, rows)
        return rows

    def rows(self) -> list[tuple]:
        """(pid, ppid, pgid, etime, stat, command) per process, as ps_rows()
        gives for ps output; etime is a callable between real ps runs."""
        now = self.clock()
        procs = self._processes()
        if procs is None or now - self.last_ps >= PS_FULL_EVERY:
            return self._real(now, procs)
        if self.watched is None:
            self.watched = _watched_uids()
        rows, known, others = self._rebuild(procs)
        if others and now - self.last_users >= PS_MIN_GAP:
            self.last_users, self.user_runs = now, self.user_runs + 1
            self.asked.update(key for key, entry in known.items() if entry is None)
            self._adopt(known, procs, ps_rows(self.ps_users(others)))
            self.known = known
            rows, known, _ = self._rebuild(procs)
        self.known = known
        return rows

    def _rebuild(self, procs: list[KProc]) -> tuple:
        """(rows, what is known per process, real uids worth a `ps -U` now)."""
        wall, rows, known, others, me = int(self.wall()), [], {}, set(), os.getuid()
        for k in procs:
            if k.pid == 0:
                continue   # the kernel task: ps -A never lists it
            key = (k.pid, k.start, k.start_us, k.comm)
            entry = self.known.get(key, False)
            if entry is False:
                command = "<defunct>" if k.pstat == SZOMB else self.kernel.command(k.pid)
                entry = None if command is None else (None, command)
            known[key] = entry
            if entry is None:   # shown once a ps prints it
                ruid = k.uid if k.ruid is None else k.ruid
                if (ruid in self.watched or ruid == me) and key not in self.asked:
                    others.add(ruid)
                continue
            letter, command = entry
            if k.pstat == SZOMB:
                command = "<defunct>"
            rows.append((k.pid, k.ppid, k.pgid, functools.partial(_ps_elapsed, wall - k.start),
                         _ps_state(k, letter), command))
        return rows, known, others


_PROCESS_TABLE = ProcessTable()


def collect(remembered: dict | None = None, table: ProcessTable | None = None) -> Snapshot:
    return snapshot_from((table or _PROCESS_TABLE).rows(), os.getpid(), remembered)


def family(role: str) -> str:
    for name in ("OpenCode", "Kilo", "Claude", "Codex", "llmtool", "ai-loop", "Actions runner"):
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


def summary(processes: list[Process], lingering: list, sessions=None,
            ci_view: "CiView | None" = None, now: float | None = None) -> str:
    # An agent family counts its open CLIs, the processes office rows stand
    # for; its daemons and servers are listed in the table, not counted.
    # Runners and Workers are counted as the office rows count them: one Mac
    # runner per Listener (its Worker process is not another runner), the
    # Linux runners in GitHub's list only while that list is fresh, and the
    # EC2 remote-test worker, which is always a member.
    members = {p.pid for p in agent_clis(processes, sessions)}
    counts: dict[str, int] = {}
    for p in processes:
        if (p.role.startswith(" ") or family(p.role) == "Actions runner"
                or family(p.role) in AGENT_FAMILIES and p.pid not in members):
            continue
        counts[family(p.role)] = counts.get(family(p.role), 0) + 1
    order = ("Claude", "Codex", "OpenCode", "Kilo", "llmtool", "ai-loop")
    now = time.time() if now is None else now
    fresh = (ci_view is not None and ci_view.runners is not None and not ci_view.error
             and now - ci_view.seen_at <= GITHUB_STALE)
    linux = sum(_runner_system(runner[0], runner[1]) == "linux" for runner in ci_view.runners) if fresh else 0
    parts = [f"{k} {counts[k]}" for k in order if k in counts] + [
        f"Mac runner {sum(p.role == 'Actions runner' for p in processes)}",
        f"EC2 runner {linux if fresh else '未確認'}", "EC2 worker 1"]
    parts += [f"other {counts['other']}"] if "other" in counts else []
    text = "  ".join(parts)
    return text + (f"  ·  ⚠ possible leftover groups: {', '.join(map(str, lingering))}" if lingering else "")


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def disk_status(usage: tuple[int, int] | None) -> str:
    if usage is None:
        return ""
    percent, free_bytes = usage
    return f"disk {percent}% ({free_bytes / (1024 ** 3):.1f}G free)"


def disk_alert(usage: tuple[int, int] | None) -> str:
    if usage is None:
        return ""
    percent, free_bytes = usage
    if percent < DISK_WARNING_PERCENT:
        return ""
    level = "ATTENTION" if percent >= DISK_ATTENTION_PERCENT else "WARNING"
    return (f"{level}: disk {percent}% used ({free_bytes / (1024 ** 3):.1f}G free); "
            "the PM can retire old D6 worktrees under the workspace root.")


class _DarwinStatFS(ctypes.Structure):
    """64-bit Darwin struct statfs layout (sys/mount.h)."""
    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint64 * 7),
    ]


def _read_darwin_statfs(path: str) -> tuple[int, int, int, int]:
    """Return blocks, free blocks, available blocks and block size via libc."""
    libc = ctypes.CDLL(None, use_errno=True)
    machine = platform.machine()
    if machine == "x86_64":
        # On Intel macOS, statfs uses the legacy 32-bit-inode ABI. Its symbol
        # cannot safely fill the 64-bit structure declared above.
        statfs = getattr(libc, "statfs64", None)
    elif machine == "arm64":
        statfs = getattr(libc, "statfs", None)
    else:
        statfs = None
    if statfs is None:
        raise OSError("compatible Darwin statfs symbol is unavailable")
    statfs.argtypes = (ctypes.c_char_p, ctypes.POINTER(_DarwinStatFS))
    statfs.restype = ctypes.c_int
    result = _DarwinStatFS()
    encoded_path = os.fsencode(path)
    if statfs(encoded_path, ctypes.byref(result)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), path)
    stats = result.f_blocks, result.f_bfree, result.f_bavail, result.f_bsize
    blocks, free_blocks, available_blocks, block_size = stats
    if (block_size <= 0 or available_blocks > blocks or free_blocks > blocks):
        raise OSError("invalid Darwin statfs result")
    return stats


def _disk_volume_stats(path: str) -> tuple[int, int, int, int]:
    """Use Darwin statfs (matching df on APFS), statvfs elsewhere or on error."""
    if sys.platform == "darwin":
        try:
            stats = _read_darwin_statfs(path)
            blocks, free_blocks, available_blocks, block_size = stats
            if (block_size <= 0 or available_blocks > blocks or free_blocks > blocks):
                raise OSError("invalid Darwin statfs result")
            return stats
        except Exception:
            pass
    usage = os.statvfs(path)
    return usage.f_blocks, usage.f_bfree, usage.f_bavail, usage.f_frsize


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
        self.agents = None   # ClaudeAgents: `claude agents` per account; None asks no CLI
        self.office: list = []
        self.office_state: dict = {}
        self.office_error: str | None = None   # why the office view could not be built
        self.office_due = 0.0   # monotonic time of the next office rebuild
        self.pm_board = PmBoard("missing", None, [], [], [])
        self.pm_board_due = 0.0
        self.one_shot: dict = {}   # pid -> [started, last_seen, gone] for Codex-started claude runs
        self.records: list = []   # llmtool task-record lines, refreshed with the office view
        self.opencode_route = None
        self.opencode_route_failed = False
        self.quota: list[str] = []
        self.quota_due = 0.0
        self.loaded_blob = script_blob()
        self.behind = False
        self.behind_due = 0.0
        self.d6: list = []   # D6 task lines, refreshed with the office view
        self.ec2_heads = None
        self.stuck_records = None
        self.version = monitor_version()   # the code this process loaded
        self.digest = script_digest()
        self.stale = False   # the file on disk changed since start: a restart is needed
        self.disk_usage: tuple[int, int] | None = None  # used percent, available bytes
        self.disk_due = 0.0

    def refresh_disk_usage(self, now: float | None = None) -> None:
        """Read the home volume at most once per minute; failure is silent."""
        now = time.monotonic() if now is None else now
        if now < self.disk_due:
            return
        self.disk_due = now + DISK_REFRESH_SECONDS
        try:
            blocks, free_blocks, available_blocks, block_size = _disk_volume_stats(
                os.path.expanduser("~"))
            used = (blocks - free_blocks) * block_size
            available = available_blocks * block_size
            total = used + available
            if total <= 0:
                self.disk_usage = None
                return
            # On APFS, bfree == bavail, so this is (blocks - available) / blocks;
            # it tracks df closely but can differ by about one percentage point.
            percent = max(0, min(100, math.ceil(used * 100 / total)))
            self.disk_usage = (percent, available)
        except Exception:
            self.disk_usage = None

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
        self.refresh_disk_usage()
        now = time.monotonic()
        if now >= self.pm_board_due:
            self.pm_board_due = now + 30.0
            try:
                self.pm_board = read_pm_board(time.time())
            except Exception:
                pass
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
            new.append((stamp, "i", f"monitor started; {summary(result.processes, result.lingering, ci_view=self.github.ci_view if self.github else None)}"))
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
        self.one_shot, finished, ended = track_one_shot(self.one_shot, result.processes, time.time())
        if self.github is not None:
            self.github.want_ci = any(p.role == "Actions runner" for p in result.processes)
            self.github.want_hosted_ci = True
        new += [(stamp, "i", text) for text in ended]
        if time.monotonic() >= self.office_due:
            # Session scans, tails, sqlite and AGY lookups are the costly part of a
            # poll; they run at most every OFFICE_EVERY seconds, not every tick.
            self.office_due = time.monotonic() + OFFICE_EVERY
            try:
                self.records = [line for _, line in llmtool_task_records(time.time())]
            except Exception:  # optional, like task evidence
                pass
            try:
                self.opencode_route = llmtool_route_outcomes(time.time()).get("opencode")
                self.opencode_route_failed = False
            except Exception:  # preserve the last successful route summary
                self.opencode_route_failed = True
            switch = worker_dispatch(load_dispatch(canonical_root())[0])[0].get("opencode-developer")
            self.opencode_paused = (f"{switch[1]} 暫停派工（{switch[2]}）"
                                    if switch and switch[0] == "PAUSED" else "")
            if time.monotonic() >= self.quota_due:
                self.quota_due = time.monotonic() + 60.0
                try:
                    self.quota = quota_warnings(time.time())
                except Exception:  # local optional evidence; preserve last good value
                    pass
            if time.monotonic() >= self.behind_due:
                self.behind_due = time.monotonic() + 300.0
                try:
                    self.behind = behind_main(self.loaded_blob, main_script_blob())
                except Exception:  # unexpected errors keep the last result; main_script_blob returns empty for unknown, which clears the warning.
                    pass
            try:
                office = office_rows(result.processes, finished_runs=finished,
                                     verdicts=self.github.verdicts if self.github else None,
                                     ci=self.github.ci_runs if self.github else None,
                                     ci_view=self.github.ci_view if self.github else None,
                                     prs=(self.github.branch_prs if self.github and not self.github.read_failed
                                          else None),
                                     runner_root=runner_roots(),
                                     agents=None if self.agents is None
                                     else self.agents.sessions(claude_config_dirs()))
            except Exception as exc:  # never break process monitoring, but never show stale rows
                message = type(exc).__name__
                if message != self.office_error:
                    new.append((stamp, "?", f"office view unavailable: {message}"))
                # A fresh baseline after the error, so recovery is not logged as changes.
                self.office, self.office_state, self.office_error = [], {}, message
            else:
                state, changes = office_events(self.office_state, office)
                new += [(stamp, kind, text) for kind, text in changes]
                self.office, self.office_state, self.office_error = office, state, None
                digest = script_digest()
                if self.digest == "?":
                    self.digest = digest   # unreadable at start: the first real read is the baseline
                elif not self.stale and digest not in (self.digest, "?"):
                    self.stale = True
                    new.append((stamp, "!", f"監控程式已更新，這個畫面還是舊版（{self.version}），請按 q 後重開"))
                try:
                    self.d6 = d6_task_lines(canonical_root(), office, time.time(),
                                            self.github.branch_prs if self.github else None,
                                            self.github.verdicts if self.github else None)
                except Exception:  # optional, like task evidence; never show a stale list
                    self.d6 = []
                try:
                    self.ec2_heads = ec2_head_results()
                except Exception:  # optional local evidence; preserve the last good snapshot
                    pass
                try:
                    self.stuck_records = stuck_records()
                except Exception:  # optional local evidence; preserve the last good snapshot
                    pass
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
import heapq
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
    except (OSError, ValueError, RecursionError):
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


_task_dir_cache: dict = {}   # evidence dir -> (record file stats, _read_task_dir())


def _read_task_dir(directory: str, names: list, files: dict) -> tuple:
    """(manifest, [(stage, attempt, result, started, result_at)]) from the
    directory's JSON records; files: their os.stat results."""
    manifest = _read_json(os.path.join(directory, "manifest.json")) or {}
    stages = []
    for entry in names:
        found = STAGE_FILE.match(entry)
        if not found:
            continue
        stage = found.group(1)
        attempt = _read_json(os.path.join(directory, entry)) or {}
        result_name = f"{stage}-result.json"
        result = _read_json(os.path.join(directory, result_name)) or {}
        result_at = files[result_name].st_mtime if result and result_name in files else 0.0
        stages.append((stage, attempt, result, files[entry].st_mtime, result_at))
    return manifest, stages


def scan_tasks(pattern: str = TASK_GLOB, window_hours: float = 12.0,
               hosts: frozenset = frozenset()) -> list[Task]:
    """Recently active llmtool harness evidence directories, newest first.

    hosts: (evidence dir, stage) pairs of live harness processes; a stage
    without a result is RUNNING only when its own host is observed alive.
    """
    cutoff = time.time() - window_hours * 3600
    tasks = []
    for directory in glob.glob(pattern):
        # Not the directory's own mtime: the daily /tmp cleaner deleting old
        # files inside it makes a days-old task look new.
        try:
            names = os.listdir(directory)
            files = {name: os.stat(os.path.join(directory, name)) for name in names
                     if name == "manifest.json" or STAGE_FILE.match(name)
                     or name.endswith("-result.json")}
            newest = max(st.st_mtime for st in files.values())
        except (OSError, ValueError):
            continue
        if "manifest.json" not in files or not stat.S_ISREG(files["manifest.json"].st_mode) \
                or newest < cutoff:
            continue
        # The JSON records are parsed again only when one of them changed.
        key = tuple(sorted((name, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
                           for name, st in files.items()))
        cached = _task_dir_cache.get(directory)
        if cached is None or cached[0] != key:
            cached = (key, _read_task_dir(directory, names, files))
            _remember(_task_dir_cache, directory, cached)
        manifest, raw_stages = cached[1]
        root = str(manifest.get("root") or "")
        stages = []
        for stage, attempt, result, started, result_at in raw_stages:
            code = str(result.get("code") or "")
            code = code if CODE_TEXT.fullmatch(code) else ("?" if result else "")
            cleanup = "" if not result else ("ok" if result.get("cleanup_confirmed") is True else "UNKNOWN")
            state_root = str(attempt.get("state_root") or "")
            steps, tin, tout, idle, tools, meta = _native(state_root, root, directory)
            live = (os.path.realpath(directory), stage) in hosts
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


EDIT_TOOLS = frozenset({"edit", "write", "patch", "multiedit", "apply_patch"})


def edit_failures(stage: Stage) -> tuple[int, int]:
    """(failed, finished) file-changing tool calls of a stage."""
    edits = [tool for tool in stage.tools if tool.name in EDIT_TOOLS and tool.status in FINAL_TOOL]
    return sum(tool.status == "error" for tool in edits), len(edits)


def task_lines(tasks: list[Task], limit: int = 6, route=None,
               route_failed: bool = False, paused: str = "") -> list[str]:
    if paused:   # Owner #120 B1: a PAUSED switch, not "no record" or a read failure
        return [f"OpenCode 已暫停（{paused}）"] + (task_lines(tasks, limit) if tasks else [])
    if not tasks:
        if route:
            age, total, succeeded, words = route
            return [f"/private/tmp 沒有 OpenCode 階段細節；llmtool Task 紀錄近 7 天 {total} 次，成功 {succeeded} 次，"
                    f"最近一次 {_short_ago(age)}：{words}"]
        if route_failed:
            return ["OpenCode Task 紀錄讀取失敗，狀態未確認"]
        return ["近 7 天 llmtool Task 紀錄沒有 OpenCode 執行"]
    lines = []
    for task in tasks[:limit]:
        lines.append(f"任務 {task.task_id}   分支 {task.branch or '-'}")
        if not task.stages:
            lines.append("    （已準備，還沒開始執行）")
        for stage in task.stages:
            when = (datetime.fromtimestamp(stage.started).strftime("%m-%d %H:%M")
                    if stage.started else "--- --:--")
            failed, edits = edit_failures(stage)
            note = (f"（改檔 {edits} 次全部失敗）" if failed and failed == edits
                    else f"（改檔 {failed}/{edits} 次失敗）" if failed else "")
            lines.append(f"    {when}  {stage.name:<8} {stage.model:<9} {stage_state(stage)}{note}  "
                         f"清理 {stage.cleanup or '-'}  步數 {stage.steps}  "
                         f"輸入 {stage.tokens_in / 1000:.1f}k / 輸出 {stage.tokens_out / 1000:.1f}k")
            if stage.meta not in ("ok", "none"):
                lines.append(f"        任務資料 {stage.meta}")
            for tool in stage.tools[-6:]:
                mark = "✓" if tool.status == "completed" else tool.status
                lines.append(f"        {tool.name:<6} {_safe_relative_reference(tool.path)}  {mark}")
            if len(stage.tools) > 6:
                lines.append(f"        … 另有 {len(stage.tools) - 6} 次較早的工具呼叫")
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
    "waiting_owner": frozenset({"BLOCKED", "WAITING", "UNKNOWN", "ANSWERED"}),
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
    "RETURNED", "FAILED", "BLOCKED", "UNKNOWN", "OPENCODE_EDITS_FAILED",
})
ACTIVITY_RESULT_LABELS = {
    "OPENCODE_OUTPUT_RETURNED": "完成，已拿到輸出",
    "OPENCODE_EDITS_FAILED": "有輸出，但改檔全部失敗",
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
                failed, edits = edit_failures(stage)
                code = stage.result if stage.result in ACTIVITY_RESULT_CODES else "UNKNOWN"
                if code == "OPENCODE_OUTPUT_RETURNED" and failed and failed == edits:
                    code = "OPENCODE_EDITS_FAILED"
                events.append(ActivityEvent(stage.result_at, "worker_result", task_id,
                                            stage_name, code,
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
# On by default (Owner 2026-10-24); --no-github turns it off. Used only by Activity/--once. Read-only `gh api` GETs of recent issue/PR comments
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
    "waiting_owner": (frozenset({"Integrator", "PM", "Owner"}),
                      frozenset({"Owner", "PM", "Integrator"})),
    "developer_dispatch": (frozenset({"PM", "Integrator"}),
                           frozenset({"Codex", "AGY", "OpenCode", "Developer", "CompanyGW"})),
    "developer_progress": (frozenset({"Codex", "OpenCode", "CompanyGW"}),
                            frozenset({"PM", "Integrator", "Reviewer", "Owner", "CI"})),
    "test_result": (frozenset({"Codex", "OpenCode", "CompanyGW"}),
                    frozenset({"Tests", "CI"})),
}
MARKER_NAMES = {"CompanyGW": "home LLM gw-agent", "Tests": "tests"}
# waiting_owner runs both ways: the PM/Integrator asks the Owner (BLOCKED, WAITING,
# UNKNOWN) and an Owner-login comment answers as ANSWERED. Any other pairing of
# direction, author login, and status is not a typed event.
_WAITING_ASK = frozenset({"BLOCKED", "WAITING", "UNKNOWN"})
WAITING_OWNER_DIRECTIONS = {
    ("Integrator", "Owner"): _WAITING_ASK, ("PM", "Owner"): _WAITING_ASK,
    ("Owner", "PM"): frozenset({"ANSWERED"}), ("Owner", "Integrator"): frozenset({"ANSWERED"}),
}


def _marker_direction_ok(kind: str, actor: str, target: str, status: str) -> bool:
    if kind != "waiting_owner":
        return True
    return status in WAITING_OWNER_DIRECTIONS.get((actor, target), frozenset())
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
# Markers are honoured only from the canonical Owner and AI logins. The AI login
# can relay comments, but an actor=Owner claim is valid only from the Owner login.
def _load_github_logins():
    """Load the canonical identities directly, without importing llmtool."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "llmtool", "llmtool", "github_identity.py")
    name = "_llmtool_monitor_github_identity"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load GitHub identity module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return frozenset((module.owner_login(), module.ai_login())), module.owner_login()


def _github_identity_state():
    """Load trusted logins; identity failures disable all marker trust."""
    try:
        logins, owner = _load_github_logins()
        return logins, owner, ""
    except Exception:
        return frozenset(), None, "GitHub 身分設定無法載入；留言標記信任已停用"


TRUSTED_GITHUB_LOGINS, OWNER_GITHUB_LOGIN, GITHUB_IDENTITY_STATUS = _github_identity_state()


def _trusted_github_comment(comment):
    user = comment.get("user") if type(comment) is dict else None
    login = user.get("login") if type(user) is dict else None
    return type(login) is str and login in TRUSTED_GITHUB_LOGINS


URL = re.compile(r"https?://\S+")
FEED_NOISE = re.compile(r"(?i)^(?:subpm-lease\b|correction\b)")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
GITHUB_TIMEOUT = 20
DEFAULT_GITHUB_REPO = "davidcatcat/davidcattoolsproject"
GITHUB_REFRESH = 30.0
GITHUB_BACKOFF = 300.0   # the wait doubles after each failed read, up to this
GITHUB_JOBS_TTL = 60.0   # an unfinished run's job list is reused while its updated_at stays the same, at most this long
GITHUB_STALE = 3 * GITHUB_REFRESH   # older runner data is shown as 未確認, never as offline
CI_JOB_READS = 5   # finished runs' job lists read per refresh (cached)
CI_RUN_READS = 10  # unfinished runs whose job lists are read per refresh (oldest first)
GITHUB_LIMIT = 16 << 20   # one page of 100 comments; a larger page fails as a stale status
GITHUB_PAGE = 100    # comments per request (the API maximum)
GITHUB_PAGES = 10    # about 600 comments in 12 hours were seen on 2026-11-05
GITHUB_FULL_READ = 1800.0   # full comment window re-read interval (30 minutes)
GITHUB_COMMENT_OVERLAP = 120.0   # overlap seconds subtracted from newest updated_at


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


def _progress_summary(body: str) -> str:
    """Show safe D6/family labels when a progress comment starts with a Task id."""
    headline = _headline(body)
    if not re.fullmatch(r"Task:? \[已遮蔽\]", headline):
        return headline
    d6 = family = ""
    for line in body.splitlines() if type(body) is str else []:
        match = re.fullmatch(r"D6:\s*(.+)", line)
        if (match and not re.search(r"[0-9A-Fa-f]{32,}", match.group(1))
                and AGENT_ID.fullmatch(match.group(1))):
            d6 = match.group(1)
            continue
        match = re.fullmatch(r"Family:\s*(.+)", line)
        if (match and not re.search(r"[0-9A-Fa-f]{32,}", match.group(1))
                and AGENT_ID.fullmatch(match.group(1))):
            family = match.group(1)
    if family and d6:
        summary = f"Family {family}（D6 {d6}）"
    elif family:
        summary = f"Family {family}"
    elif d6:
        summary = f"D6 {d6}"
    else:
        return headline
    return _headline(summary)


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
        if not _marker_direction_ok(kind, actor, target, status):
            return []
        if actor == target:
            return []
        summary = _progress_summary(body) if kind == "developer_progress" else _headline(body)
        events.append(ActivityEvent(when, kind, "", "", status, f"#{number}",
                                    actor=actor, target=target, summary=summary))
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
        trusted = _trusted_github_comment(comment)
        typed = _marker_events(body, when, number) if trusted else []
        if typed and comment.get("user", {}).get("login") != OWNER_GITHUB_LOGIN:
            typed = [event for event in typed if event.actor.casefold() != "owner"]
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


# Anything a marker addresses to the Owner is a decision the Owner owes, whatever
# type the PM chose (repair_gate, waiting_owner, ...). Fields are read loosely
# here (a target may be a long task name); only trusted, leading marker lines
# count. A later Owner-login marker with actor=Owner, an AI/Owner relay marker
# with a settled status, or the PR closing, settles it.
# A field starts after whitespace or right after "activity:", so a value such
# as note=actor=Owner is never read as a field of its own.
OWNER_FIELD = re.compile(r"(?:(?<=\s)|(?<=activity:))(type|actor|target|status)="
                         r"([A-Za-z0-9_.#-]{1,64})(?![\w.#=-])")
OWNER_SETTLED = frozenset({"APPROVED", "DECLINED", "DENIED", "GRANTED", "REJECTED", "ANSWERED",
                           "RESOLVED", "DONE", "CLOSED", "CANCELLED", "WITHDRAWN", "AUTHORIZED",
                           "ACCEPTED", "ACKNOWLEDGED", "SUPERSEDED"})
OWNER_SHOWN = 5   # at most this many ★ lines; the rest are counted


@dataclass(frozen=True)
class OwnerPending:
    when: float
    reference: str   # "#310"
    status: str
    summary: str
    kind: str = ""
    needs_owner_approval: bool = False


_MARKER_ROLES = {name.casefold(): name for name in ("PM", "Integrator", "Owner")}


def _owner_settlement_valid(fields) -> bool:
    """A waiting_owner marker settles only in a valid direction/status pairing (the same
    check as typed Activity); other marker types keep their loose settlement."""
    if fields.get("type", "").casefold() != "waiting_owner":
        return True
    actor = _MARKER_ROLES.get(fields.get("actor", ""), "")
    target = _MARKER_ROLES.get(fields.get("target", ""), "")
    return _marker_direction_ok("waiting_owner", actor, target, fields.get("status", ""))


def owner_pending(comments, pulls, earlier=()) -> list[OwnerPending]:
    """Requests addressed to the Owner that nothing later has settled (pure).
    earlier: requests still open at the previous read; they stay open until
    something in this read settles them, so a request older than the window
    the comments cover does not silently drop out."""
    marked = []
    for comment in comments if type(comments) is list else []:
        if type(comment) is not dict:
            continue
        login = (comment.get("user", {}).get("login")
                 if type(comment.get("user")) is dict else None)
        number = _issue_number(comment.get("issue_url"))
        body = comment.get("body")
        if not (_trusted_github_comment(comment) and number
                and type(body) is str):
            continue
        for line in body.splitlines()[:MAX_MARKERS]:
            if MARKER.fullmatch(line) is None:   # leading, column-zero marker lines only
                break
            fields = {}
            for key, value in OWNER_FIELD.findall(line):
                value = value.rstrip("-")   # "status=APPROVED-->" written without a space
                fields.setdefault(key, value.casefold() if key in ("actor", "target") else value.upper())
            order = comment.get("id") if type(comment.get("id")) is int else 0
            marked.append((_github_time(comment.get("created_at")), order, number, fields, body,
                           login))
    closed = {}
    for pull in pulls if type(pulls) is list else []:
        if type(pull) is dict and type(pull.get("number")) is int:
            closed[str(pull["number"])] = max(_github_time(pull.get("merged_at")),
                                             _github_time(pull.get("closed_at")))
    # One open request per thread (the latest); an Owner marker settles the thread.
    open_requests = {item.reference[1:]: item for item in earlier}
    for when, _, number, fields, body, login in sorted(marked, key=lambda item: item[:2]):
        status = fields.get("status", "")
        held = open_requests.get(number)
        if held is not None and when < held.when:
            continue   # older than the open request: neither asks nor settles again
        actor_is_owner = fields.get("actor") == "owner"
        actor_owner = actor_is_owner and login == OWNER_GITHUB_LOGIN
        relay_settles = (not actor_is_owner and fields.get("target") == "owner"
                         and status in OWNER_SETTLED)
        settles = actor_owner or relay_settles
        if settles and not _owner_settlement_valid(fields):
            continue   # e.g. actor=Owner status=WAITING: not a typed answer, settles nothing
        if settles:
            open_requests.pop(number, None)
        elif fields.get("target") == "owner" and when:
            open_requests[number] = OwnerPending(when, f"#{number}", status, _headline(body),
                                                fields.get("type", "").casefold(),
                                                fields.get("type", "").casefold() == "waiting_owner"
                                                and "owner-approval" in body.casefold())
    return sorted((item for number, item in open_requests.items()
                   if closed.get(number, 0.0) <= item.when), key=lambda item: item.when)


def branch_prs(pulls, repo: str | None = None) -> dict[str, tuple[int, str]]:
    """{head branch: (PR number, MERGED/CLOSED/OPEN)} from a pulls listing; the
    newest PR wins when a branch was reused. With repo, PRs from forks (whose
    branch may share a name with ours) are left out."""
    found: dict[str, tuple[int, str]] = {}
    for pull in sorted((p for p in pulls if type(p) is dict) if type(pulls) is list else [],
                       key=lambda p: p.get("number") if type(p.get("number")) is int else 0):
        head = pull.get("head")
        ref = head.get("ref") if type(head) is dict else None
        source = head.get("repo") if type(head) is dict else None
        if repo is not None and (type(source) is not dict or type(source.get("full_name")) is not str
                                 or source["full_name"].casefold() != repo.casefold()):
            continue
        if type(pull.get("number")) is not int or type(ref) is not str or not BRANCH.fullmatch(ref):
            continue
        state = ("MERGED" if pull.get("merged_at") else
                 "CLOSED" if pull.get("state") == "closed" else "OPEN")
        found[ref] = (pull["number"], state)
    return found


@dataclass(frozen=True)
class CiView:
    """What GitHub last said about self-hosted runners, replaced as a whole."""
    runners: list | None    # ci_runners(); None = never read
    seen_at: float          # clock time of the last good runner list
    error: str = ""         # why the last read failed ("" = it did not)
    finished: dict | None = None   # runner -> (job text, result, end time) of its last job
    queued: tuple = ()      # ci_queued_jobs() of runs waiting for a runner


def _gh_error(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "逾時"
    text = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    return {"HTTP 403": "沒有權限（HTTP 403）", "HTTP 404": "找不到（HTTP 404）"}.get(
        next(iter(re.findall(r"HTTP \d{3}", text)), ""), text[:40])


GITHUB_API_HOST = "api.github.com"
GITHUB_DIRECT_PAUSE = 300.0   # seconds of gh-only reads after a failed direct read
GITHUB_TOKEN = re.compile(r"[A-Za-z0-9_]{20,255}")


class _UseGh(Exception):
    """The direct API read is not usable for this call: ask gh instead."""


@dataclass(frozen=True)
class MainHealth:
    sha: str
    title: str
    committed_at: float
    linux: tuple | None = None   # (status, conclusion, sha)
    mac: tuple | None = None     # (status, conclusion, created_at, sha)


class GitHubSource:
    """Rate-limited, read-only GitHub activity. Failures become a status line."""

    def __init__(self, repo: str, window_hours: float = 12.0, runner=None, clock=time.time,
                 direct: bool = False, want_hosted_ci: bool = False) -> None:
        if not GITHUB_REPO.fullmatch(repo or ""):
            raise ValueError("--github expects OWNER/REPO")
        self.repo, self.window = repo, window_hours * 3600
        self.runner = runner or subprocess.run
        self.clock = clock
        # direct: GET api.github.com with gh's own token instead of forking gh
        # for every read (~0.15 s CPU each, ~10 per refresh; Issue #625). No
        # token, or no connection before the first direct answer, means gh for
        # good; any later failed direct read (no connection, 401, 5xx, too
        # large, not JSON) is read again with gh and pauses direct reads for
        # GITHUB_DIRECT_PAUSE; another non-200 answer is read with gh once.
        # The token stays in this object, never in a message.
        self.direct = direct
        self._token: str | None = None
        self._conn = None
        self._direct_ok = False
        self._direct_paused = -math.inf   # monotonic time until which reads use gh (_pause)
        self.connect = lambda: http.client.HTTPSConnection(GITHUB_API_HOST, timeout=GITHUB_TIMEOUT)
        self.events: list[ActivityEvent] = []
        self.successful_read = False
        self.read_failed = False
        self.comments_cut = False
        self._comments: list[dict] = []
        self._full_read_at: float | None = None
        self._comment_updated: float | None = None
        self.pending: list[OwnerPending] = []
        self.scope = ""   # what the ★ list could see, said next to it
        self.branch_prs: dict[str, tuple[int, str]] = {}   # head branch -> (PR, MERGED/CLOSED/OPEN)
        self.verdicts: dict | None = None   # review_verdicts(); None until GitHub was read
        self.board_pulls: list[BoardPull] | None = None  # same pulls page as branch_prs
        self.board_page_count: int | None = None
        self.board_oldest_updated: float | None = None
        self.ci_runs: list | None = None    # ci_runs(); None until GitHub was read
        self.ci_heads: dict | None = None   # latest Linux/Mac run per head from same page
        # Open PRs' checks from this refresh's one GraphQL read
        # (pr_head_checks()), keyed by (PR number, full 40-character head
        # SHA); its keys are also the open PRs the PR board shows
        # (board_open_pulls()). None while that read failed or GitHub was not
        # read. Never kept across refreshes, so a rerun on the same head shows
        # on the next one.
        self.pr_checks: dict | None = None
        self.want_ci = False   # set while a self-hosted runner is on this Mac
        self.want_hosted_ci = want_hosted_ci
        self.want_main = False
        self.main_health: MainHealth | None = None
        self.main_error = ""
        self.main_read_at = None
        self.ci_view: CiView | None = None   # self-hosted runners; None until GitHub was read
        self._finished: dict = {}   # run id -> ci_finished_jobs(); a finished run never changes
        self._jobs: dict = {}   # run id -> (updated_at, read at, jobs payload) of unfinished runs
        self.status = "GitHub：尚未讀取" + (f"；{GITHUB_IDENTITY_STATUS}" if GITHUB_IDENTITY_STATUS else "")
        self.fetched = None   # monotonic time of the last attempt
        self.settled = None   # monotonic time the last attempt ended
        self.wait = GITHUB_REFRESH   # seconds between attempts; grows while gh keeps failing

    def refresh_rule_alerts(self, pulls, path=None) -> None:
        """Check at most five newly merged main PRs against the canonical path list."""
        if path is None:
            path = RULE_ALERT_FILE
        try:
            state = _load_rule_alerts(path)
            if state is None:
                _save_rule_alerts(path, {"schema_version": 1, "baseline": int(self.clock()),
                                         "checked": [], "alerts": []})
                return
            checked = set(state["checked"])
            candidates = []
            for pull in pulls:
                if type(pull) is not dict or type(pull.get("number")) is not int:
                    continue
                number = pull["number"]
                base = pull.get("base")
                if (number in checked or type(base) is not dict
                        or base.get("ref") != "main"):
                    continue
                merged_at = _github_time(pull.get("merged_at"))
                if not merged_at or merged_at < state["baseline"]:
                    continue
                candidates.append((merged_at, number, pull))
            candidates.sort(key=lambda item: (item[0], item[1]))
            matcher = None
            matcher_path = os.path.join(canonical_root(), "tools", "llmtool", "llmtool",
                                        "sensitive_paths.py")
            try:
                spec = importlib.util.spec_from_file_location("llmtool_monitor_sensitive_paths",
                                                              matcher_path)
                if spec is None or spec.loader is None:
                    raise ImportError("sensitive matcher unavailable")
                matcher = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(matcher)
                if not callable(getattr(matcher, "sensitive_matches", None)):
                    raise ImportError("sensitive matcher unavailable")
            except Exception:
                matcher = None

            for _, number, pull in candidates[:5]:
                try:
                    files = self._get(f"repos/{self.repo}/pulls/{number}/files?per_page=100")
                except Exception:
                    break
                if type(files) is not list:
                    break
                if len(files) >= 100:
                    paths = ["<too-many-files>"]
                elif matcher is None:
                    paths = ["<sensitive-list-unreadable>"]
                else:
                    try:
                        paths = list(matcher.sensitive_matches(files))
                    except Exception:
                        paths = ["<sensitive-list-unreadable>"]
                if paths:
                    title = pull.get("title")
                    state["alerts"].append({"pr": number,
                                             "title": title if type(title) is str else "",
                                             "paths": paths})
                state["checked"].append(number)
                state["checked"] = state["checked"][-500:]
                checked.add(number)
            _save_rule_alerts(path, state)
        except Exception:
            # This auxiliary local alert must never affect GitHub refresh health.
            return

    def _get(self, path: str):
        if self.direct and time.monotonic() >= self._direct_paused:
            try:
                return self._get_direct(path)
            except _UseGh:
                pass
        gh = shutil.which("gh")
        if not gh:
            raise RuntimeError("找不到 gh 指令")
        done = self.runner([gh, "api", "-X", "GET", path], capture_output=True,
                           timeout=GITHUB_TIMEOUT, check=False)
        if done.returncode != 0:
            code = re.search(rb"HTTP (\d{3})", getattr(done, "stderr", None) or b"")
            raise RuntimeError(f"gh api 失敗（HTTP {code.group(1).decode()}）" if code
                               else f"gh api 失敗（exit {done.returncode}）")
        if len(done.stdout) > GITHUB_LIMIT:
            raise RuntimeError("回應過大")
        try:
            return json.loads(done.stdout)
        except RecursionError:   # nested too deep: a bad answer, like any other non-JSON
            raise ValueError("JSON nested too deeply") from None

    def _get_direct(self, path: str):
        if self._token is None:
            gh = shutil.which("gh")
            if not gh:
                raise RuntimeError("找不到 gh 指令")
            done = self.runner([gh, "auth", "token", "--hostname", "github.com"],
                               capture_output=True, timeout=GITHUB_TIMEOUT, check=False)
            token = (done.stdout or b"").strip().decode("ascii", "replace")
            if done.returncode or not GITHUB_TOKEN.fullmatch(token):
                self.direct = False
                raise _UseGh
            self._token = token
        headers = {"Authorization": f"token {self._token}", "Accept": "application/vnd.github+json",
                   "User-Agent": "llmtool-monitor"}
        for attempt in (1, 2):
            fresh = self._conn is None
            if fresh:
                self._conn = self.connect()
            try:
                self._conn.request("GET", "/" + path, headers=headers)
                response = self._conn.getresponse()
                body = response.read(GITHUB_LIMIT + 1)
                if response.getheader("Connection", "").lower() == "close":
                    self._close()
                break
            except Exception:
                self._close()
                if not fresh and attempt == 1:
                    continue   # a kept-alive connection the server had closed
                if not self._direct_ok:   # e.g. only a proxy reaches GitHub: gh knows how
                    self.direct = False
                raise self._pause() from None
        self._direct_ok = True
        if response.status == 401:   # gh may hold a renewed token
            self._token = None
            raise self._pause()
        if response.status >= 500:
            raise self._pause()
        if response.status != 200:
            # A real GitHub answer (a redirect, 403, 404...): gh follows the
            # redirect or words the error as before; the next read is direct.
            raise _UseGh
        if len(body) > GITHUB_LIMIT:
            raise self._pause()
        try:
            return json.loads(body)
        except (ValueError, RecursionError):
            raise self._pause() from None

    def _pause(self) -> "_UseGh":
        """Every read goes through gh for GITHUB_DIRECT_PAUSE seconds: a broken
        direct path costs one failed request per pause, not one per read. gh's
        own failures still raise, so the refresh backoff is unchanged."""
        self._direct_paused = time.monotonic() + GITHUB_DIRECT_PAUSE
        return _UseGh()

    def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def due(self) -> bool:
        """Whether a new read may start: self.wait seconds since the last one
        started and since it ended (a slow gh never runs back to back)."""
        now = time.monotonic()
        return not (self.fetched is not None and now - self.fetched < self.wait
                    or self.settled is not None and now - self.settled < self.wait)

    def refresh_in_background(self) -> None:
        """Start one daemon fetch when due, so the screen never waits on the network."""
        thread = getattr(self, "_thread", None)
        if thread is not None and thread.is_alive():
            return
        if not self.due():
            return
        self.fetched = time.monotonic()
        self._thread = threading.Thread(target=self.refresh, kwargs={"force": True},
                                        name="monitor-github", daemon=True)
        self._thread.start()

    def refresh(self, force: bool = False) -> None:
        if not force and not self.due():
            return
        self.fetched = time.monotonic()
        failed = True
        now = self.clock()
        since = now - self.window
        stamp = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        last_full = self._full_read_at
        full = (
            last_full is None
            or self._comment_updated is None
            or self.comments_cut
            or (now - last_full) >= GITHUB_FULL_READ
            or now < last_full
        )
        self._full_read_at = None
        try:
            if full:
                query = f"since={stamp}&sort=created&direction=desc"
                more, next_full = False, now
            else:
                delta_stamp = datetime.fromtimestamp(
                    self._comment_updated - GITHUB_COMMENT_OVERLAP, timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                query = f"since={delta_stamp}&sort=updated&direction=asc"
                more, next_full = self.comments_cut, last_full
            raw = []
            for page in range(1, GITHUB_PAGES + 1):
                batch = self._get(f"repos/{self.repo}/issues/comments?{query}"
                                  f"&per_page={GITHUB_PAGE}&page={page}")
                if type(batch) is not list:
                    raise ValueError("comments page")
                raw += batch
                if len(batch) < GITHUB_PAGE:
                    break
            else:
                if full:
                    # Every page was full: say so unless a probe shows nothing is
                    # left. An unanswered probe keeps the warning and the pages.
                    try:
                        probe = self._get(f"repos/{self.repo}/issues/comments?since={stamp}"
                                          f"&sort=created&direction=desc&per_page=1"
                                          f"&page={GITHUB_PAGE * GITHUB_PAGES + 1}")
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                        probe = None
                    more = not (type(probe) is list and not probe)
                    if more:
                        next_full = None
                else:
                    next_full = None
            by_id, unkeyed = {}, []
            prior = () if full else reversed(self._comments)
            for item in (*prior, *raw):
                if type(item) is not dict:
                    continue
                key = item.get("id")
                if type(key) is int:
                    by_id[key] = item
                else:
                    unkeyed.append(item)
            ordered = (*by_id.values(), *unkeyed) if full else (*reversed(by_id.values()), *unkeyed)
            comments = sorted(
                (item for item in ordered
                 if "created_at" not in item or _github_time(item.get("created_at")) >= since),
                key=lambda item: _github_time(item.get("created_at")),
                reverse=True,
            )
            newest_updated = max(
                (_github_time(item.get("updated_at") or item.get("created_at"))
                 for item in (*raw, *comments) if type(item) is dict),
                default=0.0,
            )
            if not full and self._comment_updated is not None:
                newest_updated = max(newest_updated, self._comment_updated)
            self._comments = comments
            self._comment_updated = newest_updated if newest_updated > 0 else None
            self._full_read_at = next_full
            pulls = self._get(f"repos/{self.repo}/pulls?state=all&sort=updated"
                              "&direction=desc&per_page=100")
            if type(pulls) is not list:
                raise ValueError("pulls page")
            self.refresh_rule_alerts(pulls)
            board_page_count = len(pulls)
            updated_times = []
            for pull in pulls:
                updated = pull.get("updated_at") if type(pull) is dict else None
                try:
                    if type(updated) is not str:
                        raise ValueError("invalid updated_at")
                    updated_times.append(datetime.strptime(updated, "%Y-%m-%dT%H:%M:%SZ")
                                         .replace(tzinfo=timezone.utc).timestamp())
                except (ValueError, OverflowError):
                    updated_times.append(math.nan)
            board_oldest_updated = (min(updated_times) if updated_times
                                    and all(math.isfinite(value) for value in updated_times) else None)
            # A comment posted between page reads shifts the pages: list it once.
            seen, unique = set(), []
            for item in comments:
                key = item.get("id") if type(item) is dict else None
                if key is None or (type(key) is int and key not in seen):
                    seen.add(key)
                    unique.append(item)
            events = github_events(unique, pulls, since)
            pending = owner_pending(unique, pulls, self.pending)
            prs = branch_prs(pulls, self.repo)
            work_pulls = board_pulls(pulls, self.repo)
            scope = (f"等你決定：看最近 {int(self.window // 3600)} 小時的 GitHub 留言"
                     + (f"（只讀了最新 {GITHUB_PAGE * GITHUB_PAGES} 則）" if more else "")
                     + "；監控開著時，更早還沒回覆的也會留著")
            self.events, self.pending, self.branch_prs, self.scope = events, pending, prs, scope
            self.comments_cut = more
            self.board_pulls = work_pulls
            self.board_page_count = board_page_count
            self.board_oldest_updated = board_oldest_updated
            self.verdicts = review_verdicts(unique)
            self.status = (f"GitHub {self.repo}：最近 {int(self.window // 3600)} 小時，"
                           f"{datetime.now():%H:%M:%S} 更新（每 {int(GITHUB_REFRESH)} 秒）"
                           + (f"；留言太多，只讀了最新 {GITHUB_PAGE * GITHUB_PAGES} 則" if more else "")
                           + (f"；{GITHUB_IDENTITY_STATUS}" if GITHUB_IDENTITY_STATUS else ""))
            self.successful_read = True
            self.read_failed = False
            failed = False
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            # Keep the previous events; say plainly that they may be stale.
            detail = str(exc)[:40] if isinstance(exc, RuntimeError) else type(exc).__name__
            self.status = f"GitHub：讀取失敗（{detail}），以下 GitHub 項目可能過時"
            if GITHUB_IDENTITY_STATUS:
                self.status += f"；{GITHUB_IDENTITY_STATUS}"
            self.read_failed = True
        if failed:
            self.pr_checks = None   # not read this refresh: unknown, never an old answer
        else:
            self.refresh_pr_checks()
        self.refresh_ci()
        if self.want_main:
            self.refresh_main()
        # A failing gh (offline, rate limited, hung) is retried later each
        # time, so the Mac is not forking it every 30 seconds for nothing.
        self.wait = min(self.wait * 2, GITHUB_BACKOFF) if failed else GITHUB_REFRESH
        self.settled = time.monotonic()

    def refresh_main(self) -> None:
        """Read a small, bounded snapshot of main and its two required CI runs."""
        now = time.monotonic()
        if self.main_read_at is not None and now - self.main_read_at < 300:
            return
        self.main_read_at = now
        try:
            commits = self._get(f"repos/{self.repo}/commits?sha=main&per_page=1")
            linux_payload = self._get(f"repos/{self.repo}/actions/runs?branch=main&event=push&per_page=30")
            mac_payload = self._get(f"repos/{self.repo}/actions/workflows/llmtool-mac-ci.yml/runs?branch=main&event=workflow_dispatch&per_page=1")
            if type(commits) is not list or not commits:
                raise ValueError("main commits")
            commit = commits[0]
            if (type(commit) is not dict or type(commit.get("sha")) is not str
                    or not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit["sha"])):
                raise ValueError("main commit")
            sha = commit["sha"]
            commit_obj = commit.get("commit")
            if type(commit_obj) is not dict or type(commit_obj.get("message")) is not str:
                raise ValueError("main commit message")
            author = commit_obj.get("committer")
            if type(author) is not dict or type(author.get("date")) is not str:
                raise ValueError("main commit time")
            committed_at = _github_time(author["date"])
            if not committed_at:
                raise ValueError("main commit time")
            linux_runs = linux_payload.get("workflow_runs") if type(linux_payload) is dict else None
            mac_runs = mac_payload.get("workflow_runs") if type(mac_payload) is dict else None
            if type(linux_runs) is not list or type(mac_runs) is not list:
                raise ValueError("main workflow runs")
            if any(type(run) is not dict for run in linux_runs + mac_runs):
                raise ValueError("main workflow run")
            def valid_run(run):
                if (type(run) is not dict or type(run.get("name")) is not str
                        or type(run.get("status")) is not str
                        or (run.get("conclusion") is not None and type(run.get("conclusion")) is not str)
                        or type(run.get("head_sha")) is not str
                        or not re.fullmatch(r"[0-9a-fA-F]{7,64}", run["head_sha"])
                        or type(run.get("created_at")) is not str):
                    raise ValueError("main workflow run")
                if not _github_time(run["created_at"]):
                    raise ValueError("main workflow time")
                return run
            linux_candidates = [valid_run(run) for run in linux_runs
                                if run.get("name") == "llmtool Linux CI"]
            linux_run = max(linux_candidates, key=lambda run: _github_time(run.get("created_at")),
                            default=None)
            mac_candidates = [valid_run(run) for run in mac_runs]
            mac_run = max(mac_candidates, key=lambda run: _github_time(run.get("created_at")),
                          default=None)
            linux = ((linux_run["status"], linux_run.get("conclusion"), linux_run["head_sha"])
                     if linux_run else None)
            mac_time = _github_time(mac_run.get("created_at")) if mac_run else 0.0
            mac = ((mac_run["status"], mac_run.get("conclusion"), mac_time, mac_run["head_sha"])
                   if mac_run else None)
            self.main_health = MainHealth(sha[:7], _label(commit_obj["message"], 50),
                                          committed_at, linux, mac)
            self.main_error = ""
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.main_error = (str(exc)[:40] if isinstance(exc, RuntimeError)
                               else type(exc).__name__)

    def _graphql(self, query: str, **variables):
        """One read-only `gh api graphql` call through the gh runner, with
        GITHUB_TIMEOUT and the GITHUB_LIMIT size cap. gh exits non-zero when
        the answer carries GraphQL errors but still prints it: such an answer
        with data is returned so the caller can judge which PRs it affects."""
        gh = shutil.which("gh")
        if not gh:
            raise RuntimeError("找不到 gh 指令")
        argv = [gh, "api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            argv += ["-f", f"{key}={value}"]
        done = self.runner(argv, capture_output=True, timeout=GITHUB_TIMEOUT, check=False)
        out = done.stdout or b""
        if len(out) > GITHUB_LIMIT:
            raise RuntimeError("回應過大")
        try:
            payload = json.loads(out) if out else None
        except RecursionError:
            raise ValueError("JSON nested too deeply") from None
        except ValueError:
            payload = None
        partial = (type(payload) is dict and "errors" in payload
                   and type(payload.get("data")) is dict)
        if done.returncode != 0 and not partial:
            code = re.search(rb"HTTP (\d{3})", getattr(done, "stderr", None) or b"")
            raise RuntimeError(f"gh api 失敗（HTTP {code.group(1).decode()}）" if code
                               else f"gh api 失敗（exit {done.returncode}）")
        if payload is None:
            raise ValueError("GraphQL answer is not JSON")
        return payload

    def refresh_pr_checks(self) -> None:
        """Every open PR's exact-head checks and changed paths from one
        GraphQL read (PR_CHECKS_QUERY: the 100 most recently updated open PRs),
        so no PR waits behind others. Results are keyed by PR number and full
        head SHA (pr_head_checks()) and replace the previous refresh's; nothing
        is cached. A failed read leaves every PR unknown (None). The read is
        skipped only when the pulls page is not full and lists no open PR with
        a full head SHA: that page then holds every PR, so none is open. A full
        page may leave older open PRs out; the GraphQL read finds them."""
        pulls = self.board_pulls or []
        page_full = type(self.board_page_count) is int and self.board_page_count >= 100
        if not page_full and not any(
                isinstance(pull, BoardPull) and pull.state == "OPEN"
                and type(pull.sha) is str and HEX_SHA.fullmatch(pull.sha) for pull in pulls):
            self.pr_checks = {}
            return
        try:
            owner, name = self.repo.split("/", 1)
            self.pr_checks = pr_head_checks(self._graphql(PR_CHECKS_QUERY, owner=owner, name=name))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            self.pr_checks = None

    def refresh_ci(self) -> None:
        """Runners and CI, read on their own: a failure only marks them 未確認."""
        errors = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
        view = self.ci_view or CiView(None, 0.0)
        try:
            runners = ci_runners(self._get(f"repos/{self.repo}/actions/runners?per_page=100"))
        except errors as exc:
            error = _gh_error(exc)
            # The AI account may not list runners (HTTP 403); self-hosted runners are retired (#1253).
            if "HTTP 403" in error:
                view = replace(view, runners=[], seen_at=self.clock(), error="")
            else:
                view = replace(view, error=error)
        else:
            view = replace(view, runners=runners, seen_at=self.clock(), error="")
        self.ci_view = view   # the runners show now; their jobs follow below
        if self.want_hosted_ci or self.want_ci or view.runners:
            stamps: dict = {}   # run id -> updated_at from the runs list
            read: dict = {}     # run id -> job list read during this refresh

            def jobs_of(run):
                # An unfinished run's job list is read again only once the
                # run itself changed (or the cached list is a minute old).
                cached, stamp = self._jobs.get(run), stamps.get(run)
                if (cached is not None and stamp is not None and cached[0] == stamp
                        and self.clock() - cached[1] < GITHUB_JOBS_TTL):
                    return cached[2]
                if run not in read:
                    read[run] = self._get(f"repos/{self.repo}/actions/runs/{run}/jobs")
                found = read[run]
                if stamp is not None:
                    self._jobs[run] = (stamp, self.clock(), found)
                return found
            try:
                payload = self._get(f"repos/{self.repo}/actions/runs?per_page=30")
                if type(payload) is not dict:
                    raise ValueError("actions runs")
                self.ci_heads = ci_head_runs(payload)
                stamps = {run.get("id"): str(run.get("updated_at") or "")
                          for run in (payload.get("workflow_runs") if type(payload) is dict else None) or []
                          if type(run) is dict and type(run.get("id")) is int and run.get("status") in CI_ACTIVE}
                self._jobs = {run: item for run, item in self._jobs.items() if run in stamps}
                queued, active = [], ci_runs(payload)
                self.ci_runs = (ci_runner_names(active[:CI_RUN_READS], jobs_of, queued)
                                + active[CI_RUN_READS:])
                done = ci_finished_runs(payload)
                self._finished = {run: self._finished[run] for run, _ in done if run in self._finished}
                for run, text in [item for item in done if item[0] not in self._finished][:CI_JOB_READS]:
                    self._finished[run] = ci_finished_jobs(jobs_of(run), text)
            except errors:
                self.ci_runs = None
                self.ci_heads = None
                queued = []
            last: dict = {}
            for jobs in self._finished.values():
                for runner, text, result, end in jobs:
                    if end and end > last.get(runner, ("", "", 0.0))[2]:
                        last[runner] = (text, result, end)
            view = replace(view, finished=last, queued=tuple(queued))
        else:
            self.ci_runs = None
            self.ci_heads = None
        self.ci_view = view


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
                                                  "UNKNOWN": "狀態未知",
                                                  "ANSWERED": "已回覆，這項決定不再等你"}),
        "developer_dispatch": ("PM", "Developer", {"ASSIGNED": "自述已指派 Developer 任務",
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
                or event.status not in ACTIVITY_STATUS[event.event_type]
                or not _marker_direction_ok(event.event_type, event.actor, event.target,
                                            event.status)):
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
                    github: "GitHubSource | None" = None,
                    show_noise: bool = False) -> list[str]:
    events = [event for event in events if type(event) is ActivityEvent]
    if github is not None:
        events += [event for event in github.events if type(event) is ActivityEvent]
    hidden = 0
    if not show_noise:
        visible = []
        for event in events:
            if (event.event_type == "github_comment" and type(event.summary) is str
                    and FEED_NOISE.match(event.summary)):
                hidden += 1
            else:
                visible.append(event)
        events = visible
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.reference,
                                                 event.event_type, event.stage, event.status))
    lines = [line for line in (_activity_line(event) for event in ordered[-max(0, limit):]) if line]
    if not lines:
        lines.append("目前沒有可顯示的動態。")
    if hidden:
        lines.append(f"已隱藏 {hidden} 筆 lease／更正留言")
    if github is None:
        lines.append("GitHub 動態已關閉（--no-github），跨工作階段的標記沒有顯示。")
    else:
        lines.append(
            github.status
            + "；「自述」= 已設定的 Owner／AI 帳號自行標記，未經獨立驗證；"
              "actor=Owner 僅接受 Owner 帳號。")
    return lines


def activity_lines(tasks: list[Task], limit: int = 60,
                   github: "GitHubSource | None" = None, background: bool = False,
                   tasks_root: str | None = None, show_noise: bool = False) -> list[str]:
    if github is not None:
        github.refresh_in_background() if background else github.refresh()
    lines = render_activity(activity_events(tasks), limit, github, show_noise=show_noise)
    now = github.clock() if github is not None else time.time()
    try:
        entry, problem = pm_last_dispatch(now, tasks_root)
    except Exception:
        entry, problem = None, "本機 Task 紀錄讀取失敗"
    line = pm_dispatch_line(entry, problem, now)
    return [line, *lines]


def _activity_screen_lines(lines: list[str], width: int) -> list[str]:
    """Wrap Activity text to the terminal's display width."""
    return [piece for line in lines for piece in _wrap(line, max(1, width))]


# ---------------------------------------------------------------- office view
# One row per worker / agent / subagent, like looking across an office floor:
# who is working, idle, paused or stopped, and on what. Local and read-only.
#
# What is read: the tail of each recent Codex and Claude session file is loaded
# into memory and parsed (it contains prompts, model text and tool calls), and
# the recent part of a Codex thread is scanned for `claude --print` calls and
# their results. What is kept and shown is limited to:
#   - Codex: cwd (as a place label), originator, thread ids, subagent nickname
#     and the "/root/<name>" agent path, task_started/complete event types,
#     the thread title Codex keeps in its thread list;
#   - Claude: cwd, the model-written `ai-title`, subagent `.meta.json`
#     description, and the `description` field of the newest tool call; of a
#     running CLI's registry entry its status, name and kind, and the `state`
#     word of its background job record;
#   - one-shot reviews: the PR number and head named in the PM's call, and the
#     verdict word on its own line in the call's result;
#   - OpenCode/Kilo session titles and timestamps, typed error codes;
#   - worker-dispatch.json switches, the D6 registry, llmtool task state codes;
#     the PR board keeps only pulls/runs already fetched, verdict words, active
#     review PR/head pairs and valid local Task receipt/outcome SHA/result words.
#   - stuck alerts: Task task_id, request, developer.executor_task_id/state,
#     remote_test_receipts and remote_test_outcomes.
# Titles and descriptions are model- or prompt-derived text: they are passed
# through the same redaction as Activity headlines and shortened. Prompts, other
# model text, reasoning, commands and tool output are never shown; private paths
# are never shown. A role is named only when a record binds it (a D6 registry
# root or session_ref, a PM call naming a PR and head, or a Claude session its
# launcher named claude-pm / claude-reviewer / claude-developer[-…]); otherwise
# no role is claimed and the row says only who it is.
import unicodedata

CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")
# The office reads only task_id, developer.provider_type/state/created_at/
# executor_task_id/execution.native_session_id, request, remote_test_receipts,
# remote_test_outcomes, and response JSON summary
# when binding a Codex session to local Task work.
CODEX_HISTORY = os.path.expanduser("~/.codex/thread_history_1.sqlite")
CODEX_STATE = os.path.expanduser("~/.codex/state_5.sqlite")
CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
# Further accounts each run from their own CLAUDE_CONFIG_DIR, e.g. ~/.claude-account2.
CLAUDE_OTHER_PROJECTS = os.path.expanduser("~/.claude-*/projects")
SCRIPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.abspath(__file__)


_digest_cache: tuple = ()   # (file key, digest) of the last hashed copy


def script_digest() -> str:
    """Short sha256 of this file as it is on disk now (hashed again only when
    its size or mtime changed)."""
    global _digest_cache
    key = _file_key(SCRIPT_PATH)
    if key is not None and _digest_cache and _digest_cache[0] == key:
        return _digest_cache[1]
    try:
        with open(SCRIPT_PATH, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()[:7]
    except OSError:
        return "?"
    if key is not None:
        _digest_cache = (key, digest)
    return digest


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def script_blob() -> str:
    try:
        with open(SCRIPT_PATH, "rb") as handle:
            return git_blob_id(handle.read())
    except OSError:
        return ""


_GIT_RUN = subprocess.run   # bound at import: GitHub source tests fake subprocess.run


@functools.lru_cache(maxsize=1)
def monitor_version() -> str:
    """Which code this process runs, for the title line: the last commit that
    changed this file when the file matches that checkout, else "本機修改" plus
    the file's own digest. Read-only git plumbing only (no index refresh)."""
    rel = os.path.relpath(SCRIPT_PATH, SCRIPT_ROOT)
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")

    def git(*args):
        found = _GIT_RUN(["git", "-C", SCRIPT_ROOT, *args], capture_output=True,
                         text=True, timeout=2, env=env)
        return found.stdout.strip() if found.returncode == 0 else ""

    try:
        blob, committed = git("hash-object", "--no-filters", "--", SCRIPT_PATH), git("rev-parse", f"HEAD:{rel}")
        same = bool(blob) and blob == committed
        commit = git("log", "-1", "--format=%h %cd", "--date=format:%m-%d %H:%M", "--", rel)
    except (OSError, subprocess.SubprocessError):
        same, commit = False, ""
    if same and commit:
        return commit
    return f"本機修改 {script_digest()}"


def main_script_blob() -> str:
    rel = os.path.relpath(SCRIPT_PATH, SCRIPT_ROOT)
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        found = _GIT_RUN(["git", "-C", SCRIPT_ROOT, "rev-parse", "--verify", "-q",
                          f"origin/main:{rel}"], capture_output=True, text=True,
                         timeout=2, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    output = found.stdout.strip() if found.returncode == 0 else ""
    return output if HEX_SHA.fullmatch(output) else ""


def behind_main(loaded: str, main: str) -> bool:
    return bool(loaded and main and loaded != main)


BEHIND_WARNING = "⚠ 這個監控和本機 origin/main 的版本不同，請更新後重開"


def behind_line(mon) -> str:
    return BEHIND_WARNING if getattr(mon, "behind", False) and not getattr(mon, "stale", False) else ""
OFFICE_WINDOW = 6 * 3600       # sessions untouched for longer are left out
OFFICE_DONE_WINDOW = 30 * 60   # finished subagent work stays visible this long
OFFICE_BUSY = 90.0             # seconds since the last write that still mean "working"
OFFICE_STALL = 15 * 60         # started but silent for longer: 無回應
OFFICE_EVERY = 5.0             # seconds between office rebuilds (process polling stays faster)
OFFICE_HEAD = 1 << 20
OFFICE_TAIL = 512 << 10
AGENT_PATH = re.compile(r"[A-Za-z0-9_-]{1,48}")
NICKNAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,23}")
WORKING, IDLE, DONE, STALLED, PAUSED, STOPPED, UNCHECKED, ATTENTION, CLOSED = (
    "工作中", "待命", "完成", "無回應", "暫停中", "停工", "未確認", "需注意", "已關閉")


@dataclass(frozen=True)
class OfficeRow:
    name: str
    state: str
    detail: str
    age: float | None   # seconds since last observed activity; None = not observed
    order: int
    key: str = ""       # identity other rows can name as their group
    group: str = ""     # key of the agent that started this one ("" = top level)
    kind: str = "agent"   # agent | subagent | worker | shared (a line about the worker rows
    #                       whose name starts with its group, shown under them, never folded)
    tui: bool = False     # a Codex thread opened from the terminal CLI
    cli: bool = False     # an interactive Claude session started from the terminal CLI
    # What is known about the member, kept apart from the display text:
    role: str = ""        # developer | reviewer | pm | ui, only when a record binds it; "" = not claimed
    main: bool = False    # a Codex main thread in the canonical checkout (D6 Integrator's place)
    task: str = ""        # bound work, e.g. "PR #284 @c40c80a" or "task <id>"; "" = unbound
    evidence: str = ""    # where the row comes from (codex 紀錄, claude 紀錄, 程序, 派工檔, ...)
    authority: str = ""   # who set a dispatch switch: Owner | PM
    outcome: str = ""     # a finished one-shot review: verdict word or why it has none
    alert: str = ""       # a problem worth the Owner's eye even when the state cannot say it
    sid: str = ""         # short session id shown after the name, to match a window's /status
    codex_exec_started: bool = False  # non-subagent `codex exec` whose latest marker is task_started
    exec_result: str = ""   # a D6 `codex exec` run's last turn: completed | failed | "" (other/unknown)
    work: str = ""          # bound-work label, separate from detail
    codex_session: str = ""
    codex_started: float = 0.0
    codex_exec: bool = False
    codex_title: str = ""
    work_bound: bool = False


@dataclass(frozen=True)
class CodexWork:
    task: str
    d6: str
    session: str
    state: str
    created: float
    issue: str
    summary: str


def _shown(row: "OfficeRow") -> str:
    return f"{row.name} {row.sid}" if row.sid else row.name


def _row_detail(row: OfficeRow) -> str:
    detail = row.detail
    if row.work_bound and row.codex_title and detail.startswith(row.codex_title):
        detail = detail[len(row.codex_title):].lstrip("；")
    return f"{row.work}；{detail}" if row.work and detail else row.work or detail


def _codex_sid(thread_id) -> str:
    """Codex thread ids are UUIDv7: the leading digits are a timestamp shared by
    threads started within the same minute, so the random tail tells them apart."""
    found = re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-([0-9a-f]{12})", str(thread_id or ""))
    return found.group(1)[-8:] if found else ""


def _who(agent: str, role: str, nick: str = "") -> str:
    """Display name. A role is named only when a record binds it (Owner
    2026-10-23: no guessed roles); otherwise just who it is."""
    return " ".join(part for part in (agent, role or "subagent", nick) if part)


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


D6_GONE_STATES = frozenset({"REMOVED"})
_registry_cache: dict = {}


def registered_tasks(canonical: str) -> dict[str, tuple[str, str]]:
    """{task id: (registered root, session_ref)} from the D6 registry, read-only.

    A record counts only when the registry and the record are the user's own,
    owner-only, not symlinks, name the same task, give an exact real root, and
    the worktree is still LOCKED and not removed. Anything else grants nothing."""
    registry = os.path.join(canonical, ".git", "llmtool-worktrees")
    try:
        st = os.lstat(registry)
    except OSError:
        return {}
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        return {}
    stamp = (st.st_ino, st.st_mtime_ns)   # an atomic record replace changes the dir mtime
    cached = _registry_cache.get(registry)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    found: dict[str, tuple[str, str]] = {}
    details: dict[str, tuple[str, str, float]] = {}
    try:
        names = os.listdir(registry)
    except OSError:
        names = []
    # A record is parsed again only when its file changed; the others keep
    # what the last read found for them (nothing when they were refused).
    records = _registry_records if cached is not None else {}
    kept: dict = {}
    for name in names:
        task_id = name[:-len(".json")]
        if not name.endswith(".json") or not AGENT_PATH.fullmatch(task_id):
            continue
        path = os.path.join(registry, name)
        try:
            file_st = os.lstat(path)
        except OSError:
            continue
        key = (file_st.st_ino, file_st.st_size, file_st.st_mtime_ns, file_st.st_ctime_ns,
               file_st.st_mode, file_st.st_uid)
        entry = records.get(path)
        if entry is None or entry[0] != key:
            entry = (key, _registry_record(path, task_id, file_st))
        kept[path] = entry
        if entry[1] is not None:
            found[task_id], details[task_id] = entry[1]
    _registry_records.clear()
    _registry_records.update(kept)
    _registry_cache.clear()
    _registry_cache[registry] = (stamp, found, details)
    return found


_registry_records: dict = {}   # record path -> (file key, admitted (found, detail) or None)


def _registry_record(path: str, task_id: str, file_st) -> tuple | None:
    """What one D6 record grants: ((root, session_ref), detail), or None."""
    try:
        if (not stat.S_ISREG(file_st.st_mode) or file_st.st_uid != os.getuid()
                or file_st.st_mode & 0o077 or file_st.st_size > 64 << 10):
            return None
        with open(path, "rb") as handle:
            record = json.loads(handle.read(64 << 10))
    except (OSError, ValueError, RecursionError):
        return None
    root = record.get("root_realpath") if type(record) is dict else None
    if (type(root) is not str or not os.path.isabs(root) or os.path.realpath(root) != root
            or record.get("task_id") != task_id or record.get("lock_state") != "LOCKED"
            or record.get("state") in D6_GONE_STATES):
        return None
    ref = record.get("session_ref")
    return (root, ref if type(ref) is str else ""), _d6_detail(record, file_st.st_mtime)


BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,200}")


def _d6_detail(record: dict, written: float) -> tuple[str, str, float, float, str]:
    """(state, bound agent id, recorded update, file write, branch) for the task
    list; malformed fields fall back, so a bad record never drops the task's placement."""
    state, agent, branch = record.get("state"), record.get("trusted_agent_id"), record.get("branch")
    try:
        verified = _github_time(record.get("last_verified_at"))
    except (OverflowError, OSError, ValueError):
        verified = 0.0
    return (state if type(state) is str and CODE_TEXT.fullmatch(state) else "?",
            agent if type(agent) is str and AGENT_ID.fullmatch(agent) else "",
            verified or written, written,
            branch if type(branch) is str and BRANCH.fullmatch(branch) else "")


def registry_details(canonical: str) -> dict:
    """{task id: (state, agent, recorded update, file write)} for the tasks
    registered_tasks admits."""
    registered_tasks(canonical)
    cached = _registry_cache.get(os.path.join(canonical, ".git", "llmtool-worktrees"))
    return cached[2] if cached else {}


# A role word anywhere in a bound agent id: codex-reviewer,
# claude-reviewer-agent, codex-developer-gpt-6-luna (the first word wins).
D6_ROLE = re.compile(r"(?:^|[-_])(developer|reviewer|pm)(?=$|[-_])")
# A Codex review worktree is named <task>-crv<N>-<head> (review-change).
CODEX_REVIEW_TASK = re.compile(r"-crv\d+-")


def _d6_role(canonical: str, task_id: str, default: str = "") -> str:
    """The role the D6 registry binds to a registered task: read from the
    record's trusted_agent_id. When that id cannot be read (missing or
    malformed), a -crv<N>- task name still marks a Codex review worktree;
    otherwise the caller's default (Owner #564: every Codex row in a
    registered D6 shows developer or reviewer from the registry; a Claude or
    OpenCode row claims no role its record does not name).

    Reads the registry snapshot the caller's placement check (_place ->
    registered_tasks) loaded for this refresh, so it costs no registry I/O
    of its own; the registry is read only when nothing has loaded it yet."""
    cached = _registry_cache.get(os.path.join(canonical, ".git", "llmtool-worktrees"))
    details = cached[2] if cached is not None else registry_details(canonical)
    agent = details.get(task_id, ("", "", 0, 0, ""))[1]
    found = D6_ROLE.search(agent)
    if found:
        return found.group(1)
    if not agent and CODEX_REVIEW_TASK.search(task_id):
        return "reviewer"
    return default


CODEX_HIGH_DEBUG_TASK = re.compile(r"-hd[0-9]+-[0-9a-fA-F]{7}$")
CODEX_HIGH_DEBUG_AGENT = "codex-high-debug-gpt-6.1-sol"


def _codex_d6_role(canonical: str, task_id: str, default: str = "") -> str:
    """Return the role Codex should display for a registered D6 task."""
    cached = _registry_cache.get(os.path.join(canonical, ".git", "llmtool-worktrees"))
    details = cached[2] if cached is not None else registry_details(canonical)
    agent = details.get(task_id, ("", "", 0, 0, ""))[1]
    if agent == CODEX_HIGH_DEBUG_AGENT and CODEX_HIGH_DEBUG_TASK.search(task_id):
        return "debug reviewer"
    return _d6_role(canonical, task_id, default)


D6_STATES = {   # D6 record state -> plain words
    "ACTIVE": "進行中", "REPAIR": "修復中", "REVIEW_PENDING": "等審查",
    "RETAIN_BLOCKED": "保留待處置", "RETIRE_READY": "已驗收，可收尾",
}
D6_WINDOW = 24 * 3600
D6_OPEN_STATES = frozenset({"ACTIVE", "REPAIR", "REVIEW_PENDING"})
D6_STALE_HEAD = "⚠ 另有 "
PR_DONE_WORDS = {"MERGED": "已合併", "CLOSED": "已關閉"}
AGENT_ID = re.compile(r"[A-Za-z0-9._-]{1,96}")


def d6_idle_word(phase: str) -> str:
    if phase in ("已完成，待收尾", "審查已結束，待收尾", "已驗收，可收尾"):
        return "已結束"
    if phase in ("等審查或 CI", "修復中", "等審查"):
        return "等結果"
    if phase in ("審查沒在跑，查不到結論", "閒置（查不到 PR）", "保留待處置"):
        return "異常：沒有 session 在跑"
    return "未確認"


def d6_phase(state: str, sessions: bool, pr: str, reviewer_done: bool) -> str:
    if sessions:
        return "執行中"
    if reviewer_done:
        return "審查已結束，待收尾"
    if pr in PR_DONE_WORDS:
        return "已完成，待收尾"
    if pr == "OPEN":
        return "等審查或 CI"
    if state in ("RETIRE_READY", "RETAIN_BLOCKED", "REPAIR", "REVIEW_PENDING"):
        return D6_STATES.get(state, state)
    return "閒置（查不到 PR）"


def d6_task_lines(canonical: str, office: list, now: float,
                  prs: dict[str, tuple[int, str]] | None = None,
                  verdicts: dict | None = None) -> list[str]:
    """Locked D6 tasks whose record changed in the last 24 hours, newest first,
    each with the live session working in its root, if any. Only the task id,
    state, bound agent id, branch and time are read; free-text dispositions are
    not. prs (branch -> PR state, from GitHub) flags records still in progress
    whose PR has already merged or closed, including older ones, in one line."""
    details = registry_details(canonical)
    prs = prs or {}

    def stale(state, branch):
        found = prs.get(branch) if state in D6_OPEN_STATES and branch else None
        return found if found and found[1] in PR_DONE_WORDS else None
    live = {}
    for row in office:
        if row.task.startswith("task ") and row.state != CLOSED:
            live.setdefault(row.task[5:], []).append(f"{row.name} {row.state}")
    running = _d6_running_tasks(office)
    # A recorded time in the future would pin a task on top: use the write time.
    stamped = ((task_id, state, agent, verified if verified <= now + 300 else written, branch)
               for task_id, (state, agent, verified, written, branch) in details.items())
    recent = sorted((item for item in stamped if now - item[3] <= D6_WINDOW),
                    key=lambda item: -item[3])
    width = max((len(item[0]) for item in recent), default=0)
    lines = []
    pr_numbers = {number: state for number, state in prs.values()}
    for task_id, state, agent, when, branch in recent:
        done = stale(state, branch)
        pr_state = prs.get(branch, (None, ""))[1] if branch and prs else ""
        reviewer_done = False
        match = _review_task(task_id)
        if match and task_id not in running:
            reviewer_done = any(match[1].lower() in entry[2]
                                for entry in (verdicts or {}).get(match[0], []))
        if task_id not in running and not prs:
            phase = D6_STATES.get(state, state)
        elif match and task_id not in running:
            reviewer_pr = pr_numbers.get(int(match[0]), "")
            if reviewer_done:
                phase = "審查已結束，待收尾"
            elif reviewer_pr in PR_DONE_WORDS:
                phase = "已完成，待收尾"
            else:
                phase = "審查沒在跑，查不到結論"
        elif task_id not in running and CODEX_HIGH_DEBUG_TASK.search(task_id):
            phase = "診斷沒在跑，待收尾"
        else:
            phase = d6_phase(state, task_id in running, pr_state, reviewer_done)
        who = "、".join(live.get(task_id, [])) or d6_idle_word(phase)
        note = f"  ⚠ PR #{done[0]} {PR_DONE_WORDS[done[1]]}，登記還是{D6_STATES.get(state, state)}" if done else ""
        lines.append(f"{time.strftime('%m-%d %H:%M', time.localtime(when))}  {task_id:<{width}}  "
                     f"{phase}  →  {who}  （{agent or '未綁定 agent'}）{note}")
    older = sorted(task_id for task_id, (state, _, _, written, branch) in details.items()
                   if stale(state, branch) and task_id not in {item[0] for item in recent})
    if older:
        lines.append(D6_STALE_HEAD + f"{len(older)} 筆較舊的 D6 登記還是進行中，但 PR 已合併或關閉："
                     + "、".join(older[:6]) + ("…" if len(older) > 6 else ""))
    return lines


def _d6_running_tasks(office: list) -> set[str]:
    """Return D6 tasks with live work, based on row family and state."""
    running = {row.task[5:] for row in office
               if row.task.startswith("task ")
               and (row.state in (WORKING, STALLED)
                    or row.state == ATTENTION and not row.key.startswith("codex:"))}
    # Claude subagent rows carry no task; their parent session supplies it.
    running_keys = {row.group for row in office
                    if row.kind == "subagent" and row.group
                    and row.state in (WORKING, STALLED)}
    running.update(row.task[5:] for row in office
                   if row.task.startswith("task ") and row.state != CLOSED
                   and row.key in running_keys)
    return running


def _review_task(task_id: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"pr(\d+)-(?:claude-rv|codex-rv)[A-Za-z0-9._-]*?-([0-9a-fA-F]{7})",
        task_id)
    if match is None:
        match = re.search(r"pr(\d+).*?-crv[A-Za-z0-9._-]*?-([0-9a-fA-F]{7})", task_id)
    return (match.group(1), match.group(2)) if match else None


def d6_lines(lines: list[str]) -> list[str]:
    tasks = [line for line in lines if not line.startswith(D6_STALE_HEAD)]
    summary = [line for line in lines if line.startswith(D6_STALE_HEAD)]   # never cut off
    return ["D6 任務（近 24 小時有更新，最新在上）",
            *(tasks[:15] or ["近 24 小時沒有更新的 D6 任務。"]), *summary]


def _place(cwd, canonical: str) -> tuple[str, str] | None:
    """Where a session works: ('pm', '') for the canonical checkout, ('task', id)
    for a registered D6 root, ('unregistered', name) for a folder that only looks
    like a task root, else None (not this repository)."""
    if type(cwd) is not str or not cwd:
        return None
    tasks = registered_tasks(canonical)
    # The same few session folders are placed on every office rebuild; the
    # answer is kept while the D6 registry read is the same, up to a minute.
    hit = _place_cache.get((cwd, canonical))
    if hit is not None and hit[0] is tasks and 0 <= time.monotonic() - hit[1] < PLACE_TTL:
        return hit[2]
    place = _find_place(cwd, canonical, tasks)
    _remember(_place_cache, (cwd, canonical), (tasks, time.monotonic(), place))
    return place


_place_cache: dict = {}   # (cwd, canonical) -> (registered_tasks() answer, when, place)
PLACE_TTL = 60.0


def _find_place(cwd: str, canonical: str, tasks: dict) -> tuple[str, str] | None:
    real = os.path.realpath(cwd)
    if real == canonical or real.startswith(canonical + os.sep):
        return "pm", ""
    for task_id, (root, _) in tasks.items():
        if real == root or real.startswith(root + os.sep):
            return "task", task_id
    parts = real.split(os.sep)
    name = os.path.basename(canonical)
    for i in range(len(parts) - 2):
        if parts[i] == "llmtool-dev-workspaces" and parts[i + 1] == name:
            rest = parts[i + 2:]
            if len(rest) >= 2 and rest[0] == "tasks" and AGENT_PATH.fullmatch(rest[1]):
                return "unregistered", rest[1]
            if rest and AGENT_PATH.fullmatch(rest[0]):
                return "unregistered", rest[0]
    return None


def _place_label(place: tuple[str, str]) -> str:
    return {"pm": "本 repo", "task": f"task {place[1]}",
            "unregistered": f"未登記資料夾 {place[1]}"}[place[0]]


META_FIELDS = ("id", "timestamp", "cwd", "originator", "thread_source", "parent_thread_id",
               "agent_nickname", "agent_path")


# Session records are read every office rebuild. A record that has not
# changed (same inode, size and mtime) is not read or parsed again; one that
# grew (the writers only append) is parsed from where the last read stopped.
_head_cache: dict = {}   # path -> (file key, parsed first line)
_entry_cache: dict = {}  # path -> (file key, first entrypoint)
_tail_cache: dict = {}   # path -> tail state (see _jsonl_tail)
TAIL_PROBE = 64          # bytes before the resume point that must still match
CACHE_FILES = 256        # records remembered per cache, least recently used first out


def _file_key(path: str):
    """(dev, ino, size, mtime_ns, ctime_ns) of a record, or None when it cannot
    be read. ctime is the kernel's own write stamp: a rewrite that keeps the
    size and a set mtime still changes it."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _remember(cache: dict, path: str, value) -> None:
    cache.pop(path, None)
    cache[path] = value
    while len(cache) > CACHE_FILES:
        cache.pop(next(iter(cache)))


def _jsonl_head(path: str):
    key = _file_key(path)
    cached = _head_cache.get(path)
    if key is not None and cached is not None and cached[0] == key:
        _remember(_head_cache, path, cached)
        return cached[1]
    found = _read_jsonl_head(path)
    if key is not None:
        _remember(_head_cache, path, (key, found))
    return found


def _read_jsonl_head(path: str):
    try:
        with open(path, "rb") as handle:
            raw = handle.readline(OFFICE_HEAD)
    except OSError:
        return None
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        pass
    # An oversized first line (long base instructions) is cut off. Codex writes
    # the small identity fields before the instructions, so take the first
    # occurrence of each and nothing else.
    payload = {}
    for field in META_FIELDS:
        found = re.search(rb'"' + field.encode() + rb'"\s*:\s*"((?:[^"\\]|\\.){0,4096})"', raw)
        if found:
            try:
                payload[field] = json.loads(b'"' + found.group(1) + b'"')
            except (ValueError, RecursionError):
                pass
    return {"type": "session_meta", "payload": payload} if "cwd" in payload else None


def _first_entrypoint(path: str, lines: int = 64):
    """How a Claude session was first started ("cli" or "sdk-cli")."""
    key = _file_key(path)
    cached = _entry_cache.get(path)
    if key is not None and cached is not None and cached[0] == key:
        _remember(_entry_cache, path, cached)
        return cached[1]
    found = _read_first_entrypoint(path, lines)
    if key is not None:
        _remember(_entry_cache, path, (key, found))
    return found


def _read_first_entrypoint(path: str, lines: int = 64):
    try:
        with open(path, "rb") as handle:
            for _ in range(lines):
                raw = handle.readline(OFFICE_HEAD)
                if not raw:
                    break
                try:
                    item = json.loads(raw)
                except (ValueError, RecursionError):
                    continue
                if type(item) is dict and type(item.get("entrypoint")) is str:
                    return item["entrypoint"]
    except OSError:
        pass
    return None


def _parse_jsonl(data: bytes, base: int) -> tuple[list, int]:
    """[(offset, item)] for the JSON object lines of data, which starts at
    file offset base, and the offset where its last (maybe unfinished) line
    starts: a read that resumes there sees that line whole."""
    out, offset, raw = [], base, b""
    for raw in data.split(b"\n"):
        if raw.strip():
            try:
                item = json.loads(raw)
            except (ValueError, RecursionError):
                item = None
            if type(item) is dict:
                out.append((offset, item))
        offset += len(raw) + 1
    return out, base + len(data) - len(raw)


def _jsonl_tail(path: str) -> list:
    """The JSON object lines in the last OFFICE_TAIL bytes of a record (a cut
    first line left out), as _jsonl_tail always returned them. Between calls
    the parsed lines are kept per file: an unchanged file costs one stat, a
    grown one is read from its last line on (the bytes just before it must
    still match, so a rewritten file is read whole again)."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    size, key = st.st_size, (st.st_dev, st.st_ino)
    cutoff = size - OFFICE_TAIL if size > OFFICE_TAIL else -1   # keep lines starting after it
    cached = _tail_cache.get(path)
    if cached is not None and cached["key"] == key and cached["size"] == size \
            and cached["mtime"] == (st.st_mtime_ns, st.st_ctime_ns):
        _remember(_tail_cache, path, cached)
        return [item for _, item in cached["items"]]
    items = None
    if cached is not None and cached["key"] == key and cached["size"] < size:
        line, probe = cached["line"], cached["probe"]
        try:
            with open(path, "rb") as handle:
                handle.seek(line - len(probe))
                chunk = handle.read(size - line + len(probe))
        except OSError:
            return []
        if chunk[:len(probe)] == probe and len(chunk) == size - line + len(probe):
            grown, last = _parse_jsonl(chunk[len(probe):], line)
            items = [pair for pair in cached["items"] if pair[0] < line] + grown
            probe = chunk[max(0, last - TAIL_PROBE - (line - len(probe))):last - (line - len(probe))]
            line = last
    if items is None:
        start = max(0, size - OFFICE_TAIL)
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                data = handle.read(size - start)
        except OSError:
            return []
        items, line = _parse_jsonl(data, start)
        probe = data[max(0, line - TAIL_PROBE - start):line - start]
    items = [pair for pair in items if pair[0] > cutoff]
    _remember(_tail_cache, path, {"key": key, "size": size, "mtime": (st.st_mtime_ns, st.st_ctime_ns),
                                  "items": items, "line": line, "probe": probe})
    return [item for _, item in items]


def _claude_activity_time(tail: list) -> float:
    """Newest timestamp on a real Claude conversation entry, ignoring metadata."""
    newest = 0.0
    for item in tail:
        if item.get("type") not in ("user", "assistant", "system"):
            continue
        stamp = item.get("timestamp")
        if type(stamp) is not str:
            continue
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                continue
            newest = max(newest, parsed.timestamp())
        except (ValueError, OverflowError):
            continue
    return newest


def _claude_last_turn_finished(tail: list) -> bool:
    """Whether the newest timestamped conversation entry records a finished turn.

    Claude appends a `turn_duration` system entry when an assistant turn ends.
    Later untimed metadata records must not make that completed turn look busy.
    """
    for item in reversed(tail):
        if item.get("type") not in ("user", "assistant", "system"):
            continue
        stamp = item.get("timestamp")
        if type(stamp) is not str:
            continue
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                continue
        except (ValueError, OverflowError):
            continue
        return item.get("type") == "system" and item.get("subtype") == "turn_duration"
    return False


TURN_MARKERS = ("task_started", "task_complete", "turn_aborted")
OFFICE_TURN_SCAN = 64 << 20   # how far before the tail a Codex turn marker is looked for
_turn_cache: dict = {}        # {path: (scanned up to, marker)} for the part before the tail


def _newest_turn_marker(handle, low: int, high: int):
    """The newest turn marker in whole lines inside [low, high) of a record."""
    carry = b""
    while high > low:
        start = max(low, high - (4 << 20))
        handle.seek(start)
        lines = (handle.read(high - start) + carry).split(b"\n")
        carry, high = (lines.pop(0) if start > low else b""), start
        for raw in reversed(lines):
            if b'"event_msg"' not in raw or not any(m.encode() in raw for m in TURN_MARKERS):
                continue
            try:
                item = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            inner = item.get("payload") if type(item) is dict else None
            if (item.get("type") == "event_msg" and type(inner) is dict
                    and inner.get("type") in TURN_MARKERS):
                return inner["type"]
    return None


def _codex_turn_marker(path: str):
    """The newest turn marker before the tail of a long Codex record: one turn
    can outgrow the tail. Only the part that left the tail since the last look
    is read again."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            end = max(0, size - OFFICE_TAIL)
            # Start on a line boundary: the tail's cut first line belongs here.
            handle.seek(end)
            end += len(handle.readline(OFFICE_TAIL)) if end else 0
            seen, marker = _turn_cache.get(path, (None, None))
            if seen is None or seen > end:
                seen, marker = max(0, end - OFFICE_TURN_SCAN), None
            # A marker that left the tail since the last look is newer.
            marker = _newest_turn_marker(handle, seen, end) or marker
    except OSError:
        return None
    _turn_cache[path] = (end, marker)
    return marker


def _label(text, limit: int = 40) -> str:
    line = _headline(text) if type(text) is str else ""
    return line[:limit] + ("…" if len(line) > limit else "")


def _last_action(tail: list) -> str:
    """The `description` of the newest Claude tool call: the short summary Claude
    Code writes for people (e.g. "Run monitor tests"). Only that one field is
    read; the command, file contents and every other tool input are ignored."""
    for item in reversed(tail):
        message = item.get("message") if item.get("type") == "assistant" else None
        content = message.get("content") if type(message) is dict else None
        if type(content) is not list:
            continue
        for block in reversed(content):
            if type(block) is dict and block.get("type") == "tool_use":
                tool_input = block.get("input")
                text = tool_input.get("description") if type(tool_input) is dict else None
                if type(text) is str and text.strip():
                    return _label(text)
    return ""


def _awaiting_model(tail: list) -> bool:
    """True when the newest exchanged message is a tool result the model has
    not answered yet: the request is out and the session waits on the model.
    Only the block types are read, never their content."""
    for item in reversed(tail):
        if item.get("type") not in ("user", "assistant"):
            continue
        if item["type"] == "assistant":
            return False
        content = (item.get("message") or {}).get("content") if type(item.get("message")) is dict else None
        return type(content) is list and any(
            type(block) is dict and block.get("type") == "tool_result" for block in content)
    return False


def _humanize(path_stem: str) -> str:
    return " ".join(path_stem.replace("-", "_").split("_"))


# What a Codex thread is doing, from structure only: the name of its newest
# agent tool call, or the kind of its newest command as Codex itself parsed it
# (read / search / list_files) with just a file's basename. The command text,
# search query, output and every model message are ignored.
CODEX_TOOLS = {"wait_agent": "等 subagent 回報", "spawn_agent": "派出 subagent",
               "send_message": "傳話給 subagent", "followup_task": "交代 subagent 下一步",
               "list_agents": "查看 subagent", "sleep": "等一下再看",
               "request_user_input_async": "等你回覆"}
CODEX_ITEMS = {"FileChange": "改檔案", "McpToolCall": "呼叫 MCP 工具",
               "AgentMessage": "回報進度", "CollabAgentToolCall": "和 subagent 協作"}
FILE_NAME = re.compile(r"[\w.+-]{1,60}")
EXEC_WORKDIR = re.compile(r'\bworkdir\s*:\s*"([^"\\\n]{1,1024})"')


def _codex_command(item: dict, canonical: str) -> str:
    parsed = [entry for entry in item.get("parsed_cmd") or [] if type(entry) is dict]
    kinds = [entry.get("type") for entry in parsed]
    if parsed and all(kind == "read" for kind in kinds):
        names = [os.path.basename(str(entry.get("path") or entry.get("name") or ""))
                 for entry in parsed]
        names = [name for name in names if FILE_NAME.fullmatch(name)]
        if names:
            return f"讀 {names[0]}" + (f" 等 {len(names)} 個檔" if len(names) > 1 else "")
        return "讀檔案"
    if parsed and all(kind in ("search", "list_files") for kind in kinds):
        return "搜尋程式碼" if "search" in kinds else "列出檔案"
    place = _place(item.get("cwd") if type(item.get("cwd")) is str else None, canonical)
    return "執行指令" + (f"（{_place_label(place)}）" if place and place[0] == "task" else "")


def _codex_action(tail: list, canonical: str) -> str:
    for item in reversed(tail):
        inner = item.get("payload")
        if type(inner) is not dict:
            continue
        kind = inner.get("type")
        if kind == "function_call" and inner.get("name") in CODEX_TOOLS:
            return CODEX_TOOLS[inner["name"]]
        if kind == "custom_tool_call" and inner.get("name") == "exec":
            # Newer than any finished command: still running.
            code = str(inner.get("input") or "")[:4096]
            found = EXEC_WORKDIR.search(code)
            place = _place(found.group(1), canonical) if found else None
            where = f"（{_place_label(place)}）" if place and place[0] == "task" else ""
            return ("等指令跑完" if "write_stdin" in code else "執行指令") + where
        if kind == "item_completed" and type(inner.get("item")) is dict:
            done = inner["item"]
            if done.get("type") == "CommandExecution":
                return _codex_command(done, canonical)
            if done.get("type") == "Reasoning":
                return "思考中"
            if done.get("type") in CODEX_ITEMS:
                return CODEX_ITEMS[done["type"]]
        if kind in ("task_started", "task_complete", "turn_aborted"):
            return ""
    return ""


# Codex's own record of each turn: status and start/end time only. Item and
# message columns are never selected.
CODEX_TURN_SQL = ("SELECT status, started_at, completed_at FROM thread_turns "
                  "WHERE thread_id = ? ORDER BY rollout_ordinal DESC LIMIT 1")
TURN_STATES = frozenset({"inProgress", "completed", "failed", "interrupted"})


TURN_TIME_MAX = 1 << 32   # seconds; anything later is not a real turn time
# Codex's thread list: only the title, which Codex takes from the first
# request, like the title Claude writes for a session. No other column is read.
CODEX_TITLE_SQL = "SELECT title FROM threads WHERE id = ?"


def codex_title(history: dict, thread_id) -> str:
    """A thread's title, or "". history["titles"]: connection or None; after
    one error the rest of this refresh does without it."""
    db = history.get("titles")
    if db is None or type(thread_id) is not str:
        return ""
    try:
        found = db.execute(CODEX_TITLE_SQL, (thread_id,)).fetchone()
    except sqlite3.Error:
        history["titles"] = None
        db.close()
        return ""
    return found[0] if found and type(found[0]) is str else ""


def open_codex_history(path: str):
    """Read-only connection to Codex's turn history, or None.

    Only the user's own regular files are opened (no symlink, FIFO or device,
    which could redirect the read or block open() past SQLite's timeout), and
    no side file is ever created. Codex keeps the database in WAL mode:
    - while its -wal and -shm files both exist (Codex has it open), a plain
      read-only open uses them;
    - when neither exists, every change is already in the main file, so it is
      opened immutable: no locks, no -wal or -shm;
    - with only one of them, it is skipped.
    Residual: Codex creating or removing them between this check and the
    read is not prevented; an immutable read that meets a concurrent write
    fails with an SQLite error, which drops the database for that refresh."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    sides = []
    for suffix in ("-wal", "-shm"):
        try:
            sides.append(os.lstat(path + suffix))
        except FileNotFoundError:
            pass
        except OSError:
            return None
    if len(sides) == 1 or any(not stat.S_ISREG(f.st_mode) or f.st_uid != os.getuid()
                              for f in [info, *sides]):
        return None
    mode = "mode=ro" if sides else "mode=ro&immutable=1"
    try:
        return sqlite3.connect(f"file:{urllib.parse.quote(path)}?{mode}", uri=True, timeout=0.2)
    except sqlite3.Error:
        return None


def _turn_time(value) -> int | None:
    return value if type(value) is int and 0 < value < TURN_TIME_MAX else None


def codex_turn(history: dict, thread_id) -> tuple | None:
    """(status, started, completed) of a thread's newest turn, or None.
    history: {"db": connection or None}; after one error (a lock wait that
    timed out, say) the rest of this refresh does without it."""
    db = history.get("db")
    if db is None or type(thread_id) is not str:
        return None
    try:
        found = db.execute(CODEX_TURN_SQL, (thread_id,)).fetchone()
    except sqlite3.Error:
        history["db"] = None
        db.close()
        return None
    if not found or found[0] not in TURN_STATES:
        return None
    status, started, completed = found
    return status, _turn_time(started), _turn_time(completed)


def _clock(seconds: float) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(seconds))
    except (OverflowError, OSError, ValueError):
        return "--:--"


def _codex_failure_expiry_detail(file_mtime, now):
    """Describe the stale failed-subagent row's existing file-age cutoff.

    Return None when the mtime cannot establish a safe deadline. The displayed
    wall-clock second is rounded upward, and the countdown uses that same
    second so the absolute label cannot precede the claimed remaining time.
    """
    try:
        file_mtime, now = float(file_mtime), float(now)
        if (not math.isfinite(file_mtime) or not math.isfinite(now) or
                file_mtime < 0 or now < 0 or file_mtime > now):
            return None
        raw_expiry = file_mtime + OFFICE_WINDOW
        if not math.isfinite(raw_expiry):
            return None
        expiry_second = math.ceil(raw_expiry)
        # With a non-future mtime, ceiling to the next displayed second can
        # add less than one second beyond OFFICE_WINDOW.
        remaining = min(float(OFFICE_WINDOW + 1),
                        max(0.0, expiry_second - now))
        expiry_label = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(expiry_second))
    except (OverflowError, OSError, TypeError, ValueError):
        return None

    if remaining <= 0:
        duration = "已到顯示期限"
    else:
        minutes = math.ceil(remaining / 60)
        hours, minutes = divmod(minutes, 60)
        if hours and minutes:
            duration = f"約 {hours} 小時 {minutes} 分後隱藏"
        elif hours:
            duration = f"約 {hours} 小時後隱藏"
        else:
            duration = f"約 {minutes} 分後隱藏"
    return (f"（失敗提醒最多顯示至 {expiry_label}；{duration}；"
            "不代表程序仍在執行）")


def _codex_row(path: str, now: float, canonical: str, history: dict | None = None):
    meta = _jsonl_head(path)
    payload = meta.get("payload") if type(meta) is dict else None
    if type(payload) is not dict:
        return None
    place = _place(payload.get("cwd"), canonical)
    if place is None:
        return None
    try:
        file_mtime = os.path.getmtime(path)
        age = max(0.0, now - file_mtime)
    except OSError:
        return None
    last, marked = None, 0.0
    tail = _jsonl_tail(path)
    for item in tail:
        inner = item.get("payload")
        kind = inner.get("type") if type(inner) is dict else None
        if item.get("type") == "event_msg" and kind in TURN_MARKERS:
            last, marked = kind, _github_time(str(item.get("timestamp") or "")[:19] + "Z") or 0.0
    if last is None:
        last = _codex_turn_marker(path)
    # An untimed marker is no newer than the file's last write.
    marked = marked or (now - age if last is not None else 0.0)
    subagent = payload.get("thread_source") == "subagent"
    # A `codex exec` run exits after its one turn: once that turn ends it is
    # finished work, not a member waiting for the next instruction.
    finishes = subagent or payload.get("originator") == "codex_exec"
    if last in ("task_complete", "turn_aborted"):
        state = DONE if finishes else IDLE
    elif last == "task_started":
        state = WORKING if age <= OFFICE_STALL else STALLED
    else:
        state = WORKING if age <= OFFICE_BUSY else IDLE
    turn = codex_turn(history or {}, payload.get("id"))
    since, failed, interrupted, completed = "", False, False, False
    if turn is not None:
        status, began, ended = turn
        # Codex's own turn record beats the session-file markers, except when
        # they disagree about whether a turn is running and the marker is
        # later than the recorded turn's start: then the file has seen a turn
        # the record has not caught up with (a start marker of that same turn
        # carries its start second, so it never wins).
        agree = (last == "task_started") == (status == "inProgress")
        if last is not None and not agree and marked > (began or ended or 0):
            turn = None
    if turn is not None:
        if status == "inProgress":
            state = WORKING if age <= OFFICE_STALL else STALLED
            since = f"（這輪 {_clock(began)} 起）" if began else ""
        else:
            failed, interrupted = status == "failed", status == "interrupted"
            completed = status == "completed"
            # A failed turn needs a look even in a subagent; an interrupted
            # one was stopped on purpose.
            state = ATTENTION if failed else DONE if finishes else IDLE
            # Idle since the turn ended, unless the file was written later.
            if ended and ended <= now and now - age <= ended + 5:
                age = now - ended
            word = {"completed": "結束", "failed": "失敗", "interrupted": "被中斷"}[status]
            since = f"（上一輪 {_clock(ended)} {word}）" if ended else f"（上一輪{word}）"
    # A `codex exec` run whose turn has not ended: only its process says
    # whether it is still running (see _codex_rows).
    exec_started = payload.get("originator") == "codex_exec" and (
        last == "task_started" if turn is None else status == "inProgress")
    if subagent:
        # Codex names a spawned agent "/root/<short_task_name>"; anything else
        # could carry a private path, so it is shown as unnamed.
        found = re.fullmatch(r"/root/([A-Za-z0-9_-]{1,48})", str(payload.get("agent_path") or ""))
        stem = found.group(1) if found else ""
        nick = payload.get("agent_nickname")
        nick = nick if type(nick) is str and NICKNAME.fullmatch(nick) else ""
        if state == DONE and age > OFFICE_DONE_WINDOW:
            return None
        # A D6 record whose session_ref names this agent binds it to that task;
        # otherwise no role is claimed: the work name says what it does.
        task = next((task_id for task_id, (_, ref) in registered_tasks(canonical).items()
                     if stem and re.search(rf"(?<![\w-])/root/{re.escape(stem)}(?![\w-])", ref)),
                    "")
        role = _codex_d6_role(canonical, task, "developer") if task else ""
        detail = (_humanize(stem) if stem else "（未命名工作）")
        if task:
            detail += f"（task {task}）"
        if failed:
            detail += "（上一輪失敗）"
        elif interrupted:
            detail += "（上一輪被中斷）"
        if failed:
            # The Codex office scan itself drops session files once their mtime
            # is older than OFFICE_WINDOW. Surface that stale-record cutoff so
            # the failure reminder is not mistaken for a live process.
            expiry_detail = _codex_failure_expiry_detail(file_mtime, now)
            if expiry_detail:
                detail += expiry_detail
        return ("sub", payload.get("parent_thread_id"),
                OfficeRow(_who("codex", role, nick), state, detail,
                          age, 20,
                          key=f"codex:{payload.get('id')}",
                          group=f"codex:{payload.get('parent_thread_id')}", kind="subagent",
                          role=role, task=f"task {task}" if task else "",
                          evidence="codex 紀錄", sid=_codex_sid(payload.get("id"))))
    if state == DONE and age > OFFICE_DONE_WINDOW:
        return None
    key = f"codex:{payload.get('id')}"
    started = _github_time(str(payload.get("timestamp") or "")[:19] + "Z")
    if not since and started:
        since = f"（對話 {time.strftime('%m-%d %H:%M', time.localtime(started))} 開始）"
    tui = payload.get("originator") == "codex-tui"
    # As for a Claude session: what it is doing now, else its title.
    title = _label(codex_title(history or {}, payload.get("id")))
    action = _codex_action(tail, canonical) if state == WORKING else ""
    sid = _codex_sid(payload.get("id"))
    if place[0] == "task":
        # A registered D6 root belongs to the one agent its record binds: the
        # Codex Reviewer in a -crv- review worktree, else the Codex Developer.
        role = _codex_d6_role(canonical, place[1], "developer")
        result = ""
        if payload.get("originator") == "codex_exec":
            result = ("failed" if failed else "completed" if completed or
                      (turn is None and last == "task_complete") else "")
        return ("main", payload.get("id"),
                OfficeRow(f"codex {role}", state, action or title or _place_label(place), age, 10,
                          key=key, tui=tui, role=role, task=f"task {place[1]}",
                          evidence="codex 紀錄", sid=sid,
                          codex_exec_started=exec_started,
                          exec_result=result,
                          codex_session=payload.get("id") if type(payload.get("id")) is str else "",
                          codex_started=started if type(started) in (int, float) else 0.0,
                          codex_exec=payload.get("originator") == "codex_exec",
                          codex_title=title))
    if payload.get("originator") == "codex_exec":
        # A `codex exec` run, like a `claude -p` run: one call that exits when
        # its task is done, wherever it runs. Nothing says who started it.
        return ("sub", None, OfficeRow("codex 單次呼叫", state, (action or title or _place_label(place))
                                       + since, age, 30, key=key, kind="subagent",
                                       evidence="codex 紀錄", sid=sid,
                                       codex_exec_started=exec_started,
                                       codex_session=payload.get("id") if type(payload.get("id")) is str else "",
                                       codex_started=started if type(started) in (int, float) else 0.0,
                                       codex_exec=True, codex_title=title))
    if place[0] == "unregistered":
        return ("main", payload.get("id"),
                OfficeRow("codex 對話", state, action or title or _place_label(place),
                          age, 10, key=key, tui=tui, evidence="codex 紀錄", sid=sid,
                          codex_session=payload.get("id") if type(payload.get("id")) is str else "",
                          codex_started=started if type(started) in (int, float) else 0.0,
                          codex_exec=payload.get("originator") == "codex_exec",
                          codex_title=title))
    # The canonical checkout is the Integrator's under D6, but nothing records
    # which thread holds that role, so none is claimed.
    return ("main", payload.get("id"),
            OfficeRow("codex 主對話", state, (f"：{title}" if title else "") + since, age, 0,
                      key=key, tui=tui, main=True, evidence="codex 紀錄", sid=sid,
                      codex_session=payload.get("id") if type(payload.get("id")) is str else "",
                      codex_started=started if type(started) in (int, float) else 0.0,
                      codex_exec=payload.get("originator") == "codex_exec",
                      codex_title=title),
            action if state == WORKING else "上一輪失敗，要看 Codex 畫面" if failed else "")


# One-shot Claude reviews a Codex thread runs with `claude --print`. The call
# says which PR and head it reviews. Its verdict is read from one place for
# every Reviewer route: the exact-head verdict posted on that PR (Owner rule
# 2026-10-24). The call's own output is never parsed, whatever shape the PM's
# script gives it, so a new script shape needs no new parser.
# `claude` must start a command (line start, after ; && || "| " $( or an
# opening quote of an argv/cmd string, or a JS template assigned with =`...`),
# optionally behind `timeout N`, and --print must be in that same command, so a
# search pattern such as `pgrep -f 'x|claude --print'` or prose quoting
# `claude --print` is not one.
CLAUDE_CALL = re.compile(
    r"(?:^|[\n;(\"']|&&|\|\||\|\s|\$\(|=\s*`)\s*(?:timeout\s+\d+[smhd]?\s+)?"
    r"(?:[\w./~-]*/)?claude\b"
    r"[^|;&\n]{0,80}?(?:--print\b|\s-p(?:\s|$)|'--print'|\"--print\")", re.M)
# `claude -p --help` / `--version` only prints usage: it is not a call.
CLAUDE_INFO = re.compile(r"\s(?:--help|-h|--version|-v)(?=[\s\"'|;&)]|$)")


def _claude_call(text: str):
    """The first `claude --print` call in text, skipping usage lookups."""
    for found in CLAUDE_CALL.finditer(text):
        command = re.split(r"[|;&\n]|\\n", text[found.end():found.end() + 400], maxsplit=1)[0]
        call = text[found.start():found.end()]
        flags = re.split(r"[\"'`]", call[call.find("claude"):] + command, maxsplit=1)[0]
        if not CLAUDE_INFO.search(flags):   # a -v or -h inside the prompt is not a flag
            return found
    return None


# A verdict counts only as its own line ("**Verdict: FAIL**", "## Review Verdict:
# PASS"), not inside a sentence quoting an earlier one; the word is upper case.
REVIEW_VERDICT = re.compile(r"(?im)^[\s>*#_`-]*(?:(?:formal\s+)?review\s+)?verdict\b\W{0,8}"
                            r"(?-i:([A-Z][A-Z_]{2,30}))\b")
REVIEW_PR = re.compile(r"PR #(\d{1,6})\b")
REVIEW_HEAD = re.compile(r"(?i)\bhead\b\W{0,8}([0-9a-f]{7,40})\b")
REVIEW_SCAN = 6 << 20
NO_VERDICT = "已結束，GitHub 上沒有這個版本的結論"
UNREAD_VERDICT = "已結束（沒讀 GitHub，不知道結論）"
_review_cache: dict = {}


# A review call usually fetches the diff itself, e.g. `gh -R owner/repo pr
# diff 309`, `gh pr view https://github.com/o/r/pull/309` or `gh api
# repos/o/r/pulls/309`. A bare URL is only a citation, not a fetch.
REVIEW_PR_FETCH = re.compile(
    r"\bgh\b[^\n]*?\bpr\s+(?:diff|view|checkout)\s+(?:(?:-R|--repo)[=\s]+\S+\s+)?"
    r"(?:\S*/pull/)?#?(\d{1,6})\b"
    r"|\bgh\s+api\s+\S*/pulls/(\d{1,6})\b")
EMBEDDED_DIFF = re.compile(r"^diff --git ", re.M)


def _review_target(text: str) -> tuple[str, str]:
    """The PR the call fetches wins over one its prompt merely mentions.
    Fetch commands inside an inlined diff do not count, and fetches that
    disagree fall back to the prompt's own "PR #N"."""
    cut = EMBEDDED_DIFF.search(text)
    pr, head = REVIEW_PR.search(text), REVIEW_HEAD.search(text)
    fetched = {next(group for group in found if group)
               for found in REVIEW_PR_FETCH.findall(text[:cut.start()] if cut else text)}
    number = fetched.pop() if len(fetched) == 1 else ""
    return (number or (pr.group(1) if pr else ""), head.group(1)[:7] if head else "")


def codex_reviews(path: str, now: float | None = None) -> list[tuple]:
    """[(time, pr, head7)] for `claude --print` calls in one Codex thread,
    oldest first. PR and head are the ones the call named."""
    now = time.time() if now is None else now
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    cache = _review_cache.get(path)
    if cache is None or size < cache["offset"]:
        # A forked thread (a subagent) starts with a copy of its parent's history,
        # parent turns included, all stamped with the fork time. Turn ids grow with
        # time, so its own work starts at the first turn not older than the thread.
        cache = {"offset": max(0, size - REVIEW_SCAN), "calls": [], "carry": b"",
                 "copied": _forked(path)}
        _review_cache[path] = cache
    try:
        with open(path, "rb") as handle:
            handle.seek(cache["offset"])
            data = cache["carry"] + handle.read(size - cache["offset"])
    except OSError:
        return []
    first = cache["offset"] == max(0, size - REVIEW_SCAN) and not cache["calls"]
    cache["offset"] = size
    lines = data.split(b"\n")
    cache["carry"] = lines.pop()   # an unfinished last line is read again next time
    if first and data and not data.startswith(b"{"):
        lines = lines[1:]
    for raw in lines:
        if cache["copied"]:
            if b'"task_started"' in raw:
                cache["copied"] = _turn_id(raw) < cache["copied"] and cache["copied"]
            continue
        if b"custom_tool_call" not in raw:
            continue
        try:
            item = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        payload = item.get("payload") if type(item) is dict else None
        if type(payload) is not dict or payload.get("type") != "custom_tool_call":
            continue
        text = str(payload.get("input") or "")
        if _claude_call(text):
            when = _github_time(str(item.get("timestamp") or "")[:19] + "Z")
            cache["calls"].append((when,) + _review_target(text))
    # Undated calls and calls older than the office window are dropped.
    cache["calls"] = [call for call in cache["calls"]
                      if call[0] and now - call[0] <= OFFICE_WINDOW]
    return list(cache["calls"])


def _forked(path: str) -> str:
    """The thread's own id when it was forked from another thread, else ""."""
    try:
        with open(path, "rb") as handle:
            meta = json.loads(handle.readline(1 << 20))
    except (OSError, ValueError, RecursionError):
        return ""
    payload = meta.get("payload") if type(meta) is dict else None
    if type(payload) is not dict or not payload.get("forked_from_id"):
        return ""
    return str(payload.get("id") or "") or "~"   # no own id: treat everything as copied


def _turn_id(raw: bytes) -> str:
    try:
        payload = json.loads(raw).get("payload")
    except (ValueError, RecursionError, AttributeError):
        return ""
    return str(payload.get("turn_id") or "") if type(payload) is dict else ""


def forget_reviews(paths) -> None:
    """Drop cached review state for threads no longer scanned."""
    for path in [path for path in _review_cache if path not in paths]:
        _review_cache.pop(path, None)


COMMIT_ID = re.compile(r"\b[0-9a-fA-F]{7,40}\b")


CI_ACTIVE = ("in_progress", "queued", "waiting", "pending", "requested")


def ci_head_runs(payload) -> dict:
    """Newest recognized Linux/Mac workflow run for each seven-character head."""
    found = {}
    runs = payload.get("workflow_runs") if type(payload) is dict else None
    for run in runs if type(runs) is list else []:
        if type(run) is not dict:
            continue
        name, sha, run_id = run.get("name"), run.get("head_sha"), run.get("id")
        status, conclusion = run.get("status"), run.get("conclusion")
        if (name not in ("llmtool Linux CI", "llmtool Mac CI")
                or type(sha) is not str or not HEX_SHA.fullmatch(sha)
                or type(run_id) is not int or type(status) is not str
                or (conclusion is not None and type(conclusion) is not str)):
            continue
        sha7 = sha[:7].lower()
        workflow = "linux" if name == "llmtool Linux CI" else "mac"
        per_head = found.setdefault(sha7, {})
        previous = per_head.get(workflow)
        if previous is None or run_id > previous[0]:
            per_head[workflow] = (run_id, (status, conclusion or ""))
    return {sha: {workflow: result for workflow, (_, result) in workflows.items()}
            for sha, workflows in found.items()}


REQUIRED_CHECKS = {"check (Linux 3.14)": "linux", "check (macOS 3.13)": "mac",
                   "check (docs)": "docs"}

# One read per refresh for every open PR: its head, changed paths (which
# checks its workflows' path filters require) and the head commit's check runs.
# isDraft and isCrossRepository keep the board's draft step and fork filter
# for open PRs the REST pulls page does not list.
PR_CHECKS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number
        headRefOid
        isDraft
        isCrossRepository
        files(first: 100) { totalCount nodes { path } }
        commits(last: 1) {
          nodes {
            commit {
              oid
              statusCheckRollup {
                contexts(first: 100) {
                  totalCount
                  nodes { __typename ... on CheckRun { name status conclusion databaseId } }
                }
              }
            }
          }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class HeadChecks:
    """One open PR at its head: which checks its changed paths require (None
    when the file list was cut or unreadable), the newest run of each check
    name on that exact commit, as (status, conclusion) in lower case (None
    when the check list was cut or unreadable), and whether GitHub calls the
    PR a draft."""
    required: frozenset | None
    runs: dict | None
    draft: bool = False


def _ci_path_class(path: str) -> str:
    """classify_path() of tools/llmtool/scripts/ci_path_classes.py (copied,
    not imported: the monitor runs on its own)."""
    if path.startswith("tools/ai-loop/"):
        return "ai-loop"
    if path.startswith("tools/api-agent-gateway/"):
        return "gateway"
    if path.startswith(".github/workflows/"):
        return "workflow"
    if path.startswith("tools/llmtool/"):
        relative = path[len("tools/llmtool/"):]
        if "/" not in relative and relative.endswith(".md"):
            return "docs"
        return "runtime"
    if (path.endswith(".md") or path in {"AGENTS.md", "CLAUDE.md"}
            or path.startswith("skills/") or path.startswith("docs/")):
        return "docs"
    return "other"


def _top_llmtool_md(path: str) -> bool:
    relative = path[len("tools/llmtool/"):] if path.startswith("tools/llmtool/") else None
    return relative is not None and "/" not in relative and relative.endswith(".md")


def _mac_ci_path(path: str) -> bool:
    """A path in llmtool-mac-ci.yml's pull_request paths filter."""
    if path.startswith("tools/llmtool/"):
        return not _top_llmtool_md(path)
    return (path in {"scripts/llmtool_monitor.py", "scripts/test_llmtool_monitor.py",
                     ".github/workflows/llmtool-mac-ci.yml"}
            or path.startswith(("tools/mac-runner/", "tools/safe-git/")))


def _linux_ci_path(path: str) -> bool:
    """A path in llmtool-linux-ci.yml's pull_request paths filter (top-level
    tools/llmtool Markdown is excluded, then included again; *.md is top level)."""
    return (path.startswith(("tools/llmtool/", "tools/ec2-runner/", "tools/safe-git/",
                             "tools/api-agent-gateway/", "tools/ai-loop/", "skills/", "docs/"))
            or path in {".github/workflows/llmtool-linux-ci.yml", "AGENTS.md", "CLAUDE.md"}
            or ("/" not in path and path.endswith(".md")))


def required_checks(paths) -> frozenset:
    """The checks a PR's changed paths require: macOS when the Mac workflow's
    paths filter matches; when the Linux workflow's filter matches, Linux 3.14
    if any path is code and docs if any is docs (ci_path_classes.linux_flags()).
    Pure."""
    required = set()
    if any(_mac_ci_path(path) for path in paths):
        required.add("mac")
    if any(_linux_ci_path(path) for path in paths):
        for path in paths:
            path_class = _ci_path_class(path)
            if path_class in ("runtime", "gateway", "workflow", "other"):
                required.add("linux")
            if path_class == "docs" or path.endswith(".md"):
                required.add("docs")
    return frozenset(required)


def _rollup_runs(node: dict, sha: str):
    """{key: (status, conclusion)} of the required check names on the head
    commit, the highest databaseId per name; None when the check list was cut
    or unreadable. A commit whose oid is not the head is ignored."""
    commits = node.get("commits")
    entries = commits.get("nodes") if type(commits) is dict else None
    if type(entries) is not list:
        return None
    best: dict = {}
    for entry in entries:
        commit = entry.get("commit") if type(entry) is dict else None
        oid = commit.get("oid") if type(commit) is dict else None
        if type(oid) is not str or oid.lower() != sha:
            continue
        rollup = commit.get("statusCheckRollup")
        if rollup is None:
            continue   # no checks on the head at all
        contexts = rollup.get("contexts") if type(rollup) is dict else None
        runs = contexts.get("nodes") if type(contexts) is dict else None
        total = contexts.get("totalCount") if type(contexts) is dict else None
        if type(runs) is not list or type(total) is not int or total > len(runs):
            return None
        for run in runs:
            if type(run) is not dict:
                continue
            name, run_id = run.get("name"), run.get("databaseId")
            status, conclusion = run.get("status"), run.get("conclusion")
            key = REQUIRED_CHECKS.get(name) if type(name) is str else None
            if (key is None or type(run_id) is not int or type(status) is not str
                    or (conclusion is not None and type(conclusion) is not str)):
                continue
            conclusion = (conclusion or "").lower()
            if conclusion == "startup_failure":
                conclusion = "failure"
            if key not in best or run_id > best[key][0]:
                best[key] = (run_id, (status.lower(), conclusion))
    return {key: entry for key, (_, entry) in best.items()}


def pr_head_checks(payload) -> dict:
    """{(PR number, full head SHA): HeadChecks} from one PR_CHECKS_QUERY
    answer, newest updated first; PRs from forks are left out, as on the
    pulls page (board_pulls()). Each PR has its own entry, so two open PRs on
    one head never share required checks or read errors. A PR whose node the
    answer's errors name is unknown (HeadChecks(None, None)); an error naming
    no PR, or a PR node the answer cannot identify by number and full head,
    or an answer without the pull request list, raises ValueError. A cut file
    list leaves required unknown, a cut check list leaves runs unknown. Pure;
    no I/O."""
    if type(payload) is not dict:
        raise ValueError("GraphQL answer")
    errors = payload.get("errors")
    failed: set = set()
    if errors is not None:
        if type(errors) is not list:
            raise ValueError("GraphQL errors")
        for error in errors:
            path = error.get("path") if type(error) is dict else None
            if (type(path) is list and len(path) >= 4
                    and path[:3] == ["repository", "pullRequests", "nodes"]
                    and type(path[3]) is int):
                failed.add(path[3])
            else:
                raise ValueError("GraphQL error")
    data = payload.get("data")
    repository = data.get("repository") if type(data) is dict else None
    pulls = repository.get("pullRequests") if type(repository) is dict else None
    nodes = pulls.get("nodes") if type(pulls) is dict else None
    if type(nodes) is not list:
        raise ValueError("GraphQL pull requests")
    if any(index >= len(nodes) for index in failed):
        raise ValueError("GraphQL error")
    found: dict = {}
    for index, node in enumerate(nodes):
        number = node.get("number") if type(node) is dict else None
        sha = node.get("headRefOid") if type(node) is dict else None
        sha = sha.lower() if type(sha) is str else ""
        if type(number) is not int or number < 1 or not HEX_SHA.fullmatch(sha):
            if index in failed:
                raise ValueError("GraphQL error")   # a failed PR the answer cannot name
            continue
        draft = node.get("isDraft") is True
        if index in failed:
            # Only a known fork is left out; the error may be on that field.
            if node.get("isCrossRepository") is True:
                continue
            result = HeadChecks(None, None, draft)
        elif node.get("isCrossRepository") is not False:
            continue
        else:
            files = node.get("files")
            listed = files.get("nodes") if type(files) is dict else None
            total = files.get("totalCount") if type(files) is dict else None
            paths = ([item.get("path") if type(item) is dict else None for item in listed]
                     if type(listed) is list else None)
            required = (required_checks(paths)
                        if paths is not None and all(type(path) is str for path in paths)
                        and type(total) is int and total <= len(paths) else None)
            result = HeadChecks(required, _rollup_runs(node, sha), draft)
        found[(number, sha)] = result
    return found


def ci_runs(payload) -> list:
    """Workflow runs not finished yet, oldest first: (status, text, started,
    runner, run id); runner is None until ci_runner_names() fills it in.
    The text says which workflow and what it tests (PR and head, or branch)."""
    found = []
    for run in (payload.get("workflow_runs") if type(payload) is dict else None) or []:
        if type(run) is not dict or run.get("status") not in CI_ACTIVE:
            continue
        started = _github_time(run.get("run_started_at") or run.get("created_at")) or None
        found.append((run["status"], _ci_text(run), started, None, run.get("id")))
    return sorted(found, key=lambda run: run[2] or 0)


def _ci_text(run: dict) -> str:
    sha = str(run.get("head_sha") or "")[:7]
    prs = [pr.get("number") for pr in run.get("pull_requests") or [] if type(pr) is dict]
    where = (f"PR #{prs[0]}" if prs and type(prs[0]) is int
             else str(run.get("head_branch") or "?")[:40])
    return f"{str(run.get('name') or '?')[:40]}（{where} @{sha}）"


def ci_finished_runs(payload) -> list:
    """Finished workflow runs, newest first, as (run id, text)."""
    return [(run["id"], _ci_text(run))
            for run in (payload.get("workflow_runs") if type(payload) is dict else None) or []
            if type(run) is dict and run.get("status") == "completed" and type(run.get("id")) is int]


def _clean_runner_name(name) -> str | None:
    if name is None:
        return None
    name_str = str(name)
    if not name_str:
        return None
    cleaned = "".join(c for c in name_str if not unicodedata.category(c).startswith("C"))
    cleaned = cleaned[:64]
    return cleaned if cleaned else None


clean_runner_name = _clean_runner_name


def ci_runner_names(runs: list, jobs_of, queued: list | None = None) -> list:
    """Fill in which runner runs each unfinished workflow: one entry per
    in-progress job, so a run with jobs on two runners shows on both. Job
    states decide, not the run's: GitHub calls a run "queued" while any of
    its jobs waits, even with another job already running. The run itself
    stays listed while it has jobs waiting; those go to `queued`
    (ci_queued_jobs())."""
    named = []
    for status, text, started, runner, run_id in runs:
        jobs, waiting = [], status != "in_progress"
        if type(run_id) is int:
            try:
                payload = jobs_of(run_id)
                found = [job for job in payload.get("jobs") or [] if type(job) is dict]
            except (OSError, ValueError, RuntimeError, AttributeError, subprocess.SubprocessError):
                found = None
            if found is not None:
                jobs = [job for job in found if job.get("status") == "in_progress"]
                waiting = any(job.get("status") in CI_ACTIVE and job.get("status") != "in_progress"
                              for job in found) or (waiting and not found)
                if queued is not None:
                    queued += ci_queued_jobs(payload, text)
        for job in jobs:
            job_name = str(job.get("name") or "")[:40]
            runner_name = _clean_runner_name(job.get("runner_name"))
            named.append(("in_progress", text + (f"：{job_name}" if job_name else ""),
                          _github_time(job.get("started_at")) or started,
                          runner_name, run_id))
        if not jobs:   # nothing runs yet, whatever the run says
            named.append(("queued" if waiting else status, text, started, runner, run_id))
        elif waiting:
            named.append(("queued", text, started, None, run_id))
    return named


RUNNER_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
CI_CONCLUSIONS = {"success": "成功", "failure": "失敗", "cancelled": "取消",
                  "timed_out": "逾時", "skipped": "略過"}


def main_health_line(health, error, now) -> tuple[str, str]:
    if health is None:
        text = "main：尚未讀取"
        if error:
            text += f"（未確認：{error}）"
        return text, "!" if error else ""
    def result(status, conclusion):
        if status != "completed":
            return "執行中"
        return CI_CONCLUSIONS.get(conclusion, "未確認")
    linux = "未確認"
    failed = bool(error)
    if health.linux:
        status, conclusion, sha = health.linux
        linux = result(status, conclusion)
        if sha[:7].lower() != health.sha[:7].lower():
            linux += f" @{sha[:7]}"
        failed |= status == "completed" and conclusion not in ("success", "skipped")
    mac = "未確認"
    if health.mac:
        status, conclusion, created, _ = health.mac
        mac = f"全套 {result(status, conclusion)}（{_short_ago(max(0.0, now - created))}）"
        failed |= status == "completed" and conclusion not in ("success", "skipped")
    text = (f"main {health.sha} {health.title}（{_short_ago(max(0.0, now - health.committed_at))}）"
            f"· Linux {linux} · Mac {mac}")
    if error:
        text += f"（未確認：{error}）"
    return text, "!" if failed else ""


def ci_runners(payload) -> list:
    """Registered self-hosted runners as (name, os, status, busy, labels);
    names or labels that do not look like GitHub's are dropped."""
    found = []
    for runner in (payload.get("runners") if type(payload) is dict else None) or []:
        if type(runner) is not dict or not RUNNER_NAME.fullmatch(str(runner.get("name") or "")):
            continue
        labels = tuple(str(label.get("name")) for label in runner.get("labels") or []
                       if type(label) is dict and RUNNER_NAME.fullmatch(str(label.get("name") or "")))
        found.append((runner["name"], str(runner.get("os") or "")[:16], str(runner.get("status") or ""),
                      runner.get("busy") is True, labels))
    return sorted(found)


def ci_finished_jobs(payload, text: str) -> list:
    """Finished jobs of one workflow run as (runner, job text, result, end time)."""
    found = []
    for job in (payload.get("jobs") if type(payload) is dict else None) or []:
        if type(job) is dict and job.get("status") == "completed":
            runner = _clean_runner_name(job.get("runner_name"))
            if runner:
                name = str(job.get("name") or "")[:40]
                found.append((runner, text + (f"：{name}" if name else ""),
                              CI_CONCLUSIONS.get(job.get("conclusion"), "結果不明"),
                              _github_time(job.get("completed_at"))))
    return found


def ci_queued_jobs(payload, text: str) -> list:
    """Jobs still waiting for a runner, as (labels they ask for, text)."""
    found = []
    for job in (payload.get("jobs") if type(payload) is dict else None) or []:
        if type(job) is dict and job.get("status") in CI_ACTIVE and job.get("status") != "in_progress":
            name = str(job.get("name") or "")[:40]
            labels = frozenset(str(label).lower() for label in job.get("labels") or [])
            found.append((labels, text + (f"：{name}" if name else "")))
    return found


def review_verdicts(comments) -> dict:
    """{pr: [(time, verdict, ids, head)]} from verdicts trusted accounts posted
    on PRs: a verdict line, the 7-character prefixes of every commit-like id the
    comment names, the head its HEAD line names as written (7 to 40 hex
    characters, lower case; REVIEW_HEAD; "" if none), and the comment's
    last-edit time. The PR board binds a verdict only to that head. Pure; no I/O."""
    found: dict = {}
    for comment in comments if type(comments) is list else []:
        if not _trusted_github_comment(comment):
            continue
        body, number = comment.get("body"), _issue_number(comment.get("issue_url"))
        verdict = REVIEW_VERDICT.search(body) if type(body) is str and number else None
        if verdict:
            when = max(_github_time(comment.get("created_at")), _github_time(comment.get("updated_at")))
            ids = frozenset(token[:7].lower() for token in COMMIT_ID.findall(body))
            named = REVIEW_HEAD.search(body)
            head = named.group(1).lower() if named else ""
            found.setdefault(number, []).append((when, verdict.group(1), ids, head))
    return found


PM_BOARD = os.path.expanduser("~/.local/state/llmtool/pm-board.json")
PM_BOARD_STALE = 1800
PM_PROGRESS_STALE = 1800
PM_BOARD_LIMIT = 64 * 1024
PM_BOARD_REF = re.compile(r"(?:PR )?#[0-9]{1,7}")


@dataclass(frozen=True)
class PmBoardItem:
    ref: str
    what: str
    next: str
    reason: str = ""
    current: str = ""


@dataclass(frozen=True)
class PmBoard:
    status: str
    age: float | None
    running: list
    queue: list
    blocked: list
    skipped: int = 0


@dataclass(frozen=True)
class BoardPull:
    number: int
    title: str
    state: str
    merged_at: float
    updated_at: float
    head: str
    draft: bool
    closes: tuple[int, ...] = ()
    sha: str = ""   # the full 40-character head, when GitHub gave one


@dataclass(frozen=True)
class WorkBoard:
    owner: list
    running: list
    queue: list
    blocked: list
    done: list
    github_known: bool
    board_known: bool
    github_status: str
    board_status: str
    board_age: float | None
    skipped: int
    note: str = ""
    window_hours: int = 12
    prs: list = field(default_factory=list)
    owner_cut: bool = False


# File format: a JSON object with schema_version 1, UTC updated_at, string
# updated_by, and required running/queue/blocked lists of {ref, what, next, reason?}.
def read_pm_board(now, path=None) -> PmBoard:
    """Read schema_version 1 from ~/.local/state/llmtool/pm-board.json.

    The JSON object has updated_at (UTC), updated_by and lists running, queue
    and blocked. Items have ref (#965 or PR #976), what, next and optional
    reason; extra keys such as note are ignored.
    """
    path = PM_BOARD if path is None else path
    fd = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        nonblock = getattr(os, "O_NONBLOCK", None)
        if nofollow is None or nonblock is None:
            return PmBoard("unreadable", None, [], [], [])
        flags = os.O_RDONLY | nofollow | nonblock
        fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return PmBoard("unreadable", None, [], [], [])
        raw = os.read(fd, PM_BOARD_LIMIT + 1)
        if len(raw) > PM_BOARD_LIMIT:
            return PmBoard("malformed", None, [], [], [])
    except FileNotFoundError:
        return PmBoard("missing", None, [], [], [])
    except (OSError, ValueError, TypeError):
        return PmBoard("unreadable", None, [], [], [])
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError, UnicodeDecodeError):
        return PmBoard("malformed", None, [], [], [])
    if (type(value) is not dict or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or type(value.get("updated_by")) is not str):
        return PmBoard("malformed", None, [], [], [])
    for name in ("running", "queue", "blocked"):
        if type(value.get(name)) is not list:
            return PmBoard("malformed", None, [], [], [])
    stamp = value.get("updated_at")
    try:
        if type(stamp) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", stamp):
            raise ValueError("invalid timestamp")
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = float(now) - parsed.timestamp()
        if age < 0:
            return PmBoard("undated", None, [], [], [])
    except (TypeError, ValueError, OverflowError):
        return PmBoard("undated", None, [], [], [])
    lists = {}
    skipped = 0
    for name in ("running", "queue", "blocked"):
        entries = value[name]
        valid = []
        for item in entries[:50]:
            if type(item) is not dict or type(item.get("ref")) is not str or not PM_BOARD_REF.fullmatch(item["ref"]):
                skipped += 1
                continue
            if type(item.get("what")) is not str or type(item.get("next")) is not str:
                skipped += 1
                continue
            reason = item.get("reason", "")
            if type(reason) is not str:
                skipped += 1
                continue
            valid.append(PmBoardItem(item["ref"], _label(item["what"], 60),
                                     _label(item["next"], 16) or "未確認",
                                     _label(reason, 60)))
        skipped += max(0, len(entries) - 50)
        lists[name] = valid
    return PmBoard("stale" if age > PM_BOARD_STALE else "ok", age,
                   lists["running"], lists["queue"], lists["blocked"], skipped)


def board_pulls(pulls, repo: str) -> list[BoardPull]:
    """Convert one already-read pulls page to safe, display-only PR records."""
    out = []
    entries = pulls if type(pulls) is list else []
    for pull in entries:
        if type(pull) is not dict or type(pull.get("number")) is not int or pull["number"] < 1:
            continue
        head_data = pull.get("head")
        source = head_data.get("repo") if type(head_data) is dict else None
        if (type(source) is not dict or type(source.get("full_name")) is not str
                or source["full_name"].casefold() != repo.casefold()):
            continue
        title = pull.get("title")
        state = "MERGED" if pull.get("merged_at") else (
            "OPEN" if pull.get("state") == "open" else "CLOSED" if pull.get("state") == "closed" else "")
        if type(title) is not str or not state:
            continue
        sha = head_data.get("sha")
        head = sha[:7].lower() if type(sha) is str and re.fullmatch(r"[0-9a-fA-F]+", sha) else ""
        full = sha.lower() if head and HEX_SHA.fullmatch(sha.lower()) else ""
        title = re.sub(r"#(?=[0-9])", "", title)
        body = pull.get("body")
        closes = tuple(sorted({int(match.group(1)) for match in re.finditer(
            r"(?im)\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#([0-9]{1,7})\b",
            body if type(body) is str else "")}))
        out.append(BoardPull(pull["number"], _label(title, 60), state,
                             _github_time(pull.get("merged_at")), _github_time(pull.get("updated_at")),
                             head, pull.get("draft") is True, closes, full))
    return out


@dataclass(frozen=True)
class PrRow:
    number: int
    head: str
    review: str
    ec2: str
    linux: str
    mac: str
    docs: str
    next: str
    owner: str
    current: str = "未確認"


def pr_reviewing(office) -> set[tuple[str, str]]:
    """PR/head pairs with a live D6 review task."""
    result = set()
    for task_id in _d6_running_tasks(office if type(office) is list else []):
        match = _review_task(task_id)
        if match:
            result.add((str(match[0]), match[1].lower()))
    return result


def _ci_word(entry, known: bool) -> str:
    if not known or entry is None:
        return "?"
    status, conclusion = entry
    if status in CI_ACTIVE:
        return "排隊" if status in ("queued", "waiting", "pending", "requested") else "執行中"
    if status == "completed":
        return CI_CONCLUSIONS.get(conclusion, "結果不明")
    return "?"


CHECK_KEYS = ("linux", "mac", "docs")


def _check_word(checks, number: int, sha: str, key: str) -> str:
    """One check ("linux", "mac" or "docs") of one PR at its exact head,
    looked up by PR number and full 40-character SHA (GitHubSource.pr_checks
    keys): 不需 when the PR's changed paths do not require it; ? when the read
    failed, did not return this PR at this head, named this PR in its errors,
    cut its file or check list, or the full SHA is unknown; — when a required
    check has no run on the head; else its state."""
    found = (checks.get((number, sha)) if type(checks) is dict and type(number) is int
             and type(sha) is str and HEX_SHA.fullmatch(sha) else None)
    if not isinstance(found, HeadChecks) or found.required is None:
        return "?"
    if key not in found.required:
        return "不需"
    if found.runs is None:
        return "?"
    if key not in found.runs:
        return "—"
    return _ci_word(found.runs[key], True)


def board_open_pulls(pulls, checks) -> list[BoardPull]:
    """The open PRs the PR board shows, by number: every open PR the GraphQL
    read returned (GitHubSource.pr_checks keys), plus open PRs on the pulls
    page it did not return (their checks then show ?). The GraphQL read comes
    after the pulls page, so its head wins. Title, draft flag and closing
    references come from the pulls page when it lists the PR, else the draft
    flag from the GraphQL read. Pure."""
    found = {pull.number: pull for pull in pulls or []
             if isinstance(pull, BoardPull) and pull.state == "OPEN"}
    for key, result in (checks.items() if type(checks) is dict else ()):
        if not (type(key) is tuple and len(key) == 2 and type(key[0]) is int
                and type(key[1]) is str and HEX_SHA.fullmatch(key[1])):
            continue
        number, sha = key
        known = found.get(number)
        if known is not None:
            found[number] = replace(known, head=sha[:7], sha=sha)
        else:
            found[number] = BoardPull(number, "", "OPEN", 0.0, 0.0, sha[:7],
                                      isinstance(result, HeadChecks) and result.draft is True,
                                      (), sha)
    return sorted(found.values(), key=lambda pull: pull.number)


def _names_head(named: str, head: str, sha: str = "") -> bool:
    """Whether a HEAD line's id (7 to 40 hex characters) names this head: a
    prefix of its full SHA when that is known, else its seven characters."""
    named, head, sha = named.lower(), head.lower(), (sha or "").lower()
    if sha and HEX_SHA.fullmatch(sha):
        return len(named) >= 7 and sha.startswith(named)
    return bool(head) and named[:7] == head[:7]


def _head_verdict(entries, head: str, sha: str = "") -> tuple[str, str]:
    """A PR's review state for its current head, from review_verdicts()
    entries. Only the head a verdict's HEAD line names counts, never another
    commit id in its body. The newest verdict whose HEAD line names any head
    decides: ("current", verdict) when it names this head (_names_head(), by
    the full SHA when known); ("stale", "") when it names another (older)
    head, even if an earlier verdict named this one; ("", "") when no verdict
    names a head."""
    bound = [entry for entry in entries if type(entry) in (tuple, list) and len(entry) >= 4
             and type(entry[0]) in (int, float) and type(entry[3]) is str and entry[3]
             ] if type(entries) in (tuple, list) else []
    if not bound or not head:
        return "", ""
    newest = max(bound, key=lambda entry: entry[0])
    if _names_head(newest[3], head, sha):
        return "current", newest[1]
    return "stale", ""


def _work_names(row: OfficeRow) -> str:
    return " ".join((row.task, row.work, row.detail, row.codex_title))


# The PR part of a bound work label (_codex_work_label, _bind_d6_pr_rows).
BOUND_PR = re.compile(r"(?:^|· )PR #([0-9]+)(?= ·|$)")


def _bound_pr(row: OfficeRow) -> int | None:
    """The PR a row's own bound label names. Text after "：" is a free summary."""
    if not row.work_bound:
        return None
    found = BOUND_PR.search(row.work.partition("：")[0])
    return int(found.group(1)) if found else None


def _codex_family(row: OfficeRow) -> bool:
    return "codex" in row.name.casefold() or row.key.startswith("codex:")


def _names_pr(row: OfficeRow, number: int, head: str) -> bool:
    text = _work_names(row)
    review = _review_task(row.task[5:]) if row.role == "reviewer" and row.task.startswith("task ") else None
    if row.role in ("pm", "debug reviewer"):
        return False
    if row.role == "reviewer" and row.task.startswith("task "):
        if review is None or review[0] != str(number):
            return False
        return not head or review[1].casefold().startswith(head.casefold())
    bound = _bound_pr(row)
    # A row bound to one PR is not working on another PR its title or summary mentions.
    if bound is not None and bound != number:
        return False
    if bound is None and re.search(rf"\bPR\s*#?{number}\b", text, re.IGNORECASE) is None:
        return False
    commits = re.findall(r"@([0-9a-f]{7,40})(?![0-9a-f])", text, re.IGNORECASE)
    # Some real work labels name the PR but omit its head. Bind by the head
    # when a row states one; otherwise the PR reference is sufficient.
    return not commits or bool(head and any(
        commit.casefold().startswith(head.casefold()) for commit in commits))


def _names_issue(row: OfficeRow, number: int) -> bool:
    if row.role in ("pm", "debug reviewer"):
        return False
    text = _work_names(row)
    return (re.search(rf"\bIssue\s*#?{number}\b", text, re.IGNORECASE) is not None
            or re.search(rf"(?<![\w#])(?<!PR )#{number}\b", text, re.IGNORECASE) is not None)


PEOPLE_ORDER = ("Codex", "Reviewer", "Claude", "OpenCode", "AGY", "CompanyGW", "CI")


def _row_person(row: OfficeRow) -> str:
    if row.role == "reviewer":
        return "Reviewer"
    if _codex_family(row):
        return "Codex"
    name = row.name.casefold()
    return next((label for marker, label in (
        ("claude", "Claude"), ("opencode", "OpenCode"),
        ("agy", "AGY"), ("antigravity", "AGY"),
        ("company gw", "CompanyGW"), ("gw-agent", "CompanyGW"))
                 if marker in name), "")


def _current_people(rows, predicate) -> tuple[set, bool]:
    """(who works on it, whether some relevant row leaves that uncertain).

    A working row the predicate names is a person. An UNCHECKED row the
    predicate names, or a live D6 row whose PR binding is unknown, adds
    uncertainty; a row bound to another PR does not."""
    people, uncertain = set(), False
    for row in rows:
        if row.state not in (WORKING, UNCHECKED) or row.role in ("pm", "debug reviewer"):
            continue
        if predicate(row):
            if row.state == UNCHECKED:
                uncertain = True
            elif _row_person(row):
                people.add(_row_person(row))
            continue
        if not row.task.startswith("task "):
            continue
        if row.role == "reviewer" and _review_task(row.task[5:]):
            continue   # a review task names its own PR
        if _bound_pr(row) is None:
            uncertain = True
    return people, uncertain


def _people_text(people, uncertain) -> str:
    """Known people first; 未確認 only when none is known, 沒人 only when nothing is uncertain."""
    return ("、".join(person for person in PEOPLE_ORDER if person in people)
            or ("未確認" if uncertain else "沒人"))


def _current_person(rows, predicate, *, readable=True) -> str:
    if not readable:
        return "未確認"
    return _people_text(*_current_people(rows, predicate))


def _pr_people(office, pull, checks) -> tuple[set, bool]:
    """The one person/CI answer per PR the PR table, running PR row and Issue rows share.
    checks: GitHubSource.pr_checks; CI counts while a check the PR's paths
    require is queued or running on the exact head (_check_word() says 不需
    for any other). Check data missing or unreadable for this PR (any ?)
    leaves it uncertain."""
    if not pull or not pull.head:
        return set(), True   # a PR missing from the read page has unknown head and CI
    people, uncertain = _current_people(
        office, lambda row: _names_pr(row, pull.number, pull.head))
    if pull.state == "OPEN":
        words = [_check_word(checks, pull.number, pull.sha, key) for key in CHECK_KEYS]
        if "?" in words:
            uncertain = True
        if any(word in ("執行中", "排隊") for word in words):
            people.add("CI")
    return people, uncertain


def _current_pr_people(office, pull, checks, *, github_known=True, office_known=True):
    """One shared person/CI answer for a PR and its issue-board aliases."""
    if not github_known or not office_known:
        return "未確認"
    return _people_text(*_pr_people(office, pull, checks))


def pr_board_rows(pulls, verdicts, checks, ec2, reviewing, pending, office=(), *, github_known=True,
                  office_known=True) -> list[PrRow]:
    """Build the read-only PR status table from already-read local/GitHub data.
    checks: GitHubSource.pr_checks, HeadChecks of each open PR keyed by its
    number and full head SHA; the rows are board_open_pulls(), so every open
    PR that read returned has a row even when the pulls page misses it; only
    the checks a PR's changed paths require decide its next step;
    verdicts: review_verdicts(), bound to the head their HEAD line names."""
    rows = []
    for pull in board_open_pulls(pulls, checks):
        number, head = str(pull.number), pull.head.lower()
        review = "未審" if head else "?"
        if head and (number, head) in (reviewing or set()):
            review = "審查中"
        elif head:
            bound, verdict = _head_verdict((verdicts or {}).get(number, []), head, pull.sha)
            if bound == "current":
                review = {"PASS": "通過", "FAIL": "要修"}.get(verdict, "未確認")
            elif bound == "stale":
                review = "舊審查"
        ec2_word = "?" if not head or ec2 is None else (
            ec2[0].get(head, "?") if head in ec2[0] else "?" if ec2[1] else "—")
        linux, mac, docs = (_check_word(checks, pull.number, pull.sha, key) for key in CHECK_KEYS)
        required = [word for word in (linux, mac, docs) if word != "不需"]
        others = [word for word in (linux, docs) if word != "不需"]   # the EC2 Linux workflow
        waiting_owner = any(type(item) is OwnerPending and item.kind == "waiting_owner"
                            and item.reference == f"#{number}" for item in pending or [])
        next_step, owner = "未確認", "PM"
        if waiting_owner:
            next_step, owner = "等你決定", "Owner"
        elif pull.draft:
            next_step, owner = "草稿", "Developer"
        elif review == "審查中":
            next_step, owner = "等審查", "Reviewer"
        elif review == "要修":
            next_step, owner = "修復", "Developer"
        elif review == "舊審查":   # a stale verdict needs a new review before CI work
            next_step, owner = "派審查", "PM"
        elif any(word in ("失敗", "逾時", "取消") for word in required):
            next_step, owner = "修 CI", "PM"
        elif ec2_word in ("FAIL", "SHA_MISMATCH", "TIMEOUT"):
            next_step, owner = "修 EC2 失敗", "PM"
        elif review == "通過":
            # A required Linux or docs check with no passing run is not a pass;
            # a required macOS check that did not run needs the mac-ci label.
            if (any(word in ("?", "結果不明") for word in required)
                    or any(word in ("—", "略過") for word in others)):
                next_step, owner = "未確認 CI", "PM"
            elif any(word in ("執行中", "排隊") for word in others):
                next_step, owner = "等 CI", "CI"
            elif mac in ("成功", "不需"):   # every other required check is 成功 here
                next_step, owner = "可合併", "PM"
            elif mac in ("執行中", "排隊"):
                next_step, owner = "等 macOS", "CI"
            else:   # macOS 略過 or —
                next_step, owner = "加 mac-ci", "PM"
        elif review == "未審":
            next_step, owner = "派審查", "PM"
        current = _current_pr_people(office, pull, checks,
                                     github_known=github_known,
                                     office_known=office_known)
        rows.append(PrRow(pull.number, head, review, ec2_word, linux, mac, docs,
                          next_step, owner, current))
    return rows


def _board_ref_number(ref):
    match = re.fullmatch(r"(?:PR )?#([0-9]{1,7})", ref or "")
    return int(match.group(1)) if match else None


def work_board(github, board: PmBoard, now, *, ec2=None, reviewing=None, office=(), office_known=True) -> WorkBoard:
    pulls = github.board_pulls if github is not None else None
    github_known = bool(github is not None and github.successful_read)
    board_known = board.status in ("ok", "stale")
    owner = list(github.pending if github is not None else [])
    owner_cut = bool(getattr(github, "comments_cut", False)) if github_known else False
    running = list(board.running) if board_known else []
    queue = list(board.queue) if board_known else []
    blocked = list(board.blocked) if board_known else []
    file_items = running + queue + blocked
    refs = {_board_ref_number(item.ref) for item in file_items}
    # Queue and blocked assignments take an open PR out of the running column.
    queued_blocked = {_board_ref_number(item.ref) for item in queue + blocked}
    checks = getattr(github, "pr_checks", None) if github is not None else None
    # Open PRs at the heads the PR table uses (the GraphQL read wins).
    open_pulls = ({pull.number: pull for pull in board_open_pulls(pulls, checks)}
                  if github_known and pulls is not None else {})
    if github_known:
        for pull in pulls or []:
            if pull.state != "OPEN" or pull.number in refs or pull.number in queued_blocked:
                continue
            # The same head-bound rule as the PR table: a verdict counts only for
            # the head its HEAD line names.
            at = open_pulls.get(pull.number, pull)
            bound, verdict = _head_verdict((github.verdicts or {}).get(str(pull.number), []),
                                           at.head.lower(), at.sha)
            what = pull.title + (" 草稿" if pull.draft else "")
            if bound == "current" and verdict == "FAIL":
                next_step = "Developer"
            elif bound == "current" and verdict == "PASS":
                next_step = "PM"
            elif bound == "current":
                next_step = "未確認"
                what += "「已見審查結論，下一步未確認」"
            elif bound == "stale":
                next_step = "PM"
                what += "「只有舊版本的審查結論，要重新審查」"
            else:
                next_step = "未確認"
                what += "「未見審查結論」"
            running.append(PmBoardItem(f"PR #{pull.number}", what, next_step))
    # The current owner is derived from the live office rows and the already-read
    # PR page. An issue may be worked through an open PR whose body closes it.
    # Issue aliases need a readable pulls page and come from open PRs only.
    snapshot_known = github_known and pulls is not None
    pulls_by_number = {pull.number: pull for pull in (pulls or [])}
    pulls_by_number.update(open_pulls)
    issue_prs = {}
    if snapshot_known:
        for pull in pulls:
            if pull.state == "OPEN":
                for issue in pull.closes:
                    issue_prs.setdefault(issue, set()).add(pull.number)
    resolved = []
    for item in running:
        number = _board_ref_number(item.ref)
        if not office_known or not snapshot_known:
            current = "未確認"
        elif re.match(r"PR\s", item.ref):
            current = _people_text(*_pr_people(office, pulls_by_number.get(number), checks))
        else:
            people, uncertain = _current_people(
                office, lambda row, issue=number: _names_issue(row, issue))
            for pr in sorted(issue_prs.get(number, ())):
                more, unsure = _pr_people(office, pulls_by_number[pr], checks)
                people |= more
                uncertain = uncertain or unsure
            current = _people_text(people, uncertain)
        resolved.append(replace(item, current=current))
    running = resolved
    running.sort(key=lambda item: (_board_ref_number(item.ref) or 0, item.ref))
    queue.sort(key=lambda item: (_board_ref_number(item.ref) or 0, item.ref))
    blocked.sort(key=lambda item: (_board_ref_number(item.ref) or 0, item.ref))
    # A local reason is part of the row text for queued or blocked work.
    queue = [replace(item, what=item.what + (f"：{item.reason}" if item.reason else "")) for item in queue]
    blocked = [replace(item, what=item.what + (f"：{item.reason}" if item.reason else "")) for item in blocked]
    local = datetime.fromtimestamp(float(now))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    done = sorted((pull for pull in (pulls or []) if pull.state == "MERGED" and pull.merged_at >= midnight),
                  key=lambda pull: pull.merged_at, reverse=True)
    page_count = getattr(github, "board_page_count", None) if github is not None else None
    oldest = getattr(github, "board_oldest_updated", None) if github is not None else None
    # Test doubles and older snapshots may not carry raw-page metadata.
    if page_count is None and github_known:
        page_count = len(pulls or [])
    notes = []
    if github_known and page_count is not None and page_count >= 100:
        notes.append("進行中的 GitHub 來源只看最近更新的 100 筆 PR，可能不完整")
        if oldest is None or oldest >= midnight:
            notes.append("今天完成可能不完整（只讀最近 100 筆 PR）")
    note = "；".join(notes)
    github_status = ("未讀 GitHub" if github is None else
                     "GitHub 讀取失敗，以下可能過時" if github.read_failed else
                     "GitHub 尚未讀取" if not github_known else "")
    if github is not None and GITHUB_IDENTITY_STATUS:
        github_status = "；".join(
            part for part in (github_status, GITHUB_IDENTITY_STATUS) if part)
    prs = (pr_board_rows(pulls, github.verdicts, checks,
                         ec2, reviewing, github.pending, office, github_known=github_known,
                         office_known=office_known) if github_known else [])
    return WorkBoard(owner, running, queue, blocked, done, github_known, board_known,
                     github_status, board.status, board.age, board.skipped, note,
                     int(github.window // 3600) if github is not None else 12, prs, owner_cut)


def board_view(work: WorkBoard, width=None) -> list[tuple[str, str]]:
    def append_header(text, style=""):
        lines = _wrap(text, width) if width is not None else [text]
        out.extend((line, style) for line in lines)

    today = len(work.done) if work.github_known else "?"
    owner_known = work.github_known and not GITHUB_IDENTITY_STATUS
    known_running = len(work.running)
    waiting_owner = sum(item.kind == "waiting_owner" for item in work.owner)
    other_owner = len(work.owner) - waiting_owner
    waiting_count = ("?" if work.owner_cut and not waiting_owner else
                     f"{waiting_owner}{'+' if work.owner_cut else ''}")
    other_count = ("?" if work.owner_cut and not other_owner else
                   f"{other_owner}{'+' if work.owner_cut else ''}")
    owner_count = ("?" if not owner_known or (work.owner_cut and not work.owner) else
                   f"{len(work.owner)}{'+' if work.owner_cut else ''}（等你決定 {waiting_count}，其他 Owner 請求 {other_count}）")
    counts = (f"今天合併 {today} · 進行中 {known_running if work.github_known else '?'} · "
              f"等 PM {sum(item.next == 'PM' for item in work.running + work.queue + work.blocked) if work.github_known and work.board_known else '?'} · "
              f"等你 {owner_count} · 排隊 {len(work.queue) if work.board_known else '?'} · "
              f"卡住 {len(work.blocked) if work.board_known else '?'}")
    out = []
    append_header("工作白板", "title")
    append_header(counts)
    if work.github_status:
        append_header(work.github_status, "!")
    board_warnings = {
        "missing": "PM 白板檔不存在，排隊和卡住未知",
        "unreadable": "PM 白板檔無法讀取，排隊和卡住未知",
        "malformed": "PM 白板檔格式錯誤，排隊和卡住未知",
        "undated": "PM 白板檔沒有有效更新時間，排隊和卡住未知",
    }
    if work.board_status == "stale":
        minutes = int((work.board_age or 0) // 60)
        append_header(f"PM 白板檔已 {minutes} 分鐘沒更新，排隊和卡住可能過時", "!")
    elif work.board_status in board_warnings:
        append_header(board_warnings[work.board_status], "!")
    if work.skipped:
        append_header(f"白板檔有 {work.skipped} 筆略過", "!")
    if work.note:
        append_header(work.note, "!")
    sections = (("等你", work.owner, owner_known), ("進行中", work.running, work.github_known),
                ("排隊", work.queue, work.board_known), ("卡住", work.blocked, work.board_known),
                (f"今天完成（累計 {today}）", work.done, work.github_known),
                ("PR 看板", work.prs, work.github_known))
    for title, items, known in sections:
        append_header(f"── {title} ──", "section")
        if not items:
            empty = (f"未確認（留言太多，只讀了最新 {GITHUB_PAGE * GITHUB_PAGES} 則）"
                     if known and title == "等你" and work.owner_cut else
                     f"無（只看最近 {work.window_hours} 小時留言）"
                     if known and title == "等你" else "無" if known else "未知")
            append_header(empty, "dim")
            continue
        for item in items:
            if isinstance(item, BoardPull):
                ref = f"PR #{item.number}"
                rest = f"{item.title}  → {datetime.fromtimestamp(item.merged_at).astimezone():%H:%M}"
            elif isinstance(item, PrRow):
                ref = f"PR #{item.number}"
                rest = (f"審查 {item.review} · EC2 {item.ec2} · Linux {item.linux} · "
                        f"macOS {item.mac} · Docs {item.docs}  → {item.next}（{item.owner}）"
                        f" · 現在：{item.current}")
            elif isinstance(item, OwnerPending):
                ref = item.reference
                rest = f"{item.summary or item.status}  → 你"
            else:
                ref = item.ref
                current = f" · 現在：{item.current}" if item.current else ""
                rest = f"{item.what}  → {item.next}{current}"
            if width:
                first_width = max(1, width - _width(ref) - 2)
                if first_width < 12:
                    wrapped = _wrap(rest, max(1, width - 2))
                    pieces = [ref, *["  " + line for line in wrapped]]
                else:
                    wrapped = _wrap(rest, first_width)
                    pieces = [ref + "  " + wrapped[0]]
                    pieces.extend(" " * (_width(ref) + 2) + line for line in wrapped[1:])
            else:
                pieces = [f"{ref}  {rest}"]
            out.extend((piece, "") for piece in pieces)
    return out


def _review_label(pr: str, sha: str) -> str:
    target = f"PR #{pr}" if pr else "（PR 未知）"
    return f"審 {target}" + (f" @{sha}" if sha else "")


CODEX_DAYS = 7   # a long-running thread lives in the folder of the day it started


def _review_rows(path: str, now: float, parent: OfficeRow) -> list[tuple]:
    """(row, pr, head, time) per recent `claude --print` call a Codex thread
    made, shown running until _review_result finds it ended. A call that names
    a PR and head is a Reviewer bound to that dispatch; any other call is an
    unbound one-shot run."""
    rows = []
    for when, pr, sha in codex_reviews(path, now):
        age = max(0.0, now - when)
        bound = bool(pr and sha)
        label = _review_label(pr, sha) if bound else "單次呼叫（呼叫裡沒寫 PR 和版本）"
        rows.append((OfficeRow("claude reviewer" if bound else "claude 單次呼叫", WORKING,
                               f"{label}（單次呼叫執行中）", age, 30, kind="subagent",
                               group=parent.key, key="review", role="reviewer" if bound else "",
                               task=f"PR #{pr} @{sha}" if bound else "", evidence="codex 紀錄（呼叫）"),
                     pr if bound else "", sha, when))
    return rows


def _review_result(row: OfficeRow, pr: str, sha: str, when: float, verdicts, now: float,
                   until: float | None = None) -> OfficeRow:
    """An ended call shows the first verdict posted on its PR after it started,
    and before the next call for the same PR and head started (until), that
    names its head, aged from that verdict. The call knows its own head, so no
    comment has to be read for which head it is about; later rounds that
    mention this head come after its own verdict, and a retry's verdict belongs
    to the retry. Every Reviewer route is read the same way."""
    if not pr:
        return replace(row, state=DONE, detail=row.detail.replace("（單次呼叫執行中）", "：已結束"))
    posted = sorted(item[:2] for item in (verdicts or {}).get(pr, [])
                    if item[0] >= when - CALL_SLACK and (until is None or item[0] < until)
                    and sha.lower() in item[2])
    outcome = posted[0][1] if posted else UNREAD_VERDICT if verdicts is None else NO_VERDICT
    age = max(0.0, now - posted[0][0]) if posted else row.age
    return replace(row, state=DONE, outcome=outcome, age=min(age, row.age),
                   detail=row.detail.replace("（單次呼叫執行中）", f"：{outcome}"),
                   evidence="codex 紀錄（呼叫）＋GitHub 結論" if posted else row.evidence)


CALL_GRACE = 15.0   # seconds a recorded one-shot call may run before its process is seen
CALL_SLACK = 5.0    # process start times are whole seconds; the call is recorded first


def _ended_calls(subs, live_calls) -> set:
    """Review rows the record shows running whose process is gone. Each row,
    newest first, claims the unclaimed live call that started closest after
    it; a call cannot have started before its own record, so a row with
    nothing left to claim has ended (the PM never read its result). A row
    younger than CALL_GRACE may not have spawned yet and is left alone."""
    starts = sorted(live_calls)
    ended = set()
    rows = sorted((sub for _, sub in subs if sub.key == "review" and sub.state == WORKING
                   and sub.age is not None), key=lambda sub: sub.age)
    for sub in rows:
        began = -sub.age   # relative to now, like the process starts
        match = next((start for start in starts if start >= began - CALL_SLACK), None)
        if match is not None:
            starts.remove(match)
        elif sub.age > CALL_GRACE:
            ended.add(id(sub))
    return ended


def codex_rows(now: float, root: str | None = None,
               canonical: str | None = None,
               live_calls: list[float] | None = None, verdicts: dict | None = None, *,
               codex_running: bool = False, codex_execs: int | None = None) -> list[OfficeRow]:
    """live_calls: how many seconds ago each Codex-started one-shot claude
    process now running started (as negative offsets from now), or None when
    the process table was not read. verdicts: review_verdicts() of the GitHub
    comments read, or None when GitHub was not read. codex_running: whether
    any Codex CLI runs; codex_execs: how many `codex exec` CLIs run, or None
    when not known (see _ended_exec_runs)."""
    root = CODEX_SESSIONS if root is None else root
    canonical = canonical or canonical_root()
    days = {time.strftime("%Y/%m/%d", time.localtime(now - d * 86400))
            for d in range(CODEX_DAYS)}
    paths = []
    for day in days:
        paths += glob.glob(os.path.join(root, day, "rollout-*.jsonl"))
    mains, subs, scanned, actions = {}, [], set(), {}
    history = {"db": open_codex_history(CODEX_HISTORY), "titles": open_codex_history(CODEX_STATE)}
    try:
        return _codex_rows(now, sorted(paths), canonical, history, mains, subs, scanned, actions,
                           live_calls, verdicts, codex_running, codex_execs)
    finally:
        for db in history.values():
            if db is not None:
                db.close()


EXEC_SILENT_END = 60 * 60   # a running `codex exec` turn silent this long is taken as ended


def _ended_exec_runs(rows: list[OfficeRow], codex_running: bool,
                     codex_execs: int | None) -> set[str]:
    """Keys of 無回應 `codex exec` rows whose turn never ended in the session
    file but whose run is taken as gone (killed, crashed or abandoned), so
    they show as ended work instead. codex_execs: how many `codex exec` CLIs
    are running (None: not known; then only whether any Codex CLI runs). With
    N running, the N most recently active started runs may be live; any other
    has no process left. A run silent for EXEC_SILENT_END is taken as ended
    whatever runs. A run that wrote within OFFICE_STALL is WORKING, never
    ended here."""
    started = sorted((row for row in rows if row.codex_exec_started),
                     key=lambda row: row.age if row.age is not None else 1e12)
    if codex_execs is None:
        live = {row.key for row in started} if codex_running else set()
    else:
        live = {row.key for row in started[:max(0, codex_execs)]}
    return {row.key for row in started if row.state == STALLED and (
        row.key not in live or (row.age is not None and row.age > EXEC_SILENT_END))}


def _ended_exec_row(row: OfficeRow) -> OfficeRow | None:
    """An ended `codex exec` row: done, and gone after OFFICE_DONE_WINDOW like
    a completed run."""
    if row.age is not None and row.age > OFFICE_DONE_WINDOW:
        return None
    return replace(row, state=DONE, detail=row.detail + "（推測 codex exec 已結束，未見結束標記）")


def _codex_rows(now, paths, canonical, history, mains, subs, scanned, actions,
                live_calls=None, verdicts=None, codex_running=False,
                codex_execs=None) -> list[OfficeRow]:
    calls = {}   # id(review row) -> (pr, head, time)
    for path in paths:
        try:
            if now - os.path.getmtime(path) > OFFICE_WINDOW:
                continue
        except OSError:
            continue
        found = _codex_row(path, now, canonical, history)
        if found is None:
            continue
        scanned.add(path)
        kind, key, row = found[:3]
        if kind == "main":
            mains[key] = row
            actions[key] = found[3] if len(found) > 3 else ""
        else:
            subs.append((key, row))
        for review, pr, sha, when in _review_rows(path, now, row):
            calls[id(review)] = (pr, sha, when)
            subs.append((key if kind == "main" else None, review))
    ended_exec = _ended_exec_runs(list(mains.values()) + [row for _, row in subs],
                                  codex_running, codex_execs)
    for key, row in list(mains.items()):
        if row.key in ended_exec:
            row = _ended_exec_row(row)
            if row is None:
                del mains[key]
            else:
                mains[key] = row
    subs = [(parent, _ended_exec_row(row) if row.key in ended_exec else row)
            for parent, row in subs]
    subs = [(parent, row) for parent, row in subs if row is not None]
    forget_reviews(scanned)
    # A call has ended once its process is gone; without the process table it
    # is shown running until it is too old to matter.
    ended = _ended_calls(subs, live_calls) if live_calls is not None else set()
    starts: dict = {}
    for pr, sha, when in calls.values():
        starts.setdefault((pr, sha), []).append(when)

    def until(pr, sha, when):   # when the next call for this PR and head started
        return min((start for start in starts[(pr, sha)] if start > when), default=None)
    subs = [(parent, _review_result(sub, *calls[id(sub)], verdicts, now, until(*calls[id(sub)]))
             if id(sub) in ended else sub) for parent, sub in subs]
    subs = [(parent, sub) for parent, sub in subs if sub.key != "review"
            or (sub.age or 0.0) <= (OFFICE_STALL if sub.state == WORKING else OFFICE_DONE_WINDOW)]
    for key, newer in _superseded_failures(mains).items():
        row = mains[key]
        mains[key] = replace(row, state=DONE,
                             detail=row.detail + f"（上一輪失敗，已由 {newer or '較新的單次呼叫'} 完成）")
    rows = []
    for key, row in mains.items():
        if row.main:
            busy = [sub.detail for parent, sub in subs if parent == key and sub.state == WORKING]
            detail = (
                f"指揮 {len(busy)} 個進行中的 subagent：" + "、".join(busy[:3]) if busy
                else (actions.get(key) or "處理中") if row.state in (WORKING, STALLED)
                else actions.get(key) or ("已結束" if row.state == DONE else "等待下一個指示")) + row.detail
            row = replace(row, state=WORKING if busy else row.state, detail=detail)
        rows.append(row)
    return rows + [row for _, row in subs]


def _superseded_failures(mains: dict) -> dict:
    """{key: newer sid} of failed one-shot `codex exec` rows in a registered D6
    whose newest Codex session of the same task and role is a completed
    one-shot run that ended after them. Only the displayed state changes;
    session files and turn records are never touched. A newer failure,
    running, stalled, interrupted or unknown session, or a tie, keeps every
    alert."""
    groups: dict = {}
    for key, row in mains.items():
        if row.task.startswith("task ") and row.role and row.age is not None:
            groups.setdefault((row.task, row.role), []).append((key, row))
    found = {}
    for items in groups.values():
        newest = min(row.age for _, row in items)
        top = [row for _, row in items if row.age == newest]
        if len(top) != 1 or top[0].exec_result != "completed" or top[0].state != DONE:
            continue
        for key, row in items:
            if row.state == ATTENTION and row.exec_result == "failed" and row.age > newest:
                found[key] = top[0].sid
    return found


def _claude_state(entry: dict, who: str, task: str, detail: str, age: float | None, now: float,
                  tail: list) -> tuple[str, str, float | None]:
    """(state, detail, age) of a Claude session from what its running CLI
    reports: the registry status and, for a background session, the state of
    its job (`claude agents` shows both); else from its session file."""
    status, job, since = entry.get("status"), entry.get("jobState"), entry.get("statusUpdatedAt")
    suffix = f"（{task}）" if task else ""
    quiet = float("inf") if age is None else age   # no session file: no activity seen
    state = WORKING if quiet <= OFFICE_BUSY else IDLE
    if _claude_last_turn_finished(tail):
        state = IDLE
    if status == "waiting" or job == "blocked":
        # Stopped at a prompt or menu only a person or its driving agent can answer.
        state = ATTENTION
        word = ("等人確認權限" if entry.get("waitingFor") == "permission prompt" else "等人回覆"
                if status == "waiting" and job != "blocked" else "等人操作")
        detail = f"{word}，要{who}回{suffix}"
    elif status == "busy" or job == "working":
        state = WORKING
        if quiet > OFFICE_BUSY and _awaiting_model(tail):
            detail = "等模型回覆" + suffix
    elif status == "idle":
        state = IDLE
    if state in (ATTENTION, IDLE) and type(since) in (int, float) and 0 < since / 1000 <= now:
        # How long it has waited or sat idle: the file can be touched
        # later by metadata writes that are not work.
        age = now - since / 1000
    elif state == IDLE and _awaiting_model(tail):
        # No registry to ask: an unanswered tool result means the request
        # is still with the model, not idle.
        state = WORKING if quiet <= OFFICE_STALL else STALLED
        detail = "等模型回覆" + ("超過 15 分鐘" if state == STALLED else "") + suffix
    clis = entry.get("clis")
    if type(clis) is int and clis > 1:
        detail += f"（同一 session 有 {clis} 個 CLI）"
    return state, detail, age


def _claude_row(entry: dict, state: str, detail: str, age, place, task: str, key: str,
                sid: str, canonical: str, cli: bool = False) -> OfficeRow:
    # The name and id its launcher gave it: what a PM looks for to
    # find the window that needs an answer.
    name = entry.get("name")
    name = name if type(name) is str and SESSION_NAME.fullmatch(name) else ""
    running = entry.get("status") is not None
    if running:
        detail += f"｜{name}" if name else ""
    # In a registered D6 root the record's bound agent names the role; the
    # launcher's name is the record outside D6, or when the D6 record names
    # none. Nothing else claims a role (Owner 2026-10-23, #565).
    named = SESSION_ROLE.fullmatch(name)
    launcher = named.group(1) if named else ""
    bound_role = _d6_role(canonical, place[1]) if place[0] == "task" else ""
    role = bound_role or launcher
    # Only a running CLI says whether it has a terminal; a session moved to
    # the background keeps running without one.
    mode = ("背景" if entry.get("kind") == "bg" else "互動") if running else ""
    label = f"claude {role}".rstrip()
    return OfficeRow(f"{label}（{mode}）" if role and mode else f"{label} {mode}".rstrip(),
                     state, detail, age, 30 if place[0] == "pm" else 10, key=key, role=role,
                     task=task, evidence="claude 紀錄", cli=cli,
                     sid=sid[:8] if re.fullmatch(r"[0-9a-f-]{8}", sid[:8]) else "")


SESSION_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
# The role a launcher assigns in the session name (`claude --name claude-reviewer`).
# A suffix never ends in "-", for every role alike. Names that are not one
# of the listed roles keep the ordinary unnamed-session handling.
SESSION_ROLE = re.compile(r"claude-(pm|reviewer|developer)(?:-[A-Za-z0-9._-]*[A-Za-z0-9._])?")


def claude_roots(root=None) -> list[str]:
    """Project directories of every local Claude account: the default one plus
    each ~/.claude-* config directory. root: one directory or a list of them."""
    if root is not None:
        return [root] if isinstance(root, str) else list(root)
    roots, seen = [], set()
    for top in [CLAUDE_PROJECTS] + sorted(glob.glob(CLAUDE_OTHER_PROJECTS)):
        real = os.path.realpath(top)   # a ~/.claude-* symlink to another account is read once
        if real not in seen:
            seen.add(real)
            roots.append(top)
    return roots


def claude_sessions(root=None) -> list[str]:
    """The CLI registry directory next to each account's projects directory."""
    return [os.path.join(os.path.dirname(top), "sessions") for top in claude_roots(root)]


_listing_cache: dict = {}   # project folder -> (folder mtime_ns, its *.jsonl names)


PM_RUNTIME_DIR = os.path.expanduser("~/.local/state/llmtool/pm-runtime")
_pm_binding_listing: dict = {}   # directory -> (directory key, binding names)
_pm_binding_cache: dict = {}     # path -> (file key, minimal binding fields or None)


def _pm_runtime_bindings() -> dict[str, dict]:
    """Minimal pm_runtime bindings keyed by Claude session id.

    The directory is listed only when its mtime changes. Binding contents are
    read through the monitor's no-follow regular-file helper and only the
    session id, creation time, and cwd are retained. Conversation history is
    never opened.
    """
    directory = PM_RUNTIME_DIR
    try:
        info = os.lstat(directory)
    except OSError:
        _pm_binding_listing.pop(directory, None)
        return {}
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        _pm_binding_listing.pop(directory, None)
        return {}
    stamp = (info.st_ino, info.st_mtime_ns)
    listed = _pm_binding_listing.get(directory)
    if listed is None or listed[0] != stamp:
        try:
            names = sorted(name for name in os.listdir(directory)
                           if re.fullmatch(r"[0-9a-fA-F-]{36}\.binding\.json", name))
        except OSError:
            names = []
        listed = (stamp, names)
        _remember(_pm_binding_listing, directory, listed)

    found = {}
    keep = set()
    for name in listed[1]:
        path = os.path.join(directory, name)
        keep.add(path)
        try:
            file_info = os.lstat(path)
        except OSError:
            _pm_binding_cache.pop(path, None)
            continue
        key = (file_info.st_dev, file_info.st_ino, file_info.st_size, file_info.st_mtime_ns,
               file_info.st_ctime_ns, file_info.st_mode, file_info.st_uid)
        cached = _pm_binding_cache.get(path)
        if cached is None or cached[0] != key:
            binding = None
            if (stat.S_ISREG(file_info.st_mode) and file_info.st_uid == os.getuid()
                    and not file_info.st_mode & 0o077 and file_info.st_size <= 16 << 10):
                opened = _remote_test_open(path, 16 << 10)
                opened_key = (file_info.st_ino, file_info.st_mtime_ns, file_info.st_size)
                if opened is not None and opened[0] == opened_key and opened[1] not in (None, False):
                    record = _remote_test_json(opened[1])
                    if type(record) is dict:
                        sid = record.get("native_session_id")
                        filename_sid = name[:-len(".binding.json")]
                        if (record.get("runtime") == "claude-code" and
                                type(sid) is str and sid == filename_sid and
                                re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", sid)):
                            binding = {"session_id": sid, "created_at": record.get("created_at"),
                                       "cwd": record.get("cwd")}
            cached = (key, binding)
            _remember(_pm_binding_cache, path, cached)
        binding = cached[1]
        if binding is not None:
            found[binding["session_id"]] = binding
    for path in list(_pm_binding_cache):
        if path.startswith(directory + os.sep) and path not in keep:
            _pm_binding_cache.pop(path, None)
    return found


def _session_files(top: str) -> list[str]:
    """<top>/*/*.jsonl as glob lists it; a project folder whose mtime did not
    change keeps its last listing (a new session file changes that mtime,
    writes to an existing one do not)."""
    found = []
    try:
        with os.scandir(top) as entries:
            folders = [entry for entry in entries if not entry.name.startswith(".")
                       and entry.is_dir()]
    except OSError:
        return found
    for folder in folders:
        try:
            stamp = folder.stat().st_mtime_ns
        except OSError:
            continue
        cached = _listing_cache.get(folder.path)
        if cached is None or cached[0] != stamp:
            try:
                names = [name for name in os.listdir(folder.path)
                         if name.endswith(".jsonl") and not name.startswith(".")]
            except OSError:
                names = []
            cached = (stamp, names)
            _remember(_listing_cache, folder.path, cached)
        found += [os.path.join(folder.path, name) for name in cached[1]]
    return found


QUOTA_WARN_PERCENT = 80
CODEX_QUOTA_PATH = os.path.join(".local", "state", "llmtool", "codex-quota.json")
CODEX_QUOTA_FILE = os.path.join(os.path.expanduser("~"), CODEX_QUOTA_PATH)


def _quota_timestamp(value):
    if type(value) in (int, float):
        return float(value) if math.isfinite(value) and value > 0 else None
    if type(value) is str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            ts = parsed.timestamp() if parsed.tzinfo else None
            return ts if ts is not None and math.isfinite(ts) and ts > 0 else None
        except (ValueError, OverflowError):
            return None
    return None


def read_codex_quota(now, path=None) -> dict:
    """Read ~/.local/state/llmtool/codex-quota.json.

    Returns {'state': 'FRESH', ...} with the figures, or {'state': 'UNKNOWN', 'reason': reason}
    when the file is missing, unreadable, malformed, not mode 0600, or any window's read time
    is older than its window_minutes.
    """
    if path is None:
        path = os.path.join(os.path.expanduser("~"), CODEX_QUOTA_PATH)
    try:
        now = float(now)
    except (TypeError, ValueError):
        return {"state": "UNKNOWN", "reason": "unreadable"}

    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return {"state": "UNKNOWN", "reason": "missing"}
    except OSError:
        return {"state": "UNKNOWN", "reason": "unreadable"}

    if stat.S_IMODE(st.st_mode) != 0o600 or not stat.S_ISREG(st.st_mode):
        return {"state": "UNKNOWN", "reason": "wrong mode"}

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {"state": "UNKNOWN", "reason": "unreadable"}

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"state": "UNKNOWN", "reason": "malformed"}

    if not isinstance(data, dict):
        return {"state": "UNKNOWN", "reason": "malformed"}

    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        return {"state": "UNKNOWN", "reason": "malformed"}

    primary = data.get("primary")
    secondary = data.get("secondary")
    if not isinstance(primary, dict) or not isinstance(secondary, dict):
        return {"state": "UNKNOWN", "reason": "malformed"}

    for window_data in (primary, secondary):
        used = window_data.get("used_percent")
        window_minutes = window_data.get("window_minutes")
        reset = _quota_timestamp(window_data.get("resets_at"))
        if (type(used) not in (int, float) or isinstance(used, bool) or used < 0
                or type(window_minutes) not in (int, float) or isinstance(window_minutes, bool)
                or window_minutes <= 0
                or reset is None):
            return {"state": "UNKNOWN", "reason": "malformed"}

        read_val = (window_data.get("read_at") if "read_at" in window_data
                    else window_data.get("read_time") if "read_time" in window_data
                    else data.get("read_at") if "read_at" in data
                    else data.get("read_time"))
        read_time = _quota_timestamp(read_val)
        if read_time is None:
            return {"state": "UNKNOWN", "reason": "malformed"}

        if now - read_time > window_minutes * 60:
            return {"state": "UNKNOWN", "reason": "stale"}

    res = dict(data)
    res["state"] = "FRESH"
    return res


def quota_warnings(now, codex_root=None, claude_root=None, codex_quota_path=None) -> list[str]:
    """Quota notices from codex-quota.json and bounded local Codex and Claude session tails."""
    out = []
    seen_windows = set()
    try:
        quota_data = read_codex_quota(now, path=codex_quota_path)
    except Exception:
        quota_data = {"state": "UNKNOWN", "reason": "unreadable"}

    if quota_data.get("state") == "FRESH":
        for data in (quota_data.get("primary"), quota_data.get("secondary")):
            if type(data) is not dict:
                continue
            used = data.get("used_percent")
            window = data.get("window_minutes")
            reset = _quota_timestamp(data.get("resets_at"))
            read_time = (
                _quota_timestamp(data.get("read_at"))
                or _quota_timestamp(data.get("read_time"))
                or _quota_timestamp(quota_data.get("read_at"))
                or _quota_timestamp(quota_data.get("read_time"))
                or now
            )
            if (type(used) not in (int, float) or type(used) is bool
                    or type(window) not in (int, float) or type(window) is bool
                    or reset is None or reset <= now or used < QUOTA_WARN_PERCENT):
                continue
            window_text = ("5 小時額度" if window == 300 else
                           "7 天額度" if window == 10080 else f"{window:g} 分鐘額度")
            age = max(0, int((now - read_time) // 60))
            reset_local = datetime.fromtimestamp(reset)
            reset_format = "%H:%M" if reset_local.date() == datetime.fromtimestamp(now).date() else "%m-%d %H:%M"
            line = (f"Codex {window_text}已用 {used:g}%，{reset_local:{reset_format}} 重置"
                    f"（紀錄 {age} 分鐘前）")
            if line not in out:
                out.append(line)
                seen_windows.add(window_text)

    root = CODEX_SESSIONS if codex_root is None else codex_root
    paths = []
    for day in range(CODEX_DAYS):
        folder = time.strftime("%Y/%m/%d", time.localtime(now - day * 86400))
        paths.extend(glob.glob(os.path.join(root, folder, "rollout-*.jsonl")))
    recent = []
    for path in paths:
        try:
            modified = os.path.getmtime(path)
        except OSError:
            continue
        if now - modified <= OFFICE_WINDOW:
            recent.append((modified, path))
    latest_rate = None
    for modified, path in sorted(recent)[-8:]:
        for item in _jsonl_tail(path):
            payload = item.get("payload") if item.get("type") == "event_msg" else None
            limits = payload.get("rate_limits") if type(payload) is dict else None
            if type(limits) is dict:
                latest_rate = (limits, modified)
    if latest_rate is not None:
        latest_rate, rate_modified = latest_rate
        reached = latest_rate.get("rate_limit_reached_type")
        if type(reached) is str:
            has_active_window = any(
                type(data) is dict
                and (reset := _quota_timestamp(data.get("resets_at"))) is not None
                and reset > now
                for data in (latest_rate.get("primary"), latest_rate.get("secondary"))
            )
            if has_active_window:
                out.append("Codex 已到用量上限")
        else:
            for data in (latest_rate.get("primary"), latest_rate.get("secondary")):
                if type(data) is not dict:
                    continue
                used = data.get("used_percent")
                window = data.get("window_minutes")
                reset = _quota_timestamp(data.get("resets_at"))
                if (type(used) not in (int, float) or type(used) is bool
                        or type(window) not in (int, float) or type(window) is bool
                        or reset is None or reset <= now or used < QUOTA_WARN_PERCENT):
                    continue
                window_text = ("5 小時額度" if window == 300 else
                               "7 天額度" if window == 10080 else f"{window:g} 分鐘額度")
                if window_text in seen_windows:
                    continue
                age = max(0, int((now - rate_modified) // 60))
                reset_local = datetime.fromtimestamp(reset)
                reset_format = "%H:%M" if reset_local.date() == datetime.fromtimestamp(now).date() else "%m-%d %H:%M"
                line = (f"Codex {window_text}已用 {used:g}%，{reset_local:{reset_format}} 重置"
                        f"（紀錄 {age} 分鐘前）")
                if line not in out:
                    out.append(line)
                    seen_windows.add(window_text)

    for top in claude_roots(claude_root):
        candidates = []
        for path in _session_files(top):
            try:
                modified = os.path.getmtime(path)
            except OSError:
                continue
            if now - modified <= OFFICE_WINDOW:
                candidates.append((modified, path))
        activity = []
        for _, path in sorted(candidates, reverse=True)[:8]:
            for item in _jsonl_tail(path):
                stamp = _quota_timestamp(item.get("timestamp"))
                if stamp is None:
                    continue
                if item.get("error") == "rate_limit" or item.get("type") == "assistant":
                    activity.append((stamp, item))
        hits = [(stamp, item) for stamp, item in activity if item.get("error") == "rate_limit"]
        latest = max(hits, default=None, key=lambda entry: entry[0])
        later_assistant = max((stamp for stamp, item in activity
                               if item.get("type") == "assistant" and not item.get("error")),
                              default=0.0)
        if (latest is None or later_assistant > latest[0]
                or now - latest[0] > 5 * 3600):
            continue
        message = latest[1].get("message")
        content = message.get("content") if type(message) is dict else None
        first = content[0].get("text") if type(content) is list and content and type(content[0]) is dict else ""
        reset = re.search(r"resets ([0-9:]{1,5}(?:am|pm))", first, re.I) if type(first) is str else None
        config_name = os.path.basename(os.path.dirname(top))
        suffix = f"，resets {reset.group(1)}" if reset else ""
        out.append(f"Claude（{config_name}）已到用量上限{suffix}")
    return out


HALTED = "已停止"                 # a subagent its parent stopped (#1592): not a hang
OFFICE_HALTED_WINDOW = 10 * 60   # a stopped subagent's row stays this long


def _subagent_stopped(meta: dict, tail: list) -> bool:
    """A subagent that was stopped, not one that went quiet: its meta state
    field says so when present, else its log ends with the stop's marker."""
    if type(meta.get("state")) is str:
        return meta["state"].lower() in ("killed", "stopped")
    last = tail[-1] if tail else {}
    return last.get("type") == "user" and "[Request interrupted by user" in str(last.get("message"))


def claude_rows(now: float, root=None,
                canonical: str | None = None, live: dict | None = None) -> list[OfficeRow]:
    """live: {session id: (registry entry, who answers)} of running CLIs; what
    a CLI reports in its own registry beats any guess from the session file.
    A running session that needs a person is listed even before it has
    written a session file."""
    canonical = canonical or canonical_root()
    rows, seen = [], set()
    bindings = _pm_runtime_bindings()
    sessions = sorted((top, path) for top in claude_roots(root) for path in _session_files(top))
    for top, path in sessions:
        try:
            file_age = max(0.0, now - os.path.getmtime(path))
        except OSError:
            continue
        if file_age > OFFICE_WINDOW:
            continue
        tail = _jsonl_tail(path)
        activity_at = _claude_activity_time(tail)
        age = max(0.0, now - (activity_at or (now - file_age)))
        if age > OFFICE_WINDOW:
            continue
        cwd = next((item.get("cwd") for item in reversed(tail)
                    if type(item.get("cwd")) is str), None)
        place = _place(cwd, canonical)
        if place is None:
            continue
        title = next((item.get("aiTitle") for item in reversed(tail)
                      if item.get("type") == "ai-title"), None)
        detail = _label(title) or (_place_label(place) if place[0] != "pm" else "（無標題）")
        task = f"task {place[1]}" if place[0] == "task" else ""
        action = _last_action(tail) if age <= OFFICE_BUSY else ""
        if action:
            detail = action   # what it is doing right now beats the session title
        key = f"claude:{os.path.basename(path)}"
        sid = os.path.basename(path)[:-len(".jsonl")]
        binding = bindings.get(sid)
        # An interactive session another agent pings with `claude --resume -p`
        # gains sdk-cli entries but is still the interactive member.
        first = _first_entrypoint(path)
        one_shot = first == "sdk-cli" or first is None and any(
            item.get("entrypoint") == "sdk-cli" for item in tail)
        if one_shot:
            # A `claude -p` run: it exits when its one task is done. The session
            # does not say who started it, so it is not hung under anyone.
            state = WORKING if age <= OFFICE_BUSY else DONE
            if state == DONE and age > OFFICE_DONE_WINDOW:
                continue
            label = "PM（TUI）" if binding else "claude 單次呼叫"
            rows.append(OfficeRow(label, state, detail, age, 30, key=key,
                                  kind="agent" if binding else "subagent", task=task,
                                  evidence="claude 紀錄"))
            continue
        seen.add(sid)
        entry, who = (live or {}).get(sid, ({}, ""))
        state, detail, age = _claude_state(entry, who, task, detail, age, now, tail)
        if entry.get("status") is None:
            # No registry entry reports on this session (its CLI has exited,
            # or the registry or the running CLI's entry cannot be read): its
            # session file still holds the name its launcher gave it.
            entry = {"name": next((item.get("agentName") for item in reversed(tail)
                                   if item.get("type") == "agent-name"), None)}
        row = _claude_row(entry, state, detail, age, place, task, key, sid, canonical,
                          cli=first == "cli")
        rows.append(replace(row, name="PM（TUI）", role="") if binding else row)
        session_dir = path[:-len(".jsonl")]
        for meta_path in sorted(glob.glob(os.path.join(session_dir, "subagents",
                                                       "agent-*.meta.json"))):
            log_path = meta_path[:-len(".meta.json")] + ".jsonl"
            try:
                sub_age = max(0.0, now - os.path.getmtime(log_path))
                with open(meta_path, "rb") as handle:
                    meta = json.loads(handle.read(64 << 10))
            except (OSError, ValueError, RecursionError):
                continue
            if type(meta) is not dict:
                continue
            if sub_age <= OFFICE_BUSY:
                state = WORKING
            elif meta.get("finished") is True:
                state = DONE
            else:
                state = WORKING if sub_age <= OFFICE_STALL else STALLED
            if meta.get("finished") is not True and _subagent_stopped(meta, _jsonl_tail(log_path)):
                state = HALTED
            if ((state == DONE and sub_age > OFFICE_DONE_WINDOW) or sub_age > OFFICE_WINDOW
                    or state == HALTED and sub_age > OFFICE_HALTED_WINDOW):
                continue
            sub_detail = _label(meta.get("description")) or "（未命名工作）"
            if state == WORKING:
                doing = _last_action(_jsonl_tail(log_path))
                sub_detail = f"{sub_detail}：{doing}" if doing else sub_detail
            rows.append(OfficeRow("claude subagent", state, sub_detail,
                                  sub_age, 40, group=key, kind="subagent",
                                  evidence="claude 紀錄"))
    for sid, (entry, who) in sorted((live or {}).items()):
        # A session with no recent session file (e.g. a `--bg --resume` stopped
        # at a menu before writing one) is listed only when it needs a person.
        place = _place(entry.get("cwd"), canonical)
        if place is None and entry.get("listed") and "cwd" not in entry:
            place = ("unknown", "")   # the listing names no folder: still a person's to answer
        if sid in seen or place is None:
            continue
        task = f"task {place[1]}" if place[0] == "task" else ""
        state, detail, age = _claude_state(entry, who, task, "", None, now, [])
        if state == ATTENTION:
            rows.append(_claude_row(entry, state, detail + "（還沒有對話紀錄）", age, place,
                                    task, f"claude:{sid}.jsonl", sid, canonical))
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
    ", s.directory "
    "FROM session_v2 s WHERE s.parent_id IS NULL AND s.time_idle IS NOT NULL "
    "ORDER BY s.time_updated DESC LIMIT 50")
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
        lsof = shutil.which("lsof") or "/usr/sbin/lsof"
        out = subprocess.run([lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
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


def opencode_outcome(now: float, db: str | None = None, canonical: str | None = None):
    """(state, detail, age) from the newest finished OpenCode session that worked
    in this repository (canonical checkout or a task root), or None."""
    db = OPENCODE_DB if db is None else db
    canonical = canonical or canonical_root()
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(OPENCODE_OUTCOME_SQL).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    row = next((row for row in rows if _place(row[4], canonical) is not None), None)
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
    return _place_label(place)


def _elapsed_seconds(text: str) -> float | None:
    found = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", text or "")
    if not found:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return float(((days * 24 + hours) * 60 + minutes) * 60 + seconds)


def _forget_cwds(processes: list[Process]) -> None:
    """Keep a process's cwd while it lives (lsof is read once per PID)."""
    alive = {p.pid for p in processes}
    for pid in [pid for pid in _cwd_cache if pid not in alive]:
        _cwd_cache.pop(pid, None)


def opencode_rows(processes: list[Process], now: float, canonical: str,
                  db: str | None = None, cwd_of=process_cwd) -> list[OfficeRow]:
    """One row per running OpenCode CLI/TUI or `run` client."""
    procs = sorted((p for p in processes if p.role in OPENCODE_ROLES), key=lambda p: p.pid)
    if not procs:
        return []
    return _cli_rows(procs, opencode_sessions(now, db), now, canonical, cwd_of,
                     "opencode", "OpenCode CLIENT", 50, "")


KILO_DB = os.path.expanduser("~/.local/share/kilo/kilo.db")
KILO_ROLES = ("Kilo CLIENT", "Kilo CLI")
# Kilo keeps the older OpenCode schema (`session` + `message`, no idle column).
# Only these columns and the newest message's role/completion time are read;
# message content, parts and the account/credential tables are never queried.
KILO_SESSIONS_SQL = (
    "SELECT s.directory, s.title, s.time_updated, "
    "(SELECT json_extract(m.data, '$.role') FROM message m WHERE m.session_id = s.id "
    " ORDER BY m.time_created DESC LIMIT 1), "
    "(SELECT json_extract(m.data, '$.time.completed') FROM message m WHERE m.session_id = s.id "
    " ORDER BY m.time_created DESC LIMIT 1), "
    "(SELECT count(*) FROM message m WHERE m.session_id = s.id) "
    "FROM session s WHERE s.parent_id IS NULL AND s.time_updated >= ? "
    "ORDER BY s.time_updated DESC LIMIT 200")


def kilo_sessions(now: float, db: str | None = None) -> list[tuple]:
    """Same shape as opencode_sessions(): a session is idle once its newest
    message is a completed assistant reply (or it has no message yet)."""
    db = KILO_DB if db is None else db
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(KILO_SESSIONS_SQL, (int((now - OFFICE_WINDOW) * 1000),)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out = []
    for directory, title, updated, role, completed, count in rows:
        if type(directory) is not str or type(updated) is not int:
            continue
        if count == 0:
            idle = updated
        elif role == "assistant" and type(completed) is int:
            idle = completed
        else:
            idle = None
        out.append((directory, title, updated / 1000, idle / 1000 if idle is not None else None))
    return out


def kilo_rows(processes: list[Process], now: float, canonical: str,
              db: str | None = None, cwd_of=process_cwd) -> list[OfficeRow]:
    """One row per running Kilo CLI. Kilo is not a Worker in the dispatch file,
    so its rows say so: an open Kilo is the Owner's own session, not dispatch."""
    procs = sorted((p for p in processes if p.role in KILO_ROLES), key=lambda p: p.pid)
    if not procs:
        return []
    return _cli_rows(procs, kilo_sessions(now, db), now, canonical, cwd_of,
                     "kilo", "Kilo CLIENT", 75, "（未列入派工）")


def _open_cli_rows(clis: list[Process], rows: list[OfficeRow], now: float, canonical: str,
                   cwd_of) -> list[OfficeRow]:
    """An open Codex or Claude CLI that no session row shows still gets a
    row, the one an open OpenCode CLI without a session gets, wherever it
    is. Nothing records which CLI holds which session, so session rows are
    matched to CLIs by count: first in the CLI's own folder, then a session
    working elsewhere in this repository takes a CLI left over in it. Where
    that leaves the pairing a guess, or the CLI's sessions are not read,
    only that it is open is claimed."""
    out = []
    for prefix, order in (("codex", 45), ("claude", 47)):
        shown = [row.task for row in rows
                 if row.name.startswith(prefix) and not row.group and row.kind != "worker"
                 and row.state not in (CLOSED, DONE)]
        idle = []
        for proc in clis:
            place = _place(cwd_of(proc.pid), canonical)
            if family(proc.role).lower() != prefix:
                continue
            task = None if place is None else f"task {place[1]}" if place[0] == "task" else ""
            if task in shown:
                shown.remove(task)
            else:
                idle.append(proc)
        # A Bash `cd` into a D6 moves the session's place, not the CLI's cwd;
        # sessions outside this repository are not read.
        inside = [proc for proc in idle if _place(cwd_of(proc.pid), canonical) is not None]
        taken, guessed = inside[:len(shown)], inside[len(shown):] if shown else []
        idle = [proc for proc in idle if proc not in taken]
        for proc, row in zip(idle, _cli_rows(idle, [], now, canonical, cwd_of, prefix, "", order, "")):
            if proc in guessed or proc not in inside:
                row = replace(row, state=UNCHECKED, detail=f"{_where(cwd_of(proc.pid), canonical)}：已開啟")
            out.append(row)
    return out


def _cli_rows(procs: list[Process], sessions: list[tuple], now: float, canonical: str,
              cwd_of, prefix: str, client_role: str, base: int, note: str) -> list[OfficeRow]:
    def running(item):
        return now - item[2] <= OFFICE_STALL and (item[3] is None or item[2] - item[3] > 2.0)
    cwds = {proc.pid: cwd_of(proc.pid) for proc in procs}
    reals = {pid: os.path.realpath(cwd) if cwd else None for pid, cwd in cwds.items()}
    own = {pid: next((item for item in sessions if real and os.path.realpath(item[0]) == real), None)
           for pid, real in reals.items()}
    # A CLI may work in another folder (e.g. a D6 task worktree) than the one
    # it was opened in. A running session no CLI's folder claims belongs to the
    # one CLI without a running session of its own; with several, none is guessed.
    claimed = {os.path.realpath(item[0]) for item in own.values() if item is not None}
    stray = [item for item in sessions if running(item) and os.path.realpath(item[0]) not in claimed]
    free = [pid for pid, item in own.items() if item is None or not running(item)]
    if stray and len(free) == 1:
        own[free[0]] = stray[0]
    rows = []
    for number, proc in enumerate(procs, 1):
        cwd, latest = cwds[proc.pid], own[proc.pid]
        busy = latest is not None and running(latest)
        where = _where(latest[0] if busy else cwd, canonical)
        name = f"{prefix} {'worker' if proc.role == client_role else 'cli'} #{number}"
        if busy:
            place = _place(latest[0], canonical)
            task = f"task {place[1]}" if place and place[0] == "task" else ""
            # The D6 record's bound agent names the role; none is guessed.
            role = _d6_role(canonical, place[1]) if task else ""
            detail = f"{where}：{_label(latest[1], 28) or '執行中'}{note}"
            rows.append(OfficeRow(name, WORKING, detail, max(0.0, now - latest[2]), base + number,
                                  kind="worker", role=role, task=task,
                                  evidence="session 紀錄"))
        else:
            opened = _elapsed_seconds(proc.elapsed)
            since = f"，已開 {_ago(opened)[:-1]}" if opened is not None and opened >= 60 else ""
            rows.append(OfficeRow(name, IDLE, f"{where}：已開啟，沒有執行中的任務{since}{note}",
                                  opened if opened is not None else 0.0, base + number,
                                  kind="worker",
                                  evidence="程序"))
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
        cwd = cwd_of(proc.pid)
        place = _place(cwd, canonical)
        where = _where(cwd, canonical)
        task = f"task {place[1]}" if place and place[0] == "task" else ""
        role = _d6_role(canonical, place[1]) if task else ""
        cpu = cpu_of(proc.pid)
        opened = _elapsed_seconds(proc.elapsed)
        if cpu is None:
            state, note = UNCHECKED, "CLI 已開啟，無法判斷是否在工作"
        elif cpu >= AGY_BUSY_CPU:
            state, note = WORKING, f"CLI 執行中（依 CPU {cpu:.0f}% 推算）"
        else:
            state, note = IDLE, "CLI 已開啟，閒置（依 CPU 推算）"
        rows.append(OfficeRow(f"agy cli #{number}", state, f"{where}：{note}",
                              opened if opened is not None else 0.0, 60 + number, kind="worker",
                              role=role, task=task, evidence="程序"))
    return rows


# tools/mac-runner installs the runner under its own account; before that it
# ran from the Owner's ~/git/actions-runner.
RUNNER_ROOT = ("/Users/ghrunner/actions-runner" if os.path.isdir("/Users/ghrunner")
               else os.path.expanduser("~/git/actions-runner"))
RUNNER_ACCOUNT = re.compile(r"ghrunner([2-9])?")   # tools/mac-runner pairing


def runner_roots() -> list:
    """Every installed runner: one /Users/ghrunnerN/actions-runner each, else
    the Owner's old ~/git/actions-runner."""
    homes = sorted(glob.glob("/Users/ghrunner*"))
    found = [f"{home}/actions-runner" for home in homes
             if RUNNER_ACCOUNT.fullmatch(os.path.basename(home))]
    return found or [RUNNER_ROOT]


_pid_facts: dict = {}   # (what, pid) -> answer, forgotten when the pid is gone (forget_pids)


def _pid_fact(what: str, pid: int, read):
    """read(pid) once per living pid: its user or isolation cannot change."""
    key = (what, pid)
    if key not in _pid_facts:
        _pid_facts[key] = read(pid)
    return _pid_facts[key]


def forget_pids(processes: list[Process]) -> None:
    alive = {p.pid for p in processes}
    for key in [key for key in _pid_facts if key[1] not in alive]:
        _pid_facts.pop(key, None)


def process_user(pid: int) -> str | None:
    return _pid_fact("user", pid, _read_process_user)


def _read_process_user(pid: int) -> str | None:
    try:
        return subprocess.run(["/bin/ps", "-o", "user=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=2, check=False).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
RUNNER_LOG = re.compile(r"WRITE LINE: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)Z: (.+)")
RUNNER_RESULTS = {"Succeeded": "成功", "Failed": "失敗", "Canceled": "取消"}
RUNNER_LOGS = re.compile(r"Runner_\d{8}-\d{6}-utc\.log")


def process_isolation(pid: int) -> str | None:
    """How a process is kept from this account's data: "專用帳號 <name>" when
    it runs as another account, "沙箱" inside a macOS sandbox, "" when neither;
    None = cannot tell."""
    return _pid_fact("isolation", pid, _read_process_isolation)


def _read_process_isolation(pid: int) -> str | None:
    try:
        out = subprocess.run(["/bin/ps", "-o", "uid=,user=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=2, check=False).stdout.split()
        if len(out) == 2 and int(out[0]) != os.getuid():
            return f"專用帳號 {out[1]}"
        import ctypes
        check = ctypes.CDLL("/usr/lib/libSystem.B.dylib").sandbox_check
        check.argtypes, check.restype = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int), ctypes.c_int
        return {0: "", 1: "沙箱"}.get(check(pid, None, 0))
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return None


def _runner_log(root: str) -> list:
    """The runner's own console lines (time, text), oldest first, from its
    newest _diag/Runner_*.log files (one per start or configuration)."""
    try:
        names = sorted(n for n in os.listdir(os.path.join(root, "_diag")) if RUNNER_LOGS.fullmatch(n))
    except OSError:
        return []
    lines = []
    for name in names[-10:]:
        path = os.path.join(root, "_diag", name)
        # The log folder belongs to the runner account, whose jobs could put a
        # link, FIFO or device there: open without following or blocking,
        # read only a regular file, and at most its last OFFICE_TAIL bytes.
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                continue
            key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            cached = _runner_log_cache.get(path)
            if cached is not None and cached[0] == key:   # unchanged since the last read
                lines += cached[1]
                continue
            os.lseek(fd, max(0, info.st_size - OFFICE_TAIL), os.SEEK_SET)
            text = os.read(fd, OFFICE_TAIL).decode("utf-8", "replace")
        except OSError:
            continue
        finally:
            os.close(fd)
        parsed = []
        for found in RUNNER_LOG.finditer(text):
            when = datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            parsed.append((when.timestamp(), found.group(2).strip()))
        _remember(_runner_log_cache, path, (key, parsed))
        lines += parsed
    return lines


_runner_log_cache: dict = {}   # log path -> (file key, parsed lines)


def runner_rows(processes: list[Process], now: float, root=None,
                cwd_of=process_cwd, isolation=process_isolation,
                ci: list | None = None, user_of=process_user,
                waiting_jobs: list[str] | None = None) -> list[OfficeRow]:
    """One row per self-hosted GitHub Actions runner (Issue #99): running or
    not, kept apart from this account or not, its current or last job,
    restarts and where its logs are. The runners share one GitHub queue, so
    the waiting jobs are one line of their own under the runner rows (W47),
    not a note on runner #1. `root` is the folder (or list of folders) where
    a stopped runner is looked for."""
    roots = [root] if isinstance(root, str) else list(root or [])
    listeners = sorted((p for p in processes if p.role == "Actions runner"), key=lambda p: p.pid)
    found = []   # (root, listener or None)
    for listener in listeners:
        user = user_of(listener.pid) or ""
        home = f"/Users/{user}/actions-runner" if RUNNER_ACCOUNT.fullmatch(user) else None
        place = cwd_of(listener.pid) or home or (roots[0] if len(listeners) == 1 and roots else None)
        if place:
            found.append((place, listener))
    seen = {os.path.realpath(place) for place, _ in found}
    found += [(place, None) for place in roots if os.path.realpath(place) not in seen
              and (os.path.isfile(os.path.join(place, ".runner"))
                   or RUNNER_ACCOUNT.fullmatch(os.path.basename(os.path.dirname(place)) or ""))]
    found.sort(key=lambda item: item[0])
    rows = []
    for number, (place, listener) in enumerate(found, 1):
        rows.append(_runner_row(place, listener, processes, now, isolation, ci or [],
                                number, len(found)))
    waiting = (waiting_jobs if waiting_jobs is not None else
               [run[1] for run in ci or [] if run[0] != "in_progress"])
    if rows and waiting:
        rows.append(ci_queue_row(waiting, len(rows)))
    return rows


def ci_queue_row(waiting: list[str], runners: int, platform: str = "mac") -> OfficeRow:
    """Render one shared platform queue, or an unassigned queue, without folding it."""
    prefix = "linux" if platform == "linux" else "mac"
    name = (f"ci 未分配 排隊" if platform == "unassigned" else
            f"{prefix} ci 兩台共用" if runners == 2 else
            f"{prefix} ci {runners} 台共用" if runners > 2 else f"{prefix} ci 排隊")
    detail = (f"排隊 {len(waiting)} 個：" + "、".join(waiting[:3])
              + ("…" if len(waiting) > 3 else ""))
    group = ("linux ci runner" if platform == "linux" else
             "" if platform == "unassigned" else RUNNER_ROW)
    return OfficeRow(name, "", detail, None, 80 + runners + 1, group=group, kind="shared",
                     evidence="GitHub")


def _runner_name(place: str) -> str:
    found = RUNNER_ACCOUNT.fullmatch(os.path.basename(os.path.dirname(place)))
    if found:
        return "davidproject-self-action" + (f"-{found.group(1)}" if found.group(1) else "")
    try:   # the Owner's old runner folder: its own registration names it
        with open(os.path.join(place, ".runner"), encoding="utf-8-sig") as handle:
            name = str(json.load(handle).get("agentName") or "")
    except (OSError, ValueError, RecursionError, AttributeError):
        return ""
    return name if RUNNER_NAME.fullmatch(name) else ""


RUNNER_ROW = "mac ci runner"   # the Mac runner rows' name; the shared queue line sits under them


def _runner_row(root, listener, processes, now, isolation, ci, number, total):
    lines = _runner_log(root)
    starts = sum(text == "Listening for Jobs" and now - when < 86400 for when, text in lines)
    jobs = [(when, text[len("Running job: "):]) for when, text in lines if text.startswith("Running job: ")]
    ends = [(when, found.group(1), found.group(2)) for when, text in lines
            if (found := re.fullmatch(r"Job (.+) completed with result: (\w+)", text))]
    runner = _runner_name(root)
    notes, alert, ran = [], "", None
    label = "CI runner" + (f" {runner}" if total > 1 and runner else "")
    if listener is None:
        state, age = IDLE, None
        notes.append("已退役（#1253：CI 改用 GitHub 主機）")
        alert = ""
    else:
        age = _elapsed_seconds(listener.elapsed)
        kept = isolation(listener.pid)
        if kept == "":
            hooked = os.path.isfile(os.path.join(root, "isolation", "job-started.sh"))
            alert = (f"{label} 用你的帳號在跑（可能是手動 ./run.sh）："
                     + ("每個 CI 工作都會被拒" if hooked else "CI 工作沒有隔離"))
            notes.append("沒有隔離")
        elif kept is None:
            notes.append("無法確認是否隔離")
        else:
            notes.append(kept)
        worker = next((p for p in processes if p.role == "Actions runner job"
                       and p.ppid == listener.pid), None)
        if worker is not None:   # the job process is the evidence; the log only names it
            state = WORKING
            ran = _elapsed_seconds(worker.elapsed)
            running = [run[1] for run in ci if run[0] == "in_progress"
                       and (run[3] == runner or (total == 1 and run[3] is None))]
            minutes = f"（{max(1, int(ran // 60))} 分鐘）" if ran is not None else ""
            name = running[0] if running else jobs[-1][1] if jobs else ""
            notes.insert(0, f"執行中：{name}{minutes}" if name else f"執行中{minutes}（看不到工作名稱）")
        else:
            state = IDLE
            notes.insert(0, "閒置，等工作")
        if age is not None and age >= 60:
            notes.append(f"已開 {_ago(age)[:-1]}")
        if worker is not None and ran is not None:
            age = ran   # the row's time is this job's, not the runner's uptime
    busy = state == WORKING
    if alert and listener is not None:
        state = ATTENTION   # like 公司 gw: a row with a problem never looks idle
    if ends and not busy:
        when, name, result = ends[-1]
        notes.append(f"上一個工作：{name} {RUNNER_RESULTS.get(result, result)}（{_ago(now - when)}）")
    if starts > 1:
        notes.append(f"近 24 小時啟動 {starts} 次")
    logs, home = os.path.join(root, "_diag"), os.path.expanduser("~")
    if os.access(logs, os.R_OK | os.X_OK):   # a folder the Owner cannot open is no help
        notes.append("日誌：" + ("~" + logs[len(home):] if logs.startswith(home + os.sep) else logs))
    name = RUNNER_ROW + (f" #{number}" if total > 1 else "")
    return OfficeRow(name, state, "；".join(notes), age, 80 + number, key=runner, kind="worker",
                     evidence="程序", alert=alert)


RUNNER_DEFAULT_LABELS = frozenset({"self-hosted", "linux", "macos", "windows", "x64", "arm64", "arm"})


def _ci_queue_groups(view: CiView | None, local=()) -> tuple[list[str], list[str], list[str]]:
    """Assign each already-read queued job once to Mac, Linux, or unassigned."""
    if view is None or not view.queued:
        return [], [], []
    labels_by_name = {name: {label.lower() for label in labels}
                      for name, _system, _status, _busy, labels in view.runners or []}
    mac_names = set(local)
    mac_labels = set().union(*(labels_by_name.get(name, set()) for name in mac_names))
    linux_labels = set().union(*(labels_by_name.get(name, set()) for name, system, *_ in view.runners or []
                                 if name not in mac_names and _runner_system(name, system) == "linux"))
    mac, linux, unassigned = [], [], []
    seen = set()
    for wanted, text in view.queued:
        key = (frozenset(wanted), text)
        if key in seen:
            continue
        seen.add(key)
        fits_mac = bool(wanted and mac_labels and wanted <= mac_labels)
        fits_linux = bool(wanted and linux_labels and wanted <= linux_labels)
        if fits_mac and (not fits_linux or "macos" in wanted and "linux" not in wanted):
            mac.append(text)
        elif fits_linux:
            linux.append(text)
        else:
            unassigned.append(text)
    return mac, linux, unassigned


def _runner_system(name: str, system: str) -> str:
    """The runner's platform word for its row: GitHub's os, except that an
    `ec2-` runner is a Linux one even while its os or labels are incomplete
    (a newly added runner can carry only a pending label)."""
    return "linux" if name.startswith("ec2-") else system.lower()


def github_runner_rows(view: CiView | None, now: float, local=(), ci: list | None = None) -> list[OfficeRow]:
    """One row per self-hosted runner GitHub lists that no row on this Mac
    covers (e.g. a Linux runner elsewhere): GitHub's word alone, so data
    that is old or unreadable says 未確認, never 離線. Pure; no I/O."""
    if view is None:
        return []
    if view.runners is None:
        if not view.error:
            return []
        return [OfficeRow("ci runner（GitHub）", UNCHECKED,
                          f"讀不到 GitHub 的 runner 清單：{view.error}；這台 Mac 以外的 runner 狀態未確認",
                          None, 90, kind="worker", evidence="GitHub",
                          alert=f"GitHub runner 清單讀不到（{view.error}）")]
    rows, old = [], now - view.seen_at
    linux_runners = [(name, system, status, busy, labels)
                     for name, system, status, busy, labels in view.runners or []
                     if name not in local and _runner_system(name, system) == "linux"]
    linux_names = [runner[0] for runner in linux_runners]
    for number, (name, system, status, busy, labels) in enumerate(view.runners, 1):
        if name in local:
            continue
        running = [run for run in ci or [] if run[0] == "in_progress" and run[3] == name]
        alert = ""
        if old > GITHUB_STALE or view.error:
            seen = {"offline": "離線"}.get(status, "執行中" if busy else "閒置" if status == "online" else status)
            state, age = UNCHECKED, old
            notes = [f"未確認：GitHub 資料 {_ago(old)[:-1] if old >= 60 else '不到 1 分鐘'}沒更新"
                     + (f"（{view.error}）" if view.error else "") + f"；上次看到：{seen}"]
        elif status == "offline":
            state, age = STOPPED, None
            notes, alert = ["離線（排給它的 CI 會等它）"], f"CI runner {name} 離線"
        elif status == "online" and busy:
            state = WORKING
            started = running[0][2] if running else None
            age = now - started if started else None
            minutes = f"（已執行 {max(1, int(age // 60))} 分鐘）" if age is not None else ""
            notes = [f"執行中：{running[0][1]}{minutes}" if running else "執行中（看不到工作名稱）"]
        elif status == "online":
            state, age, notes = IDLE, old, ["閒置，等工作"]
        else:
            state, age, notes = UNCHECKED, old, ["GitHub 回報的狀態看不懂"]
        custom = [label for label in labels if label.lower() not in RUNNER_DEFAULT_LABELS]
        notes.append((system or "平台不明") + (f"；標籤 {'、'.join(custom)}" if custom else ""))
        last = (view.finished or {}).get(name)
        if last and state != WORKING:
            notes.append(f"上一個工作：{last[0]} {last[1]}（{_ago(now - last[2])}）")
        kind = _runner_system(name, system)
        rows.append(OfficeRow(f"{kind + ' ' if kind else ''}ci runner {name}", state,
                              "；".join(notes), age, 90 + number, key=name, kind="worker",
                              evidence="GitHub", alert=alert))
    # Queue details belong to the current GitHub snapshot. After a refresh
    # failure, keep the last runner rows marked unconfirmed but don't present
    # an old queue as if it were still waiting.
    if old <= GITHUB_STALE and not view.error:
        _mac_waiting, linux_waiting, unassigned = _ci_queue_groups(view, local)
        if linux_runners and linux_waiting:
            rows.append(ci_queue_row(linux_waiting, len(linux_names), "linux"))
        # Queue rows describe work waiting for a runner in this view. With no
        # non-local runners registered there is no remote queue to attribute;
        # the local Mac row owns its own matching queue.
        if unassigned and linux_runners:
            rows.append(ci_queue_row(unassigned, 0, "unassigned"))
    return rows


def _ci_conclusion_word(conclusion: str | None) -> str:
    """Map conclusion string to Chinese words matching CI_CONCLUSIONS."""
    if not conclusion:
        return "結果不明"
    if conclusion in CI_CONCLUSIONS:
        return CI_CONCLUSIONS[conclusion]
    if conclusion in CI_CONCLUSIONS.values():
        return conclusion
    if conclusion == "結果不明":
        return "結果不明"
    c_lower = str(conclusion).lower()
    return CI_CONCLUSIONS.get(c_lower, "結果不明")


def hosted_ci_rows(ci_runs: list | None, view: CiView | None, now: float) -> list[OfficeRow]:
    """One OfficeRow per queued or in-progress llmtool hosted CI run, or one
    UNCHECKED row when data is missing or stale. Pure; no I/O."""
    if ci_runs is None:
        reason = f"GitHub 資料讀取失敗（{view.error}）" if view and view.error else "GitHub CI 資料尚未讀取"
        return [OfficeRow("hosted ci", UNCHECKED, f"未確認：{reason}", None, 95,
                          kind="worker", evidence="GitHub",
                          alert=f"GitHub CI 資料讀取失敗（{view.error}）" if view and view.error else "")]
    if view is None:
        return [OfficeRow("hosted ci", UNCHECKED, "未確認：沒有 GitHub 資料", None, 95,
                          kind="worker", evidence="GitHub")]
    old = now - view.seen_at
    if old > GITHUB_STALE or view.error:
        if view.error:
            reason = f"GitHub 資料讀取失敗（{view.error}）"
        else:
            reason = f"GitHub 資料 {_ago(old)[:-1] if old >= 60 else '不到 1 分鐘'}沒更新"
        return [OfficeRow("hosted ci", UNCHECKED, f"未確認：{reason}", old, 95,
                          kind="worker", evidence="GitHub",
                          alert=f"GitHub CI 資料未更新（{reason}）" if view.error else "")]

    last_entry = None
    if view.finished:
        items = view.finished.values() if isinstance(view.finished, dict) else view.finished
        valid_items = []
        for item in items:
            if type(item) in (tuple, list) and len(item) >= 2:
                end = item[2] if len(item) > 2 and type(item[2]) in (int, float) else 0.0
                valid_items.append((end, item[0], item[1]))
        if valid_items:
            end_t, last_text, last_result = max(valid_items, key=lambda x: x[0])
            result_word = _ci_conclusion_word(last_result)
            last_entry = (last_text, result_word, end_t if end_t > 0 else None)

    runs: dict[typing.Any, dict] = {}
    for entry in ci_runs:
        if type(entry) in (tuple, list):
            status = str(entry[0] or "")
            text = str(entry[1] or "")
            started = entry[2] if len(entry) > 2 and type(entry[2]) in (int, float) else None
            runner = entry[3] if len(entry) > 3 and type(entry[3]) is str else None
            run_id = entry[4] if len(entry) > 4 else None
        elif isinstance(entry, dict):
            status = str(entry.get("status") or "")
            text = str(entry.get("text") or entry.get("name") or "")
            started = entry.get("started")
            runner = entry.get("runner")
            run_id = entry.get("run_id") or entry.get("id")
        else:
            continue

        if "：" in text:
            base_text, _, job_name = text.partition("：")
        else:
            base_text, job_name = text, ""
        base_text = base_text.strip()
        job_name = job_name.strip()

        key = run_id if run_id is not None else base_text
        if key not in runs:
            if "（" in base_text:
                wf_name = base_text.split("（", 1)[0].strip()
            else:
                wf_name = base_text
            runs[key] = {
                "base_text": base_text,
                "wf_name": wf_name,
                "started": started,
                "in_progress_jobs": [],
                "queued_count": 0,
            }
        run_data = runs[key]
        if started is not None:
            if run_data["started"] is None or started < run_data["started"]:
                run_data["started"] = started

        if status == "in_progress" and job_name:
            if job_name not in run_data["in_progress_jobs"]:
                run_data["in_progress_jobs"].append(job_name)
        elif status in CI_ACTIVE or status in ("queued", "in_progress"):
            run_data["queued_count"] += 1

    if not runs:
        if last_entry:
            last_text, result_word, end_time = last_entry
            age = max(0.0, now - end_time) if end_time else None
            time_str = f"（{_ago(age)}）" if age is not None else ""
            detail = f"閒置，沒有執行中的 CI；上一個工作：{last_text} {result_word}{time_str}"
            return [OfficeRow("hosted ci", IDLE, detail, age, 95, kind="worker", evidence="GitHub")]
        return [OfficeRow("hosted ci", IDLE, "閒置，沒有執行中的 CI", None, 95, kind="worker", evidence="GitHub")]

    rows = []
    for idx, (key, run_data) in enumerate(runs.items(), 1):
        base_text = run_data["base_text"]
        wf_name = run_data["wf_name"]
        in_jobs = run_data["in_progress_jobs"]
        in_count = len(in_jobs)
        queued_count = run_data["queued_count"]

        if view and view.queued:
            extra_q = sum(1 for _, q_text in view.queued if q_text.startswith(base_text))
            queued_count = max(queued_count, extra_q)

        has_running = len(in_jobs) > 0
        started = run_data["started"]
        age = max(0.0, now - started) if (started and math.isfinite(started) and started > 0) else None

        if not has_running:
            state = "排隊"
            notes = [f"排隊 {queued_count} 個" if queued_count > 0 else "排隊中"]
            if age is not None:
                notes.append(f"已排隊 {max(1, int(age // 60))} 分鐘")
            if last_entry:
                last_time = f"（{_ago(max(0.0, now - last_entry[2]))}）" if last_entry[2] else ""
                notes.append(f"上一個工作：{last_entry[0]} {last_entry[1]}{last_time}")
        else:
            state = WORKING
            if in_jobs:
                job_names = "、".join(in_jobs[:3]) + ("…" if len(in_jobs) > 3 else "")
                notes = [f"執行中 {in_count} 個：{job_names}"]
            else:
                notes = [f"執行中 {in_count} 個"]
            if queued_count > 0:
                notes.append(f"排隊 {queued_count} 個")
            if age is not None:
                notes.append(f"已執行 {max(1, int(age // 60))} 分鐘")

        row_name = f"hosted ci {wf_name}" if wf_name else "hosted ci"
        detail = f"{base_text}：{'；'.join(notes)}"
        rows.append(OfficeRow(row_name, state, detail, age, 95 + idx,
                              key=str(key), kind="worker", evidence="GitHub"))

    return rows


LLMTOOL_TASKS = os.path.expanduser("~/.local/state/llmtool/tasks")
LLMTOOL_EVIDENCE_WINDOW = 7 * 86400
PROFILE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
DEVELOPER_RESULTS = {   # llmtool developer state / error code -> plain words
    "DEVELOPER_RESPONDED": "成功回應",
    "DEVELOPER_OUTPUT_INVALID_DIFF": "輸出被拒（diff 格式錯誤）",
    "DEVELOPER_OUTPUT_INVALID_ENVELOPE": "輸出被拒（回應格式錯誤）",
    "DEVELOPER_OUTPUT_REJECTED": "輸出被拒",
    "DEVELOPER_PROVIDER_UNAVAILABLE": "連不上服務",
    "DEVELOPER_UNAVAILABLE": "無法使用",
    "DEVELOPER_OUTCOME_UNKNOWN": "結果不明",
}


_llmtool_records: dict = {}   # record path -> (file key, the few fields the views use)
LLMTOOL_RECORD_FIELDS = ("task_id", "state", "updated_at")
LLMTOOL_RECORD_PARTS = {"developer": ("provider_profile", "state", "error_code", "model", "executor_task_id", "usage", "audit_copy_path_sha256"),
                        "completion": ("executor_task_id", "state"), "pm_assignment": ("route",)}


def _llmtool_record(path: str, st):
    """The identifier, route, state and code fields of one llmtool task record
    (never its request, response, contract or diff), parsed again only when
    the file changed. None when it cannot be read or is not an object."""
    key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    cached = _llmtool_records.get(path)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        with open(path, "rb") as handle:
            record = json.loads(handle.read(1 << 20))
    except (OSError, ValueError, RecursionError):
        record = None
    if type(record) is dict:
        kept = {name: record.get(name) for name in LLMTOOL_RECORD_FIELDS}
        for part, names in LLMTOOL_RECORD_PARTS.items():
            inner = record.get(part)
            kept[part] = ({name: inner.get(name) for name in names} if type(inner) is dict
                          else None if inner is None else "")   # "" = present, not an object
    else:
        kept = None
    _llmtool_records[path] = (key, kept)
    return kept


def _llmtool_paths(root: str) -> list[str]:
    """The record files, with records that disappeared forgotten."""
    paths = glob.glob(os.path.join(root, "*.json"))
    if len(_llmtool_records) > len(paths) + 64:
        for stale in set(_llmtool_records) - set(paths):
            _llmtool_records.pop(stale, None)
    return paths


def llmtool_route_outcomes(now: float, root: str | None = None) -> dict:
    """{developer profile: (age, total, succeeded, latest words)} from llmtool's
    local task records. Only the developer route's profile, state and error
    code are read; requests, responses, prompts and diffs are never touched."""
    root = LLMTOOL_TASKS if root is None else root
    found: dict = {}
    for path in _llmtool_paths(root):
        try:
            st = os.stat(path)
        except OSError:
            continue
        age = max(0.0, now - st.st_mtime)
        if age > LLMTOOL_EVIDENCE_WINDOW:
            continue
        record = _llmtool_record(path, st)
        if record is None:
            continue
        developer = record.get("developer")
        if type(developer) is not dict:
            continue
        profile, state, code = (developer.get("provider_profile"), developer.get("state"),
                                developer.get("error_code"))
        if type(profile) is not str or not PROFILE.fullmatch(profile):
            continue
        if type(state) is not str or not CODE_TEXT.fullmatch(state):
            continue
        code = code if type(code) is str and CODE_TEXT.fullmatch(code) else None
        words = (DEVELOPER_RESULTS.get(code) or DEVELOPER_RESULTS.get(state)
                 or code or state)
        age_, total, ok, latest = found.get(profile, (None, 0, 0, ""))
        newer = age_ is None or age < age_
        found[profile] = (age if newer else age_, total + 1,
                          ok + (state == "DEVELOPER_RESPONDED"), words if newer else latest)
    return found


GW_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")


def gw_task_rows(now: float, root: str | None = None) -> list[OfficeRow]:
    """Live and finished subagent rows for company-gw and company-gw-agent Tasks
    changed within OFFICE_DONE_WINDOW. Only bounded developer route fields
    (profile, state, model, executor_task_id, usage, error_code) and task_id
    are read; request, response, diff, summary, acceptance and audit text are
    never touched."""
    root = LLMTOOL_TASKS if root is None else root
    rows = []
    for path in _llmtool_paths(root):
        try:
            st = os.stat(path)
        except OSError:
            continue
        age = max(0.0, now - st.st_mtime)
        if age > OFFICE_DONE_WINDOW:
            continue
        record = _llmtool_record(path, st)
        if type(record) is not dict:
            continue
        developer = record.get("developer")
        if type(developer) is not dict:
            continue
        profile = developer.get("provider_profile")
        if profile not in ("company-gw", "company-gw-agent"):
            continue
        task_id = record.get("task_id")
        task_id = task_id if type(task_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id) else ""
        if not task_id:
            continue
        state = developer.get("state")
        model = developer.get("model")
        plain_model = model if type(model) is str and GW_MODEL.fullmatch(model) else "不明"
        if state == "DEVELOPER_DISPATCHING":
            if profile != "company-gw":
                continue
            executor_task_id = developer.get("executor_task_id")
            executor_task_id = (executor_task_id if type(executor_task_id) is str and TASK_ID.fullmatch(executor_task_id)
                                else None)
            detail = f'{executor_task_id or "?"} · {plain_model} · 工作中'
            rows.append(OfficeRow(f'home LLM {task_id[:8]}', WORKING, detail, age, 40,
                                  kind='subagent', evidence='llmtool 任務紀錄'))
        else:
            usage = developer.get("usage")
            if type(usage) is dict:
                p = usage.get("prompt_tokens")
                p_text = str(p) if type(p) is int and p >= 0 else "不明"
                c = usage.get("completion_tokens")
                c_text = str(c) if type(c) is int and c >= 0 else "不明"
                t = usage.get("total_tokens")
                t_text = str(t) if type(t) is int and t >= 0 else "不明"
                tokens_text = f"輸入 {p_text} · 輸出 {c_text} · 合計 {t_text} tokens"
            else:
                tokens_text = "用量未記錄"
            detail = f"{plain_model} · 已結束 · {tokens_text}"
            error_code = developer.get("error_code")
            if type(error_code) is str and error_code and CODE_TEXT.fullmatch(error_code):
                detail += f" · {error_code}"
            rows.append(OfficeRow(f'home LLM {task_id[:8]}', DONE, detail, age, 40,
                                  kind='subagent', evidence='llmtool 任務紀錄'))
    rows.sort(key=lambda r: (r.age if r.age is not None else 1e12, r.name))
    return rows


RECORD_STATES = {   # llmtool task / completion state -> plain words
    "PM_RESPONDED": "PM 已規劃，還沒派給 developer",
    "PM_UNAVAILABLE": "PM 無法使用",
    "WAITING_EXECUTOR": "等執行者接手",
    "WAITING_PM": "等 PM",
    "PM_EVIDENCE_READY": "結果已備妥，等 PM 讀取",
}
LLMTOOL_RECORD_WINDOW = 12 * 3600


def llmtool_task_records(now: float, root: str | None = None) -> list[tuple[float, str]]:
    """[(updated, line)] newest first for llmtool task records updated in the
    last 12 hours. Only ids, routes, states and error codes are read; requests,
    responses, contracts and diffs are never touched."""
    root = LLMTOOL_TASKS if root is None else root
    found = []
    for path in _llmtool_paths(root):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if now - st.st_mtime > LLMTOOL_RECORD_WINDOW:
            continue
        record = _llmtool_record(path, st)
        if type(record) is not dict:
            continue
        updated = _github_time(record.get("updated_at")) or st.st_mtime
        if now - updated > LLMTOOL_RECORD_WINDOW:
            continue
        developer = record.get("developer") if type(record.get("developer")) is dict else {}
        completion = record.get("completion") if type(record.get("completion")) is dict else {}
        assignment = record.get("pm_assignment") if type(record.get("pm_assignment")) is dict else {}

        def code(value):
            return value if type(value) is str and CODE_TEXT.fullmatch(value) else None

        name = completion.get("executor_task_id")
        name = name if type(name) is str and TASK_ID.fullmatch(name) else (
            str(record.get("task_id") or "")[:8] if re.fullmatch(r"[0-9a-f]{8,64}",
                                                             str(record.get("task_id") or "")) else "?")
        route = next((value for value in (developer.get("provider_profile"), assignment.get("route"))
                      if type(value) is str and PROFILE.fullmatch(value)), "")
        dev_state, dev_code = code(developer.get("state")), code(developer.get("error_code"))
        if dev_state:
            result = DEVELOPER_RESULTS.get(dev_code) or DEVELOPER_RESULTS.get(dev_state) or dev_state
            result += f"（{dev_code}）" if dev_code and dev_code not in DEVELOPER_RESULTS else ""
        else:
            top = code(record.get("state"))
            result = RECORD_STATES.get(top, top or "狀態不明")
        after = code(completion.get("state"))
        if dev_state and after:
            result += f"；{RECORD_STATES.get(after, after)}"
        when = time.strftime("%m-%d %H:%M", time.localtime(updated))
        # Without a developer record the route is only the PM's plan.
        via = (("派給 " if dev_state else "PM 建議 ") + route) if route else "未指定路線"
        found.append((updated, f"{when}  {name:<36} {via:<18} {result}"))
    return sorted(found, reverse=True)


REMOTE_TEST_RESULTS = frozenset({"PASS", "FAIL", "TIMEOUT", "SHA_MISMATCH"})
REMOTE_TEST_OUTCOMES = frozenset({"ADMISSION_DENIED", "UNAVAILABLE", "TIMEOUT", "UNKNOWN"})
REMOTE_TEST_FILES = 32        # newest task files read per office rebuild
REMOTE_TEST_READ = 4 << 20    # a Task holds up to 32 receipts of up to 64 result lines
REMOTE_TEST_SKEW = 300.0      # receipts carry the EC2 clock; file times the local one
REMOTE_TEST_RELIST = 60.0     # seconds a task-file listing is reused while the folder is unchanged
REMOTE_TEST_SCAN = 4096       # folder entries looked at per listing, at most
REMOTE_TEST_BUDGET = 4 << 20  # bytes of task files read per office rebuild, in total
EC2_HEAD_BUDGET = 4 << 20
REMOTE_TEST_CAPPED = "task 目錄超過上限，結果可能不是最新"
REMOTE_TEST_OVER_BUDGET = "task 紀錄超過讀取上限，結果可能不是最新"
_remote_test_listing: dict = {}   # root -> (folder mtime_ns, listed at, newest paths, capped)
_remote_test_parsed: dict = {}    # root -> {path: ((st_ino, st_mtime_ns, st_size), entries)}
_ec2_parsed: dict = {}
STUCK_REVIEW = 3600
STUCK_MAC = 2700
STUCK_BUSY_WINDOW = 1800
STUCK_REPAIR_CAP = 2
STUCK_BUDGET = 4 << 20
_stuck_parsed: dict = {}


@dataclass(frozen=True)
class StuckRecord:
    task: str
    d6: str
    repair: bool
    busy_at: float


def _stuck_entry(record) -> StuckRecord | None:
    """Extract only the bounded, non-free-text fields needed by stuck alerts."""
    if type(record) is not dict:
        return None
    task = record.get("task_id")
    if type(task) is not str or re.fullmatch(r"[0-9a-f]{32}", task) is None:
        return None
    request = record.get("request")
    developer = record.get("developer")
    if type(developer) is not dict:
        developer = {}
    state = developer.get("state")
    d6 = developer.get("executor_task_id")
    d6 = d6 if type(d6) is str else ""
    repair = (type(request) is str and request.startswith("Repair round")
              and type(state) is str and state in DISPATCH_ACCEPTED)
    events = []
    receipts = record.get("remote_test_receipts")
    outcomes = record.get("remote_test_outcomes")
    for item in receipts if type(receipts) is list else []:
        if type(item) is dict:
            when = _remote_test_time(item.get("ended_at"))
            if when > 0:
                events.append((when, False))
    for item in outcomes if type(outcomes) is list else []:
        if type(item) is dict:
            when = _remote_test_time(item.get("at"))
            if when > 0:
                events.append((when, item.get("code") == "REMOTE_BUSY"))
    latest = max(events, default=(0.0, False))
    busy_at = latest[0] if latest[1] else 0.0
    if not d6 and not busy_at:
        return None
    return StuckRecord(task[:8], d6, repair, busy_at)


def stuck_records(root: str | None = None) -> tuple[list[StuckRecord], str]:
    """Read bounded cached Task fields used by stuck alerts."""
    root = LLMTOOL_TASKS if root is None else root
    return _bounded_task_entries(root, _stuck_parsed, STUCK_BUDGET, _stuck_entry)


def stuck_reviews(office, details) -> list[tuple[str, str, float]]:
    found = []
    running = _d6_running_tasks(office if type(office) is list else [])
    for task_id in running:
        match = _review_task(task_id)
        if match and task_id in details:
            found.append((match[0], match[1], details[task_id][2]))
    return sorted(found)


def stuck_open_d6(details, prs) -> set[str]:
    return {task_id for task_id, value in details.items()
            if value[4] and prs.get(value[4], (None, ""))[1] == "OPEN"}


def stuck_alerts(now, ci, reviews, records, open_d6) -> list[str]:
    alerts = []
    for pr, head, since in sorted(reviews or []):
        if type(since) not in (int, float) or since <= 0:
            continue
        age = now - since
        if age > STUCK_REVIEW:
            alerts.append(f"審查超過 1 小時：PR #{pr} @{head}（已 {int(age // 60)} 分鐘）")
    runs = {}
    if ci is not None:
        for index, entry in enumerate(ci):
            if type(entry) not in (tuple, list) or len(entry) != 5:
                continue
            status, text, started, runner, run_id = entry
            if (type(text) is not str or not text.startswith("llmtool Mac CI")
                    or type(started) not in (int, float) or not math.isfinite(started)
                    or started <= 0):
                continue
            key = ("id", run_id) if type(run_id) is int else ("entry", index)
            previous = runs.get(key)
            if previous is None:
                runs[key] = [started, status == "in_progress", text]
            else:
                oldest = previous[0]
                previous[0] = min(previous[0], started)
                previous[1] = previous[1] or status == "in_progress"
                if started < oldest:
                    previous[2] = text
        mac = []
        for started, active, text in runs.values():
            age = now - started
            if age > STUCK_MAC:
                mac.append((started, f"macOS 超過 45 分鐘：{text}（{'執行中' if active else '排隊'}，已 {int(age // 60)} 分鐘）"))
        alerts.extend(line for _, line in sorted(mac))
    busy = []
    if records is not None:
        for record in records:
            if not isinstance(record, StuckRecord) or record.busy_at <= 0:
                continue
            age = now - record.busy_at
            if 0 <= age <= STUCK_BUSY_WINDOW:
                busy.append((record.busy_at, record.task,
                             f"EC2 忙碌：task {record.task}（REMOTE_BUSY，{int(age // 60)} 分鐘前）"))
        alerts.extend(item[2] for item in sorted(busy, key=lambda item: (-item[0], item[1]))[:3])
    repairs = {}
    if records is not None:
        allowed = open_d6 or set()
        for record in records:
            if isinstance(record, StuckRecord) and record.repair and record.d6 in allowed:
                repairs[record.d6] = repairs.get(record.d6, 0) + 1
    # Only the newest Task files are read, so this count is a lower bound.
    limited = sorted((d6, count) for d6, count in repairs.items()
                     if count >= STUCK_REPAIR_CAP)
    alerts.extend(f"修復已到上限：D6 {d6}（至少 {count} 輪）" for d6, count in limited[:5])
    if len(limited) > 5:
        alerts.append(f"另有 {len(limited) - 5} 件到達修復上限")
    return alerts


def _remote_test_paths(root: str) -> tuple[list, bool]:
    """(up to REMOTE_TEST_FILES newest *.json paths in root, whether the listing
    stopped at REMOTE_TEST_SCAN entries). The folder is listed again only when
    its mtime changes (TaskStore writes by rename, which changes it) or after
    REMOTE_TEST_RELIST seconds; otherwise only the kept paths are looked at again."""
    try:
        folder = os.stat(root).st_mtime_ns
    except OSError:
        return [], False
    kept = _remote_test_listing.get(root)
    if kept and kept[0] == folder and time.monotonic() - kept[1] < REMOTE_TEST_RELIST:
        return kept[2], kept[3]
    stamped, seen, capped = [], 0, False
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if seen >= REMOTE_TEST_SCAN:
                    capped = True   # more entries remain; the newest may be among them
                    break
                seen += 1
                if not entry.name.endswith(".json"):
                    continue
                try:   # never follow a link: it could point at the remote-test config
                    info = entry.stat(follow_symlinks=False)
                    if not entry.is_symlink() and stat.S_ISREG(info.st_mode):
                        stamped.append((info.st_mtime, entry.path))
                except OSError:
                    continue
    except OSError:
        return [], False
    paths = [path for _, path in heapq.nlargest(REMOTE_TEST_FILES, stamped)]
    if len(_remote_test_listing) >= 8:
        _remote_test_listing.clear()   # one root in use; tests pass many
    _remote_test_listing[root] = (folder, time.monotonic(), paths, capped)
    return paths, capped


def _remote_test_open(path: str, budget: int):
    """(file key, raw bytes) of one regular task file, opened without following
    a link or blocking on a FIFO and checked again on the open descriptor, so a
    file swapped after the listing is refused too. A hard link (TaskStore
    writes by rename, so its files have one) could name the remote-test config
    and is refused. (key, None) for a refused or oversized file, (key, False)
    when it does not fit the remaining budget, None when unreadable."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        key = (info.st_ino, info.st_mtime_ns, info.st_size)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > REMOTE_TEST_READ:
            return key, None
        if info.st_size > budget:
            return key, False
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return key, handle.read(info.st_size)
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _remote_test_json(raw: bytes):
    """Parsed JSON, or None when malformed or nested too deeply."""
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None


def _remote_test_time(value) -> float:
    if type(value) is not str:
        return 0.0
    return _github_time(re.sub(r"\.\d{6}Z$", "Z", value))


DISPATCH_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z$")


def _dispatch_time(value) -> float:
    if type(value) is not str or not DISPATCH_TIMESTAMP.fullmatch(value):
        return 0.0
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
        return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


CODEX_WORK_BUDGET = 4 << 20
DISPATCH_BUDGET = 4 << 20
CODEX_WORK_BEFORE = 5.0
CODEX_WORK_AFTER = 300.0
_codex_work_parsed: dict = {}  # root -> {path: ((st_ino, st_mtime_ns, st_size), entries)}
_dispatch_parsed: dict = {}  # root -> {path: ((st_ino, st_mtime_ns, st_size), entries)}
CODEX_WORK_ISSUE = re.compile(r"^(?:Repair round: )*#([1-9][0-9]{0,6})\b")
DISPATCH_ACCEPTED = frozenset({"DEVELOPER_DISPATCHING", "DEVELOPER_RESPONDED",
                               "DEVELOPER_OUTCOME_UNKNOWN", "DEVELOPER_OUTPUT_REJECTED"})
# A refused dispatch can still leave a GitHub marker, so markers do not count.
DISPATCH_PROVIDERS = {"codex-cli": "Codex", "opencode-cli": "OpenCode",
                      "agy-cli": "AGY", "openai-compatible": "home LLM gw",
                      "gw-agent-cli": "home LLM gw-agent"}


@dataclass(frozen=True)
class DispatchRecord:
    created: float
    provider: str
    issue: str
    task: str = ""   # Owner #120 B2: the dispatched Task


def _dispatch_entry(record) -> DispatchRecord | None:
    """Extract only fields that prove an admitted Developer dispatch."""
    if type(record) is not dict:
        return None
    task = record.get("task_id")
    developer = record.get("developer")
    state = developer.get("state") if type(developer) is dict else None
    if (type(task) is not str or not re.fullmatch(r"[0-9a-f]{32}", task)
            or type(developer) is not dict
            or type(state) is not str or state not in DISPATCH_ACCEPTED):
        return None
    created = _remote_test_time(developer.get("created_at"))
    if created <= 0:
        return None
    provider = developer.get("provider_type")
    provider = provider if type(provider) is str else ""
    request = record.get("request")
    found = CODEX_WORK_ISSUE.match(request) if type(request) is str else None
    return DispatchRecord(created, provider, found.group(1) if found else "", task)


def _task_load(path: str, budget: int):
    """(file key, whole parsed record, bytes read), or None when it was not read."""
    opened = _remote_test_open(path, budget)
    if opened is None or opened[1] is False:
        return None
    key, raw = opened
    return key, _remote_test_json(raw) if raw else None, len(raw or b"")


def _bounded_task_entries(root: str, cache: dict, budget: int, extract):
    """Read bounded, unchanged-cached Task records through a field extractor."""
    paths, capped = _remote_test_paths(root)
    stamped = []
    for path in paths:
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            stamped.append((info.st_mtime, path,
                            (info.st_ino, info.st_mtime_ns, info.st_size), info.st_nlink))
    short, kept, entries = False, {}, []
    parsed = cache.get(root, {})
    for _, path, key, link_count in sorted(stamped, reverse=True)[:REMOTE_TEST_FILES]:
        if link_count != 1:
            continue
        if key[2] > REMOTE_TEST_READ:
            short = True
            continue
        cached = parsed.get(path)
        if cached and cached[0] == key:
            record_entries = cached[1]
        else:
            loaded = _task_load(path, budget)
            if loaded is None:
                short = True
                continue
            key, record, used = loaded
            budget -= used
            entry = extract(record)
            record_entries = (tuple(entry) if type(entry) in (tuple, list)
                              else (entry,) if entry is not None else ())
        kept[path] = (key, record_entries)
        entries.extend(record_entries)
    if root not in cache and len(cache) >= 8:
        cache.clear()
    cache[root] = kept
    problem = REMOTE_TEST_CAPPED if capped else REMOTE_TEST_OVER_BUDGET if short else ""
    return entries, problem


def _ec2_head_entry(record) -> tuple:
    """Extract valid SHA-bound remote-test results from one Task."""
    if type(record) is not dict:
        return ()
    found = []
    receipts = record.get("remote_test_receipts")
    outcomes = record.get("remote_test_outcomes")
    for item in receipts if type(receipts) is list else []:
        result = item.get("result") if type(item) is dict else None
        sha = item.get("source_sha") if type(item) is dict else None
        when = _remote_test_time(item.get("ended_at")) if type(item) is dict else 0
        if (type(result) is str and result in REMOTE_TEST_RESULTS
                and type(sha) is str and HEX_SHA.fullmatch(sha) and when > 0):
            found.append((sha[:7].lower(), when, result))
    for item in outcomes if type(outcomes) is list else []:
        result = item.get("result") if type(item) is dict else None
        sha = item.get("sha") if type(item) is dict else None
        when = _remote_test_time(item.get("at")) if type(item) is dict else 0
        if (type(result) is str and result in REMOTE_TEST_OUTCOMES
                and type(sha) is str and HEX_SHA.fullmatch(sha) and when > 0):
            found.append((sha[:7].lower(), when, result))
    return tuple(found)


def ec2_head_results(root: str | None = None) -> tuple[dict, str]:
    """Newest local remote-test result per head SHA, read with bounded caching."""
    root = LLMTOOL_TASKS if root is None else root
    entries, problem = _bounded_task_entries(root, _ec2_parsed,
                                             EC2_HEAD_BUDGET, _ec2_head_entry)
    if not problem:
        paths, _ = _remote_test_paths(root)
        if len(paths) >= REMOTE_TEST_FILES:
            problem = "task 僅讀最近 32 筆，未見結果未確認"
    latest = {}
    for sha, when, word in entries:
        if when > latest.get(sha, (0, ""))[0]:
            latest[sha] = (when, word)
    return ({sha: word for sha, (_, word) in latest.items()}, problem)


_dispatch_listed: dict = {}  # root -> (folder mtime_ns, monotonic time, listing)


def _dispatch_listing(root: str) -> list:
    """[(file key, path)] of the regular *.json files in root, newest first,
    reused while the folder is unchanged for at most REMOTE_TEST_RELIST seconds."""
    folder = os.stat(root).st_mtime_ns
    kept = _dispatch_listed.get(root)
    if kept and kept[0] == folder and time.monotonic() - kept[1] < REMOTE_TEST_RELIST:
        return kept[2]
    stamped = []
    with os.scandir(root) as entries:
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue   # removed since the folder was listed
            if entry.name.endswith(".json") and stat.S_ISREG(info.st_mode):
                stamped.append(((info.st_ino, info.st_mtime_ns, info.st_size), entry.path))
    listing = sorted(stamped, key=lambda item: item[0][1], reverse=True)
    if root not in _dispatch_listed and len(_dispatch_listed) >= 8:
        _dispatch_listed.clear()
    _dispatch_listed[root] = (folder, time.monotonic(), listing)
    return listing


def pm_last_dispatch(now: float, root: str | None = None) -> tuple[DispatchRecord | None, str]:
    """Return the newest locally recorded admitted Developer dispatch.

    Owner #120 B2: every Task file counts, not only the newest 32, so review
    Tasks cannot push a dispatch out. Files are read newest first, at most
    DISPATCH_BUDGET bytes per call, and unchanged files come from the cache.
    Reading stops at a file written before the newest dispatch, once that
    dispatch's own file time confirms the order. problem is "" when certain."""
    root = LLMTOOL_TASKS if root is None else root
    try:
        stamped = _dispatch_listing(root)
    except FileNotFoundError:
        return None, ""
    except OSError:
        return None, "本機 Task 紀錄讀取失敗"
    newest, ordered, problem, budget = None, False, "", DISPATCH_BUDGET
    cached, kept = _dispatch_parsed.get(root, {}), {}
    for key, path in stamped:
        if ordered and key[1] / 1e9 < newest.created - REMOTE_TEST_SKEW:
            break   # written before the newest dispatch, so it holds no newer one
        if path in cached and cached[path][0] == key:
            entry = cached[path][1]
        else:
            opened = _remote_test_open(path, budget)
            if opened is None or opened[1] is False:
                problem = "本機 Task 紀錄讀取失敗" if opened is None else REMOTE_TEST_OVER_BUDGET
                break
            key, raw = opened
            budget -= len(raw or b"")
            entry = _dispatch_entry(_remote_test_json(raw) if raw else None)
        kept[path] = (key, entry)
        if (entry is not None and entry.created <= now + REMOTE_TEST_SKEW
                and (newest is None or entry.created > newest.created)):
            newest, ordered = entry, entry.created <= key[1] / 1e9 + REMOTE_TEST_SKEW
    if root not in _dispatch_parsed and len(_dispatch_parsed) >= 8:
        _dispatch_parsed.clear()
    _dispatch_parsed[root] = kept
    return newest, problem


def pm_dispatch_line(entry: DispatchRecord | None, problem: str, now: float) -> str:
    if problem:
        line = f"⚠ PM 上次派工：未確認（{problem}）"
        if entry is not None:
            line += f"；已讀到的最近一次：{_ago(max(0.0, now - entry.created))}"
        return line
    if entry is None:
        return "⚠ PM 上次派工：本機紀錄沒有派工"
    age = max(0.0, now - entry.created)
    line = f"PM 上次派工：{_ago(age)}"
    if age > 30 * 60:
        line = f"⚠ {line}；超過 30 分鐘沒有派工"
    provider = DISPATCH_PROVIDERS.get(entry.provider, "Developer")
    line += f"（→ {provider}"
    if entry.issue:
        line += f"，#{entry.issue}"
    return line + (f"，Task {entry.task[:8]}" if entry.task else "") + "）"


def _codex_work_entry(record) -> CodexWork | None:
    """Extract only the explicitly approved local Task fields for Codex binding."""
    if type(record) is not dict:
        return None
    task = record.get("task_id")
    developer = record.get("developer")
    if (type(task) is not str or not re.fullmatch(r"[0-9a-f]{32}", task)
            or type(developer) is not dict
            or developer.get("provider_type") != "codex-cli"
            or type(developer.get("executor_task_id")) is not str):
        return None
    state = developer.get("state")
    state = state if type(state) is str else ""
    created = _remote_test_time(developer.get("created_at"))
    execution = developer.get("execution")
    session = execution.get("native_session_id") if type(execution) is dict else ""
    session = session if type(session) is str else ""
    request = record.get("request")
    found = CODEX_WORK_ISSUE.match(request) if type(request) is str else None
    issue = found.group(1) if found else ""
    response = record.get("response")
    try:
        parsed = _remote_test_json(response.encode("utf-8")) if type(response) is str else None
    except (UnicodeError, ValueError, RecursionError):
        parsed = None
    try:
        summary = (_label(parsed.get("summary"), 40)
                   if type(parsed) is dict and type(parsed.get("summary")) is str else "")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        summary = ""
    return CodexWork(task, developer["executor_task_id"], session, state, created,
                     issue, summary)


def codex_work_records(root: str | None = None) -> tuple[list[CodexWork], str]:
    """Read bounded, unchanged-cached Codex Task records from the local Task store."""
    root = LLMTOOL_TASKS if root is None else root
    return _bounded_task_entries(root, _codex_work_parsed,
                                 CODEX_WORK_BUDGET, _codex_work_entry)


def _codex_work_match(entries: list[CodexWork], d6: str, session: str,
                      started: float, exec_run: bool) -> tuple[CodexWork, bool] | None:
    exact = [entry for entry in entries if entry.d6 == d6 and session
             and entry.session == session]
    exact_tasks = {entry.task: entry for entry in exact}
    if len(exact_tasks) == 1:
        return next(iter(exact_tasks.values())), True
    if len(exact_tasks) > 1 or not exec_run:
        return None
    running = [entry for entry in entries if entry.d6 == d6
               and entry.state == "DEVELOPER_DISPATCHING" and not entry.session
               and entry.created > 0
               and entry.created - CODEX_WORK_BEFORE <= started <= entry.created + CODEX_WORK_AFTER]
    tasks = {entry.task: entry for entry in running}
    return (next(iter(tasks.values())), False) if len(tasks) == 1 else None


def _codex_work_label(entry: CodexWork, pr: int | None) -> str:
    parts = ([f"#{entry.issue}"] if entry.issue else [])
    if pr is not None:
        parts.append(f"PR #{pr}")
    parts.extend((f"task {entry.task[:8]}", f"D6 {entry.d6}"))
    label = " · ".join(parts)
    return label + (f"：{entry.summary}" if entry.summary else "")


def _remote_test_entries(record) -> tuple:
    """((when, words), ...) for each well-typed receipt and outcome of a record.
    Only result words, codes, the Task id, the SHA and the test count are kept."""
    if type(record) is not dict:
        return ()
    task = record.get("task_id")
    task = task[:8] if type(task) is str and re.fullmatch(r"[0-9a-f]{8,64}", task) else "未知"
    found = []
    receipts, outcomes = record.get("remote_test_receipts"), record.get("remote_test_outcomes")
    for item in receipts if type(receipts) is list else []:
        result = item.get("result") if type(item) is dict else None
        if type(result) is not str or result not in REMOTE_TEST_RESULTS:
            continue
        sha, ran = item.get("source_sha"), item.get("ran_total")
        words = f"task {task} {result}"
        actions = item.get("actions")
        if type(actions) is dict:
            run_id = actions.get("run_id")
            if type(run_id) is int and not isinstance(run_id, bool) and 1 <= run_id <= (1 << 53):
                words += f" GitHub-hosted run {run_id}"
        words += f" @{sha[:7]}" if type(sha) is str and HEX_SHA.fullmatch(sha) else ""
        words += f"，跑了 {ran} 個測試" if type(ran) is int and ran >= 0 else ""
        found.append((_remote_test_time(item.get("ended_at")), words))
    for item in outcomes if type(outcomes) is list else []:
        result = item.get("result") if type(item) is dict else None
        if type(result) is not str or result not in REMOTE_TEST_OUTCOMES:
            continue
        sha, code = item.get("sha"), item.get("code")
        words = f"task {task} {result}"
        words += f"（{code}）" if type(code) is str and CODE_TEXT.fullmatch(code) else ""
        words += f" @{sha[:7]}" if type(sha) is str and HEX_SHA.fullmatch(sha) else ""
        found.append((_remote_test_time(item.get("at")), words))
    dispatches = record.get("remote_test_dispatches")
    for item in dispatches if type(dispatches) is list else []:
        if type(item) is not dict:
            continue
        state = item.get("state")
        if state not in ("DISPATCHED", "RUN_FOUND"):
            continue
        sha = item.get("sha")
        if type(sha) is not str or not HEX_SHA.fullmatch(sha):
            continue
        dispatched_at = item.get("dispatched_at")
        when = _dispatch_time(dispatched_at)
        if when <= 0:
            continue
        run_id = item.get("run_id")
        if run_id is None:
            run_word = "待定"
        elif type(run_id) is int and not isinstance(run_id, bool) and 1 <= run_id <= (1 << 53):
            run_word = str(run_id)
        else:
            continue
        words = f"task {task} hosted {state} run {run_word} @{sha[:7]}"
        found.append((when, words))
    return tuple(entry for entry in found if entry[0])


def remote_test_latest(root: str | None = None) -> tuple[tuple[float, str] | None, str]:
    """((when, words) or None, problem): the newest EC2 remote-test receipt or
    receipt-less outcome in llmtool's local task records. problem is "" or why
    a newer one may exist unseen (the listing or the read budget stopped early).
    The remote config, host, paths and logs are never touched. At most
    REMOTE_TEST_FILES files are looked at, newest first, stopping once a file is
    older than the newest entry found; an unchanged file (same inode, mtime and
    size) is not read again, and at most REMOTE_TEST_BUDGET bytes are read."""
    root = LLMTOOL_TASKS if root is None else root
    stamped = []
    listed, capped = _remote_test_paths(root)
    for path in listed:
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            stamped.append((info.st_mtime, path, (info.st_ino, info.st_mtime_ns, info.st_size)))
    best, budget, short, kept = None, REMOTE_TEST_BUDGET, False, {}
    parsed = _remote_test_parsed.get(root, {})
    for mtime, path, key in sorted(stamped, reverse=True)[:REMOTE_TEST_FILES]:
        if best is not None and mtime < best[0] - REMOTE_TEST_SKEW:
            break   # any entry in an older file was written before it
        cached = parsed.get(path)
        if cached and cached[0] == key:
            entries = cached[1]
        else:
            opened = _remote_test_open(path, budget)
            if opened is None:
                continue
            key, raw = opened
            if raw is False:
                short = True   # read the rest on a later rebuild
                continue
            budget -= len(raw or b"")
            entries = _remote_test_entries(_remote_test_json(raw)) if raw else ()
        kept[path] = (key, entries)
        for entry in entries:
            if best is None or entry[0] > best[0]:
                best = entry
    # Keep only files looked at this time, so the cache is no larger than the listing.
    if root not in _remote_test_parsed and len(_remote_test_parsed) >= 8:
        _remote_test_parsed.clear()   # one root in use; tests pass many
    _remote_test_parsed[root] = kept
    return best, REMOTE_TEST_CAPPED if capped else REMOTE_TEST_OVER_BUDGET if short else ""


def _remote_test_runs(processes: list[Process]) -> list:
    """[(process, modules)] for each live `llmtool task remote-test` CLI. An ssh
    counts only through a CLI in its parent chain (it may name the modules the
    CLI argv left out); any other ssh of the same shape is not this worker."""
    by_pid = {p.pid: p for p in processes}
    clis = {p.pid: p for p in processes if p.job and p.job[0] == "cli"}
    modules = {pid: p.job[3] for pid, p in clis.items()}
    for p in processes:
        if not p.job or p.job[0] != "ssh":
            continue
        parent, seen = by_pid.get(p.ppid), 0
        while parent is not None and parent.pid not in clis and seen < MAX_ANCESTRY:
            parent, seen = by_pid.get(parent.ppid), seen + 1
        if parent is not None and parent.pid in clis and not modules[parent.pid]:
            if clis[parent.pid].job[2] in ("", p.job[2]):
                modules[parent.pid] = p.job[3]
    return [(p, modules[pid]) for pid, p in sorted(clis.items())]


def remote_test_row(processes: list[Process], now: float, root: str | None = None) -> OfficeRow:
    """The EC2 remote-test worker: the run the process table shows, else the
    newest local record. It says only what the process or record says."""
    runs = _remote_test_runs(processes)
    if runs:
        (proc, modules), (_, task, sha, _) = runs[0], runs[0][0].job
        detail = f"執行中：task {task or '未知'}" + (f" @{sha}" if sha else "")
        detail += f"；{', '.join(modules)}" if modules else ""
        if len(runs) > 1:
            detail += f"；另有 {len(runs) - 1} 個執行中"
        return OfficeRow("EC2 遠端測試", WORKING, detail, _elapsed_seconds(proc.elapsed),
                         80, kind="worker", evidence="程序")
    latest, problem = remote_test_latest(root)
    if problem:   # only part of the records was read: never claim the newest
        detail = (f"已讀到的一次：{latest[1]}；" if latest else "") + problem
        return OfficeRow("EC2 遠端測試", UNCHECKED, detail,
                         max(0.0, now - latest[0]) if latest else None, 80, kind="worker",
                         evidence="llmtool 紀錄")
    if latest is None:
        return OfficeRow("EC2 遠端測試", IDLE, "尚無遠端測試紀錄", None, 80, kind="worker",
                         evidence="llmtool 紀錄")
    if re.search(r"\bhosted (?:DISPATCHED|RUN_FOUND)\b", latest[1]):
        return OfficeRow("EC2 遠端測試", WORKING, f"GitHub-hosted 執行中：{latest[1]}",
                         max(0.0, now - latest[0]), 80, kind="worker", evidence="llmtool 紀錄")
    return OfficeRow("EC2 遠端測試", IDLE, f"最近一次：{latest[1]}", max(0.0, now - latest[0]),
                     80, kind="worker", evidence="llmtool 紀錄")


# Company LLM runs (#763 Owner): while a gw-agent Developer run is under way it
# gets a live row like the Claude and Codex rows, then a 完成 row for
# OFFICE_DONE_WINDOW; otherwise only its status row stays. No Task file is
# opened for these rows: it holds the prompt and model output. A run is live
# only on its gateway process. Its Task id comes from the authorization.json
# beside its audit file (ids, scope digest, egress basis; no prompt, no
# credential), and a finished run from the time of the audit copy llmtool keeps
# (developer_routes/gw_agent.py, collect). A company gw run is an API call
# inside llmtool and leaves neither, so it has no row here.
HOME_GW = "home LLM gw"               # roster name of the retired company-gw route (#120 A3)
HOME_GW_AGENT = "home LLM gw-agent"   # roster name of the gw-agent route (#120 A3)
GW_ROSTER = {"gw-agent developer": HOME_GW_AGENT}
GW_AUDIT_ROOT = None   # None: the temp directory, resolved in _gw_finished (it may not exist)
GW_AUDIT_PREFIX = "llmtool-gw-agent-audit-"
GW_AUDIT_ID = re.compile(r"[A-Za-z0-9_]{1,16}")   # the random end of a mkdtemp name
GW_AUTH_MAX = 64 << 10


def _gw_auth_task(audit: str) -> str:
    """The Task id in the authorization.json llmtool wrote beside a running
    gateway's audit file, else ""."""
    if not audit:
        return ""
    opened = _remote_test_open(os.path.join(os.path.dirname(audit), "authorization.json"),
                               GW_AUTH_MAX)
    record = _remote_test_json(opened[1]) if opened and opened[1] else None
    task = record.get("task_id") if type(record) is dict else None
    return task if type(task) is str and DISPATCH_TASK_ID.fullmatch(task) else ""


def _gw_finished(now: float) -> list[tuple[float, str]]:
    """[(age, id)], newest first, of gw-agent runs that ended within
    OFFICE_DONE_WINDOW. llmtool copies the audit file when a run ends, so the
    copy's time is the end, and its folder's random name tells runs apart. Only
    file times are read, and only the user's own plain files count, as in
    cleanup_audit_copies (developer_routes/gw_agent.py). Without a temp
    directory there are no finished runs to show."""
    try:
        root = tempfile.gettempdir() if GW_AUDIT_ROOT is None else GW_AUDIT_ROOT
        with os.scandir(root) as entries:
            names = [entry.name for entry in entries if entry.name.startswith(GW_AUDIT_PREFIX)]
    except OSError:
        return []
    found = []
    for name in names:
        run = name[len(GW_AUDIT_PREFIX):]
        folder = os.path.join(root, name)
        try:
            folder_info, info = os.lstat(folder), os.lstat(os.path.join(folder, "audit.jsonl"))
        except OSError:
            continue
        age = now - info.st_mtime
        if (GW_AUDIT_ID.fullmatch(run) and stat.S_ISDIR(folder_info.st_mode)
                and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and folder_info.st_uid == info.st_uid == os.getuid()
                and -REMOTE_TEST_SKEW <= age <= OFFICE_DONE_WINDOW):
            found.append((max(0.0, age), run))
    return sorted(found)


def _gw_activity(audit: str, now: float, elapsed: float | None) -> float:
    """Seconds since the gateway last wrote its audit file: only the file's
    time is read, never its content. Without it, the process age."""
    try:
        if audit:
            return max(0.0, now - os.lstat(audit).st_mtime)
    except (OSError, ValueError):
        pass
    return elapsed or 0.0


def _gw_row(state: str, words: str, age: float, d6: str, registered: dict, task: str,
            sid: str, evidence: str) -> OfficeRow:
    """A registered D6 is named by the PR binding (_bind_d6_pr_rows) like other
    D6 rows; any other place is named here."""
    parts = [f"task {task[:8]}"] if task else ["未綁定 Task"]
    if d6 not in registered:
        parts.append(f"D6 {d6}（未登記）" if d6 else "未綁定 D6")
    return OfficeRow("gw-agent developer", state, " · ".join(parts + [words]), age, 10,
                     role="developer", task=f"task {d6}" if d6 in registered else "",
                     evidence=evidence, sid=sid)


def _gw_task_for_run(run: str) -> tuple[str, str]:
    """(task id, D6) of the llmtool Task whose gw-agent audit copy is this run's file,
    matched by the SHA-256 of the path only; ("", "") when none."""
    base = tempfile.gettempdir() if GW_AUDIT_ROOT is None else GW_AUDIT_ROOT
    path = os.path.join(base, GW_AUDIT_PREFIX + run, "audit.jsonl")
    digest = hashlib.sha256(os.fsencode(path)).hexdigest()
    for _key, record in list(_llmtool_records.values()):
        developer = record.get("developer") if type(record) is dict else None
        if type(developer) is dict and developer.get("audit_copy_path_sha256") == digest:
            return str(record.get("task_id") or ""), str(developer.get("executor_task_id") or "")
    return "", ""


def gw_rows(processes: list[Process], now: float, canonical: str) -> list[OfficeRow]:
    """Live and recently finished gw-agent Developer runs."""
    registered = registered_tasks(canonical)
    rows = []
    for proc in sorted(processes, key=lambda p: p.pid):
        if proc.role != GW_AGENT_ROLE or len(proc.gw) != 3:
            continue
        workdir = os.path.realpath(proc.gw[1]) if proc.gw[1] else ""
        d6 = next((task_id for task_id, (place, _) in registered.items() if place == workdir), "")
        if not d6 and AGENT_PATH.fullmatch(os.path.basename(workdir)):
            d6 = os.path.basename(workdir)
        age = _gw_activity(proc.gw[2], now, _elapsed_seconds(proc.elapsed))
        rows.append(_gw_row(WORKING, "執行中", age, d6, registered, _gw_auth_task(proc.gw[2]),
                            f"pid {proc.pid}", "程序"))
    for age, run in _gw_finished(now):
        task, d6 = _gw_task_for_run(run)
        rows.append(_gw_row(DONE, "已結束", age, d6, registered, task, run, "llmtool 稽核檔"))
    return rows


# Codex reviewers are not a standing Worker: they run as sessions the PM starts
# and appear under it. The `codex-reviewer` dispatch switch (Owner 2026-10-25)
# gates them but gets no roster row of its own.
WORKER_ROSTER = (   # (display name, worker-dispatch.json key, sort order)
    ("opencode developer", "opencode-developer", 50),
    ("agy", "agy", 60),
    (HOME_GW, "company-gw", 70),
    (HOME_GW_AGENT, "company-gw-agent", 75),
)
# #1502 WP-B: AI Debug switch rows; shown only when the dispatch file has the keys.
DEBUG_ROSTER = (("codex debug", "codex-debug", 80), ("claude debug", "claude-debug", 85))
# Owner dispatch switches (Owner 2026-10-23): one record per Worker, kept apart
# from live technical state. A PAUSED switch holds until the Owner changes it,
# whatever quota, login or app state is observed. Without a readable, valid
# switch the monitor says it cannot tell; it never falls back to other text.
DISPATCH_RELATIVE = os.path.join("tools", "llmtool", "worker-dispatch.json")
DISPATCH_REFS = ("origin/main", "main")   # local refs only; the monitor never fetches
DISPATCH_VALUES = frozenset({"ALLOWED", "PAUSED", "OWNER_ASSIGNMENT_ONLY"})
# The llmtool gate (tools/llmtool/llmtool/worker_dispatch.py, _parse_policy and
# _read_policy_file) is all or nothing: one invalid entry refuses every
# Worker. The monitor applies the same rules so it never shows a switch the
# gate would refuse; a test checks both agree.
DISPATCH_WORKERS = frozenset({"codex-developer", "claude-developer", "opencode-developer", "agy",
                              "company-gw", "company-gw-agent", "claude-reviewer",
                              "codex-reviewer"})
# #1502 WP-B: the AI Debug keys are optional, as in the gate: a file without
# them is valid, a file with only one of them is not.
DISPATCH_DEBUG_WORKERS = frozenset({"codex-debug", "claude-debug"})
# The gw Agent route (#120) accepts exactly one of two entries, as the gate
# does: PAUSED by the PM with the bare issue as evidence (WP3b), or ALLOWED by
# the Owner with exactly the 2026-11-10 standing authorization comment (#120).
# Anything else for this key fails closed. Every other key still needs Owner.
DISPATCH_EXACT_ENTRIES = {"company-gw-agent": frozenset({
    ("PAUSED", "PM", "https://github.com/davidcatcat/davidcattoolsproject/issues/120"),
    ("ALLOWED", "Owner",
     "https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6760670167"),
})}
DISPATCH_FIELDS = (frozenset({"dispatch", "decided_by", "decided_at", "reason", "evidence"}),
                   frozenset({"dispatch", "decided_by", "decided_at", "reason", "evidence",
                              "owner_assignments"}))
DISPATCH_MAX = 64 * 1024
_REPO_URL = r"https://github\.com/davidcatcat/davidcattoolsproject/"
DISPATCH_EVIDENCE = re.compile(_REPO_URL + r"(?:issues/289|pull/283)(?:#issuecomment-[1-9][0-9]*)?")
DISPATCH_ASSIGNMENT_REF = re.compile(_REPO_URL + r"issues/289#issuecomment-[1-9][0-9]*")
DISPATCH_TASK_ID = re.compile(r"[0-9a-f]{32}")
DISPATCH_TTL = 30.0
_dispatch_cache: dict = {}


DISPATCH_REFUSED = b""   # a file the gate's reader refuses outright


def _read_dispatch_file(path: str) -> bytes:
    """The policy file as the gate's _read_policy_file accepts it: a regular,
    single-link, non-symlink file of a sane size that did not change while
    read. Never blocks (a FIFO is refused). FileNotFoundError passes up; any
    other refusal returns DISPATCH_REFUSED."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError:
        return DISPATCH_REFUSED
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                not 2 <= before.st_size <= DISPATCH_MAX):
            return DISPATCH_REFUSED
        raw = os.read(fd, DISPATCH_MAX + 1)
        after = os.fstat(fd)
    except OSError:
        return DISPATCH_REFUSED
    finally:
        os.close(fd)
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                             info.st_ctime_ns, info.st_nlink)
    return raw if identity(before) == identity(after) and len(raw) == before.st_size \
        else DISPATCH_REFUSED


def load_dispatch(canonical: str, path: str | None = None) -> tuple[bytes | None, str]:
    """(file bytes or None, where they came from). The checkout's file wins,
    read the way the gate reads it; only when it does not exist at all, the
    newest local main ref that has it (possibly behind GitHub)."""
    try:
        return _read_dispatch_file(path or os.path.join(canonical, DISPATCH_RELATIVE)), "派工檔"
    except FileNotFoundError:
        pass
    if path is not None:
        return None, ""
    cached = _dispatch_cache.get(canonical)
    if cached is not None and time.monotonic() - cached[0] < DISPATCH_TTL:
        return cached[1]
    found: tuple[bytes | None, str] = (None, "")
    for ref in DISPATCH_REFS:
        try:
            result = subprocess.run(["git", "-C", canonical, "cat-file", "blob",
                                     f"{ref}:tools/llmtool/worker-dispatch.json"],
                                    capture_output=True, timeout=2, check=False)
        except (OSError, subprocess.SubprocessError):
            break
        if result.returncode == 0 and len(result.stdout) <= 256 << 10:
            found = (result.stdout, f"本機 {ref} 的派工檔（可能不是最新）")
            break
    _dispatch_cache[canonical] = (time.monotonic(), found)
    return found


def _unique_keys(pairs):
    found = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key")
        found[key] = value
    return found


def _dispatch_entry_ok(key, item) -> bool:
    if type(item) is not dict or frozenset(item) not in DISPATCH_FIELDS:
        return False
    assignments = item.get("owner_assignments", [])
    reason, evidence, when = item.get("reason"), item.get("evidence"), item.get("decided_at")
    exact = DISPATCH_EXACT_ENTRIES.get(key)
    if exact is not None:
        authority_ok = (type(item.get("dispatch")) is str and type(item.get("decided_by")) is str and
                        type(evidence) is str and
                        (item["dispatch"], item["decided_by"], evidence) in exact)
    else:
        authority_ok = (item.get("decided_by") == "Owner" and type(evidence) is str and
                        DISPATCH_EVIDENCE.fullmatch(evidence) is not None)
    if (not authority_ok or type(item.get("dispatch")) is not str or
            item.get("dispatch") not in DISPATCH_VALUES or
            type(when) is not str or type(reason) is not str or not reason or len(reason) > 512 or
            type(assignments) is not list):
        return False
    seen = set()
    for assignment in assignments:
        if (type(assignment) is not dict or set(assignment) != {"task_id", "owner_assignment_ref"} or
                type(assignment["task_id"]) is not str or
                DISPATCH_TASK_ID.fullmatch(assignment["task_id"]) is None or
                type(assignment["owner_assignment_ref"]) is not str or
                DISPATCH_ASSIGNMENT_REF.fullmatch(assignment["owner_assignment_ref"]) is None):
            return False
        seen.add((assignment["task_id"], assignment["owner_assignment_ref"]))
    if len(seen) != len(assignments):
        return False
    try:
        return date.fromisoformat(when).isoformat() == when
    except ValueError:
        return False


def worker_dispatch(raw: bytes | None) -> tuple[dict, str]:
    """({worker key: (dispatch, decided_by, MM-DD)}, problem). problem is "" when
    the llmtool gate would accept the file, else why every Worker is refused."""
    if raw is None:
        return {}, "missing"
    if raw == DISPATCH_REFUSED:
        return {}, "rejected"
    try:
        if not 2 <= len(raw) <= DISPATCH_MAX:
            raise ValueError("size")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return {}, "invalid"
    workers = data.get("workers") if type(data) is dict else None
    try:
        ok = (type(data) is dict and set(data) == {"schema_version", "workers"} and
              type(data["schema_version"]) is int and data["schema_version"] == 1 and
              type(workers) is dict and
              set(workers) in (DISPATCH_WORKERS, DISPATCH_WORKERS | DISPATCH_DEBUG_WORKERS) and
              all(_dispatch_entry_ok(key, item) for key, item in workers.items()))
    except (TypeError, ValueError):   # e.g. an unhashable value where a name belongs
        ok = False
    if not ok:
        return {}, "rejected"
    return {key: (item["dispatch"], item["decided_by"], item["decided_at"][5:])
            for key, item in workers.items()}, ""


DISPATCH_PROBLEMS = {
    "missing": "讀不到派工檔，無法判斷能否派工",
    "invalid": "派工檔格式錯誤：llmtool 會拒絕所有派工",
    "rejected": "派工檔有不合規的地方：llmtool 會拒絕所有派工",
}


def track_one_shot(state: dict, processes: list[Process], now: float,
                   sessions: str | None = None):
    """Remember short `claude -p` runs started by Codex after they exit.

    They leave no session file and often last under a minute, so without this
    a run is visible only while it happens to be running. Returns the new
    state, [(duration_s, seconds_since_end)] for runs that ended within the
    done window, and one log line per run that just ended. Memory only."""
    state = {pid: list(item) for pid, item in state.items()}
    live = {p.pid: p for p in _one_shot_runs(processes, sessions or claude_sessions())}
    for pid, proc in live.items():
        if pid not in state or state[pid][2] is not None:   # new, or a reused PID
            state[pid] = [now - (_elapsed_seconds(proc.elapsed) or 0.0), now, None]
        state[pid][1] = now
    ended = []
    for pid, item in state.items():
        if pid not in live and item[2] is None:
            item[2] = now
            ended.append(f"claude 單次呼叫（由 Codex 啟動）結束（約 {max(1, int(item[1] - item[0]))} 秒）")
    state = {pid: item for pid, item in state.items()
             if item[2] is None or now - item[2] <= OFFICE_DONE_WINDOW}
    finished = [(item[1] - item[0], now - item[2]) for item in state.values() if item[2] is not None]
    return state, finished, ended


# The time label a Codex row ends with; only this carries over to a closed row.
TURN_LABEL = re.compile(r"（(?:對話 \d\d-\d\d \d\d:\d\d 開始|這輪 \d\d:\d\d 起|"
                        r"上一輪(?: \d\d:\d\d)? ?(?:結束|失敗|被中斷))）$")


def _closed_codex_cli(rows: list[OfficeRow], clis: int) -> list[OfficeRow]:
    """A thread opened from the Codex CLI keeps its record open in the
    app-server daemon after the CLI exits or moves on to a new thread. With N
    CLIs running, the N most recently active CLI threads are taken as the
    attached ones; any other shows as closed for a while, then leaves with
    what it started."""
    tui = sorted((row for row in rows if row.tui),
                 key=lambda row: row.age if row.age is not None else 1e12)
    attached = {row.key for row in tui[:clis]}
    gone, out = set(), []
    for row in rows:
        if not row.tui or row.key in attached:
            out.append(row)
        elif row.age is not None and row.age > OFFICE_DONE_WINDOW:
            gone.add(row.key)
        else:
            found = TURN_LABEL.search(row.detail)
            out.append(replace(row, state=CLOSED,
                               detail="推測 Codex CLI 已關閉" + (found.group(0) if found else "")))
    return [row for row in out if row.group not in gone]


CLAUDE_CLI_ROLES = ("Claude Code CLI", "Claude Code CLI (node launcher)")
AGENT_FAMILIES = ("Claude", "Codex", "OpenCode", "Kilo")
# Roles of an open agent CLI (also behind its npm launcher): each is one member.
AGENT_CLI_ROLES = CLAUDE_CLI_ROLES[:1] + ("Claude Code background session", "Codex CLI",
                                         "AGY CLI") + OPENCODE_ROLES + KILO_ROLES


def agent_clis(processes: list[Process], sessions=None) -> list[Process]:
    """Open agent CLIs, by PID. An npm launcher and the CLI it started are one;
    a background session counts once a session has moved into it."""
    procs = [p for p in processes if p.role.removesuffix(" (node launcher)") in AGENT_CLI_ROLES]
    launched = {p.owner for p in procs}
    registered = _claude_registered(claude_sessions() if sessions is None else sessions, procs) or {}
    return sorted((p for p in procs if f"{p.role} {p.pid}" not in launched
                   and (p.role != "Claude Code background session" or p.pid in registered)),
                  key=lambda p: p.pid)


def _claude_registered(sessions, processes: list[Process]) -> dict[int, dict] | None:
    """Registry entry of each running Claude CLI that names its session in the
    CLI's own <pid>.json file. sessions: one registry directory or a list, one
    per account. None when there is no registry to read."""
    names, readable = [], False
    for directory in [sessions] if isinstance(sessions, str) else sessions:
        try:
            names += [(directory, name) for name in os.listdir(directory)]
        except OSError:
            continue
        readable = True
    if not readable:
        return None
    pids = {p.pid for p in processes
            if p.role in CLAUDE_CLI_ROLES + ("Claude Code background session",)}
    found = {}
    for directory, name in names:
        pid = name[:-len(".json")]
        if not name.endswith(".json") or not pid.isdigit() or int(pid) not in pids:
            continue
        path = os.path.join(directory, name)
        key = _file_key(path)
        cached = _cli_registry_cache.get(path)
        if key is not None and cached is not None and cached[0] == key:
            written, entry = cached[1], cached[2]
        else:
            try:
                written = os.path.getmtime(path)
                with open(path, "rb") as handle:
                    entry = json.loads(handle.read(64 << 10))
            except (OSError, ValueError, RecursionError):
                continue
            if key is not None:
                _remember(_cli_registry_cache, path, (key, written, entry))
        if type(entry) is dict and entry.get("pid") == int(pid) and type(entry.get("sessionId")) is str:
            # A crashed CLI of another account can leave a file for a PID the
            # OS has since reused: the running CLI keeps its own file fresh.
            if int(pid) not in found or written > found[int(pid)][0]:
                found[int(pid)] = (written, dict(entry, jobState=_job_state(directory, entry)))
    return {pid: entry for pid, (_, entry) in found.items()}


_cli_registry_cache: dict = {}   # <pid>.json path -> (file key, mtime, entry)


def _job_state(sessions: str, entry: dict) -> str | None:
    """What `claude agents` reports for a background session (working, blocked,
    done, ...): the `state` word of its job record next to the registry. Only
    that word is kept; the record also holds the prompt and environment."""
    job = entry.get("jobId")
    if type(job) is not str or not AGENT_PATH.fullmatch(job):
        return None
    try:
        with open(os.path.join(os.path.dirname(sessions), "jobs", job, "state.json"), "rb") as handle:
            record = json.loads(handle.read(64 << 10))
    except (OSError, ValueError, RecursionError):
        return None
    state = record.get("state") if type(record) is dict else None
    return state if type(state) is str else None


# `claude agents --json` per account (#364): a blocked listing needs running
# process evidence before it can represent a session waiting for a person. The
# listing is asked at most every AGENTS_EVERY
# seconds per monitor, off the UI thread, with a short timeout; a failing or
# slow call leaves no rows. Only the session id, process id, state words, cwd
# and launcher name are kept: never the prompt, transcript, argv or environment.
AGENTS_EVERY = 60.0
AGENTS_TIMEOUT = 5.0
AGENTS_OUTPUT = 1 << 20
AGENTS_WAITING = frozenset({"blocked", "waiting"})
SESSION_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{7,63}")


def claude_config_dirs(root=None) -> list[str]:
    """Each local Claude account's config directory (CLAUDE_CONFIG_DIR)."""
    return [os.path.dirname(top) for top in claude_roots(root)]


def parse_claude_agents(text) -> dict[str, dict]:
    """{session id: minimal registry-like entry} of the sessions a
    `claude agents --json` listing reports as blocked or waiting for a person.
    Anything else (busy, working, done, idle, malformed) is left out."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return {}
    if type(data) is dict:
        data = next((data[key] for key in ("agents", "sessions", "jobs")
                     if type(data.get(key)) is list), [])
    if type(data) is not list:
        return {}
    found = {}
    for item in data[:500]:
        if type(item) is not dict:
            continue
        sid = next((item[key] for key in ("sessionId", "session_id", "id")
                    if type(item.get(key)) is str), None)
        if sid is None or not SESSION_ID.fullmatch(sid):
            continue
        words = {item.get(key) for key in ("state", "status", "jobState")}
        words = {word for word in words if type(word) is str}
        if not words & AGENTS_WAITING:
            continue
        entry = {"sessionId": sid, "kind": "bg", "listed": True}
        if "waiting" in words:
            entry["status"] = "waiting"
        else:
            entry["status"] = "busy"
        if "blocked" in words:
            entry["jobState"] = "blocked"
        pid = item.get("pid")
        if type(pid) is int and pid > 0:
            entry["listedPid"] = pid
        cwd = item.get("cwd")
        if type(cwd) is str and cwd.startswith(os.sep) and len(cwd) <= 4096:
            entry["cwd"] = cwd
        name = item.get("name")
        if type(name) is str and SESSION_NAME.fullmatch(name):
            entry["name"] = name
        found[sid] = entry
    return found


def _run_claude_agents(claude: str, config_dir: str, timeout: float) -> str | None:
    env = dict(os.environ)
    if os.path.realpath(config_dir) == os.path.realpath(os.path.dirname(CLAUDE_PROJECTS)):
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    try:
        child = subprocess.Popen([claude, "agents", "--json"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    # Read with a size cap and a deadline: a noisy or stuck CLI is killed as
    # soon as it passes AGENTS_OUTPUT bytes or the timeout, never buffered whole.
    chunks, size, ok = [], 0, False
    deadline = time.monotonic() + timeout
    try:
        fd = child.stdout.fileno()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                ok = True
                break
            size += len(chunk)
            if size > AGENTS_OUTPUT:
                break
            chunks.append(chunk)
        if ok:
            left = max(0.0, deadline - time.monotonic())
            ok = child.wait(timeout=left) == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        ok = False
    finally:
        if child.poll() is None:
            try:
                child.kill()
            except OSError:
                pass
        try:
            child.wait(timeout=1.0)
        except subprocess.SubprocessError:
            pass
        try:
            child.stdout.close()
        except OSError:
            pass
    if not ok:
        return None
    return b"".join(chunks).decode("utf-8", "replace")


class ClaudeAgents:
    """The last `claude agents --json` answer of each account, refreshed at most
    every `every` seconds in a background thread (inline when background is
    False). runner(config_dir) returns the listing text or None."""

    def __init__(self, runner=None, clock=time.monotonic, background: bool = True,
                 every: float = AGENTS_EVERY, timeout: float = AGENTS_TIMEOUT) -> None:
        self.runner, self.clock, self.background = runner, clock, background
        self.every, self.timeout = every, timeout
        self.found: dict[str, dict] = {}
        self.due = 0.0
        self.thread: threading.Thread | None = None

    def _default_runner(self, config_dir: str) -> str | None:
        claude = shutil.which("claude")
        return None if claude is None else _run_claude_agents(claude, config_dir, self.timeout)

    def _refresh(self, config_dirs: list[str]) -> None:
        runner = self.runner or self._default_runner
        found = {}
        for config_dir in config_dirs:
            # An account that never ran a background session has nothing to list.
            if not any(os.path.isdir(os.path.join(config_dir, name)) for name in ("jobs", "sessions")):
                continue
            try:
                found.update(parse_claude_agents(runner(config_dir)))
            except Exception:   # one account's failure never hides another's or the office
                continue
        self.found = found

    def sessions(self, config_dirs: list[str]) -> dict[str, dict]:
        now = self.clock()
        busy = self.thread is not None and self.thread.is_alive()
        if now >= self.due and not busy:
            self.due = now + self.every
            dirs = list(config_dirs)
            if self.background:
                self.thread = threading.Thread(target=self._refresh, args=(dirs,), daemon=True)
                self.thread.start()
            else:
                self._refresh(dirs)
        return dict(self.found)


def _claude_live_sessions(sessions, processes: list[Process]) -> tuple[set, int] | None:
    """Session ids of running Claude CLIs, from the CLI's own <pid>.json
    registry, plus how many running CLIs have no readable entry. None when
    there is no registry to read, so nothing is judged closed. An unnamed
    background session is a spare holding no session yet, so it keeps none."""
    found = _claude_registered(sessions, processes)
    if found is None:
        return None
    pids = {p.pid for p in processes if p.role in CLAUDE_CLI_ROLES}
    return {entry["sessionId"] for entry in found.values()}, len(pids - set(found))


def _one_shot_runs(processes: list[Process], sessions) -> list[Process]:
    """Claude CLIs started by Codex that keep no session: an interactive
    session Codex drives registers itself and is shown from its own record."""
    registered = _claude_registered(sessions, processes) or {}
    return [p for p in processes if p.role == "Claude Code CLI"
            and str(p.owner or "").startswith("Codex") and p.pid not in registered]


def _closed_claude_cli(rows: list[OfficeRow], sessions,
                       processes: list[Process], keep=frozenset()) -> list[OfficeRow]:
    """An interactive CLI session whose process has exited still leaves its
    record behind. Each running CLI names its session in the registry; a CLI
    without a readable entry keeps the most recently active unnamed session.
    Any other CLI session shows as closed for a while, then leaves with its
    subagents. A blocked listing keeps a transcript only when running process
    evidence ties it to a live session. keep: keys of live blocked sessions."""
    found = _claude_live_sessions(sessions, processes)
    if found is None:
        return rows
    live, unnamed = found
    keys = {f"claude:{sid}.jsonl" for sid in live} | set(keep)
    rest = sorted((row for row in rows if row.cli and row.key not in keys),
                  key=lambda row: row.age if row.age is not None else 1e12)
    keys |= {row.key for row in rest[:unnamed]}
    gone, out = set(), []
    for row in rows:
        if not row.cli or row.key in keys:
            out.append(row)
        elif row.age is not None and row.age > OFFICE_DONE_WINDOW:
            gone.add(row.key)
        else:
            detail = row.detail
            if row.state == ATTENTION:
                before_launcher, separator, launcher = detail.partition("｜")
                before_launcher = before_launcher.replace("等人回覆", "先前等人回覆")
                before_launcher = before_launcher.replace("等人確認權限", "先前等人確認權限")
                before_launcher = before_launcher.replace("等人操作", "先前等人操作")
                before_launcher = re.sub(r"，要.*?回", "", before_launcher)
                detail = before_launcher + (separator + launcher if separator else "")
            out.append(replace(row, state=CLOSED,
                               detail=f"推測 Claude CLI 已關閉（{detail}）"))
    return [row for row in out if row.group not in gone]


def _d6_process_rows(processes: list[Process], rows: list[OfficeRow], canonical: str,
                    cwd_of=process_cwd, now: float | None = None) -> list[OfficeRow]:
    """Show a live agent CLI in its registered D6 task even without a transcript."""
    now = time.time() if now is None else now
    covered = {}
    for row in rows:
        if row.task.startswith("task ") and row.state != CLOSED:
            covered.setdefault(row.task[5:], []).append(row)
    seen = set()
    out = []
    for proc in sorted(processes, key=lambda item: item.pid):
        base_role = proc.role.removesuffix(" (node launcher)")
        if proc.role in CLAUDE_CLI_ROLES or base_role == "Claude Code CLI":
            family = "claude"
        elif proc.role in OPENCODE_ROLES or base_role in OPENCODE_ROLES:
            family = "opencode"
        elif base_role == "Codex CLI":
            family = "codex"
        else:
            continue
        cwd = cwd_of(proc.pid)
        place = _place(cwd, canonical)
        if not place or place[0] != "task" or place[1] in seen:
            continue
        if any(not (family == "claude" and row.state == DONE and row.key.startswith("claude:"))
               for row in covered.get(place[1], ())):
            # At this revision a DONE claude: session row is a one-shot, and only a Claude process can outlive its inactivity-based DONE state.
            continue
        registered = registered_tasks(canonical).get(place[1])
        if not registered or os.path.realpath(cwd) != registered[0]:
            continue
        seen.add(place[1])
        # A Codex CLI in a registered D6 is its Developer unless the record
        # binds a Reviewer; other families claim no role the record does not name.
        role = (_codex_d6_role(canonical, place[1], "developer")
                if family == "codex" else _d6_role(canonical, place[1]))
        age = _elapsed_seconds(proc.elapsed)
        out.append(OfficeRow(f"{family} {role}".rstrip(), WORKING, f"task {place[1]}（程序 cwd）",
                             age if age is not None else 0.0, 10,
                             role=role, task=f"task {place[1]}", evidence="程序",
                             codex_exec=proc.codex_exec if family == "codex" else False,
                             codex_started=(now - age if family == "codex" and age is not None else 0.0)))
    return out


def _codex_work_rows(rows: list[OfficeRow], now: float, canonical: str,
                     tasks_root: str, prs: dict | None) -> list[OfficeRow]:
    targets = [row for row in rows if
               (row.key.startswith("codex:") and row.kind == "agent")
               or row.name == "codex 單次呼叫"
               or (row.evidence == "程序" and row.name.startswith("codex "))]
    if not targets:
        return rows
    target_ids = {id(row) for row in targets}
    try:
        entries, problem = codex_work_records(tasks_root)
        registered = registry_details(canonical)
    except Exception:
        entries, problem, registered = [], REMOTE_TEST_OVER_BUDGET, {}
    matches = {}
    fallback_counts = {}
    for row in targets:
        task_id = row.task[5:] if row.task.startswith("task ") else ""
        if task_id not in registered or row.role in ("reviewer", "debug reviewer"):
            continue
        match = _codex_work_match(entries, task_id, row.codex_session,
                                  row.codex_started, row.codex_exec)
        if match:
            matches[id(row)] = match
            entry, exact = match
            if not exact:
                key = (task_id, entry.task)
                fallback_counts[key] = fallback_counts.get(key, 0) + 1
    out = []
    for row in rows:
        if id(row) not in target_ids:
            out.append(row)
            continue
        task_id = row.task[5:] if row.task.startswith("task ") else ""
        bound = False
        if task_id in registered:
            review = _review_task(task_id) if row.role == "reviewer" else None
            if review:
                work = f"審查 PR #{review[0]} @{review[1]}"
                bound = True
            elif row.role == "debug reviewer" and CODEX_HIGH_DEBUG_TASK.search(task_id):
                name = re.sub(r"-hd[0-9]+-[0-9a-fA-F]{7}$", "", task_id)
                work = f"診斷 D6 {name} @{task_id[-7:]}"
                bound = True
            else:
                match = matches.get(id(row))
                if match and (match[1] or fallback_counts.get((task_id, match[0].task), 0) == 1):
                    entry = match[0]
                    branch = registered[task_id][4]
                    pr = prs.get(branch, (None, ""))[0] if prs is not None and branch else None
                    work = _codex_work_label(entry, pr)
                    bound = True
                else:
                    work = (f"工作紀錄未讀完，未確認（D6 {task_id}）" if problem
                            else f"未綁定工作（D6 {task_id}）")
        else:
            work = "未綁定工作"
        out.append(replace(row, work=work, work_bound=bound))
    return out


def _bind_d6_pr_rows(rows: list[OfficeRow], canonical: str,
                     prs: dict | None) -> list[OfficeRow]:
    """Attach the already-read branch-to-PR binding to live non-Codex D6 rows.
    Codex rows keep the label _codex_work_rows gave them, bound or not."""
    try:
        registered = registry_details(canonical)
    except Exception:
        registered = {}
    out = []
    for row in rows:
        task_id = row.task[5:] if row.task.startswith("task ") else ""
        details = registered.get(task_id)
        if (not task_id or row.role in ("reviewer", "pm", "debug reviewer")
                or _codex_family(row) or row.work or row.work_bound or not details):
            out.append(row)
            continue
        branch = details[4]
        pr = prs.get(branch, (None, "OPEN"))[0] if prs is not None and branch else None
        if pr is None and prs is not None and branch:
            # #1592: the PR list was read and has no PR for this branch yet.
            out.append(replace(row, work=f"尚未開 PR（D6 {task_id}）", work_bound=False))
        elif pr is None:
            out.append(replace(row, work=f"PR 未確認（D6 {task_id}）", work_bound=False))
        else:
            out.append(replace(row, work=f"PR #{pr} · D6 {task_id}", work_bound=True))
    return out


def _live_rank(entry: dict) -> int:
    """How urgent a live session's status words are: 0 waits for a person,
    1 works, 2 sits idle, 3 anything else."""
    status, job = entry.get("status"), entry.get("jobState")
    if status == "waiting" or job == "blocked":
        return 0
    if status == "busy" or job == "working":
        return 1
    return 2 if status == "idle" else 3


def _live_by_session(registered: dict | None) -> dict[str, dict]:
    """{session id: entry} of running CLIs. When several CLIs name one session
    the most urgent wins (waiting/blocked > busy/working > idle > other), then
    the newest statusUpdatedAt, then the higher PID; `clis` counts them."""
    groups: dict[str, list] = {}
    for pid, entry in (registered or {}).items():
        groups.setdefault(entry["sessionId"], []).append((pid, entry))

    def order(item):
        pid, entry = item
        since = entry.get("statusUpdatedAt")
        since = since if type(since) in (int, float) else float("-inf")
        return (_live_rank(entry), -since, -pid)

    out = {}
    for sid, items in groups.items():
        pid, entry = min(items, key=order)
        out[sid] = dict(entry, pid=pid, clis=len(items))
    return out


def office_rows(processes: list[Process], now: float | None = None, *,
                codex_root: str | None = None, claude_root=None,
                canonical: str | None = None,
                opencode_db: str | None = None, kilo_db: str | None = None,
                cwd_of=process_cwd,
                cpu_of=process_cpu, dispatch: str | None = None,
                llmtool_tasks: str | None = None,
                finished_runs: list | None = None,
                verdicts: dict | None = None,
                runner_root: str | None = None, ci: list | None = None,
                ci_view: CiView | None = None,
                agents: dict | None = None,
                prs: dict | None = None) -> list[OfficeRow]:
    """agents: {session id: entry} reported by `claude agents`; blocked entries
    need running process evidence, while waiting-only entries keep today's behavior."""
    now = time.time() if now is None else now
    canonical = canonical or canonical_root()
    sessions = claude_sessions(claude_root)
    owners = {p.pid: p.owner if type(p.owner) is str else "" for p in processes}
    # Blocked listings still need attention; waiting-only CLIs need a reply
    # from the Codex PM that launched them, you when no agent did, or that agent.
    def answerer(owner: str) -> str:
        if owner.startswith("Codex"):
            return "PM（Codex）"
        return "你" if not owner or owner.startswith("Claude Code daemon") else f"啟動它的 {family(owner)}"

    registered = _claude_registered(sessions, processes)
    live_sessions = _live_by_session(registered)
    running_claude_pids = {p.pid for p in processes
                            if p.role in CLAUDE_CLI_ROLES + ("Claude Code background session",)}
    live = {sid: (entry, answerer(owners.get(entry.get("pid"), "")))
            for sid, entry in live_sessions.items()}
    registered_sessions = set(live_sessions)

    def has_running_evidence(sid: str, entry: dict) -> bool:
        return (registered is None or sid in registered_sessions
                or (type(entry.get("listedPid")) is int
                    and entry["listedPid"] in running_claude_pids))

    listed_agents = [(sid, entry) for sid, entry in (agents or {}).items()
                     if entry.get("jobState") != "blocked"
                     or has_running_evidence(sid, entry)]
    for sid, listed in listed_agents:
        # What `claude agents` says beats the registry's own status word unless the registry is more urgent.
        # Blocked listings need running evidence before they can ask for attention or keep a transcript open.
        entry, who = live.get(sid, ({}, "你"))
        if entry and _live_rank(entry) < _live_rank(listed):
            listed = {key: value for key, value in listed.items()
                      if key not in ("status", "jobState")}
        merged = {**entry, **listed}
        if "cwd" in entry:
            merged["cwd"] = entry["cwd"]
        live[sid] = (merged, who)
    # Monitor.poll builds the office only after the process table was read,
    # so an empty list means nothing is running, not "unknown".
    running = sorted(_one_shot_runs(processes, sessions), key=lambda p: p.pid)
    calls = [-(_elapsed_seconds(p.elapsed) or 0.0) for p in running]
    codex_running = any(p.role.startswith("Codex CLI") for p in processes)
    # Each running CLI is attached to one thread.
    clis = agent_clis(processes, sessions)
    # A `codex exec` run without a live process has ended, however its
    # session file stops (Owner report 2026-11-03: killed runs showed 無回應).
    codex_execs = sum(p.codex_exec and family(p.role) == "Codex" for p in clis)
    codex = codex_rows(now, codex_root, canonical, calls, verdicts,
                       codex_running=codex_running, codex_execs=codex_execs)
    rows = (_settled_codex_failures(codex, canonical, prs)
            + claude_rows(now, claude_root, canonical, live))
    rows = _closed_codex_cli(rows, sum(family(p.role) == "Codex" for p in clis))
    keep_agents = ((sid, entry) for sid, entry in listed_agents
                   if entry.get("jobState") == "blocked")
    rows = _closed_claude_cli(rows, sessions, processes,
                              keep={f"claude:{sid}.jsonl" for sid, _ in keep_agents})
    # A resident OpenCode SERVER alone is not a member; each CLI/TUI or `run`
    # client is.
    _forget_cwds(processes)
    forget_pids(processes)
    opencode = opencode_rows(processes, now, canonical, opencode_db, cwd_of)
    rows += opencode + kilo_rows(processes, now, canonical, kilo_db, cwd_of)
    rows += _d6_process_rows(processes, rows, canonical, cwd_of, now)
    rows += _open_cli_rows([p for p in clis if p not in running], rows, now, canonical, cwd_of)
    agy_open = any(p.role == "Antigravity App (AGY)" for p in processes)
    # `claude -p --no-session-persistence` started by Codex leaves no session
    # file. Calls a Codex record already shows are not repeated; any further
    # process is listed on its own, since nothing says which thread started it.
    recorded = [row for row in rows if row.key == "review"]
    for proc in running[sum(row.state == WORKING for row in recorded):]:
        rows.append(OfficeRow("claude 單次呼叫", WORKING,
                              "由 Codex 啟動，執行中（看不出是哪個對話、在做什麼）",
                              _elapsed_seconds(proc.elapsed) or 0.0, 30, kind="subagent",
                              evidence="程序"))
    for duration, since_end in (finished_runs or [])[sum(row.state == DONE for row in recorded):]:
        rows.append(OfficeRow("claude 單次呼叫", DONE,
                              f"由 Codex 啟動，已結束（跑了約 {max(1, int(duration))} 秒，"
                              "看不出在做什麼）", since_end, 30, kind="subagent", evidence="程序"))
    agy = agy_rows(processes, canonical, cwd_of, cpu_of)
    mac = runner_rows(processes, now, runner_root, cwd_of, ci=ci, waiting_jobs=[])
    local_names = {row.key for row in mac if row.kind == "worker" and row.key}
    mac_waiting, _linux_waiting, _unassigned = _ci_queue_groups(ci_view, local_names)
    runner_count = sum(row.kind == "worker" for row in mac)
    if mac_waiting:
        mac.append(ci_queue_row(mac_waiting, runner_count))
    rows += agy + mac + github_runner_rows(ci_view, now, local_names, ci)
    if ci_view is not None:
        rows += hosted_ci_rows(ci, ci_view, now)
    rows.append(remote_test_row(processes, now, llmtool_tasks))
    gw_runs = gw_rows(processes, now, canonical) + gw_task_rows(now, llmtool_tasks)
    rows += gw_runs
    gw_live: dict = {}
    for row in gw_runs:
        if row.state == WORKING:
            gw_key = GW_ROSTER.get(row.name) or (HOME_GW if row.name.startswith("home LLM") else None)
            if gw_key:
                gw_live.setdefault(gw_key, []).append(row)
    raw, source = load_dispatch(canonical, dispatch)
    switches, problem = worker_dispatch(raw)
    for name, key, order in WORKER_ROSTER + DEBUG_ROSTER:
        live = (opencode if name == "opencode developer" else agy if name == "agy"
                else gw_live.get(name, []))
        switch = switches.get(key)
        if switch is None and not problem:
            continue   # an optional key (#1502 WP-B debug route) absent from an older file
        trouble = problem
        paused = not trouble and switch[0] == "PAUSED"
        if name == HOME_GW and paused:
            continue   # #1592: the retired company-gw route gets no row while PAUSED
        if live and not trouble and not paused and name != HOME_GW:
            continue   # the live CLI rows say everything
        notes, state, age, alert = [], None, None, ""
        if name == "opencode developer" and not live:
            outcome = opencode_outcome(now, opencode_db, canonical)
            if outcome is not None:
                state, age = outcome[0], outcome[2]
                notes.append("本 repo " + outcome[1])
                if state == STOPPED:   # kept even when the dispatch state is unknown
                    alert = "本 repo " + outcome[1]
        if name == "agy" and agy_open and not live:
            notes.append("App 開著")
        if name == HOME_GW:
            gw = llmtool_route_outcomes(now, llmtool_tasks).get("company-gw")
            if gw is not None:
                age = gw[0]
                if gw[2] == 0:
                    state = ATTENTION   # reachable on paper, but every recent llmtool run failed
                    alert = f"近 7 天 {gw[1]} 次都沒成功，最近一次：{gw[3]}"
                notes.append(f"本機 llmtool 紀錄（不分 repo）最近一次：{gw[3]}"
                             f"（近 7 天 {gw[1]} 次，成功 {gw[2]} 次）")
        if live:
            notes.append(f"有 {len(live)} 個執行中" if name in gw_live
                         else f"有 {len(live)} 個 CLI 開著（非派工）")
        if trouble:
            label, state, authority = DISPATCH_PROBLEMS[trouble], UNCHECKED, ""
        else:
            label = {"PAUSED": f"{switch[1]} 暫停派工（{switch[2]}）",
                     "OWNER_ASSIGNMENT_ONLY": "僅 Owner 指定時派工",
                     "ALLOWED": "可派工"}[switch[0]]
            if source != "派工檔":
                label += f"（{source}）"
            authority = switch[1]
            if paused:
                state, age = PAUSED, None
            elif state is None:
                state = IDLE
        if paused:
            alert = ""   # the Owner already stopped dispatch; a fault is not news
        rows.append(OfficeRow(name, state, "；".join([label] + notes), age, order,
                              kind="worker", evidence=source or "派工檔", authority=authority,
                              alert=alert))
    rows = _codex_work_rows(rows, now, canonical,
                            LLMTOOL_TASKS if llmtool_tasks is None else llmtool_tasks, prs)
    rows = _bind_d6_pr_rows(rows, canonical, prs)
    return sorted(rows, key=lambda row: (row.order, row.age if row.age is not None else 1e12,
                                         row.name))


def _settled_codex_failures(rows: list[OfficeRow], canonical: str,
                            prs: dict[str, tuple[int, str]] | None) -> list[OfficeRow]:
    """Demote failed Codex rows when their registered D6 PR is known done.

    This changes displayed state only. Missing, unreadable, or unknown PR data
    leaves the alert in place, as does a task without a registered branch.
    """
    if type(prs) is not dict or not prs:
        return rows
    details = registry_details(canonical)
    settled = {}
    for task_id, (_, _, _, _, branch) in details.items():
        if not branch:
            continue
        pull = prs.get(branch)
        if (type(pull) is tuple and len(pull) == 2 and type(pull[1]) is str
                and pull[1] in PR_DONE_WORDS):
            settled[task_id] = True
    if not settled:
        return rows

    def _parent_task_id(task_id: str) -> str:
        if len(task_id) < 11:
            return task_id
        dash = len(task_id) - 8
        if task_id[dash] != "-":
            return task_id
        sha = task_id[dash + 1:]
        if not all(c in "0123456789abcdefABCDEF" for c in sha):
            return task_id
        prefix = task_id[:dash]
        hd = prefix.rfind("-hd")
        if hd <= 0:
            return task_id
        number = prefix[hd + 3:]
        if not number or not all(c in "0123456789" for c in number):
            return task_id
        return prefix[:hd]

    return [replace(row, state=DONE,
                    detail=row.detail + "（上一輪失敗，PR 已結束）")
            if row.key.startswith("codex:") and row.state == ATTENTION
            and row.exec_result == "failed"
            and row.task.startswith("task ")
            and (row.task[5:] in settled
                 or _parent_task_id(row.task[5:]) in settled)
            else row for row in rows]


def _char_width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


@functools.lru_cache(maxsize=4096)   # the same lines are measured on every repaint
def _width(text: str) -> int:
    return sum(map(_char_width, text))


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _ago(age: float | None) -> str:
    if age is None:
        return "無即時紀錄"
    if age < 60:
        return "剛剛"
    if age < 3600:
        return f"{int(age // 60)} 分鐘前"
    return f"{int(age // 3600)} 小時前"


def pm_progress_line(work: WorkBoard, dispatch, now: float) -> tuple[str, str]:
    entry, problem = dispatch if dispatch is not None else (None, "")
    if dispatch is None or problem:
        age_text, age = "?", None
    elif entry is None:
        age_text, age = "沒有紀錄", None
    else:
        age = max(0.0, now - entry.created)
        age_text = _ago(age)

    queue_count = len(work.queue) if work.board_known else None
    queue_text = "?" if queue_count is None else str(queue_count)
    if queue_count is not None and work.board_status == "stale":
        queue_text += "（可能過時）"

    if not work.github_known or work.github_status:
        merges_text = "?"
    else:
        merges_text = str(len(work.done))
        if "今天完成可能不完整" in work.note:
            merges_text += "+"

    style = "!" if (queue_count is not None and queue_count > 0
                    and work.board_status == "ok"
                    and age is not None and age > PM_PROGRESS_STALE) else ""
    return f"PM 進度：上次派工 {age_text} · 排隊 {queue_text} · 今天合併 {merges_text}", style


def pm_progress(mon, now: float) -> tuple[str, str]:
    try:
        work = work_board(mon.github, mon.pm_board, now)
    except Exception:
        return "PM 進度：未確認", ""
    try:
        dispatch = pm_last_dispatch(now)
    except Exception:
        dispatch = None
    return pm_progress_line(work, dispatch, now)


LIVE_SECTION = "即時（讀得到 session 紀錄）"
STATUS_SECTION = "只看得到狀態（依派工紀錄和程序推算）"


BANDS = ("?", "+", "!", "dim")   # row colours top to bottom: red, green, yellow, gray
GRAY = BANDS.index("dim")


def _tree(rows: list[OfficeRow], fold: bool = False) -> list[tuple[str, OfficeRow]]:
    """(prefix, row) in display order: each agent followed by what it started.
    Each section is ordered by colour band (red, green, yellow, gray; see BANDS),
    stable within a band, and a group is ranked by its most urgent member so a
    dimmed parent never drags a working subagent down. With fold, every gray
    row (its whole group gray too) becomes one count at the end of its section
    (Owner 2026-10-28: green and yellow stay on screen, key a unfolds). A
    shared line (kind shared) is not ranked or folded: it goes right after the
    last shown row it is about (PR #614 review: never between the runner rows),
    or last of all when they are all folded away (W47)."""
    keys = {row.key for row in rows if row.key}
    children: dict[str, list[OfficeRow]] = {}
    for row in rows:
        if row.group in keys:
            children.setdefault(row.group, []).append(row)

    def below(row: OfficeRow, depth: int) -> list[OfficeRow]:
        return children.get(row.key, []) if row.key and depth < 3 else []

    def band(row: OfficeRow, depth: int) -> int:
        return min([BANDS.index(_row_style(row.state)),
                    *(band(child, depth + 1) for child in below(row, depth))])

    def ordered(items: list[OfficeRow], depth: int) -> list[OfficeRow]:
        return sorted(items, key=lambda row: band(row, depth))   # stable: band only

    def size(row: OfficeRow, depth: int) -> int:
        return 1 + sum(size(child, depth + 1) for child in below(row, depth))

    def walk(row: OfficeRow, depth: int, out: list) -> int:
        """Appends the row and its group to out; returns how many gray rows were
        folded away instead (every gray member when fold is on)."""
        if fold and band(row, depth) == GRAY:
            return size(row, depth)
        out.append(("  " * (depth - 1) + "└ " if depth else "", row))
        return sum(walk(child, depth + 1, out) for child in ordered(below(row, depth), depth + 1))

    def section(items: list[OfficeRow], shared: tuple | list = ()) -> list[tuple[str, OfficeRow]]:
        out: list[tuple[str, OfficeRow]] = []
        hidden = sum(walk(row, 0, out) for row in ordered(items, 0))
        for line in shared:   # after the last shown row it is about, whatever its band
            out.insert(max([i + 1 for i, (_, row) in enumerate(out) if row.name.startswith(line.group)],
                           default=len(out)), ("", line))
        if hidden:
            out.append(("", OfficeRow(f"… 灰色 {hidden} 項", "", "（已摺疊，按 a 展開）", None, 10 ** 6)))
        return out

    out: list[tuple[str, OfficeRow]] = []
    agents = [row for row in rows if row.group not in keys and row.kind not in ("worker", "shared")]
    if any(row.kind not in ("worker", "shared") or row.group in keys for row in rows):
        out.append(("—", OfficeRow(LIVE_SECTION, "", "", None, 0)))
        out += section(agents)
    workers = [row for row in rows if row.kind == "worker" and row.group not in keys]
    if workers:
        out.append(("—", OfficeRow(STATUS_SECTION, "", "", None, 0)))
        out += section(workers, [row for row in rows if row.kind == "shared"])
    return out


REVIEW_NO_VERDICT = frozenset({NO_VERDICT})
ALERT_STATES = (STALLED, STOPPED, ATTENTION)


def _short_ago(age: float | None) -> str:
    """The time column: short, and kept clear of the cut-off detail."""
    if age is None:
        return ""
    if age < 60:
        return "剛剛"
    if age < 3600:
        return f"{int(age // 60)} 分前"
    return f"{int(age // 3600)} 小時前"


def _review_alerts(rows: list[OfficeRow]) -> list[str]:
    """One line per PR whose one-shot reviews ended without a verdict more than
    once: repeated calls on one PR read as one problem, not as separate rows."""
    by_pr: dict[str, list[OfficeRow]] = {}
    for row in rows:
        if row.key == "review" and row.task.startswith("PR #"):
            by_pr.setdefault(row.task.split()[1], []).append(row)
    out = []
    for pr, items in by_pr.items():
        missing = sum(row.outcome in REVIEW_NO_VERDICT for row in items)
        running = [row for row in items if row.state == WORKING]
        done = sorted((row for row in items if row.state == DONE),
                      key=lambda row: row.age if row.age is not None else 1e12)
        if missing < 2 or (done and not running and done[0].outcome not in REVIEW_NO_VERDICT):
            continue   # fine, or the newest review did reach a verdict
        parts = [f"{missing} 次結束後 GitHub 沒有結論"]
        fails = sum(row.outcome == "FAIL" for row in items)
        if fails:
            parts.append(f"{fails} 次 FAIL")
        tail = (f"；目前執行中 {running[0].task.split()[-1]}" if running
                else "；目前沒有在跑")
        out.append(f"{pr} reviewer：" + "、".join(parts) + tail)
    return out


DETAIL_COLS = 44   # detail width when the screen width is not known
DETAIL_MAX = 72    # widest detail column: longer text wraps, the time stays near it
BREAK_AFTER = "；，、 "   # preferred places to continue on the next line
NO_LINE_START = "）」』】，。、；：！？)]}"


def _wrap(text: str, cols: int) -> list[str]:
    """text split into lines of at most cols display columns (at least one),
    broken after ；，、 or a space where possible, never before a closing mark."""
    return list(_wrapped(text, cols))


@functools.lru_cache(maxsize=1024)   # repainted once a second with the same rows
def _wrapped(text: str, cols: int) -> tuple:
    pieces = []
    while len(text) > 1 and _width(text) > cols:
        fit, used = 0, 0
        while used + _char_width(text[fit]) <= cols:
            used += _char_width(text[fit])
            fit += 1
        cut = max((i + 1 for i in range(fit) if text[i] in BREAK_AFTER), default=0)
        if cut < max(1, fit // 2):   # no good break point in the second half: break anywhere
            cut = max(1, fit)
            while 1 < cut and text[cut] in NO_LINE_START:
                cut -= 1
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip(" ")
    return tuple(pieces + [text])


def _row_style(state: str) -> str:
    """Working rows stand out, problems are yellow, everything else is dimmed."""
    return "+" if state == WORKING else "!" if state in ALERT_STATES else "dim"


def _line_style(row: OfficeRow, rows: list[OfficeRow]) -> str:
    """A shared line takes the most urgent colour of the rows it is about
    (the Mac runners' band); every other row is coloured by its own state."""
    if row.kind != "shared":
        return _row_style(row.state)
    about = [_row_style(other.state) for other in rows if other.name.startswith(row.group)]
    return min(about, key=BANDS.index, default="dim")


# The bracketed reference after a CI name (see _ci_text), e.g. the
# （main @c85a0ec） of 「llmtool Linux CI（main @c85a0ec）」 or （PR #1098 @b29462a）.
# The TUI draws it bright white on runner rows (#763); the text never changes.
CI_REF = re.compile(r"（(?:PR #\d+|[^（）\s]+) @[0-9A-Fa-f]*）")
RUNNER_ROW_NAME = re.compile(r"(?:\S+ )?ci runner\b")


def _is_runner_row(row: OfficeRow) -> bool:
    return row.kind == "worker" and bool(RUNNER_ROW_NAME.match(row.name))


def _ref_flags(detail: str, pieces: list[str]) -> list[list[bool]]:
    """For each wrapped piece of detail, which of its characters belong to a
    CI reference; a reference that wrapping splits is lit on both lines."""
    spans = [found.span() for found in CI_REF.finditer(detail)]
    out, pos = [], 0
    for piece in pieces:
        start = detail.find(piece, pos)   # _wrap only drops spaces between pieces
        if start < 0:
            out.append([False] * len(piece))
            continue
        pos = start + len(piece)
        out.append([any(s <= start + i < e for s, e in spans) for i in range(len(piece))])
    return out


def _split_lit(text: str, lit: list[bool]) -> list[tuple[str, bool]]:
    """text as (text, highlighted) runs; lit is per character (extra flags ignored)."""
    parts: list[tuple[str, bool]] = []
    for char, on in zip(text, lit):
        if parts and parts[-1][1] == on:
            parts[-1] = (parts[-1][0] + char, on)
        else:
            parts.append((char, on))
    return parts or [(text, False)]


def ci_ref_parts(detail: str, pieces: list[str]) -> list[list[tuple[str, bool]]]:
    """Each wrapped piece of a runner row's detail split into (text,
    highlighted) parts that join back to the piece; highlighted is the CI
    reference, e.g. （PR #1098 @b29462a）. A piece without one is one part."""
    return [_split_lit(piece, lit) for piece, lit in zip(pieces, _ref_flags(detail, pieces))]


def owner_count_line(github) -> tuple[str, str]:
    if github is None:
        return "等你決定：未讀 GitHub", ""
    if GITHUB_IDENTITY_STATUS:
        return f"等你決定：未確認（{GITHUB_IDENTITY_STATUS}）", "!"
    if not github.successful_read:
        text = "等你決定：尚未讀取"
        if github.read_failed:
            text += "（GitHub 讀取失敗，可能過時）"
        return text, "!" if github.read_failed else ""
    count = sum(item.kind == "waiting_owner" for item in github.pending)
    cut = getattr(github, "comments_cut", False)
    if cut:
        if count == 0:
            text = (f"等你決定：未確認（留言太多，只讀了最新 "
                    f"{GITHUB_PAGE * GITHUB_PAGES} 則）")
        else:
            text = (f"等你決定：{count} 件以上（留言太多，只讀了最新 "
                    f"{GITHUB_PAGE * GITHUB_PAGES} 則）")
    else:
        text = f"等你決定：{count} 件"
    others = sum(item.kind != "waiting_owner" for item in github.pending)
    if others:
        text += f"（另有 {others} 筆其他 Owner 請求）"
    elif count == 0 and not cut:
        text += f"（只看最近 {int(github.window // 3600)} 小時留言）"
    if github.read_failed:
        text += "（GitHub 讀取失敗，可能過時）"
    return text, "!" if count or others else ""


def install_lines(github, pin_file=None) -> list[tuple[str, str]]:
    """Show Owner approval and pinned-install updates without changing state."""
    if (github is None or not github.successful_read
            or getattr(github, "read_failed", False)):
        return []

    out = []
    approvals = sum(
        item.needs_owner_approval
        for item in github.pending
    )
    if approvals:
        out.append((f"等你終端核准：{approvals} 件（請在自己的終端執行 "
                    "llmtool owner-approval grant）", "!"))

    if pin_file is None:
        pin_file = os.path.join(os.path.expanduser("~"), ".local/state/llmtool/approved-install.json")
    fd = None
    try:
        fd = os.open(pin_file, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("pin is not a regular file")
        with os.fdopen(fd, "rb") as pin:
            fd = None
            raw = pin.read(4097)
        if len(raw) > 4096:
            raise ValueError("pin too large")
        data = json.loads(raw)
        approved_sha = data.get("approved_sha") if isinstance(data, dict) else None
        if (not isinstance(approved_sha, str) or len(approved_sha) != 40
                or any(char not in "0123456789abcdef" for char in approved_sha)):
            raise ValueError("invalid approved SHA")
    except FileNotFoundError:
        return out
    except (OSError, ValueError, UnicodeError, RecursionError):
        out.append(("安裝釘選檔無法讀取", "!"))
        return out
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    main_health = github.main_health
    if main_health is not None and approved_sha[:7] != main_health.sha:
        out.append((f"新版本等你安裝：{approved_sha[:7]} → {main_health.sha}"
                    "（請在自己的終端執行 llmtool self-update）", "!"))
    return out


def owner_waiting(github) -> int | None:
    if (github is None or GITHUB_IDENTITY_STATUS
            or not github.successful_read or github.read_failed):
        return None
    count = sum(item.kind == "waiting_owner" for item in github.pending)
    if getattr(github, "comments_cut", False) and count == 0:
        return None
    return count


def stuck_lines(mon, now) -> list[str]:
    try:
        github = mon.github
        prs = github.branch_prs if github is not None else {}
        details = registry_details(canonical_root())
        records = mon.stuck_records[0] if mon.stuck_records is not None else None
        return stuck_alerts(now, github.ci_runs if github is not None else None,
                            stuck_reviews(mon.office, details), records,
                            stuck_open_d6(details, prs))
    except Exception:
        return []


def office_top(github, quota, now, stuck=None, progress=None, install=None) -> list[tuple[str, str]]:
    out = [owner_count_line(github)]
    if isinstance(install, list):
        out.extend(install)
    if github is not None:
        out.append(main_health_line(github.main_health, github.main_error, now))
    if progress is not None:
        out.append(progress)
    out.extend((line, "!") for line in (quota or []))
    out.extend((line, "!") for line in (stuck or []))
    return out


def office_view(rows: list[OfficeRow], error: str | None = None,
                width: int | None = None, expand: bool = False,
                pending: list[OwnerPending] | None = None,
                now: float | None = None, scope: str = "", *,
                waiting: int | None = None,
                top: list[tuple[str, str]] | None = None,
                parts: dict[int, list[tuple[str, bool]]] | None = None,
                entries: list[dict[str, typing.Any]] | None = None) -> list[tuple[str, str]]:
    """[(line, style)]: style is "title", "!", "+", "dim", "section" or "".
    parts, if given, gets {line index: [(text, highlighted)]} for each runner
    row line holding a CI reference (the TUI draws those bright white).
    With a screen width, each detail is cut so the time column stays visible.
    Rows are ordered red, green, yellow, gray in each section; without expand
    the gray rows are folded into one count per section (key a in the TUI).
    pending: GitHub requests waiting for the Owner, shown first."""
    working = sum(row.state == WORKING for row in rows)
    waiting_text = "?" if waiting is None else str(waiting)
    working_text = "?" if error else str(working)
    out = [(f"辦公室 · 工作中 {working_text} · 等你 {waiting_text}", "title")]
    now = time.time() if now is None else now
    for text, style in top or []:
        out += [(('  ' if i else '') + piece, style) for i, piece in
                enumerate(_wrap(text, max(10, width - 2)) if width else [text])]
    pending = list(pending or [])
    texts = [f"★ 等你決定：GitHub {item.reference}（{_short_ago(max(0.0, now - item.when))}）"
             + (f" — {item.summary}" if item.summary else "")
             for item in pending[-OWNER_SHOWN:]]
    if len(pending) > OWNER_SHOWN:
        texts.append(f"★ …另有 {len(pending) - OWNER_SHOWN} 筆較早的等你決定")
    for text in texts:
        out += [(("  " if i else "") + piece, "!") for i, piece in
                enumerate(_wrap(text, max(10, width - 2)) if width else [text])]
    if error:
        return out + [(f"⚠ 辦公室資料讀取失敗（{error}），目前無法判斷誰在做什麼。", "!")]
    if not rows:
        return out + [("目前沒有觀察到任何 agent 或 worker。", "dim")]
    mains = sum(row.main and row.state != CLOSED for row in rows)
    if mains > 1:
        out.append((f"⚠ 有 {mains} 個 Codex 主對話都開在主 repo："
                    "可能同時寫入主 repo（D6 只允許一個 Integrator 寫入）", "!"))
    # The same dispatch problem on several Workers is said once, not per row.
    notes = []
    shared: dict[str, list[OfficeRow]] = {}
    for row in rows:
        label = next((text for text in DISPATCH_PROBLEMS.values()
                      if row.kind == "worker" and row.detail.startswith(text)), None)
        if label:
            shared.setdefault(label, []).append(row)
    shared = {label: items for label, items in shared.items() if len(items) > 1}
    for label, items in shared.items():
        notes.append(f"ℹ {label}：" + "、".join(row.name for row in items))
    trimmed = {id(row): row.detail[len(label):].lstrip("；") or "沒有其他紀錄"
               for label, items in shared.items() for row in items}
    rows = [replace(row, detail=trimmed[id(row)]) if id(row) in trimmed else row
            for row in rows]
    # Likewise a fallback source of the dispatch file ("本機 origin/main 的派工檔…"),
    # which goes to the dimmed legend at the bottom.
    legend = []
    for source in sorted({row.evidence for row in rows if row.kind == "worker"}):
        note = f"（{source}）"
        if source and sum(note in row.detail for row in rows) > 1:
            legend.append(f"派工狀態讀自{source}")
            rows = [replace(row, detail=row.detail.replace(note, "", 1)) for row in rows]
    alerts = _review_alerts(rows)
    alerts += [f"{_shown(row)}：{row.alert}" for row in rows if row.alert]
    # In full (wrapped, not cut): the session name at the end is how a PM
    # finds the window that needs an answer.
    alerts += [f"{_shown(row)}（{row.state}，{_short_ago(row.age)}）{row.detail}"
               for row in rows if row.state in ALERT_STATES and not row.alert]
    for text in [f"⚠ {text}" for text in alerts] + notes:
        out += [(("  " if i else "") + piece, "!") for i, piece in
                enumerate(_wrap(text, width - 2) if width else [text])]
    tree = _tree(rows, fold=not expand)
    name_cols = max(_width(prefix + _shown(row)) for prefix, row in tree if prefix != "—") + 2
    # The detail column is as wide as the longest detail, up to DETAIL_MAX and
    # what the screen leaves, so the time stays near its text on a wide screen.
    longest = max([_width(_row_detail(row)) for _, row in tree if _row_detail(row)] or [0])
    detail_cols = (DETAIL_COLS if width is None else
                   max(12, min(longest, DETAIL_MAX, width - name_cols - 8 - 11)))
    current_section = "live"
    for prefix, row in tree:
        if prefix == "—":
            current_section = "status" if row.name == STATUS_SECTION else "live"
            if row.name == STATUS_SECTION:
                out.append(("", ""))   # a blank line sets the Worker list apart
            out.append((f"── {row.name} ──", "section"))
            continue
        # In the TUI a long detail continues on the next lines under itself.
        detail = _row_detail(row)
        pieces = [detail] if width is None else _wrap(detail, detail_cols)
        when = _short_ago(row.age)
        head = f"{_pad(prefix + _shown(row), name_cols)}{_pad(row.state, 8)}"
        line = head + pieces[0]
        if when:
            line = f"{_pad(line, name_cols + 8 + detail_cols)}  {when}"
        style = _line_style(row, rows)
        if entries is not None:
            depth = 0 if prefix == "" else prefix.count("  ") + 1
            if style == "+":
                band = "working"
            elif style == "!":
                band = "alert"
            elif style == "dim":
                band = "idle"
            else:
                band = "error"
            if row.role in ("pm", "developer", "reviewer"):
                room = row.role
            elif _is_runner_row(row) or row.name == "EC2 遠端測試":
                room = "machine"
            elif row.kind == "worker" and bool(row.authority):
                room = "developer"
            elif depth > 0:
                room = "other"
                for prev in reversed(entries):
                    if prev["depth"] < depth:
                        room = prev["room"]
                        break
            else:
                room = "other"
            entries.append({
                "section": current_section,
                "depth": depth,
                "name": _shown(row),
                "state": row.state,
                "detail": detail,
                "age": when,
                "band": band,
                "room": room,
            })
        first = len(out)
        out.append((line.rstrip(), style))
        out += [(" " * (name_cols + 8) + piece, style) for piece in pieces[1:]]
        if parts is not None and _is_runner_row(row):
            shifts = [len(head)] + [name_cols + 8] * (len(pieces) - 1)
            for index, (shift, lit) in enumerate(zip(shifts, _ref_flags(detail, pieces)), first):
                if any(lit):
                    text = out[index][0]
                    parts[index] = _split_lit(text, [False] * shift + lit + [False] * len(text))
    legend.append("工作中 = 最近有動作，不代表模型正在產生內容")
    if scope:
        legend.append(scope)
    text = "；".join(legend)
    out += [(piece, "dim") for piece in (_wrap(text, width) if width else [text])]
    return out


def office_lines(rows: list[OfficeRow], error: str | None = None,
                 width: int | None = None, expand: bool = False,
                 pending: list[OwnerPending] | None = None, scope: str = "", *,
                 waiting: int | None = None,
                 top: list[tuple[str, str]] | None = None) -> list[str]:
    return [line for line, _ in office_view(rows, error, width, expand, pending,
                                             scope=scope, waiting=waiting, top=top)]


def office_events(old: dict, rows: list[OfficeRow]) -> tuple[dict, list[tuple[str, str]]]:
    """Log lines for members whose state changed since the previous poll."""
    # The short session id keeps two sessions with the same name and detail apart.
    new = {f"{_shown(row)}|{row.detail}": row.state for row in rows}
    events = []
    for key, state in new.items():
        before = old.get(key)
        if before is not None and before != state:
            name, detail = key.split("|", 1)
            kind = "+" if state == WORKING else "!" if state in (STALLED, STOPPED, ATTENTION) else "i"
            events.append((kind, f"{name}「{detail}」：{before} → {state}"))
    return new, events


MARK = {"+": "+", "-": "-", "!": "!", "ok": "✓", "?": "?", "i": "·", "▶": "▶", "→": "→", "■": "■"}
FOOTNOTE = "工作中 ≠ 模型正在產生內容；程序消失 ≠ 任務完成"
FOLLOW_VIEWS = frozenset({"log", "activity"})   # newest at the bottom; offset counts back


def record_lines(records: list[str]) -> list[str]:
    head = "llmtool 任務紀錄（近 12 小時，最新在上）"
    return [head, *(records[:8] or ["近 12 小時沒有更新的任務紀錄。"])]


def run_once(github: "GitHubSource | None" = None) -> int:
    mon = Monitor()
    mon.agents = ClaudeAgents(background=False)   # a single poll: ask before it, not after
    if github is not None:
        github.want_hosted_ci = True
        github.refresh()   # before the poll, so the D6 list can see PR states
        mon.github = github
    mon.poll()
    header = (f"AI agents · 監控版本 {mon.version} · {datetime.now():%Y-%m-%d %H:%M:%S} · "
              f"{summary(mon.processes, mon.lingering, ci_view=github.ci_view if github else None)}")
    disk = disk_status(mon.disk_usage)
    print(header + (f"  ·  {disk}" if disk else ""))
    behind = behind_line(mon)
    if behind:
        print(behind)
    alert = disk_alert(mon.disk_usage)
    if alert:
        print(alert)
    if mon.error:
        print(f"STATUS: UNKNOWN (unable to read process table: {mon.error})")
        return 1
    print("\n".join(office_lines(mon.office, mon.office_error, expand=True,
                                 pending=github.pending if github is not None else None,
                                 scope=github.scope if github is not None else "",
                                 waiting=owner_waiting(github or mon.github),
                                 top=office_top(github, mon.quota, time.time(),
                                                stuck=stuck_lines(mon, time.time()),
                                                progress=pm_progress(mon, time.time()),
                                                install=rule_alert_lines() + install_lines(github)))))
    print("\n".join(line for line, _ in board_view(
        work_board(github, mon.pm_board, time.time(), ec2=mon.ec2_heads,
                   reviewing=pr_reviewing(mon.office), office=mon.office,
                   office_known=not bool(mon.office_error)))))
    print()
    print("\n".join(table_lines(mon.processes)))
    print("\n".join(["", *d6_lines(mon.d6), "", *record_lines(mon.records), "",
                     *task_lines(mon.tasks, 3, mon.opencode_route,
                                 mon.opencode_route_failed,
                                 getattr(mon, "opencode_paused", ""))]))
    print("\n最近動態")
    print("\n".join(activity_lines(mon.tasks, github=github)))
    print(f"說明：{FOOTNOTE}。")
    return 0


def run_plain(interval: float, mon: "Monitor") -> int:
    """Append-only stream: one line per change, never a repainted screen."""
    print(f"AI agents · 監控版本 {mon.version} · read-only process monitor · {FOOTNOTE} · Ctrl+C to quit",
          flush=True)
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
        if curses.COLOR_PAIRS > 10:   # CI references on runner rows (#763), drawn with A_BOLD
            curses.init_pair(10, 15 if curses.COLORS >= 16 else curses.COLOR_WHITE, -1)
            colors["ref"] = curses.color_pair(10)
    view, offset, next_poll = "office", 0, 0.0
    expanded = False   # office view: gray rows folded (default) or listed (key a)
    noise = False      # Activity view: feed noise hidden (default) or shown (key a)
    next_draw, dirty = 0.0, True

    def put(y: int, text: str, attr: int = 0, x: int = 0) -> None:
        h, w = stdscr.getmaxyx()
        if 0 <= y < h and 0 <= x < w - 1:
            try:
                stdscr.addnstr(y, x, text, w - 1 - x, attr)
            except curses.error:
                pass

    while True:
        if time.monotonic() >= next_poll:
            mon.poll()
            if mon.github is not None:
                mon.github.refresh_in_background()
            next_poll = time.monotonic() + interval
            dirty = True
        # The screen is painted after a poll or a key, else once a second for
        # the clock; getch() still wakes every 100 ms so keys answer at once.
        if dirty or time.monotonic() >= next_draw:
            dirty, next_draw = False, time.monotonic() + DRAW_EVERY
            offset = _paint_screen(stdscr, mon, view, offset, expanded, colors, put,
                                   noise=noise)
        key = stdscr.getch()
        if key == -1:
            continue
        dirty = True
        if key > 255 and key not in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE,
                                     curses.KEY_NPAGE, curses.KEY_END) or 127 < key <= 255:
            # Unknown input such as committed input-method text: ignore it and
            # redraw the whole screen, in case the terminal scrolled while
            # composing.
            stdscr.clearok(True)
            continue
        if key in (ord("q"), ord("Q"), 27):
            return
        views = ("log", "tasks", "table", "activity", "office", "board")
        if key == 9:  # Tab cycles through log, tasks, processes, Activity, office and board
            view, offset = views[(views.index(view) + 1) % len(views)], 0
        elif key in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5"), ord("6")):
            view, offset = views[key - ord("1")], 0
        elif key in (ord("a"), ord("A")) and view == "office":
            expanded = not expanded
        elif key in (ord("a"), ord("A")) and view == "activity":
            noise = not noise
        elif key == curses.KEY_UP:
            offset += 1 if view in FOLLOW_VIEWS else -1
        elif key == curses.KEY_DOWN:
            offset -= 1 if view in FOLLOW_VIEWS else -1
        elif key == curses.KEY_PPAGE:
            offset += body_lines(stdscr) if view in FOLLOW_VIEWS else -body_lines(stdscr)
        elif key == curses.KEY_NPAGE:
            offset -= body_lines(stdscr) if view in FOLLOW_VIEWS else -body_lines(stdscr)
        elif key in (curses.KEY_END, ord("G")):
            offset = 0
        offset = max(0, offset)


DRAW_EVERY = 1.0   # seconds between repaints when nothing happened (the clock)


def body_lines(stdscr) -> int:
    return max(1, stdscr.getmaxyx()[0] - 4)


def _paint_screen(stdscr, mon, view, offset, expanded, colors, put, *, noise=False) -> int:
    """One repaint of the current view (the loop above decides when);
    returns the scroll offset clamped to the view."""
    import curses
    h, w = stdscr.getmaxyx()
    body = max(1, h - 4)
    stdscr.erase()
    header = f"AI agents · 監控版本 {mon.version} · {datetime.now():%H:%M:%S} · polls {mon.polls}"
    disk = disk_status(mon.disk_usage)
    if disk:
        header += f" · {disk}"
    put(0, header,
        colors.get("title", 0) | curses.A_BOLD)
    status = (f"STATUS UNKNOWN: {mon.error}" if mon.error
              else summary(mon.processes, mon.lingering,
                           ci_view=mon.github.ci_view if mon.github else None))
    running = running_stages(mon.tasks)
    status += f"  ·  llmtool stages running: {running}" if running else ""
    put(1, status, colors.get("!", 0) if (mon.error or mon.lingering) else 0)
    alert = disk_alert(mon.disk_usage)
    stale = (f"⚠ 監控程式已更新，這個畫面還是舊版（{mon.version}），請按 q 後重開"
             if mon.stale else "")
    behind = behind_line(mon)
    if alert or stale or behind:
        put(2, "  ·  ".join(item for item in (stale, behind, alert) if item),
            colors.get("!", 0) | curses.A_BOLD)
    if view == "log":
        entries = list(mon.log)
        offset = max(0, min(offset, len(entries) - body))
        end = len(entries) - offset
        for row, (stamp, kind, text) in enumerate(entries[max(0, end - body):end]):
            put(3 + row, f"{stamp} {MARK.get(kind, ' ')} {text}", colors.get(kind, 0))
        hint = "Tab/1-6: log·tasks·processes·Activity·辦公室·白板  ↑↓ PgUp/PgDn: scroll  End: follow  q: quit"
        if offset:
            hint = f"[scrolled back {offset}]  " + hint
    elif view == "tasks":
        lines = (d6_lines(mon.d6) + [""] + record_lines(mon.records) + [""]
                 + task_lines(mon.tasks, route=mon.opencode_route,
                             route_failed=mon.opencode_route_failed,
                             paused=getattr(mon, "opencode_paused", "")))
        offset = max(0, min(offset, len(lines) - body))
        for row, line in enumerate(lines[offset:offset + body]):
            attr = curses.A_BOLD if line.startswith(("任務 ", "llmtool 任務紀錄", "D6 任務")) else 0
            if "RUNNING" in line:
                attr |= colors.get("+", 0)
            elif "UNKNOWN" in line or "TIMEOUT" in line or "BLOCKED" in line or " error" in line:
                attr |= colors.get("!", 0)
            put(3 + row, line, attr)
        hint = "Tab/1-6: log·tasks·processes·Activity·辦公室·白板  ↑↓ PgUp/PgDn: scroll  q: quit"
    elif view == "activity":
        all_lines = activity_lines(mon.tasks, github=mon.github, background=True,
                                   show_noise=noise)
        header = _activity_screen_lines(all_lines[:1], w - 1)
        # Keep the header on the first Activity body row, but never on the
        # navigation hint row on very short terminals.
        header = header[:max(0, h - 4)]
        for row, line in enumerate(header):
            put(3 + row, line, colors.get("!", 0) if line.startswith("⚠") else 0)
        lines = _activity_screen_lines(all_lines[1:], w - 1)
        start = 3 + len(header)
        # The final row is reserved for the navigation hint, including on
        # unusually short terminals.
        available = max(0, h - 1 - start)
        offset = max(0, min(offset, len(lines) - available))
        end = len(lines) - offset
        for row, line in enumerate(lines[max(0, end - available):end]):
            attr = colors.get("!", 0) if "未知" in line or "UNKNOWN" in line or "沒送到" in line else 0
            put(start + row, line, attr)
        hint = (f"a: {'隱藏雜訊' if noise else '顯示雜訊'}  "
                "Tab/1-6: log·tasks·processes·Activity·辦公室·白板  ↑↓ PgUp/PgDn: scroll  "
                "End: follow  q: quit")
        if offset:
            hint = f"[scrolled back {offset}]  " + hint
    elif view == "office":
        parts: dict[int, list[tuple[str, bool]]] = {}
        lines = office_view(mon.office, mon.office_error, w - 1, expanded, parts=parts,
                            pending=mon.github.pending if mon.github is not None else None,
                            scope=mon.github.scope if mon.github is not None else "",
                            waiting=owner_waiting(mon.github),
                            top=office_top(mon.github, mon.quota, time.time(),
                                           stuck=stuck_lines(mon, time.time()),
                                           progress=pm_progress(mon, time.time()),
                                           install=rule_alert_lines() + install_lines(mon.github)))
        offset = max(0, min(offset, len(lines) - body))
        styles = {"title": colors.get("title", 0) | curses.A_BOLD, "!": colors.get("!", 0),
                  "+": colors.get("+", 0), "dim": curses.A_DIM, "section": curses.A_BOLD}
        ref = colors.get("ref")   # None without colors: the rows stay as they were
        for row, (line, style) in enumerate(lines[offset:offset + body]):
            put(3 + row, line, styles.get(style, 0))
            # The CI reference is drawn again over the row, bright white and bold.
            x = 0
            for text, lit in parts.get(offset + row, []) if ref is not None else []:
                if lit:
                    put(3 + row, text, ref | curses.A_BOLD, x)
                x += _width(text)
        hint = ("Tab/1-6: log·tasks·processes·Activity·辦公室·白板  ↑↓ PgUp/PgDn: scroll  "
                f"a: {'摺疊' if expanded else '展開'}灰色  q: quit")
    elif view == "board":
        lines = board_view(work_board(mon.github, mon.pm_board, time.time(),
                                      ec2=mon.ec2_heads,
                                      reviewing=pr_reviewing(mon.office),
                                      office=mon.office,
                                      office_known=not bool(mon.office_error)), w - 1)
        offset = max(0, min(offset, len(lines) - body))
        styles = {"title": colors.get("title", 0) | curses.A_BOLD,
                  "!": colors.get("!", 0) | curses.A_BOLD,
                  "section": curses.A_BOLD, "dim": curses.A_DIM}
        for row, (line, style) in enumerate(lines[offset:offset + body]):
            put(3 + row, line, styles.get(style, 0))
        hint = "Tab/1-6: log·tasks·processes·Activity·辦公室·白板  ↑↓ PgUp/PgDn: scroll  q: quit"
    else:
        lines = table_lines(mon.processes) if not mon.error else ["(process table unavailable)"]
        offset = max(0, min(offset, len(lines) - body))
        for row, line in enumerate(lines[offset:offset + body]):
            put(3 + row, line, curses.A_BOLD if offset + row == 0 else 0)
        hint = "Tab/1-6: log·tasks·processes·Activity·辦公室·白板  ↑↓ PgUp/PgDn: scroll  q: quit"
    put(h - 1, f"{hint}  ·  {FOOTNOTE}", curses.A_DIM)
    # Park the (hidden) cursor at the top-left. macOS Terminal draws an input
    # method's composing text (e.g. Zhuyin) at the cursor; left on the last
    # row, that text runs past the edge and scrolls the whole screen up.
    try:
        stdscr.move(0, 0)
    except curses.error:
        pass
    stdscr.refresh()
    return offset


# ---------------------------------------------------------------------------
# State server for local browser inspection (loopback only)
# ---------------------------------------------------------------------------

import hmac
import http.server
import secrets

STATE_SERVER_HTML = (
    b"<!DOCTYPE html>\n"
    b"<html><head><meta charset=\"utf-8\"><title>llmtool monitor</title></head>"
    b"<body>llmtool monitor</body></html>\n"
)


class StateRequestHandler(http.server.BaseHTTPRequestHandler):
    """Loopback-only HTTP request handler for StateServer."""

    server: StateServer

    def log_message(self, format: str, *args: typing.Any) -> None:
        """Override to write nothing so tokens never reach logs or stderr."""
        pass

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        """Override to ensure error responses carry security headers."""
        self._send_reply(code, f"Error {code}\n".encode("utf-8"), send_body=True)

    def _send_reply(
        self,
        status_code: int,
        body: bytes,
        content_type: str = "text/plain; charset=utf-8",
        send_body: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; form-action 'none'; "
            "script-src 'self'; style-src 'self'; connect-src 'self'",
        )
        if extra_headers:
            for header_name, header_val in extra_headers.items():
                self.send_header(header_name, header_val)
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _check_host(self, send_body: bool = True) -> bool:
        host_header = self.headers.get("Host")
        if not host_header:
            self._send_reply(403, b"Forbidden\n", send_body=send_body)
            return False
        port = self.server.server_port
        allowed = (f"127.0.0.1:{port}", f"localhost:{port}")
        if host_header.strip().lower() not in allowed:
            self._send_reply(403, b"Forbidden\n", send_body=send_body)
            return False
        return True

    def _handle_get_or_head(self, send_body: bool) -> None:
        if not self._check_host(send_body=send_body):
            return

        parts = self.requestline.split(" ")
        raw = parts[1] if len(parts) == 3 else ""
        raw_path = raw.split("?", 1)[0]
        token = self.server.token

        is_page = False
        is_state = False

        if raw_path.startswith("/") and raw_path.endswith("/"):
            candidate = raw_path[1:-1]
            if candidate and "/" not in candidate:
                if hmac.compare_digest(
                    candidate.encode("utf-8"), token.encode("utf-8")
                ):
                    is_page = True
        elif raw_path.startswith("/") and raw_path.endswith("/state.json"):
            candidate = raw_path[1:-11]
            if candidate and "/" not in candidate:
                if hmac.compare_digest(
                    candidate.encode("utf-8"), token.encode("utf-8")
                ):
                    is_state = True

        if is_page:
            self._send_reply(
                200,
                STATE_SERVER_HTML,
                content_type="text/html; charset=utf-8",
                send_body=send_body,
            )
        elif is_state:
            data = self.server.get_bytes()
            if isinstance(data, str):
                data = data.encode("utf-8")
            self._send_reply(
                200, data, content_type="application/json", send_body=send_body
            )
        else:
            self._send_reply(404, b"Not Found\n", send_body=send_body)

    def do_GET(self) -> None:
        self._handle_get_or_head(send_body=True)

    def do_HEAD(self) -> None:
        self._handle_get_or_head(send_body=False)

    def do_POST(self) -> None:
        if not self._check_host(send_body=True):
            return
        self._send_reply(
            405,
            b"Method Not Allowed\n",
            send_body=True,
            extra_headers={"Allow": "GET, HEAD"},
        )

    def do_PUT(self) -> None:
        if not self._check_host(send_body=True):
            return
        self._send_reply(
            405,
            b"Method Not Allowed\n",
            send_body=True,
            extra_headers={"Allow": "GET, HEAD"},
        )

    def do_PATCH(self) -> None:
        if not self._check_host(send_body=True):
            return
        self._send_reply(
            405,
            b"Method Not Allowed\n",
            send_body=True,
            extra_headers={"Allow": "GET, HEAD"},
        )

    def do_DELETE(self) -> None:
        if not self._check_host(send_body=True):
            return
        self._send_reply(
            405,
            b"Method Not Allowed\n",
            send_body=True,
            extra_headers={"Allow": "GET, HEAD"},
        )

    def do_OPTIONS(self) -> None:
        if not self._check_host(send_body=True):
            return
        self._send_reply(
            405,
            b"Method Not Allowed\n",
            send_body=True,
            extra_headers={"Allow": "GET, HEAD"},
        )


class StateServer(http.server.ThreadingHTTPServer):
    """Loopback-only HTTP server serving monitor state bytes."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[http.server.BaseHTTPRequestHandler] = StateRequestHandler,
        get_bytes: typing.Callable[[], bytes] | None = None,
        token: str | None = None,
    ):
        if server_address[0] != "127.0.0.1":
            raise ValueError("SERVE_HOST_REFUSED")
        self.get_bytes = get_bytes or (lambda: b"{}")
        self.token = token or secrets.token_urlsafe(16)
        super().__init__(server_address, RequestHandlerClass)
        self.server_port = int(self.server_address[1])


def _state_redact(text: typing.Any) -> str:
    """Redact secret-like patterns from exported string values."""
    if not isinstance(text, str):
        return ""
    return SECRETISH.sub("[已遮蔽]", text)


def office_state(
    top: typing.Any,
    owner: typing.Any,
    prs: typing.Any,
    stuck: typing.Any,
    generated_at: typing.Any,
    data_age: typing.Any,
    office: typing.Any = None,
) -> dict[str, typing.Any]:
    """Build the JSON view dictionary from values the screen already displays."""
    top_entries = top if isinstance(top, (list, tuple)) else []
    top_out: list[dict[str, str]] = []
    for item in top_entries:
        if isinstance(item, (tuple, list)):
            t = item[0] if len(item) > 0 else ""
            s = item[1] if len(item) > 1 else ""
        elif isinstance(item, dict):
            t = item.get("text", "")
            s = item.get("style", "")
        else:
            t = str(item)
            s = ""
        if not isinstance(t, str):
            t = ""
        if (
            "brew " in t
            or "pip " in t
            or "install" in t
            or t.startswith("$ ")
            or t.lstrip().startswith("$ ")
        ):
            continue
        top_out.append({
            "text": _state_redact(t),
            "style": _state_redact(s),
        })

    owner_entries = owner if isinstance(owner, (list, tuple)) else []
    owner_out = [_state_redact(x) for x in owner_entries]

    pr_entries = prs if isinstance(prs, (list, tuple)) else []
    prs_out: list[dict[str, typing.Any]] = []
    for row in pr_entries:
        if isinstance(row, dict):
            num = row.get("number", 0)
            review = row.get("review", "")
            ec2 = row.get("ec2", "")
            linux = row.get("linux", "")
            mac = row.get("mac", "")
            next_val = row.get("next", "")
            owner_val = row.get("owner", "")
            current_val = row.get("current", "")
        else:
            num = getattr(row, "number", 0)
            review = getattr(row, "review", "")
            ec2 = getattr(row, "ec2", "")
            linux = getattr(row, "linux", "")
            mac = getattr(row, "mac", "")
            next_val = getattr(row, "next", "")
            owner_val = getattr(row, "owner", "")
            current_val = getattr(row, "current", "")

        if not isinstance(num, int):
            try:
                num = int(num)
            except (ValueError, TypeError):
                num = 0

        prs_out.append({
            "number": num,
            "review": _state_redact(review),
            "ec2": _state_redact(ec2),
            "linux": _state_redact(linux),
            "mac": _state_redact(mac),
            "next": _state_redact(next_val),
            "owner": _state_redact(owner_val),
            "current": _state_redact(current_val),
        })

    stuck_entries = stuck if isinstance(stuck, (list, tuple)) else []
    stuck_out = [_state_redact(x) for x in stuck_entries]

    office_out: list[dict[str, typing.Any]] = []
    if isinstance(office, (list, tuple)):
        for item in office:
            if not isinstance(item, dict):
                continue
            sec = item.get("section")
            section = sec if sec in ("live", "status") else "live"

            d = item.get("depth")
            depth = d if type(d) is int and not isinstance(d, bool) and d >= 0 else 0

            name = _state_redact(item.get("name", ""))
            state = _state_redact(item.get("state", ""))
            detail = _state_redact(item.get("detail", ""))
            age = _state_redact(item.get("age", ""))

            b = item.get("band")
            band = b if b in ("working", "alert", "idle", "error") else "error"

            r = item.get("room")
            room = r if r in ("pm", "developer", "reviewer", "machine", "other") else "other"

            office_out.append({
                "section": section,
                "depth": depth,
                "name": name,
                "state": state,
                "detail": detail,
                "age": age,
                "band": band,
                "room": room,
            })

    age_val = None
    if data_age is not None:
        try:
            age_val = float(data_age)
        except (ValueError, TypeError):
            age_val = None

    return {
        "schema_version": 1,
        "generated_at": _state_redact(generated_at),
        "data_age": age_val,
        "top": top_out,
        "owner": owner_out,
        "prs": prs_out,
        "stuck": stuck_out,
        "office": office_out,
    }


def make_state_server(
    get_bytes: typing.Callable[[], bytes],
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[StateServer, str]:
    """Create and bind a loopback state server returning (server, url)."""
    if host != "127.0.0.1":
        raise ValueError("SERVE_HOST_REFUSED")
    token = secrets.token_urlsafe(16)
    server = StateServer(
        (host, port), StateRequestHandler, get_bytes=get_bytes, token=token
    )
    url = f"http://127.0.0.1:{server.server_port}/{token}/"
    return server, url


def main(argv: list[str] | None = None) -> int:
    # #1234: GitHub reads always use the AI login (gh-ai config), never the
    # Owner's default login; if gh-ai is missing, reads fail instead.
    os.environ["GH_CONFIG_DIR"] = os.path.expanduser("~/.config/gh-ai")
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        os.environ.pop(name, None)
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
                        help="compatibility option; GitHub Activity is already on by default")
    parser.add_argument("--no-github", action="store_true",
                        help=f"turn off the read-only gh api Activity on {DEFAULT_GITHUB_REPO} (no network)")
    parser.add_argument("--gh-cli", action="store_true",
                        help="read GitHub only through `gh api` processes (never holds gh's token; more CPU)")
    parser.add_argument("--clear-rule-alert", type=int, metavar="PR",
                        help="clear one local sensitive-path alert by PR number")
    parser.add_argument("--codex-quota", action="store_true",
                        help="print the Codex quota status as one JSON line, then exit")
    args = parser.parse_args(argv)
    if args.codex_quota:
        print(json.dumps(read_codex_quota(time.time()), ensure_ascii=False))
        return 0
    if args.clear_rule_alert is not None:
        try:
            state = _load_rule_alerts(RULE_ALERT_FILE)
        except Exception:
            print("RULE_ALERT_RECORD_BAD")
            return 1
        number = args.clear_rule_alert
        if state is None or not any(alert["pr"] == number for alert in state["alerts"]):
            print(f"RULE_ALERT_NOT_FOUND pr={number}")
            return 1
        state["alerts"] = [alert for alert in state["alerts"] if alert["pr"] != number]
        if number not in state["checked"]:
            state["checked"] = (state["checked"] + [number])[-500:]
        try:
            _save_rule_alerts(RULE_ALERT_FILE, state)
        except Exception:
            print("RULE_ALERT_RECORD_BAD")
            return 1
        print(f"RULE_ALERT_CLEARED pr={number}")
        return 0
    if not 0.5 <= args.interval <= 60:
        parser.error("--interval must be between 0.5 and 60 seconds")
    github = None if args.no_github else GitHubSource(DEFAULT_GITHUB_REPO,
                                                       direct=not (args.once or args.gh_cli))
    if github is not None:
        github.want_main = True
        github.want_hosted_ci = True
    if args.once:
        return run_once(github)
    mon = Monitor(min_age=max(0.0, args.min_age), show_all=args.all)
    mon.agents = ClaudeAgents()
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
