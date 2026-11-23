"""#2503: advisory scope checks before a Developer dispatch or repair.

A Developer reads only its read_paths. When the goal, an acceptance item, a
repair's review findings or an AI Debug (RCA) report names a repository file
that is not in read_paths, the Developer stops (AGY answers BLOCKED) or works
from incomplete code (#2494). These checks only list such problems as advice
items. They never block a dispatch: the caller shows the list to the PM and
records it on the Task with the PM's optional reason.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone

# Same path shape as tools/llmtool/scripts/spec_preflight.py REPO_PATH_PATTERN.
REPO_PATH_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9_./-])"
    r"((?:tools|scripts|docs|\.claude)/[a-zA-Z0-9_/-]+(?:\.[a-zA-Z0-9_/-]+)*\.(?:py|md|json))"
    r"(?![a-zA-Z0-9_/-]|\.[a-zA-Z0-9])"
)
READ_MISSING = 'READ_MISSING'
MAX_ITEMS = 64
MAX_PATH_CHARS = 256
MAX_NOTE_CHARS = 300
MAX_REASON_CHARS = 500
MAX_ENTRIES = 16
MAX_TEXT_CHARS = 512 * 1024


def _texts(sources):
    items = []
    for source in sources:
        if source is None:
            continue
        if type(source) is str:
            items.append(source)
        elif type(source) in (list, tuple):
            items.extend(_texts(source))
        else:
            items.append(json.dumps(source, ensure_ascii=False, sort_keys=True, default=str))
    return [text[:MAX_TEXT_CHARS] for text in items]


def named_paths(*sources):
    """Repository paths named in the given texts, in first-seen order, without duplicates."""
    found = []
    for text in _texts(sources):
        for path in REPO_PATH_PATTERN.findall(text):
            path = path.strip('/')
            if (path not in found and len(path) <= MAX_PATH_CHARS and
                    '..' not in path.split('/')):
                found.append(path)
    return found


def missing_read_paths(*, texts, read_paths, write_paths, exists):
    """Sorted paths named in texts that exist (per exists()) and are in neither read nor write."""
    allowed = {path.strip('/') for path in list(read_paths or []) + list(write_paths or [])
               if type(path) is str}
    return sorted(path for path in named_paths(texts)
                  if path not in allowed and exists(path))[:MAX_ITEMS]


def git_exists(root, commit='HEAD'):
    """exists() for missing_read_paths: the path is a file at commit in root."""
    def exists(path):
        try:
            result = subprocess.run(
                ['git', '-C', str(root), 'cat-file', '-e', f'{commit}:{path}'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0
    return exists


def item(kind, path=None, note=None):
    return {'kind': kind, 'path': path, 'note': note}


def entry(items, reason, source_sha=None):
    """One TaskStore read_scope_advice entry."""
    return {'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'items': list(items)[:MAX_ITEMS], 'pm_reason': reason, 'source_sha': source_sha}


def warning(items):
    lines = ['SCOPE_ADVICE (#2503): the dispatch continues; fix the spec and resend, or pass '
             '--read-scope-reason to record why it proceeds.']
    for value in items:
        text = '- ' + value['kind']
        if value.get('path'):
            text += ' ' + value['path']
        if value.get('note'):
            text += ': ' + value['note']
        lines.append(text)
    return '\n'.join(lines)


# --- #2503 PR-5: companion-file advice -------------------------------------

COMPANION_WRITE = 'COMPANION_WRITE'
COMPANION_READ = 'COMPANION_READ'
TEST_REFERENCES_WRITE = 'TEST_REFERENCES_WRITE'
TEST_REFERENCES_MORE = 'TEST_REFERENCES_MORE'
# #2602u: a test outside write that directly exercises a changed name.
TEST_LOCKS_CHANGE = 'TEST_LOCKS_CHANGE'
TEST_REFERENCES_CAPPED = 'TEST_REFERENCES_CAPPED'
GUIDANCE_DRIFT_JSON = 'tools/llmtool/guidance-drift.json'
GUIDANCE_DRIFT_CHECK = 'tools/llmtool/scripts/guidance_drift_check.py'
_HASHED_GUIDANCE = ('CLAUDE.md', 'AGENTS.md')
MAX_TESTS_PER_PATH = 8
_TEST_DIRS = ('tools/llmtool/tests', 'scripts/test_*.py')
_WORD_END = '([^A-Za-z0-9_]|$)'


def _is_rule_json(path):
    """A tools/llmtool/<name>.json rule file; guidance_drift_check.py holds its strict schema."""
    parts = path.split('/')
    return (len(parts) == 3 and parts[:2] == ['tools', 'llmtool'] and
            parts[2].endswith('.json') and not parts[2].startswith('.'))


def companion_items(*, read_paths, write_paths):
    """Fixed pairings (#2503 PR-5 a): files that change or must be read together."""
    read = set(read_paths or [])
    write = set(write_paths or [])
    items = []
    if write & set(_HASHED_GUIDANCE) and GUIDANCE_DRIFT_JSON not in write:
        items.append(item(COMPANION_WRITE, GUIDANCE_DRIFT_JSON,
                          'records the CLAUDE.md/AGENTS.md hash; without it HASH_STALE'))
    rule_files = sorted(path for path in write
                        if _is_rule_json(path) and path != GUIDANCE_DRIFT_JSON)
    if rule_files and GUIDANCE_DRIFT_CHECK not in read | write:
        items.append(item(COMPANION_READ, GUIDANCE_DRIFT_CHECK,
                          ('holds the strict schema for ' + ', '.join(rule_files))[:MAX_NOTE_CHARS]))
    return items


def _ere(text):
    return re.sub(r'([.^$*+?()\[\]{}|\\])', r'\\\1', text)


def reference_patterns(path):
    """git grep -E patterns (POSIX ERE, no \\b) that reference the file at path."""
    name = path.rsplit('/', 1)[-1]
    patterns = ['(^|[^A-Za-z0-9_.-])' + _ere(name) + _WORD_END]
    if name.endswith('.py'):
        leaf = name[:-3]
        parts = path[:-3].split('/')
        patterns.append('(import|from) (.*[^A-Za-z0-9_.])?' + _ere(leaf) + _WORD_END)
        if parts[:3] == ['tools', 'llmtool', 'llmtool']:
            patterns.append(_ere('.'.join(['llmtool'] + parts[3:])) + _WORD_END)
    return patterns


def _module_referencing_tests(*, root, commit, read_paths, write_paths, runner=None):
    """Test files at commit that reference a written file but are in neither read nor write.

    Bounded: one git grep per written non-test .py/.json file over
    tools/llmtool/tests and scripts/test_*.py, at most MAX_TESTS_PER_PATH
    results each. Any error gives no result for that file.
    """
    run = runner or subprocess.run
    known = set(read_paths or []) | set(write_paths or [])
    found = {}
    more = []
    for path in sorted(set(write_paths or [])):
        name = path.rsplit('/', 1)[-1]
        if not name.endswith(('.py', '.json')) or name.startswith('test_') or '/tests/' in path:
            continue
        argv = ['git', '-C', str(root), 'grep', '-l', '-E']
        for pattern in reference_patterns(path):
            argv += ['-e', pattern]
        argv += [commit, '--', *_TEST_DIRS]
        try:
            result = run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         timeout=20, check=False)
        except Exception:
            continue
        if getattr(result, 'returncode', 2) not in (0, 1):
            continue
        names = []
        for line in (result.stdout or '').splitlines():
            test = line[len(commit) + 1:] if line.startswith(commit + ':') else line
            if test and test not in known and test != path:
                names.append(test)
        leaf = name.rsplit('.', 1)[0]
        # Tests named after the file come first; the rest follow by name.
        ranked = sorted(set(names), key=lambda test: (leaf not in test.rsplit('/', 1)[-1], test))
        for test in ranked[:MAX_TESTS_PER_PATH]:
            found.setdefault(test, path)
        if len(ranked) > MAX_TESTS_PER_PATH:
            more.append(item(TEST_REFERENCES_MORE, path,
                             '%d more test files reference it; shown: the first %d'
                             % (len(ranked) - MAX_TESTS_PER_PATH, MAX_TESTS_PER_PATH)))
    return [item(TEST_REFERENCES_WRITE, test,
                 ('references ' + source + '; it may pin the old behaviour')[:MAX_NOTE_CHARS])
            for test, source in sorted(found.items())] + more


