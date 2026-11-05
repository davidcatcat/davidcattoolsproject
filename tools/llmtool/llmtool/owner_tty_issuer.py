"""Issue real Owner approval receipts after a direct terminal challenge.

This is an accident guard against AI error; a same-user process that can open
a pseudo-terminal can still answer, so it is not OS isolation (Owner accepted
this residual, #1019 S2). No F2 repository effect happens here.
"""

import argparse
import hmac
import json
import os
from pathlib import Path
import re
import select
import secrets
import sqlite3
import subprocess
import sys
import termios
import time
import unicodedata

APPROVAL_TTL_SECONDS = 3600
CHALLENGE_TIMEOUT_SECONDS = 120
_SHA = re.compile(r'^[0-9a-f]{40}$')


class OwnerApprovalRefusal(Exception):
    """A refusal with one fixed, safe-to-display reason code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _refuse(code):
    raise OwnerApprovalRefusal(code)


def _clock():
    return time.time()


def _valid_sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def merge_binding(pr, head):
    if type(pr) is not int or pr <= 0 or not _valid_sha(head):
        raise ValueError('invalid merge binding')
    return json.dumps({'action': 'MERGE_SENSITIVE_PR', 'head': head, 'pr': pr,
                       'version': 1}, sort_keys=True, separators=(',', ':'))


def install_binding(old_sha, new_sha):
    if not _valid_sha(old_sha) or not _valid_sha(new_sha):
        raise ValueError('invalid install binding')
    return json.dumps({'action': 'INSTALL_VERSION', 'new_sha': new_sha,
                       'old_sha': old_sha, 'version': 1},
                      sort_keys=True, separators=(',', ':'))


def _ledger_path():
    return Path.home() / '.local/state/llmtool/owner-approvals/approvals.sqlite3'


def _ledger_types():
    from .authorization_ledger import LedgerError, ReceiptLedger

    return LedgerError, ReceiptLedger


def _ledger_create():
    _, ledger_type = _ledger_types()
    path = _ledger_path()
    directory = Path.home()
    for component in ('.local', 'state', 'llmtool', 'owner-approvals'):
        directory = directory / component
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise
    return ledger_type.create(path, clock=_clock, kind='OWNER_APPROVAL')


def _ledger_open():
    _, ledger_type = _ledger_types()
    return ledger_type.open(_ledger_path(), clock=_clock, kind='OWNER_APPROVAL')


def _ledger_for_grant():
    return _ledger_open() if _ledger_path().exists() else _ledger_create()


def _compare_line(line, expected):
    if line.endswith(b'\r\n'):
        line = line[:-2]
    elif line.endswith((b'\r', b'\n')):
        line = line[:-1]
    return hmac.compare_digest(line, expected.encode('ascii'))


def confirm_on_tty(lines):
    """Prompt only on the controlling terminal and accept one bounded answer."""
    if not os.isatty(0) or not os.isatty(1):
        _refuse('OWNER_APPROVAL_NOT_INTERACTIVE')
    try:
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    except OSError:
        _refuse('OWNER_APPROVAL_NOT_INTERACTIVE')
    try:
        try:
            if os.tcgetpgrp(fd) != os.getpgrp():
                _refuse('OWNER_APPROVAL_NOT_INTERACTIVE')
        except OSError:
            _refuse('OWNER_APPROVAL_NOT_INTERACTIVE')
        code = secrets.token_hex(6)
        body = ''.join(str(line) + '\n' for line in lines)
        body += ('Only the Owner may answer. No AI may type the code.\n' + code + '\n')
        payload = body.encode('utf-8')
        offset = 0
        while offset < len(payload):
            try:
                written = os.write(fd, payload[offset:])
            except OSError:
                _refuse('OWNER_APPROVAL_NOT_INTERACTIVE')
            if written <= 0:
                _refuse('OWNER_APPROVAL_NOT_INTERACTIVE')
            offset += written
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        except OSError:
            _refuse('OWNER_APPROVAL_NOT_INTERACTIVE')
        deadline = time.monotonic() + CHALLENGE_TIMEOUT_SECONDS
        answer = bytearray()
        while len(answer) < 64:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _refuse('OWNER_APPROVAL_TIMEOUT')
            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except (OSError, ValueError):
                _refuse('OWNER_APPROVAL_NOT_INTERACTIVE')
            if not ready:
                _refuse('OWNER_APPROVAL_TIMEOUT')
            try:
                chunk = os.read(fd, min(64 - len(answer), 64))
            except OSError:
                _refuse('OWNER_APPROVAL_CHALLENGE_FAILED')
            if not chunk:
                _refuse('OWNER_APPROVAL_CHALLENGE_FAILED')
            answer.extend(chunk)
            if b'\n' in chunk or b'\r' in chunk:
                break
        else:
            _refuse('OWNER_APPROVAL_CHALLENGE_FAILED')
        if not _compare_line(bytes(answer), code):
            _refuse('OWNER_APPROVAL_CHALLENGE_FAILED')
        return True
    finally:
        os.close(fd)


def grant(binding, lines):
    confirm_on_tty(lines)
    try:
        ledger = _ledger_for_grant()
        try:
            result = ledger.issue_owner_approval(binding, ttl_seconds=APPROVAL_TTL_SECONDS)
        finally:
            ledger.close()
        return result[1]
    except OwnerApprovalRefusal:
        raise
    except Exception as error:
        ledger_error, _ = _ledger_types()
        from .errors import LLMToolError

        if isinstance(error, (LLMToolError, OSError, sqlite3.Error)):
            _refuse('OWNER_APPROVAL_ALREADY_LIVE' if isinstance(error, ledger_error) and
                    getattr(error.code, 'value', None) == 'NOT_ALLOWED' else
                    'OWNER_APPROVAL_LEDGER')
        raise


def is_approved(binding):
    try:
        from .errors import LLMToolError

        ledger = _ledger_open()
        try:
            return bool(ledger.owner_approval_live(binding))
        finally:
            ledger.close()
    except (LLMToolError, OSError, sqlite3.Error):
        return False


def consume(binding):
    try:
        from .errors import LLMToolError

        ledger = _ledger_open()
        try:
            return bool(ledger.consume_owner_approval(binding))
        finally:
            ledger.close()
    except (LLMToolError, OSError, sqlite3.Error):
        return False


def add_owner_approval_parser(subparsers):
    parser = subparsers.add_parser('owner-approval', help='Grant a one-use Owner approval')
    commands = parser.add_subparsers(dest='owner_approval_command', required=True)
    grant_parser = commands.add_parser('grant', help='Grant approval for an open PR HEAD')
    grant_parser.add_argument('--pr', type=int, required=True)
    grant_parser.add_argument('--head', required=True)
    return parser


def _gh_pr_view(number):
    try:
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            ['gh', 'pr', 'view', str(number), '--json',
             'number,title,state,headRefOid,files'],
            cwd=root, timeout=30, capture_output=True, check=False)
        if result.returncode != 0:
            _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
        data = json.loads(result.stdout.decode('utf-8'))
    except OwnerApprovalRefusal:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
    if (type(data) is not dict or type(data.get('number')) is not int or
            data.get('number') != number or type(data.get('title')) is not str or
            type(data.get('state')) is not str or not _valid_sha(data.get('headRefOid')) or
            type(data.get('files')) is not list or any(
                type(item) is not dict or type(item.get('path')) is not str
                for item in data['files'])):
        _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
    return data


def _safe_terminal_text(value, limit):
    if type(value) is not str:
        value = str(value)
    return ''.join('?' if unicodedata.category(char).startswith('C') else char
                   for char in value)[:limit]


def _display_lines(data, number, head):
    if data.get('state') != 'OPEN':
        _refuse('OWNER_APPROVAL_PR_NOT_OPEN')
    if data.get('headRefOid') != head:
        _refuse('OWNER_APPROVAL_HEAD_MISMATCH')
    title = _safe_terminal_text(data.get('title', ''), 120)
    raw_files = data.get('files')
    if type(raw_files) is not list:
        _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
    paths = [_safe_terminal_text(item.get('path', '') if type(item) is dict else item, 200)
             for item in raw_files[:50]]
    lines = [f'PR {number}: {title}', f'HEAD: {head}', f'File count: {len(raw_files)}']
    lines.extend('  ' + path for path in paths)
    if len(raw_files) > 50:
        lines.append(f'  +{len(raw_files) - 50} more')
    lines.append(f'Approval expires in {APPROVAL_TTL_SECONDS} seconds.')
    return lines


def run_owner_approval(args):
    try:
        if type(args.pr) is not int or args.pr <= 0 or not _valid_sha(args.head):
            _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
        data = _gh_pr_view(args.pr)
        lines = _display_lines(data, args.pr, args.head)
        binding = merge_binding(args.pr, args.head)
        expires_at = grant(binding, lines)
        print(f'OWNER_APPROVAL_GRANTED pr={args.pr} head={args.head} expires_at={expires_at}')
        return 0
    except OwnerApprovalRefusal as error:
        print('llmtool: ' + error.code, file=sys.stderr)
        return 1

