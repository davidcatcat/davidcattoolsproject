"""#1502 WP-E owner model override validation.

This check is an accident guard, not a security boundary: it catches a
malformed, stale or mismatched owner override before it is used, but it
cannot prove who authored a comment. The repository argument is supplied by
the caller and is never hard-coded here.

The module uses only the standard library and ``llmtool.errors``. It never
writes files, opens a network connection, runs a process or reads the
environment.
"""

import re

from .errors import LLMToolError

_MARKER = (
    '<!-- activity: type=pm_progress actor=PM target=Owner '
    'status=ANSWERED -->'
)
_OVERRIDE_PREFIX = 'owner-model-override:'
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z')
_ROUTE = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
_REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
_OVERRIDE_LINE = re.compile(
    r'owner-model-override: route=(\S+) model=(\S+)\Z')
_OVERRIDE_KEYS = {'model', 'ref'}


class OwnerOverrideError(LLMToolError):
    """A refused owner model override, classified by a stable code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise OwnerOverrideError(code) from None


def validate_owner_override(override, route, *, repository, fetch_comment):
    """Validate one owner comment override and return its accepted values."""
    if type(repository) is not str or _REPOSITORY.fullmatch(repository) is None:
        _fail('OWNER_OVERRIDE_INVALID')
    if type(override) is not dict or set(override) != _OVERRIDE_KEYS:
        _fail('OWNER_OVERRIDE_INVALID')
    model = override['model']
    if (type(model) is not str or _MODEL.fullmatch(model) is None or
            '://' in model):
        _fail('OWNER_OVERRIDE_INVALID')
    if type(route) is not str or _ROUTE.fullmatch(route) is None:
        _fail('OWNER_OVERRIDE_INVALID')

    ref = override['ref']
    ref_match = None
    if type(ref) is str:
        pattern = re.compile(
            r'https://github\.com/' + re.escape(repository) +
            r'/issues/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)\Z')
        ref_match = pattern.fullmatch(ref)
    if ref_match is None:
        _fail('OWNER_OVERRIDE_REF_INVALID')
    issue_number = int(ref_match.group(1))
    comment_id = int(ref_match.group(2))

    try:
        result = fetch_comment(comment_id)
    except Exception:
        raise OwnerOverrideError('OWNER_OVERRIDE_REF_UNREADABLE') from None
    if type(result) is not dict:
        raise OwnerOverrideError('OWNER_OVERRIDE_REF_UNREADABLE') from None
    body = result.get('body')
    issue_url = result.get('issue_url')
    if type(body) is not str or type(issue_url) is not str:
        raise OwnerOverrideError('OWNER_OVERRIDE_REF_UNREADABLE') from None

    expected_issue_url = ('https://api.github.com/repos/' + repository +
                          '/issues/' + str(issue_number))
    if issue_url != expected_issue_url:
        _fail('OWNER_OVERRIDE_REF_INVALID')

    lines = [line.strip() for line in body.splitlines()]
    if _MARKER not in lines:
        _fail('OWNER_OVERRIDE_MARKER_MISSING')

    override_lines = [line for line in lines
                      if line.startswith(_OVERRIDE_PREFIX)]
    if not override_lines:
        _fail('OWNER_OVERRIDE_LINE_MISSING')
    if len(override_lines) > 1:
        _fail('OWNER_OVERRIDE_LINE_AMBIGUOUS')
    line_match = _OVERRIDE_LINE.fullmatch(override_lines[0])
    if line_match is None:
        _fail('OWNER_OVERRIDE_LINE_MISSING')
    if line_match.group(1) != route or line_match.group(2) != model:
        _fail('OWNER_OVERRIDE_MISMATCH')

    return {
        'route': route,
        'model': model,
        'ref': ref,
        'comment_id': comment_id,
    }
