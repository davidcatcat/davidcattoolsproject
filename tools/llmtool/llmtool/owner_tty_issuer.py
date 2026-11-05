"""Issue real Owner approval receipts after a direct terminal challenge.

This is an accident guard against AI error, not a security boundary. A same-user
process can answer through a pseudo-terminal, call the ledger or write its file
directly with no terminal, restore an older copy of it, or point HOME at another
one (Owner accepted this residual, #1019 S2). The challenge cannot be skipped
through this module's CLI. No F2 repository effect happens here.
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


def _repository_slug():
    try:
        from .config import repository_config

        slug = repository_config()['slug']
        if (type(slug) is not str or len(slug.split('/')) != 2 or
                any(not part for part in slug.split('/'))):
            raise ValueError('invalid repository slug')
        return slug
    except Exception:
        _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')


def merge_binding(pr, head, base_ref, repository):
    slug = _repository_slug()
    if (type(pr) is not int or pr <= 0 or not _valid_sha(head) or
            base_ref != 'main' or repository != slug):
        raise ValueError('invalid merge binding')
    return json.dumps({'action': 'MERGE_SENSITIVE_PR', 'head': head, 'pr': pr,
                       'base_ref': base_ref, 'repository': repository, 'version': 1},
                      sort_keys=True, separators=(',', ':'))


def merge_binding_from_pr(pr):
    try:
        return merge_binding(pr['number'], pr['head']['sha'], pr['base']['ref'],
                             pr['base']['repo']['full_name'])
    except OwnerApprovalRefusal:
        raise
    except Exception:
        raise ValueError('invalid pull request') from None


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
        body += ('Only the Owner may answer. No AI may type the code.\n'
                 'Type the code below and press Enter only if you approve. '
                 'Press Enter alone to refuse.\n' + code + '\n')
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


def _gh_api(path):
    try:
        slug = _repository_slug()
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            ['gh', 'api', 'repos/' + slug + '/' + path],
            cwd=root, timeout=30, capture_output=True, check=False)
        if result.returncode != 0:
            _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
        data = json.loads(result.stdout.decode('utf-8'))
    except OwnerApprovalRefusal:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
    return data


def _metadata(data, number):
    if (type(data) is not dict or type(data.get('number')) is not int or
            data['number'] != number or type(data.get('title')) is not str or
            type(data.get('state')) is not str or type(data.get('merged')) is not bool or
            not _valid_sha(data.get('head', {}).get('sha') if type(data.get('head')) is dict else None) or
            type(data.get('base')) is not dict or type(data['base'].get('ref')) is not str or
            type(data['base'].get('repo')) is not dict or
            type(data['base']['repo'].get('full_name')) is not str or
            type(data.get('changed_files')) is not int):
        _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
    return data


def _read_pr(number):
    first = _metadata(_gh_api(f'pulls/{number}'), number)
    files = _gh_api(f'pulls/{number}/files?per_page=100')
    second = _metadata(_gh_api(f'pulls/{number}'), number)
    if (any(first[key] != second[key] for key in ('state', 'merged', 'changed_files')) or
            first['head']['sha'] != second['head']['sha'] or
            first['base']['ref'] != second['base']['ref']):
        _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
    if (type(files) is not list or any(
            type(item) is not dict or type(item.get('filename')) is not str or
            type(item.get('status')) is not str or
            ('previous_filename' in item and type(item['previous_filename']) is not str)
            for item in files)):
        _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
    return second, files


def safe_terminal_text(value, limit):
    if type(value) is not str:
        value = str(value)
    fillers = {'\u115f', '\u1160', '\u3164', '\uffa0'}
    cleaned = ''.join('?' if (unicodedata.category(char).startswith('C') or
                              unicodedata.category(char) in ('Zl', 'Zp') or char in fillers)
                      else char for char in value)
    cleaned = ' '.join(cleaned.split())
    if len(cleaned) > limit:
        return cleaned[:max(0, limit - len(' [cut]'))] + ' [cut]'
    return cleaned


def _display_lines(data, files, number, head):
    if data.get('state') != 'open' or data.get('merged') is True:
        _refuse('OWNER_APPROVAL_PR_NOT_OPEN')
    if data['head']['sha'] != head:
        _refuse('OWNER_APPROVAL_HEAD_MISMATCH')
    if data['base']['ref'] != 'main':
        _refuse('OWNER_APPROVAL_BASE_NOT_MAIN')
    if data['base']['repo']['full_name'] != _repository_slug():
        _refuse('OWNER_APPROVAL_REPOSITORY_MISMATCH')
    if data['changed_files'] >= 100 or len(files) != data['changed_files']:
        _refuse('OWNER_APPROVAL_TOO_MANY_FILES')
    if any(len(item['filename']) > 1024 or len(item.get('previous_filename', '')) > 1024
           for item in files):
        _refuse('OWNER_APPROVAL_DISPLAY_TOO_LARGE')
    title = safe_terminal_text(data.get('title', ''), 300)
    repository = safe_terminal_text(data['base']['repo']['full_name'], 255)
    lines = [f'Action: merge PR #{number} into main of {repository} '
             '(MERGE_SENSITIVE_PR).', f'Title: {title}', f'HEAD: {head}',
             f'Base: {data["base"]["ref"]}', f'Files: {len(files)}']
    for item in files:
        filename = safe_terminal_text(item['filename'], 4096)
        previous = item.get('previous_filename')
        path = (safe_terminal_text(previous, 4096) + ' -> ' + filename
                if previous is not None else filename)
        status = safe_terminal_text(item['status'], 32)
        lines.append(f'{status}: {path}')
    lines.append(f'Approval expires in {APPROVAL_TTL_SECONDS} seconds.')
    return lines


def run_owner_approval(args):
    try:
        if type(args.pr) is not int or args.pr <= 0 or not _valid_sha(args.head):
            _refuse('OWNER_APPROVAL_PR_UNAVAILABLE')
        data, files = _read_pr(args.pr)
        lines = _display_lines(data, files, args.pr, args.head)
        binding = merge_binding_from_pr(data)
        if is_approved(binding):
            _refuse('OWNER_APPROVAL_ALREADY_LIVE')
        expires_at = grant(binding, lines)
        print(f'OWNER_APPROVAL_GRANTED pr={args.pr} head={args.head} expires_at={expires_at}')
        return 0
    except OwnerApprovalRefusal as error:
        print('llmtool: ' + error.code, file=sys.stderr)
        return 1

