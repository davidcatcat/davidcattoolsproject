#!/usr/bin/env python3
"""Allow a manually dispatched full Mac suite run for main."""

from __future__ import annotations

import argparse
import sys
def is_due(event: str) -> bool:
    """Return whether an event may run the full suite; reject stale triggers."""
    if event != "workflow_dispatch":
        raise ValueError(f"unexpected event {event!r}; expected 'workflow_dispatch'")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", required=True, help="owner/repository (accepted for workflow compatibility)"
    )
    parser.add_argument("--sha", required=True, help="current main commit SHA")
    parser.add_argument(
        "--event",
        required=True,
        choices=("workflow_dispatch",),
        help="workflow event; only workflow_dispatch is supported",
    )
    args = parser.parse_args(argv)
    print("DUE" if is_due(args.event) else "SKIP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
