"""C1: bounded local evidence selection, never test execution or model triage.

COMPLETE describes known failure-ID coverage in one supported captured report.
It neither authenticates the capture nor establishes that the tests passed.
"""

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .errors import LLMToolError
from .retrieval_files import excluded_credential_path

MAX_FILE_BYTES = 1024 * 1024
MAX_SOURCE_LINES = 32768
MAX_PATH_BYTES = 4096
MAX_PATH_DEPTH = 64
MAX_ID_BYTES = 512
MAX_PYTEST_HEADER_BYTES = 8192
MAX_FAILURE_IDS = 128
MAX_EXCERPT_BYTES = 2048
MAX_EXCERPTS = 32
MAX_TOTAL_EXCERPT_BYTES = 16384

_UNIT_HEADER = re.compile(r'(FAIL|ERROR): ([^\s()]+ \([\w.]+\)(?: \([^\r\n]+\))?)')
_UNIT_RUN = re.compile(r'Ran ([0-9]{1,9}) tests? in [0-9]+(?:\.[0-9]+)?s')
_UNIT_FAILED = re.compile(r'FAILED \(([^\n]+)\)')
_UNIT_OK = re.compile(r'OK(?: \(([^\n]+)\))?')
_NODE_ID = re.compile(r'(?:[\w.-]+/)*[\w.-]+\.py(?:::[\w.]+)*(?:\[[^\n]*\])?')
_PYTEST_STATS = re.compile(
    r'([0-9][^\n]*?) in [0-9]+(?:\.[0-9]+)?s(?: \([0-9:]+\))?')
# Disable IGNORECASE for this boundary class: otherwise its negated ASCII range
# also excludes İ, ı, ſ, and K when used inside the credential patterns.
_NON_ASCII_WORD = r'(?-i:[^\W\x00-\x7f])'
# Both key boundaries are case-sensitive ASCII look-arounds, never \b or a bare
# ASCII class: under IGNORECASE those treat İ, ı, ſ, K (or any non-ASCII word
# character, for \b) as an ASCII word neighbour and would hide the key (#1138).
_CLOUD_KEY = (
    r'(?:(?<!(?-i:[A-Za-z0-9_]))(?:AKIA|ASIA)[A-Z0-9]{16}(?!(?-i:[A-Za-z0-9_])))'
)
_SENSITIVE_PREFIX = (
    r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|'
    + _CLOUD_KEY + r'|'
    r'(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{12,}|'
    r'(?<![A-Za-z0-9_])(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|'
    r'(?<![A-Za-z0-9_])xox[abprs]-[A-Za-z0-9-]{20,}|'
    r'(?<![A-Za-z0-9_])(?:bearer|basic)\s+\S+|'
    r'["\']?(?<![A-Za-z0-9_])(?:api[_-]?key|password|passwd|token|secret|access[_-]?token|'
    r'client[_-]?secret|aws_secret_access_key)["\']?\s*[:=]')
# A keyword assignment whose whole remaining line is a null/boolean literal
# (e.g. `token = None`, `password: null`) carries no value. Any other text
# after the literal on that line, including a second assignment, still matches.
_SENSITIVE = re.compile(
    _SENSITIVE_PREFIX + r'(?!\s*(?:none|null|true|false)[ \t]*(?:\r?\n|$))', re.IGNORECASE)
# The same exemption over the CLI's JSON-escaped payload, where line breaks and
# TABs are printed as backslash escapes. Used only alongside the raw-text gate.
_SENSITIVE_JSON = re.compile(
    _SENSITIVE_PREFIX + r'(?!(?:\s|\\[nrt])*(?:none|null|true|false)(?: |\\t)*(?:\\r\\n|\\n|$))',
    re.IGNORECASE)
