"""Owner trust configuration commands."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
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


def _legacy_sources():
    """Resolve expected values from the pre-trust implementation constants."""
    from . import config, review_bundle, review_origins, task_completion

    lifecycle_path = Path(__file__).resolve().parents[1] / "scripts" / "d6_worktree_lifecycle.py"
    spec = importlib.util.spec_from_file_location("_llmtool_d6_legacy", lifecycle_path)
    if spec is None or spec.loader is None:
        raise TrustError("TRUST_LEGACY_UNAVAILABLE")
    try:
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        repo = config.legacy_repository_config(home=str(_owner_home()))
        retain_match = re.search(r"\(\?:([0-9|]+)\)", lifecycle.OWNER_APPROVAL_RE.pattern)
        retain_issues = tuple(int(value) for value in retain_match.group(1).split("|")) if retain_match else ()
        if len(retain_issues) != 2:
            raise ValueError
    except Exception:
        raise TrustError("TRUST_LEGACY_UNAVAILABLE")
    return {
        "canonical_root": str(review_bundle.LEGACY_PROTECTED[0]),
        "workspace_root": str(review_bundle.LEGACY_PROTECTED[1]),
        "tasks_root": str(lifecycle.TASKS_ROOT),
        "slug": repo["slug"],
        "allowed_origins": tuple(sorted(review_origins.REVIEW_ORIGINS)),
        "worker_dispatch_issue": repo["dispatch_issue"],
        "worker_dispatch_evidence_pr": repo["dispatch_evidence_pr"],
        "retain_release_issues": retain_issues,
        "workflows": (
            (task_completion._CI_WORKFLOW_PATH, task_completion._CI_WORKFLOW_NAME,
             tuple(sorted(task_completion._CI_RUNNER_NAMES)), (), task_completion._MAC_CHECK, None),
            (task_completion._LINUX_CI_WORKFLOW_PATH, task_completion._LINUX_CI_WORKFLOW_NAME,
             tuple(sorted(task_completion._LINUX_CI_RUNNER_NAMES)), (), task_completion._LINUX_CHECK,
             task_completion._DOCS_CHECK),
        ),
        "not_run_checks": tuple(task_completion._NOT_RUN_CHECKS),
        "reviewer_host_policy_issue": 120,
    }


def add_trust_parser(sub):
    parser = sub.add_parser("trust", help="Initialize or inspect owner-only trust configuration")
    actions = parser.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="Create the private trust.json for this checkout")
    init.add_argument("--input", help="JSON draft containing approval_refs and ci_trust")
    init.add_argument("--import-config", action="store_true")
    init.add_argument("--replace", action="store_true", help="replace trust.json after confirming its current digest")
    init.add_argument("--confirm-rotation", metavar="OLD_SHA256")
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
    # Inherited GIT_* values could point Git at a different checkout than the real one.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(["git", *args], cwd=cwd, check=True, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8")
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        raise TrustError("TRUST_INIT_REPOSITORY_INVALID") from None
    return result.stdout.strip()


def _checkout_top():
    return Path(_git("rev-parse", "--show-toplevel")).resolve(strict=True)


def _checkout_values(home):
    top = _checkout_top()
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


def _atomic_replace(path, raw):
    """Replace one file with complete owner-only bytes, or leave it untouched."""
    temporary = path.parent / (".trust.json." + secrets.token_hex(12))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        raise TrustError("TRUST_INIT_WRITE_FAILED") from None
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _import_config_refs(home):
    """Import only the two repository dispatch references from config.yaml."""
    config_path = home / ".config" / "llmtool" / "config.yaml"
    try:
        import yaml
        with config_path.open("r", encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
        repository = value.get("repository") if type(value) is dict else None
        if type(repository) is not dict:
            raise ValueError
        issue = repository.get("dispatch_issue")
        evidence_pr = repository.get("dispatch_evidence_pr")
        if (type(issue) is not int or not 1 <= issue <= 10**9 or
                type(evidence_pr) is not int or not 1 <= evidence_pr <= 10**9):
            raise ValueError
        return issue, evidence_pr
    except Exception:
        raise TrustError("TRUST_INIT_INPUT_INVALID") from None


def _binding_sha256(document):
    from .trust import _binding
    return _binding(document)


def _affected_active_records(root, new_binding):
    """Count active D6 registrations under root whose trust binding would rotate."""
    try:
        common = Path(_git("-C", root, "rev-parse", "--path-format=absolute",
                           "--git-common-dir", cwd=root))
        registry_dir = common / "llmtool-worktrees"
    except (OSError, TrustError):
        raise TrustError("TRUST_REGISTRY_UNAVAILABLE") from None
    try:
        directory_info = registry_dir.lstat()
    except FileNotFoundError:
        return 0
    except OSError:
        raise TrustError("TRUST_REGISTRY_UNAVAILABLE") from None
    try:
        if (not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != os.geteuid() or
                stat.S_IMODE(directory_info.st_mode) != 0o700):
            raise TrustError("TRUST_REGISTRY_INVALID")
        paths = tuple(registry_dir.glob("*.json"))
    except TrustError:
        raise
    except OSError:
        raise TrustError("TRUST_REGISTRY_UNAVAILABLE") from None
    # Records are read in full up to 1 MiB (#1093). A record above 64 KiB is
    # parsed like any other: an ACTIVE, REVIEW_PENDING or REPAIR one is counted
    # by the rule below and any other state is ignored. Larger, unparsable or
    # unsafe records stay TRUST_REGISTRY_INVALID.
    max_record_bytes = 1024 * 1024
    count = 0
    for registry in paths:
        descriptor = None
        try:
            descriptor = os.open(registry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                                 getattr(os, "O_CLOEXEC", 0))
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
                    not 1 <= info.st_size <= max_record_bytes):
                raise TrustError("TRUST_REGISTRY_INVALID")
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = None
                raw = stream.read(max_record_bytes + 1)
                after = os.fstat(stream.fileno())
            current = registry.lstat()
            identity = lambda item: (item.st_dev, item.st_ino, item.st_size,
                                     item.st_mtime_ns, item.st_ctime_ns)
            if (len(raw) != info.st_size or len(raw) > max_record_bytes or
                    identity(info) != identity(after) or identity(info) != identity(current)):
                raise TrustError("TRUST_REGISTRY_INVALID")
            record = json.loads(raw.decode("utf-8"))
            if type(record) is not dict or type(record.get("state")) is not str:
                raise ValueError
        except TrustError:
            raise
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            raise TrustError("TRUST_REGISTRY_INVALID") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if (record["state"] in {"ACTIVE", "REVIEW_PENDING", "REPAIR"} and
                (record.get("trust_sha256") is None or
                 record.get("trust_binding_sha256") is None or
                 record.get("trust_binding_sha256") != new_binding)):
            count += 1
    return count


def _init(args):
    if os.name != "posix" or sys.platform.startswith("win"):
        raise TrustError("TRUST_UNSUPPORTED_PLATFORM")
    if os.getuid() != os.geteuid() or os.geteuid() == 0:
        raise TrustError("TRUST_IDENTITY_INVALID")
    home = _owner_home()
    target = home / ".config" / "llmtool" / "trust.json"
    replacing = bool(getattr(args, "replace", False))
    confirm = getattr(args, "confirm_rotation", None)
    previous_raw = None
    if replacing:
        if not confirm:
            raise TrustError("TRUST_ROTATION_UNCONFIRMED")
        try:
            current = load_trust(home=str(home))
        except TrustError:
            raise TrustError("TRUST_ROTATION_UNCONFIRMED") from None
        if not re.fullmatch(r"[0-9a-f]{64}", confirm) or current.sha256 != confirm:
            raise TrustError("TRUST_ROTATION_UNCONFIRMED")
        try:
            previous_raw = target.read_bytes()
        except OSError:
            raise TrustError("TRUST_ROTATION_UNCONFIRMED") from None
        if hashlib.sha256(previous_raw).hexdigest() != current.sha256:
            raise TrustError("TRUST_ROTATION_UNCONFIRMED")
        # Only the trusted checkout may rotate trust; refuse before any draft read or write.
        if str(_checkout_top()) != current.canonical_root:
            raise TrustError("TRUST_ROTATION_ROOT_UNTRUSTED")
    elif confirm is not None:
        raise TrustError("TRUST_ROTATION_UNCONFIRMED")
    if not replacing and (target.exists() or target.is_symlink()):
        raise TrustError("TRUST_EXISTS")
    inferred = _checkout_values(home)
    if not args.input:
        raise TrustError("TRUST_INIT_INPUT_INVALID")
    draft = _read_draft(args.input)

    legacy = _legacy_sources()
    workflows = [{"path": item[0], "name": item[1], "runner_names": list(item[2]),
                  "runner_labels": None, "code_check": item[4], "docs_check": item[5]}
                 for item in legacy["workflows"]]
    approval_refs = {
        "reviewer_host_policy_issue": legacy["reviewer_host_policy_issue"],
        "worker_dispatch_issue": legacy["worker_dispatch_issue"],
        "worker_dispatch_evidence_pr": legacy["worker_dispatch_evidence_pr"],
        "retain_release_issues": list(legacy["retain_release_issues"]),
    }

    document = {
        "schema_version": 1,
        "canonical_root": inferred["canonical_root"],
        "workspace_root": inferred["workspace_root"],
        "tasks_root": inferred["tasks_root"],
        "repository": {**inferred["repository"], "approval_refs": approval_refs},
        "ci_trust": {"mode": "self-hosted", "github_actions_app_id": 15368,
                     "workflows": workflows, "not_run_checks": list(legacy["not_run_checks"])},
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
        issue, evidence_pr = _import_config_refs(home)
        document["repository"]["approval_refs"]["worker_dispatch_issue"] = issue
        document["repository"]["approval_refs"]["worker_dispatch_evidence_pr"] = evidence_pr
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
    raw = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")
    affected = (_affected_active_records(current.canonical_root, _binding_sha256(document))
                if replacing else 0)
    if replacing:
        _atomic_replace(target, raw)
    else:
        _atomic_create(target, raw)
    try:
        loaded = load_trust(home=str(home))
        verify_repository(loaded)
    except TrustError:
        if replacing and previous_raw is not None:
            try:
                _atomic_replace(target, previous_raw)
            except TrustError:
                raise TrustError("TRUST_INIT_ROLLBACK_FAILED") from None
        else:
            try:
                target.unlink()
            except OSError:
                pass
        raise
    print("trust_sha256: " + loaded.sha256)
    if replacing:
        print(f"affected_active_records: {affected}")
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
        "workspace_root": (trust.workspace_root
                           if Path(trust.tasks_root).parent == Path(trust.workspace_root)
                           else None),
        "canonical_root": trust.canonical_root,
        "allowed_origins": tuple(sorted(repository["allowed_origins"])),
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
    legacy = _legacy_sources()
    expected = {key: legacy[key] for key in fields}
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
