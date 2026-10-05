"""Read only the pilot's synthetic fixtures; emit auditable line/byte ranges."""

import hashlib
import json
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parent
    name, first, last = sys.argv[1:]
    if name not in ("B01_test_triage", "B02_log_triage", "B03_context_pack"):
        raise ValueError("not a pilot fixture")
    raw = (root / name / "fixture.txt").read_bytes()
    lines = raw.splitlines(keepends=True)
    first, last = int(first), int(last)
    if not 1 <= first <= last <= len(lines):
        raise ValueError("invalid bounded line range")
    start = sum(map(len, lines[:first - 1]))
    end = sum(map(len, lines[:last]))
    excerpt = raw[start:end]
    print("EVIDENCE_READ_METADATA " + json.dumps({
        "fixture": name, "first_line": first, "last_line": last,
        "start_byte": start, "end_byte_exclusive": end,
        "bytes": len(excerpt), "sha256": hashlib.sha256(excerpt).hexdigest(),
    }, sort_keys=True))
    print("EVIDENCE_BEGIN")
    sys.stdout.write(excerpt.decode("utf-8"))
    print("EVIDENCE_END")


if __name__ == "__main__":
    main()