# Stdlib logging's default `LEVEL:logger:message` form, plus pytest's
# captured-log `LEVEL    logger:file:line message` form; never failure headers.
_LOG_ERROR = re.compile(r'ERROR:[\w.-]+:')
_PYTEST_LOG_ERROR = re.compile(r'ERROR(?::[\w.-]+:| {4}\S)')
_JWT_SEGMENT = re.compile(r'[A-Za-z0-9_-]+', re.IGNORECASE)
_JWT_START = re.compile(r'(?<!(?-i:[A-Za-z0-9_]))eyJ', re.IGNORECASE)
_JWT_END = re.compile(r'[A-Za-z0-9_-]{8,}\b', re.IGNORECASE)
_JWT_END_NON_ASCII = re.compile(r'[A-Za-z0-9_-]{8,}(?=' + _NON_ASCII_WORD + r')', re.IGNORECASE)
_URL_SCHEME = re.compile(r'[a-z][a-z0-9+.-]*', re.IGNORECASE)
_URL_AUTHORITY = re.compile(r'[^\s/]+')


def _sensitive_jwt(text):
    # Consume maximal, non-overlapping segments once. A JWT's first two
    # segments must end at dots; its last may end at an internal word boundary.
    # Each bounded prefix search / anchored tail match scans at most one
    # segment, never the remaining source from every overlapping eyJ start.
    # _JWT_START enforces the header boundary; the fallback tail boundary
    # requires an adjacent non-ASCII word character at the final segment end.
    previous_end, first, second = None, False, False
    for segment in _JWT_SEGMENT.finditer(text):
        start, end = segment.span()
        adjacent = (previous_end is not None and start == previous_end + 1
                    and text[previous_end] == '.')
        if adjacent and second:
            if _JWT_END.match(text, start) or _JWT_END_NON_ASCII.match(text, start):
                return True
        second = adjacent and first and end - start >= 8
        first = (end - start >= 11
                 and _JWT_START.search(text, start, end - 8) is not None)
        previous_end = end
    return False


def _sensitive_url(text):
    # Consume scheme runs without a suffix that could trigger backtracking.
    # Authorities end at whitespace or '/', so these additional scans cannot
    # overlap beyond the ':' of a subsequent URL delimiter. Keep arbitrary
    # scheme/userinfo lengths and the original conservative credential check.
    for scheme in _URL_SCHEME.finditer(text):
        start = scheme.end()
        if not text.startswith('://', start):
            continue
        authority = _URL_AUTHORITY.match(text, start + 3)
        if authority is not None:
            value = authority[0]
            colon = value.find(':', 1)
            if colon != -1 and value.find('@', colon + 2) != -1:
                return True
    return False


def _sensitive(text):
    return bool(_SENSITIVE.search(text) or _sensitive_jwt(text) or _sensitive_url(text))


def _emitted_sensitive(text):
    # The CLI prints ensure_ascii JSON. Its backslash escape of a non-ASCII word
    # character can add a word boundary that the Unicode-aware string lacks.
    # Escape only non-ASCII here: escaping LF/TAB would merge separate tokens.
    return _sensitive(text) or _sensitive(text.encode('ascii', 'backslashreplace').decode('ascii'))


def _emitted_json_sensitive(text):
    # Final gate only: also screen the exact JSON payload the CLI prints. Its
    # TAB escape turns TAB + "oken = value" into a token assignment. Apply only
    # the keyword/credential patterns here: the URL and JWT heuristics would
    # treat escaped LF/TAB as joining separate tokens.
    return (_emitted_sensitive(text)
            or _SENSITIVE_JSON.search(json.dumps(text, ensure_ascii=True)[1:-1]) is not None)


def _error(category):
    # All callers supply fixed categories; never interpolate untrusted data.
    return LLMToolError('test-excerpts: ' + category + '; details omitted')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _inode(info):
    return info.st_dev, info.st_ino, info.st_mode


def _path(filename):
    value = os.fspath(filename)
    if (not isinstance(value, str) or not value or len(value) > MAX_PATH_BYTES
            or len(value.encode('utf-8')) > MAX_PATH_BYTES
            or any(unicodedata.category(c) in ('Cc', 'Cf') for c in value)
            or '\\' in value or '..' in value.split('/')):
        raise _error('invalid explicit file path')
    path = Path(os.path.abspath(value))
    if (len(str(path).encode('utf-8')) > MAX_PATH_BYTES
            or len(path.parts) > MAX_PATH_DEPTH
            or path.parts[1:2] in (('dev',), ('proc',), ('sys',))
            or any(part.lower() == '.git' for part in path.parts)
            or excluded_credential_path(path.as_posix())):
        raise _error('excluded or unbounded input path')
    return path


