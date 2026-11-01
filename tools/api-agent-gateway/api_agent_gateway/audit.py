"""Hash-chained, content-free JSONL audit log.

Each line is canonical JSON with ``seq`` and ``prev_sha256``; the digest of a
record is the SHA-256 of its line bytes (without the newline). The first
record is the header. The log never stores raw prompts, tool arguments, file
contents, model text or secrets: only digests, counts and codes.

This detects after-the-fact tampering by another party; it does not protect
against the same UID rewriting the whole chain.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

GENESIS = '0' * 64
_ALLOWED_TYPES = frozenset(('header', 'model_call', 'tool', 'compaction', 'refusal', 'end'))


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


class AuditError(Exception):
    pass


class AuditLog:
    def __init__(self, path: str):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_APPEND | getattr(os, 'O_CLOEXEC', 0)
        try:
            self._fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise AuditError('AUDIT_FILE_UNAVAILABLE') from exc
        os.fchmod(self._fd, 0o600)
        self._seq = 0
        self.last_sha256 = GENESIS

    def append(self, record_type: str, **fields: Any) -> str:
        if record_type not in _ALLOWED_TYPES:
            raise AuditError('AUDIT_RECORD_TYPE')
        record = dict(fields)
        record.update(type=record_type, seq=self._seq, prev_sha256=self.last_sha256)
        line = canonical(record)
        os.write(self._fd, line + b'\n')
        os.fsync(self._fd)
        self._seq += 1
        self.last_sha256 = sha256_hex(line)
        return self.last_sha256

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1


def verify(path: str) -> str:
    """Verify the chain and return the digest of the final record."""
    prev = GENESIS
    seq = 0
    with open(path, 'rb') as fh:
        for raw in fh:
            if not raw.endswith(b'\n'):
                raise AuditError('AUDIT_TRUNCATED')
            line = raw[:-1]
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise AuditError('AUDIT_MALFORMED') from exc
            if canonical(record) != line:
                raise AuditError('AUDIT_NOT_CANONICAL')
            if record.get('seq') != seq or record.get('prev_sha256') != prev:
                raise AuditError('AUDIT_CHAIN_BROKEN')
            if seq == 0 and record.get('type') != 'header':
                raise AuditError('AUDIT_HEADER_MISSING')
            prev = sha256_hex(line)
            seq += 1
    if seq == 0:
        raise AuditError('AUDIT_EMPTY')
    return prev