# --- #2602u: list only the tests that exercise the names a spec changes ------

MAX_PRECISE_IDENTIFIERS = 16  # Bound per-dispatch identifier fan-out from spec texts (CWE-400).
MAX_PRECISE_GREPS = 16  # Bound git grep subprocess count per precise search (CWE-400).
PRECISE_SEARCH_MAX_SECONDS = 15.0  # Bound total elapsed time of precise search (CWE-400).
PRECISE_CALL_TIMEOUT_SECONDS = 20  # Per-call timeout cap for precise search git calls (CWE-400).


class _PreciseSearchCapped(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise _PreciseSearchCapped('time limit %.1fs reached' % PRECISE_SEARCH_MAX_SECONDS)

_CHANGE_VERB = re.compile(
    r'\b(?:add|adjust|change|edit|extend|fix|introduce|modify|move|refactor|'
    r'remove|rename|replace|update)\b', re.IGNORECASE)
_CODE_NAME = re.compile(r'`\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^`]*\))?\s*`')
_CALL_NAME = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(')
_IDENTIFIER = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]{2,})(?![A-Za-z0-9_])')
_DEFINED_NAME = re.compile(
    r'^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE)
_ASSIGNED_NAME = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)[ \t]*=', re.MULTILINE)
# After a change verb these are prose, not names the spec is changing.
_STOP_WORDS = frozenset('''
    add adjusted after and are before change changed changes editing edits for
    from function functions into item items its kind kinds list lists module
    modules new not note notes only path paths test tests text texts the their
    them then this to update updated updates use used using value values when
    with
'''.split())


