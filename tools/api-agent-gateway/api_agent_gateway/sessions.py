"""Session state: per-user 0700 directory, 0600 files, lock, retention.

Layout under the state directory::

    sessions/<id>/meta.json     content-free binding and timestamps
    sessions/<id>/history.json  model-visible conversation (sensitive)
    sessions/<id>/lock          flock target for the single-run lock
    tombstones/<id>.json        content-free record after deletion
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import time
from typing import Any, Optional

from .contract import POLICY_REFUSED, Refusal

_ID = re.compile(r'[0-9a-f]{32}')
_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
END_REASONS = ('ended', 'task_terminal', 'expired', 'corrupt')


def new_session_id() -> str:
    return secrets.token_hex(16)


def valid_id(session_id: str) -> bool:
    return isinstance(session_id, str) and bool(_ID.fullmatch(session_id))


def _iso(ts: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))


def _secure_dir(path: str) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) & 0o077):
        raise Refusal(POLICY_REFUSED, 'STATE_DIR_UNSAFE')


def _write_json(dir_path: str, name: str, obj: Any) -> None:
    tmp = os.path.join(dir_path, f'.{name}.{secrets.token_hex(6)}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC, 0o600)
    try:
        data = json.dumps(obj, sort_keys=True).encode('utf-8')
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, os.path.join(dir_path, name))


def _read_json(path: str) -> Any:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | _CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o077:
            raise ValueError('unsafe state file')
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    return json.loads(b''.join(chunks).decode('utf-8'))


def _inside_git_worktree(path: str) -> bool:
    cur = os.path.realpath(path)
    while True:
        if os.path.lexists(os.path.join(cur, '.git')):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


class Lock:
    def __init__(self, fd: int):
        self.fd = fd

    def release(self) -> None:
        if self.fd >= 0:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = -1


class Store:
    def __init__(self, state_dir: str, clock=time.time):
        self.root = os.path.abspath(state_dir)
        self.clock = clock
        parent = os.path.dirname(self.root)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        _secure_dir(self.root)
        if _inside_git_worktree(self.root):
            raise Refusal(POLICY_REFUSED, 'STATE_DIR_IN_WORKTREE')
        self.sessions_dir = os.path.join(self.root, 'sessions')
        self.tombstones_dir = os.path.join(self.root, 'tombstones')
        _secure_dir(self.sessions_dir)
        _secure_dir(self.tombstones_dir)

    def _dir(self, sid: str) -> str:
        if not valid_id(sid):
            raise Refusal(POLICY_REFUSED, 'SESSION_NOT_FOUND')
        return os.path.join(self.sessions_dir, sid)

    def tombstone_path(self, sid: str) -> str:
        return os.path.join(self.tombstones_dir, sid + '.json')

    # -- lifecycle -----------------------------------------------------------
    def create(self, meta: dict) -> tuple[str, Lock]:
        sid = new_session_id()
        path = self._dir(sid)
        os.mkdir(path, 0o700)
        lock = self.lock(sid)
        now = self.clock()
        meta = dict(meta, session_id=sid, created_at=now, last_turn_at=now, status='active')
        _write_json(path, 'meta.json', meta)
        return sid, lock

    def lock(self, sid: str) -> Lock:
        path = os.path.join(self._dir(sid), 'lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | _CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise Refusal(POLICY_REFUSED, 'SESSION_LOCKED') from exc
        return Lock(fd)

    def load_meta(self, sid: str) -> dict:
        if os.path.lexists(self.tombstone_path(sid)):
            raise Refusal(POLICY_REFUSED, 'SESSION_ENDED')
        path = self._dir(sid)
        try:
            meta = _read_json(os.path.join(path, 'meta.json'))
        except FileNotFoundError as exc:
            raise Refusal(POLICY_REFUSED, 'SESSION_NOT_FOUND') from exc
        except (OSError, ValueError) as exc:
            raise Refusal(POLICY_REFUSED, 'SESSION_STATE_INVALID') from exc
        if not isinstance(meta, dict) or meta.get('session_id') != sid or meta.get('status') != 'active':
            raise Refusal(POLICY_REFUSED, 'SESSION_STATE_INVALID')
        return meta

    def save_meta(self, sid: str, meta: dict) -> None:
        _write_json(self._dir(sid), 'meta.json', meta)

    def load_history(self, sid: str) -> dict:
        try:
            hist = _read_json(os.path.join(self._dir(sid), 'history.json'))
        except (OSError, ValueError) as exc:
            raise Refusal(POLICY_REFUSED, 'SESSION_STATE_INVALID') from exc
        if not isinstance(hist, dict):
            raise Refusal(POLICY_REFUSED, 'SESSION_STATE_INVALID')
        return hist

    def save_history(self, sid: str, history: dict) -> None:
        _write_json(self._dir(sid), 'history.json', history)

    def expired(self, meta: dict) -> bool:
        try:
            hours = min(int(meta['expiry_hours']), 72)
            return self.clock() >= float(meta['last_turn_at']) + hours * 3600
        except (KeyError, TypeError, ValueError):
            return True

    def delete(self, sid: str, reason: str, meta: Optional[dict] = None) -> dict:
        """Delete conversation state, leaving a content-free tombstone.

        The caller must hold the session lock.
        """
        if reason not in END_REASONS:
            raise ValueError(reason)
        meta = meta or {}
        tomb = {
            'session_id': sid,
            'created_at': _iso(meta['created_at']) if isinstance(meta.get('created_at'), (int, float)) else None,
            'last_turn_at': _iso(meta['last_turn_at']) if isinstance(meta.get('last_turn_at'), (int, float)) else None,
            'ended_at': _iso(self.clock()),
            'end_reason': reason,
        }
        if not os.path.lexists(self.tombstone_path(sid)):
            _write_json(self.tombstones_dir, sid + '.json', tomb)
        path = self._dir(sid)
        for name in sorted(os.listdir(path)):
            if name != 'lock':
                os.unlink(os.path.join(path, name))
        os.unlink(os.path.join(path, 'lock'))
        os.rmdir(path)
        return tomb

    def end(self, sid: str, reason: str) -> dict:
        if os.path.lexists(self.tombstone_path(sid)) and not os.path.lexists(self._dir(sid)):
            raise Refusal(POLICY_REFUSED, 'SESSION_ENDED')
        if not os.path.isdir(self._dir(sid)):
            raise Refusal(POLICY_REFUSED, 'SESSION_NOT_FOUND')
        lock = self.lock(sid)
        try:
            try:
                meta = _read_json(os.path.join(self._dir(sid), 'meta.json'))
            except (OSError, ValueError):
                meta = {}
            return self.delete(sid, reason, meta if isinstance(meta, dict) else {})
        finally:
            lock.release()

    def sweep(self) -> list[str]:
        """Expire sessions; also finish deletions interrupted after a tombstone."""
        removed = []
        for sid in sorted(os.listdir(self.sessions_dir)):
            if not valid_id(sid):
                continue
            path = os.path.join(self.sessions_dir, sid)
            try:
                meta = _read_json(os.path.join(path, 'meta.json'))
            except (OSError, ValueError):
                meta = None
            tombstoned = os.path.lexists(self.tombstone_path(sid))
            if not tombstoned and isinstance(meta, dict) and not self.expired(meta):
                continue
            try:
                lock = self.lock(sid)
            except Refusal:
                continue
            try:
                reason = 'expired' if isinstance(meta, dict) else 'corrupt'
                self.delete(sid, reason, meta if isinstance(meta, dict) else {})
                removed.append(sid)
            finally:
                lock.release()
        return removed

    def listing(self) -> list[dict]:
        out = []
        for sid in sorted(os.listdir(self.sessions_dir)):
            if not valid_id(sid):
                continue
            try:
                meta = _read_json(os.path.join(self.sessions_dir, sid, 'meta.json'))
            except (OSError, ValueError):
                continue
            try:
                self.lock(sid).release()
                running = False
            except Refusal:
                running = True
            out.append({
                'session_id': sid,
                'status': 'running' if running else 'active',
                'created_at': _iso(meta['created_at']),
                'last_turn_at': _iso(meta['last_turn_at']),
                'expires_at': _iso(meta['last_turn_at'] + meta['expiry_hours'] * 3600),
                'workdir': meta['workdir'],
                'task_id': meta['task_id'],
                'profile': meta['profile'],
                'turns': meta.get('turns', 0),
                'runs': meta.get('runs', 0),
                'last_status': meta.get('last_status'),
            })
        for name in sorted(os.listdir(self.tombstones_dir)):
            if not name.endswith('.json') or not valid_id(name[:-5]):
                continue
            try:
                tomb = _read_json(os.path.join(self.tombstones_dir, name))
            except (OSError, ValueError):
                continue
            out.append(dict(tomb, status='ended'))
        return out
