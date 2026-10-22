#!/usr/bin/env python3
"""Cache-backed, per-worktree developer environment bootstrap for llmtool.

A11c ENV-1 (Issue #267). Every registered Developer worktree keeps its own venv,
its own source tree, its own branch and Git ownership. What is reused is only the
*verified wheel cache* (a read-only wheelhouse keyed by platform, Python ABI and
the dependency lock hash) plus pip's own download cache when the wheelhouse is
first populated. Nothing writable is shared: no venv, no site-packages, no
interpreter state, no PATH entry, no symlink into another worktree.

Commands
--------
    python scripts/worktree_env.py fingerprint [--worktree DIR] [--extras tui]
    python scripts/worktree_env.py bootstrap   [--worktree DIR] [--extras tui]
                                              [--python EXE] [--cache-root DIR]
                                              [--offline] [--recreate]
                                              [--rebuild-cache] [--no-project]
                                              [--wheel-source DIR] [--json]
    python scripts/worktree_env.py verify      [--worktree DIR] [--json]
    python scripts/worktree_env.py cache-info  [--cache-root DIR] [--json]

Design notes
------------
* Fingerprint inputs (all hashed together): cache schema, builder version,
  host platform, CPU architecture, `sysconfig` platform tag, Python
  implementation + version + SOABI, selected extras, and the exact bytes of
  `tools/llmtool/worktree-env.lock`.
* The wheelhouse lives outside every worktree at
  `~/Library/Caches/llmtool/worktree-env` (macOS) or
  `$XDG_CACHE_HOME/llmtool/worktree-env` (other POSIX). Entries are built once
  into a private temp directory, hashed into `manifest.json`, then published
  atomically and made read-only (0555/0444). Readers verify every wheel against
  the manifest before installing, so a corrupted or tampered cache fails closed
  instead of being used or silently repaired.
* Installs never reach the network when a verified entry exists:
  `pip install --no-index --find-links <wheelhouse>`.
* The project itself is installed from the *current* worktree with
  `--no-deps --no-build-isolation`, and `verify` re-checks that `llmtool`
  resolves inside that worktree, that pip's `direct_url.json` points at it, that
  the venv is inside the worktree, and that installed versions equal the lock.
  A venv copied from another worktree is therefore rejected.

Exit codes: 0 ok, 2 offline cache miss, 3 integrity/provenance failure,
4 invalid usage or layout, 5 subprocess/pip failure.
"""

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

SCHEMA = 1
BUILDER = "worktree_env/1"
LOCK_RELATIVE = Path("tools/llmtool/worktree-env.lock")
PROJECT_RELATIVE = Path("tools/llmtool")
VENV_RELATIVE = Path("tools/llmtool/.venv")
MARKER_NAME = ".worktree-env.json"
REQUIRED_GROUPS = ("base", "build")
OPTIONAL_GROUPS = ("tui",)
PIP_TIMEOUT_S = 900
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class EnvError(Exception):
    """Bootstrap failure with a stable status string and an exit code."""

    def __init__(self, status, message, exit_code=3):
        super().__init__(message)
        self.status = status
        self.exit_code = exit_code


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def normalize_name(name):
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def write_json_atomic(path, payload, mode=0o600):
    directory = path.parent
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ensure_dir(path, mode=0o700):
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    if path.is_symlink():
        raise EnvError("UNSAFE_PATH", "refusing symlinked directory: %s" % path, 3)


def _assert_no_symlink_components(root):
    """Reject a worktree whose project path is a symlink.

    `Path.resolve()` would otherwise make `tools/llmtool` resolve outside the
    worktree, silently defeating the venv containment check and allowing
    `--recreate` to remove a directory that is not part of this worktree.
    """

    for relative in (Path("tools"), Path("tools") / "llmtool"):
        candidate = Path(root) / relative
        if candidate.is_symlink():
            raise EnvError(
                "UNSAFE_PATH",
                "refusing symlinked worktree component: %s" % candidate,
                3,
            )


def _resolve_worktree(worktree):
    root = Path(worktree or ".").expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root)
    try:
        real = root.resolve(strict=True)
    except OSError as exc:
        raise EnvError("INVALID_WORKTREE", "worktree does not exist: %s (%s)" % (root, exc), 4)
    _assert_no_symlink_components(real)
    if not (real / PROJECT_RELATIVE / "pyproject.toml").is_file():
        raise EnvError(
            "INVALID_WORKTREE",
            "%s is not an llmtool worktree (missing tools/llmtool/pyproject.toml)" % real,
            4,
        )
    if not (real / LOCK_RELATIVE).is_file():
        raise EnvError(
            "MISSING_LOCK",
            "missing %s; the dependency lock is required for a reproducible cache key"
            % (real / LOCK_RELATIVE),
            4,
        )
    return real


def _resolve_cache_root(cache_root, worktree):
    if cache_root:
        root = Path(cache_root).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
    else:
        root = Path(default_cache_root())
    root = Path(os.path.abspath(str(root)))
    real_root = Path(os.path.realpath(str(root)))
    real_wt = Path(os.path.realpath(str(worktree)))
    if real_root == real_wt or real_wt in real_root.parents:
        raise EnvError(
            "CACHE_INSIDE_WORKTREE",
            "cache root %s is inside worktree %s; the cache must stay outside the source tree"
            % (real_root, real_wt),
            4,
        )
    created = not real_root.exists()
    _ensure_dir(real_root)
    if created:
        try:
            os.chmod(str(real_root), 0o700)
        except OSError:
            pass
    return real_root


def default_cache_root():
    if sys.platform == "darwin":
        return str(Path.home() / "Library" / "Caches" / "llmtool" / "worktree-env")
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(base) / "llmtool" / "worktree-env")


