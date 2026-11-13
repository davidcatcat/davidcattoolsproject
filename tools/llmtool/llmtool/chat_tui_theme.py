"""Themes, the TUI settings file and OSC 8 links for the full-screen chat (W56d).

* Theme: ``/theme dark|light|auto``. ``auto`` reads the terminal background from
  COLORFGBG ("fg;bg", the last field is the background colour index) and is dark when
  that is missing or unclear. The style tables themselves live in ``chat_tui_view``.
* Settings: the choice is kept in ``~/.local/state/llmtool/tui-settings.json`` (the
  llmtool state dir), written atomically with mode 0600; a missing, foreign or malformed
  file is ignored and the defaults apply.
* Links: on terminals known to support OSC 8 hyperlinks (iTerm2, WezTerm, Warp, kitty),
  ``path/file.py:123`` becomes a file:// link and ``#123`` a link to that issue/PR of the
  git remote's GitHub repository. Links are display-only: the pane text, selection and
  copy are unchanged. Elsewhere no OSC 8 sequence is ever written.

The pane writes each linked cell as an OSC 8 open sequence (a prompt_toolkit zero-width
escape) followed by the cell. prompt_toolkit redraws only changed cells, so a run's
closing sequence may never be written; ``install_osc8_guard`` therefore closes the link
on the output itself right after each linked cell, before anything else is written.
"""

import json
import os
import re
import socket
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

THEMES = ("dark", "light")
THEME_CHOICES = THEMES + ("auto",)
DEFAULT_SETTINGS = {"theme": "auto", "links": "on", "auto_resume": "on"}
SETTINGS_NAME = "tui-settings.json"
SETTINGS_MAX_BYTES = 4096
# TERM_PROGRAM values (and TERM) of terminals with OSC 8 hyperlinks.
OSC8_PROGRAMS = ("iTerm.app", "WezTerm", "WarpTerminal", "kitty")
OSC8_TERMS = ("xterm-kitty",)
OSC8_CLOSE = "\x1b]8;;\x1b\\"
LINK_IDS = 4096          # Link ids kept before the table starts again.
LINK_CONTEXT = 256       # Characters around a row searched for a link that crosses it.
GIT_SECONDS = 2.0
# A source path with a line number: "dir/file.py:12" or "file.md:3:7". A bare name needs
# a known extension, so "example.com:443" or "1.5:30" stay plain text.
FILE_LINK = re.compile(r"(?<![\w./~-])((?:~/|/|\.\.?/)?(?:[\w.-]+/)*[\w-][\w.-]*"
                       r"\.([A-Za-z][A-Za-z0-9]{0,7})):(\d{1,7})(?::\d{1,5})?(?![\w/])")
FILE_EXTENSIONS = frozenset((
    "py", "md", "json", "yaml", "yml", "toml", "txt", "sh", "js", "ts", "tsx", "jsx", "go",
    "rs", "c", "h", "cc", "cpp", "java", "rb", "html", "css", "cfg", "ini", "tsv", "csv"))
ISSUE_LINK = re.compile(r"(?<![\w&#/])#(\d{1,7})(?![\w-])")
_GITHUB_REMOTE = re.compile(r"^(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
                            r"([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")


def auto_theme(environ=None):
    """"light" when COLORFGBG names a light background (7 or 9–15), else "dark"."""
    environ = os.environ if environ is None else environ
    background = environ.get("COLORFGBG", "").split(";")[-1].strip()
    if not background.isdigit():
        return "dark"
    index = int(background)
    return "light" if index == 7 or 9 <= index <= 15 else "dark"


def resolve_theme(choice, environ=None):
    """The theme a choice draws with: dark, light, or auto's reading of the terminal."""
    return choice if choice in THEMES else auto_theme(environ)


def osc8_supported(environ=None):
    """True only on terminals known to render OSC 8 hyperlinks (never under tmux/screen)."""
    environ = os.environ if environ is None else environ
    if environ.get("TMUX") or environ.get("TERM", "").startswith("screen"):
        return False
    return (environ.get("TERM_PROGRAM", "") in OSC8_PROGRAMS
            or environ.get("TERM", "") in OSC8_TERMS)


# -- settings file ---------------------------------------------------------------------
def settings_path():
    return Path.home() / ".local" / "state" / "llmtool" / SETTINGS_NAME


def load_settings(path=None):
    """Saved choices merged over DEFAULT_SETTINGS; anything unreadable is ignored."""
    settings = dict(DEFAULT_SETTINGS)
    path = settings_path() if path is None else Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return settings
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_size > SETTINGS_MAX_BYTES):
            return settings
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = json.loads(stream.read(SETTINGS_MAX_BYTES).decode("utf-8"))
    except (OSError, ValueError):
        return settings
    finally:
        os.close(fd)
    if type(data) is dict:
        if data.get("theme") in THEME_CHOICES:
            settings["theme"] = data["theme"]
        if data.get("links") in ("on", "off"):
            settings["links"] = data["links"]
        if data.get("auto_resume") in ("on", "off"):
            settings["auto_resume"] = data["auto_resume"]
    return settings


