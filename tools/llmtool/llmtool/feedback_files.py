"""Explicit bounded feedback file operations; no discovery or implicit storage."""

import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

from .errors import LLMToolError
from .feedback import FeedbackCase, JSON_LIMIT, parse_feedback_case


@contextmanager
def _parent(filename):
    """Anchor all operations to directory descriptors; reject symlink components."""
    path = Path(filename)
    if '..' in path.parts:
        raise LLMToolError('feedback file paths cannot contain parent traversal')
    path = Path(os.path.abspath(path))
    if not path.name:
        raise LLMToolError('feedback requires an explicit file')
    descriptor = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(path.anchor, flags)
        for part in path.parent.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, path.name
    except (OSError, ValueError):
        raise LLMToolError('feedback file operation failed; details omitted') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read(directory, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise LLMToolError('feedback input requires a regular file')
        if info.st_size > JSON_LIMIT:
            raise LLMToolError('feedback file exceeds 1 MiB')
        raw = stream.read(JSON_LIMIT + 1)
    if len(raw) > JSON_LIMIT:
        raise LLMToolError('feedback file exceeds 1 MiB')
    try:
        return raw.decode('utf-8'), info
    except UnicodeError:
        raise LLMToolError('feedback file must be UTF-8') from None


def read_feedback_text(filename):
    with _parent(filename) as (directory, name):
        return _read(directory, name)[0]


def read_feedback_file(filename):
    return parse_feedback_case(read_feedback_text(filename))


def write_feedback_file(filename, case, previous=None):
    """Create without overwrite, or replace precisely the explicitly reviewed case.

    New files are 0600. Updates retain existing permission bits (not ownership or
    special bits). Concurrent writers are unsupported; identity/content checks
    catch intervening updates before replacement.
    """
    if type(case) is not FeedbackCase or (previous is not None and type(previous) is not FeedbackCase):
        raise LLMToolError('feedback writes require typed FeedbackCase values')
    raw = (case.to_json() + '\n').encode('utf-8')
    if len(raw) > JSON_LIMIT:
        raise LLMToolError('feedback file exceeds 1 MiB')
    with _parent(filename) as (directory, name):
        if not name.endswith('.json'):
            raise LLMToolError('feedback output must be an explicit .json file')
        mode, original = 0o600, None
        if previous is not None:
            text, original = _read(directory, name)
            if parse_feedback_case(text) != previous or case.id != previous.id:
                raise LLMToolError('feedback file changed before review update')
            mode = stat.S_IMODE(original.st_mode) & 0o777
        temporary = '.llmtool-feedback-' + secrets.token_hex(12)
        created = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            created = True
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            if previous is None:
                # Atomic publication with no overwrite, including dangling symlinks.
                os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            else:
                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                        original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns):
                    raise LLMToolError('feedback file changed before review update')
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            if created:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
