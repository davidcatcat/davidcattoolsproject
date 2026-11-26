# A13 npm Launcher Plan: Offline-Testable Slices for Home LLM (`gw-agent`)

- **Status:** Design plan only (no code, CI, or rule changes).
- **Priority:** A13 stays behind A11c (`tools/llmtool/ROADMAP.md`); implementation dispatches only after A11c closure or explicit PM/Owner scheduling.
- **Scope:** Offline-testable subset of Issue #396 (A13.0–A13.3 foundations) using the npm thin-launcher direction. Windows is not supported.

---

## 1. Distribution Direction (P1)

1. **Thin npm launcher, Python core:** `llmtool` remains a Python package (`tools/llmtool/pyproject.toml`). A new npm package in `tools/llmtool-npm/` provides only a thin Node.js CLI launcher (`bin/llmtool.js`).
2. **First-run bootstrap:** On first run (not during `npm install`), the launcher locates a `python3` binary meeting the project minimum (`>=3.9` from `tools/llmtool/pyproject.toml`), creates a dedicated virtual environment in the user data directory (`~/.local/share/llmtool-npm/venv` on Linux/macOS), installs `llmtool` into it, and hands over execution (`exec`/spawn with inherited stdio and forwarded `argv`) to `<venv>/bin/llmtool`.
3. **Pinned, sha256-verified, and offline-installable source:** A dedicated staging slice (S7) builds the `llmtool` wheel (`0.1.0`) and stages it alongside exact-pinned dependency wheels (such as `PyYAML` from `tools/llmtool/pyproject.toml`) into `tools/llmtool-npm/vendor/` (bundled inside the npm package, or a local `--wheelhouse` directory), recording exact filenames, versions, and `sha256` digests in `tools/llmtool-npm/manifest.json` and `tools/llmtool-npm/requirements.lock`. On first run, the launcher verifies every staged wheel's `sha256` against `manifest.json` and installs into the venv with `pip install --no-index --only-binary=:all: --require-hashes -r requirements.lock` (never fetching from PyPI or invoking `setuptools` build-time downloads).

---

## 2. Hard Constraints (P2)

- **No npm lifecycle scripts:** `package.json` must not define `preinstall`, `install`, or `postinstall` scripts.
- **No unpinned downloads:** Every installed `llmtool` wheel and transitive dependency (e.g., `PyYAML>=6.0,<7` in `tools/llmtool/pyproject.toml`) must be exact-pinned in `manifest.json` and `requirements.lock` with `sha256` digests; `pip` runs strictly with `--no-index --only-binary=:all: --require-hashes` against `tools/llmtool-npm/vendor/` so neither runtime dependencies nor `setuptools>=68` build-system packages can be downloaded.
- **No credential handling:** Launcher and bootstrap code must never read, store, log, or forward API keys, tokens, or `.env` secrets.
- **No publishing, signing, or notarizing:** No `npm publish`, PyPI upload, GitHub Release, codesign, or notarization steps.
- **No network calls in tests:** All unit and integration tests run offline against local temp directories, mock executables, and local fixture archives.
- **No rule-file changes:** `AGENTS.md`, `CLAUDE.md`, `.claude/skills/**`, `worker-dispatch.json`, and `sensitive-paths.json` remain untouched.

---

## 3. Existing Components & Version Audit (P4)

**Do not rebuild:**
- `tools/llmtool/llmtool/doctor.py` (`doctor_output()`, `run_doctor()`): already implements offline config, executable-presence, and read-only diagnostics inspection.
- `tools/llmtool/llmtool/__init__.py` (`_source_version()`, `_distribution_version()`): already resolves `__version__` from `importlib.metadata` or `tools/llmtool/pyproject.toml`.
- `tools/llmtool/pyproject.toml`: already defines `name = "llmtool"`, `version = "0.1.0"`, `requires-python = ">=3.9"`, dependencies, and `llmtool.entrypoint:main`.
- `docs/design/a13-trust-json-design-369.md`: existing `trust.json` design for replacing hardcoded trust anchors (tracked separately).

