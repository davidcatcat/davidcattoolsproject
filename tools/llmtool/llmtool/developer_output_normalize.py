"""Conservative normalization for text Developer unified-diff proposals.

All output remains subject to the strict task-submission parser and later Git
application checks. This module has no filesystem, provider, or Git behavior.
"""

import difflib
import re


_DIFF_HEADER = re.compile(r'^diff --git a/([^\r\n]+) b/([^\r\n]+)$')
_FILE_OLD = re.compile(r'^--- a/([^\r\n]+)$')
_FILE_NEW = re.compile(r'^\+\+\+ b/([^\r\n]+)$')
_HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$')
_INDEX = re.compile(r'^index [0-9a-f]+\.\.[0-9a-f]+(?: (?:100644|100755))?$')
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
    if any(char in value for char in ('\r', '\u2028', '\u2029', '\x85',
                                      '\x1c', '\x1d', '\x1e')):
        return value, ()
    text = value
    tokens = []
    lines = text.split('\n')
    if text.endswith('\n'):
        lines.pop()

    # Permit one fenced block only when all text outside it is whitespace.
    fence_lines = [i for i, line in enumerate(lines) if line.strip().startswith('```')]
    if fence_lines:
        if len(fence_lines) != 2:
            return value, ()
        first, last = fence_lines
        opening = lines[first].strip()
        if opening not in ('```', '```diff') or lines[last].strip() != '```':
            return value, ()
        if any(line.strip() for line in lines[:first] + lines[last + 1:]):
            return value, ()
        inner = lines[first + 1:last]
        text = '\n'.join(inner)
        tokens.append('OUTER_FENCE_STRIPPED')

    lines = text.split('\n')
    if text.endswith('\n'):
        lines.pop()
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

    # Do not let header recovery or hunk recounting absorb another file's
    # ---/+++ headers as deletion/addition lines. A header-looking pair inside
    # a hunk is ambiguous too, so leave the original text for strict rejection.
    for section in sections:
        file_header_pairs = 0
        inside_hunk = False
        for index, line in enumerate(section):
            if _HUNK.fullmatch(line):
                inside_hunk = True
            if (index + 1 < len(section) and _FILE_OLD.fullmatch(line) and
                    _FILE_NEW.fullmatch(section[index + 1])):
                file_header_pairs += 1
                if inside_hunk or file_header_pairs > 1:
                    return value, ()

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
            old_declared = int(hunk.group(2)) if hunk.group(2) is not None else 1
            new_declared = int(hunk.group(4)) if hunk.group(4) is not None else 1
            if old_count == old_declared and new_count == new_declared:
                # Keep an already-valid header byte-for-byte; in particular,
                # do not report a normalization for omitted count fields.
                rebuilt = line
            else:
                rebuilt = f'@@ -{hunk.group(1)},{old_count} +{hunk.group(3)},{new_count} @@{hunk.group(5)}'
            if rebuilt != line:
                tokens.append('HUNK_COUNTS_RECOUNTED')
            fixed.append(rebuilt)
            fixed.extend(body)
        output.extend(fixed)

    normalized = '\n'.join(output) + '\n'
    return normalized, tuple(dict.fromkeys(tokens))


