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
