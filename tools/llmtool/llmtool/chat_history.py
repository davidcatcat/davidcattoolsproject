"""Private, bounded persistence for interactive chat turns."""

import json
import os
import stat
import tempfile
from pathlib import Path

MAX_TURNS = 200
MAX_BYTES = 256 * 1024
_NOTICE = "chat history was unavailable; continuing without saved history"


class ChatHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.notice = None
        self.enabled = True

    def _notice(self):
        self.notice = _NOTICE
        self.enabled = False

    def _directory(self, create=False):
        path = self.path.parent
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            return False
        if info.st_uid != os.getuid():
            return False
        if create and stat.S_IMODE(info.st_mode) != 0o700:
            os.chmod(path, 0o700, follow_symlinks=False)
            info = path.lstat()
        return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700

    def load(self):
        if not self.enabled:
            return []
        try:
            if not self._directory(create=True):
                self._notice()
                return []
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError):
            self._notice()
            return []
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > MAX_BYTES):
                self._notice()
                return []
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                self._notice()
                return []
            value = json.loads(raw.decode("utf-8"))
            if type(value) is not list or len(value) > MAX_TURNS * 2:
                self._notice()
                return []
            turns = []
            if len(value) % 2:
                self._notice()
                return []
            for index, item in enumerate(value):
                if (type(item) is not dict or set(item) != {"role", "content"}
                        or item["role"] != ("user" if index % 2 == 0 else "assistant")
                        or type(item["content"]) is not str):
                    self._notice()
                    return []
                turns.append({"role": item["role"], "content": item["content"]})
            return self._screen(turns)
        except (OSError, ValueError, UnicodeError, TypeError):
            self._notice()
            return []
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _screen(turns):
        from .task_submission_cli import _redact_rejected_output
        from .test_output_excerpts import _emitted_sensitive

        screened = []
        for item in turns:
            content = item["content"]
            redacted = _redact_rejected_output(content)
            if (redacted.startswith("[REDACTED") or _emitted_sensitive(content)
                    or _emitted_sensitive(redacted)):
                content = "[withheld]"
            screened.append({"role": item["role"], "content": content})
        return screened

    def save(self, turns):
        if not self.enabled:
            return
        try:
            if not self._directory(create=True):
                raise OSError("unsafe history directory")
            current = turns[-MAX_TURNS * 2:]
            screened = self._screen(current)
            while True:
                payload = json.dumps(screened, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                if len(payload) <= MAX_BYTES or not screened:
                    break
                screened = screened[2:]
            try:
                info = self.path.lstat()
            except FileNotFoundError:
                info = None
            if info is not None and (not stat.S_ISREG(info.st_mode)
                                     or info.st_uid != os.getuid() or info.st_nlink != 1):
                raise OSError("unsafe history file")
            fd, temporary = tempfile.mkstemp(prefix=".history-", dir=str(self.path.parent))
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    fd = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if fd >= 0:
                    os.close(fd)
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        except (OSError, ValueError, TypeError, UnicodeError):
            self._notice()

    def clear(self):
        self.save([])
