"""Repository identity accepted by D6 review collection, read lazily from trust.json."""
import re

from . import trust
from .task_review import reject

# Legacy values, kept only for ``llmtool trust verify-legacy`` until #369 PR-10.
REVIEW_ORIGINS = frozenset((
    'git@github.com:davidcatcat/davidcattoolsproject.git',
    'https://github.com/davidcatcat/davidcattoolsproject.git',
))


def review_trust():
    """Return the frozen TrustConfig; fail closed with the sanitized TRUST_* code."""
    try:
        return trust.load_trust()
    except trust.TrustError as exc:
        code = exc.code
    reject(code)


def trusted_repository():
    return review_trust().repository


def trusted_slug():
    return trusted_repository()['slug']


def origin_allowed(origin):
    """Exact membership in trust ``allowed_origins`` (no normalisation)."""
    return type(origin) is str and origin in trusted_repository()['allowed_origins']


def host_policy_reference_pattern():
    repository = trusted_repository()
    issue = repository['approval_refs']['reviewer_host_policy_issue']
    return (r'https://github\.com/' + re.escape(repository['slug']) +
            r'/issues/' + str(issue) + r'#issuecomment-[0-9]+')


def actions_run_pattern():
    return (r'https://github\.com/' + re.escape(trusted_slug()) +
            r'/actions/runs/[1-9][0-9]{0,19}')
