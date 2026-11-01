"""Race-safe confinement of file access to declared read/write sets.

The workdir is opened once as a directory descriptor. Every path component is
opened relative to its parent descriptor with ``O_NOFOLLOW``; symlinks, ``..``,
absolute paths, ``.git``, special files, hard-linked files and cross-device
directories are refused. Writes go through a temp file in the same directory
descriptor plus ``renameat`` (or ``linkat`` for exclusive creation) and the
whole component chain is re-verified before and after.
"""
from __future__ import annotations

import errno
import os
import secrets
import stat
from typing import Callable, Iterable, Optional

_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC
TEMP_PREFIX = '.gw-agent-tmp-'
MAX_PATH_LEN = 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_WRITE_BYTES = 2 * 1024 * 1024


class ConfinementError(Exception):
    """A refused file operation; reported to the model as a tool error."""

    def __init__(self, code: str, message: str = ''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class ConfinementViolation(ConfinementError):
    """Identity changed under us: fatal for the run."""


def normalize(path: object, *, allow_root: bool = False) -> str:
    """Return a canonical relative path or raise ConfinementError."""
    if not isinstance(path, str):
        raise ConfinementError('PATH_INVALID', 'path must be a string')
    if path in ('', '.', './') and allow_root:
        return ''
    if not path or len(path) > MAX_PATH_LEN:
        raise ConfinementError('PATH_INVALID', 'empty or too long path')
    if path.startswith('/') or path.startswith('~'):
        raise ConfinementError('PATH_ABSOLUTE', 'absolute paths are refused')
    if any(ord(c) < 0x20 or c in '\\\x7f' for c in path):
        raise ConfinementError('PATH_INVALID', 'control characters or backslashes are refused')
    if path.endswith('/'):
        path = path[:-1]
    parts = path.split('/')
    for part in parts:
        if part in ('', '.'):
            raise ConfinementError('PATH_INVALID', 'empty or "." component')
        if part == '..':
            raise ConfinementError('PATH_TRAVERSAL', '".." is refused')
        if part.lower() == '.git':
            raise ConfinementError('PATH_GIT', '.git is refused')
        if part.startswith(TEMP_PREFIX):
            raise ConfinementError('PATH_INVALID', 'gateway temp names are refused')
    return '/'.join(parts)


class Scope:
    """Declared read and write sets. A trailing ``/`` declares a subtree."""

    def __init__(self, read_paths: Iterable[str], write_paths: Iterable[str]):
        self.read_files, self.read_dirs = self._parse(read_paths)
        self.write_files, self.write_dirs = self._parse(write_paths)

    @staticmethod
    def _parse(entries: Iterable[str]) -> tuple[frozenset, frozenset]:
        files, dirs = set(), set()
        for entry in entries:
            norm = normalize(entry)
            (dirs if entry.endswith('/') else files).add(norm)
        return frozenset(files), frozenset(dirs)

    @staticmethod
    def _within(rel: str, files: frozenset, dirs: frozenset) -> bool:
        if rel in files:
            return True
        return any(rel.startswith(d + '/') for d in dirs)

    def can_write(self, rel: str) -> bool:
        return self._within(rel, self.write_files, self.write_dirs)

    def can_read(self, rel: str) -> bool:
        return (self._within(rel, self.read_files, self.read_dirs) or self.can_write(rel))

    def can_list(self, rel: str) -> bool:
        """A directory is listable if it is in scope or an ancestor of scope."""
        if rel == '' or rel in self.read_dirs or rel in self.write_dirs or self.can_read(rel):
            return True
        prefix = rel + '/'
        return any(e.startswith(prefix) for e in
                   (self.read_files | self.read_dirs | self.write_files | self.write_dirs))

    def visible(self, rel: str) -> bool:
        return self.can_read(rel) or self.can_list(rel)


def _oserror(exc: OSError) -> ConfinementError:
    code = {
        errno.ELOOP: 'SYMLINK_REFUSED', errno.ENOTDIR: 'NOT_A_DIRECTORY',
        errno.ENOENT: 'NOT_FOUND', errno.EISDIR: 'IS_A_DIRECTORY',
        errno.EACCES: 'PERMISSION_DENIED', errno.EPERM: 'PERMISSION_DENIED',
        errno.ENXIO: 'SPECIAL_FILE', errno.EOPNOTSUPP: 'SPECIAL_FILE',
    }.get(exc.errno, 'OS_ERROR')
    return ConfinementError(code, f'{code.lower().replace("_", " ")}')


class Workspace:
    """All file access for the model's tools goes through this object."""

    def __init__(self, root: str, scope: Scope,
                 hook: Optional[Callable[[str, str], None]] = None):
        try:
            self.root_fd = os.open(root, _DIR_FLAGS)
        except OSError as exc:
            raise ConfinementError('WORKDIR_INVALID', 'workdir cannot be opened as a real directory') from exc
        st = os.fstat(self.root_fd)
        self.identity = (st.st_dev, st.st_ino)
        self.scope = scope
        self._hook = hook or (lambda point, rel: None)

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    # -- walking -------------------------------------------------------------
    def _open_dir(self, parent_fd: int, name: str) -> int:
        if name.lower() == '.git':
            raise ConfinementError('PATH_GIT', '.git is refused')
        try:
            fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno in (errno.ENOTDIR, errno.ELOOP):
                try:
                    if stat.S_ISLNK(os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode):
                        raise ConfinementError('SYMLINK_REFUSED', 'symlinks are refused') from exc
                except OSError:
                    pass
            raise _oserror(exc) from exc
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_dev != self.identity[0]:
            os.close(fd)
            raise ConfinementError('MOUNT_REFUSED' if stat.S_ISDIR(st.st_mode) else 'NOT_A_DIRECTORY')
        return fd

    def _walk(self, parts: list[str]) -> tuple[int, list[tuple[int, int]]]:
        """Open ``parts`` as directories; return (fd, identity chain)."""
        fd = os.dup(self.root_fd)
        chain = [self.identity]
        try:
            for part in parts:
                nfd = self._open_dir(fd, part)
                os.close(fd)
                fd = nfd
                st = os.fstat(fd)
                chain.append((st.st_dev, st.st_ino))
        except BaseException:
            os.close(fd)
            raise
        return fd, chain

    def _verify(self, parts: list[str], chain: list[tuple[int, int]]) -> None:
        try:
            fd, again = self._walk(parts)
        except ConfinementError as exc:
            raise ConfinementViolation('IDENTITY_CHANGED', 'directory chain changed during the operation') from exc
        os.close(fd)
        if again != chain:
            raise ConfinementViolation('IDENTITY_CHANGED', 'directory chain changed during the operation')

    def _open_file(self, parent_fd: int, name: str) -> tuple[int, os.stat_result]:
        try:
            fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise _oserror(exc) from exc
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            raise ConfinementError('IS_A_DIRECTORY' if stat.S_ISDIR(st.st_mode) else 'SPECIAL_FILE',
                                   'only regular files are allowed')
        if st.st_nlink != 1:
            os.close(fd)
            raise ConfinementError('HARD_LINK_REFUSED', 'hard-linked files are refused')
        return fd, st

    @staticmethod
    def _read_all(fd: int, limit: int) -> bytes:
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ConfinementError('FILE_TOO_LARGE', f'file exceeds {limit} bytes')
            chunks.append(chunk)
        return b''.join(chunks)

    # -- read operations -----------------------------------------------------
    def read_text(self, path: object) -> tuple[str, str]:
        rel = normalize(path)
        if not self.scope.can_read(rel):
            raise ConfinementError('OUT_OF_SCOPE_READ', 'path is outside the declared read set')
        parts = rel.split('/')
        parent_fd, chain = self._walk(parts[:-1])
        try:
            self._hook('read_before_open', rel)
            fd, st = self._open_file(parent_fd, parts[-1])
            try:
                if st.st_size > MAX_FILE_BYTES:
                    raise ConfinementError('FILE_TOO_LARGE', f'file exceeds {MAX_FILE_BYTES} bytes')
                data = self._read_all(fd, MAX_FILE_BYTES)
            finally:
                os.close(fd)
            self._hook('read_after_open', rel)
            self._verify(parts[:-1], chain)
            self._verify_entry(parent_fd, parts[-1], st)
        finally:
            os.close(parent_fd)
        if b'\x00' in data:
            raise ConfinementError('BINARY_FILE', 'binary files are refused')
        try:
            return rel, data.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ConfinementError('BINARY_FILE', 'file is not UTF-8 text') from exc

    def _verify_entry(self, parent_fd: int, name: str, st: os.stat_result) -> None:
        try:
            now = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise ConfinementViolation('IDENTITY_CHANGED', 'file changed during the operation') from exc
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino) or not stat.S_ISREG(now.st_mode):
            raise ConfinementViolation('IDENTITY_CHANGED', 'file changed during the operation')

    def list_dir(self, path: object, limit: int = 500) -> tuple[str, list[dict], bool]:
        rel = normalize(path, allow_root=True)
        if not self.scope.can_list(rel):
            raise ConfinementError('OUT_OF_SCOPE_READ', 'directory is outside the declared scope')
        parts = rel.split('/') if rel else []
        fd, chain = self._walk(parts)
        try:
            names = sorted(os.listdir(fd))
            entries, truncated = [], False
            for name in names:
                if name.lower() == '.git' or name.startswith(TEMP_PREFIX):
                    continue
                child = f'{rel}/{name}' if rel else name
                if not self.scope.visible(child):
                    continue
                if len(entries) >= limit:
                    truncated = True
                    break
                try:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    kind = 'dir'
                elif stat.S_ISLNK(st.st_mode):
                    kind = 'symlink-refused'
                elif stat.S_ISREG(st.st_mode):
                    kind = 'file' if st.st_nlink == 1 else 'hardlink-refused'
                else:
                    kind = 'special-refused'
                item = {'name': name, 'type': kind}
                if kind == 'file':
                    item['size'] = st.st_size
                entries.append(item)
            self._verify(parts, chain)
        finally:
            os.close(fd)
        return rel, entries, truncated

    def grep(self, pattern: str, path: object, *, ignore_case: bool, max_results: int,
             line_chars: int = 300, max_files: int = 2000) -> tuple[list[dict], bool]:
        rel = normalize(path, allow_root=True)
        needle = pattern.lower() if ignore_case else pattern
        results: list[dict] = []
        state = {'files': 0, 'truncated': False}

        def scan_file(dir_fd: int, name: str, child: str) -> None:
            state['files'] += 1
            if state['files'] > max_files:
                state['truncated'] = True
                return
            try:
                fd, st = self._open_file(dir_fd, name)
            except ConfinementError:
                return
            try:
                if st.st_size > 1024 * 1024:
                    return
                data = self._read_all(fd, 1024 * 1024)
            except ConfinementError:
                return
            finally:
                os.close(fd)
            if b'\x00' in data:
                return
            text = data.decode('utf-8', errors='replace')
            for number, line in enumerate(text.splitlines(), 1):
                hay = line.lower() if ignore_case else line
                if needle in hay:
                    if len(results) >= max_results:
                        state['truncated'] = True
                        return
                    results.append({'path': child, 'line': number, 'text': line[:line_chars]})

        def scan_dir(dir_fd: int, drel: str, depth: int) -> None:
            if depth > 32:
                state['truncated'] = True
                return
            for name in sorted(os.listdir(dir_fd)):
                if state['truncated']:
                    return
                if name.lower() == '.git' or name.startswith(TEMP_PREFIX):
                    continue
                child = f'{drel}/{name}' if drel else name
                if not self.scope.visible(child):
                    continue
                try:
                    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    try:
                        sub = self._open_dir(dir_fd, name)
                    except ConfinementError:
                        continue
                    try:
                        scan_dir(sub, child, depth + 1)
                    finally:
                        os.close(sub)
                elif stat.S_ISREG(st.st_mode) and self.scope.can_read(child):
                    scan_file(dir_fd, name, child)

        if rel and self.scope.can_read(rel):
            parts = rel.split('/')
            parent_fd, chain = self._walk(parts[:-1])
            try:
                scan_file(parent_fd, parts[-1], rel)
                self._verify(parts[:-1], chain)
            finally:
                os.close(parent_fd)
        elif self.scope.can_list(rel):
            parts = rel.split('/') if rel else []
            fd, chain = self._walk(parts)
            try:
                scan_dir(fd, rel, 0)
                self._verify(parts, chain)
            finally:
                os.close(fd)
        else:
            raise ConfinementError('OUT_OF_SCOPE_READ', 'path is outside the declared scope')
        return results, state['truncated']

    # -- write operation -----------------------------------------------------
    def apply_edit(self, path: object, old: Optional[str], new: str, create: bool) -> str:
        rel = normalize(path)
        if not self.scope.can_write(rel):
            raise ConfinementError('OUT_OF_SCOPE_WRITE', 'path is outside the declared write set')
        if len(new.encode('utf-8')) > MAX_WRITE_BYTES:
            raise ConfinementError('FILE_TOO_LARGE', 'new content too large')
        parts = rel.split('/')
        name = parts[-1]
        parent_fd, chain = self._walk(parts[:-1])
        tmp = TEMP_PREFIX + secrets.token_hex(8)
        tmp_created = False
        try:
            expect = None
            if create:
                try:
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ConfinementError('ALREADY_EXISTS', 'file already exists; use an edit')
                content, mode = new, 0o644
            else:
                if not old:
                    raise ConfinementError('EDIT_INVALID', 'old text is required for an edit')
                fd, st = self._open_file(parent_fd, name)
                try:
                    data = self._read_all(fd, MAX_FILE_BYTES)
                finally:
                    os.close(fd)
                try:
                    text = data.decode('utf-8')
                except UnicodeDecodeError as exc:
                    raise ConfinementError('BINARY_FILE', 'file is not UTF-8 text') from exc
                hits = text.count(old)
                if hits == 0:
                    raise ConfinementError('EDIT_OLD_NOT_FOUND', 'old text not found')
                if hits > 1:
                    raise ConfinementError('EDIT_OLD_AMBIGUOUS', 'old text occurs more than once')
                content = text.replace(old, new, 1)
                if len(content.encode('utf-8')) > MAX_WRITE_BYTES:
                    raise ConfinementError('FILE_TOO_LARGE', 'resulting file too large')
                mode = stat.S_IMODE(st.st_mode) & 0o777
                expect = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            self._hook('write_before_temp', rel)
            tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC,
                          mode, dir_fd=parent_fd)
            tmp_created = True
            try:
                payload = content.encode('utf-8')
                view = memoryview(payload)
                while view:
                    view = view[os.write(tfd, view):]
                os.fchmod(tfd, mode)
                os.fsync(tfd)
                tst = os.fstat(tfd)
            finally:
                os.close(tfd)
            self._hook('write_before_rename', rel)
            self._verify(parts[:-1], chain)
            if create:
                try:
                    os.link(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                except FileExistsError as exc:
                    raise ConfinementError('ALREADY_EXISTS', 'file appeared concurrently') from exc
                os.unlink(tmp, dir_fd=parent_fd)
                tmp_created = False
            else:
                try:
                    now = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except OSError as exc:
                    raise ConfinementError('CONCURRENT_MODIFICATION', 'file changed during the edit') from exc
                if (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) != expect or not stat.S_ISREG(now.st_mode):
                    raise ConfinementError('CONCURRENT_MODIFICATION', 'file changed during the edit')
                os.rename(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                tmp_created = False
            self._hook('write_after_rename', rel)
            self._verify(parts[:-1], chain)
            final = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if ((final.st_dev, final.st_ino) != (tst.st_dev, tst.st_ino)
                    or not stat.S_ISREG(final.st_mode) or final.st_nlink != 1):
                raise ConfinementViolation('IDENTITY_CHANGED', 'written file identity changed')
        finally:
            if tmp_created:
                try:
                    os.unlink(tmp, dir_fd=parent_fd)
                except OSError:
                    pass
            os.close(parent_fd)
        return rel