**Version string audit:**
- `tools/llmtool/pyproject.toml` declares `version = "0.1.0"`, and `tools/llmtool/llmtool/__init__.py` (`_source_version()`) parses that same file dynamically. `docs/design/gateway-install-2310.md` also records `0.1.0` in both `tools/api-agent-gateway/pyproject.toml` and `api_agent_gateway/__init__.py`. **No version string mismatch** was found in the read files.
- *Portability note:* `tools/llmtool/pyproject.toml` sets `requires-python = ">=3.9"`, while `docs/design/a13-trust-json-design-369.md` lists `"Linux Python 3.9"` in `not_run_checks` and targets Python 3.13/3.14 in CI.

---

## 4. `doctor` Extensions for Node, Python, and Venv (P5)

Extend `tools/llmtool/llmtool/doctor.py` (`doctor_output()`) with read-only, offline runtime checks (never invoking network or printing secret paths on error):
- **Node check:** Locate `node` via `shutil.which("node")` with `os.path.isabs(...)`; report `node=available` or `node=missing`.
- **Python check:** Verify `sys.version_info >= (3, 9)` (matching `tools/llmtool/pyproject.toml`) and executable presence; report `python=ok` or `python=unsupported`.
- **Virtualenv check:** Inspect `~/.local/share/llmtool-npm/venv` (read-only `stat`/`is_file` on `pyvenv.cfg` and `bin/llmtool`); report `venv=present`, `venv=incomplete`, or `venv=missing`.

---

## 5. Slice Plan (P3 & P5)

All `gw-agent` slices live in `tools/llmtool-npm/` (outside `tools/llmtool/sensitive-paths.json`) or touch only `tools/llmtool/llmtool/doctor.py`. Every `gw-agent` slice keeps total `read_paths` + `write_paths` well under **12 KiB** so the files plus task packet stay within the **24,576-byte** ceiling.

### 5.1 Home LLM (`gw-agent`) Slices

| Slice | Tier | Read paths | Write paths | Size ceiling (files + packet) | Verification (offline) |
|---|---|---|---|---|---|
| **S1: Package & manifest** | `gw-agent` (small) | `tools/llmtool/pyproject.toml` | `tools/llmtool-npm/package.json`, `tools/llmtool-npm/manifest.json`, `tools/llmtool-npm/requirements.lock` | <= 4 KiB files (<= 24,576 B total) | `node -e` JSON schema, lockfile format, & no-lifecycle-script check; `version == 0.1.0` |
| **S2: Python finder** | `gw-agent` (small) | `tools/llmtool/pyproject.toml`, `tools/llmtool-npm/manifest.json` | `tools/llmtool-npm/lib/python_finder.js`, `tools/llmtool-npm/tests/python_finder.test.js` | <= 6 KiB files (<= 24,576 B total) | `node --test` with mock `python3` scripts (`3.8` rejected, `>=3.9` accepted) |
| **S3: Sha256 verifier** | `gw-agent` (small) | `tools/llmtool-npm/manifest.json` | `tools/llmtool-npm/lib/verify_hash.js`, `tools/llmtool-npm/tests/verify_hash.test.js` | <= 6 KiB files (<= 24,576 B total) | `node --test` with temp fixture files (valid hash passes, mismatch/missing fails closed) |
| **S4: Venv bootstrapper** | `gw-agent` (small) | `tools/llmtool-npm/manifest.json`, `tools/llmtool-npm/requirements.lock`, `tools/llmtool-npm/lib/verify_hash.js` | `tools/llmtool-npm/lib/venv_bootstrap.js`, `tools/llmtool-npm/tests/venv_bootstrap.test.js` | <= 8 KiB files (<= 24,576 B total) | `node --test` with fake `python3`/`pip` asserting `--no-index --only-binary=:all: --require-hashes` & temp data dir (idempotent, no network) |
| **S5: Thin CLI launcher** | `gw-agent` (small) | `tools/llmtool-npm/package.json`, `tools/llmtool-npm/lib/python_finder.js`, `tools/llmtool-npm/lib/venv_bootstrap.js` | `tools/llmtool-npm/bin/llmtool.js`, `tools/llmtool-npm/tests/launcher.test.js` | <= 10 KiB files (<= 24,576 B total) | `node --test` verifying argv forwarding, exit code propagation, and no credential env injection |
| **S6: Doctor runtime checks** | `gw-agent` (small) | `tools/llmtool/llmtool/doctor.py`, `tools/llmtool/pyproject.toml` | `tools/llmtool/llmtool/doctor.py`, `tools/llmtool/tests/test_doctor_runtime.py` | <= 10 KiB files (<= 24,576 B total) | `pytest` on `doctor_output()` with monkeypatched `shutil.which`, `sys.version_info`, and temp venv |

