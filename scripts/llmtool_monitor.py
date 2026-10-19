#!/usr/bin/env python3
"""Read-only macOS/Linux process monitor for local AI agent jobs.

Shows OpenCode, Claude Code, Codex and llmtool processes. No model calls,
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
    watched_groups: frozenset  # agent-led groups to keep following next poll
    lingering: list            # watched groups whose leader is gone/zombie


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    pgid: int
    elapsed: str
    state: str
    role: str


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


def parse_processes(output: str, self_pid: int,
                    remembered: frozenset = frozenset()) -> Snapshot:
    """remembered: PGIDs of agent-led groups seen in earlier polls. A leader that
    exited and was reaped leaves members whose only link is that PGID."""
    rows = {}
    for line in output.splitlines():
        found = LINE.match(line)
        if found is None:
            continue
        pid, ppid, pgid = (int(v) for v in found.group(1, 2, 3))
        if pid == self_pid or ppid == self_pid:  # this monitor and its own ps
            continue
        elapsed = found.group(4)
        stat = found.group(5)
        rows[pid] = (ppid, pgid,
                     elapsed if len(elapsed) <= 18 else "UNKNOWN",
                     stat if ALLOWED_STATE.fullmatch(stat) else "?",
                     classify(found.group(6)))
    matched = {pid for pid, row in rows.items()
               if row[4] is not None and row[4] not in NO_EXPAND}
    # Expand a whole process group only when the agent leads it (as llmtool's
    # start_new_session children do); otherwise unrelated apps would flood in.
    groups = {rows[pid][1] for pid in matched if rows[pid][1] == pid}
    present = {row[1] for row in rows.values()}
    watched = groups | {g for g in remembered if g in present}

    def descends_from_match(pid: int) -> bool:
        seen = 0
        parent = rows[pid][0]
        while parent in rows and seen < MAX_ANCESTRY:
            if parent in matched:
                return True
            parent = rows[parent][0]
            seen += 1
        return False

    result = []
    for pid, (ppid, pgid, elapsed, stat, role) in rows.items():
        if role is None:
            if pgid in groups:
                role = "  group member"
            elif pgid in watched:
                role = "  orphaned group member"
            elif descends_from_match(pid):
                role = "  descendant"
            else:
                continue
        if stat.startswith("Z"):
            role += " (zombie)"
        result.append(Process(pid, ppid, pgid, elapsed, stat, role))
    lingering = sorted(
        g for g in watched
        if any(pid != g and row[1] == g for pid, row in rows.items())
        and (g not in rows or rows[g][3].startswith("Z")))
    # Group leaders first, then their members, within each process group.
    result.sort(key=lambda p: (p.pgid, p.pid != p.pgid, p.ppid, p.pid))
    return Snapshot(result, frozenset(watched), lingering)


def collect(remembered: frozenset = frozenset()) -> Snapshot:
    response = subprocess.run(PS_ARGS, check=False, capture_output=True, text=True,
                              timeout=4, shell=False)
    if response.returncode:
        raise RuntimeError(f"ps exited with status {response.returncode}")
    return parse_processes(response.stdout, os.getpid(), remembered)


def events(previous: dict[int, Process], current: dict[int, Process]) -> list[str]:
    # Lifecycle is limited to visibility in ps. Disappeared != completed.
    added = [f"Detected {p.role.strip()}  PID {p.pid} (PGID {p.pgid})"
             for pid, p in current.items() if pid not in previous]
    gone = [f"No longer observed {p.role.strip()}  PID {p.pid} (PGID {p.pgid})"
            for pid, p in previous.items() if pid not in current]
    return added + gone


def render(current: list[Process], history: deque[str], error: str | None,
           use_color: bool, iterations: int, lingering: list | None = None) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    esc = "\x1b[96m" if use_color else ""
    warn = "\x1b[93m" if use_color else ""
    end = "\x1b[0m" if use_color else ""
    out = [f"{esc}AI agents (OpenCode / Claude / Codex / llmtool) · Local Process Monitor{end}",
           f"Observed at: {now}    Polls: {iterations}",
           "Read-only OS snapshot · no AI/API calls · Ctrl+C to quit", ""]
    if error:
        out.append(f"STATUS: UNKNOWN (unable to read process table: {error})")
    elif not current:
        out.append("STATUS: NO MATCHING LOCAL PROCESS OBSERVED")
        out.append("This does NOT mean a queued or remote task is complete or idle.")
    else:
        out.append(f"STATUS: {len(current)} matching local process(es) observed")
        if lingering:
            out.append(f"{warn}NOTE: group(s) {', '.join(map(str, lingering))} still have members "
                       f"while the leader is gone/zombie (possible cleanup UNKNOWN){end}")
        out.append("ROLE                         PID     PPID    PGID    ELAPSED       OS STATE")
        out.append("-" * 79)
        for p in current[:50]:
            out.append(f"{p.role:<28} {p.pid:<7} {p.ppid:<7} {p.pgid:<7} {p.elapsed:<13} {p.state}")
        if len(current) > 50:
            out.append(f"... {len(current)-50} additional processes not shown")
    out += ["", "RECENT OBSERVATIONS (this monitor session only)"]
    if history:
        out.extend("  " + e for e in list(history)[-10:])
    else:
        out.append("  No process arrival/departure observed yet")
    out += ["", "Note: process RUNNING != model actively generating; a missing process != task DONE."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between snapshots (default 1; minimum 0.5)")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI title color")
    args = parser.parse_args(argv)
    if not 0.5 <= args.interval <= 60:
        parser.error("--interval must be between 0.5 and 60 seconds")
    prev: dict[int, Process] = {}
    remembered: frozenset = frozenset()
    history: deque[str] = deque(maxlen=10)
    iteration = 0
    use_color = sys.stdout.isatty() and not args.no_color and not os.environ.get("NO_COLOR")
    try:
        while True:
            iteration += 1
            try:
                result = collect(remembered)
                snapshot, lingering = result.processes, result.lingering
                remembered = result.watched_groups
                current = {p.pid: p for p in snapshot}
                for entry in events(prev, current):
                    history.append(entry)
                prev = current
                error = None
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                snapshot, lingering = [], []
                error = type(exc).__name__ + ": " + str(exc)[:65]
                # On a failed read, do not generate fake departure events.
            if not args.once and sys.stdout.isatty():
                sys.stdout.write("\x1b[2J\x1b[H")
            print(render(snapshot, history, error, use_color, iteration, lingering), flush=True)
            if args.once:
                return 0 if error is None else 1
            if not sys.stdout.isatty():
                print("\n" + "-" * 79, flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped; no worker was signalled or modified.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
