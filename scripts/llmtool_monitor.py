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


def family(role: str) -> str:
    for name in ("OpenCode", "Claude", "Codex", "llmtool", "ai-loop"):
        if name in role:
            return name
    return "other"


def events(previous: dict[int, Process], current: dict[int, Process],
           old_lingering: list, lingering: list) -> list[tuple[str, str]]:
    """(kind, text). Lifecycle is limited to visibility in ps; gone != completed."""
    out = [("+", f"{p.role.strip()}  PID {p.pid}  PGID {p.pgid}")
           for pid, p in current.items() if pid not in previous]
    out += [("-", f"{p.role.strip()}  PID {p.pid}  no longer observed")
            for pid, p in previous.items() if pid not in current]
    out += [("!", f"group {g}: leader gone/zombie while members remain (possible cleanup UNKNOWN)")
            for g in lingering if g not in old_lingering]
    out += [("ok", f"group {g}: no lingering members any more")
            for g in old_lingering if g not in lingering]
    return out


def summary(processes: list[Process], lingering: list) -> str:
    counts: dict[str, int] = {}
    for p in processes:
        if not p.role.startswith(" "):
            counts[family(p.role)] = counts.get(family(p.role), 0) + 1
    text = "  ".join(f"{k} {counts[k]}" for k in ("Claude", "Codex", "OpenCode", "llmtool", "ai-loop", "other")
                     if k in counts) or "no agent process"
    return text + (f"  ·  ⚠ lingering groups: {', '.join(map(str, lingering))}" if lingering else "")


def table_lines(processes: list[Process]) -> list[str]:
    lines = ["ROLE                         PID     PPID    PGID    ELAPSED       STATE"]
    lines += [f"{p.role:<28} {p.pid:<7} {p.ppid:<7} {p.pgid:<7} {p.elapsed:<13} {p.state}"
              for p in processes]
    return lines


class Monitor:
    """Polling state shared by every view. Never signals or modifies anything."""

    def __init__(self) -> None:
        self.prev: dict[int, Process] = {}
        self.remembered: frozenset = frozenset()
        self.processes: list[Process] = []
        self.lingering: list = []
        self.error: str | None = None
        self.polls = 0
        self.log: deque[tuple[str, str, str]] = deque(maxlen=2000)  # (time, kind, text)

    def poll(self) -> list[tuple[str, str, str]]:
        self.polls += 1
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
        new = [(stamp, k, t) for k, t in events(self.prev, current, self.lingering, result.lingering)]
        if self.polls == 1:
            new.insert(0, (stamp, "i", f"monitor started; {summary(result.processes, result.lingering)}"))
        self.prev, self.remembered = current, result.watched_groups
        self.processes, self.lingering, self.error = result.processes, result.lingering, None
        self.log.extend(new)
        return new


MARK = {"+": "+", "-": "-", "!": "!", "ok": "✓", "?": "?", "i": "·"}
FOOTNOTE = "running != model generating; gone != task done"


def run_once() -> int:
    mon = Monitor()
    mon.poll()
    print(f"AI agents · {datetime.now():%Y-%m-%d %H:%M:%S} · {summary(mon.processes, mon.lingering)}")
    if mon.error:
        print(f"STATUS: UNKNOWN (unable to read process table: {mon.error})")
        return 1
    print("\n".join(table_lines(mon.processes)))
    print(f"\nNote: {FOOTNOTE}.")
    return 0


def run_plain(interval: float) -> int:
    """Append-only stream: one line per change, never a repainted screen."""
    mon = Monitor()
    print(f"AI agents · read-only process monitor · {FOOTNOTE} · Ctrl+C to quit", flush=True)
    try:
        while True:
            for stamp, kind, text in mon.poll():
                print(f"{stamp} {MARK.get(kind, ' ')} {text}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped; no worker was signalled or modified.")
        return 0


def run_curses(stdscr, interval: float, use_color: bool) -> None:
    import curses
    curses.curs_set(0)
    stdscr.timeout(100)
    stdscr.keypad(True)
    colors = {}
    if use_color and curses.has_colors():
        curses.use_default_colors()
        for n, (key, fg) in enumerate((("title", curses.COLOR_CYAN), ("!", curses.COLOR_YELLOW),
                                       ("+", curses.COLOR_GREEN), ("-", curses.COLOR_MAGENTA),
                                       ("?", curses.COLOR_RED), ("ok", curses.COLOR_GREEN)), start=1):
            curses.init_pair(n, fg, -1)
            colors[key] = curses.color_pair(n)
    mon = Monitor()
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
        put(1, status, colors.get("!", 0) if (mon.error or mon.lingering) else 0)
        if view == "log":
            entries = list(mon.log)
            offset = max(0, min(offset, len(entries) - body))
            end = len(entries) - offset
            for row, (stamp, kind, text) in enumerate(entries[max(0, end - body):end]):
                put(3 + row, f"{stamp} {MARK.get(kind, ' ')} {text}", colors.get(kind, 0))
            hint = "Tab: processes  ↑↓/PgUp/PgDn: scroll  End: follow  q: quit"
            if offset:
                hint = f"[scrolled back {offset}]  " + hint
        else:
            lines = table_lines(mon.processes) if not mon.error else ["(process table unavailable)"]
            offset = max(0, min(offset, len(lines) - body))
            for row, line in enumerate(lines[offset:offset + body]):
                put(3 + row, line, curses.A_BOLD if offset + row == 0 else 0)
            hint = "Tab: event log  ↑↓/PgUp/PgDn: scroll  q: quit"
        put(h - 1, f"{hint}  ·  {FOOTNOTE}", curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return
        if key in (9, ord("p"), ord("l")):  # Tab, or p/l to pick processes/log
            view = "table" if (key == ord("p") or (key == 9 and view == "log")) else "log"
            offset = 0
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
    args = parser.parse_args(argv)
    if not 0.5 <= args.interval <= 60:
        parser.error("--interval must be between 0.5 and 60 seconds")
    if args.once:
        return run_once()
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "dumb") != "dumb"
    if args.plain or not interactive:
        return run_plain(args.interval)
    import curses
    use_color = not args.no_color and not os.environ.get("NO_COLOR")
    try:
        curses.wrapper(run_curses, args.interval, use_color)
    except KeyboardInterrupt:
        pass
    print("Monitor stopped; no worker was signalled or modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
