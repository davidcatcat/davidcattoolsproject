"""Shared validation for legacy EC2 remote-test log paths."""

import re

_LEGACY_REMOTE_LOG_PATH = re.compile(
    r'/tmp/llmtool-remote-log\.[A-Za-z0-9]+/remote-test\.log'
)


def is_valid_remote_log_path(value):
    """Return True when ``value`` is None or an exact legacy EC2 remote log path."""
    if value is None:
        return True
    if type(value) is not str:
        return False
    return _LEGACY_REMOTE_LOG_PATH.fullmatch(value) is not None
