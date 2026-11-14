"""Resolve governance files from an immutable Git commit."""

import hashlib
import json
import os
import re
import subprocess


class SkillManifestError(Exception):
    """A safe, code-only failure while building a skill manifest."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


SKILL_MANIFEST_COMMIT_INVALID = 'SKILL_MANIFEST_COMMIT_INVALID'
SKILL_MANIFEST_PATH_MISSING = 'SKILL_MANIFEST_PATH_MISSING'
SKILL_MANIFEST_NOT_FILE = 'SKILL_MANIFEST_NOT_FILE'
SKILL_MANIFEST_NOT_UTF8 = 'SKILL_MANIFEST_NOT_UTF8'
SKILL_MANIFEST_TOO_LARGE = 'SKILL_MANIFEST_TOO_LARGE'
SKILL_MANIFEST_ROLE_INVALID = 'SKILL_MANIFEST_ROLE_INVALID'
SKILL_MANIFEST_GATE_INVALID = 'SKILL_MANIFEST_GATE_INVALID'
SKILL_MANIFEST_ROUTE_INVALID = 'SKILL_MANIFEST_ROUTE_INVALID'
SKILL_MANIFEST_PREFIX_TOO_LARGE = 'SKILL_MANIFEST_PREFIX_TOO_LARGE'
SKILL_MANIFEST_MAIN_REF_INVALID = 'SKILL_MANIFEST_MAIN_REF_INVALID'
SKILL_MANIFEST_COMMIT_NOT_ON_MAIN = 'SKILL_MANIFEST_COMMIT_NOT_ON_MAIN'
# #1931: a well-formed commit id whose object is absent from this repository.
SKILL_MANIFEST_COMMIT_NOT_LOCAL = 'SKILL_MANIFEST_COMMIT_NOT_LOCAL'

# Role cards are read only from a commit on main as seen at dispatch.
DEFAULT_MAIN_REF = 'refs/remotes/origin/main'
_MAIN_REF_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$')

MAX_SKILL_FILE_SIZE = 1024 * 1024
# WP-F design 3.7: the whole Developer rule prefix (common core + card) stays
# within 14 KiB so at least 50 KiB of a 64 KiB prompt is left for the Task.
MAX_DEVELOPER_PREFIX_SIZE = 14 * 1024
GIT_READ_TIMEOUT_SECONDS = 10

COMMON_CORE_CARD_PATH = 'skills/role-cards/common-core.md'
DEVELOPER_CARD_PATH = 'skills/role-cards/developer.md'
# Role-card table (WP-F design 3.7, #1502): common core first, then the
# Developer card, in prompt order. Every Developer route receives the
# common-core card. gw-agent has no card delivery yet (its runs record
# NOT_DELIVERED ROUTE_NOT_SUPPORTED); its row names the cards it would get.
# The card file holds every route appendix, so all Developer routes share
# one card.
_ROLE_CARD_TABLE = {
    'agy': (COMMON_CORE_CARD_PATH, DEVELOPER_CARD_PATH),
    'claude-developer': (COMMON_CORE_CARD_PATH, DEVELOPER_CARD_PATH),
    'gw-agent': (COMMON_CORE_CARD_PATH, DEVELOPER_CARD_PATH),
    'opencode-developer': (COMMON_CORE_CARD_PATH, DEVELOPER_CARD_PATH),
}

_BASE_PATHS = (
    'AGENTS.md',
    'skills/ai-team-delivery/SKILL.md',
)
_CLAUDE_BASE_PATHS = (
    'CLAUDE.md',
    'skills/ai-team-delivery/SKILL.md',
)
_PM_PATHS = (
    'skills/ai-team-delivery/pm-continuation.md',
    'skills/ai-team-delivery/pm-continuation-reference.md',
)
_REVIEW_PATHS = (
    'skills/review-change/SKILL.md',
    'skills/ai-team-delivery/formal-verdict.md',
)
_GIT_PATHS = ('skills/safe-git/SKILL.md',)
_MANIFEST_TABLE = {
    'pm': {
        'dispatch': _BASE_PATHS + _PM_PATHS,
        'develop': _BASE_PATHS + _PM_PATHS,
        'review': _BASE_PATHS + _PM_PATHS + _REVIEW_PATHS,
        'complete': _BASE_PATHS + _PM_PATHS,
        'publish': _BASE_PATHS + _PM_PATHS + _GIT_PATHS,
        'merge': _BASE_PATHS + _PM_PATHS + _GIT_PATHS,
    },
    'developer': {
        'dispatch': _BASE_PATHS,
        'develop': _BASE_PATHS,
        'review': _BASE_PATHS + _REVIEW_PATHS,
        'complete': _BASE_PATHS,
        'publish': _BASE_PATHS + _GIT_PATHS,
        'merge': _BASE_PATHS + _GIT_PATHS,
    },
    'reviewer': {
        'dispatch': _BASE_PATHS,
        'develop': _BASE_PATHS,
        'review': _BASE_PATHS + _REVIEW_PATHS,
        'complete': _BASE_PATHS,
        'publish': _BASE_PATHS + _GIT_PATHS,
        'merge': _BASE_PATHS + _GIT_PATHS,
    },
    'high-debug': {
        'dispatch': _BASE_PATHS,
        'develop': _BASE_PATHS,
        'review': _BASE_PATHS + _REVIEW_PATHS,
        'complete': _BASE_PATHS,
        'publish': _BASE_PATHS + _GIT_PATHS,
        'merge': _BASE_PATHS + _GIT_PATHS,
    },
}
_COMMIT_RE = re.compile(r'^[0-9a-fA-F]{40}$')


def _raise(code):
    raise SkillManifestError(code)


def resolve(role, gate):
    """Return the sorted required path tuple for one role and gate."""
    if type(role) is not str or role not in _MANIFEST_TABLE:
        _raise(SKILL_MANIFEST_ROLE_INVALID)
    gates = _MANIFEST_TABLE[role]
    if type(gate) is not str or gate not in gates:
        _raise(SKILL_MANIFEST_GATE_INVALID)
    return tuple(sorted(gates[gate]))


def _run_git(git_runner, repo_root, args):
    """Run one bounded Git read, suppressing stderr from safe errors."""
    try:
        # Environment variables such as GIT_DIR and GIT_WORK_TREE can redirect
        # this read away from repo_root. Do not inherit any Git-specific state.
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith('GIT_')
        }
        result = git_runner(
            [
                'git', '--no-replace-objects', '-c',
                'core.useReplaceRefs=false',
            ] + list(args),
            cwd=repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    return result


def _stdout(result):
    try:
        value = getattr(result, 'stdout', None)
        if type(value) is bytes:
            return value
        if type(value) is str:
            return value.encode('utf-8')
    except Exception:
        pass
    return b''


def _valid_object_id(value):
    return (
        type(value) is bytes
        and re.fullmatch(rb'[0-9a-fA-F]{40}', value) is not None
    )


def _read_blob(git_runner, repo_root, commit, path):
    tree = _run_git(
        git_runner, repo_root,
        ['ls-tree', '--full-tree', '-z', commit, '--', path],
    )
    if tree is None or getattr(tree, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_PATH_MISSING)

    records = [record for record in _stdout(tree).split(b'\0') if record]
    selected = None
    for record in records:
        metadata, separator, recorded_path = record.partition(b'\t')
        if not separator or recorded_path.decode('utf-8', 'replace') != path:
            continue
        fields = metadata.split()
        if len(fields) == 3:
            selected = fields
            break
    if selected is None:
        _raise(SKILL_MANIFEST_PATH_MISSING)

    mode, object_type, object_id = selected
    if mode == b'120000' or object_type != b'blob':
        _raise(SKILL_MANIFEST_NOT_FILE)
    if not _valid_object_id(object_id):
        _raise(SKILL_MANIFEST_PATH_MISSING)

    size_result = _run_git(
        git_runner, repo_root,
        ['cat-file', '-s', object_id.decode('ascii')],
    )
    if size_result is None or getattr(size_result, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    try:
        size = int(_stdout(size_result).strip())
    except (TypeError, ValueError):
        _raise(SKILL_MANIFEST_PATH_MISSING)
    if size < 0:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    if size > MAX_SKILL_FILE_SIZE:
        _raise(SKILL_MANIFEST_TOO_LARGE)

    blob = _run_git(
        git_runner, repo_root,
        ['cat-file', 'blob', object_id.decode('ascii')],
    )
    if blob is None or getattr(blob, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    content = _stdout(blob)
    if len(content) != size:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    try:
        content.decode('utf-8')
    except UnicodeDecodeError:
        _raise(SKILL_MANIFEST_NOT_UTF8)
    return content


def build_manifest(repo_root, commit, role, gate, git_runner=subprocess.run,
                   provider='codex'):
    """Build a deterministic manifest using only blobs reachable at ``commit``.

    ``git_runner`` is injectable for focused tests and receives subprocess.run
    arguments. The returned manifest digest covers all other returned fields.
    """
    if type(commit) is not str or _COMMIT_RE.fullmatch(commit) is None:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    paths = resolve(role, gate)
    if provider == 'claude':
        task_paths = tuple(path for path in paths if path not in _BASE_PATHS)
        paths = tuple(sorted(_CLAUDE_BASE_PATHS + task_paths))

    _verify_commit_object(git_runner, repo_root, commit)

    entries = []
    for path in paths:
        content = _read_blob(git_runner, repo_root, commit, path)
        entries.append({
            'path': path,
            'sha256': hashlib.sha256(content).hexdigest(),
            'size': len(content),
        })

    manifest = {
        'schema_version': 1,
        'commit': commit.lower(),
        'role': role,
        'gate': gate,
        'entries': entries,
    }
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
    ).encode('utf-8')
    manifest['manifest_sha256'] = hashlib.sha256(canonical).hexdigest()
    return manifest


def _verify_commit_object(git_runner, repo_root, commit):
    """Refuse unless repo_root is the top level and commit names a commit."""
    top = _run_git(
        git_runner, repo_root,
        ['rev-parse', '--show-toplevel'],
    )
    if top is None or getattr(top, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    try:
        top_path = os.path.realpath(_stdout(top).decode('utf-8').strip())
        requested_root = os.path.realpath(repo_root)
    except (UnicodeDecodeError, TypeError, ValueError, OSError):
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    if not top_path or top_path != requested_root:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)

    checked_commit = _run_git(
        git_runner, repo_root,
        ['rev-parse', '--verify', commit + '^{commit}'],
    )
    if (checked_commit is None
            or getattr(checked_commit, 'returncode', 1) != 0
            or _stdout(checked_commit).strip().lower()
            != commit.lower().encode('ascii')):
        # #1931: an absent object (for example a main commit not fetched yet)
        # has its own code, so the caller can fetch and retry. cat-file -e
        # exits 1 only for a missing object; anything else stays INVALID.
        if checked_commit is not None and _COMMIT_RE.fullmatch(commit) is not None:
            present = _run_git(git_runner, repo_root, ['cat-file', '-e', commit.lower()])
            if present is not None and getattr(present, 'returncode', None) == 1:
                _raise(SKILL_MANIFEST_COMMIT_NOT_LOCAL)
        _raise(SKILL_MANIFEST_COMMIT_INVALID)


def role_card_routes():
    """Return the sorted Developer routes that have a role-card entry."""
    return tuple(sorted(_ROLE_CARD_TABLE))


def _verify_on_main(git_runner, repo_root, commit, main_ref):
    """Refuse unless ``commit`` equals or is an ancestor of ``main_ref``."""
    resolved = _run_git(
        git_runner, repo_root,
        ['rev-parse', '--verify', '--quiet', main_ref + '^{commit}'],
    )
    if resolved is None or getattr(resolved, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_MAIN_REF_INVALID)
    main_commit = _stdout(resolved).strip()
    if not _valid_object_id(main_commit):
        _raise(SKILL_MANIFEST_MAIN_REF_INVALID)
    # Both arguments are verified 40-hex object ids, so neither can be read
    # as an option. Exit 0 means ancestor or equal; anything else refuses.
    ancestry = _run_git(
        git_runner, repo_root,
        ['merge-base', '--is-ancestor', commit.lower(),
         main_commit.decode('ascii').lower()],
    )
    if ancestry is None or getattr(ancestry, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_COMMIT_NOT_ON_MAIN)


def build_role_cards(repo_root, base_commit, route, git_runner=subprocess.run,
                     main_ref=DEFAULT_MAIN_REF):
    """Return common core + Developer card text for one Developer route.

    Every card is read from Git objects at ``base_commit``, never from the
    working tree. ``base_commit`` must equal or be an ancestor of the commit
    ``main_ref`` names at call time (default ``origin/main``); a D6 or PR
    head that is not on main is refused (SKILL_MANIFEST_COMMIT_NOT_ON_MAIN),
    as is a missing or malformed main ref (SKILL_MANIFEST_MAIN_REF_INVALID).
    Refuses an unknown route (SKILL_MANIFEST_ROUTE_INVALID)
    and a prefix whose total card bytes exceed MAX_DEVELOPER_PREFIX_SIZE
    (SKILL_MANIFEST_PREFIX_TOO_LARGE). ``cards_sha256`` covers the commit,
    route and each card's path, sha256 and size, not the text itself.
    """
    if type(route) is not str or route not in _ROLE_CARD_TABLE:
        _raise(SKILL_MANIFEST_ROUTE_INVALID)
    if type(base_commit) is not str or _COMMIT_RE.fullmatch(base_commit) is None:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    if (type(main_ref) is not str or _MAIN_REF_RE.fullmatch(main_ref) is None
            or '..' in main_ref or main_ref.endswith(('/', '.', '.lock'))):
        _raise(SKILL_MANIFEST_MAIN_REF_INVALID)
    _verify_commit_object(git_runner, repo_root, base_commit)
    _verify_on_main(git_runner, repo_root, base_commit, main_ref)

    commit = base_commit.lower()
    cards = []
    total = 0
    for path in _ROLE_CARD_TABLE[route]:
        content = _read_blob(git_runner, repo_root, commit, path)
        total += len(content)
        if total > MAX_DEVELOPER_PREFIX_SIZE:
            _raise(SKILL_MANIFEST_PREFIX_TOO_LARGE)
        cards.append({
            'path': path,
            'sha256': hashlib.sha256(content).hexdigest(),
            'size': len(content),
            'text': content.decode('utf-8'),
        })

    digest_input = {
        'schema_version': 1,
        'commit': commit,
        'role': 'developer',
        'route': route,
        'cards': [
            {key: card[key] for key in ('path', 'sha256', 'size')}
            for card in cards
        ],
    }
    canonical = json.dumps(
        digest_input, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
    ).encode('utf-8')
    return {
        'schema_version': 1,
        'commit': commit,
        'role': 'developer',
        'route': route,
        'cards': cards,
        'prefix_size': total,
        'cards_sha256': hashlib.sha256(canonical).hexdigest(),
    }


DEBUG_CARD_PATH = 'skills/role-cards/debug.md'
HIGH_DEBUG_REPORT_PATH = 'skills/ai-team-delivery/high-debug-report.md'
# WP-F design 3.7 Debug row (#1502): common core, the Debug card and the High
# Debug report procedure, in prompt order. Both Debug routes share this list.
_DEBUG_CARD_PATHS = (COMMON_CORE_CARD_PATH, DEBUG_CARD_PATH, HIGH_DEBUG_REPORT_PATH)
MAX_DEBUG_PREFIX_SIZE = 14 * 1024

REVIEWER_CARD_PATH = 'skills/role-cards/reviewer.md'
REVIEW_CHANGE_SKILL_PATH = 'skills/review-change/SKILL.md'
FORMAL_VERDICT_PATH = 'skills/ai-team-delivery/formal-verdict.md'
# WP-F design 3.7 Reviewer row (#1502): common core, the Reviewer card and the
# two review-gate procedures, in prompt order. Both Reviewer routes share it.
_REVIEW_CARD_PATHS = (COMMON_CORE_CARD_PATH, REVIEWER_CARD_PATH,
                      REVIEW_CHANGE_SKILL_PATH, FORMAL_VERDICT_PATH)
MAX_REVIEW_PREFIX_SIZE = 24 * 1024
# The wrapped-prefix cap role_card_prefix() applies per built role; every
# other role keeps MAX_DEVELOPER_PREFIX_SIZE.
_PREFIX_CAPS = {'review': MAX_REVIEW_PREFIX_SIZE}


def _build_fixed_cards(repo_root, base_commit, paths, role, cap, git_runner,
                       main_ref):
    """Read one fixed card list from Git objects at ``base_commit``.

    Applies the same commit and main ancestry checks as build_role_cards();
    the working tree is never read. ``role`` is also the route. The total card
    bytes must fit ``cap`` (else SKILL_MANIFEST_PREFIX_TOO_LARGE).
    """
    if type(base_commit) is not str or _COMMIT_RE.fullmatch(base_commit) is None:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    if (type(main_ref) is not str or _MAIN_REF_RE.fullmatch(main_ref) is None
            or '..' in main_ref or main_ref.endswith(('/', '.', '.lock'))):
        _raise(SKILL_MANIFEST_MAIN_REF_INVALID)
    _verify_commit_object(git_runner, repo_root, base_commit)
    _verify_on_main(git_runner, repo_root, base_commit, main_ref)

    commit = base_commit.lower()
    cards = []
    total = 0
    for path in paths:
        content = _read_blob(git_runner, repo_root, commit, path)
        total += len(content)
        if total > cap:
            _raise(SKILL_MANIFEST_PREFIX_TOO_LARGE)
        cards.append({
            'path': path,
            'sha256': hashlib.sha256(content).hexdigest(),
            'size': len(content),
            'text': content.decode('utf-8'),
        })

    digest_input = {
        'schema_version': 1,
        'commit': commit,
        'role': role,
        'route': role,
        'cards': [
            {key: card[key] for key in ('path', 'sha256', 'size')}
            for card in cards
        ],
    }
    canonical = json.dumps(
        digest_input, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
    ).encode('utf-8')
    return {
        'schema_version': 1,
        'commit': commit,
        'role': role,
        'route': role,
        'cards': cards,
        'prefix_size': total,
        'cards_sha256': hashlib.sha256(canonical).hexdigest(),
    }


def build_debug_cards(repo_root, base_commit, git_runner=subprocess.run,
                      main_ref=DEFAULT_MAIN_REF):
    """Return the Debug rule cards in the same shape as build_role_cards().

    The fixed list is common core, Debug card and High Debug report, read
    only from Git objects at ``base_commit`` after the same commit and main
    ancestry checks as build_role_cards(); the working tree is never read.
    The total card bytes must fit MAX_DEBUG_PREFIX_SIZE (else
    SKILL_MANIFEST_PREFIX_TOO_LARGE). ``role`` and ``route`` are 'debug';
    ``cards_sha256`` covers the commit, role, route and each card's path,
    sha256 and size. Use role_card_prefix() for the prompt text.
    """
    return _build_fixed_cards(repo_root, base_commit, _DEBUG_CARD_PATHS, 'debug',
                              MAX_DEBUG_PREFIX_SIZE, git_runner, main_ref)


def build_review_cards(repo_root, base_commit, git_runner=subprocess.run,
                       main_ref=DEFAULT_MAIN_REF):
    """Return the formal Reviewer rule cards in the same shape as build_debug_cards().

    The fixed list is common core, Reviewer card, review-change skill and
    formal verdict format, read only from Git objects at ``base_commit`` after
    the same commit and main ancestry checks; the working tree is never read.
    The total card bytes must fit MAX_REVIEW_PREFIX_SIZE (else
    SKILL_MANIFEST_PREFIX_TOO_LARGE). ``role`` and ``route`` are 'review'.
    """
    return _build_fixed_cards(repo_root, base_commit, _REVIEW_CARD_PATHS, 'review',
                              MAX_REVIEW_PREFIX_SIZE, git_runner, main_ref)


def role_card_prefix(built):
    """Return the exact prompt prefix for cards from a card builder.

    Each card is wrapped, in table order, as
    ``\\nREQUIRED_SKILL_BEGIN path=<p>\\n<text>\\nREQUIRED_SKILL_END path=<p>``.
    Every card text must still match its recorded sha256 (else
    SKILL_MANIFEST_PATH_MISSING). The whole wrapped prefix, in UTF-8 bytes,
    must fit MAX_REVIEW_PREFIX_SIZE for role 'review' and
    MAX_DEVELOPER_PREFIX_SIZE otherwise (else SKILL_MANIFEST_PREFIX_TOO_LARGE);
    it is never truncated.
    """
    cards = built.get('cards') if type(built) is dict else None
    if type(cards) is not list or not cards:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    sections = []
    for card in cards:
        if (type(card) is not dict or type(card.get('path')) is not str or
                type(card.get('text')) is not str or
                type(card.get('sha256')) is not str):
            _raise(SKILL_MANIFEST_PATH_MISSING)
        try:
            raw = card['text'].encode('utf-8')
        except UnicodeError:
            _raise(SKILL_MANIFEST_NOT_UTF8)
        if hashlib.sha256(raw).hexdigest() != card['sha256']:
            _raise(SKILL_MANIFEST_PATH_MISSING)
        sections.append('\nREQUIRED_SKILL_BEGIN path=' + card['path'] + '\n' +
                        card['text'] + '\nREQUIRED_SKILL_END path=' + card['path'])
    prefix = ''.join(sections)
    role = built.get('role')
    cap = _PREFIX_CAPS.get(role, MAX_DEVELOPER_PREFIX_SIZE) if type(role) is str \
        else MAX_DEVELOPER_PREFIX_SIZE
    if len(prefix.encode('utf-8')) > cap:
        _raise(SKILL_MANIFEST_PREFIX_TOO_LARGE)
    return prefix


def read_manifest_files(repo_root, manifest, git_runner=subprocess.run):
    """Read manifest file text from the commit named by a built manifest."""
    if type(manifest) is not dict or type(manifest.get('commit')) is not str:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    entries = manifest.get('entries')
    if type(entries) is not list:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    result = []
    for entry in entries:
        if (type(entry) is not dict or type(entry.get('path')) is not str or
                type(entry.get('sha256')) is not str):
            _raise(SKILL_MANIFEST_PATH_MISSING)
        content = _read_blob(
            git_runner, repo_root, manifest['commit'], entry['path'],
        )
        if hashlib.sha256(content).hexdigest() != entry['sha256']:
            _raise(SKILL_MANIFEST_PATH_MISSING)
        result.append((entry['path'], content.decode('utf-8')))
    return result
