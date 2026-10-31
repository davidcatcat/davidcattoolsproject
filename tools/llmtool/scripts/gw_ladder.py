#!/usr/bin/env python3
"""Run one offline company-gw ladder level against its hidden unittest."""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
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


# Runs the hidden test inside the child and writes the counts to a result file
# the scorer names. PASS needs that file, so a level that exits early (for
# example os._exit(0) at import) or only prints "Ran N tests" is not a PASS.
# The file is written last and only by this harness; level code runs in the
# same process, so this stops accidental or naive spoofing, not deliberate
# in-process tampering.
_HARNESS = """
import json, sys, unittest
level_dir, result_path = sys.argv[1], sys.argv[2]
suite = unittest.defaultTestLoader.discover(level_dir, pattern="hidden_test.py")
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1,
                                warnings="default").run(suite)
with open(result_path, "x", encoding="utf-8") as stream:
    json.dump({"ran": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "ok": result.wasSuccessful()}, stream)
sys.exit(0 if result.wasSuccessful() else 1)
"""
# After the child exits, how long its output pipes may stay open.
DRAIN_SECONDS = 5


def _kill_group(process):
    """Kill the child's whole process group: its descendants may hold the pipes."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, TypeError):
        pass


def _read_result(result_path):
    try:
        with open(result_path, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    if (type(data) is not dict or
            any(type(data.get(key)) is not int or data[key] < 0
                for key in ("ran", "failures", "errors")) or
            type(data.get("ok")) is not bool):
        return None
    return data


def _run_tests(command, root, result_path, timeout=TIMEOUT_SECONDS):
    try:
        process = subprocess.Popen(
            command, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True,
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
        _kill_group(process)
        return_code = process.wait()
    # Descendants left behind by the tested code must not keep the pipes open.
    _kill_group(process)
    deadline = time.monotonic() + DRAIN_SECONDS
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))
    if timed_out:
        return "ERROR", 0, 0, "test process exceeded 60 seconds"
    if any(reader.is_alive() for reader in readers):
        # A descendant in its own session can still hold the pipes.
        return "ERROR", 0, 0, "test output did not close"
    process.stdout.close()
    process.stderr.close()
    data = _read_result(result_path)
    if data is None:
        return "ERROR", 0, 0, "test harness did not report a result"
    failures = data["failures"] + data["errors"]
    if data["ran"] == 0:
        return "ERROR", 0, failures, "unittest did not report any tests"
    passed = return_code == 0 and data["ok"] and failures == 0
    return ("PASS" if passed else "FAIL"), data["ran"], failures, None


def score(level, root):
    root = Path(root).resolve()
    level_dir = root / "tools" / "llmtool" / "eval" / "gw_ladder" / level
    hidden_test = level_dir / "hidden_test.py"
    if not hidden_test.is_file():
        return {"level": level, "result": "ERROR", "ran": 0, "failures": 0}
    with tempfile.TemporaryDirectory(prefix="gw-ladder-") as directory:
        result_path = os.path.join(directory, "result.json")
        command = [sys.executable, "-I", "-B", "-c", _HARNESS,
                   os.fspath(level_dir), result_path]
        result, ran, failures, _reason = _run_tests(command, root, result_path)
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