### 5.2 Non-Home-LLM Slices (AGY / OpenCode)

Slices below require `AGY` or `OpenCode` due to multi-file state transitions or end-to-end acceptance harnesses. No slice reads or writes oversized files (`tools/llmtool/llmtool/cli.py` and `.github/workflows/llmtool-mac-ci.yml` exceed 24,576 bytes and are excluded; Python-side `cli.py` wiring or `.github/workflows/` edits must wait until those files are split or handled outside the 24,576-byte ceiling).

| Slice | Tier | Read paths | Write paths | Size ceiling (files + packet) | Verification (offline) |
|---|---|---|---|---|---|
| **S7: Archive & lockfile staging** | `AGY` / `OpenCode` (medium) | `tools/llmtool/pyproject.toml`, `tools/llmtool-npm/manifest.json`, `tools/llmtool-npm/requirements.lock` | `tools/llmtool-npm/scripts/stage_artifacts.sh`, `tools/llmtool-npm/manifest.json`, `tools/llmtool-npm/requirements.lock`, `tools/llmtool-npm/tests/stage_artifacts.test.js` | <= 10 KiB files (<= 24,576 B total) | `node --test` verifying staged `vendor/` wheel naming, `sha256` updates in `manifest.json`/`requirements.lock`, and `--no-index` installability from local wheelhouse |
| **S8: Launcher lifecycle commands** | `AGY` / `OpenCode` (medium) | `tools/llmtool-npm/package.json`, `tools/llmtool-npm/manifest.json`, `tools/llmtool-npm/lib/verify_hash.js`, `tools/llmtool-npm/lib/venv_bootstrap.js`, `tools/llmtool-npm/bin/llmtool.js` | `tools/llmtool-npm/lib/lifecycle.js`, `tools/llmtool-npm/bin/llmtool.js`, `tools/llmtool-npm/tests/lifecycle.test.js` | <= 12 KiB files (<= 24,576 B total) | `node --test` verifying `update`, `uninstall`, and `rollback` (staging backup, hash check, clean removal) in temp dirs |
| **S9: Offline CI runner & clean-machine harness** | `AGY` / `OpenCode` (medium) | `tools/llmtool-npm/package.json`, `tools/llmtool-npm/manifest.json`, `tools/llmtool-npm/bin/llmtool.js`, `tools/llmtool-npm/lib/lifecycle.js` | `tools/llmtool-npm/scripts/ci_check.sh`, `tools/llmtool-npm/tests/clean_machine.test.js` | <= 12 KiB files (<= 24,576 B total) | `sh tools/llmtool-npm/scripts/ci_check.sh` against isolated temp `HOME` and local `vendor/` fixture archive (no network, no lifecycle scripts) |

---

## 6. Needed CI Job (Not Added Here) (P5)

- **Job name:** `check (npm-launcher)` (needed in Linux and macOS CI workflows; not added here and excluded from S1–S9 because `.github/workflows/` is in `tools/llmtool/sensitive-paths.json` and `llmtool-mac-ci.yml` exceeds 24,576 bytes).
- **Commands:** Run `sh tools/llmtool-npm/scripts/ci_check.sh` (which executes `node --test tools/llmtool-npm/tests/*.test.js` and asserts `package.json` has no `preinstall`/`install`/`postinstall` scripts) and `pytest tools/llmtool/tests/test_doctor_runtime.py` offline with no network calls.