def _module_sources(run, root, commit, write_paths, *, deadline=None):
    """Sources at commit of the written non-test Python modules; {} on any read error."""
    sources = {}
    for path in sorted(set(write_paths or [])):
        name = path.rsplit('/', 1)[-1]
        if not name.endswith('.py') or name.startswith('test_') or '/tests/' in path:
            continue
        call_timeout = PRECISE_CALL_TIMEOUT_SECONDS
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _PreciseSearchCapped('time limit %.1fs reached' % PRECISE_SEARCH_MAX_SECONDS)
            call_timeout = min(PRECISE_CALL_TIMEOUT_SECONDS, remaining)
        try:
            result = run(['git', '-C', str(root), 'show', '%s:%s' % (commit, path)],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         timeout=call_timeout, check=False)
        except subprocess.TimeoutExpired:
            _check_deadline(deadline)
            raise
        _check_deadline(deadline)
        if getattr(result, 'returncode', 2) != 0:
            continue
        sources[path] = result.stdout or ''
    return sources


def _defined_names(sources):
    """Names defined in the given module sources; paths by name, plus the callable names."""
    definitions = {}
    callable_names = set()

    def add(name, path):
        paths = definitions.setdefault(name, [])
        if path not in paths:
            paths.append(path)

    for path, source in sources.items():
        for match in _DEFINED_NAME.finditer(source or ''):
            name = match.group(1)
            callable_names.add(name)
            add(name, path)
        for match in _ASSIGNED_NAME.finditer(source or ''):
            add(match.group(1), path)
    return definitions, callable_names


def _candidate_names(text, definitions):
    """Names in one text that are defined in a written module, in first-seen order."""
    found = []

    def add(name):
        if name in definitions and name not in found:
            found.append(name)

    for match in _CODE_NAME.finditer(text):
        add(match.group(1))
    for match in _CALL_NAME.finditer(text):
        add(match.group(1))
    for verb in _CHANGE_VERB.finditer(text):
        for match in _IDENTIFIER.finditer(text[verb.end():verb.end() + 80]):
            name = match.group(1)
            if name.lower() not in _STOP_WORDS:
                add(name)
    return found


