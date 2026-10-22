"""ENV-1 tests for scripts/worktree_env.py (Issue #267).

All tests are hermetic: dependency wheels are tiny synthetic wheels generated in
a temporary directory, and the fake worktree project uses a stdlib-only PEP 517
backend, so no network access and no real third-party package is needed.

The tests cover the acceptance matrix requested by WP-OC-03:
cold-ish build, warm reuse, cache hit, cache miss, dependency fingerprint
mismatch, Python/platform fingerprint mismatch, multi-worktree source isolation
and cache corruption fail-closed.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))

import worktree_env as we  # noqa: E402


def _bundled_pip_supports_editable():
    """True when a fresh venv of the default interpreter gets pip >= 21.3.

    The synthetic wheel source cannot contain a real pip wheel, so tests that
    perform the editable (PEP 660) project install need the interpreter's own
    ensurepip pip to be capable. Apple CLT CPython 3.9.6 bundles pip 21.2.4; on
    such interpreters those tests are skipped (the PIP_TOO_OLD fail-closed path
    is covered separately), while CI interpreters run them.
    """

    try:
        python = we.default_python_executable()
        proc = subprocess.run(
            [python, "-I", "-c", "import ensurepip; print(ensurepip.version())"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        parts = proc.stdout.strip().split(".")
        return proc.returncode == 0 and tuple(int(x) for x in parts[:2]) >= we.PEP660_MIN_PIP
    except Exception:
        return False


needs_editable_pip = unittest.skipUnless(
    _bundled_pip_supports_editable(),
    "interpreter bundles pip < 21.3 (no PEP 660 editable install); covered in CI",
)


def _b64_digest(data):
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(destination, name, version, marker="pkg", requires=()):
    """Create a minimal, pip-installable pure-python wheel."""

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    distribution = name.replace("-", "_")
    package = name.replace("-", "_")
    dist_info = "%s-%s.dist-info" % (distribution, version)
    files = {
        "%s/__init__.py" % package: ("MARKER = %r\n" % marker).encode("utf-8"),
        dist_info + "/METADATA": (
            "Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (name, version)
            + "".join("Requires-Dist: %s\n" % item for item in requires)
        ).encode("utf-8"),
        dist_info + "/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode("utf-8"),
    }
    record = [
        "%s,sha256=%s,%d" % (arc, _b64_digest(data), len(data))
        for arc, data in files.items()
    ]
    record.append(dist_info + "/RECORD,,")
    files[dist_info + "/RECORD"] = ("\n".join(record) + "\n").encode("utf-8")
    target = destination / ("%s-%s-py3-none-any.whl" % (distribution, version))
    with zipfile.ZipFile(str(target), "w") as archive:
        for arc, data in files.items():
            archive.writestr(arc, data)
    return target


_BACKEND_SOURCE = r'''
import base64
import hashlib
import zipfile
from pathlib import Path


def _digest(data):
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _write_wheel(wheel_directory, editable):
    version = "0.1.0"
    distribution = "llmtool"
    package = Path("llmtool")
    dist_info = "%s-%s.dist-info" % (distribution, version)
    files = {}
    if not editable:
        for path in sorted(package.rglob("*.py")):
            files["%s/%s" % (distribution, path.relative_to(package).as_posix())] = path.read_bytes()
    files[dist_info + "/METADATA"] = (
        "Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (distribution, version)
    ).encode("utf-8")
    files[dist_info + "/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: fakebackend\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode("utf-8")
    if editable:
        files["llmtool_editable.pth"] = (str(Path.cwd()) + "\n").encode("utf-8")
    record = [
        "%s,sha256=%s,%d" % (name, _digest(data), len(data))
        for name, data in files.items()
    ]
    record.append(dist_info + "/RECORD,,")
    files[dist_info + "/RECORD"] = ("\n".join(record) + "\n").encode("utf-8")
    target = Path(wheel_directory) / ("%s-%s-py3-none-any.whl" % (distribution, version))
    with zipfile.ZipFile(str(target), "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return target.name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _write_wheel(wheel_directory, editable=False)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _write_wheel(wheel_directory, editable=True)


def get_requires_for_build_editable(config_settings=None):
    return []
'''

_PYPROJECT = """[build-system]
requires = []
build-backend = "fakebackend"
backend-path = ["."]

