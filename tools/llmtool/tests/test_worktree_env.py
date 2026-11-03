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
import venv
import zipfile
from pathlib import Path
from unittest import mock

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

def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lock_text(hashes, entries=(("tiny-dep", "1.0.0"),)):
    """Fixture lock (schema 2): every entry carries its trusted sha256 lines."""

    lines = ["# test lock", "schema = 2", "", "[base]"]
    for name, version in entries:
        lines.append('%s = "%s"' % (name, version))
        for digest in hashes[name]:
            lines.append("    sha256:%s" % digest)
    return "\n".join(lines) + "\n"


def make_worktree(root, marker, hashes, extra_base=(), dependencies='"tiny-dep>=1.0"', broken=False):
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
    entries = (("tiny-dep", "1.0.0"),) + tuple(extra_base)
    (project / "worktree-env.lock").write_text(lock_text(hashes, entries), encoding="utf-8")
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
                mode = 0o755 if entry.is_dir() else stat.S_IMODE(entry.stat().st_mode) | 0o200
                os.chmod(str(entry), mode)
            except OSError:
                pass


class EnvironmentTempCase(unittest.TestCase):
    """Per-test workspaces backed by immutable class-level fixture templates."""

    _shared_fixture_root = None

    @classmethod
    def _ensure_shared_fixtures(cls):
        if EnvironmentTempCase._shared_fixture_root is not None:
            return
        root = Path(tempfile.mkdtemp(prefix="llmtool-env-fixtures-"))
        wheel_template = root / "wheels"
        dep = make_wheel(wheel_template, "tiny-dep", "1.0.0", marker="dep")
        extra = make_wheel(wheel_template, "tiny-extra", "1.0.0", marker="extra")
        wheel_hashes = {
            "tiny-dep": [sha256_of(dep)],
            "tiny-extra": [sha256_of(extra)],
        }
        for path in wheel_template.iterdir():
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
        wheel_template.chmod(0o555)

        if _bundled_pip_supports_editable():
            environ = mock.patch.dict(os.environ)
            environ.start()
            try:
                for key in we.INDEX_ENV_KEYS:
                    os.environ.pop(key, None)
                os.environ["PIP_CONFIG_FILE"] = os.devnull
                template_wt_a = make_worktree(
                    root / "template-wt-a", "source-A", wheel_hashes
                )
                template_cache_a = root / "template-cache-a"
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = we.main([
                        "bootstrap",
                        "--worktree", str(template_wt_a),
                        "--cache-root", str(template_cache_a),
                        "--wheel-source", str(wheel_template),
                        "--json",
                    ])
                if code != 0:
                    raise AssertionError("template bootstrap failed: " + stderr.getvalue())
                for dirpath, dirs, files in os.walk(str(template_wt_a), topdown=False):
                    for name in files:
                        path = Path(dirpath) / name
                        if not path.is_symlink():
                            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
                    for name in dirs:
                        path = Path(dirpath) / name
                        if not path.is_symlink():
                            path.chmod(0o555)
                template_wt_a.chmod(0o555)
            finally:
                environ.stop()
        else:
            template_wt_a = None
            template_cache_a = None

        EnvironmentTempCase._shared_fixture_root = root
        EnvironmentTempCase._wheel_template = wheel_template
        EnvironmentTempCase._wheel_hashes = wheel_hashes
        EnvironmentTempCase._template_wt_a = template_wt_a
        EnvironmentTempCase._template_cache_a = template_cache_a

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_shared_fixtures()
        cls._fixture_root = Path(tempfile.mkdtemp(prefix="llmtool-env-cls-fixtures-"))

    @classmethod
    def tearDownClass(cls):
        _chmod_writable(cls._fixture_root)
        shutil.rmtree(str(cls._fixture_root), ignore_errors=True)
        super().tearDownClass()

    def copy_bootstrapped_worktree(self, destination, marker="source-A"):
        destination = Path(destination)
        shutil.copytree(str(self._template_wt_a), str(destination), symlinks=True)
        _chmod_writable(destination)
        old_root = str(self._template_wt_a.resolve())
        new_root = str(destination.resolve())
        venv_root = destination / "tools" / "llmtool" / ".venv"
        marker_file = venv_root / ".worktree-env.json"
        payload = json.loads(marker_file.read_text(encoding="utf-8"))
        payload["worktree_root"] = new_root
        marker_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for pth in venv_root.rglob("llmtool_editable.pth"):
            pth.write_text(pth.read_text(encoding="utf-8").replace(old_root, new_root), encoding="utf-8")
        for direct_url in venv_root.rglob("llmtool-0.1.0.dist-info/direct_url.json"):
            direct_url.write_text(
                direct_url.read_text(encoding="utf-8").replace(old_root, new_root),
                encoding="utf-8",
            )
        if marker != "source-A":
            (destination / "tools" / "llmtool" / "llmtool" / "__init__.py").write_text(
                "MARKER = %r\n" % marker, encoding="utf-8"
            )
        return destination

    def setUp(self):
        # Hermetic package-source configuration: no ambient PIP_* index
        # settings and no user/global pip config files (pip and worktree_env
        # both skip every config file when PIP_CONFIG_FILE is os.devnull).
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for key in we.INDEX_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["PIP_CONFIG_FILE"] = os.devnull
        self.tmp = Path(tempfile.mkdtemp(prefix="llmtool-env-test-"))
        self.wheels = self.tmp / "wheels"
        shutil.copytree(str(self._wheel_template), str(self.wheels))
        self.hashes = {name: list(digests) for name, digests in self._wheel_hashes.items()}
        self.cache = self.tmp / "cache"

    def make_worktree(self, root, marker, hashes=None, **kwargs):
        return make_worktree(root, marker, hashes or self.hashes, **kwargs)

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
        inputs = we.fingerprint_inputs(
            lock, [], interp, we.installer_identity(python), we.index_identity(interp)
        )
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


