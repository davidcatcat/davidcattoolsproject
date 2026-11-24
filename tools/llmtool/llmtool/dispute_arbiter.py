"""#2087: ruling-specific parts of the dispute arbiter flow.

This slice holds only the versioned ruling header pattern, defined once here.
The legacy header without ``Version:`` stays in ``task_submission_cli``.
"""

import re

# Second non-empty line of an arbiter's ruling comment:
#   Ruling: <F#> <UPHELD|WITHDRAWN> | PR: #<pr> | Version: <review_version> | HEAD: <head>
# Groups: finding id, outcome, PR number, review version, head.
RULING_HEADER_VERSIONED = re.compile(
    r'Ruling: (F[1-9][0-9]{0,3}) (UPHELD|WITHDRAWN) \| PR: #([1-9][0-9]{0,8}) \| '
    r'Version: ([1-9][0-9]{0,3}) \| HEAD: ([0-9a-f]{40})')
