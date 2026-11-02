"""Owner trust configuration commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys

from .trust import TrustError, load_trust, verify_repository


_SLUG = "davidcatcat/davidcattoolsproject"
_ORIGINS = (f"git@github.com:{_SLUG}.git", f"https://github.com/{_SLUG}.git")
_WORKFLOWS = (
    {"path": ".github/workflows/llmtool-mac-ci.yml", "name": "llmtool Mac CI",
     "runner_names": ["davidproject-self-action", "davidproject-self-action-2"],
     "runner_labels": None, "code_check": "check (macOS 3.13)", "docs_check": None},
    {"path": ".github/workflows/llmtool-linux-ci.yml", "name": "llmtool Linux CI",
     "runner_names": ["ec2-self-runner", "ec2-self-runner-2", "ec2-self-runner-3"],
     "runner_labels": None, "code_check": "check (Linux 3.14)", "docs_check": "check (docs)"},
)
_APPROVAL_REFS = {
    "reviewer_host_policy_issue": 120,
    "worker_dispatch_issue": 289,
    "worker_dispatch_evidence_pr": 283,
    "retain_release_issues": [289, 352],
}
_NOT_RUN_CHECKS = ["Linux Python 3.9", "Windows CI"]


def add_trust_parser(sub):
    parser = sub.add_parser("trust", help="Initialize or inspect owner-only trust configuration")
    actions = parser.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="Create the private trust.json for this checkout")
    init.add_argument("--input", help="JSON draft containing approval_refs and ci_trust")
    init.add_argument("--import-config", action="store_true")
    show = actions.add_parser("show", help="Show sanitized trust configuration")
    show.add_argument("--reveal-paths", action="store_true")
    actions.add_parser("verify-legacy", help="Compare trust fields with legacy repository values")


def _owner_home():
    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError):
        raise TrustError("TRUST_HOME_INVALID") from None
    if (not os.path.isabs(home) or os.path.normpath(home) != home or
            os.path.realpath(home) != home):
        raise TrustError("TRUST_HOME_INVALID")
    return Path(home)


def _git(*args, cwd=None):
    try:
        result = subprocess.run(["git", *args], cwd=cwd, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8")
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        raise TrustError("TRUST_INIT_REPOSITORY_INVALID") from None
    return result.stdout.strip()


def _checkout_values(home):
    top = Path(_git("rev-parse", "--show-toplevel")).resolve(strict=True)
    common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=top)).resolve(strict=True)
    git_dir = Path(_git("rev-parse", "--path-format=absolute", "--absolute-git-dir", cwd=top)).resolve(strict=True)
    if common != top / ".git" or git_dir != common:
        raise TrustError("TRUST_INIT_REPOSITORY_INVALID")
    origin = _git("remote", "get-url", "origin", cwd=top)
    ssh = re.fullmatch(r"git@github\.com:([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100})\.git", origin)
    https = re.fullmatch(r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100})\.git", origin)
    match = ssh or https
    if match is None or match.group(1).split("/", 1)[1] in (".", ".."):
        raise TrustError("TRUST_INIT_REPOSITORY_INVALID")
    slug = match.group(1)
    repo_name = slug.split("/", 1)[1]
    workspace = home / ".local" / "share" / "llmtool-dev-workspaces" / repo_name
    return {
        "canonical_root": str(top),
        "workspace_root": str(workspace),
        "tasks_root": str(workspace / "tasks"),
        "repository": {"slug": slug,
                       "allowed_origins": [f"git@github.com:{slug}.git",
                                           f"https://github.com/{slug}.git"]},
    }


def _read_draft(filename):
    try:
        path = Path(filename)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
            raise ValueError
        raw = path.read_bytes()
        if not 1 <= len(raw) <= 16384:
            raise ValueError
        value = json.loads(raw.decode("utf-8"))
        if type(value) is not dict:
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        raise TrustError("TRUST_INIT_INPUT_INVALID") from None


def _create_private_directory(path):
    try:
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
    except FileExistsError:
        try:
            info = path.lstat()
        except OSError:
            raise TrustError("TRUST_DIRECTORY_NOT_PRIVATE") from None
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) != 0o700):
            raise TrustError("TRUST_DIRECTORY_NOT_PRIVATE")
    except OSError:
        raise TrustError("TRUST_DIRECTORY_NOT_PRIVATE") from None


def _atomic_create(path, raw):
    parent = path.parent
    temporary = parent / (".trust.json." + secrets.token_hex(12))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        raise TrustError("TRUST_EXISTS") from None
    except OSError:
        raise TrustError("TRUST_INIT_WRITE_FAILED") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except OSError:
            pass


def _init(args):
    if os.name != "posix" or sys.platform.startswith("win"):
        raise TrustError("TRUST_UNSUPPORTED_PLATFORM")
    if os.getuid() != os.geteuid() or os.geteuid() == 0:
        raise TrustError("TRUST_IDENTITY_INVALID")
    home = _owner_home()
    target = home / ".config" / "llmtool" / "trust.json"
    if target.exists() or target.is_symlink():
        raise TrustError("TRUST_EXISTS")
    inferred = _checkout_values(home)
    if args.input:
        draft = _read_draft(args.input)
    elif sys.stdin.isatty():
        raise TrustError("TRUST_INIT_INPUT_INVALID")
    else:
        raise TrustError("TRUST_INIT_INPUT_INVALID")

    document = {
        "schema_version": 1,
        "canonical_root": inferred["canonical_root"],
        "workspace_root": inferred["workspace_root"],
        "tasks_root": inferred["tasks_root"],
        "repository": {**inferred["repository"], "approval_refs": _APPROVAL_REFS},
        "ci_trust": {"mode": "self-hosted", "github_actions_app_id": 15368,
                     "workflows": list(_WORKFLOWS), "not_run_checks": _NOT_RUN_CHECKS},
        "tools": {"git": None, "gh_path": None, "lsof": None},
    }
    # A draft may supply unresolved fields, but cannot override checkout-derived identity.
    for key in ("canonical_root", "workspace_root", "tasks_root"):
        if key in draft and draft[key] != inferred[key]:
            raise TrustError("TRUST_INIT_INPUT_INVALID")
    if "repository" in draft:
        supplied = draft["repository"]
        if type(supplied) is not dict:
            raise TrustError("TRUST_INIT_INPUT_INVALID")
        for key, value in inferred["repository"].items():
            if key in supplied and supplied[key] != value:
                raise TrustError("TRUST_INIT_INPUT_INVALID")
        if "approval_refs" in supplied:
            document["repository"]["approval_refs"] = supplied["approval_refs"]
    if "ci_trust" in draft:
        document["ci_trust"] = draft["ci_trust"]
    if "tools" in draft:
        document["tools"] = draft["tools"]
    if args.import_config:
        raise TrustError("TRUST_INIT_INPUT_INVALID")
    # Validate the complete document before touching the destination.
    from .trust import _validate_schema
    _validate_schema(document)
    config_dir = target.parent
    config_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _create_private_directory(config_dir)
    # Build missing workspace roots privately; existing directories are validated by load_trust.
    for root in (Path(inferred["workspace_root"]), Path(inferred["tasks_root"])):
        if not root.exists():
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(root, 0o700)
    _atomic_create(target, (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    try:
        loaded = load_trust(home=str(home))
        verify_repository(loaded)
    except TrustError:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    print("trust_sha256: " + loaded.sha256)
    return 0


def _show(args):
    try:
        trust = load_trust()
    except TrustError as exc:
        print(f"STOP: trust rejected ({exc.code})")
        return 1
    workflows = []
    for workflow in trust.ci_trust["workflows"]:
        workflows.append({
            "path": workflow["path"] if args.reveal_paths else "OK",
            "name": workflow["name"],
            "code_check": workflow["code_check"],
            "docs_check": workflow["docs_check"],
            "runner_count": len(workflow["runner_names"] or workflow["runner_labels"]),
        })
    output = {
        "schema_version": trust.schema_version,
        "trust_sha256": trust.sha256,
        "trust_binding_sha256": trust.binding_sha256,
        "ci_trust": {"mode": trust.ci_trust["mode"], "workflows": workflows},
    }
    if args.reveal_paths:
        output["paths"] = {"canonical_root": trust.canonical_root,
                           "workspace_root": trust.workspace_root, "tasks_root": trust.tasks_root}
    else:
        output["paths"] = {"canonical_root": "OK", "workspace_root": "OK", "tasks_root": "OK"}
    print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0


def _legacy_fields(trust):
    repository = trust.repository
    ci = trust.ci_trust
    refs = repository["approval_refs"]
    fields = {
        "tasks_root": trust.tasks_root,
        "canonical_root": trust.canonical_root,
        "allowed_origins": tuple(repository["allowed_origins"]),
        "slug": repository["slug"],
        "reviewer_host_policy_issue": refs["reviewer_host_policy_issue"],
        "worker_dispatch_issue": refs["worker_dispatch_issue"],
        "worker_dispatch_evidence_pr": refs["worker_dispatch_evidence_pr"],
        "retain_release_issues": tuple(refs["retain_release_issues"]),
        "workflows": tuple((w["path"], w["name"], tuple(w["runner_names"] or ()),
                             tuple(w["runner_labels"] or ()), w["code_check"], w["docs_check"])
                           for w in ci["workflows"]),
        "not_run_checks": tuple(ci["not_run_checks"]),
    }
    workspace_ok = (Path(trust.tasks_root).parent == Path(trust.workspace_root))
    fields["workspace_root"] = workspace_ok
    expected = {
        "tasks_root": str(_owner_home() / ".local/share/llmtool-dev-workspaces" /
                           Path(trust.canonical_root).name / "tasks"),
        "canonical_root": str(Path(_git("rev-parse", "--show-toplevel")).resolve()),
        "allowed_origins": _ORIGINS,
        "slug": _SLUG,
        **{**_APPROVAL_REFS, "retain_release_issues": tuple(_APPROVAL_REFS["retain_release_issues"])},
        "workflows": tuple((w["path"], w["name"], tuple(w["runner_names"]), (),
                             w["code_check"], w["docs_check"]) for w in _WORKFLOWS),
        "not_run_checks": tuple(_NOT_RUN_CHECKS),
    }
    expected["workspace_root"] = True
    return fields, expected


def _verify_legacy():
    try:
        trust = load_trust()
        fields, expected = _legacy_fields(trust)
    except TrustError as exc:
        print(f"STOP: trust rejected ({exc.code})")
        return 1
    except (OSError, ValueError):
        print("STOP: trust rejected (TRUST_LEGACY_UNAVAILABLE)")
        return 1
    matched = True
    for key in ("tasks_root", "canonical_root", "allowed_origins", "slug",
                "reviewer_host_policy_issue", "worker_dispatch_issue",
                "worker_dispatch_evidence_pr", "retain_release_issues", "workflows",
                "not_run_checks", "workspace_root"):
        ok = fields[key] == expected[key]
        matched = matched and ok
        print(f"{key}: {'MATCH' if ok else 'MISMATCH'}")
    return 0 if matched else 1


def run_trust(args):
    if args.action == "show":
        return _show(args)
    if args.action == "verify-legacy":
        return _verify_legacy()
    try:
        return _init(args)
    except TrustError as exc:
        print(f"STOP: trust init rejected ({exc.code})", file=sys.stderr)
        return 1