def _precise_referencing_tests(*, root, commit, read_paths, write_paths, texts, runner=None):
    """Tests that directly exercise names the spec texts change; None when no name is found.

    Any error propagates to referencing_tests(), which keeps the module-level list.
    """
    run = runner or subprocess.run
    given = [text for text in _texts(texts) if text.strip()]
    if not given:
        return None
    deadline = time.monotonic() + PRECISE_SEARCH_MAX_SECONDS
    definitions, callable_names = _defined_names(
        _module_sources(run, root, commit, write_paths, deadline=deadline))
    names = []
    for text in given:
        for name in _candidate_names(text, definitions):
            if name not in names:
                names.append(name)
    if not names:
        return None
    if len(names) > MAX_PRECISE_IDENTIFIERS:
        raise _PreciseSearchCapped(
            'identifier limit %d reached (%d matched)' % (MAX_PRECISE_IDENTIFIERS, len(names)))
    write = {path.strip('/') for path in write_paths or [] if type(path) is str}
    found = {}
    more = []
    greps = 0
    for name in names:
        if greps >= MAX_PRECISE_GREPS:
            raise _PreciseSearchCapped('git grep limit %d reached' % MAX_PRECISE_GREPS)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _PreciseSearchCapped('time limit %.1fs reached' % PRECISE_SEARCH_MAX_SECONDS)
        if name in callable_names:
            # A function or class is exercised by a call, not by an import alone.
            pattern = '(^|[^A-Za-z0-9_])' + _ere(name) + '[[:space:]]*\\('
        else:
            pattern = '(^|[^A-Za-z0-9_])' + _ere(name) + '([^A-Za-z0-9_]|$)'
        argv = ['git', '-C', str(root), 'grep', '-l', '-E', '-e', pattern,
                commit, '--', *_TEST_DIRS]
        greps += 1
        try:
            result = run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         timeout=min(PRECISE_CALL_TIMEOUT_SECONDS, remaining), check=False)
        except subprocess.TimeoutExpired:
            _check_deadline(deadline)
            raise
        _check_deadline(deadline)
        if getattr(result, 'returncode', 2) not in (0, 1):
            raise RuntimeError('git grep failed for ' + name)
        tests = []
        for line in (result.stdout or '').splitlines():
            test = line[len(commit) + 1:] if line.startswith(commit + ':') else line
            if test and test not in tests:
                tests.append(test)
        tests.sort()
        for test in tests[:MAX_TESTS_PER_PATH]:
            if test not in write:
                found.setdefault(test, [])
                if name not in found[test]:
                    found[test].append(name)
        if len(tests) > MAX_TESTS_PER_PATH:
            more.append(item(TEST_REFERENCES_MORE, (definitions.get(name) or [None])[0],
                             ('%d test files directly exercise %s; shown: the first %d'
                              % (len(tests), name, MAX_TESTS_PER_PATH))[:MAX_NOTE_CHARS]))
    return [item(TEST_LOCKS_CHANGE, test,
                 ('may lock the behaviour the acceptance changes; it belongs in write under '
                  'the locked-test rule; directly exercises ' +
                  ', '.join(found[test]))[:MAX_NOTE_CHARS])
            for test in sorted(found)] + more


def referencing_tests(*, root, commit, read_paths, write_paths, runner=None, texts=None):
    """Test files to show the PM for the files being written.

    Without texts (the historical input) this returns exactly the module-level
    reference list. With texts (the goal and acceptance), it returns only tests
    that directly exercise names the texts change, and falls back to the
    module-level list when no changed name can be identified or the new logic
    errors. The advice stays fail-open either way.
    """
    if texts is not None:
        try:
            precise = _precise_referencing_tests(
                root=root, commit=commit, read_paths=read_paths, write_paths=write_paths,
                texts=texts, runner=runner)
            if precise is not None:
                return precise
        except _PreciseSearchCapped as exc:
            note = ('precise test search was capped (%s); using module-level list'
                    % exc.reason)[:MAX_NOTE_CHARS]
            return _module_referencing_tests(
                root=root, commit=commit, read_paths=read_paths, write_paths=write_paths,
                runner=runner) + [item(TEST_REFERENCES_CAPPED, None, note)]
        except Exception:
            pass
    return _module_referencing_tests(
        root=root, commit=commit, read_paths=read_paths, write_paths=write_paths, runner=runner)


# --- #2503 PR-6: route capability and size advice ----------------------------

ROUTE_PATH_DENIED = 'ROUTE_PATH_DENIED'
ROUTE_NO_SHELL = 'ROUTE_NO_SHELL'
READ_FILE_TOO_LARGE = 'READ_FILE_TOO_LARGE'
READ_TOTAL_TOO_LARGE = 'READ_TOTAL_TOO_LARGE'
WRITE_FILE_TOO_LONG = 'WRITE_FILE_TOO_LONG'
_SHELL_ASK = re.compile(
    r"\b(?:run|execute|invoke)\b[^.\n]{0,60}?\b(?:tests?|pytest|unittest|make|commands?|"
    r"scripts?|shell|bash)\b|\bgit\s+(?:commit|push|add|checkout|rebase|merge|stash|reset|switch)\b",
    re.IGNORECASE)
