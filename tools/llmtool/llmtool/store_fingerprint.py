"""Directory store fingerprint from entry metadata only.

Issue #1019, G3 latency note. Computes a stable fingerprint of a directory
from its own fstat metadata and its immediate entries' names and lstat
metadata (device, inode, mode, owner, group, link count, size, mtime_ns,
and ctime_ns). File contents are never read and symbolic links are never
followed, neither for the directory itself nor for its entries.
"""

import hashlib
import os
from stat import S_ISDIR

__all__ = ('fingerprint_directory', 'StoreFingerprintError')

#: The NUL byte never appears in a file-system entry name; it delimits the
#: name, size, and modification-time fields of the hashed record stream.
_FIELD_SEP = b'\0'


class StoreFingerprintError(OSError):
    """Directory state could not be fingerprinted.

    Carries one fixed upper-case code and never includes the directory
    path, any entry name, or any other input value.
    """

    code = 'STORE_FINGERPRINT_INVALID'

    def __init__(self):
        super().__init__('store directory fingerprint invalid')


def fingerprint_directory(path):
    """Return a stable hex digest of a directory's metadata and entries.

    The digest depends on the directory's own fstat metadata and its immediate
    entries' names and lstat metadata (device, inode, mode, owner, group, link
    count, size, mtime_ns, and ctime_ns); the same directory state gives the
    same digest, and a change to any of these gives a different one. Any
    unexpected condition raises StoreFingerprintError with the fixed code
    STORE_FINGERPRINT_INVALID.
    """
    failed = False
    try:
        return _digest(*_collect_entries(path))
    except StoreFingerprintError:
        raise
    except Exception:
        # Fail closed; raise outside except so no context or cause is attached.
        failed = True
    if failed:
        raise StoreFingerprintError()


def _collect_entries(path):
    """Return (dir_meta, sorted_entries) from fstat/lstat of the directory."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not S_ISDIR(info.st_mode):
            raise StoreFingerprintError()
        dir_meta = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_nlink,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        entries = []
        for entry in os.scandir(fd):
            stat = entry.stat(follow_symlinks=False)
            entries.append((
                os.fsencode(entry.name),
                stat.st_dev,
                stat.st_ino,
                stat.st_mode,
                stat.st_uid,
                stat.st_gid,
                stat.st_nlink,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            ))
    finally:
        os.close(fd)
    entries.sort()
    return dir_meta, entries


def _digest(dir_meta, entries):
    """Return the sha256 hex digest of the canonical metadata stream."""
    digest = hashlib.sha256()
    for value in dir_meta:
        digest.update(str(value).encode('ascii'))
        digest.update(_FIELD_SEP)
    for entry in entries:
        digest.update(_FIELD_SEP)
        digest.update(entry[0])
        for value in entry[1:]:
            digest.update(_FIELD_SEP)
            digest.update(str(value).encode('ascii'))
    return digest.hexdigest()
