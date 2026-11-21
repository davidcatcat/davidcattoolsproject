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
    patterns = [_ere(name) + _WORD_END]
    if name.endswith('.py'):
        leaf = name[:-3]
        parts = path[:-3].split('/')
        patterns.append('(import|from) (.*[^A-Za-z0-9_.])?' + _ere(leaf) + _WORD_END)
        if parts[:3] == ['tools', 'llmtool', 'llmtool']:
            patterns.append(_ere('.'.join(['llmtool'] + parts[3:])) + _WORD_END)
    return patterns


def referencing_tests(*, root, commit, read_paths, write_paths, runner=None):
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