# "Do not run tests" is a limit, not an ask.
_NEGATED = re.compile(r"\b(?:not|never|no|don't|do not|without|nor)\b[^.\n]*$", re.IGNORECASE)


def route_path_items(provider, paths, *, validate=None):
    """read/write paths the route's own path validator refuses (#2503 PR-6 a)."""
    if provider != 'opencode':
        return []
    if validate is None:
        from .action_contract import _path as validate
    items = []
    for path in sorted(set(paths or [])):
        try:
            validate(path)
        except Exception:
            items.append(item(ROUTE_PATH_DENIED, path,
                              'OpenCode refuses this path (action_contract path rule)'))
    return items


def _allows_shell(value):
    """True if an OpenCode permission envelope allows a shell-like action anywhere."""
    if type(value) is dict:
        if (value.get('effect') == 'allow' and
                value.get('action') in ('bash', 'shell', 'command', 'exec', '*')):
            return True
        return any(_allows_shell(item) for item in value.values())
    if type(value) in (list, tuple):
        return any(_allows_shell(item) for item in value)
    return False


def route_has_shell(provider):
    """Shell/Git ability from the route's own tool configuration; None when unknown."""
    try:
        if provider == 'claude':
            from .developer_routes.claude import ClaudeRoute
            argv = ClaudeRoute.build_argv(None, 'claude',
                                          {'model': 'm', 'effort': 'medium', 'prompt': 'p'})
            return 'Bash' in argv[argv.index('--tools') + 1].split(',')
        if provider == 'gw-agent':
            from .developer_routes.gw_agent import _AUDIT_TOOLS
            return any(name in _AUDIT_TOOLS for name in ('shell', 'bash', 'run_command'))
        if provider == 'opencode':
            from .opencode_direct_developer import configuration
            return _allows_shell(configuration(read_paths=['a.py'], write_paths=['a.py']))
    except Exception:
        return None
    return None


def shell_items(provider, texts):
    """The spec asks for a shell, test or Git step the route cannot do (#2503 PR-6 b)."""
    if route_has_shell(provider) is not False:
        return []
    for text in _texts(texts):
        match = next((found for found in _SHELL_ASK.finditer(text)
                      if not _NEGATED.search(text[max(0, found.start() - 24):found.start()])),
                     None)
        if match:
            return [item(ROUTE_NO_SHELL, None,
                         (provider + ' has no shell or Git; the spec asks: ' +
                          match.group(0))[:MAX_NOTE_CHARS])]
    return []


def route_size_limits(provider):
    """The route's existing configured limits; {} when it has none (no new numbers)."""
    if provider == 'opencode':
        from .opencode_direct_developer import (resolved_max_source_file_bytes,
                                                resolved_max_source_total_bytes)
        return {'read_file_bytes': resolved_max_source_file_bytes(),
                'read_total_bytes': resolved_max_source_total_bytes()}
    if provider == 'gw-agent':
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'spec_preflight.py'
        spec = importlib.util.spec_from_file_location('_llmtool_spec_preflight_limits', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {'write_file_lines': module.large_file_lines()}
    return {}


def size_items(provider, root, read_paths, write_paths, *, limits=None):
    """Files over the route's configured limits (#2503 PR-6 c)."""
    from pathlib import Path
    limits = route_size_limits(provider) if limits is None else limits
    if not limits:
        return []
    items, total = [], 0

    def data(path):
        try:
            file = Path(root) / path
            return file.read_bytes() if file.is_file() and not file.is_symlink() else None
        except OSError:
            return None

    for path in sorted(set(read_paths or [])):
        content = data(path)
        if content is None:
            continue
        total += len(content)
        limit = limits.get('read_file_bytes')
        if type(limit) is int and len(content) > limit:
            items.append(item(READ_FILE_TOO_LARGE, path,
                              '%d bytes > route limit %d' % (len(content), limit)))
    limit = limits.get('read_total_bytes')
    if type(limit) is int and total > limit:
        items.append(item(READ_TOTAL_TOO_LARGE, None,
                          'read total %d bytes > route limit %d' % (total, limit)))
    limit = limits.get('write_file_lines')
    if type(limit) is int:
        for path in sorted(set(write_paths or [])):
            content = data(path)
            if content is not None and content.count(b'\n') > limit:
                items.append(item(WRITE_FILE_TOO_LONG, path,
                                  '%d lines > route guide %d (policy.json)'
                                  % (content.count(b'\n'), limit)))
    return items
