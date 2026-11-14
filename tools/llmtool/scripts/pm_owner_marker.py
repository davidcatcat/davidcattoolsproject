#!/usr/bin/env python3
"""Post and list PM-relayed Owner decision markers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


MARKER_ASK = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"
MARKER_ANSWER = "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->"
REPO_SLUG = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
ISSUE_NUMBER = re.compile(r"[1-9][0-9]{0,6}\Z")
MARKER = re.compile(r"<!--\s*activity:([^<>]{0,300}?)-->")
OWNER_FIELD = re.compile(
    r"(?:(?<=\s)|(?<=activity:))(type|actor|target|status)="
    r"([A-Za-z0-9_.#-]{1,64})(?![\w.#=-])"
)
OWNER_SETTLED = frozenset({
    "APPROVED", "DECLINED", "DENIED", "GRANTED", "REJECTED", "ANSWERED",
    "RESOLVED", "DONE", "CLOSED", "CANCELLED", "WITHDRAWN", "AUTHORIZED",
    "ACCEPTED", "ACKNOWLEDGED", "SUPERSEDED",
})
WAITING_OWNER_DIRECTIONS = {
    ("integrator", "owner"): frozenset({"BLOCKED", "WAITING", "UNKNOWN"}),
    ("pm", "owner"): frozenset({"BLOCKED", "WAITING", "UNKNOWN"}),
    ("owner", "pm"): frozenset({"ANSWERED"}),
    ("owner", "integrator"): frozenset({"ANSWERED"}),
}
TRUSTED_ASSOCIATION = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

# Owner ask gate rows (#1943). Must match the Owner ask gate table in
# skills/ai-team-delivery/owner-escalation.md.
RESERVED_ROWS = ("R-SCOPE", "R-RELEASE", "R-SPEND", "R-EGRESS", "R-SECURITY", "R-CRED",
                 "R-ROOT", "R-MACOS", "R-REVIEWER", "R-DISPATCH", "R-ACCEPT", "R-BUDGET",
                 "R-STOP", "R-OTHER")
# Line 2 of an ask. It is deliberately not an activity marker, so the marker
# parser (here and in the monitor) stops at it and reads line 1 only.
OWNER_ASK_LINE = "<!-- owner-ask: v=1 reserved_row={row} -->"
ASK_REQUIRED_FIELDS = ("reserved_row", "blocked_action", "authority_check", "evidence",
                       "not_defect_not_harness")
ASK_SINGLE_LINE_FIELDS = ("reserved_row", "blocked_action")
ASK_FIELD_MAX = 2000
_NOT_WORD_BEFORE = r"(?<![A-Za-z0-9_])"
_NOT_WORD_AFTER = r"(?![A-Za-z0-9_])"
DISPATCH_EVIDENCE = re.compile(
    _NOT_WORD_BEFORE + r"(?:OWNER_PAUSED|OWNER_ASSIGNMENT_REQUIRED)" + _NOT_WORD_AFTER)
ACCEPT_CODE = re.compile(_NOT_WORD_BEFORE + r"MERGE_OWNER_ACCEPTANCE_MANUAL" + _NOT_WORD_AFTER)
PR_REFERENCE = re.compile(r"(?<![A-Za-z0-9_&#])#[1-9][0-9]{0,6}" + _NOT_WORD_AFTER)
HEAD_SHA = re.compile(_NOT_WORD_BEFORE + r"[0-9a-fA-F]{40}" + _NOT_WORD_AFTER)
ISSUE_COMMENT_URL = re.compile(
    _NOT_WORD_BEFORE + r"https://github\.com/[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}"
    r"/issues/[1-9][0-9]{0,6}#issuecomment-[1-9][0-9]{0,19}" + _NOT_WORD_AFTER)
REVIEWER_NAME = re.compile(r"(?<![A-Za-z0-9_-])(?:codex|claude)-reviewer(?![A-Za-z0-9_-])")
ROW_EVIDENCE = {
    "R-DISPATCH": lambda text: DISPATCH_EVIDENCE.search(text) is not None,
    "R-ACCEPT": lambda text: (ACCEPT_CODE.search(text) is not None and
                              PR_REFERENCE.search(text) is not None and
                              HEAD_SHA.search(text) is not None),
    "R-STOP": lambda text: ISSUE_COMMENT_URL.search(text) is not None,
    "R-REVIEWER": lambda text: REVIEWER_NAME.search(text) is not None,
}


class MarkerError(Exception):
    pass


try:
    from llmtool import ai_github_exec
except ImportError:
    try:
        import ai_github_exec
    except ImportError:
        _pkg_dir = Path(__file__).resolve().parent.parent
        if str(_pkg_dir) not in sys.path:
            sys.path.insert(0, str(_pkg_dir))
        from llmtool import ai_github_exec

AiIdentityError = ai_github_exec.AiIdentityError


def verified_ai_env(*args, **kwargs):
    return ai_github_exec.verified_ai_env(*args, **kwargs)


def _run(argv: list[str], *, timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False, shell=False, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MarkerError("command failed") from exc


def repository_slug(explicit: str | None = None) -> str:
    if explicit is not None:
        if not REPO_SLUG.fullmatch(explicit):
            raise MarkerError("repository slug is invalid")
        return explicit
    result = _run(["git", "remote", "get-url", "origin"], timeout=10)
    if result.returncode:
        raise MarkerError("cannot determine repository from origin remote")
    remote = result.stdout.decode("utf-8", "replace").strip()
    match = re.search(r"(?:github\.com[:/])([^/\s:]+/[^/\s]+?)(?:\.git)?\Z", remote)
    slug = match.group(1) if match else ""
    if not REPO_SLUG.fullmatch(slug):
        raise MarkerError("origin remote is not a GitHub repository")
    return slug


def _post(number: str, body: str, repo: str) -> str:
    try:
        env = verified_ai_env()
    except AiIdentityError as exc:
        raise MarkerError(f"GITHUB_AI_IDENTITY_REFUSED {exc.code}") from exc
    _gh_path = os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    env.update({
        "PATH": _gh_path,
        "LC_ALL": "C",
        "GH_PROMPT_DISABLED": "1",
        "NO_COLOR": "1",
    })
    fd, path = tempfile.mkstemp(prefix="pm-owner-marker-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(body)
            stream.write("\n")
        result = _run(["gh", "issue", "comment", number, "--repo", repo,
                       "--body-file", path], env=env)
        if result.returncode:
            raise MarkerError("gh failed to post comment")
        url = result.stdout.decode("utf-8", "replace").strip().splitlines()
        if not url or not url[-1].startswith(("https://github.com/", "http://github.com/")):
            raise MarkerError("gh did not return a comment URL")
        return url[-1]
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _github_json(repo: str, endpoint: str) -> list:
    result = _run(["gh", "api", "--paginate", "--slurp",
                   f"repos/{repo}/{endpoint}"])
    if result.returncode:
        raise MarkerError("gh failed to read repository data")
    try:
        payload = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarkerError("gh returned invalid JSON") from exc
    if type(payload) is not list or any(type(page) is not list for page in payload):
        raise MarkerError("gh returned unexpected JSON")
    return [item for page in payload for item in page]


def _github_time(value) -> int:
    if type(value) is not str:
        return 0
    try:
        from datetime import datetime, timezone
        return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def _issue_number(url) -> str:
    found = re.search(r"/(?:issues|pull)/(\d{1,7})$", url) if type(url) is str else None
    return found.group(1) if found else ""


def _waiting_markers(comments: list) -> dict[str, tuple[int, int, str, str]]:
    """Mirror monitor.owner_pending marker, trust, ordering and settlement rules."""
    marked = []
    for comment in comments:
        if type(comment) is not dict:
            continue
        association = comment.get("author_association")
        if type(association) is not str or association not in TRUSTED_ASSOCIATION:
            continue
        number, body = _issue_number(comment.get("issue_url")), comment.get("body")
        if not number or type(body) is not str:
            continue
        for line in body.splitlines()[:4]:
            if MARKER.fullmatch(line) is None:
                break
            fields = {}
            for key, value in OWNER_FIELD.findall(line):
                value = value.rstrip("-")
                fields.setdefault(key, value.casefold() if key in ("actor", "target") else value)
            marked.append((_github_time(comment.get("created_at")),
                           comment.get("id") if type(comment.get("id")) is int else 0,
                           number, fields))
    latest = {}
    for when, order, number, fields in sorted(marked, key=lambda item: item[:2]):
        current = latest.get(number)
        if current is not None and (when, order) < current[:2]:
            continue
        kind, actor = fields.get("type", ""), fields.get("actor", "")
        target, status = fields.get("target", ""), fields.get("status", "")
        settles = actor == "owner" or (target == "owner" and status.upper() in OWNER_SETTLED)
        if (settles and kind.casefold() == "waiting_owner" and
                status.upper() not in WAITING_OWNER_DIRECTIONS.get((actor, target), frozenset())):
            continue
        if settles:
            latest.pop(number, None)
        elif target == "owner" and when:
            latest[number] = (when, order, kind or "unknown", status or "UNKNOWN")
    return latest


def open_rows_from_latest(latest: dict, pulls: list) -> list[str]:
    closed = {}
    for pull in pulls:
        if type(pull) is dict and type(pull.get("number")) is int:
            closed[str(pull["number"])] = max(_github_time(pull.get("merged_at")),
                                                _github_time(pull.get("closed_at")))
    return sorted((number for number, (when, _, _, _) in latest.items()
                   if closed.get(number, 0) <= when),
                  key=lambda number: (latest[number][0], int(number)))


def open_rows(repo: str) -> list[tuple[str, str, str]]:
    comments = _github_json(repo, "issues/comments?per_page=100")
    pulls = _github_json(repo, "pulls?state=all&per_page=100")
    latest = _waiting_markers(comments)
    return [(number, latest[number][2], latest[number][3])
            for number in open_rows_from_latest(latest, pulls)]


def ask_record(reserved_row, blocked_action, authority_check, evidence,
               not_defect_not_harness, other_reason=None) -> tuple[tuple[str, str], ...]:
    """Return the validated ask record as ordered (name, value) pairs.

    Refusals raise MarkerError whose message starts with a fixed code and names
    at most the field, never its value. Nothing here touches GitHub.
    """
    given = dict(zip(ASK_REQUIRED_FIELDS, (reserved_row, blocked_action, authority_check,
                                           evidence, not_defect_not_harness)))
    values = {}
    for name in ASK_REQUIRED_FIELDS:
        value = given[name]
        if type(value) is not str or not value.strip():
            raise MarkerError(f"OWNER_ASK_FIELD_MISSING {name}")
        values[name] = value.strip()
    if other_reason is not None and type(other_reason) is not str:
        raise MarkerError("OWNER_ASK_FIELD_INVALID other_reason")
    other = (other_reason or "").strip()
    for name, value in (*values.items(), ("other_reason", other)):
        if "<!--" in value or "-->" in value or len(value) > ASK_FIELD_MAX:
            raise MarkerError(f"OWNER_ASK_FIELD_INVALID {name}")
    for name in ASK_SINGLE_LINE_FIELDS:
        if values[name].splitlines() != [values[name]]:
            raise MarkerError(f"OWNER_ASK_FIELD_INVALID {name}")
    row = values["reserved_row"]
    if row not in RESERVED_ROWS:
        raise MarkerError("OWNER_ASK_ROW_UNKNOWN")
    if row == "R-OTHER" and not other:
        raise MarkerError("OWNER_ASK_OTHER_REASON_REQUIRED")
    check = ROW_EVIDENCE.get(row)
    if check is not None and not check(values["evidence"]):
        raise MarkerError("OWNER_ASK_EVIDENCE_MISMATCH")
    record = [(name, values[name]) for name in ASK_REQUIRED_FIELDS]
    if row == "R-OTHER":
        record.append(("other_reason", other))
    return tuple(record)


def _body_ask(question: str, sheet: str | None, record: tuple[tuple[str, str], ...]) -> str:
    row = dict(record)["reserved_row"]
    if row not in RESERVED_ROWS:
        raise MarkerError("OWNER_ASK_ROW_UNKNOWN")
    lines = [MARKER_ASK, OWNER_ASK_LINE.format(row=row), "", question.strip(), "",
             "Ask record:"]
    for name, value in record:
        lines.append(f"- {name}: " + "\n  ".join(value.splitlines()))
    body = "\n".join(lines)
    if sheet:
        body += "\n\n" + sheet.rstrip()
    return body


def _body_answer(owner_words: str, pm_reading: str) -> str:
    return (f"{MARKER_ANSWER}\n\nOwner words relayed by the PM:\n> " +
            owner_words.strip().replace("\n", "\n> ") +
            "\n\nPM reading:\n" + pm_reading.strip())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="GitHub OWNER/NAME (defaults to the origin remote)")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("number")
    ask.add_argument("--question", required=True)
    ask.add_argument("--sheet")
    ask.add_argument("--reserved-row", required=True, help="one of: " + ", ".join(RESERVED_ROWS))
    ask.add_argument("--blocked-action", required=True)
    ask.add_argument("--authority-check", required=True)
    ask.add_argument("--evidence", required=True)
    ask.add_argument("--not-defect-not-harness", required=True)
    ask.add_argument("--other-reason")
    answer = sub.add_parser("answer")
    answer.add_argument("number")
    answer.add_argument("--owner-words", required=True)
    answer.add_argument("--pm-reading", required=True)
    sub.add_parser("open-rows")
    args = parser.parse_args(argv)
    try:
        if args.command != "open-rows" and not ISSUE_NUMBER.fullmatch(args.number):
            raise MarkerError("issue or pull request number is invalid")
        if args.command == "ask":
            # Validate the whole ask before any repository or GitHub access.
            question = args.question.strip()
            if not question or question.startswith("<!--"):
                raise MarkerError("question is required")
            record = ask_record(args.reserved_row, args.blocked_action, args.authority_check,
                                args.evidence, args.not_defect_not_harness, args.other_reason)
        repo = repository_slug(args.repo)
        if args.command == "ask":
            sheet = Path(args.sheet).read_text(encoding="utf-8") if args.sheet else None
            print(_post(args.number, _body_ask(question, sheet, record), repo))
        elif args.command == "answer":
            if not args.owner_words.strip() or not args.pm_reading.strip():
                raise MarkerError("Owner words and PM reading are required")
            print(_post(args.number, _body_answer(args.owner_words, args.pm_reading), repo))
        else:
            for number, kind, status in open_rows(repo):
                print(f"{number} {kind} {status}")
    except (MarkerError, OSError, UnicodeError) as exc:
        print(f"pm_owner_marker: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
