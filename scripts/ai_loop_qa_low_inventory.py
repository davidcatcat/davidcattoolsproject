#!/usr/bin/env python3
"""Fail-closed private QA inventory for PR55; no models, Git writes or publication.

This is a component for a future reviewed ai-loop maintenance entrypoint.
Do not invoke it as a standalone authorization to merge.
"""
import hashlib
import json
from pathlib import Path

PR_HEAD = "ee8180bc3ad5c455b63e493fee9f2955a90c5463"
QA_RUN = "20261009T050558Z-60868"
DIFF_SHA = "e65bf139f62439be7803981b855ca96add17778eb2daff4c9fb329b69b0c1311"
ALLOWED = frozenset((
    "tools/llmtool/llmtool/test_output_excerpts.py",
    "tools/llmtool/llmtool/cli.py",
    "tools/llmtool/tests/test_test_output_excerpts.py",
))
EXPECTED = {"security": 2, "functional_qa": 4}
MAX_BYTES = 256 * 1024


class EvidenceError(ValueError):
    pass


def canonical_sha(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _read_regular(path):
    """Reject a swapped parent symlink and bound every raw QA input read."""
    import os
    import stat
    path = Path(path)
    if not path.is_absolute():
        raise EvidenceError("QA evidence path must be absolute")
    dirs, fd = [], None
    try:
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        dirs.append(parent)
        for part in path.parts[1:-1]:
            if part in ("", ".", ".."):
                raise EvidenceError("unsafe QA parent path")
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=parent)
            dirs.append(parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
            raise EvidenceError("unsafe or oversized QA record")
        with os.fdopen(os.dup(fd), "rb") as source:
            data = source.read(MAX_BYTES + 1)
        after = os.fstat(fd)
        if (len(data) > MAX_BYTES or len(data) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise EvidenceError("QA record mutated during read")
        return data
    except OSError as exc:
        raise EvidenceError("QA source path missing or symlinked") from exc
    finally:
        if fd is not None:
            os.close(fd)
        for directory in reversed(dirs):
            os.close(directory)


def frozen_inventory(qa_path):
    qa_path = Path(qa_path)
    if qa_path.name != "qa-pr55.json" or qa_path.parent.name != QA_RUN:
        raise EvidenceError("unexpected frozen QA evidence location")
    # Parent symlinks are not allowed either; callers must additionally bind the
    # resolved path to the canonical .ai-loop root before use.
    if any(p.is_symlink() for p in (qa_path, *qa_path.parents)):
        raise EvidenceError("symlink in QA evidence path")
    try:
        raw = _read_regular(qa_path)
        report = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("QA evidence unavailable or malformed") from exc
    if not isinstance(report, dict):
        raise EvidenceError("QA report is not an object")
    if (report.get("run_id") != QA_RUN or report.get("pr") != 55
            or report.get("head") != PR_HEAD or report.get("diff_sha256") != DIFF_SHA
            or report.get("qa_result") != "PASS"
            or report.get("tests_related") != "PASS"
            or report.get("tests_full_local") != "PASS"
            or report.get("merge_authorized") is not False
            or report.get("product_provider_egress_authorized") is not False):
        raise EvidenceError("frozen identity or QA status mismatch")
    roles = report.get("roles")
    if not isinstance(roles, list) or len(roles) != 2:
        raise EvidenceError("missing independent QA roles")
    result = []
    seen = set()
    for role in roles:
        if not isinstance(role, dict) or role.get("status") != "PASS":
            raise EvidenceError("QA role not PASS")
        name = role.get("role")
        if name not in EXPECTED or name in seen:
            raise EvidenceError("unexpected or repeated role")
        seen.add(name)
        findings = role.get("findings")
        if not isinstance(findings, list) or len(findings) != EXPECTED[name]:
            raise EvidenceError("incorrect QA finding count")
        for index, item in enumerate(findings):
            if (not isinstance(item, dict)
                    or set(item) != {"severity", "blocking", "file", "detail", "evidence"}
                    or item["severity"] != "Low" or item["blocking"] is not False
                    or item["file"] not in ALLOWED
                    or not isinstance(item["detail"], str) or not item["detail"].strip()
                    or not isinstance(item["evidence"], str) or not item["evidence"].strip()):
                raise EvidenceError("invalid or blocking finding")
            result.append({"role": name, "ordinal": index + 1,
                           "finding_sha256": canonical_sha(item),
                           "file": item["file"], "severity": "Low"})
    if seen != set(EXPECTED) or len(result) != 6:
        raise EvidenceError("incomplete frozen QA")
    if len({r["finding_sha256"] for r in result}) != 6:
        raise EvidenceError("ambiguous duplicate QA findings")
    return {"head": PR_HEAD, "run_id": QA_RUN, "diff_sha256": DIFF_SHA,
            "qa_record_sha256": hashlib.sha256(raw).hexdigest(),
            "findings": result, "merge_authorized": False}