[project]
name = "llmtool"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [%s]
"""

_LOCK = """# test lock
schema = 1

[base]
tiny-dep = "1.0.0"
%s
"""


def make_worktree(root, marker, extra_base="", dependencies='"tiny-dep>=1.0"', broken=False):
    root = Path(root)
    project = root / "tools" / "llmtool"
    (project / "llmtool").mkdir(parents=True, exist_ok=True)
    (project / "llmtool" / "__init__.py").write_text("MARKER = %r\n" % marker, encoding="utf-8")
    (project / "pyproject.toml").write_text(_PYPROJECT % dependencies, encoding="utf-8")
    backend = _BACKEND_SOURCE
    if broken:
        backend = backend.replace(
            "def build_editable(wheel_directory, config_settings=None, metadata_directory=None):\n    return _write_wheel(wheel_directory, editable=True)",
            "def build_editable(wheel_directory, config_settings=None, metadata_directory=None):\n    raise RuntimeError('simulated backend failure')",
        )
    (project / "fakebackend.py").write_text(backend, encoding="utf-8")
    (project / "worktree-env.lock").write_text(_LOCK % extra_base, encoding="utf-8")
    return root


def _chmod_writable(path):
    try:
        os.chmod(str(path), 0o755)
    except OSError:
        pass
    for root, dirs, files in os.walk(str(path), followlinks=False):
        for name in dirs + files:
            entry = Path(root) / name
            if entry.is_symlink():
                continue
            try:
                os.chmod(str(entry), 0o755 if entry.is_dir() else 0o644)
            except OSError:
                pass


class EnvironmentTempCase(unittest.TestCase):
    """Shared temp workspace with one wheel source and a scratch cache root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="llmtool-env-test-"))
        self.wheels = self.tmp / "wheels"
        make_wheel(self.wheels, "tiny-dep", "1.0.0", marker="dep")
        make_wheel(self.wheels, "tiny-extra", "1.0.0", marker="extra")
        self.cache = self.tmp / "cache"

    def tearDown(self):
        _chmod_writable(self.tmp)
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = we.main([str(arg) for arg in args])
        return code, stdout.getvalue(), stderr.getvalue()

    def bootstrap(self, worktree, *extra):
        return self.run_cli(
            "bootstrap",
            "--worktree",
            worktree,
            "--cache-root",
            self.cache,
            "--wheel-source",
            self.wheels,
            "--json",
            *extra
        )

    def venv_python(self, worktree):
        return Path(worktree) / "tools" / "llmtool" / ".venv" / "bin" / "python"

    def seed_cache(self, worktree):
        """Populate the cache entry directly (no venv) for cache-only tests."""
        lock = Path(worktree) / "tools" / "llmtool" / "worktree-env.lock"
        python = we.default_python_executable()
        interp = we.interpreter_info(python)
        inputs = we.fingerprint_inputs(lock, [], interp)
        fingerprint = we.fingerprint_id(inputs)
        requirements = we.selected_requirements(we.parse_lock(lock), [])
        we.ensure_wheelhouse(
            self.cache, fingerprint, inputs, requirements, python, wheel_source=self.wheels
        )
        return fingerprint

    def dep_marker(self, worktree):
        proc = subprocess.run(
            [str(self.venv_python(worktree)), "-I", "-c", "import tiny_dep; print(tiny_dep.MARKER)"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(self.tmp),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def venv_report(self, worktree):
        script = (
            "import json, llmtool, tiny_dep, os\n"
            "print(json.dumps({'marker': llmtool.MARKER,"
            " 'file': os.path.realpath(llmtool.__file__),"
            " 'dep': os.path.realpath(tiny_dep.__file__)}))\n"
        )
        proc = subprocess.run(
            [str(self.venv_python(worktree)), "-I", "-c", script],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(self.tmp),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)


class FingerprintTest(unittest.TestCase):
    def _inputs(self, **overrides):
        base = {
            "platform": "darwin",
            "platform_tag": "macosx-10.13-x86_64",
            "machine": "AMD64",
            "implementation": "CPython",
            "version": "3.9.6",
            "abi": "cpython-39-darwin",
        }
        base.update(overrides)
        return base

    def test_fingerprint_is_deterministic_and_normalises_machine(self):
        lock = Path(tempfile.mkdtemp(prefix="llmtool-lock-")) / "lock"
        lock.write_text("schema = 1\n[base]\ntiny-dep = \"1.0.0\"\n", encoding="utf-8")
        try:
            first = we.fingerprint_inputs(lock, ["tui"], self._inputs())
            second = we.fingerprint_inputs(lock, ["tui"], self._inputs())
            self.assertEqual(first, second)
            self.assertEqual(first["machine"], "x86_64")
            self.assertEqual(we.fingerprint_id(first), we.fingerprint_id(second))
        finally:
            shutil.rmtree(str(lock.parent), ignore_errors=True)

    def test_fingerprint_changes_with_every_invalidating_input(self):
        lock = Path(tempfile.mkdtemp(prefix="llmtool-lock-")) / "lock"
        lock.write_text("schema = 1\n[base]\ntiny-dep = \"1.0.0\"\n", encoding="utf-8")
        try:
            baseline = we.fingerprint_id(we.fingerprint_inputs(lock, [], self._inputs()))
            variants = {
                "python": self._inputs(version="3.10.0"),
                "abi": self._inputs(abi="cpython-310-darwin"),
                "platform": self._inputs(platform="linux", platform_tag="linux-x86_64"),
                "machine": self._inputs(machine="arm64"),
                "platform_tag": self._inputs(platform_tag="macosx-14.0-x86_64"),
            }
            for label, interp in variants.items():
                with self.subTest(label):
                    candidate = we.fingerprint_id(we.fingerprint_inputs(lock, [], interp))
                    self.assertNotEqual(baseline, candidate)
            with self.subTest("extras"):
                candidate = we.fingerprint_id(we.fingerprint_inputs(lock, ["tui"], self._inputs()))
                self.assertNotEqual(baseline, candidate)
            lock.write_text(
                "schema = 1\n[base]\ntiny-dep = \"2.0.0\"\n", encoding="utf-8"
            )
            candidate = we.fingerprint_id(we.fingerprint_inputs(lock, [], self._inputs()))
            self.assertNotEqual(baseline, candidate)
        finally:
            shutil.rmtree(str(lock.parent), ignore_errors=True)


class LockTest(unittest.TestCase):
    def _parse(self, text):
        directory = Path(tempfile.mkdtemp(prefix="llmtool-lock-"))
        lock = directory / "worktree-env.lock"
        lock.write_text(text, encoding="utf-8")
        try:
            return we.parse_lock(lock)
        finally:
            shutil.rmtree(str(directory), ignore_errors=True)

    def test_parse_normalises_names_and_allows_empty_build(self):
        sections = self._parse(
            "schema = 1\n\n[base]\ntiny_dep = \"1.0.0\"\n\n[build]\n"
        )
        self.assertEqual(sections["base"], {"tiny-dep": "1.0.0"})
        self.assertEqual(sections["build"], {})

    def test_rejects_duplicate_package_across_groups(self):
        with self.assertRaises(we.EnvError) as caught:
            self._parse(
                "schema = 1\n[base]\ntiny-dep = \"1.0.0\"\n[build]\nTiny_Dep = \"1.0.0\"\n"
            )
        self.assertEqual(caught.exception.status, "INVALID_LOCK")

    def test_rejects_unpinned_or_invalid_entries(self):
        for text in (
            'schema = 1\n[base]\ntiny-dep = ">=1.0"\n',
            'schema = 1\n[base]\ntiny dep = "1.0.0"\n',
            'schema = 2\n[base]\ntiny-dep = "1.0.0"\n',
            'schema = 1\n[unknown]\ntiny-dep = "1.0.0"\n',
            'schema = 1\n[base]\n',
            'tiny-dep = "1.0.0"\n',
        ):
            with self.subTest(text):
                with self.assertRaises(we.EnvError) as caught:
                    self._parse(text)
                self.assertEqual(caught.exception.status, "INVALID_LOCK")

    def test_repository_lock_satisfies_pyproject_constraints(self):
        sections = we.parse_lock(TOOLS / "worktree-env.lock")
        pyproject = (TOOLS / "pyproject.toml").read_text(encoding="utf-8")
        declared = _declared_requirements(pyproject)
        self.assertTrue(declared["base"], "pyproject must declare runtime dependencies")
        for group, requirements in declared.items():
            for name, specifier in requirements.items():
                with self.subTest(name=name):
                    self.assertIn(name, sections[group], "lock is missing %s in [%s]" % (name, group))
                    self.assertTrue(
                        _satisfies(sections[group][name], specifier),
                        "lock pins %s==%s but pyproject requires %s"
                        % (name, sections[group][name], specifier),
                    )

    def test_repository_lock_pins_editable_capable_pip_and_setuptools(self):
        sections = we.parse_lock(TOOLS / "worktree-env.lock")
        self.assertIn("pip", sections["build"])
        self.assertGreaterEqual(_version_tuple(sections["build"]["pip"])[:2], we.PEP660_MIN_PIP)
        self.assertIn("setuptools", sections["build"])
        pyproject = (TOOLS / "pyproject.toml").read_text(encoding="utf-8")
        import re

        requires = re.search(r"^requires\s*=\s*\[(.*?)\]", pyproject, re.S | re.M).group(1)
        for raw in re.findall(r"\"([^\"]+)\"", requires):
            name, specifier = _split_requirement(raw)
            with self.subTest(name=name):
                self.assertIn(name, sections["build"])
                self.assertTrue(_satisfies(sections["build"][name], specifier))


def _declared_requirements(pyproject_text):
    """Extract `[project].dependencies` and optional dependency groups."""

    import re

    result = {"base": {}}
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", pyproject_text, re.S | re.M)
    if not match:
        return result
    for raw in re.findall(r"\"([^\"]+)\"", match.group(1)):
        name, specifier = _split_requirement(raw)
        result["base"][name] = specifier

    optional = re.search(
        r"^\[project\.optional-dependencies\]\s*(.*?)(?=^\[|\Z)", pyproject_text, re.S | re.M
    )
    if optional:
        for group, body in re.findall(r"^([A-Za-z0-9_.-]+)\s*=\s*\[(.*?)\]", optional.group(1), re.S | re.M):
            result[group] = {}
            for raw in re.findall(r"\"([^\"]+)\"", body):
                name, specifier = _split_requirement(raw)
                result[group][name] = specifier
    return result


def _split_requirement(raw):
    import re

    match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", raw.strip())
    if not match:
        raise AssertionError("unparsable requirement %r" % raw)
    return we.normalize_name(match.group(1)), match.group(2).strip()


def _version_tuple(version):
    parts = []
    for chunk in version.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits or 0))
    return tuple(parts)


