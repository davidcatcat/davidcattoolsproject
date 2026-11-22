"""One-shot, bounded EC2 test execution for an admitted D6 Task result.

``run_incident_remote_test`` (#1706 R9) runs the same session for a main
incident: an admitted incident SHA and its failing modules, recorded on the
incident instead of a Task.
"""

import pathlib as _pathlib


def _load_part(filename):
    """Execute one source part in this module's own namespace."""
    path = _pathlib.Path(__file__).with_name(filename)
    exec(compile(path.read_bytes(), str(path), 'exec'), globals())


for _part in (
        'remote_test_part1.py',
        'remote_test_part2.py',
        'remote_test_part3.py',
        'remote_test_part4.py'):
    _load_part(_part)

del _load_part, _part, _pathlib
