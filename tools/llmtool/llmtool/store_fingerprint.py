"""Directory store fingerprint from entry metadata only.

Issue #1019, G3 latency note. Computes a stable fingerprint of a directory
from its immediate entries' names, sizes, and modification times in
nanoseconds, taken with lstat. File contents are never read and symbolic
links are never followed, neither for the directory itself nor for its
entries.
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
    """Return a stable hex digest of a directory's entry metadata.

    The digest depends only on the entry names, sizes, and modification
    times in nanoseconds, taken with lstat; the same directory state gives
    the same digest, and a change to any of these gives a different one.
    Any unexpected condition raises StoreFingerprintError with the fixed
    code STORE_FINGERPRINT_INVALID.
    """
    failed = False
    try:
        return _digest(_collect_entries(path))
    except StoreFingerprintError:
        raise
    except Exception:
        # Fail closed; raise outside except so no context or cause is attached.
        failed = True
    if failed:
        raise StoreFingerprintError()


def _collect_entries(path):
    """Return sorted (name, size, mtime_ns) lstat records of the entries."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not S_ISDIR(info.st_mode):
            raise StoreFingerprintError()
        entries = []
        for entry in os.scandir(fd):
            stat = entry.stat(follow_symlinks=False)
            entries.append((os.fsencode(entry.name), stat.st_size,
                            stat.st_mtime_ns))
    finally:
        os.close(fd)
    entries.sort()
    return entries


def _digest(entries):
    """Return the sha256 hex digest of the canonical record stream."""
    data = b''
    for name, size, mtime_ns in entries:
        data += _FIELD_SEP + name + _FIELD_SEP
        data += str(size).encode('ascii') + _FIELD_SEP
        data += str(mtime_ns).encode('ascii')
    return hashlib.sha256(data).hexdigest()