def _read_file(filename):
    """Pin every physical path component and compare before/open/after state.

    O_NONBLOCK prevents a raced-in FIFO from blocking before the regular-file
    check. No symlink resolution, directory listing, HOME/config lookup or write.
    """
    if os.name != 'posix' or not all(hasattr(os, name) for name in
                                    ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')):
        raise _error('no-follow file access is unavailable')
    descriptors, links = [], []
    try:
        path = _path(filename)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        parent = os.open(path.anchor, flags | os.O_DIRECTORY)
        descriptors.append(parent)
        for part in path.parts[1:-1]:
            before = os.stat(part, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise _error('input requires physical directories')
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
            descriptors.append(child)
            if _inode(before) != _inode(os.fstat(child)):
                raise _error('input directory changed')
            links.append((parent, part, child))
            parent = child
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise _error('input requires a single-link regular file')
        if not 0 <= before.st_size <= MAX_FILE_BYTES:
            raise _error('source exceeds 1 MiB')
        descriptor = os.open(path.name, flags, dir_fd=parent)
        descriptors.append(descriptor)
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise _error('input changed during open')
        chunks, size = [], 0
        while size <= MAX_FILE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (_identity(opened) != _identity(after) or _identity(opened) != _identity(current)
                or size != opened.st_size or size > MAX_FILE_BYTES):
            raise _error('input changed during bounded read')
        for directory, name, child in links:
            if _inode(os.stat(name, dir_fd=directory, follow_symlinks=False)) != _inode(os.fstat(child)):
                raise _error('input directory changed')
        return b''.join(chunks)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise _error('explicit file access failed') from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@dataclass(frozen=True)
class _Line:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class _Failure:
    identifier: str
    kind: str
    anchor: _Line
    end: int


def _lines(raw):
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise _error('source must be UTF-8 text') from None
    if any((unicodedata.category(c) in ('Cc', 'Cf') and c not in '\n\t\r')
           or (c == '\r' and (i + 1 == len(text) or text[i + 1] != '\n'))
           for i, c in enumerate(text)):
        raise _error('source contains binary or unsafe controls')
    if _sensitive(text):
        raise _error('source contains sensitive-content indicators')
    if raw.count(b'\n') > MAX_SOURCE_LINES:
        raise _error('source line count exceeds bound')
    lines, offset = [], 0
    # Split only LF, not Unicode separators; offsets always address original bytes.
    for part in raw.splitlines(keepends=True):
        line = part.decode('utf-8').removesuffix('\n').removesuffix('\r')
        lines.append(_Line(line, offset, offset + len(part)))
        offset += len(part)
    return lines


def _reason(reasons, value):
    if value not in reasons:
        reasons.append(value)


def _counts(text, labels, reasons):
    result = {}
    for item in text.split(', '):
        match = re.fullmatch(r'([0-9]{1,9}) ([a-z]+)', item)
        if not match or match[2] not in labels or labels[match[2]] in result:
            _reason(reasons, 'malformed_summary')
            return None
        result[labels[match[2]]] = int(match[1])
    return result


def _unittest(lines, reasons):
    failures, runs, summaries = [], [], []
    for index, line in enumerate(lines):
        run = _UNIT_RUN.fullmatch(line.text)
        if run:
            runs.append((index, int(run[1])))
        outcome = _UNIT_FAILED.fullmatch(line.text) or _UNIT_OK.fullmatch(line.text)
        if outcome and (index == 0 or not lines[index - 1].text):
            summaries.append((index, outcome))
        # Stdlib logging can emit this line between unittest progress tokens.
        # It is not a test header. Keep an ambiguous separator-anchored form
        # malformed, and retain the full-source and final-emission privacy gates.
        if (_LOG_ERROR.match(line.text)
                and (index == 0 or lines[index - 1].text != '=' * 70)):
            continue
        if not line.text.startswith(('FAIL:', 'ERROR:')):
            if re.match(r'(?:FAIL|ERROR)(?: |$)', line.text):
                _reason(reasons, 'malformed_failure_header')
            continue
        header = _UNIT_HEADER.fullmatch(line.text)
        if not header or len(header[2].encode('utf-8')) > MAX_ID_BYTES:
            _reason(reasons, 'malformed_failure_header')
            continue
        # Real unittest reports bracket the header with fixed separators. Do not
        # turn FAIL: text printed inside a traceback into another failure ID.
        dash = index + 1
        if dash < len(lines) and lines[dash].text != '-' * 70:
            dash += 1  # Optional first docstring line in verbose descriptions.
        if (index == 0 or lines[index - 1].text != '=' * 70
                or dash >= len(lines) or lines[dash].text != '-' * 70):
            _reason(reasons, 'unanchored_failure_header')
            continue
        end = dash + 1
        while end < len(lines) and lines[end].text not in ('=' * 70, '-' * 70) \
                and not _UNIT_RUN.fullmatch(lines[end].text):
            end += 1
        body = [item.text for item in lines[dash + 1:end] if item.text]
        if (not body or body[0] != 'Traceback (most recent call last):'
                or body[-1].startswith((' ', '\t', 'Traceback '))):
            _reason(reasons, 'incomplete_failure_block')
        failures.append(_Failure(header[2], header[1], line,
                                 lines[end - 1].end if end > dash + 1 else lines[dash].end))
    if len(runs) != 1 or len(summaries) != 1:
        _reason(reasons, 'multiple_runs' if len(runs) > 1 or len(summaries) > 1 else 'missing_summary')
        return failures, None, None, []
    run_index, run_count = runs[0]
    summary_index, outcome = summaries[0]
    if (summary_index <= run_index or any(line.text for line in lines[summary_index + 1:])
            or any(line.text for line in lines[run_index + 1:summary_index])):
        _reason(reasons, 'nonterminal_summary')
    values = {}
    if outcome[1]:
        for item in outcome[1].split(', '):
            pair = re.fullmatch(r'(failures|errors|skipped|expected failures|unexpected successes)=([0-9]{1,9})', item)
            if not pair or pair[1] in values:
                _reason(reasons, 'malformed_summary')
                return failures, None, run_count, []
            values[pair[1]] = int(pair[2])
    expected = {'FAIL': values.get('failures', 0), 'ERROR': values.get('errors', 0)}
    if (outcome[0].startswith('FAILED') != bool(sum(expected.values()))
            or values.get('unexpected successes', 0) or any(value == 0 for value in values.values())
            or outcome[0].startswith('OK') and any(key in values for key in ('failures', 'errors'))):
        _reason(reasons, 'unsupported_outcomes')
    if run_count < len(failures) + values.get('skipped', 0) + values.get('expected failures', 0):
        _reason(reasons, 'run_count_mismatch')
    return failures, expected, run_count, [lines[run_index], lines[summary_index]]


def _pytest_identifier(text):
    # Parameter values can contain spaces and " - ". Consider only prefixes
    # that are whole node IDs; ambiguous delimiters fail closed.
    if len(text.encode('utf-8')) > MAX_PYTEST_HEADER_BYTES or text.count(' - ') > 16:
        return None
    candidates = [text[:match.start()] for match in re.finditer(' - ', text)
                  if _NODE_ID.fullmatch(text[:match.start()])]
    if not candidates and _NODE_ID.fullmatch(text):
        candidates = [text]
    if len(candidates) != 1:
        return None
    identifier = candidates[0]
    if (any(part == '..' for part in identifier.split('::', 1)[0].split('/'))
            or len(identifier.encode('utf-8')) > MAX_ID_BYTES):
        return None
    return identifier


def _pytest(lines, reasons):
    failures, summaries, banners = [], [], []
    for index, line in enumerate(lines):
        if re.fullmatch(r'=+ short test summary info =+', line.text):
            banners.append(index)
        content = line.text
        wrapped = re.fullmatch(r'=+ (.*?) =+', content)
        summary = _PYTEST_STATS.fullmatch(wrapped[1] if wrapped else content)
        if summary:
            summaries.append((index, summary[1]))
        # Captured log/stderr sections precede the single summary banner, and
        # failure headers count only after it, so a log line there cannot hide
        # or add an ID. After the banner, keep log-shaped lines malformed.
        if not banners and _PYTEST_LOG_ERROR.match(line.text):
            continue
        header = re.match(r'(FAILED|ERROR) (.*)', line.text)
        if not header:
            if re.match(r'(?:FAIL|FAILED|ERROR)(?:[: ]|$)', line.text):
                _reason(reasons, 'malformed_failure_header')
            continue
        identifier = _pytest_identifier(header[2])
        if identifier is None:
            _reason(reasons, 'malformed_failure_header')
            continue
        if not banners or index <= banners[-1]:
            _reason(reasons, 'unanchored_failure_header')
            continue
        failures.append(_Failure(identifier, 'FAIL' if header[1] == 'FAILED' else 'ERROR', line, line.end))
    if len(summaries) != 1 or len(banners) > 1:
        _reason(reasons, 'multiple_runs' if len(summaries) > 1 or len(banners) > 1 else 'missing_summary')
        return failures, None, None, []
    index, text = summaries[0]
    if any(line.text for line in lines[index + 1:]):
        _reason(reasons, 'nonterminal_summary')
    if failures and any(item.anchor.start >= lines[index].start for item in failures):
        _reason(reasons, 'nonterminal_summary')
    values = _counts(text, {'failed': 'FAIL', 'error': 'ERROR', 'errors': 'ERROR',
                           'passed': 'passed', 'skipped': 'skipped', 'deselected': 'deselected',
                           'xfailed': 'xfailed', 'xpassed': 'xpassed',
                           'warning': 'warnings', 'warnings': 'warnings'}, reasons)
    if values is not None and (any(value == 0 for value in values.values())
            or not any(values.get(key, 0) for key in ('FAIL', 'ERROR', 'passed', 'skipped', 'xfailed', 'xpassed'))):
        _reason(reasons, 'unsupported_outcomes')
    expected = None if values is None else {'FAIL': values.get('FAIL', 0), 'ERROR': values.get('ERROR', 0)}
    return failures, expected, None, [lines[index]]


def select_test_excerpts(filename, test_exit_code, *, max_excerpt_bytes=MAX_EXCERPT_BYTES,
                         max_excerpts=MAX_EXCERPTS):
    """Read one explicit raw capture and return version-1 JSON-compatible data.

    No model summaries are inputs. Capture origin/exit metadata are supplied by
    the caller, not inferred or authenticated by this syntax-only utility.
    """
    if type(test_exit_code) is not int or not -127 <= test_exit_code <= 255:
        raise _error('invalid captured exit code')
    if (type(max_excerpt_bytes) is not int or not 128 <= max_excerpt_bytes <= MAX_EXCERPT_BYTES
            or type(max_excerpts) is not int or not 1 <= max_excerpts <= MAX_EXCERPTS):
        raise _error('invalid excerpt bounds')
    raw = _read_file(filename)
    lines = _lines(raw)
    reasons = []
    if raw and not raw.endswith(b'\n'):
        _reason(reasons, 'unterminated_source')
    unit = any(_UNIT_HEADER.fullmatch(line.text) or _UNIT_RUN.fullmatch(line.text)
               or _UNIT_FAILED.fullmatch(line.text) or _UNIT_OK.fullmatch(line.text) for line in lines)
    pytest = any(line.text.startswith('FAILED ') and not _UNIT_FAILED.fullmatch(line.text)
                 or re.fullmatch(r'=+ short test summary info =+', line.text)
                 or _PYTEST_STATS.fullmatch(line.text.strip('= ')) for line in lines)
    detected = 'mixed' if unit and pytest else 'unittest' if unit else 'pytest' if pytest else 'unknown'
    if detected in ('mixed', 'unknown'):
        _reason(reasons, 'mixed_formats' if detected == 'mixed' else 'unknown_format')
        failures, expected, run_count, summaries = [], None, None, []
    else:
        failures, expected, run_count, summaries = (_unittest if unit else _pytest)(lines, reasons)
    observed = Counter(item.kind for item in failures)
    if expected is not None:
        if any(observed[kind] != expected[kind] for kind in ('FAIL', 'ERROR')):
            _reason(reasons, 'failure_count_mismatch')
        if bool(sum(expected.values())) != bool(test_exit_code):
            _reason(reasons, 'exit_summary_mismatch')
    if test_exit_code and not failures:
        _reason(reasons, 'nonzero_exit_without_failure_ids')
    digest = hashlib.sha256(raw).hexdigest()
    reference = 'sha256:' + digest

    def span(start, end):
        return {'source_ref': reference, 'start_byte': start, 'end_byte': end}

    sources = {}
    for item in failures:
        if item.identifier not in sources:
            sources[item.identifier] = {'id': item.identifier, 'kinds': [], 'occurrences': 0,
                                        'anchor': span(item.anchor.start, item.anchor.end)}
        source = sources[item.identifier]
        source['occurrences'] += 1
        if item.kind not in source['kinds']:
            source['kinds'].append(item.kind)
    selected_ids = list(sources)[:MAX_FAILURE_IDS]
    omitted_ids = len(sources) - len(selected_ids)
    if omitted_ids:
        _reason(reasons, 'failure_id_limit')
    excerpts, used, truncated = [], 0, 0
    for item in failures:
        if (len(excerpts) >= max_excerpts or used >= MAX_TOTAL_EXCERPT_BYTES
                or item.identifier not in selected_ids):
            continue
        start = item.anchor.start
        end = min(item.end, start + max_excerpt_bytes, start + MAX_TOTAL_EXCERPT_BYTES - used)
        # Never dump the whole source, even for a tiny partial capture.
        if start == 0 and end == len(raw):
            continue
        text = raw[start:end].decode('utf-8', errors='ignore')
        end = start + len(text.encode('utf-8'))
        if end == start:
            continue
        partial = end < item.end
        excerpts.append(dict(span(start, end), id=item.identifier, kind=item.kind,
                             text=text, truncated=partial))
        used += end - start
        truncated += int(partial)
    # Byte, UTF-8 and aggregate cuts can add a word boundary the full source
    # lacked (e.g. AKIA + 16 characters cut out of a longer word). Re-check the
    # exact emitted strings and fail closed instead of dropping or redacting them.
    if any(_emitted_json_sensitive(value) for value in
           selected_ids + [item['text'] for item in excerpts]):
        raise _error('emitted excerpt contains sensitive-content indicators')
    incomplete = bool(reasons)
    test_status = ('FAILED' if test_exit_code or failures or expected and sum(expected.values())
                   else 'UNKNOWN' if incomplete else 'NO_FAILURES_REPORTED')
    return {
        'schema_version': 1,
        'source': {'kind': 'captured_test_output', 'ref': reference, 'sha256': digest,
                   'size_bytes': len(raw)},
        'test_exit_code': test_exit_code,
        'detected_format': detected,
        'status': 'INCOMPLETE' if incomplete else 'COMPLETE',
        'test_status': test_status,
        'failure_ids': selected_ids,
        'failure_sources': [sources[identifier] for identifier in selected_ids],
        'excerpts': excerpts,
        'summary_sources': [span(line.start, line.end) for line in summaries],
        'coverage': {'failure_ids_complete': not incomplete, 'unknown': incomplete,
                     'failure_ids_omitted': bool(omitted_ids),
                     'excerpts_omitted': bool(truncated or len(failures) > len(excerpts)),
                     'source_content_omitted': len(raw) > used,
                     'unknown_reasons': reasons, 'reported_counts': expected,
                     'observed_counts': {kind: observed[kind] for kind in ('FAIL', 'ERROR')},
                     'run_count': run_count},
        'omitted': {'failure_ids': omitted_ids, 'excerpts': len(failures) - len(excerpts),
                    'truncated_excerpts': truncated,
                    'excerpt_bytes': sum(item.end - item.anchor.start for item in failures) - used,
                    'source_bytes': len(raw) - used},
    }
