"""Conservative normalization for text Developer unified-diff proposals.

All output remains subject to the strict task-submission parser and later Git
application checks. This module has no filesystem, provider, or Git behavior.
"""

import re


_DIFF_HEADER = re.compile(r'^diff --git a/([^\r\n]+) b/([^\r\n]+)$')
_FILE_OLD = re.compile(r'^--- a/([^\r\n]+)$')
_FILE_NEW = re.compile(r'^\+\+\+ b/([^\r\n]+)$')
_HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$')
_UNSAFE = re.compile(
    r'^(?:old mode |new mode |new file mode |deleted file mode |'
    r'rename from |rename to |copy from |copy to |GIT binary patch|'
    r'Binary files )', re.MULTILINE)


def normalize_developer_output(value, write_paths):
    """Return (normalized text, fixed-token tuple); unsupported shapes stay intact.

    Text diff sections are eligible when every path is in scope. Scope and
    strict syntax are checked by the caller after this function returns.
    """
    if type(value) is not str or not value or len(value.encode('utf-8', 'ignore')) > 64 * 1024:
        return value, ()
    text = value
    tokens = []
    lines = text.splitlines()

    # Permit one fenced block, with optional prose around that block only.
    fence_lines = [i for i, line in enumerate(lines) if line.strip().startswith('```')]
    if fence_lines:
        if len(fence_lines) != 2:
            return value, ()
        first, last = fence_lines
        opening = lines[first].strip()
        if opening not in ('```', '```diff') or lines[last].strip() != '```':
            return value, ()
        inner = lines[first + 1:last]
        # The fenced remainder itself must be a single diff candidate. Prose
        # outside is allowed only because there is exactly one complete fence.
        text = '\n'.join(inner)
        tokens.append('OUTER_FENCE_STRIPPED')

    lines = text.splitlines()
    if fence_lines:
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
    if not lines:
        return value, ()
    if any(line.startswith('GIT binary patch') or line.startswith('Binary files ')
           or _UNSAFE.match(line) for line in lines):
        return value, ()

    # The only supported paths are simple, matching a/b paths explicitly in
    # the write allowlist. Quoted paths and path transformations are rejected.
    expected = set(write_paths)
    starts = [i for i, line in enumerate(lines) if line.startswith('diff --git ')]
    if starts:
        if any(line.strip() for line in lines[:starts[0]]):
            return value, ()
        sections = [lines[start:(starts[index + 1] if index + 1 < len(starts) else len(lines))]
                    for index, start in enumerate(starts)]
    else:
        # Header recovery is deliberately limited to one otherwise complete file diff.
        sections = [lines]

    output = []
    for section in sections:
        header_index = next((i for i, line in enumerate(section)
                             if line.startswith('diff --git ')), None)
        old_index = next((i for i, line in enumerate(section) if line.startswith('--- ')), None)
        new_index = next((i for i, line in enumerate(section) if line.startswith('+++ ')), None)
        if old_index is None or new_index is None or new_index != old_index + 1:
            return value, ()
        old_match = _FILE_OLD.fullmatch(section[old_index])
        new_match = _FILE_NEW.fullmatch(section[new_index])
        if old_match is None or new_match is None or old_match.group(1) != new_match.group(1):
            return value, ()
        path = old_match.group(1)
        if (path not in expected or path.startswith('"') or '\\' in path or
                any(part in ('', '.', '..') for part in path.split('/'))):
            return value, ()
        if header_index is None:
            if len(sections) != 1:
                return value, ()
            section.insert(old_index, f'diff --git a/{path} b/{path}')
            tokens.append('DIFF_HEADER_ADDED')
        else:
            match = _DIFF_HEADER.fullmatch(section[header_index])
            if match is None or match.groups() != (path, path):
                return value, ()

        # Convert only bare empty hunk lines, then count the complete body.
        fixed = []
        i = 0
        while i < len(section):
            line = section[i]
            hunk = _HUNK.fullmatch(line)
            if hunk is None:
                fixed.append(line)
                i += 1
                continue
            i += 1
            body = []
            while i < len(section) and not section[i].startswith('@@ ') and not section[i].startswith('diff --git '):
                candidate = section[i]
                if candidate == '':
                    candidate = ' '
                    tokens.append('BARE_BLANK_CONTEXT_SPACED')
                body.append(candidate)
                i += 1
            old_count = sum(1 for item in body if item.startswith((' ', '-')))
            new_count = sum(1 for item in body if item.startswith((' ', '+')))
            rebuilt = f'@@ -{hunk.group(1)},{old_count} +{hunk.group(3)},{new_count} @@{hunk.group(5)}'
            if rebuilt != line:
                tokens.append('HUNK_COUNTS_RECOUNTED')
            fixed.append(rebuilt)
            fixed.extend(body)
        output.extend(fixed)

    normalized = '\n'.join(output) + '\n'
    return normalized, tuple(dict.fromkeys(tokens))
