"""Immutable CI profile: current check names and workflow file names.

Issue #2793, package H2a. Describes the CI check names and workflow file
names as an immutable value. Construction validates every name with pure
string checks only: no file, network, or process access.
"""

from dataclasses import dataclass, fields

__all__ = ('CiProfile', 'CiProfileError', 'default_profile')

#: Reject names longer than this many characters.
_MAX_NAME_LEN = 100


class CiProfileError(ValueError):
    """Profile name rejected during construction.

    Carries one fixed upper-case code and never includes the rejected value.
    """

    code = 'CI_PROFILE_NAME_INVALID'

    def __init__(self):
        super().__init__('ci profile name invalid')


def _validate_name(value):
    """Refuse values that are not non-empty printable, non-blank strings of at most 100 chars."""
    if (
        not isinstance(value, str)
        or not value
        or not value.strip()
        or not value.isprintable()
        or len(value) > _MAX_NAME_LEN
    ):
        raise CiProfileError()


@dataclass(frozen=True)
class CiProfile:
    """Immutable names of the current CI checks and workflow files.

    linux_code_check: Linux code check (Linux 3.14)
    docs_check: docs check
    gateway_check: gateway check
    ai_loop_check: ai-loop helper check
    macos_check: macOS check
    linux_workflow_file: Linux workflow file name
    macos_workflow_file: macOS workflow file name
    """

    linux_code_check: str
    docs_check: str
    gateway_check: str
    ai_loop_check: str
    macos_check: str
    linux_workflow_file: str
    macos_workflow_file: str

    def __post_init__(self):
        for _field in fields(self):
            _validate_name(getattr(self, _field.name))


def default_profile():
    """Return a CiProfile holding the current CI check and workflow file names."""
    return CiProfile(
        linux_code_check='check (Linux 3.14)',
        docs_check='check (docs)',
        gateway_check='gateway tests (Linux 3.14)',
        ai_loop_check='ai-loop helper tests (Python 3.14)',
        macos_check='check (macOS 3.13)',
        linux_workflow_file='llmtool-linux-ci.yml',
        macos_workflow_file='llmtool-mac-ci.yml',
    )
