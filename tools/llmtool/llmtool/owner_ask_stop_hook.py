"""Claude Code Stop hook: a PM chat reply that asks the Owner must cite a verified ask.

#2285 Phase B part 1. Input is the Stop hook JSON on stdin (session_id,
transcript_path, cwd, stop_hook_active, last_assistant_message). The process
always exits 0.

Detection is a screen only and never decides pass or fail. A reply is screened
in when it has a PM chat ask line (owner_ask_audit.CHAT_ASK, except the
audit's nothing-to-do line), a lettered A/B option list with the word Owner, or
a pm_owner_marker.RESERVED_ROWS term. A screened-in reply passes only when one
cited issue comment URL is fully verified:

* line 1 of the comment is pm_owner_marker.MARKER_ASK and line 2 matches
  owner_ask_audit.GATED_LINE with a row in RESERVED_ROWS;
* the comment belongs to the cited Issue and its author_association is in
  TRUSTED_ASSOCIATION;
* it is younger than owner_ask_audit.WAIT_HORIZON;
* no later comment on that Issue settles it (MARKER_ANSWER or OWNER_SETTLED);
* it was created before this turn's first assistant entry in transcript_path.

Everything is fail-closed. A transcript that is missing, unreadable, has any
malformed or undecodable record, or a user/assistant record with a missing or
unparsable timestamp gives ORDER_UNKNOWN. Every comment page of the cited Issue
is read read-only through ``gh api`` (argv, shell=False, timeout); a page
error, an incomplete list (the Issue comment count differs, a page is
oversized or an id repeats) or a page-limit overrun is not PASS. Any JSON
decode error, RecursionError included, and any malformed comment entry are not
PASS; nothing is silently dropped.

Observe mode (default) never blocks. Enforce mode (``LLMTOOL_OWNER_ASK_GATE_MODE
=enforce``) blocks once with stdout ``{"decision":"block","reason":...}``; an
unverifiable outcome blocks once with a reason saying verification was
unavailable. If stop_hook_active is set, or the same reply hash was already
blocked in this session, the hook never blocks again. Every non-PASS outcome
appends one JSON line (time, session, Issue or NONE, outcome code, reply
SHA-256, mode, action) to the gate log. Reply text, comment bodies and tokens
are never logged.

Residual: the cited repository is not compared with the working tree origin;
a trusted ask marker in another repository the author can write to verifies.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from datetime import datetime
from typing import NamedTuple


MODE_ENV = 'LLMTOOL_OWNER_ASK_GATE_MODE'
OBSERVE = 'observe'
ENFORCE = 'enforce'

PER_PAGE = 100
# 30 pages of 100 comments; a longer Issue is a page-limit overrun, never PASS.
MAX_COMMENT_PAGES = 30
MAX_CITATIONS = 5
# Total seconds for all gh calls of one hook run (Claude Code's default hook
# timeout is 60 seconds); each call also keeps owner_ask_audit.PAGE_TIMEOUT.
HOOK_BUDGET = 50
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_LOG_SCAN_BYTES = 8 * 1024 * 1024

PASS = 'PASS'
NO_ASK = 'NO_ASK'
INPUT_INVALID = 'INPUT_INVALID'
NO_CITATION = 'NO_CITATION'
COMMENT_NOT_FOUND = 'COMMENT_NOT_FOUND'
MARKER_MISSING = 'MARKER_MISSING'
ROW_MISSING = 'ROW_MISSING'
ISSUE_MISMATCH = 'ISSUE_MISMATCH'
UNTRUSTED_AUTHOR = 'UNTRUSTED_AUTHOR'
ASK_EXPIRED = 'ASK_EXPIRED'
ASK_SETTLED = 'ASK_SETTLED'
ASK_NOT_BEFORE_TURN = 'ASK_NOT_BEFORE_TURN'
FETCH_FAILED = 'FETCH_FAILED'
FETCH_INCOMPLETE = 'FETCH_INCOMPLETE'
FETCH_PAGE_LIMIT = 'FETCH_PAGE_LIMIT'
DECODE_FAILED = 'DECODE_FAILED'
ENTRY_INVALID = 'ENTRY_INVALID'
ORDER_UNKNOWN = 'ORDER_UNKNOWN'
HOOK_ERROR = 'HOOK_ERROR'
UNVERIFIABLE = frozenset((FETCH_FAILED, FETCH_INCOMPLETE, FETCH_PAGE_LIMIT, DECODE_FAILED,
                          ENTRY_INVALID, ORDER_UNKNOWN, HOOK_ERROR))

ACTION_LOG = 'LOG'
ACTION_BLOCK = 'BLOCK'
ACTION_LOOP_GUARD = 'LOOP_GUARD'

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'scripts'
_SESSION = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')
# One lettered option line, such as "A. ...", "(B) ...", "- **A)** ...".
_OPTION_LINE = re.compile(r'^[ \t>*_-]*[(\[]?([A-Za-z])[.):\]][ \t*_]+\S', re.MULTILINE)
_OWNER_WORD = re.compile(r'(?<![A-Za-z0-9_])Owner(?![A-Za-z0-9_])')


class FetchError(Exception):
    """A gh call failed; the message is a fixed code, never command output."""


class Citation(NamedTuple):
    owner: str
    repo: str
    number: int
    comment_id: int


def _load_script(name):
    """Load tools/llmtool/scripts/<name>.py once, reusing an already loaded copy."""
    path = (_SCRIPTS_DIR / (name + '.py')).resolve()
    current = sys.modules.get(name)
    current_file = getattr(current, '__file__', None)
    if current is not None and type(current_file) is str and Path(current_file).resolve() == path:
        return current
    register = current is None
    spec = importlib.util.spec_from_file_location(
        name if register else '_owner_ask_stop_hook_' + name, path)
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if register and sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    return module


try:
    # pm_owner_marker first: owner_ask_audit then finds it in sys.modules.
    _marker = _load_script('pm_owner_marker')
    _audit = _load_script('owner_ask_audit')
    _ROW_TERM = re.compile(r'(?<![A-Za-z0-9_-])(?:' +
                           '|'.join(map(re.escape, _marker.RESERVED_ROWS)) +
                           r')(?![A-Za-z0-9_-])')
    _LOAD_ERROR = None
except Exception as exc:  # The hook still exits 0 and reports HOOK_ERROR.
    _marker = _audit = _ROW_TERM = None
    _LOAD_ERROR = type(exc).__name__


def default_log_path():
    return Path.home() / '.local' / 'state' / 'llmtool' / 'owner-ask-gate.jsonl'


def _stderr(code):
    try:
        print('owner_ask_stop_hook: ' + code, file=sys.stderr)
    except (OSError, ValueError):
        pass


# Detection (screen only).

def asks_owner(message):
    """True when the reply is screened in as an Owner ask. Never a pass or fail."""
    for line in message.splitlines():
        if _audit.CHAT_ASK in line and _audit.CHAT_NOTHING not in line:
            return True
    if _ROW_TERM.search(message) is not None:
        return True
    letters = {match.group(1).upper() for match in _OPTION_LINE.finditer(message)}
    return {'A', 'B'} <= letters and _OWNER_WORD.search(message) is not None


def _parse_citation(url):
    head, _, comment = url.partition('#issuecomment-')
    parts = head.split('/')
    # https: / '' / host / owner / repo / issues / number
    if len(parts) != 7 or parts[5] != 'issues':
        return None
    owner, repo, number = parts[3], parts[4], parts[6]
    if (_marker.REPO_SLUG.fullmatch(owner + '/' + repo) is None or
            _marker.ISSUE_NUMBER.fullmatch(number) is None or
            re.fullmatch(r'[1-9][0-9]{0,19}', comment) is None):
        return None
    return Citation(owner, repo, int(number), int(comment))


def cited_comments(message):
    """Distinct cited issue comment URLs, in reply order."""
    found, seen = [], set()
    for match in _marker.ISSUE_COMMENT_URL.finditer(message):
        citation = _parse_citation(match.group(0))
        if citation is None:
            continue
        key = (citation.owner.casefold(), citation.repo.casefold(), citation.number,
               citation.comment_id)
        if key not in seen:
            seen.add(key)
            found.append(citation)
    return found


# Transcript order.

def _transcript_seconds(value):
    if type(value) is not str:
        return None
    text = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _prompt(record):
    """True for a human prompt, False for other user records, None if malformed."""
    content = record['message'].get('content')
    if type(content) is str:
        kinds = ['text'] if content.strip() else []
    elif type(content) is list:
        kinds = [part.get('type') for part in content if type(part) is dict]
    else:
        return None
    if record.get('isSidechain') is True or record.get('isMeta') is True:
        return False
    return 'text' in kinds and 'tool_result' not in kinds


def turn_start(transcript_path):
    """Epoch seconds of this turn's first assistant entry, or None (ORDER_UNKNOWN).

    Every record must be a JSON object with a string type. User and assistant
    records also need a message object and a parseable, zone-aware timestamp.
    One bad record makes the whole transcript unknown; it is never skipped.
    """
    if type(transcript_path) is not str or not os.path.isabs(transcript_path):
        return None
    limit = _audit.MAX_TRANSCRIPT_BYTES
    try:
        if not stat.S_ISREG(os.stat(transcript_path).st_mode):
            return None
        with open(transcript_path, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            data = handle.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit:
        return None
    entries = []
    for raw in data.split(b'\n'):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode('utf-8'))
        except (ValueError, RecursionError):
            return None
        if type(record) is not dict or type(record.get('type')) is not str:
            return None
        kind = record['type']
        if kind not in ('user', 'assistant'):
            continue
        when = _transcript_seconds(record.get('timestamp'))
        if when is None or type(record.get('message')) is not dict:
            return None
        prompt = _prompt(record) if kind == 'user' else False
        if prompt is None:
            return None
        entries.append((kind, when, record, prompt))
    start = None
    for index, (kind, _, _, prompt) in enumerate(entries):
        if kind == 'user' and prompt:
            start = index
    if start is None:
        return None
    for kind, when, record, _ in entries[start + 1:]:
        if kind == 'assistant' and record.get('isSidechain') is not True:
            return when
    return None


# GitHub read (read-only).

def gh_fetch(endpoint, timeout):
    """Read one GitHub API endpoint with gh, read-only. Returns stdout bytes."""
    env = dict(os.environ)
    env.update({'GH_PROMPT_DISABLED': '1', 'NO_COLOR': '1', 'LC_ALL': 'C'})
    try:
        result = subprocess.run(['gh', 'api', '--method', 'GET', endpoint],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, check=False, shell=False, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise FetchError(FETCH_FAILED) from exc
    if result.returncode:
        raise FetchError(FETCH_FAILED)
    return result.stdout


class _Budget:
    def __init__(self, clock):
        self._clock = clock
        self._deadline = clock() + HOOK_BUDGET

    def timeout(self):
        remaining = self._deadline - self._clock()
        return min(_audit.PAGE_TIMEOUT, remaining) if remaining > 0 else None


def _fetch_json(fetch, endpoint, budget):
    timeout = budget.timeout()
    if timeout is None:
        return None, FETCH_FAILED
    try:
        raw = fetch(endpoint, timeout)
    except FetchError:
        return None, FETCH_FAILED
    if type(raw) is not bytes:
        return None, FETCH_FAILED
    try:
        return json.loads(raw.decode('utf-8')), None
    except (ValueError, RecursionError):
        return None, DECODE_FAILED


def _entry(item):
    """A validated comment entry, or None when any required field is malformed."""
    if type(item) is not dict:
        return None
    cid, url = item.get('id'), item.get('issue_url')
    created, body = item.get('created_at'), item.get('body')
    if type(cid) is not int or cid <= 0:
        return None
    if type(url) is not str or not _marker._issue_number(url):
        return None
    when = _audit._seconds(created) if type(created) is str else None
    if when is None or type(body) is not str:
        return None
    return {'id': cid, 'issue_url': url, 'created': when, 'body': body,
            'association': item.get('author_association')}


def issue_comments(citation, fetch, budget):
    """(None, entries) for the complete comment list of the cited Issue, or (code, None)."""
    base = f'repos/{citation.owner}/{citation.repo}/issues/{citation.number}'
    issue, code = _fetch_json(fetch, base, budget)
    if code:
        return code, None
    if type(issue) is not dict:
        return DECODE_FAILED, None
    count = issue.get('comments')
    if type(count) is not int or count < 0:
        return FETCH_INCOMPLETE, None
    entries, seen = [], set()
    for page in range(1, MAX_COMMENT_PAGES + 1):
        data, code = _fetch_json(fetch, f'{base}/comments?per_page={PER_PAGE}&page={page}',
                                 budget)
        if code:
            return code, None
        if type(data) is not list:
            return DECODE_FAILED, None
        if len(data) > PER_PAGE:
            return FETCH_INCOMPLETE, None
        for item in data:
            entry = _entry(item)
            if entry is None:
                return ENTRY_INVALID, None
            if entry['id'] in seen:
                return FETCH_INCOMPLETE, None
            seen.add(entry['id'])
            entries.append(entry)
        if len(data) < PER_PAGE:
            break
    else:
        return FETCH_PAGE_LIMIT, None
    if len(entries) != count:
        return FETCH_INCOMPLETE, None
    return None, entries


# Verification.

def _same_issue(issue_url, citation):
    suffix = f'/repos/{citation.owner}/{citation.repo}/issues/{citation.number}'
    return (_marker._issue_number(issue_url) == str(citation.number) and
            issue_url.casefold().endswith(suffix.casefold()))


def _settles(body):
    lines = body.splitlines()
    if not lines:
        return False
    first = lines[0].strip()
    if first == _marker.MARKER_ANSWER:
        return True
    if _marker.MARKER.fullmatch(first) is None:
        return False
    fields = {}
    for key, value in _marker.OWNER_FIELD.findall(first):
        fields.setdefault(key, value.rstrip('-'))
    actor = fields.get('actor', '').casefold()
    target = fields.get('target', '').casefold()
    status = fields.get('status', '').upper()
    return actor == 'owner' or (target == 'owner' and status in _marker.OWNER_SETTLED)


def verify_citation(citation, entries, start, now):
    """The outcome code for one cited comment within a complete comment list."""
    target = next((entry for entry in entries if entry['id'] == citation.comment_id), None)
    if target is None:
        return COMMENT_NOT_FOUND
    if not _same_issue(target['issue_url'], citation):
        return ISSUE_MISMATCH
    lines = target['body'].splitlines()
    if not lines or lines[0] != _marker.MARKER_ASK:
        return MARKER_MISSING
    gated = _audit.GATED_LINE.fullmatch(lines[1]) if len(lines) > 1 else None
    if gated is None or gated.group(1) not in _marker.RESERVED_ROWS:
        return ROW_MISSING
    association = target['association']
    if type(association) is not str or association not in _marker.TRUSTED_ASSOCIATION:
        return UNTRUSTED_AUTHOR
    created = target['created']
    if not now - created < _audit.WAIT_HORIZON:
        return ASK_EXPIRED
    order = (created, target['id'])
    if any((entry['created'], entry['id']) > order and _settles(entry['body'])
           for entry in entries):
        return ASK_SETTLED
    # created_at has whole seconds: the comment existed by created + 1 at the latest.
    if not created + 1 <= start:
        return ASK_NOT_BEFORE_TURN
    return PASS


def evaluate(payload, message, *, fetch, now, clock=time.monotonic):
    """(outcome code, Issue number or 'NONE') for one Stop event."""
    if _LOAD_ERROR is not None:
        raise RuntimeError('owner ask contracts unavailable')
    if not asks_owner(message):
        return NO_ASK, 'NONE'
    citations = cited_comments(message)[:MAX_CITATIONS]
    if not citations:
        return NO_CITATION, 'NONE'
    start = turn_start(payload.get('transcript_path'))
    if start is None:
        return ORDER_UNKNOWN, str(citations[0].number)
    budget = _Budget(clock)
    cache, failures = {}, []
    for citation in citations:
        key = (citation.owner.casefold(), citation.repo.casefold(), citation.number)
        if key not in cache:
            cache[key] = issue_comments(citation, fetch, budget)
        code, entries = cache[key]
        if not code:
            code = verify_citation(citation, entries, start, now)
        if code == PASS:
            return PASS, str(citation.number)
        failures.append((code, str(citation.number)))
    unverifiable = [failure for failure in failures if failure[0] in UNVERIFIABLE]
    return (unverifiable or failures)[0]


# Decision, log and entry point.

def block_reason(outcome):
    if outcome in UNVERIFIABLE:
        return (f'Owner ask gate ({outcome}): verification was unavailable, so the Owner ask '
                'in this reply could not be checked. This stop is blocked once and will not '
                'be blocked again.')
    return (f'Owner ask gate ({outcome}): this reply asks the Owner but cites no verified '
            'owner-ask comment. Post the ask with tools/llmtool/scripts/pm_owner_marker.py '
            'ask and cite its issue comment URL, or remove the Owner ask from the reply.')


def _payload(data):
    if type(data) is not bytes or len(data) > MAX_INPUT_BYTES:
        return None
    try:
        value = json.loads(data.decode('utf-8'))
    except (ValueError, RecursionError):
        return None
    return value if type(value) is dict else None


def _session(payload):
    value = payload.get('session_id') if payload is not None else None
    return value if type(value) is str and _SESSION.fullmatch(value) else 'UNKNOWN'


def _append_log(path, record):
    line = (json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0),
                     0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        _stderr('LOG_WRITE_FAILED')


def _already_blocked(path, session, digest):
    """True if this session already blocked this reply hash. Unreadable log: False."""
    try:
        with open(path, 'rb') as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > MAX_LOG_SCAN_BYTES:
                handle.seek(size - MAX_LOG_SCAN_BYTES)
                data = handle.read().split(b'\n', 1)[-1]
            else:
                data = handle.read()
    except OSError:
        return False
    for raw in data.splitlines():
        try:
            record = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if (type(record) is dict and record.get('action') == ACTION_BLOCK and
                record.get('session') == session and record.get('reply_sha256') == digest):
            return True
    return False


def run(stdin_bytes, *, environ=None, fetch=None, now=None, log_path=None, clock=None):
    """Handle one Stop event. Returns the stdout text: '' or one block JSON line."""
    environ = os.environ if environ is None else environ
    mode = ENFORCE if environ.get(MODE_ENV) == ENFORCE else OBSERVE
    now = time.time() if now is None else now
    log_path = default_log_path() if log_path is None else Path(log_path)
    payload = _payload(stdin_bytes)
    session = _session(payload)
    message = payload.get('last_assistant_message') if payload is not None else None

    def log(issue, outcome, digest, action):
        _append_log(log_path, {'time': _stamp(now), 'session': session, 'issue': issue,
                               'outcome': outcome, 'reply_sha256': digest, 'mode': mode,
                               'action': action})

    if type(message) is not str:
        # Without the reply nothing can be screened; never block on bad input.
        log('NONE', INPUT_INVALID, 'NONE', ACTION_LOG)
        return ''
    digest = hashlib.sha256(message.encode('utf-8', 'surrogatepass')).hexdigest()
    try:
        outcome, issue = evaluate(payload, message, fetch=gh_fetch if fetch is None else fetch,
                                  now=now, clock=time.monotonic if clock is None else clock)
    except Exception:
        outcome, issue = HOOK_ERROR, 'NONE'
    if outcome in (PASS, NO_ASK):
        return ''
    action = ACTION_LOG
    if mode == ENFORCE:
        guarded = (payload.get('stop_hook_active', False) is not False or
                   _already_blocked(log_path, session, digest))
        action = ACTION_LOOP_GUARD if guarded else ACTION_BLOCK
    log(issue, outcome, digest, action)
    if action != ACTION_BLOCK:
        return ''
    return json.dumps({'decision': 'block', 'reason': block_reason(outcome)}) + '\n'


def _stamp(now):
    if _audit is not None:
        return _audit._stamp(int(now))
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(now)))


def main(argv=None):
    del argv
    try:
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    except (AttributeError, OSError, ValueError):
        data = b''
    try:
        output = run(data)
    except Exception:
        _stderr('HOOK_FAILURE')
        output = ''
    if output:
        try:
            sys.stdout.write(output)
            sys.stdout.flush()
        except (OSError, ValueError):
            _stderr('HOOK_OUTPUT_FAILED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
