#!/usr/bin/env python3
"""Run selected llmtool unittest modules and write a remote-test receipt.json."""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MODULE = re.compile(r"test_[a-z0-9_]{1,80}\Z")
_MAX_REMOTE_MODULES = 8
_MODULE_TIMEOUT = 180
_TOTAL_TIMEOUT = 540
_REMOTE_LIMIT = 32768
_FAILURE_EXCERPT_LIMIT = 4096
_FAILURE_EXCERPT_LINES = 40
_MAX_LOG_BYTES = 20_000_000
_RECEIPT_FIELDS = frozenset((
    "result", "exit_code", "source_sha", "tree", "fetch_head", "modules",
    "identity", "started_at", "ended_at", "elapsed_s", "ran_total", "final",
    "failures", "log_bytes", "log_sha256", "remote_log_path",
))
SENSITIVE = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|(?:(?<![A-Za-z0-9_])(?:AKIA|ASIA)[A-Z0-9]{16}\b|(?<!(?-i:[A-Za-z0-9_]))(?:AKIA|ASIA)[A-Z0-9]{16}(?=(?-i:[^\W\x00-\x7f])))|\bsk-[A-Za-z0-9_-]{12,}|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|\bxox[abprs]-[A-Za-z0-9-]{20,}|\b(?:bearer|basic)\s+\S+|\b[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@|\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|[\"']?\b(?:api[_-]?key|password|passwd|token|secret|access[_-]?token|client[_-]?secret|aws_secret_access_key)[\"']?\s*[:=]",
    re.I,
)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def screen_line(line):
    return SENSITIVE.search(line) is not None


def final_line(lines):
    return next(
        (
            line for line in reversed(lines)
            if line.endswith("OK") or line.startswith("FAILED (") or
            re.fullmatch(
                r"OK(?: \((?:skipped=\d+|expected failures=\d+)(?:, (?:skipped=\d+|expected failures=\d+))*\))?",
                line,
            ) or
            line.endswith("FAILED") or line.startswith("ERROR")
        ),
        "NO_FINAL",
    )


def passing_final(line):
    return line.endswith("OK") or re.fullmatch(r"OK \(skipped=[1-9][0-9]{0,5}\)", line) is not None


def failure_excerpt(outputs, work, home, *, screen=None):
    check_sensitive = screen_line if screen is None else screen
    if isinstance(home, str):
        homes = [home] if home else []
    elif isinstance(home, (list, tuple, set)):
        homes = [item for item in home if isinstance(item, str) and item]
    else:
        homes = []
    real_home = os.path.expanduser("~")
    if real_home and real_home != "/" and real_home not in homes:
        homes.append(real_home)
    blocks = []

    def safe_line(line):
        if work:
            line = line.replace(work, "<REMOTE_WORKDIR>")
        for item in homes:
            line = line.replace(item, "<REMOTE_HOME>")
        if not check_sensitive(line):
            return line
        if re.fullmatch(r"(?:FAIL|ERROR): [A-Za-z0-9_.-]+ \([A-Za-z0-9_.]+\)", line):
            without_test_id = re.sub(r"\([A-Za-z0-9_.]+\)", "()", line)
            if not check_sensitive(without_test_id):
                return line
        return None

    for output in outputs:
        raw_bytes = output if isinstance(output, bytes) else str(output).encode("utf-8")
        lines = raw_bytes.decode("utf8", "replace").splitlines()
        index = 0
        while index < len(lines):
            if not re.match(r"^(?:FAIL|ERROR): ", lines[index]):
                index += 1
                continue
            block = []
            header = safe_line(lines[index])
            if header is not None:
                block.append(header)
            index += 1
            if index < len(lines) and re.match(r"^(?:={10,}|-{10,})$", lines[index]):
                index += 1
            while index < len(lines) and not re.match(r"^(?:={10,}|-{10,})$", lines[index]):
                line = safe_line(lines[index])
                if line is not None:
                    block.append(line)
                index += 1
            blocks.extend(block)
    kept = []
    size = 0
    for line in blocks:
        encoded = (line + "\n").encode("utf8")
        if len(kept) >= _FAILURE_EXCERPT_LINES or size + len(encoded) > _FAILURE_EXCERPT_LIMIT:
            break
        kept.append(line)
        size += len(encoded)
    return "\n".join(kept)


def _kill_session(pid):
    if not isinstance(pid, int) or pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def run_module(python, src, module, timeout):
    temp_root = tempfile.mkdtemp(prefix="llmtool-remote-test-mod.")
    os.chmod(temp_root, 0o700)
    home_dir = os.path.join(temp_root, "home")
    tmp_dir = os.path.join(temp_root, "tmp")
    os.mkdir(home_dir, 0o700)
    os.mkdir(tmp_dir, 0o700)
    env = os.environ.copy()
    env.update({
        "HOME": home_dir,
        "TMPDIR": tmp_dir,
        "TEMP": tmp_dir,
        "TMP": tmp_dir,
        "GIT_OPTIONAL_LOCKS": "0",
        "PYTHONNOUSERSITE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    })
    argv = [python, "-m", "unittest", "discover", "-s", "tests", "-p", f"{module}.py"]
    proc = None
    timed_out = False
    stdout = b""
    stderr = b""
    try:
        proc = subprocess.Popen(
            argv,
            cwd=src,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=max(0.1, float(timeout)))
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_session(proc.pid)
            stdout, stderr = proc.communicate()
    finally:
        if proc is not None:
            _kill_session(proc.pid)
        shutil.rmtree(temp_root, ignore_errors=True)
    if timed_out:
        returncode = 124
    else:
        returncode = proc.returncode if proc is not None and proc.returncode is not None else 1
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout or b"",
        stderr=stderr or b"",
        timed_out=timed_out,
        home=home_dir,
    )


def _resolve_tree(src, sha):
    for rev in (f"{sha}^{{tree}}", "HEAD^{tree}"):
        try:
            proc = subprocess.run(
                ["git", "-C", src, "rev-parse", "--verify", rev],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            candidate = proc.stdout.decode("ascii", "replace").strip()
            if _SHA.fullmatch(candidate):
                return candidate
    return hashlib.sha1(f"tree {sha}".encode("ascii")).hexdigest()


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _parse_and_validate(argv):
    parser = _ArgumentParser(prog="remote_test_receipt.py", add_help=False)
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", add_help=False)
    run_parser.add_argument("--src", required=True)
    run_parser.add_argument("--python", required=True)
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--sha", required=True)
    run_parser.add_argument("--fetch-head", dest="fetch_head", required=True)
    run_parser.add_argument("modules", nargs="+")

    mismatch_parser = subparsers.add_parser("mismatch", add_help=False)
    mismatch_parser.add_argument("--out", required=True)
    mismatch_parser.add_argument("--sha", required=True)
    mismatch_parser.add_argument("--fetch-head", dest="fetch_head", required=True)
    mismatch_parser.add_argument("modules", nargs="+")

    args = parser.parse_args(argv)
    if args.command not in ("run", "mismatch"):
        raise ValueError("missing or invalid subcommand")
    if (
        not isinstance(args.out, str) or not args.out or
        ".." in Path(args.out).parts or os.path.lexists(args.out)
    ):
        raise ValueError("invalid or existing out directory")
    if not isinstance(args.sha, str) or _SHA.fullmatch(args.sha) is None:
        raise ValueError("invalid sha")
    if (
        not args.modules or
        any(not isinstance(mod, str) or _MODULE.fullmatch(mod) is None for mod in args.modules) or
        len(set(args.modules)) != len(args.modules) or
        len(args.modules) > _MAX_REMOTE_MODULES
    ):
        raise ValueError("invalid modules")
    args.modules = sorted(args.modules)

    if args.command == "run":
        if (
            not isinstance(args.fetch_head, str) or
            _SHA.fullmatch(args.fetch_head) is None or
            args.fetch_head != args.sha
        ):
            raise ValueError("invalid fetch-head for run")
        if (
            not isinstance(args.src, str) or not os.path.isabs(args.src) or
            ".." in Path(args.src).parts or not os.path.isdir(args.src)
        ):
            raise ValueError("invalid src path")
        if (
            not isinstance(args.python, str) or not os.path.isabs(args.python) or
            ".." in Path(args.python).parts or not os.path.isfile(args.python)
        ):
            raise ValueError("invalid python path")
    else:
        if args.fetch_head == "none":
            args.fetch_head = None
        elif (
            not isinstance(args.fetch_head, str) or
            _SHA.fullmatch(args.fetch_head) is None or
            args.fetch_head == args.sha
        ):
            raise ValueError("invalid fetch-head for mismatch")
    return args


def _normalize_run_result(raw_result):
    if isinstance(raw_result, tuple):
        returncode, stdout, stderr = raw_result[:3]
        timed_out = bool(raw_result[3]) if len(raw_result) > 3 else (returncode == 124)
        home = None
    else:
        returncode = getattr(raw_result, "returncode", 1)
        stdout = getattr(raw_result, "stdout", b"")
        stderr = getattr(raw_result, "stderr", b"")
        timed_out = bool(getattr(raw_result, "timed_out", False)) or returncode == 124
        home = getattr(raw_result, "home", None)
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8")
    elif stdout is None:
        stdout = b""
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8")
    elif stderr is None:
        stderr = b""
    return returncode, stdout, stderr, timed_out, home


def _write_receipt(out_dir, receipt):
    raw = json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > _REMOTE_LIMIT:
        raise ValueError("receipt exceeds limit")
    (Path(out_dir) / "receipt.json").write_bytes(raw)


def main(argv=None, *, runner=None, clock=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    monotonic = time.monotonic if clock is None else clock
    execute_module = run_module if runner is None else runner
    try:
        args = _parse_and_validate(argv)
        os.makedirs(args.out, mode=0o700, exist_ok=False)
    except (OSError, ValueError):
        return 40

    started = _now()
    begin = monotonic()
    uid = os.getuid()

    if args.command == "mismatch":
        ended = _now()
        elapsed = round(max(0.0, monotonic() - begin), 3)
        receipt = {
            "result": "SHA_MISMATCH",
            "exit_code": 41,
            "source_sha": args.sha,
            "tree": None,
            "fetch_head": args.fetch_head,
            "modules": args.modules,
            "identity": {"uid": uid},
            "started_at": started,
            "ended_at": ended,
            "elapsed_s": elapsed,
            "ran_total": 0,
            "final": [],
            "failures": [],
            "log_bytes": 0,
            "log_sha256": hashlib.sha256(b"").hexdigest(),
            "remote_log_path": None,
            "failure_excerpt": "",
        }
        try:
            _write_receipt(args.out, receipt)
        except (OSError, ValueError):
            return 1
        print("SHA_MISMATCH")
        return 41

    deadline = begin + _TOTAL_TIMEOUT
    tree = _resolve_tree(args.src, args.sha)
    log = b""
    result = "FAIL"
    code = 1
    finals = []
    failures = []
    failed_outputs = []
    homes = []
    ran = 0
    printed_summaries = []

    for mod in args.modules:
        remaining = deadline - monotonic()
        if remaining <= 0:
            result = "TIMEOUT"
            code = 124
            failures.append(mod)
            finals.append("NO_FINAL")
            printed_summaries.append((None, "NO_FINAL"))
            break
        limit = min(_MODULE_TIMEOUT, remaining)
        try:
            raw_res = execute_module(args.python, args.src, mod, limit)
            returncode, stdout, stderr, timed_out, mod_home = _normalize_run_result(raw_res)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.encode("utf-8") if isinstance(exc.stdout, str) else (exc.stdout or b"")
            stderr = exc.stderr.encode("utf-8") if isinstance(exc.stderr, str) else (exc.stderr or b"")
            returncode = 124
            timed_out = True
            mod_home = None

        if mod_home:
            homes.append(mod_home)
        test_output = stdout + stderr
        log += test_output
        lines = [
            x.decode("utf8", "replace").strip()
            for x in test_output.splitlines()
            if b"Ran " in x or b"OK" in x or b"FAILED" in x or b"ERROR" in x
        ]
        final = final_line(lines)[:512]
        finals.append(final)
        ran_match = re.search(rb"Ran (\d+) tests?", test_output)
        ran += int(ran_match.group(1)) if ran_match else 0
        ran_line = next(
            (
                x.decode("utf8", "replace").strip()
                for x in test_output.splitlines()
                if re.match(rb"^Ran \d+ tests?\b", x.strip())
            ),
            None,
        )
        printed_summaries.append((ran_line, final))

        if timed_out or returncode == 124:
            result = "TIMEOUT"
            code = 124
            failures.append(mod)
            failed_outputs.extend((stdout, stderr))
            break
        if returncode != 0 or not passing_final(final):
            result = "FAIL"
            code = returncode if isinstance(returncode, int) and returncode > 0 else 1
            failures.append(mod)
            failed_outputs.extend((stdout, stderr))

    if result != "TIMEOUT" and len(failures) == 0 and ran > 0:
        result = "PASS"
        code = 0

    ended = _now()
    elapsed = round(max(0.0, monotonic() - begin), 3)
    receipt = {
        "result": result,
        "exit_code": code,
        "source_sha": args.sha,
        "tree": tree,
        "fetch_head": args.fetch_head,
        "modules": args.modules,
        "identity": {"uid": uid},
        "started_at": started,
        "ended_at": ended,
        "elapsed_s": elapsed,
        "ran_total": ran,
        "final": finals[-len(args.modules) * 2:],
        "failures": failures[:10],
        "log_bytes": min(len(log), _MAX_LOG_BYTES),
        "log_sha256": hashlib.sha256(log).hexdigest(),
        "remote_log_path": None,
    }
    if result != "PASS":
        receipt["failure_excerpt"] = failure_excerpt(failed_outputs, args.src, homes)

    try:
        _write_receipt(args.out, receipt)
    except (OSError, ValueError):
        return 1

    for ran_line, final in printed_summaries:
        if ran_line and not screen_line(ran_line):
            print(ran_line)
        if final and not screen_line(final):
            print(final)
    if result != "PASS" and receipt.get("failure_excerpt"):
        print(receipt["failure_excerpt"])
    print(result)
    return 0 if result == "PASS" else (code or 1)


if __name__ == "__main__":
    sys.exit(main())