# --------------------------------------------------------------------------
# lock file
# --------------------------------------------------------------------------


def parse_lock(path):
    """Parse the tiny INI-like lock file. Deterministic and dependency free."""

    sections = {}
    current = None
    data = path.read_bytes()
    text = data.decode("utf-8")
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            if current in sections:
                raise EnvError("INVALID_LOCK", "duplicate section [%s]" % current, 4)
            sections[current] = {}
            continue
        if "=" not in line:
            raise EnvError("INVALID_LOCK", "line %d is not key = value: %r" % (lineno, raw), 4)
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "schema":
            if value != str(SCHEMA):
                raise EnvError("INVALID_LOCK", "unsupported lock schema %r" % value, 4)
            continue
        if current is None:
            raise EnvError("INVALID_LOCK", "entry outside a section on line %d" % lineno, 4)
        name = normalize_name(key)
        version = value.strip().strip('"').strip("'")
        if not _SAFE_NAME.match(name) or not _SAFE_VERSION.match(version):
            raise EnvError("INVALID_LOCK", "unsafe pinned entry on line %d: %r" % (lineno, raw), 4)
        if name in sections[current]:
            raise EnvError("INVALID_LOCK", "duplicate %s in [%s]" % (name, current), 4)
        sections[current][name] = version

    for group in REQUIRED_GROUPS:
        if not sections.get(group):
            if group == "base":
                raise EnvError("INVALID_LOCK", "missing or empty required section [%s]" % group, 4)
            sections[group] = {}
    seen = {}
    for group, entries in sections.items():
        if group not in REQUIRED_GROUPS + OPTIONAL_GROUPS:
            raise EnvError("INVALID_LOCK", "unknown section [%s]" % group, 4)
        for name in entries:
            if name in seen:
                raise EnvError(
                    "INVALID_LOCK",
                    "package %s appears in both [%s] and [%s]; "
                    "the same distribution cannot be installed twice" % (name, seen[name], group),
                    4,
                )
            seen[name] = group
    return sections


def selected_requirements(sections, extras):
    groups = list(REQUIRED_GROUPS)
    for extra in extras:
        if extra not in OPTIONAL_GROUPS:
            raise EnvError("INVALID_EXTRAS", "unknown extra %r" % extra, 4)
        groups.append(extra)
    reqs = []
    for group in groups:
        for name, version in sorted(sections[group].items()):
            reqs.append({"name": name, "version": version, "group": group})
    return reqs


def requirement_lines(requirements):
    return "".join("%s==%s\n" % (item["name"], item["version"]) for item in requirements)


# --------------------------------------------------------------------------
# interpreter + fingerprint
# --------------------------------------------------------------------------

_INTERPRETER_PROBE = r"""
import json, sys, sysconfig, platform
print("@@" + json.dumps({
    "implementation": platform.python_implementation(),
    "version": ".".join(str(part) for part in sys.version_info[:3]),
    "abi": sysconfig.get_config_var("SOABI") or "",
    "platform": sys.platform,
    "platform_tag": sysconfig.get_platform(),
    "machine": platform.machine(),
    "executable": sys.executable,
}))
"""


def interpreter_info(python_executable):
    cmd = [python_executable, "-I", "-c", _INTERPRETER_PROBE]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvError(
            "INTERPRETER_UNAVAILABLE", "cannot run %s: %s" % (python_executable, exc), 4
        )
    for line in proc.stdout.splitlines():
        if line.startswith("@@"):
            info = json.loads(line[2:])
            break
    else:
        raise EnvError(
            "INTERPRETER_UNAVAILABLE",
            "%s did not report interpreter info: %s"
            % (python_executable, (proc.stderr or "").strip()[-500:]),
            4,
        )
    info["platform"] = info.get("platform") or sys.platform
    return info