_INSTALLER = {"name": "pip", "version": "25.1.1"}
_NO_INDEX = {"env": {}, "config": []}
_H = "a" * 64


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

    def _fp(
        self, lock, extras, interp, installer=_INSTALLER, index=_NO_INDEX, project_install=True
    ):
        return we.fingerprint_id(
            we.fingerprint_inputs(
                lock, extras, interp, installer, index, project_install=project_install
            )
        )

    def test_fingerprint_is_deterministic_and_normalises_machine(self):
        lock = Path(tempfile.mkdtemp(prefix="llmtool-lock-")) / "lock"
        lock.write_text("schema = 2\n[base]\ntiny-dep = \"1.0.0\"\n    sha256:%s\n" % _H, encoding="utf-8")
        try:
            first = we.fingerprint_inputs(lock, ["tui"], self._inputs(), _INSTALLER, _NO_INDEX)
            second = we.fingerprint_inputs(lock, ["tui"], self._inputs(), _INSTALLER, _NO_INDEX)
            self.assertEqual(first, second)
            self.assertEqual(first["machine"], "x86_64")
            self.assertEqual(first["installer"], _INSTALLER)
            self.assertTrue(first["install"]["project_install"])
            self.assertIn("--no-build-isolation", first["install"]["project_flags"])
            self.assertIn("--require-hashes", first["install"]["dependency_flags"])
            self.assertEqual(we.fingerprint_id(first), we.fingerprint_id(second))
        finally:
            shutil.rmtree(str(lock.parent), ignore_errors=True)

    def test_installer_and_index_identity_are_mandatory(self):
        lock = Path(tempfile.mkdtemp(prefix="llmtool-lock-")) / "lock"
        lock.write_text("schema = 2\n", encoding="utf-8")
        try:
            for installer, index in ((None, _NO_INDEX), ({"name": "pip"}, _NO_INDEX), (_INSTALLER, None), (_INSTALLER, {})):
                with self.subTest(installer=installer, index=index):
                    with self.assertRaises(we.EnvError) as caught:
                        we.fingerprint_inputs(lock, [], self._inputs(), installer, index)
                    self.assertEqual(caught.exception.status, "INVALID_FINGERPRINT")
        finally:
            shutil.rmtree(str(lock.parent), ignore_errors=True)

    def test_fingerprint_changes_with_every_invalidating_input(self):
        lock = Path(tempfile.mkdtemp(prefix="llmtool-lock-")) / "lock"
        lock.write_text("schema = 2\n[base]\ntiny-dep = \"1.0.0\"\n    sha256:%s\n" % _H, encoding="utf-8")
        try:
            baseline = self._fp(lock, [], self._inputs())
            variants = {
                "python": self._inputs(version="3.10.0"),
                "abi": self._inputs(abi="cpython-310-darwin"),
                "platform": self._inputs(platform="linux", platform_tag="linux-x86_64"),
                "machine": self._inputs(machine="arm64"),
                "platform_tag": self._inputs(platform_tag="macosx-14.0-x86_64"),
            }
            for label, interp in variants.items():
                with self.subTest(label):
                    self.assertNotEqual(baseline, self._fp(lock, [], interp))
            with self.subTest("extras"):
                self.assertNotEqual(baseline, self._fp(lock, ["tui"], self._inputs()))
            with self.subTest("project install mode"):
                self.assertNotEqual(
                    baseline,
                    self._fp(lock, [], self._inputs(), project_install=False),
                )
            with self.subTest("build flags"):
                with mock.patch.object(
                    we,
                    "PROJECT_INSTALL_OPTIONS",
                    tuple(we.PROJECT_INSTALL_OPTIONS) + ("--no-build-isolation-test",),
                ):
                    self.assertNotEqual(baseline, self._fp(lock, [], self._inputs()))
            with self.subTest("installer version"):
                self.assertNotEqual(
                    baseline, self._fp(lock, [], self._inputs(), installer={"name": "pip", "version": "21.2.4"})
                )
            with self.subTest("installer name"):
                self.assertNotEqual(
                    baseline, self._fp(lock, [], self._inputs(), installer={"name": "uv", "version": "25.1.1"})
                )
            for label, index in (
                ("PIP_INDEX_URL", {"env": {"PIP_INDEX_URL": ["https://mirror.example/simple"]}, "config": []}),
                ("PIP_EXTRA_INDEX_URL", {"env": {"PIP_EXTRA_INDEX_URL": ["https://extra.example/simple"]}, "config": []}),
                ("PIP_FIND_LINKS", {"env": {"PIP_FIND_LINKS": ["/srv/wheels"]}, "config": []}),
                ("pip config", {"env": {}, "config": [["global", "index-url", ["https://cfg.example/simple"]]]}),
            ):
                with self.subTest(label):
                    self.assertNotEqual(baseline, self._fp(lock, [], self._inputs(), index=index))
            lock.write_text(
                "schema = 2\n[base]\ntiny-dep = \"2.0.0\"\n    sha256:%s\n" % _H, encoding="utf-8"
            )
            self.assertNotEqual(baseline, self._fp(lock, [], self._inputs()))
            lock.write_text(
                "schema = 2\n[base]\ntiny-dep = \"1.0.0\"\n    sha256:%s\n" % ("b" * 64), encoding="utf-8"
            )
            self.assertNotEqual(baseline, self._fp(lock, [], self._inputs()), "a hash change must invalidate")
        finally:
            shutil.rmtree(str(lock.parent), ignore_errors=True)


