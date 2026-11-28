# A13.1 Design Note: Canonical Root, Worktree Boundary, D6 Environment, and Repository/Origin Verification

Status: **Proposed design (docs only)** — nothing in this note is implemented.
Source basis: `tools/llmtool/scripts/d6_worktree_lifecycle.py` and `tools/llmtool/scripts/d6_worktree_env.py` at `30adfab962aae6a029c063ce872709444f46fcda`.

Related boundaries and milestone scope are linked rather than restated:
- A13.1 milestone scope, exit evidence, and explicit-context rules: [ROADMAP.md](../../tools/llmtool/ROADMAP.md).
- `.git` common-directory basename boundary (`D6_COMMON_DIR_INVALID` in `d6_worktree_lifecycle.py` and `REVIEW_REGISTRATION_INVALID` in `review_authority.py`): [a13-git-layout-boundary.md](a13-git-layout-boundary.md).
- State-location defaults and A13.1/A13.3 handoff: [a13-state-location-boundary.md](a13-state-location-boundary.md).

Authorization boundary: real external repository writes and external GitHub mutations require separate Owner authorization.

---

## 1. Canonical Root and Worktree Boundary

### Current baseline

In `d6_worktree_lifecycle.py` (`_load_trust`) and `d6_worktree_env.py` (`_load_trust`), D6 reads a single global `~/.config/llmtool/trust.json` containing `canonical_root`, `workspace_root`, `tasks_root`, and `repository` (`slug`, `allowed_origins`, `approval_refs`).

- `d6_worktree_lifecycle.py` (`create_worktree`, `record_result`, `bind_native_session`, `rebind_device_tasks`, `retire_worktrees`, `reconcile_registry`, `lease_status_report`, `release_expired_lease`, `release_retained_records`, `owner_approved_retain_release`, `_main`) binds operations to `trust["canonical_root"]` and `trust["tasks_root"]`.
- `resume_worktree` in `d6_worktree_lifecycle.py` derives `common_dir` from `trust["canonical_root"]` and checks `_require_trust_binding`.
- `_rotated_record_root` in `d6_worktree_lifecycle.py` enforces that a worktree root is named after `task_id`, belongs to `common_dir`, and is outside `canonical_root` (neither equal to, inside, nor an ancestor of `canonical_root`).
- `validate_registered_worktree` in `d6_worktree_env.py` verifies `root` realpath, `rev-parse --show-toplevel`, `--git-common-dir`, owner-only `0o700` permissions, `physical_identity` (`device`, `inode`, `uid`, `mode`), `LOCKED` worktree registration, and `common_dir / "llmtool-worktrees"` registry consistency.

### Proposed A13.1 boundary

- **Explicit project context:** Replace the single global `trust.json` lookup with explicit per-project registration resolution for `canonical_root`, project-scoped `tasks_root`, and `common_dir`. Legacy single-project `trust.json` self-development is preserved only through explicit migration or explicit selection, never as an implicit fallback when project context is missing or ambiguous.
- **Unchanged isolation invariants:** Worktree `root` remains at the selected project's `tasks_root / task_id` outside `canonical_root`, with owner-only `0o700` permissions, verified `physical_identity`, `LOCKED` Git worktree registration, and the `.git` common-dir boundary linked above.
- **Fail-closed context check (proposed):** Unregistered targets, missing project selection, or ambiguous canonical-root/worktree context must refuse the action (proposed refusal code: `D6_PROJECT_CONTEXT_INVALID`) rather than defaulting to `trust["canonical_root"]`.

---

## 2. D6 Environment Boundary and Bootstrap Source Semantics

### 2.1 Separating llmtool tooling trust from target project trust