def default_python_executable():
    """Return a python3 that can actually be executed.

    `sys.executable` is not always runnable: Homebrew-based interpreters report
    the `opt` symlink path, and an interpreter whose mode lost the execute bit
    cannot be used to create a venv. Candidates are only accepted after a real
    `-I -c pass` execution, otherwise the bootstrap would fail halfway.
    """

    candidates = []
    base_executable = getattr(sys, "_base_executable", None)
    if base_executable:
        candidates.append(base_executable)
    candidates.append(os.path.join(sys.base_prefix, "bin", "python3"))
    candidates.extend(
        [
            sys.executable,
            os.path.realpath(sys.executable) if sys.executable else "",
            shutil.which("python3") or "",
            "/usr/bin/python3",
        ]
    )
    seen = set()
    unique = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    candidates = unique
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            continue
        try:
            proc = subprocess.run(
                [candidate, "-I", "-c", "pass"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return candidate
    raise EnvError(
        "INTERPRETER_UNAVAILABLE",
        "no runnable python3 interpreter found (tried %s)" % [c for c in candidates if c],
        4,
    )


def _normalize_machine(machine):
    value = (machine or "").strip().lower()
    aliases = {"amd64": "x86_64", "aarch64": "arm64", "arm64e": "arm64"}
    return aliases.get(value, value)


def fingerprint_inputs(lock_path, extras, interp):
    lock_bytes = Path(lock_path).read_bytes()
    inputs = {
        "schema": SCHEMA,
        "builder": BUILDER,
        "lock_sha256": sha256_bytes(lock_bytes),
        "extras": sorted(extras),
        "platform": interp.get("platform") or sys.platform,
        "platform_tag": interp.get("platform_tag") or sysconfig.get_platform(),
        "machine": _normalize_machine(interp.get("machine")),
        "implementation": (interp.get("implementation") or "CPython").lower(),
        "python_version": interp.get("version") or "%d.%d.%d" % sys.version_info[:3],
        "abi": interp.get("abi") or (sysconfig.get_config_var("SOABI") or ""),
    }
    return inputs


def fingerprint_id(inputs):
    digest = sha256_bytes(canonical_json(inputs).encode("utf-8"))
    major_minor = ".".join(inputs["python_version"].split(".")[:2])
    return "%s%s-%s-%s-%s" % (
        inputs["implementation"],
        major_minor,
        inputs["platform"],
        inputs["machine"] or "unknown",
        digest[:12],
    )


# --------------------------------------------------------------------------
# wheelhouse cache
# --------------------------------------------------------------------------


def wheelhouse_path(cache_root, fp):
    return Path(cache_root) / "wheels" / fp


def _parse_wheel_filename(filename):
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return normalize_name(parts[0]), parts[1]


def _walk_without_symlinks(path):
    """Yield descendants of `path` without following symlinks."""

    for root, dirs, files in os.walk(str(path), followlinks=False):
        for name in sorted(dirs) + sorted(files):
            yield Path(root) / name


def _chmod_files_readonly(path):
    for entry in _walk_without_symlinks(path):
        if entry.is_symlink() or not entry.is_file():
            continue
        os.chmod(str(entry), 0o444)


def _make_tree_writable(path):
    """Make a tree removable without ever chmod-ing through a symlink.

    A venv contains `bin/python` symlinks to the base interpreter; following them
    here would change the mode of the shared interpreter for every user, so
    symlinks are skipped (lstat based) and only regular files/dirs are touched.
    """

    try:
        if not Path(path).is_symlink():
            os.chmod(str(path), 0o755)
    except OSError:
        pass
    for entry in _walk_without_symlinks(path):
        if entry.is_symlink():
            continue
        try:
            os.chmod(str(entry), 0o755 if entry.is_dir() else 0o644)
        except OSError:
            pass


def remove_cache_entry(cache_root, fp):
    wheels = Path(cache_root) / "wheels"
    target = wheels / fp
    if target.is_symlink():
        raise EnvError("UNSAFE_PATH", "refusing to remove symlinked cache entry %s" % target, 3)
    if not target.exists():
        return False
    if Path(os.path.realpath(str(target))).parent != Path(os.path.realpath(str(wheels))):
        raise EnvError("UNSAFE_PATH", "refusing to remove out-of-cache path %s" % target, 3)
    _make_tree_writable(target)
    shutil.rmtree(str(target))
    return True


def verify_wheelhouse(path, fp, requirements):
    path = Path(path)
    manifest_path = path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EnvError("CACHE_CORRUPT", "cache entry %s has no manifest" % path, 3)
    except (OSError, ValueError) as exc:
        raise EnvError("CACHE_CORRUPT", "unreadable manifest in %s: %s" % (path, exc), 3)

    if manifest.get("schema") != SCHEMA:
        raise EnvError("CACHE_CORRUPT", "manifest schema mismatch in %s" % path, 3)
    if manifest.get("fingerprint") != fp:
        raise EnvError(
            "CACHE_CORRUPT",
            "manifest fingerprint %r does not match requested %r"
            % (manifest.get("fingerprint"), fp),
            3,
        )

    files = manifest.get("files") or []
    seen = {}
    for item in files:
        name = item.get("file") if isinstance(item, dict) else None
        if not isinstance(name, str) or "/" in name or "\\" in name or name in (".", ".."):
            raise EnvError("CACHE_CORRUPT", "unsafe file name in manifest: %r" % (name,), 3)
        entry = path / name
        if entry.is_symlink():
            raise EnvError("CACHE_CORRUPT", "cache entry file is a symlink: %s" % entry, 3)
        if not entry.is_file():
            raise EnvError("CACHE_CORRUPT", "cache entry file is missing: %s" % entry, 3)
        size = entry.stat().st_size
        if size != item.get("size"):
            raise EnvError(
                "CACHE_CORRUPT", "size mismatch for %s (%s != %s)" % (name, size, item.get("size")), 3
            )
        digest = sha256_file(entry)
        if digest != item.get("sha256"):
            raise EnvError("CACHE_CORRUPT", "sha256 mismatch for %s" % name, 3)
        seen[item.get("name")] = item

    expected = {item["name"]: item["version"] for item in requirements}
    if set(seen) != set(expected):
        raise EnvError(
            "CACHE_CORRUPT",
            "cache covers %s but lock requires %s"
            % (sorted(seen), sorted(expected)),
            3,
        )
    for name, version in expected.items():
        if seen[name].get("version") != version:
            raise EnvError(
                "CACHE_CORRUPT",
                "cached version of %s is %s, lock requires %s"
                % (name, seen[name].get("version"), version),
                3,
            )

    manifest_files = sorted(item["file"] for item in files)
    listing = sorted(p.name for p in path.iterdir() if p.name != "manifest.json")
    if listing != manifest_files:
        raise EnvError(
            "CACHE_CORRUPT",
            "cache entry content does not match the manifest (%s vs %s)"
            % (listing, manifest_files),
            3,
        )
    _verify_lock_closure(path, files, expected)
    return manifest


def _wheel_requirements(wheel_path):
    """Read `Requires-Dist` names from a wheel's METADATA (stdlib only)."""

    import zipfile

    names = []
    with zipfile.ZipFile(str(wheel_path)) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.count("/") == 1 and name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise EnvError(
                "CACHE_CORRUPT",
                "wheel %s does not contain exactly one METADATA file" % wheel_path,
                3,
            )
        for line in archive.read(metadata_names[0]).decode("utf-8", "replace").splitlines():
            if not line.lower().startswith("requires-dist:"):
                continue
            value = line.split(":", 1)[1].strip()
            if "extra ==" in value or "extra==" in value:
                continue
            requirement = re.split(r"[;\[(<>=!~ ]", value, maxsplit=1)[0].strip()
            if requirement:
                names.append(normalize_name(requirement))
    return names


def _verify_lock_closure(path, files, expected):
    """Fail closed when a wheel needs a package the lock does not pin.

    `pip download --no-deps` only downloads what the lock names, so a lock that
    is not transitively closed would otherwise fail late inside pip (exit 5)
    instead of failing with an explicit lock error.
    """

    providers = {item["name"]: path / item["file"] for item in files}
    missing = {}
    for name, wheel in providers.items():
        for requirement in _wheel_requirements(wheel):
            if requirement not in expected:
                missing.setdefault(requirement, []).append(name)
    if missing:
        raise EnvError(
            "LOCK_NOT_CLOSED",
            "the lock is not transitively closed; missing pins: %s"
            % {dep: sorted(users) for dep, users in sorted(missing.items())},
            3,
        )


def _build_manifest(path, fp, inputs, requirements, wheels):
    files = []
    for name, wheel in sorted(wheels.items()):
        info = _parse_wheel_filename(wheel.name)
        if info is None:
            raise EnvError("INVALID_WHEEL", "unparsable wheel file name %s" % wheel.name, 3)
        dist, version = info
        files.append(
            {
                "name": dist,
                "version": version,
                "file": wheel.name,
                "sha256": sha256_file(wheel),
                "size": wheel.stat().st_size,
            }
        )
    return {
        "schema": SCHEMA,
        "builder": BUILDER,
        "fingerprint": fp,
        "fingerprint_inputs": inputs,
        "requirements": [
            {"name": item["name"], "version": item["version"], "group": item["group"]}
            for item in requirements
        ],
        "files": files,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _collect_wheels(requirements, wheel_source):
    """Return {normalized_name: Path} covering exactly `requirements`."""
    source = Path(wheel_source)
    if not source.is_dir():
        raise EnvError("INVALID_WHEEL_SOURCE", "wheel source is not a directory: %s" % source, 4)
    found = {}
    for entry in sorted(source.iterdir()):
        if entry.is_symlink() or not entry.is_file() or not entry.name.endswith(".whl"):
            continue
        info = _parse_wheel_filename(entry.name)
        if info is None:
            raise EnvError("INVALID_WHEEL", "unparsable wheel file name %s" % entry.name, 3)
        dist, version = info
        if dist in found:
            raise EnvError(
                "INVALID_WHEEL_SOURCE", "multiple wheels for %s in %s" % (dist, source), 3
            )
        found[dist] = (version, entry)

    expected = {item["name"]: item["version"] for item in requirements}
    missing = sorted(set(expected) - set(found))
    if missing:
        raise EnvError(
            "WHEELS_INCOMPLETE", "wheel source is missing %s" % missing, 3
        )
    mismatched = [
        (name, found[name][0], expected[name])
        for name in expected
        if found[name][0] != expected[name]
    ]
    if mismatched:
        raise EnvError(
            "WHEEL_VERSION_MISMATCH",
            "wheel versions do not match the lock: %s" % mismatched,
            3,
        )
    return {name: found[name][1] for name in expected}


def _download_wheels(python_executable, requirements, destination):
    req_path = Path(destination) / "requirements.txt"
    req_path.write_text(requirement_lines(requirements), encoding="utf-8")
    cmd = [
        python_executable,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "--only-binary=:all:",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--dest",
        str(destination),
        "-r",
        str(req_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvError("PIP_DOWNLOAD_FAILED", "pip download could not run: %s" % exc, 5)
    if proc.returncode != 0:
        raise EnvError(
            "PIP_DOWNLOAD_FAILED",
            "pip download failed (%d): %s"
            % (proc.returncode, (proc.stderr or proc.stdout or "").strip()[-2000:]),
            5,
        )
    req_path.unlink()
    wheels = {}
    for entry in sorted(Path(destination).iterdir()):
        info = _parse_wheel_filename(entry.name)
        if info is None:
            continue
        dist, version = info
        if dist in wheels:
            raise EnvError("PIP_DOWNLOAD_AMBIGUOUS", "multiple wheels for %s" % dist, 5)
        wheels[dist] = (version, entry)
    return {name: wheels[name][1] for name in wheels}


def _reap_stale_staging(tmp_root, max_age_s=24 * 3600):
    """Remove abandoned staging directories from crashed builds (bounded)."""

    now = time.time()
    try:
        entries = sorted(Path(tmp_root).iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if now - entry.stat().st_mtime < max_age_s:
                continue
            _make_tree_writable(entry)
            shutil.rmtree(str(entry), ignore_errors=True)
        except OSError:
            continue


def ensure_wheelhouse(
    cache_root,
    fp,
    inputs,
    requirements,
    python_executable,
    wheel_source=None,
    offline=False,
    rebuild=False,
):
    """Return (wheelhouse path, "hit"|"miss"). Never returns a corrupt entry."""

    if not re.fullmatch(r"[A-Za-z0-9._-]+", fp or ""):
        raise EnvError("INVALID_FINGERPRINT", "unsafe fingerprint value %r" % (fp,), 3)
    final = wheelhouse_path(cache_root, fp)
    if final.is_symlink():
        raise EnvError("CACHE_CORRUPT", "cache entry is a symlink: %s" % final, 3)
    if not rebuild and final.exists():
        verify_wheelhouse(final, fp, requirements)
        return final, "hit"

    tmp_root = Path(cache_root) / "tmp"
    _ensure_dir(tmp_root)
    _ensure_dir(Path(cache_root) / "wheels")
    lock_path = Path(cache_root) / ".lock"
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _reap_stale_staging(tmp_root)
        if rebuild and final.exists():
            remove_cache_entry(cache_root, fp)
        if final.exists():
            verify_wheelhouse(final, fp, requirements)
            return final, "hit"
        if offline and not wheel_source:
            raise EnvError(
                "CACHE_MISS",
                "no verified cache entry for %s and --offline was requested; "
                "run once online or seed with --wheel-source" % fp,
                2,
            )
        staging = Path(tempfile.mkdtemp(dir=str(tmp_root), prefix=fp + "-"))
        try:
            staging.chmod(0o700)
            if wheel_source:
                wheels = _collect_wheels(requirements, wheel_source)
                for wheel in wheels.values():
                    shutil.copyfile(str(wheel), str(staging / wheel.name))
            else:
                wheels = _download_wheels(python_executable, requirements, staging)
            schema_mismatch = sorted(
                name
                for name, wheel in wheels.items()
                if name not in {item["name"] for item in requirements}
            )
            if schema_mismatch:
                raise EnvError(
                    "WHEELS_INCOMPLETE",
                    "downloaded packages outside the lock: %s" % schema_mismatch,
                    3,
                )
            missing = sorted({item["name"] for item in requirements} - set(wheels))
            if missing:
                raise EnvError(
                    "WHEELS_INCOMPLETE", "no wheel was obtained for %s" % missing, 3
                )
            manifest = _build_manifest(staging, fp, inputs, requirements, wheels)
            write_json_atomic(staging / "manifest.json", manifest, mode=0o444)
            verify_wheelhouse(staging, fp, requirements)
            _chmod_files_readonly(staging)
            try:
                os.replace(str(staging), str(final))
            except OSError as exc:
                if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
                    verify_wheelhouse(final, fp, requirements)
                    return final, "hit"
                raise
            os.chmod(str(final), 0o555)
            return final, "miss"
        except BaseException:
            if staging.exists():
                _make_tree_writable(staging)
                shutil.rmtree(str(staging), ignore_errors=True)
            raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


# --------------------------------------------------------------------------
# venv + install + verification
# --------------------------------------------------------------------------


def venv_dir(worktree):
    return Path(worktree) / VENV_RELATIVE


def venv_python(venv):
    return str(Path(venv) / "bin" / "python")


def _assert_venv_within_worktree(venv, worktree):
    venv = Path(venv)
    if venv.is_symlink():
        raise EnvError("UNSAFE_VENV", "venv path is a symlink: %s" % venv, 3)
    real_venv = Path(os.path.realpath(str(venv)))
    real_wt = Path(os.path.realpath(str(worktree)))
    if real_venv.parent != (real_wt / "tools" / "llmtool").resolve():
        raise EnvError(
            "UNSAFE_VENV",
            "venv %s does not resolve to <worktree>/tools/llmtool/.venv" % venv,
            3,
        )
    if not (real_venv / "pyvenv.cfg").is_file():
        raise EnvError("INVALID_VENV", "venv is missing pyvenv.cfg: %s" % venv, 4)


def create_venv(worktree, python_executable, recreate=False):
    venv = venv_dir(worktree)
    if venv.exists() or venv.is_symlink():
        if not recreate:
            raise EnvError(
                "VENV_EXISTS", "venv already exists; verify it or pass --recreate" , 4
            )
        _assert_venv_within_worktree(venv, worktree)
        _make_tree_writable(venv)
        shutil.rmtree(str(venv))
    cmd = [python_executable, "-m", "venv", str(venv)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvError("VENV_FAILED", "venv creation could not run: %s" % exc, 5)
    if proc.returncode != 0:
        raise EnvError(
            "VENV_FAILED",
            "venv creation failed (%d): %s"
            % (proc.returncode, (proc.stderr or proc.stdout or "").strip()[-2000:]),
            5,
        )
    _assert_venv_within_worktree(venv, worktree)
    return venv


def install_requirements(python_executable, wheelhouse, requirements):
    req_path = Path(tempfile.mkdtemp(prefix="llmtool-env-req-")) / "requirements.txt"
    req_path.write_text(requirement_lines(requirements), encoding="utf-8")
    cmd = [
        python_executable,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(wheelhouse),
        "--only-binary=:all:",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "-r",
        str(req_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvError("PIP_INSTALL_FAILED", "dependency install could not run: %s" % exc, 5)
    finally:
        shutil.rmtree(str(req_path.parent), ignore_errors=True)
    if proc.returncode != 0:
        raise EnvError(
            "PIP_INSTALL_FAILED",
            "dependency install failed (%d): %s"
            % (proc.returncode, (proc.stderr or proc.stdout or "").strip()[-2000:]),
            5,
        )


PEP660_MIN_PIP = (21, 3)


def pip_version(python_executable):
    """Return the venv pip version as an int tuple (fail closed if unknown)."""

    try:
        proc = subprocess.run(
            [python_executable, "-I", "-m", "pip", "--version", "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvError("PIP_UNAVAILABLE", "cannot run pip in the venv: %s" % exc, 5)
    match = re.match(r"^pip (\d+)\.(\d+)", proc.stdout or "")
    if proc.returncode != 0 or not match:
        raise EnvError(
            "PIP_UNAVAILABLE",
            "venv pip did not report a version: %s" % (proc.stderr or proc.stdout or "").strip()[-500:],
            5,
        )
    return (int(match.group(1)), int(match.group(2)))


def require_editable_capable_pip(python_executable):
    """Editable installs of a pyproject-only project need PEP 660 (pip >= 21.3).

    Some interpreters (for example Apple CommandLineTools CPython 3.9.6) bundle
    pip 21.2.4. The repository lock therefore pins a PEP 660 capable pip in its
    [build] group, which the locked dependency install upgrades offline from the
    verified wheelhouse. A venv whose pip is still too old fails closed here
    instead of silently falling back to a non-editable snapshot install.
    """

    version = pip_version(python_executable)
    if version < PEP660_MIN_PIP:
        raise EnvError(
            "PIP_TOO_OLD",
            "venv pip %d.%d cannot perform an editable (PEP 660) install; pin "
            "pip >= %d.%d in the [build] group of worktree-env.lock"
            % (version + PEP660_MIN_PIP),
            4,
        )
    return version


def install_project(python_executable, worktree, wheelhouse):
    """Install the *current* worktree project editable into this worktree's venv.

    The install is editable (PEP 660, `--no-build-isolation` with the locked
    setuptools from the wheelhouse), so `llmtool` imports from this worktree's
    own source tree and pip records `direct_url.json` with `dir_info.editable`
    pointing at it; `verify` checks both. No snapshot copy of the source is
    placed in site-packages, so the venv can never silently run stale code.
    """

    require_editable_capable_pip(python_executable)
    project = Path(worktree) / PROJECT_RELATIVE
    cmd = [
        python_executable,
        "-m",
        "pip",
        "install",
        "--editable",
        str(project),
        "--no-deps",
        "--no-index",
        "--find-links",
        str(wheelhouse),
        "--only-binary=:all:",
        "--no-build-isolation",
        "--no-cache-dir",
        "--disable-pip-version-check",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvError("PIP_INSTALL_FAILED", "project install could not run: %s" % exc, 5)
    if proc.returncode != 0:
        raise EnvError(
            "PIP_INSTALL_FAILED",
            "worktree project install failed (%d): %s"
            % (proc.returncode, (proc.stderr or proc.stdout or "").strip()[-2000:]),
            5,
        )


def write_marker(venv, marker):
    write_json_atomic(Path(venv) / MARKER_NAME, marker, mode=0o600)


def read_marker(venv):
    path = Path(venv) / MARKER_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EnvError(
            "MARKER_MISSING",
            "venv has no %s; it was not created by scripts/worktree_env.py" % MARKER_NAME,
            3,
        )
    except (OSError, ValueError) as exc:
        raise EnvError("MARKER_CORRUPT", "unreadable %s: %s" % (path, exc), 3)


_ENV_PROBE = r"""
import json, os
import importlib.metadata as metadata
result = {"versions": {}, "llmtool_file": None, "direct_url": None,
          "installed_init": None, "error": None}
for name in %s:
    try:
        result["versions"][name] = metadata.version(name)
    except Exception:
        result["versions"][name] = None
try:
    dist = metadata.distribution("llmtool")
    result["direct_url"] = dist.read_text("direct_url.json")
    result["installed_init"] = os.path.realpath(str(dist.locate_file("llmtool/__init__.py")))
except Exception as exc:
    result["error"] = "llmtool distribution missing: %%s" %% exc
try:
    import llmtool
    result["llmtool_file"] = os.path.realpath(llmtool.__file__)
except Exception as exc:
    result["import_error"] = str(exc)
print("@@" + json.dumps(result))
"""


def _probe_venv(python_executable, names):
    script = _ENV_PROBE % (json.dumps(sorted(names)),)
    neutral = tempfile.mkdtemp(prefix="llmtool-env-probe-")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONHOME")
    }
    env["PYTHONNOUSERSITE"] = "1"
    try:
        try:
            proc = subprocess.run(
                [python_executable, "-I", "-c", script],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=neutral,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise EnvError("PROBE_FAILED", "cannot run venv python: %s" % exc, 3)
    finally:
        shutil.rmtree(neutral, ignore_errors=True)
    for line in proc.stdout.splitlines():
        if line.startswith("@@"):
            return json.loads(line[2:])
    raise EnvError(
        "PROBE_FAILED",
        "could not probe venv %s: %s"
        % (python_executable, (proc.stderr or proc.stdout or "").strip()[-1000:]),
        3,
    )


def verify_env(worktree, lock_path, extras, expect_project=None):
    worktree = Path(worktree)
    venv = venv_dir(worktree)
    if not venv.exists():
        raise EnvError("VENV_MISSING", "no venv at %s" % venv, 3)
    _assert_venv_within_worktree(venv, worktree)

    interp = interpreter_info(venv_python(venv))
    inputs = fingerprint_inputs(lock_path, extras, interp)
    fp = fingerprint_id(inputs)
    marker = read_marker(venv)
    if marker.get("fingerprint") != fp:
        raise EnvError(
            "ENV_DRIFT",
            "venv fingerprint %r does not match required %r (dependency/python/platform change); "
            "re-run bootstrap with --recreate" % (marker.get("fingerprint"), fp),
            3,
        )
    recorded = marker.get("worktree_root")
    if not isinstance(recorded, str) or not recorded or not Path(recorded).is_absolute():
        raise EnvError(
            "FOREIGN_VENV",
            "marker does not record an absolute worktree_root; refusing to trust this venv",
            3,
        )
    if Path(os.path.realpath(recorded)) != Path(os.path.realpath(str(worktree))):
        raise EnvError(
            "FOREIGN_VENV",
            "venv was created for %r, not %s" % (recorded, worktree),
            3,
        )

    sections = parse_lock(Path(lock_path))
    requirements = selected_requirements(sections, extras)
    probe = _probe_venv(venv_python(venv), [item["name"] for item in requirements])
    bad = [
        (item["name"], probe["versions"].get(item["name"]), item["version"])
        for item in requirements
        if probe["versions"].get(item["name"]) != item["version"]
    ]
    if bad:
        raise EnvError("ENV_DRIFT", "installed versions do not match the lock: %s" % bad, 3)

    project_expected = marker.get("project") if expect_project is None else expect_project
    if project_expected:
        if probe.get("error"):
            raise EnvError("PROJECT_MISSING", probe["error"], 3)
        if probe.get("import_error"):
            raise EnvError(
                "PROJECT_IMPORT_FAILED", "llmtool does not import: %s" % probe["import_error"], 3
            )
        source_root = Path(os.path.realpath(str(worktree / PROJECT_RELATIVE)))
        expected_init = Path(os.path.realpath(str(source_root / "llmtool" / "__init__.py")))
        imported = Path(probe.get("llmtool_file") or "")
        if imported != expected_init:
            raise EnvError(
                "FOREIGN_SOURCE",
                "llmtool resolves to %s, not this worktree's source (%s); "
                "the install is not editable or belongs to another worktree"
                % (imported, expected_init),
                3,
            )
        direct = probe.get("direct_url")
        if not direct:
            raise EnvError(
                "PROVENANCE_MISSING",
                "llmtool install has no direct_url.json; source provenance is unverifiable",
                3,
            )
        try:
            record = json.loads(direct)
        except ValueError as exc:
            raise EnvError("PROVENANCE_MISSING", "unreadable direct_url.json: %s" % exc, 3)
        if not (record.get("dir_info") or {}).get("editable"):
            raise EnvError(
                "PROVENANCE_MISSING",
                "llmtool must be installed editable from this worktree (direct_url.json dir_info.editable)",
                3,
            )
        url = record.get("url") or ""
        try:
            from urllib.parse import unquote, urlparse

            parsed = urlparse(url)
            recorded_path = Path(os.path.realpath(unquote(parsed.path)))
        except Exception:
            raise EnvError("PROVENANCE_MISSING", "unparsable direct_url %r" % url, 3)
        if parsed.scheme != "file" or recorded_path != source_root:
            raise EnvError(
                "FOREIGN_SOURCE",
                "llmtool was installed from %s, not %s" % (recorded_path, source_root),
                3,
            )
    return {
        "fingerprint": fp,
        "python": interp["version"],
        "abi": interp["abi"],
        "platform": interp["platform"],
        "machine": _normalize_machine(interp["machine"]),
        "venv": str(venv),
        "project": bool(project_expected),
        "packages": {item["name"]: item["version"] for item in requirements},
    }


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_fingerprint(args):
    worktree = _resolve_worktree(args.worktree)
    sections = parse_lock(worktree / LOCK_RELATIVE)
    interp = interpreter_info(args.python or default_python_executable())
    inputs = fingerprint_inputs(worktree / LOCK_RELATIVE, args.extras, interp)
    return {
        "fingerprint": fingerprint_id(inputs),
        "inputs": inputs,
        "requirements": selected_requirements(sections, args.extras),
    }


def worktree_lock(worktree):
    """Serialise bootstrap runs for one worktree (venv phase is not reentrant)."""

    path = Path(worktree) / PROJECT_RELATIVE / ".worktree-env.lock"
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    return fd


def cmd_bootstrap(args):
    started = time.monotonic()
    timings = {}
    worktree = _resolve_worktree(args.worktree)
    lock_path = worktree / LOCK_RELATIVE
    sections = parse_lock(lock_path)
    requirements = selected_requirements(sections, args.extras)
    python_executable = args.python or default_python_executable()
    interp = interpreter_info(python_executable)
    inputs = fingerprint_inputs(lock_path, args.extras, interp)
    fp = fingerprint_id(inputs)
    cache_root = _resolve_cache_root(args.cache_root, worktree)
    lock_fd = worktree_lock(worktree)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return _bootstrap_locked(
            args, started, timings, worktree, lock_path, requirements,
            python_executable, inputs, fp, cache_root,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _bootstrap_locked(
    args, started, timings, worktree, lock_path, requirements,
    python_executable, inputs, fp, cache_root,
):
    venv = venv_dir(worktree)
    if venv.exists() and not args.recreate:
        phase = time.monotonic()
        report = verify_env(worktree, lock_path, args.extras)
        timings["verify"] = round(time.monotonic() - phase, 3)
        report.update(
            {
                "status": "env_reused",
                "cache": str(wheelhouse_path(cache_root, fp)),
                "cache_status": "unused",
                "timings": timings,
                "total_s": round(time.monotonic() - started, 3),
            }
        )
        return report

    phase = time.monotonic()
    wheelhouse, cache_status = ensure_wheelhouse(
        cache_root,
        fp,
        inputs,
        requirements,
        python_executable,
        wheel_source=args.wheel_source,
        offline=args.offline,
        rebuild=args.rebuild_cache,
    )
    timings["cache"] = round(time.monotonic() - phase, 3)

    phase = time.monotonic()
    venv = create_venv(worktree, python_executable, recreate=args.recreate)
    timings["venv"] = round(time.monotonic() - phase, 3)

    try:
        return _install_into_new_venv(
            args, started, timings, worktree, lock_path, requirements, venv,
            wheelhouse, cache_status, inputs, fp,
        )
    except BaseException:
        # Never leave a half-built venv that would block the next bootstrap with
        # MARKER_MISSING; the cache entry stays valid and is reused on retry.
        if venv.exists() and not venv.is_symlink():
            _make_tree_writable(venv)
            shutil.rmtree(str(venv), ignore_errors=True)
        raise


def _install_into_new_venv(
    args, started, timings, worktree, lock_path, requirements, venv,
    wheelhouse, cache_status, inputs, fp,
):
    venv_inputs = fingerprint_inputs(
        lock_path, args.extras, interpreter_info(venv_python(venv))
    )
    if fingerprint_id(venv_inputs) != fp:
        raise EnvError(
            "INTERPRETER_MISMATCH",
            "venv interpreter does not match the computed fingerprint; "
            "refusing to install a cache built for another interpreter",
            3,
        )

    phase = time.monotonic()
    install_requirements(venv_python(venv), wheelhouse, requirements)
    timings["deps"] = round(time.monotonic() - phase, 3)

    if not args.no_project:
        phase = time.monotonic()
        install_project(venv_python(venv), worktree, wheelhouse)
        timings["project"] = round(time.monotonic() - phase, 3)

    marker = {
        "schema": SCHEMA,
        "builder": BUILDER,
        "fingerprint": fp,
        "fingerprint_inputs": inputs,
        "worktree_root": str(Path(os.path.realpath(str(worktree)))),
        "project": not args.no_project,
        "extras": sorted(args.extras),
        "lock_sha256": inputs["lock_sha256"],
        "wheelhouse": str(wheelhouse),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_marker(venv, marker)

    phase = time.monotonic()
    report = verify_env(worktree, lock_path, args.extras, expect_project=not args.no_project)
    timings["verify"] = round(time.monotonic() - phase, 3)
    report.update(
        {
            "status": "env_created",
            "cache": str(wheelhouse),
            "cache_status": cache_status,
            "timings": timings,
            "total_s": round(time.monotonic() - started, 3),
        }
    )
    return report


def cmd_verify(args):
    worktree = _resolve_worktree(args.worktree)
    report = verify_env(
        worktree,
        worktree / LOCK_RELATIVE,
        args.extras,
        expect_project=None if args.project is None else args.project,
    )
    report["status"] = "verified"
    return report


def cmd_cache_info(args):
    worktree = _resolve_worktree(args.worktree)
    cache_root = _resolve_cache_root(args.cache_root, worktree)
    wheels = Path(cache_root) / "wheels"
    entries = []
    if wheels.is_dir():
        for entry in sorted(wheels.iterdir()):
            if not entry.is_dir() or entry.is_symlink():
                continue
            size = sum(
                item.stat().st_size for item in entry.rglob("*") if item.is_file() and not item.is_symlink()
            )
            try:
                manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
                packages = {
                    item["name"]: item["version"] for item in manifest.get("files", [])
                }
            except (OSError, ValueError):
                packages = None
            entries.append({"fingerprint": entry.name, "bytes": size, "packages": packages})
    return {"cache_root": str(cache_root), "entries": entries}


class _ArgumentParser(argparse.ArgumentParser):
    """Usage errors exit 4, like every other layout/usage failure."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        raise SystemExit(4)


def build_parser():
    parser = _ArgumentParser(
        prog="worktree_env.py",
        description="Deterministic cache-backed developer environment bootstrap (Issue #267 ENV-1).",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(target, project_flag=False):
        target.add_argument("--worktree", default=".", help="task worktree root (default: cwd)")
        target.add_argument(
            "--extras",
            action="append",
            default=[],
            choices=sorted(OPTIONAL_GROUPS),
            help="optional lock group to install (repeatable)",
        )
        target.add_argument("--python", default=None, help="interpreter used to create the venv")
        if project_flag:
            target.add_argument(
                "--project", dest="project", action="store_true", default=None,
                help="require the worktree project install",
            )
            target.add_argument(
                "--no-project", dest="project", action="store_false",
                help="do not require the worktree project install",
            )
        target.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )

    fp_parser = sub.add_parser("fingerprint", help="print the environment fingerprint")
    common(fp_parser)

    boot = sub.add_parser("bootstrap", help="create or verify the per-worktree venv")
    common(boot)
    boot.add_argument("--cache-root", default=None, help="wheel cache root (default: platform cache dir)")
    boot.add_argument("--offline", action="store_true", help="never download; fail on cache miss")
    boot.add_argument("--recreate", action="store_true", help="delete and rebuild the worktree venv")
    boot.add_argument("--rebuild-cache", action="store_true", help="discard this fingerprint's cache entry")
    boot.add_argument(
        "--wheel-source", default=None,
        help="seed the cache from a directory of wheels pinned by the lock "
        "(hashes are recorded from those files, not compared to the lock)",
    )
    boot.add_argument(
        "--no-project", dest="no_project", action="store_true",
        help="install locked dependencies only (no llmtool project install)",
    )

    verify = sub.add_parser("verify", help="verify venv integrity, provenance and isolation")
    common(verify, project_flag=True)

    info = sub.add_parser("cache-info", help="list cache entries for this platform")
    common(info)
    info.add_argument("--cache-root", default=None)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "fingerprint": cmd_fingerprint,
        "bootstrap": cmd_bootstrap,
        "verify": cmd_verify,
        "cache-info": cmd_cache_info,
    }
    try:
        report = handlers[args.command](args)
    except EnvError as exc:
        sys.stderr.write("ERROR %s: %s\n" % (exc.status, exc))
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "code": exc.status, "message": str(exc)}))
        return exc.exit_code
    except Exception as exc:  # never leak a traceback as exit code 1
        sys.stderr.write("ERROR INTERNAL: %s: %s\n" % (type(exc).__name__, exc))
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "code": "INTERNAL", "message": str(exc)}))
        return 3
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "fingerprint":
        print(report["fingerprint"])
    elif args.command == "cache-info":
        print(report["cache_root"])
        for entry in report["entries"]:
            print("  %s  %.1f MiB  %s" % (entry["fingerprint"], entry["bytes"] / 1048576.0, entry["packages"]))
    else:
        line = "%s %s python=%s" % (report["status"], report["fingerprint"], report["python"])
        if "cache_status" in report:
            line += " cache=%s" % report["cache_status"]
        print(line)
        if report.get("cache"):
            print("  cache: %s" % report["cache"])
        if report.get("venv"):
            print("  venv: %s" % report["venv"])
        for key in ("cache", "venv", "deps", "project", "verify"):
            if key in report.get("timings", {}):
                print("  %-7s %ss" % (key + ":", report["timings"][key]))
        if "total_s" in report:
            print("  total: %ss" % report["total_s"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