class IndexIdentityTest(unittest.TestCase):
    SECRETS = ("alice", "s3cr3t-pw", "tok-abc", "frag-xyz", "bob", "hunter2", "cfgpass", "cfgtoken")

    def setUp(self):
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for key in we.INDEX_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["PIP_CONFIG_FILE"] = os.devnull
        self.interp = {"prefix": "/nonexistent-prefix", "base_prefix": "/nonexistent-prefix"}

    def test_strip_credentials(self):
        self.assertEqual(
            we.strip_credentials("https://alice:s3cr3t-pw@Pypi.Example.invalid:8443/simple/?token=tok-abc#frag-xyz"),
            "https://pypi.example.invalid:8443/simple/",
        )
        self.assertEqual(we.strip_credentials("/srv/wheels"), "/srv/wheels")
        with self.assertRaises(we.EnvError):
            we.strip_credentials("https://host:notaport/simple")
        with self.assertRaisesRegex(we.EnvError, "unsupported userinfo") as caught:
            we.strip_credentials("alice:scheme-secret@links.example.invalid/simple")
        self.assertNotIn("scheme-secret", str(caught.exception))

    def test_scheme_less_credentials_rejected_from_env_and_pip_config(self):
        value = "alice:scheme-secret@links.example.invalid/simple"
        with mock.patch.dict(os.environ, {"PIP_FIND_LINKS": value}):
            with self.assertRaises(we.EnvError) as caught:
                we.index_identity(self.interp)
        self.assertNotIn("scheme-secret", str(caught.exception))

        tmp = Path(tempfile.mkdtemp(prefix="llmtool-pipcfg-schemeless-"))
        config = tmp / "pip.conf"
        config.write_text("[install]\nfind-links = %s\n" % value, encoding="utf-8")
        with mock.patch.dict(os.environ, {"PIP_CONFIG_FILE": str(config)}):
            with self.assertRaises(we.EnvError) as caught:
                we.index_identity(self.interp)
        self.assertNotIn("scheme-secret", str(caught.exception))

    def test_each_source_changes_identity_and_never_records_credentials(self):
        baseline = we.index_identity(self.interp)
        self.assertEqual(baseline, {"env": {}, "config": []})
        tmp = Path(tempfile.mkdtemp(prefix="llmtool-pipcfg-"))
        try:
            config = tmp / "pip.conf"
            config.write_text(
                "[global]\nindex-url = https://carol:cfgpass@cfg.example.invalid/simple?auth=cfgtoken\n"
                "timeout = 5\n", encoding="utf-8",
            )
            cases = {
                "PIP_INDEX_URL": "https://alice:s3cr3t-pw@pypi.example.invalid/simple?token=tok-abc#frag-xyz",
                "PIP_EXTRA_INDEX_URL": "https://bob:hunter2@extra.example.invalid/simple https://plain.example.invalid/s",
                "PIP_FIND_LINKS": "https://bob:hunter2@links.example.invalid/wheels/",
                "PIP_CONFIG_FILE": str(config),
            }
            seen = []
            for key, value in cases.items():
                with self.subTest(key):
                    with mock.patch.dict(os.environ, {key: value}):
                        identity = we.index_identity(self.interp)
                    self.assertNotEqual(identity, baseline)
                    self.assertNotIn(identity, seen)
                    seen.append(identity)
                    blob = json.dumps(identity)
                    for secret in self.SECRETS:
                        self.assertNotIn(secret, blob)
                    self.assertIn("example.invalid", blob)
            self.assertEqual(seen[-1]["config"], [["global", "index-url", ["https://cfg.example.invalid/simple"]]])
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


