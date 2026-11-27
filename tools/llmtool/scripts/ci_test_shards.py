#!/usr/bin/env python3
"""Print a deterministic, timing-balanced shard of llmtool test modules."""

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
TIMINGS = ROOT / "ci" / "test-module-seconds.json"
MAC_TIMINGS = ROOT / "ci" / "mac-test-module-seconds.json"
INVALID_TIMING_DIAGNOSTIC = "invalid timing configuration"


def _is_finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _to_decimal(value):
    return Decimal.from_float(value) if isinstance(value, float) else Decimal(value)


def _reject_json_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_timings(path):
    timing_data = json.loads(
        Path(path).read_text(encoding="utf-8"),
        parse_constant=_reject_json_constant,
    )
    if not isinstance(timing_data, dict):
        raise ValueError("timing data must be a JSON object")
    if "default_seconds" not in timing_data or "modules" not in timing_data:
        raise ValueError("timing data must contain 'default_seconds' and 'modules'")

    default = timing_data["default_seconds"]
    if not _is_finite_number(default) or default <= 0:
        raise ValueError("'default_seconds' must be a finite positive number")

    weights = timing_data["modules"]
    if not isinstance(weights, dict):
        raise ValueError("'modules' must be a JSON object")

    for module_name, weight in weights.items():
        if not isinstance(module_name, str) or not module_name:
            raise ValueError("module keys in 'modules' must be non-empty strings")
        if not _is_finite_number(weight) or weight < 0:
            raise ValueError(f"weight for {module_name!r} must be a finite nonnegative number")

    return default, weights


def partition(shard_count, timings=None):
    """Return test module filenames grouped into ``shard_count`` shards."""
    path = timings if timings is not None else TIMINGS
    default, weights = _load_timings(path)
    modules = sorted(path.name for path in TESTS.glob("test_*.py"))
    shards = [[] for _ in range(shard_count)]
    totals = [Decimal(0)] * shard_count

    # Longest processing time first keeps each bin balanced. Stable filename
    # ordering and shard-index tie breaks make the output reproducible.
    for module in sorted(modules, key=lambda name: (-weights.get(name, default), name)):
        shard = min(range(shard_count), key=lambda index: (totals[index], index))
        shards[shard].append(module)
        totals[shard] += _to_decimal(weights.get(module, default))

    return [sorted(shard) for shard in shards]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard_count", type=int, help="number of shards")
    parser.add_argument("shard", type=int, help="1-based shard number")
    parser.add_argument(
        "--timings",
        type=Path,
        default=None,
        metavar="PATH",
        help="path to module timing json",
    )
    args = parser.parse_args()
    if args.shard_count < 1 or not 1 <= args.shard <= args.shard_count:
        parser.error("require shard_count >= 1 and 1 <= shard <= shard_count")
    try:
        selected = partition(args.shard_count, timings=args.timings)[args.shard - 1]
    except (OSError, ValueError):
        raise SystemExit(INVALID_TIMING_DIAGNOSTIC)
    for module in selected:
        print(module)


if __name__ == "__main__":
    main()