def _satisfies(version, specifier):
    """Small PEP 440 subset: >=, >, <=, <, ==, comma-separated conjunctions."""

    if not specifier:
        return True
    for clause in (piece.strip() for piece in specifier.split(",")):
        if not clause:
            continue
        for operator in (">=", "<=", "==", ">", "<", "~="):
            if clause.startswith(operator):
                target = clause[len(operator):].strip()
                break
        else:
            raise AssertionError("unsupported specifier %r" % clause)
        left, right = _version_tuple(version), _version_tuple(target)
        if operator == ">=" and not left >= right:
            return False
        if operator == ">" and not left > right:
            return False
        if operator == "<=" and not left <= right:
            return False
        if operator == "<" and not left < right:
            return False
        if operator == "==" and not left == right:
            return False
        if operator == "~=":
            raise AssertionError("~= not supported by this test helper")
    return True


class CacheTest(EnvironmentTempCase):
    def test_cache_miss_offline_fails_closed_without_creating_a_venv(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        code, out, err = self.run_cli(
            "bootstrap",
            "--worktree",
            worktree,
            "--cache-root",
            self.cache,
            "--offline",
            "--json",
        )
        self.assertEqual(code, 2)
        self.assertIn("CACHE_MISS", err)
        self.assertFalse((worktree / "tools" / "llmtool" / ".venv").exists())

    @needs_editable_pip
    def test_cache_seeded_from_wheels_then_hit_and_reused(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 0, err)
        first = json.loads(out)
        self.assertEqual(first["status"], "env_created")
        self.assertEqual(first["cache_status"], "miss")

        report = self.venv_report(worktree)
        self.assertEqual(report["marker"], "A")
        self.assertTrue(report["file"].startswith(str(worktree.resolve())))

        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 0, err)
        second = json.loads(out)
        self.assertEqual(second["status"], "env_reused")

        base = os.path.realpath(we.default_python_executable())
        mode_before = stat.S_IMODE(os.stat(base).st_mode)
        code, out, err = self.bootstrap(worktree, "--recreate")
        self.assertEqual(code, 0, err)
        third = json.loads(out)
        self.assertEqual(third["status"], "env_created")
        self.assertEqual(third["cache_status"], "hit")
        self.assertEqual(
            mode_before,
            stat.S_IMODE(os.stat(base).st_mode),
            "venv recreation must never chmod through interpreter symlinks",
        )

        manifest = json.loads(
            (self.cache / "wheels" / third["fingerprint"] / "manifest.json").read_text()
        )
        self.assertEqual(
            {item["name"] for item in manifest["files"]}, {"tiny-dep"}
        )

    def test_corrupt_wheel_fails_closed_and_preserves_existing_venv(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree, "--no-project")
        self.assertEqual(code, 0, err)
        fingerprint = json.loads(out)["fingerprint"]
        entry = self.cache / "wheels" / fingerprint
        wheel = next(entry.glob("*.whl"))
        _chmod_writable(entry)
        data = bytearray(wheel.read_bytes())
        data[0:4] = b"PK\x03\x04"
        data[-8:] = b"tampered"
        wheel.write_bytes(bytes(data))

        code, out, err = self.bootstrap(worktree, "--recreate")
        self.assertEqual(code, 3)
        self.assertIn("CACHE_CORRUPT", err)
        self.assertTrue(self.venv_python(worktree).is_file(), "venv must survive a failed rebuild")
        self.assertEqual(self.dep_marker(worktree), "dep")
        self.assertTrue((worktree / "tools" / "llmtool" / ".venv" / "pyvenv.cfg").is_file())

    def test_missing_manifest_and_extra_wheel_fail_closed(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        fingerprint = self.seed_cache(worktree)
        entry = self.cache / "wheels" / fingerprint
        _chmod_writable(entry)

        make_wheel(entry, "tiny-evil", "1.0.0")
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 3)
        self.assertIn("CACHE_CORRUPT", err)

        (entry / "manifest.json").unlink()
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 3)
        self.assertIn("CACHE_CORRUPT", err)
        self.assertFalse((worktree / "tools" / "llmtool" / ".venv").exists())

    def test_wheel_source_must_match_lock_exactly(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        empty = self.tmp / "empty-wheels"
        empty.mkdir()
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache,
            "--wheel-source", empty, "--json",
        )
        self.assertEqual(code, 3)
        self.assertIn("WHEELS_INCOMPLETE", err)

        wrong = self.tmp / "wrong-wheels"
        make_wheel(wrong, "tiny-dep", "2.0.0")
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache,
            "--wheel-source", wrong, "--json",
        )
        self.assertEqual(code, 3)
        self.assertIn("WHEEL_VERSION_MISMATCH", err)
        self.assertFalse((worktree / "tools" / "llmtool" / ".venv").exists())

    @needs_editable_pip
    def test_dependency_fingerprint_mismatch_gets_its_own_cache_entry(self):
        first = make_worktree(self.tmp / "wt-a", "A")
        second = make_worktree(self.tmp / "wt-b", "B", extra_base='tiny-extra = "1.0.0"\n')

        code, out, err = self.bootstrap(first)
        self.assertEqual(code, 0, err)
        fp_a = json.loads(out)["fingerprint"]

        # The cache entry for A must not satisfy B (different dependency set).
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", second, "--cache-root", self.cache, "--offline", "--json"
        )
        self.assertEqual(code, 2)
        self.assertIn("CACHE_MISS", err)

        code, out, err = self.bootstrap(second)
        self.assertEqual(code, 0, err)
        fp_b = json.loads(out)["fingerprint"]
        self.assertNotEqual(fp_a, fp_b)
        self.assertTrue((self.cache / "wheels" / fp_a / "manifest.json").is_file())
        self.assertTrue((self.cache / "wheels" / fp_b / "manifest.json").is_file())

    def test_cache_root_inside_worktree_is_rejected(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        code, out, err = self.run_cli(
            "bootstrap",
            "--worktree",
            worktree,
            "--cache-root",
            worktree / "cache",
            "--wheel-source",
            self.wheels,
            "--json",
        )
        self.assertEqual(code, 4)
        self.assertIn("CACHE_INSIDE_WORKTREE", err)

    def test_cache_info_lists_entries(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        self.seed_cache(worktree)
        code, out, err = self.run_cli(
            "cache-info", "--worktree", worktree, "--cache-root", self.cache, "--json"
        )
        self.assertEqual(code, 0, err)
        info = json.loads(out)
        self.assertEqual(len(info["entries"]), 1)
        self.assertEqual(info["entries"][0]["packages"], {"tiny-dep": "1.0.0"})


    def test_lock_must_be_transitively_closed(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        wheels = self.tmp / "unclosed-wheels"
        make_wheel(wheels, "tiny-dep", "1.0.0", requires=["ghost-dep>=1.0"])
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache,
            "--wheel-source", wheels, "--json",
        )
        self.assertEqual(code, 3)
        self.assertIn("LOCK_NOT_CLOSED", err)
        self.assertFalse((worktree / "tools" / "llmtool" / ".venv").exists())

    def test_stray_file_in_cache_entry_is_rejected(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        fingerprint = self.seed_cache(worktree)
        entry = self.cache / "wheels" / fingerprint
        _chmod_writable(entry)
        (entry / "payload.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache, "--json"
        )
        self.assertEqual(code, 3)
        self.assertIn("CACHE_CORRUPT", err)

    @needs_editable_pip
    def test_failed_project_install_removes_half_built_venv_and_retry_succeeds(self):
        worktree = make_worktree(self.tmp / "wt", "A", broken=True)
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 5)
        self.assertIn("PIP_INSTALL_FAILED", err)
        self.assertFalse(
            (worktree / "tools" / "llmtool" / ".venv").exists(),
            "a failed install must not leave a marker-less venv behind",
        )

        make_worktree(worktree, "A")  # repair the backend, same lock
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "env_created")

    @needs_editable_pip
    def test_non_editable_install_is_rejected(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 0, err)
        venv = worktree / "tools" / "llmtool" / ".venv"
        direct_urls = list(venv.rglob("llmtool-0.1.0.dist-info/direct_url.json"))
        self.assertEqual(len(direct_urls), 1, direct_urls)
        payload = json.loads(direct_urls[0].read_text())
        payload["dir_info"] = {}
        _chmod_writable(venv)
        direct_urls[0].write_text(json.dumps(payload), encoding="utf-8")
        code, out, err = self.run_cli("verify", "--worktree", worktree, "--json")
        self.assertEqual(code, 3)
        self.assertIn("PROVENANCE_MISSING", err)

    @needs_editable_pip
    def test_marker_without_worktree_root_fails_closed(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 0, err)
        marker = worktree / "tools" / "llmtool" / ".venv" / ".worktree-env.json"
        payload = json.loads(marker.read_text())
        for broken_value in (None, ""):
            with self.subTest(value=broken_value):
                forged = dict(payload)
                if broken_value is None:
                    forged.pop("worktree_root", None)
                else:
                    forged["worktree_root"] = broken_value
                _chmod_writable(marker.parent)
                marker.write_text(json.dumps(forged), encoding="utf-8")
                code, out, err = self.run_cli("verify", "--worktree", worktree, "--json")
                self.assertEqual(code, 3)
                self.assertIn("FOREIGN_VENV", err)

    def test_text_output_of_bootstrap_and_verify(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache,
            "--wheel-source", self.wheels, "--no-project",
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("env_created "), out)
        self.assertIn("cache=miss", out)
        code, out, err = self.run_cli("verify", "--worktree", worktree, "--no-project")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("verified "), out)

    def test_pip_without_pep660_fails_closed_and_removes_venv(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        from unittest import mock

        with mock.patch.object(we, "pip_version", return_value=(21, 2)):
            code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 4)
        self.assertIn("PIP_TOO_OLD", err)
        self.assertFalse(
            (worktree / "tools" / "llmtool" / ".venv").exists(),
            "an old-pip venv must not be left behind or fall back to a snapshot install",
        )

    def test_usage_errors_exit_4_and_global_json_still_works(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(stderr):
            we.main(["bootstrap", "--not-a-flag"])
        self.assertEqual(caught.exception.code, 4)

        code, out, err = self.run_cli("--json", "fingerprint", "--worktree", worktree)
        self.assertEqual(code, 0, err)
        self.assertIn("fingerprint", json.loads(out))


class IsolationTest(EnvironmentTempCase):
    @needs_editable_pip
    def test_two_worktrees_use_their_own_source_and_venv(self):
        first = make_worktree(self.tmp / "wt-a", "source-A")
        second = make_worktree(self.tmp / "wt-b", "source-B")

        for worktree in (first, second):
            code, out, err = self.bootstrap(worktree)
            self.assertEqual(code, 0, err)

        report_a = self.venv_report(first)
        report_b = self.venv_report(second)
        self.assertEqual(report_a["marker"], "source-A")
        self.assertEqual(report_b["marker"], "source-B")
        self.assertTrue(report_a["file"].startswith(str(first.resolve()) + os.sep))
        self.assertTrue(report_b["file"].startswith(str(second.resolve()) + os.sep))
        self.assertFalse(report_a["file"].startswith(str(second.resolve()) + os.sep))
        self.assertNotEqual(
            (first / "tools" / "llmtool" / ".venv").resolve(),
            (second / "tools" / "llmtool" / ".venv").resolve(),
        )

        for worktree in (first, second):
            code, out, err = self.run_cli("verify", "--worktree", worktree, "--json")
            self.assertEqual(code, 0, err)

    @needs_editable_pip
    def test_copied_venv_from_another_worktree_is_rejected(self):
        first = make_worktree(self.tmp / "wt-a", "source-A")
        third = make_worktree(self.tmp / "wt-c", "source-C")
        code, out, err = self.bootstrap(first)
        self.assertEqual(code, 0, err)

        source_venv = first / "tools" / "llmtool" / ".venv"
        target_venv = third / "tools" / "llmtool" / ".venv"
        shutil.copytree(str(source_venv), str(target_venv), symlinks=True)
        code, out, err = self.run_cli("verify", "--worktree", third, "--json")
        self.assertEqual(code, 3)
        self.assertIn("FOREIGN_VENV", err)

        marker = target_venv / ".worktree-env.json"
        _chmod_writable(target_venv)
        payload = json.loads(marker.read_text())
        payload["worktree_root"] = str(third.resolve())
        marker.write_text(json.dumps(payload))
        code, out, err = self.run_cli("verify", "--worktree", third, "--json")
        self.assertEqual(code, 3)
        self.assertIn("FOREIGN_SOURCE", err)

    @needs_editable_pip
    def test_symlinked_venv_is_rejected(self):
        first = make_worktree(self.tmp / "wt-a", "source-A")
        second = make_worktree(self.tmp / "wt-b", "source-B")
        code, out, err = self.bootstrap(first)
        self.assertEqual(code, 0, err)
        os.symlink(
            str(first / "tools" / "llmtool" / ".venv"),
            str(second / "tools" / "llmtool" / ".venv"),
        )
        code, out, err = self.run_cli("verify", "--worktree", second, "--json")
        self.assertEqual(code, 3)
        self.assertIn("UNSAFE_VENV", err)

        code, out, err = self.bootstrap(second, "--recreate")
        self.assertEqual(code, 3)
        self.assertIn("UNSAFE_VENV", err)

    def test_verify_without_bootstrap_is_rejected(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        code, out, err = self.run_cli("verify", "--worktree", worktree, "--json")
        self.assertEqual(code, 3)
        self.assertIn("VENV_MISSING", err)

    def test_worktree_without_lock_is_rejected(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        (worktree / "tools" / "llmtool" / "worktree-env.lock").unlink()
        code, out, err = self.run_cli("fingerprint", "--worktree", worktree, "--json")
        self.assertEqual(code, 4)
        self.assertIn("MISSING_LOCK", err)


    def test_symlinked_project_directory_is_rejected(self):
        worktree = make_worktree(self.tmp / "wt", "A")
        outside = self.tmp / "outside-project"
        shutil.move(str(worktree / "tools" / "llmtool"), str(outside))
        os.symlink(str(outside), str(worktree / "tools" / "llmtool"))

        code, out, err = self.run_cli("fingerprint", "--worktree", worktree, "--json")
        self.assertEqual(code, 3)
        self.assertIn("UNSAFE_PATH", err)

        code, out, err = self.bootstrap(worktree, "--recreate")
        self.assertEqual(code, 3)
        self.assertIn("UNSAFE_PATH", err)
        self.assertTrue(
            (outside / "pyproject.toml").is_file(),
            "the symlink target must not be modified or removed",
        )


if __name__ == "__main__":
    unittest.main()
