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


def _body_ask(question: str, sheet: str | None) -> str:
    body = f"{MARKER_ASK}\n\n{question.strip()}"
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
    answer = sub.add_parser("answer")
    answer.add_argument("number")
    answer.add_argument("--owner-words", required=True)
    answer.add_argument("--pm-reading", required=True)
    sub.add_parser("open-rows")
    args = parser.parse_args(argv)
    try:
        if args.command != "open-rows" and not ISSUE_NUMBER.fullmatch(args.number):
            raise MarkerError("issue or pull request number is invalid")
        repo = repository_slug(args.repo)
        if args.command == "ask":
            question = args.question.strip()
            if not question or question.startswith("<!--"):
                raise MarkerError("question is required")
            sheet = Path(args.sheet).read_text(encoding="utf-8") if args.sheet else None
            print(_post(args.number, _body_ask(question, sheet), repo))
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
