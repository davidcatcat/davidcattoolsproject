#!/usr/bin/env python3
"""Print a deterministic, timing-balanced shard of llmtool test modules."""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
TIMINGS = ROOT / "ci" / "test-module-seconds.json"
MAC_TIMINGS = ROOT / "ci" / "mac-test-module-seconds.json"


def partition(shard_count, timings=None, *, timings_path=None):
    """Return test module filenames grouped into ``shard_count`` shards."""
    path = timings_path if timings_path is not None else timings
    if path is None:
        path = TIMINGS
    timing_data = json.loads(Path(path).read_text(encoding="utf-8"))
    default = timing_data["default_seconds"]
    weights = timing_data["modules"]
    modules = sorted(path.name for path in TESTS.glob("test_*.py"))
    shards = [[] for _ in range(shard_count)]
    totals = [0] * shard_count

    # Longest processing time first keeps each bin balanced. Stable filename
    # ordering and shard-index tie breaks make the output reproducible.
    for module in sorted(modules, key=lambda name: (-weights.get(name, default), name)):
        shard = min(range(shard_count), key=lambda index: (totals[index], index))
        shards[shard].append(module)
        totals[shard] += weights.get(module, default)

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
    for module in partition(args.shard_count, timings=args.timings)[args.shard - 1]:
        print(module)


if __name__ == "__main__":
    main()
