"""Advisory inline copies of formal verdict findings (#1235 C6a, piece 1).

The formal verdict stays one issue comment. This module only selects the
"Finding: F<n> [<severity>] <path>:<line> <text>" lines that point at a file the
PR changed, so a later piece can post each one as an advisory PR line comment.
It makes no GitHub call. A copy is never a verdict, and a missing copy never blocks.
"""

import re

MAX_INLINE_FINDINGS = 20
MAX_INLINE_TEXT = 1000
_LINE = re.compile(r'Finding: (F[1-9][0-9]{0,2}) \[(Blocker|High|Medium|Low|Advisory)\] '
                   r'([A-Za-z0-9_./-]+):([1-9][0-9]{0,5})(?:-[1-9][0-9]{0,5})?[ \t]+(\S.*)')


def inline_findings(body, changed_paths):
    """Return [{'path', 'line', 'body'}] for findings on changed files, in verdict order.

    Lines that are not finding lines, unsafe or unchanged paths and repeats of a
    finding ID are skipped. At most MAX_INLINE_FINDINGS entries are returned.
    """
    if type(body) is not str:
        return []
    changed = {path for path in changed_paths if type(path) is str}
    found, seen = [], set()
    for raw in body.splitlines():
        match = _LINE.fullmatch(raw.strip())
        if match is None:
            continue
        finding_id, severity, path, line, text = match.groups()
        if (finding_id in seen or path not in changed or path.startswith('/')
                or '..' in path.split('/')):
            continue
        seen.add(finding_id)
        note = 'Advisory copy of formal verdict finding %s [%s]: %s' % (finding_id, severity, text)
        found.append({'path': path, 'line': int(line), 'body': note[:MAX_INLINE_TEXT]})
        if len(found) == MAX_INLINE_FINDINGS:
            break
    return found
