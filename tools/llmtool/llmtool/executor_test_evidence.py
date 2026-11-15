"""#2119 WP-3 slice 1: expected remote test modules for a Task. Pure; not wired in.

Design: docs/design/executor-step-tests-off-mac.md section 4.1. The remote set
uses the same pattern as remote_test._task_path_modules; the TUI set uses the
test-file patterns of task_advance. Anything that looks like a test but is not
in the remote set is unsupported, and so is every remote path when the set has
more than MAX_REMOTE_MODULES modules: nothing is dropped silently.
"""
import re

from .errors import LLMToolError

MAX_REMOTE_MODULES = 8
_REMOTE = re.compile(r'tools/llmtool/tests/test_[a-z0-9_]{1,80}\.py')
_TUI = re.compile(r'test_(chat_tui|tui_)[a-z0-9_]*')
_TEST_SHAPED = re.compile(r'test_[^/]*\.py')


def expected_remote_modules(read_paths, write_paths):
    """Return (remote, tui, unsupported) as sorted tuples.

    remote: module stems of declared remote test paths, TUI modules included.
    tui: the remote stems that are TUI test modules.
    unsupported: sorted test-shaped paths outside the remote pattern, plus every
    remote path when there are more than MAX_REMOTE_MODULES remote modules.
    """
    paths = set()
    for value in (read_paths, write_paths):
        if type(value) not in (list, tuple) or any(type(item) is not str for item in value):
            raise LLMToolError('EXECUTOR_TEST_PATHS_INVALID')
        paths.update(value)
    remote_paths = sorted(path for path in paths if _REMOTE.fullmatch(path))
    remote = tuple(sorted(path.rsplit('/', 1)[-1][:-3] for path in remote_paths))
    tui = tuple(stem for stem in remote if _TUI.fullmatch(stem))
    unsupported = {path for path in paths
                   if _TEST_SHAPED.fullmatch(path.rsplit('/', 1)[-1]) and
                   not _REMOTE.fullmatch(path)}
    if len(remote) > MAX_REMOTE_MODULES:
        unsupported.update(remote_paths)
    return remote, tui, tuple(sorted(unsupported))
