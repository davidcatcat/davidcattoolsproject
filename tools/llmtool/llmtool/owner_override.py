"""Owner model overrides for #1502 WP-E.

An owner may approve a single build route/model pairing in a GitHub issue
comment. This check is an accident guard, not a security boundary: it catches
mistyped, stale, or unapproved overrides but does not authenticate the person
or process that supplied them.

The module uses only the standard library (``re``) and ``llmtool.errors``. It
never writes files, opens a network connection, runs a process, or reads the
environment.
"""

import re

from .errors import LLMToolError

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_ROUTE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_REF = re.compile(
    r"https://github\.com/davidcatcat/davidcattoolsproject/issues/"
    r"([1-9][0-9]*)#issuecomment-([1-9][0-9]*)"
)
_OVERRIDE_LINE = re.compile(r"owner-model-override: route=(\S+) model=(\S+)")
_MARKER = "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->"
_ISSUE_URL_PREFIX = "https://api.github.com/repos/davidcatcat/davidcattoolsproject/issues/"


class OwnerOverrideError(LLMToolError):
    """Refusal to apply an owner override, carrying a stable code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def validate_owner_override(override, route, *, fetch_comment):
    """Return a normalised override only when its issue comment approves it."""
    if type(override) is not dict or set(override) != {"model", "ref"}:
        raise OwnerOverrideError("OWNER_OVERRIDE_INVALID")
    model = override["model"]
    if type(model) is not str or _MODEL.fullmatch(model) is None or "://" in model:
        raise OwnerOverrideError("OWNER_OVERRIDE_INVALID")
    if type(route) is not str or _ROUTE.fullmatch(route) is None:
        raise OwnerOverrideError("OWNER_OVERRIDE_INVALID")

    ref = override["ref"]
    if type(ref) is not str:
        raise OwnerOverrideError("OWNER_OVERRIDE_REF_INVALID")
    ref_match = _REF.fullmatch(ref)
    if ref_match is None:
        raise OwnerOverrideError("OWNER_OVERRIDE_REF_INVALID")
    try:
        issue_number = int(ref_match.group(1))
        comment_id = int(ref_match.group(2))
    except ValueError:
        # Only reachable for ids beyond Python's int-string digit limit.
        raise OwnerOverrideError("OWNER_OVERRIDE_REF_INVALID") from None

    try:
        result = fetch_comment(comment_id)
    except Exception:
        raise OwnerOverrideError("OWNER_OVERRIDE_REF_UNREADABLE") from None
    if not isinstance(result, dict):
        raise OwnerOverrideError("OWNER_OVERRIDE_REF_UNREADABLE")
    body = result.get("body")
    issue_url = result.get("issue_url")
    if type(body) is not str or type(issue_url) is not str:
        raise OwnerOverrideError("OWNER_OVERRIDE_REF_UNREADABLE")

    if issue_url != _ISSUE_URL_PREFIX + str(issue_number):
        raise OwnerOverrideError("OWNER_OVERRIDE_REF_INVALID")

    lines = [line.strip() for line in body.splitlines()]
    if _MARKER not in lines:
        raise OwnerOverrideError("OWNER_OVERRIDE_MARKER_MISSING")

    override_lines = [line for line in lines if line.startswith("owner-model-override:")]
    if not override_lines:
        raise OwnerOverrideError("OWNER_OVERRIDE_LINE_MISSING")
    if len(override_lines) > 1:
        raise OwnerOverrideError("OWNER_OVERRIDE_LINE_AMBIGUOUS")
    line_match = _OVERRIDE_LINE.fullmatch(override_lines[0])
    if line_match is None:
        raise OwnerOverrideError("OWNER_OVERRIDE_LINE_MISSING")
    if line_match.group(1) != route or line_match.group(2) != model:
        raise OwnerOverrideError("OWNER_OVERRIDE_MISMATCH")

    return {
        "route": route,
        "model": model,
        "ref": ref,
        "comment_id": comment_id,
    }