def save_settings(settings, path=None):
    """Write the settings atomically (temp file, fsync, rename) with mode 0600."""
    path = settings_path() if path is None else Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = {key: settings[key] for key in DEFAULT_SETTINGS if key in settings}
    fd, temporary = tempfile.mkstemp(prefix=".tui-settings-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(value, stream, sort_keys=True)
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


# -- links -----------------------------------------------------------------------------
def repo_info(cwd=None):
    """(git top-level directory or None, "owner/repo" of a GitHub origin or None).

    Links are an extra: any failure of git (missing, slow, odd output) gives (None, None).
    It can take 2 x GIT_SECONDS, so the TUI calls it off the UI thread.
    """
    def git(*args):
        try:
            done = subprocess.run(("git",) + args, cwd=cwd, capture_output=True, text=True,
                                  timeout=GIT_SECONDS, stdin=subprocess.DEVNULL, check=False)
            if done.returncode != 0 or not isinstance(done.stdout, str):
                return None
            return done.stdout.strip() or None
        except Exception:
            return None

    # Only an absolute, existing directory is a repo root; anything else is no repo info.
    top = git("rev-parse", "--show-toplevel")
    if not top or not os.path.isabs(top) or not os.path.isdir(top):
        return None, None
    match = _GITHUB_REMOTE.match(git("config", "--get", "remote.origin.url") or "")
    return os.path.realpath(top), match.group(1) if match else None


class Linker:
    """Finds linkable references in the pane text and gives their OSC 8 sequences."""

    def __init__(self, base_dir, repo_slug=None, host=None):
        self.base_dir = str(base_dir)
        self.repo_slug = repo_slug
        self.host = socket.gethostname() if host is None else host
        self._ids = {}

    def file_url(self, path):
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(self.base_dir, path)
        return "file://" + quote(self.host, safe="") + quote(os.path.normpath(path), safe="/")

    def issue_url(self, number):
        return "https://github.com/%s/issues/%d" % (self.repo_slug, int(number))

    def links(self, text, start, end):
        """[(low, high, url)] of the references that overlap text[start:end]."""
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        line_end = len(text) if line_end < 0 else line_end
        low_bound = max(line_start, start - LINK_CONTEXT)
        high_bound = min(line_end, end + LINK_CONTEXT)
        window = text[low_bound:high_bound]
        found = []
        for match in FILE_LINK.finditer(window):
            path = match.group(1)
            if "/" in path or match.group(2).lower() in FILE_EXTENSIONS:
                found.append((low_bound + match.start(), low_bound + match.end(),
                              self.file_url(path)))
        if self.repo_slug:
            for match in ISSUE_LINK.finditer(window):
                found.append((low_bound + match.start(), low_bound + match.end(),
                              self.issue_url(match.group(1))))
        found.sort()
        result, last = [], -1
        for low, high, url in found:
            # A match cut by the search window (not at a line edge) is not trusted.
            if (low == low_bound and low_bound != line_start) or (
                    high == high_bound and high_bound != line_end):
                continue
            if low >= last and high > start and low < end:
                result.append((low, high, url))
                last = high
        return result

    def open_sequence(self, url):
        """OSC 8 open with an id, so a terminal highlights a wrapped link as one."""
        if url not in self._ids:
            if len(self._ids) >= LINK_IDS:
                self._ids.clear()
            self._ids[url] = len(self._ids) + 1
        return "\x1b]8;id=llmtool-%d;%s\x1b\\" % (self._ids[url], url)


def install_osc8_guard(output):
    """Close an OSC 8 link right after the one cell written behind its open sequence.

    Wraps the output's methods (as ``chat_tui._enable_mouse_drag_reporting`` does): after
    an open sequence and one written cell, any further write, cursor move, attribute
    change or flush first writes OSC8_CLOSE, unless it is the next cell's own open
    sequence. A link therefore never covers a cell that did not ask for it.
    """
    state = {"open": False, "written": False}
    raw = output.write_raw

    def close():
        if state["open"] and state["written"]:
            raw(OSC8_CLOSE)
            state["open"] = state["written"] = False

    def write_raw(data):
        if data.startswith("\x1b]8;") and data != OSC8_CLOSE:
            state["open"], state["written"] = True, False   # a new open replaces the old
        else:
            close()
        raw(data)

    def wrap(name):
        method = getattr(output, name, None)
        if method is None:
            return

        def wrapped(*args, **kwargs):
            close()
            result = method(*args, **kwargs)
            if name == "write" and state["open"]:
                state["written"] = True
            return result
        setattr(output, name, wrapped)

    output.write_raw = write_raw
    for name in ("write", "cursor_goto", "cursor_up", "cursor_down", "cursor_forward",
                 "cursor_backward", "erase_end_of_line", "erase_down", "erase_screen",
                 "reset_attributes", "set_attributes", "flush", "hide_cursor", "show_cursor",
                 "quit_alternate_screen", "disable_mouse_support"):
        wrap(name)
