"""Owner exception verifier for deterministic redaction under issue #1253."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import re
from typing import Any, Callable

from . import github_identity


EXCEPTION_TYPE = 'OWNER_EXCEPTION_DETERMINISTIC_REDACTION'
ISSUE = 1253
AUTHORIZATION_COMMENT_ID = 6788268483
SLOTS = ('S2a', 'S2b')
REGISTER_MARKER = '<!-- activity: type=owner_exception actor=PM target=PM status=REGISTERED -->'
RETIRE_MARKER = '<!-- activity: type=owner_exception actor=PM target=PM status=RETIRED -->'

_URL_PATTERN = re.compile(
    r'https://github\.com/([^/#?\s]+/[^/#?\s]+)/issues/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)'
)
_HEX40 = re.compile(r'[0-9a-f]{40}')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_UTC_TIME = re.compile(
    r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})'
    r'(?:\.([0-9]{1,6}))?(?:Z|\+00:00)?'
)


def _trusted_slug() -> str | None:
    try:
        from . import task_completion
        slug = task_completion._trusted_slug()
        if isinstance(slug, str):
            return slug
    except Exception:
        pass
    try:
        from . import trust
        slug = trust.load_trust().repository['slug']
        if isinstance(slug, str):
            return slug
    except Exception:
        pass
    return None


def _utc_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = _UTC_TIME.fullmatch(value)
    if match is None:
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    fraction = (match.group(7) or '').ljust(6, '0')
    try:
        return datetime(
            *(int(part) for part in match.groups()[:6]),
            int(fraction),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _extract_line(body: str, prefix: str) -> str | None:
    for line in body.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def default_replacement_map() -> list[tuple[str, str]]:
    """Load the user redaction map via redact_known_values.load_and_validate_map."""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    module_path = scripts_dir / "redact_known_values.py"
    spec = importlib.util.spec_from_file_location("redact_known_values", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load redact_known_values from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    repo_root = mod.find_repo_root()
    map_path = mod.resolve_map_path(None)
    return mod.load_and_validate_map(map_path, repo_root)


def verify(
    url: str,
    number: int,
    github_get: Callable[[str], Any],
    *,
    replacement_map: list[tuple[str, str]] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Verify that url registers a scoped deterministic redaction for PR number.

    Return (None, evidence) on valid pass, or (code, None) on refusal.
    """
    if not isinstance(url, str):
        return 'OWNER_EXCEPTION_REF_INVALID', None

    match = _URL_PATTERN.fullmatch(url)
    if match is None:
        return 'OWNER_EXCEPTION_REF_INVALID', None

    slug, issue_str, comment_id_str = match.group(1), match.group(2), match.group(3)

    trusted_slug = _trusted_slug()
    if trusted_slug is None or slug != trusted_slug:
        return 'OWNER_EXCEPTION_REF_INVALID', None

    if issue_str != str(ISSUE):
        return 'OWNER_EXCEPTION_SCOPE', None

    if not isinstance(number, int) or number < 1:
        return 'OWNER_EXCEPTION_REF_INVALID', None

    try:
        comment = github_get(f'issues/comments/{comment_id_str}')
    except Exception:
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    if not isinstance(comment, dict):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None
    if any(not isinstance(comment.get(k), str) for k in ('body', 'html_url', 'created_at', 'updated_at')):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    user = comment.get('user')
    if not isinstance(user, dict) or not isinstance(user.get('login'), str):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    if comment['html_url'] != url:
        return 'OWNER_EXCEPTION_REF_INVALID', None

    if comment['created_at'] != comment['updated_at']:
        return 'OWNER_EXCEPTION_EDITED', None

    try:
        allowed_logins = (github_identity.owner_login(), github_identity.ai_login())
    except Exception:
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    if user.get('type') != 'User' or user['login'] not in allowed_logins:
        return 'OWNER_EXCEPTION_AUTHOR', None

    body = comment['body']
    first_line = body.split('\n', 1)[0]
    if first_line.endswith('\r'):
        first_line = first_line[:-1]
    if first_line != REGISTER_MARKER:
        return 'OWNER_EXCEPTION_MARKER', None

    try:
        auth_comment = github_get(f'issues/comments/{AUTHORIZATION_COMMENT_ID}')
    except Exception:
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    if not isinstance(auth_comment, dict):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    auth_user = auth_comment.get('user')
    if not isinstance(auth_user, dict) or not isinstance(auth_user.get('login'), str):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    if auth_user.get('type') != 'User' or auth_user['login'] not in allowed_logins:
        return 'OWNER_EXCEPTION_AUTHOR', None

    if not isinstance(auth_comment.get('created_at'), str):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    reg_time = _utc_time(comment['created_at'])
    auth_time = _utc_time(auth_comment['created_at'])
    if reg_time is None or auth_time is None or reg_time <= auth_time:
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    raw_lines = [line.strip() for line in body.splitlines() if line.strip()]
    exception_val: str | None = None
    slot_val: str | None = None
    pr_val: str | None = None
    base_val: str | None = None
    head_val: str | None = None
    file_map: dict[str, str] = {}
    duplicate_file = False
    malformed_file = False
    has_unknown_lines = False

    for line in raw_lines[1:]:
        if line.startswith('exception: '):
            if exception_val is not None:
                return 'OWNER_EXCEPTION_MARKER', None
            exception_val = line[len('exception: '):].strip()
        elif line.startswith('slot: '):
            if slot_val is not None:
                return 'OWNER_EXCEPTION_MARKER', None
            slot_val = line[len('slot: '):].strip()
        elif line.startswith('pr: '):
            if pr_val is not None:
                return 'OWNER_EXCEPTION_MARKER', None
            pr_val = line[len('pr: '):].strip()
        elif line.startswith('base: '):
            if base_val is not None:
                return 'OWNER_EXCEPTION_MARKER', None
            base_val = line[len('base: '):].strip()
        elif line.startswith('head: '):
            if head_val is not None:
                return 'OWNER_EXCEPTION_MARKER', None
            head_val = line[len('head: '):].strip()
        elif line.startswith('file: '):
            content = line[len('file: '):].strip()
            parts = content.split(' ', 1)
            if len(parts) != 2:
                malformed_file = True
            else:
                sha_part, path_part = parts[0].strip(), parts[1].strip()
                if not _HEX64.fullmatch(sha_part):
                    malformed_file = True
                elif path_part in file_map:
                    duplicate_file = True
                else:
                    file_map[path_part] = sha_part
        else:
            has_unknown_lines = True

    if has_unknown_lines:
        return 'OWNER_EXCEPTION_MARKER', None

    if exception_val is None or exception_val != EXCEPTION_TYPE:
        return 'OWNER_EXCEPTION_TYPE', None

    if slot_val is None or slot_val not in SLOTS:
        return 'OWNER_EXCEPTION_SCOPE', None

    if pr_val is None or pr_val != f'#{number}':
        return 'OWNER_EXCEPTION_REF_INVALID', None

    if base_val is None or not _HEX40.fullmatch(base_val):
        return 'OWNER_EXCEPTION_SHA_MISMATCH', None

    if head_val is None or not _HEX40.fullmatch(head_val):
        return 'OWNER_EXCEPTION_SHA_MISMATCH', None

    if malformed_file or duplicate_file or not file_map:
        return 'OWNER_EXCEPTION_FILES_MISMATCH', None

    try:
        pull = github_get(f'pulls/{number}')
    except Exception:
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    if not isinstance(pull, dict):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None
    if pull.get('state') != 'open' or pull.get('merged') is True:
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    pr_base = pull.get('base')
    pr_head = pull.get('head')
    if not isinstance(pr_base, dict) or not isinstance(pr_head, dict):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    if pr_base.get('sha') != base_val or pr_head.get('sha') != head_val:
        return 'OWNER_EXCEPTION_SHA_MISMATCH', None

    pr_files: list[dict[str, Any]] = []
    page = 1
    while True:
        suffix = f"?per_page=100&page={page}" if page > 1 else "?per_page=100"
        try:
            batch = github_get(f'pulls/{number}/files{suffix}')
        except Exception:
            return 'OWNER_EXCEPTION_UNAVAILABLE', None
        if not isinstance(batch, list):
            return 'OWNER_EXCEPTION_UNAVAILABLE', None
        pr_files.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    pr_file_paths: set[str] = set()
    for item in pr_files:
        if not isinstance(item, dict):
            return 'OWNER_EXCEPTION_UNAVAILABLE', None
        if item.get('status') != 'modified':
            return 'OWNER_EXCEPTION_FILES_MISMATCH', None
        fn = item.get('filename')
        if not isinstance(fn, str):
            return 'OWNER_EXCEPTION_UNAVAILABLE', None
        pr_file_paths.add(fn)

    if pr_file_paths != set(file_map.keys()):
        return 'OWNER_EXCEPTION_FILES_MISMATCH', None

    if replacement_map is None:
        try:
            replacement_map = default_replacement_map()
        except Exception:
            return 'OWNER_EXCEPTION_UNAVAILABLE', None

    if not isinstance(replacement_map, list):
        return 'OWNER_EXCEPTION_UNAVAILABLE', None

    file_evidence: list[list[Any]] = []
    for path in sorted(file_map.keys()):
        expected_hash = file_map[path]
        try:
            base_content_obj = github_get(f'contents/{path}?ref={base_val}')
            head_content_obj = github_get(f'contents/{path}?ref={head_val}')
        except Exception:
            return 'OWNER_EXCEPTION_UNAVAILABLE', None

        if not isinstance(base_content_obj, dict) or not isinstance(head_content_obj, dict):
            return 'OWNER_EXCEPTION_UNAVAILABLE', None
        if 'content' not in base_content_obj or 'content' not in head_content_obj:
            return 'OWNER_EXCEPTION_UNAVAILABLE', None

        try:
            base_bytes = base64.b64decode(base_content_obj['content'])
            head_bytes = base64.b64decode(head_content_obj['content'])
        except Exception:
            return 'OWNER_EXCEPTION_UNAVAILABLE', None

        head_sha256 = hashlib.sha256(head_bytes).hexdigest()
        if head_sha256 != expected_hash:
            return 'OWNER_EXCEPTION_HASH_MISMATCH', None

        current_bytes = base_bytes
        replacements_count = 0
        for val, plc in replacement_map:
            val_bytes = val.encode('utf-8') if isinstance(val, str) else val
            plc_bytes = plc.encode('utf-8') if isinstance(plc, str) else plc
            count = current_bytes.count(val_bytes)
            if count > 0:
                current_bytes = current_bytes.replace(val_bytes, plc_bytes)
                replacements_count += count

        if replacements_count == 0 or current_bytes != head_bytes:
            return 'OWNER_EXCEPTION_NOT_MECHANICAL', None

        file_evidence.append([path, head_sha256, replacements_count])

    all_comments: list[dict[str, Any]] = []
    page = 1
    while True:
        suffix = f"?per_page=100&page={page}" if page > 1 else "?per_page=100"
        try:
            batch = github_get(f'issues/{ISSUE}/comments{suffix}')
        except Exception:
            return 'OWNER_EXCEPTION_UNAVAILABLE', None
        if not isinstance(batch, list):
            return 'OWNER_EXCEPTION_UNAVAILABLE', None
        all_comments.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    allowed_author_comments: list[dict[str, Any]] = []
    for c in all_comments:
        if not isinstance(c, dict):
            continue
        u = c.get('user')
        if not isinstance(u, dict) or u.get('type') != 'User':
            continue
        if u.get('login') not in allowed_logins:
            continue
        b = c.get('body')
        if not isinstance(b, str):
            continue
        allowed_author_comments.append(c)

    for c in allowed_author_comments:
        c_first = c['body'].split('\n', 1)[0]
        if c_first.endswith('\r'):
            c_first = c_first[:-1]
        if c_first == RETIRE_MARKER:
            return 'OWNER_EXCEPTION_RETIRED', None

    earliest_per_slot: dict[str, tuple[datetime, dict[str, Any], int]] = {}
    for c in allowed_author_comments:
        c_first = c['body'].split('\n', 1)[0]
        if c_first.endswith('\r'):
            c_first = c_first[:-1]
        if c_first != REGISTER_MARKER:
            continue
        c_slot = _extract_line(c['body'], 'slot: ')
        c_pr = _extract_line(c['body'], 'pr: ')
        if not c_slot or not c_pr or not c_pr.startswith('#'):
            continue
        try:
            c_pr_num = int(c_pr[1:])
        except ValueError:
            continue
        if c_slot not in SLOTS:
            continue
        c_time = _utc_time(c.get('created_at'))
        if c_time is None:
            continue
        if c_slot not in earliest_per_slot:
            earliest_per_slot[c_slot] = (c_time, c, c_pr_num)
        else:
            if c_time < earliest_per_slot[c_slot][0]:
                earliest_per_slot[c_slot] = (c_time, c, c_pr_num)

    if slot_val not in earliest_per_slot:
        return 'OWNER_EXCEPTION_REUSED', None

    earliest_comment = earliest_per_slot[slot_val][1]
    if earliest_comment.get('html_url') != url and str(earliest_comment.get('id')) != comment_id_str:
        return 'OWNER_EXCEPTION_REUSED', None

    pr_by_slot: dict[int, str] = {}
    for s, (_, _, pr_num) in earliest_per_slot.items():
        if pr_num in pr_by_slot:
            return 'OWNER_EXCEPTION_REUSED', None
        pr_by_slot[pr_num] = s

    evidence: dict[str, Any] = {
        'type': EXCEPTION_TYPE,
        'slot': slot_val,
        'url': url,
        'pr': number,
        'base': base_val,
        'head': head_val,
        'files': sorted(file_evidence, key=lambda x: x[0]),
    }
    return None, evidence