- **Current behaviour:** `_trusted_dispatcher_git` in `d6_worktree_lifecycle.py` calls `_load_trust()`, sets `dispatcher_root = Path(trust["canonical_root"])`, verifies that `d6_worktree_lifecycle.py` and `d6_worktree_env.py` match `HEAD` in a clean `dispatcher_root` whose `origin` is in `trust["origins"]`, and otherwise refuses `ENV_BOOTSTRAP_SOURCE_UNTRUSTED`. `_bootstrap` and `_approved_task_source` also resolve `d6_worktree_env.py` from `dispatcher_root` (or `env2_approved_tooling_sha` in `dispatcher_root`'s object store).
- **Problem for external projects:** This conflates **llmtool tooling/installation trust** (proving the `d6_worktree_lifecycle.py` and `d6_worktree_env.py` code being executed is authentic) with **target project trust** (the registered external repository being developed).
- **Proposed separation:**
  - **llmtool tooling trust** verifies the llmtool installation or Integrator checkout supplying `d6_worktree_lifecycle.py` and `d6_worktree_env.py`.
  - **Target project trust** verifies the selected registered project (`canonical_root`, `tasks_root`, `allowed_origins`, target branch, and project policy).
  - Neither trust domain may substitute for or be inferred from the other.

### 2.2 `d6_worktree_env.py` trust loading, digest comparison, and origin check

- **Current behaviour:** `d6_worktree_env.py` (`main`, `validate_registered_worktree`, `_invoke_bootstrap`) calls `_load_trust()`, which reads only the legacy `~/.config/llmtool/trust.json`. `main` compares `--trust-sha256` passed by `_bootstrap` in `d6_worktree_lifecycle.py` against `_load_trust()["sha256"]` (`TRUST_CHANGED_DURING_RUN`). `validate_registered_worktree` checks `origin not in trust["origins"]` and refuses with `D6_SOURCE_BINDING_MISMATCH`.
- **Unchanged:**
  - Fail-closed parent-to-child trust-digest verification (`TRUST_CHANGED_DURING_RUN` when the trust record changes between parent and child).
  - Fail-closed foreign-origin refusal (`D6_SOURCE_BINDING_MISMATCH` in `validate_registered_worktree` when the worktree's `origin` is not in the authorized origins).
- **Proposed change:** `d6_worktree_env.py` must be updated in lockstep with `d6_worktree_lifecycle.py` so both resolve the **selected registered project's** trust/registration record (rather than reading only legacy `trust.json`), compare the parent-passed project-trust digest (`--project-trust-sha256`, **proposed**, alongside or superseding `--trust-sha256` for registered projects), and validate `origin` against the selected project's `allowed_origins`.

### 2.3 Meaning of `BOOTSTRAP_SOURCES` and `_validate_bootstrap_sources` for external projects

- **Current behaviour:**
  - `_validate_bootstrap_sources` in `d6_worktree_env.py` requires the target worktree `root` to contain committed blobs at `tooling_sha` (matching on-disk files with no group/world write bits) for `tools/llmtool/Makefile`, `tools/llmtool/.gitignore`, `tools/llmtool/scripts/worktree_env.py`, `tools/llmtool/worktree-env.lock`, `tools/llmtool/pyproject.toml`, `tools/llmtool/scripts/d6_worktree_env.py`, and optional build inputs (`setup.py`, `setup.cfg`, `MANIFEST.in`), refusing `D6_BOOTSTRAP_SOURCE_MISMATCH` otherwise.
  - `_invoke_bootstrap` in `d6_worktree_env.py` checks `root/tools/llmtool/Makefile` and `root/tools/llmtool/scripts/worktree_env.py` (`ENV_BOOTSTRAP_SOURCE_MISSING`), runs `make -C tools/llmtool env-bootstrap`, and writes `tools/llmtool/.d6-bootstrap-report.json` (`_atomic_bootstrap_report`).
  - `d6_worktree_lifecycle.py` defines `BOOTSTRAP_SOURCES` and `RETENTION_PATHS` under `tools/llmtool/`, intersects `write_paths` with `BOOTSTRAP_SOURCES` in `record_result` (`env2_source_allowance`), and checks `root/tools` and `root/tools/llmtool` in `_validate_record_result_report_path` (`D6_RECORD_RESULT_ENV2_MARKER_INVALID`).
- **Meaning for self-development (unchanged):** In `davidcattoolsproject`, `tools/llmtool/*` are target-repository files under development, so `_validate_bootstrap_sources` and `make env-bootstrap` verify and build the repo-local environment inside the worktree.
- **Meaning for a registered external project:** An external repository does not contain `tools/llmtool/*`. Applying `BOOTSTRAP_SOURCES` or `_validate_bootstrap_sources` to an external repository would refuse every external project with `D6_BOOTSTRAP_SOURCE_MISMATCH` (and fail `_invoke_bootstrap` and `_validate_record_result_report_path`). Therefore:
  - Target-repo `BOOTSTRAP_SOURCES` verification is **not** tooling trust and must **not** be applied to registered external projects.
  - `d6_worktree_env.py` and the llmtool runtime are supplied by the trusted llmtool installation, not by the external repository.
  - `record_result`, `_validate_record_result_report_path`, and `RETENTION_PATHS` must not require `root/tools/llmtool/` inside an external worktree.
- **Fail-closed rule until external bootstrap is implemented (proposed):** Because an external-project environment-bootstrap contract is not yet implemented, any unconfigured or unsupported external-project ENV-2 bootstrap request must refuse the action (proposed refusal code: `ENV_BOOTSTRAP_EXTERNAL_UNSUPPORTED`) rather than falling back to `tools/llmtool/Makefile`, skipping validation, or reporting PASS.

---

## 3. Repository and Origin Verification

### Current baseline

- `d6_worktree_lifecycle.py` verifies `rev-parse --show-toplevel` (`D6_REPOSITORY_INVALID`), `.git` common-dir (`D6_COMMON_DIR_INVALID`), and `remote get-url origin in trust["origins"]` (`D6_ORIGIN_INVALID` in `create_worktree`, `rebind_device_tasks`, `retire_worktrees`, `release_retained_records`, `owner_approved_retain_release`; `D6_COMMON_DIR_INVALID` in `reconcile_registry`, `_lease_registry`).
- `d6_worktree_env.py` (`validate_registered_worktree`) verifies `rev-parse --show-toplevel` (`D6_ROOT_MISMATCH`), `.git` common-dir (`D6_COMMON_DIR_INVALID`), `origin in trust["origins"]`, `branch`, `head_sha`, and `base_sha` ancestry (`D6_SOURCE_BINDING_MISMATCH`).
- `_publication` and `_check_merge_evidence` in `d6_worktree_lifecycle.py` hardcode `refs/remotes/origin/main` and `refs/remotes/origin/`.
- `OWNER_APPROVED_RETAIN_RELEASE_RE` and `OWNER_APPROVED_DISCARD_RE` in `d6_worktree_lifecycle.py` hardcode `davidcatcat/davidcattoolsproject`, whereas `_retain_release_re` derives the repository slug from `trust["slug"]`.

### Proposed A13.1 verification contract

- **Unchanged:** Fail-closed refusal on foreign or mismatched `origin` (`D6_ORIGIN_INVALID` / `D6_SOURCE_BINDING_MISMATCH`), mismatched Git toplevel (`D6_REPOSITORY_INVALID` / `D6_ROOT_MISMATCH`), invalid common-dir (`D6_COMMON_DIR_INVALID`), rotated or mismatched trust binding (`D6_TRUST_ROTATED`), and cross-project registry access.
- **Proposed changes:**
  - Validate `canonical_root`, `origin`, `slug`, and registry `trust_binding_sha256` against the **selected registered project** on every mutating D6 operation and in `d6_worktree_env.py` `validate_registered_worktree`.
  - Parameterize `_publication` and `_check_merge_evidence` by the registered project's configured target branch (refusing if the target branch is missing or unconfigured) instead of assuming `origin/main`.
  - Parameterize `OWNER_APPROVED_RETAIN_RELEASE_RE` and `OWNER_APPROVED_DISCARD_RE` by the selected project's `slug` (matching `_retain_release_re`), or refuse when unconfigured.

---

## 4. Open Questions

1. **OPEN — Project registration schema and digest binding:** Exact schema, storage path, and migration path for per-project registration/trust records (and how `trust_binding_sha256` and proposed `--project-trust-sha256` bind to it).
2. **OPEN — Installed llmtool tooling trust (A13.4+):** How `_trusted_dispatcher_git` (or its replacement) verifies `d6_worktree_lifecycle.py` and `d6_worktree_env.py` when llmtool is run from an installed npm/Python package rather than a clean Integrator Git checkout.
3. **OPEN — External-project ENV-2 mode and report/retention paths:** Whether external projects use a project-configured environment bootstrapper or an explicit no-llmtool-venv mode, and where `.d6-bootstrap-report.json` and `RETENTION_PATHS` are placed when `tools/llmtool/` is absent from the external repository.
4. **OPEN — `.git` common-dir layout parity:** Whether `.git` basename enforcement (`D6_COMMON_DIR_INVALID` / `REVIEW_REGISTRATION_INVALID`) stays unchanged or is extended with coordinated parity across D6 and review registration.

---

## 5. Gates

- Exact-head check (docs).
- Formal review by a reviewer of another model family.
