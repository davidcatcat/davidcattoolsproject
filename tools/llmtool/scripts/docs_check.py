#!/usr/bin/env python3
"""Fast, offline checks for changed Markdown files."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


REPO = Path(__file__).resolve().parents[3]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
HTML_ID = re.compile(r"<a\b[^>]*\bid\s*=\s*['\"]([^'\"]+)['\"]", re.I)
FENCE = re.compile(r"^\s*(```+|~~~+)")
SENSITIVE = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bsk-[A-Za-z0-9_-]{12,}|"
    r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|"
    r"\bxox[abprs]-[A-Za-z0-9-]{20,}|\b(?:bearer|basic)\s+\S+|"
    r"[\"']?\b(?:api[_-]?key|password|passwd|token|secret|access[_-]?token|"
    r"client[_-]?secret|aws_secret_access_key)[\"']?\s*[:=]", re.IGNORECASE)
JWT_SEGMENT = re.compile(r"[A-Za-z0-9_-]+", re.IGNORECASE)
JWT_START = re.compile(r"\beyJ", re.IGNORECASE)
JWT_END = re.compile(r"[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
URL_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*", re.IGNORECASE)
URL_AUTHORITY = re.compile(r"[^\s/]+")


def emitted_sensitive(text: str) -> bool:
    """Match llmtool.test_output_excerpts._emitted_sensitive indicators."""
    def sensitive(value: str) -> bool:
        if SENSITIVE.search(value):
            return True
        previous_end, first, second = None, False, False
        for segment in JWT_SEGMENT.finditer(value):
            start, end = segment.span()
            adjacent = previous_end is not None and start == previous_end + 1 and value[previous_end] == "."
            if adjacent and second and JWT_END.match(value, start):
                return True
            second = adjacent and first and end - start >= 8
            first = end - start >= 11 and JWT_START.search(value, start, end - 8) is not None
            previous_end = end
        for scheme in URL_SCHEME.finditer(value):
            start = scheme.end()
            if value.startswith("://", start):
                authority = URL_AUTHORITY.match(value, start + 3)
                if authority is not None:
                    item = authority[0]
                    colon = item.find(":", 1)
                    if colon != -1 and item.find("@", colon + 2) != -1:
                        return True
        return False

    return sensitive(text) or sensitive(text.encode("ascii", "backslashreplace").decode("ascii"))


def changed_files(base: str, head: str) -> tuple[list[str], list[tuple[str, str]]]:
    names = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", f"{base}...{head}"],
        cwd=REPO, check=True, stdout=subprocess.PIPE,
    ).stdout.decode("utf-8", "surrogateescape")
    files = [name for name in names.split("\0") if name.endswith(".md")]
    patch = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-renames", "--unified=0", f"{base}...{head}", "--", "*.md"],
        cwd=REPO, check=True, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
    ).stdout
    added: list[tuple[str, str]] = []
    current = ""
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            added.append((current, line[1:]))
    return files, added


def slug(heading: str) -> str:
    heading = re.sub(r"`([^`]*)`", r"\1", heading).lower()
    heading = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE)
    return re.sub(r"\s", "-", heading.strip())


def anchors(path: Path) -> set[str]:
    result: set[str] = set()
    counts: dict[str, int] = {}
    fence: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = FENCE.match(line)
        if match:
            marker = match[1][0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence:
            continue
        result.update(HTML_ID.findall(line))
        heading = HEADING.match(line)
        if heading:
            base = slug(heading[1])
            count = counts.get(base, 0)
            counts[base] = count + 1
            result.add(base if count == 0 else f"{base}-{count}")
    return result


def markdown_lines(path: Path) -> list[str]:
    lines, fence = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = FENCE.match(line)
        if match:
            marker = match[1][0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return lines


def check_frontmatter(repo_path: str, source: str) -> list[str]:
    if not re.fullmatch(r"skills/[^/]+/SKILL\.md", repo_path):
        return []
    lines = source.splitlines()
    if not lines or lines[0].strip() != "---":
        return ["missing YAML frontmatter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return ["unterminated YAML frontmatter"]
    values = {}
    for line in lines[1:end]:
        match = re.match(r"^(name|description)\s*:\s*(.*?)\s*$", line)
        if match:
            values[match[1]] = match[2].strip(" '\"\t")
    return [f"frontmatter {key} must be non-empty" for key in ("name", "description") if not values.get(key)]


def check(base: str, head: str) -> list[str]:
    files, added = changed_files(base, head)
    findings: list[str] = []
    for name, line in added:
        if name and emitted_sensitive(line):
            findings.append(f"{name}: added line contains sensitive-content indicators")
    for name in files:
        path = REPO / name
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        findings.extend(f"{name}: {item}" for item in check_frontmatter(name, source))
        for line in markdown_lines(path):
            for match in LINK.finditer(line):
                target = unquote(match[1] or match[2])
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or target.startswith("/"):
                    continue
                link_path = unquote(parsed.path)
                destination = (path.parent / link_path).resolve() if link_path else path.resolve()
                if not destination.is_file():
                    findings.append(f"{name}: broken relative link {target}")
                elif parsed.fragment and parsed.fragment not in anchors(destination):
                    findings.append(f"{name}: missing anchor {parsed.fragment} in {link_path or name}")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        findings = check(args.base, args.head)
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        print(f"docs-check: could not inspect diff or Markdown ({type(exc).__name__})")
        return 1
    for finding in findings:
        print(finding)
    print(f"docs-check: {len(findings)} finding(s)")
    return int(bool(findings))


if __name__ == "__main__":
    sys.exit(main())