class LockTest(unittest.TestCase):
    def _parse(self, text):
        directory = Path(tempfile.mkdtemp(prefix="llmtool-lock-"))
        lock = directory / "worktree-env.lock"
        lock.write_text(text, encoding="utf-8")
        try:
            return we.parse_lock(lock)
        finally:
            shutil.rmtree(str(directory), ignore_errors=True)

    def test_parse_normalises_names_hashes_and_allows_empty_build(self):
        sections = self._parse(
            "schema = 2\n\n[base]\ntiny_dep = \"1.0.0\"\n    sha256:%s  # tiny_dep-1.0.0.whl\n"
            "    sha256:%s\n\n[build]\n" % (_H, "b" * 64)
        )
        self.assertEqual(sections["base"], {"tiny-dep": "1.0.0"})
        self.assertEqual(sections["build"], {})
        self.assertEqual(sections.hashes["tiny-dep"], frozenset({_H, "b" * 64}))
        requirements = we.selected_requirements(sections, [])
        self.assertEqual(requirements[0]["sha256"], sorted({_H, "b" * 64}))
        self.assertIn("--hash=sha256:%s" % _H, we.requirement_lines(requirements))

    def test_rejects_duplicate_package_across_groups(self):
        with self.assertRaises(we.EnvError) as caught:
            self._parse(
                "schema = 2\n[base]\ntiny-dep = \"1.0.0\"\n    sha256:%s\n[build]\nTiny_Dep = \"1.0.0\"\n    sha256:%s\n"
                % (_H, _H)
            )
        self.assertEqual(caught.exception.status, "INVALID_LOCK")

    def test_rejects_unpinned_unhashed_or_invalid_entries(self):
        good_hash = "    sha256:%s\n" % _H
        for text in (
            'schema = 2\n[base]\ntiny-dep = ">=1.0"\n' + good_hash,
            'schema = 2\n[base]\ntiny dep = "1.0.0"\n' + good_hash,
            'schema = 1\n[base]\ntiny-dep = "1.0.0"\n' + good_hash,
            'schema = 2\n[unknown]\ntiny-dep = "1.0.0"\n' + good_hash,
            'schema = 2\n[base]\n',
            'tiny-dep = "1.0.0"\n' + good_hash,
            'schema = 2\n[base]\ntiny-dep = "1.0.0"\n',
            'schema = 2\n[base]\ntiny-dep = "1.0.0"\n    sha256:XYZ\n',
            'schema = 2\n[base]\ntiny-dep = "1.0.0"\n    sha256:%s\n' % ("a" * 63),
            'schema = 2\n[base]\n    sha256:%s\ntiny-dep = "1.0.0"\n' % _H,
            'schema = 2\n[base]\ntiny-dep = "1.0.0"\n' + good_hash + good_hash,
        ):
            with self.subTest(text):
                with self.assertRaises(we.EnvError) as caught:
                    self._parse(text)
                self.assertEqual(caught.exception.status, "INVALID_LOCK")

    def test_repository_lock_pins_hashes_for_every_package(self):
        sections = we.parse_lock(TOOLS / "worktree-env.lock")
        for group in ("base", "build", "tui"):
            for name in sections[group]:
                with self.subTest(name=name):
                    self.assertTrue(sections.hashes[name])

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
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._pip_venv_template = cls._fixture_root / "pip-venv"
        venv.EnvBuilder(with_pip=True).create(str(cls._pip_venv_template))
        for root, dirs, files in os.walk(str(cls._pip_venv_template), topdown=False):
            for name in files:
                path = Path(root) / name
                if not path.is_symlink():
                    path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
            for name in dirs:
                path = Path(root) / name
                if not path.is_symlink():
                    path.chmod(0o555)
        cls._pip_venv_template.chmod(0o555)

    @classmethod
    def tearDownClass(cls):
        _chmod_writable(cls._pip_venv_template)
        super().tearDownClass()

    def copy_pip_venv(self, destination):
        shutil.copytree(
            str(self._pip_venv_template), str(destination), symlinks=True
        )
        _chmod_writable(destination)

    def test_cache_miss_offline_fails_closed_without_creating_a_venv(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
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
        worktree = self.make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 0, err)
        first = json.loads(out)
        self.assertEqual(first["status"], "env_created")
        self.assertEqual(first["cache_status"], "miss")
        first_ops = first["operations"]
        self.assertEqual(first_ops["pip_download_command_count"], 0)
        self.assertEqual(first_ops["downloaded_wheel_count"], 0)
        self.assertEqual(first_ops["wheel_source_copy_count"], len(first["packages"]))
        self.assertEqual(first_ops["dependency_install_command_count"], 1)
        self.assertEqual(first_ops["dependency_install_package_count"],
                         len(first_ops["dependency_installed"]))
        self.assertTrue(first_ops["dependency_installed"])
        self.assertEqual(first_ops["project_install_command_count"], 1)
        self.assertEqual(first_ops["project_install_package_count"], 1)
        self.assertEqual(first_ops["project_installed"][0]["name"], "llmtool")

        report = self.venv_report(worktree)
        self.assertEqual(report["marker"], "A")
        self.assertTrue(report["file"].startswith(str(worktree.resolve())))

        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 0, err)
        second = json.loads(out)
        self.assertEqual(second["status"], "env_reused")
        self.assertEqual(second["operations"]["pip_download_command_count"], 0)
        self.assertEqual(second["operations"]["dependency_install_command_count"], 0)
        self.assertEqual(second["operations"]["project_install_command_count"], 0)

        base = os.path.realpath(we.default_python_executable())
        mode_before = stat.S_IMODE(os.stat(base).st_mode)
        code, out, err = self.bootstrap(worktree, "--recreate")
        self.assertEqual(code, 0, err)
        third = json.loads(out)
        self.assertEqual(third["status"], "env_created")
        self.assertEqual(third["cache_status"], "hit")
        self.assertEqual(third["operations"]["pip_download_command_count"], 0)
        self.assertEqual(third["operations"]["downloaded_wheel_count"], 0)
        self.assertEqual(third["operations"]["dependency_install_command_count"], 1)
        self.assertEqual(third["operations"]["project_install_command_count"], 1)
        self.assertEqual(
            mode_before,
            stat.S_IMODE(os.stat(base).st_mode),
            "venv recreation must never chmod through interpreter symlinks",
        )

        manifest = json.loads(
            (self.cache / "wheels" / third["cache_fingerprint"] / "manifest.json").read_text()
        )
        self.assertEqual(
            {item["name"] for item in manifest["files"]}, {"tiny-dep"}
        )

    @needs_editable_pip
    def test_project_install_mode_is_fingerprinted_and_existing_env_rejects_mode_change(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree, "--no-project")
        self.assertEqual(code, 0, err)
        first = json.loads(out)
        self.assertEqual(first["status"], "env_created")
        self.assertFalse(first["project"])

        code, out, err = self.run_cli(
            "fingerprint", "--worktree", worktree, "--no-project", "--json"
        )
        self.assertEqual(code, 0, err)
        dependencies_only = json.loads(out)
        self.assertFalse(dependencies_only["inputs"]["install"]["project_install"])
        self.assertEqual(dependencies_only["fingerprint"], first["cache_fingerprint"])

        code, out, err = self.run_cli("fingerprint", "--worktree", worktree, "--json")
        self.assertEqual(code, 0, err)
        project_mode = json.loads(out)
        self.assertTrue(project_mode["inputs"]["install"]["project_install"])
        self.assertNotEqual(project_mode["fingerprint"], first["cache_fingerprint"])

        code, out, err = self.run_cli(
            "verify", "--worktree", worktree, "--project", "--json"
        )
        self.assertEqual(code, 3)
        self.assertIn("ENV_DRIFT", err)

        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 3)
        self.assertIn("ENV_DRIFT", err)
        self.assertTrue(self.venv_python(worktree).is_file())

        code, out, err = self.bootstrap(worktree, "--recreate")
        self.assertEqual(code, 0, err)
        rebuilt = json.loads(out)
        self.assertEqual(rebuilt["status"], "env_created")
        self.assertTrue(rebuilt["project"])
        self.assertNotEqual(rebuilt["cache_fingerprint"], first["cache_fingerprint"])

    def test_corrupt_wheel_fails_closed_and_preserves_existing_venv(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree, "--no-project")
        self.assertEqual(code, 0, err)
        fingerprint = json.loads(out)["cache_fingerprint"]
        entry = self.cache / "wheels" / fingerprint
        wheel = next(entry.glob("*.whl"))
        _chmod_writable(entry)
        data = bytearray(wheel.read_bytes())
        data[0:4] = b"PK\x03\x04"
        data[-8:] = b"tampered"
        wheel.write_bytes(bytes(data))

        code, out, err = self.bootstrap(worktree, "--recreate", "--no-project")
        self.assertEqual(code, 3)
        self.assertIn("CACHE_CORRUPT", err)
        self.assertTrue(self.venv_python(worktree).is_file(), "venv must survive a failed rebuild")
        self.assertEqual(self.dep_marker(worktree), "dep")
        self.assertTrue((worktree / "tools" / "llmtool" / ".venv" / "pyvenv.cfg").is_file())

    def test_recreate_refuses_invalid_existing_venv_without_deleting_it(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        venv = worktree / "tools" / "llmtool" / ".venv"
        venv.mkdir()
        sentinel = venv / "preserve-me.txt"
        sentinel.write_text("pre-existing data", encoding="utf-8")

        code, out, err = self.bootstrap(worktree, "--recreate")

        self.assertEqual(code, 4)
        self.assertIn("INVALID_VENV", err)
        self.assertTrue(sentinel.is_file())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "pre-existing data")

    def test_pip_failure_diagnostics_redact_http_and_file_url_secrets(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        original_run = subprocess.run
        secret_output = (
            "Could not fetch https://user:password@packages.example/simple?token=secret-value\n"
            "Could not open file:///private/tmp/wheels/pkg.whl?token=file-secret#fragment-secret"
        )

        def run_with_pip_failure(command, *args, **kwargs):
            if "pip" in command and "download" in command:
                return subprocess.CompletedProcess(command, 1, "", secret_output)
            return original_run(command, *args, **kwargs)

        with mock.patch.object(we.subprocess, "run", side_effect=run_with_pip_failure):
            code, out, err = self.run_cli(
                "bootstrap", "--worktree", worktree, "--cache-root", self.cache, "--json"
            )

        self.assertEqual(code, 5)
        self.assertIn("PIP_DOWNLOAD_FAILED", err)
        self.assertIn("PIP_DOWNLOAD_FAILED", out)
        for secret in (
            "user", "password", "token", "secret-value", "file-secret", "fragment-secret"
        ):
            self.assertNotIn(secret, err)
            self.assertNotIn(secret, out)

    def _capture_real_subprocess_output(self, operation, markers):
        original_run = subprocess.run
        captured = []

        def capture(command, *args, **kwargs):
            result = original_run(command, *args, **kwargs)
            if len(command) > 2 and command[1:3] == ["-m", "pip"]:
                captured.append((result.stderr or "") + (result.stdout or ""))
            return result

        with mock.patch.object(we.subprocess, "run", side_effect=capture):
            with self.assertRaises(we.EnvError) as caught:
                operation()
        raw = "\n".join(captured)
        self.assertTrue(captured, "the regression must exercise a real pip subprocess")
        for marker in markers:
            self.assertIn(marker, raw, "real pip output no longer reproduces the diagnostic format")
            self.assertNotIn(marker, str(caught.exception))

    def test_real_pip_download_error_redacts_file_index_query_and_fragment(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        requirements = we.selected_requirements(
            we.parse_lock(worktree / "tools" / "llmtool" / "worktree-env.lock"), []
        )
        destination = self.tmp / "download-destination"
        destination.mkdir()
        index = "file://%s?token=download-secret#download-fragment" % (
            self.tmp / "missing-index"
        )
        with mock.patch.dict(os.environ, {"PIP_INDEX_URL": index}):
            self._capture_real_subprocess_output(
                lambda: we._download_wheels(sys.executable, requirements, destination),
                ("download-secret", "download-fragment"),
            )

    def test_real_pip_dependency_install_error_redacts_file_links(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        requirements = we.selected_requirements(
            we.parse_lock(worktree / "tools" / "llmtool" / "worktree-env.lock"), []
        )
        wheelhouse = self.tmp / "empty-wheelhouse"
        wheelhouse.mkdir()
        isolated_python = self.tmp / "pip-dependency-venv" / "bin" / "python"
        self.copy_pip_venv(isolated_python.parent.parent)
        links = "file://%s?token=install-secret#install-fragment" % wheelhouse
        with mock.patch.dict(os.environ, {"PIP_FIND_LINKS": links}):
            self._capture_real_subprocess_output(
                lambda: we.install_requirements(str(isolated_python), wheelhouse, requirements),
                ("install-secret", "install-fragment"),
            )

    @needs_editable_pip
    def test_real_pip_project_install_error_redacts_worktree_path(self):
        marker = "project-secret"
        fragment = "project-fragment"
        project_root = self.tmp / ("wt?token=%s#%s" % (marker, fragment))
        wheel_source = self.tmp / "wheel-source"
        wheel = make_wheel(wheel_source, "tiny-dep", "1.0.0")
        worktree = self.make_worktree(
            project_root, "A", {"tiny-dep": [sha256_of(wheel)]}, broken=True
        )
        wheelhouse = self.tmp / "empty-project-wheelhouse"
        wheelhouse.mkdir()
        isolated_python = self.tmp / "pip-project-venv" / "bin" / "python"
        self.copy_pip_venv(isolated_python.parent.parent)
        self._capture_real_subprocess_output(
            lambda: we.install_project(
                str(isolated_python), worktree / "tools" / "llmtool", wheelhouse
            ),
            (marker, fragment),
        )

    def test_subprocess_helpers_and_internal_error_redact_url_path_diagnostics(self):
        secret = "helper-secret"
        fragment = "helper-fragment"
        script = self.tmp / "diagnostic-probe"
        script.write_text(
            "#!/bin/sh\nprintf '%%s\\n' 'url: /private/path?token=%s#%s' >&2\nexit 1\n"
            % (secret, fragment),
            encoding="utf-8",
        )
        script.chmod(0o755)

        for operation in (
            lambda: we.interpreter_info(str(script)),
            lambda: we.pip_version_string(str(script)),
            lambda: we.pip_version(str(script)),
            lambda: we._probe_venv(str(script), []),
        ):
            with self.assertRaises(we.EnvError) as caught:
                operation()
            self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(fragment, str(caught.exception))

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(
            we, "cmd_fingerprint", side_effect=RuntimeError(
                "file:/private/path?token=%s#%s" % (secret, fragment)
            )
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = we.main(["--json", "fingerprint", "--worktree", str(self.tmp)])
        self.assertEqual(code, 3)
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(fragment, stdout.getvalue() + stderr.getvalue())

    def test_probe_does_not_write_bytecode_into_imported_package(self):
        package_root = self.tmp / "probe-package"
        package = package_root / "llmtool"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("VALUE = 'probe'\n", encoding="utf-8")

        probe_venv = self.tmp / "probe-venv"
        venv.EnvBuilder(with_pip=False).create(str(probe_venv))
        version = "%d.%d" % sys.version_info[:2]
        site_packages = probe_venv / "lib" / ("python" + version) / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)
        (site_packages / "probe-package.pth").write_text(
            str(package_root) + "\n", encoding="utf-8"
        )

        result = we._probe_venv(str(probe_venv / "bin" / "python"), [])

        self.assertEqual(result["llmtool_file"], str((package / "__init__.py").resolve()))
        self.assertFalse((package / "__pycache__").exists())

    def test_redactor_strips_arbitrary_query_keys_and_bare_path_fragments(self):
        diagnostic = (
            "urllib3 Retry(total=0): GET /private/wheels/pkg.whl"
            "?key=key-secret&sig=sig-secret&sas=sas-secret&sv=sv-secret"
            "#bare-fragment-secret; also pkg/sub?sig=relative-secret#relative-fragment"
        )

        redacted = we._redact_diagnostic(diagnostic)

        self.assertIn("GET /private/wheels/pkg.whl", redacted)
        for secret in (
            "key-secret", "sig-secret", "sas-secret", "sv-secret", "bare-fragment-secret",
            "relative-secret", "relative-fragment",
        ):
            self.assertNotIn(secret, redacted)
        self.assertNotIn("?", redacted)
        self.assertNotIn("#", redacted)

    def test_missing_manifest_and_extra_wheel_fail_closed(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
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
        worktree = self.make_worktree(self.tmp / "wt", "A")
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
        first = self.make_worktree(self.tmp / "wt-a", "A")
        second = self.make_worktree(self.tmp / "wt-b", "B", extra_base=(("tiny-extra", "1.0.0"),))

        code, out, err = self.bootstrap(first)
        self.assertEqual(code, 0, err)
        fp_a = json.loads(out)["cache_fingerprint"]

        # The cache entry for A must not satisfy B (different dependency set).
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", second, "--cache-root", self.cache, "--offline", "--json"
        )
        self.assertEqual(code, 2)
        self.assertIn("CACHE_MISS", err)

        code, out, err = self.bootstrap(second)
        self.assertEqual(code, 0, err)
        fp_b = json.loads(out)["cache_fingerprint"]
        self.assertNotEqual(fp_a, fp_b)
        self.assertTrue((self.cache / "wheels" / fp_a / "manifest.json").is_file())
        self.assertTrue((self.cache / "wheels" / fp_b / "manifest.json").is_file())

    def test_cache_root_inside_worktree_is_rejected(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
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

    def test_cache_root_with_unsafe_owner_or_permissions_is_rejected(self):
        cache = self.tmp / "unsafe-cache"
        cache.mkdir(mode=0o700)
        info = cache.lstat()
        foreign_owner = mock.Mock(st_mode=info.st_mode, st_uid=os.getuid() + 1)
        with self.assertRaises(we.EnvError) as raised:
            we._validate_cache_root(cache, foreign_owner)
        self.assertEqual(raised.exception.status, "UNSAFE_CACHE_ROOT")

        cache.chmod(0o777)
        with self.assertRaises(we.EnvError) as raised:
            we._validate_cache_root(cache)
        self.assertEqual(raised.exception.status, "UNSAFE_CACHE_ROOT")

    def test_symlinked_cache_root_and_lock_are_rejected(self):
        target = self.tmp / "cache-target"
        target.mkdir(mode=0o700)
        linked_root = self.tmp / "cache-link"
        os.symlink(str(target), str(linked_root))
        worktree = self.make_worktree(self.tmp / "wt", "A")
        with self.assertRaises(we.EnvError) as raised:
            we._resolve_cache_root(linked_root, worktree)
        self.assertEqual(raised.exception.status, "UNSAFE_CACHE_ROOT")

        lock_target = self.tmp / "lock-target"
        lock_target.touch()
        os.symlink(str(lock_target), str(target / ".lock"))
        with self.assertRaises(we.EnvError) as raised:
            we.open_cache_lock(target)
        self.assertEqual(raised.exception.status, "UNSAFE_CACHE_LOCK")

    def test_cache_info_lists_entries(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        self.seed_cache(worktree)
        code, out, err = self.run_cli(
            "cache-info", "--worktree", worktree, "--cache-root", self.cache, "--json"
        )
        self.assertEqual(code, 0, err)
        info = json.loads(out)
        self.assertEqual(len(info["entries"]), 1)
        self.assertEqual(info["entries"][0]["packages"], {"tiny-dep": "1.0.0"})


    def test_lock_must_be_transitively_closed(self):
        wheels = self.tmp / "unclosed-wheels"
        unclosed = make_wheel(wheels, "tiny-dep", "1.0.0", requires=["ghost-dep>=1.0"])
        worktree = self.make_worktree(self.tmp / "wt", "A", hashes={"tiny-dep": [sha256_of(unclosed)]})
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache,
            "--wheel-source", wheels, "--json",
        )
        self.assertEqual(code, 3)
        self.assertIn("LOCK_NOT_CLOSED", err)
        self.assertFalse((worktree / "tools" / "llmtool" / ".venv").exists())

    def test_stray_file_in_cache_entry_is_rejected(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
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
        worktree = self.make_worktree(self.tmp / "wt", "A", broken=True)
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 5)
        self.assertIn("PIP_INSTALL_FAILED", err)
        self.assertFalse(
            (worktree / "tools" / "llmtool" / ".venv").exists(),
            "a failed install must not leave a marker-less venv behind",
        )

        self.make_worktree(worktree, "A")  # repair the backend, same lock
        code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "env_created")

    @needs_editable_pip
    def test_non_editable_install_is_rejected(self):
        worktree = self.copy_bootstrapped_worktree(self.tmp / "wt", "A")
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
        worktree = self.copy_bootstrapped_worktree(self.tmp / "wt", "A")
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
        worktree = self.make_worktree(self.tmp / "wt", "A")
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
        worktree = self.make_worktree(self.tmp / "wt", "A")
        from unittest import mock

        with mock.patch.object(we, "pip_version", return_value=(21, 2)):
            code, out, err = self.bootstrap(worktree)
        self.assertEqual(code, 4)
        self.assertIn("PIP_TOO_OLD", err)
        self.assertFalse(
            (worktree / "tools" / "llmtool" / ".venv").exists(),
            "an old-pip venv must not be left behind or fall back to a snapshot install",
        )

    def _retamper_entry(self, entry, replacement):
        """Replace the cached tiny-dep wheel AND rewrite manifest.json to match."""

        _chmod_writable(entry)
        wheel = next(entry.glob("tiny_dep-*.whl"))
        wheel.write_bytes(replacement.read_bytes())
        manifest_path = entry / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for item in manifest["files"]:
            if item["name"] == "tiny-dep":
                item["sha256"] = sha256_of(wheel)
                item["size"] = wheel.stat().st_size
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_consistent_manifest_and_wheel_tamper_is_rejected_on_hit(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        fingerprint = self.seed_cache(worktree)
        evil = make_wheel(self.tmp / "evil", "tiny-dep", "1.0.0", marker="evil")
        self._retamper_entry(self.cache / "wheels" / fingerprint, evil)
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache, "--offline", "--json"
        )
        self.assertEqual(code, 3)
        self.assertIn("WHEEL_HASH_UNTRUSTED", err)
        self.assertFalse((worktree / "tools" / "llmtool" / ".venv").exists())

    def test_consistent_tamper_is_rejected_for_an_existing_venv_recreate(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree, "--no-project")
        self.assertEqual(code, 0, err)
        entry = self.cache / "wheels" / json.loads(out)["cache_fingerprint"]
        evil = make_wheel(self.tmp / "evil", "tiny-dep", "1.0.0", marker="evil")
        self._retamper_entry(entry, evil)
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache, "--recreate",
            "--no-project", "--json",
        )
        self.assertEqual(code, 3)
        self.assertIn("WHEEL_HASH_UNTRUSTED", err)
        self.assertEqual(self.dep_marker(worktree), "dep", "the existing venv must survive")

    def test_wheel_source_with_unpinned_digest_fails_closed(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        local = self.tmp / "locally-built"
        make_wheel(local, "tiny-dep", "1.0.0", marker="locally-built")
        code, out, err = self.run_cli(
            "bootstrap", "--worktree", worktree, "--cache-root", self.cache,
            "--wheel-source", local, "--json",
        )
        self.assertEqual(code, 3)
        self.assertIn("WHEEL_HASH_UNTRUSTED", err)
        self.assertFalse((worktree / "tools" / "llmtool" / ".venv").exists())
        wheels = self.cache / "wheels"
        self.assertEqual(list(wheels.iterdir()) if wheels.exists() else [], [], "nothing may be published")

    def test_download_uses_hash_checking_and_rejects_unpinned_wheels(self):
        lock_dir = self.make_worktree(self.tmp / "wt", "A") / "tools" / "llmtool"
        requirements = we.selected_requirements(we.parse_lock(lock_dir / "worktree-env.lock"), [])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), Path(cmd[cmd.index("-r") + 1]).read_text()))
            make_wheel(Path(cmd[cmd.index("--dest") + 1]), "tiny-dep", "1.0.0", marker="from-index")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        destination = self.tmp / "download"
        destination.mkdir()
        with mock.patch.object(we.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(we.EnvError) as caught:
                we._download_wheels(sys.executable, requirements, destination)
        self.assertEqual(caught.exception.status, "WHEEL_HASH_UNTRUSTED")
        cmd, req = calls[0]
        self.assertIn("--require-hashes", cmd)
        self.assertIn("--hash=sha256:%s" % self.hashes["tiny-dep"][0], req)

        trusted = self.tmp / "download-ok"
        trusted.mkdir()

        def fake_trusted(cmd, **kwargs):
            shutil.copyfile(str(next(self.wheels.glob("tiny_dep-*.whl"))),
                            str(Path(cmd[cmd.index("--dest") + 1]) / "tiny_dep-1.0.0-py3-none-any.whl"))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(we.subprocess, "run", side_effect=fake_trusted):
            wheels = we._download_wheels(sys.executable, requirements, trusted)
        self.assertEqual(set(wheels), {"tiny-dep"})

    def test_installer_change_invalidates_env_and_cache_key(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree, "--no-project")
        self.assertEqual(code, 0, err)
        original = json.loads(out)
        real = we.installer_identity

        def other_installer(python):
            identity = dict(real(python))
            identity["version"] = identity["version"] + ".post-test"
            return identity

        with mock.patch.object(we, "installer_identity", side_effect=other_installer):
            code, out, err = self.run_cli("fingerprint", "--worktree", worktree, "--json")
            self.assertEqual(code, 0, err)
            self.assertNotEqual(json.loads(out)["fingerprint"], original["cache_fingerprint"])
            code, out, err = self.run_cli("verify", "--worktree", worktree, "--no-project", "--json")
            self.assertEqual(code, 3)
            self.assertIn("ENV_DRIFT", err)
            code, out, err = self.bootstrap(worktree, "--no-project")
            self.assertEqual(code, 3)
            self.assertIn("ENV_DRIFT", err)

    def test_index_configuration_change_invalidates_env_and_cache_key(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        code, out, err = self.bootstrap(worktree, "--no-project")
        self.assertEqual(code, 0, err)
        original = json.loads(out)["cache_fingerprint"]
        config = self.tmp / "pip.conf"
        config.write_text("[install]\nfind-links = https://links.example.invalid/w\n", encoding="utf-8")
        for key, value in (
            ("PIP_INDEX_URL", "https://mirror.example.invalid/simple"),
            ("PIP_EXTRA_INDEX_URL", "https://extra.example.invalid/simple"),
            ("PIP_FIND_LINKS", "https://links.example.invalid/wheels"),
            ("PIP_CONFIG_FILE", str(config)),
        ):
            with self.subTest(key), mock.patch.dict(os.environ, {key: value}):
                code, out, err = self.run_cli("fingerprint", "--worktree", worktree, "--json")
                self.assertEqual(code, 0, err)
                self.assertNotEqual(json.loads(out)["fingerprint"], original)
                code, out, err = self.run_cli("verify", "--worktree", worktree, "--no-project", "--json")
                self.assertEqual(code, 3)
                self.assertIn("ENV_DRIFT", err)
        code, out, err = self.run_cli("verify", "--worktree", worktree, "--no-project", "--json")
        self.assertEqual(code, 0, err)

    def test_credentials_never_reach_fingerprint_manifest_marker_or_output(self):
        secrets = ("alice", "s3cr3t-pw", "tok-abc", "bob", "hunter2", "carol", "cfgpass", "cfgtoken")
        config = self.tmp / "pip.conf"
        config.write_text(
            "[global]\nextra-index-url = https://carol:cfgpass@cfg.example.invalid/simple?auth=cfgtoken\n",
            encoding="utf-8",
        )
        worktree = self.make_worktree(self.tmp / "wt", "A")
        with mock.patch.dict(os.environ, {
            "PIP_INDEX_URL": "https://alice:s3cr3t-pw@pypi.example.invalid/simple?token=tok-abc",
            "PIP_FIND_LINKS": "https://bob:hunter2@links.example.invalid/wheels/",
            "PIP_CONFIG_FILE": str(config),
        }):
            code, boot_out, err = self.bootstrap(worktree, "--no-project")
            self.assertEqual(code, 0, err)
            code, fp_out, fp_err = self.run_cli("fingerprint", "--worktree", worktree, "--json")
            self.assertEqual(code, 0, fp_err)
            code, verify_out, verify_err = self.run_cli(
                "verify", "--worktree", worktree, "--no-project", "--json"
            )
            self.assertEqual(code, 0, verify_err)
        report = json.loads(boot_out)
        manifest = (self.cache / "wheels" / report["cache_fingerprint"] / "manifest.json").read_text()
        marker = (worktree / "tools" / "llmtool" / ".venv" / ".worktree-env.json").read_text()
        for label, blob in (("manifest", manifest), ("marker", marker), ("bootstrap", boot_out + err),
                            ("fingerprint", fp_out + fp_err), ("verify", verify_out + verify_err)):
            for secret in secrets:
                with self.subTest(where=label, secret=secret):
                    self.assertNotIn(secret, blob)
        self.assertIn("https://pypi.example.invalid/simple", manifest)
        self.assertIn("https://cfg.example.invalid/simple", marker)

    def test_failed_venv_creation_is_rolled_back_and_retry_succeeds(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        venv = worktree / "tools" / "llmtool" / ".venv"

        def partial_venv(*args, **kwargs):
            (venv / "bin").mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text("home = /nowhere\n", encoding="utf-8")
            raise we.EnvError("VENV_FAILED", "simulated partial venv", 5)

        with mock.patch.object(we, "create_venv", side_effect=partial_venv):
            code, out, err = self.bootstrap(worktree, "--no-project")
        self.assertEqual(code, 5)
        self.assertIn("VENV_FAILED", err)
        self.assertFalse(venv.exists(), "a partially created venv must be rolled back")
        code, out, err = self.bootstrap(worktree, "--no-project")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "env_created")

    def test_usage_errors_exit_4_and_global_json_still_works(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
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
        first = self.make_worktree(self.tmp / "wt-a", "source-A")
        second = self.make_worktree(self.tmp / "wt-b", "source-B")

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
        first = self.copy_bootstrapped_worktree(self.tmp / "wt-a", "source-A")
        third = self.make_worktree(self.tmp / "wt-c", "source-C")

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
    def test_symlinked_package_directory_is_rejected_as_foreign_source(self):
        worktree = self.copy_bootstrapped_worktree(self.tmp / "wt", "source-A")
        package_dir = worktree / "tools" / "llmtool" / "llmtool"
        foreign_package = self.tmp / "foreign-package"
        shutil.move(str(package_dir), str(foreign_package))
        os.symlink(str(foreign_package), str(package_dir))

        code, out, err = self.run_cli("verify", "--worktree", worktree, "--json")

        self.assertEqual(code, 3)
        self.assertIn("UNSAFE_SOURCE", err)

    @needs_editable_pip
    def test_symlinked_package_init_is_rejected_as_foreign_source(self):
        worktree = self.copy_bootstrapped_worktree(self.tmp / "wt", "source-A")
        package_init = worktree / "tools" / "llmtool" / "llmtool" / "__init__.py"
        foreign_init = self.tmp / "foreign-init.py"
        shutil.move(str(package_init), str(foreign_init))
        os.symlink(str(foreign_init), str(package_init))

        code, out, err = self.run_cli("verify", "--worktree", worktree, "--json")

        self.assertEqual(code, 3)
        self.assertIn("UNSAFE_SOURCE", err)

    @needs_editable_pip
    def test_symlinked_venv_is_rejected(self):
        first = self.copy_bootstrapped_worktree(self.tmp / "wt-a", "source-A")
        second = self.make_worktree(self.tmp / "wt-b", "source-B")
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
        worktree = self.make_worktree(self.tmp / "wt", "A")
        code, out, err = self.run_cli("verify", "--worktree", worktree, "--json")
        self.assertEqual(code, 3)
        self.assertIn("VENV_MISSING", err)

    def test_worktree_without_lock_is_rejected(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
        (worktree / "tools" / "llmtool" / "worktree-env.lock").unlink()
        code, out, err = self.run_cli("fingerprint", "--worktree", worktree, "--json")
        self.assertEqual(code, 4)
        self.assertIn("MISSING_LOCK", err)


    def test_symlinked_project_directory_is_rejected(self):
        worktree = self.make_worktree(self.tmp / "wt", "A")
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


def tearDownModule():
    if EnvironmentTempCase._shared_fixture_root is not None:
        _chmod_writable(EnvironmentTempCase._shared_fixture_root)
        shutil.rmtree(str(EnvironmentTempCase._shared_fixture_root), ignore_errors=True)
        EnvironmentTempCase._shared_fixture_root = None


if __name__ == "__main__":
    unittest.main()
