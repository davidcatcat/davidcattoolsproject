#!/usr/bin/env python3
"""Run one offline company-gw ladder level against its hidden unittest."""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

OUTPUT_LIMIT = 65_536
TIMEOUT_SECONDS = 60


def _drain(stream, chunks):
    captured = 0
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return
        if captured < OUTPUT_LIMIT:
            kept = chunk[:OUTPUT_LIMIT - captured]
            chunks.append(kept)
            captured += len(kept)


def _run_tests(command, root, timeout=TIMEOUT_SECONDS):
    try:
        process = subprocess.Popen(
            command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return "ERROR", 0, 0, str(exc)

    stdout_chunks = []
    stderr_chunks = []
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, stderr_chunks), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        return_code = process.wait()
    for reader in readers:
        reader.join()

    output = b"".join(stdout_chunks + stderr_chunks).decode("utf-8", errors="replace")
    ran_match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    ran = int(ran_match.group(1)) if ran_match else 0
    failed_match = re.search(r"FAILED\s*\(([^)]*)\)", output)
    failures = 0
    if failed_match:
        for count in re.findall(r"(?:failures|errors)=(\d+)", failed_match.group(1)):
            failures += int(count)
    if timed_out:
        return "ERROR", ran, failures, "test process exceeded 60 seconds"
    if ran == 0:
        return "ERROR", ran, failures, "unittest did not report any tests"
    return ("PASS" if return_code == 0 else "FAIL"), ran, failures, None


def score(level, root):
    root = Path(root).resolve()
    level_dir = root / "tools" / "llmtool" / "eval" / "gw_ladder" / level
    hidden_test = level_dir / "hidden_test.py"
    if not hidden_test.is_file():
        return {"level": level, "result": "ERROR", "ran": 0, "failures": 0}
    command = [
        sys.executable, "-I", "-B", "-m", "unittest", "discover",
        "-s", os.fspath(level_dir), "-p", "hidden_test.py",
    ]
    result, ran, failures, _reason = _run_tests(command, root)
    return {"level": level, "result": result, "ran": ran, "failures": failures}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("level", choices=[f"L{number}" for number in range(1, 10)])
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(score(args.level, args.root), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