def reanchor_developer_output(value, write_paths, source_files, *, return_valid=False):
    """Rebuild proposal hunks from exact, unique old-side matches in source files.

    ``source_files`` maps allowed repository paths to UTF-8 source text read at
    the bound source revision. A failed or ambiguous match is never repaired.
    """
    def result(text, tokens, valid):
        return (text, tokens, valid) if return_valid else (text, tokens)

    if type(value) is not str or type(source_files) is not dict:
        return result(None, (), False)
    lines = value.splitlines()
    expected = set(write_paths)
    starts = [i for i, line in enumerate(lines) if line.startswith('diff --git ')]
    if not starts:
        return result(None, (), False)
    sections = [lines[start:(starts[index + 1] if index + 1 < len(starts) else len(lines))]
                for index, start in enumerate(starts)]
    regenerated = []
    for section in sections:
        if len(section) < 3:
            return result(None, (), False)
        header = _DIFF_HEADER.fullmatch(section[0])
        file_header_index = 1
        if len(section) > 1 and section[1].startswith('index '):
            if _INDEX.fullmatch(section[1]) is None:
                return result(None, (), False)
            file_header_index = 2
        if len(section) <= file_header_index + 1:
            return result(None, (), False)
        old_header = _FILE_OLD.fullmatch(section[file_header_index])
        new_header = _FILE_NEW.fullmatch(section[file_header_index + 1])
        if (header is None or old_header is None or new_header is None or
                header.groups() != (old_header.group(1), new_header.group(1)) or
                old_header.group(1) != new_header.group(1)):
            return result(None, (), False)
        path = old_header.group(1)
        if path not in expected or path not in source_files:
            return result(None, (), False)
        try:
            source_text = source_files[path]
            if type(source_text) is not str or '\r' in source_text:
                return result(None, (), False)
            source_has_final_newline = source_text.endswith('\n')
            source = source_text.splitlines()
        except (UnicodeError, AttributeError):
            return result(None, (), False)

        edits = []
        i = file_header_index + 2
        while i < len(section):
            hunk = _HUNK.fullmatch(section[i])
            if hunk is None:
                return result(None, (), False)
            i += 1
            body = []
            while i < len(section) and not section[i].startswith('@@ '):
                if section[i].startswith('diff --git '):
                    break
                if section[i] == r'\ No newline at end of file':
                    # Git places this marker after the affected old/new line.
                    if not body or not body[-1].startswith((' ', '-', '+')):
                        return result(None, (), False)
                    body.append(section[i])
                    i += 1
                    continue
                body.append(section[i])
                i += 1
            old_side = [line[1:] for line in body if line.startswith((' ', '-'))]
            if not old_side or not any(line.startswith(('+', '-')) for line in body):
                return result(None, (), False)
            matches = [index for index in range(len(source) - len(old_side) + 1)
                       if source[index:index + len(old_side)] == old_side]
            if len(matches) != 1:
                return result(None, (), False)
            start = matches[0]
            # Reconstruct the target slice by replaying the model's operations
            # against the matched source, preserving every model +/- line.
            target = []
            cursor = start
            for line in body:
                if line == r'\ No newline at end of file':
                    continue
                if line.startswith(' '):
                    target.append(source[cursor])
                    cursor += 1
                elif line.startswith('-'):
                    cursor += 1
                elif line.startswith('+'):
                    target.append(line[1:])
                else:
                    return result(None, (), False)
            edits.append((start, cursor, target))
        if not edits:
            return result(None, (), False)
        modified = list(source)
        previous_end = len(source) + 1
        for start, end, target in reversed(edits):
            if end > previous_end:
                return result(None, (), False)
            modified[start:end] = target
            previous_end = start
        # A Developer marker is untrusted metadata. Keep the source's exact
        # final-newline state on the reconstructed result's final record.
        source_records = [line + '\n' for line in source]
        if source_records and not source_has_final_newline:
            source_records[-1] = source[-1]
        modified_records = [line + '\n' for line in modified]
        if modified_records and not source_has_final_newline:
            modified_records[-1] = modified[-1]
        diff_records = list(difflib.unified_diff(
            source_records, modified_records,
            fromfile='', tofile='', n=3, lineterm=''))
        diff_lines = [line[:-1] if line.endswith('\n') else line
                      for line in diff_records]
        hunks = [line for line in diff_lines if line.startswith('@@ ')]
        if not hunks:
            return result(None, (), False)
        regenerated.append(f'diff --git a/{path} b/{path}')
        if file_header_index == 2:
            # Keep Git's standard index metadata byte-for-byte. Regeneration
            # changes hunk context only; the modeled +/- content is preserved.
            regenerated.append(section[1])
        regenerated.extend([f'--- a/{path}', f'+++ b/{path}'])
        # The first two lines from unified_diff are empty-path file headers.
        body_records = diff_records[2:]
        # difflib retains each record's newline state. Convert that state into
        # Git's marker immediately after the affected record; text equality
        # alone cannot tell an unchanged EOF line from a repeated line above.
        body_with_markers = []
        for record in body_records:
            line = record[:-1] if record.endswith('\n') else record
            body_with_markers.append(line)
            if line.startswith((' ', '-', '+')) and not record.endswith('\n'):
                body_with_markers.append(r'\ No newline at end of file')
        body = body_with_markers
        regenerated.extend(body)
    anchored = '\n'.join(regenerated) + '\n'
    tokens = ('HUNK_REANCHORED',) if anchored != value else ()
    return result(anchored, tokens, True)
