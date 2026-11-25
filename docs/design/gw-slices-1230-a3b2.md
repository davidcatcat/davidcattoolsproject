# #1230 A3 B2 Step 2 (Slices 3–5) — Executable Split Plan and `gw-agent` Slice Assessment

Facts in this plan come only from the files in this task's `read_paths` at `source_sha` `f1b1b566108b26f5d7869e5bd0bf612c483f2b85` and the quoted PM/Advisor request. Exact byte counts via `wc -c` or `git ls-tree -l` could not be run in this sandbox because `run_command` failed (`agentapi: operation not permitted`); where exact measurement was unavailable, this plan states a proven lower bound from `read_file` line/content observations and marks external files outside `read_paths` as **UNKNOWN**. **Docs only:** this document is the only file created; no code or test is changed. Gates: `check (docs)` CI and formal review by `claude-reviewer`.

---

## 1. Tier Limits and Evaluation Rules (`tools/llmtool/scripts/spec_preflight.py`)

- **`LADDER_SIZES`** (`(files written, context bytes, gw-agent turns)` in `tools/llmtool/scripts/spec_preflight.py`):
  - T1: `(1, 1536, 6)`
  - T2: `(1, 3072, 10)`
  - T3: `(2, 6144, 12)`
  - T4: `(3, 10240, 16)`
  - **T5: `(4, 16384, 16)`** (`gw-agent` cap: at most `4` written files, at most `16,384` context bytes counting `read_bytes + packet_bytes`, at most `16` turns)
  - T6: `(5, 24576, 16)` (`LADDER_MAX_TIER = 6`)
- **Turn formula** (`_match_tier` / `spec_tier` in `tools/llmtool/scripts/spec_preflight.py`): `turns = len(reads) + 2 * len(writes) + 1`.
- **New file floor** (`NEW_FILE_MIN_TIER = 4` in `tools/llmtool/scripts/spec_preflight.py`): writing any path that does not yet exist in the repo requires at least **T4**.
- **Large-file refusal** (`DEFAULT_LARGE_FILE_LINES = 2000` in `large_file_lines` and `main`, `tools/llmtool/scripts/spec_preflight.py`): for `gw-agent`, any written file with `>= 2000` newline bytes is refused with `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1`.
- **Read-scope rule** (`check_read_scope` in `tools/llmtool/scripts/spec_preflight.py`): writing any `.py` file under `tools/` or `scripts/` (excluding `tools/llmtool/tests/`) requires at least one `test_*.py` module in `read`.

---

## 2. Legacy Constant Inventory for Slices 3 and 4 (P1)

### 2.1 Slice 3 — `tools/llmtool/llmtool/task_completion.py`

In `tools/llmtool/tests/test_owner_literal_guard.py`, `ALLOWLIST` lists two `LEGACY_VERIFY` entries (three lines) for `tools/llmtool/llmtool/task_completion.py`, alongside related legacy CI constants in module scope:

| Constant (`task_completion.py`) | `ALLOWLIST` label | Production users in `task_completion.py` | External production / test users | Status after Slices 1–2 | Action & `trust.json` source |
|---|---|---|---|---|---|
| `_MAC_CHECK` | Not in `ALLOWLIST` | None in `task_completion.py` (used in `_REQUIRED_CHECKS`) | External callers **UNKNOWN** (outside `read_paths`) | Kept per Advisor ruling | **Keep** (Advisor ruling 2026-11-26) |
| `_GITHUB_ACTIONS_APP_ID` | Not in `ALLOWLIST` | None in `task_completion.py` | External callers **UNKNOWN** (outside `read_paths`) | Kept per Advisor ruling | **Keep** (Advisor ruling 2026-11-26; pinned to `15368` in `trust.py` `_validate_schema`) |
| `_CI_RUNNER_NAMES` | `LEGACY_VERIFY` | None (`_trusted_ci_runner` and `_ci_trust` read `trust.json`) | `trust_cli.py` (`verify-legacy`) and `test_task_completion.py` / `test_trust_cli.py` are outside `read_paths` (**UNKNOWN**) | Dead in `task_completion.py`; kept only for `verify-legacy` per module comment | Delete in Slice 3 once `verify-legacy` is removed or updated; replaced by `ci_trust.workflows[].runner_names` via `_ci_trust` |
| `_LINUX_CI_RUNNER_NAMES` | `LEGACY_VERIFY` (2 lines) | None (`_trusted_ci_runner` and `_ci_trust` read `trust.json`) | Same as above (**UNKNOWN** outside `read_paths`) | Dead in `task_completion.py` | Delete in Slice 3; replaced by `ci_trust.workflows[].runner_names` via `_ci_trust` |
| `_LINUX_CHECK`, `_DOCS_CHECK`, `_REQUIRED_CHECKS`, `_NOT_RUN_CHECKS`, `_CI_WORKFLOW_PATH`, `_CI_WORKFLOW_NAME`, `_LINUX_CI_WORKFLOW_PATH`, `_LINUX_CI_WORKFLOW_NAME` | Not in `ALLOWLIST` | None (`_not_run_checks` and `_ci_trust` read `trust.json`) | Same as above (**UNKNOWN** outside `read_paths`) | Dead in `task_completion.py` | Delete in Slice 3; replaced by `ci_trust.workflows[]` and `ci_trust.not_run_checks` via `_ci_trust` |

### 2.2 Slice 4 — `tools/llmtool/scripts/d6_worktree_lifecycle.py`

In `tools/llmtool/tests/test_owner_literal_guard.py`, `ALLOWLIST` lists five `LEGACY_VERIFY` entries and two `RETAIN_RELEASE_REFS` entries for `tools/llmtool/scripts/d6_worktree_lifecycle.py`:

| Constant (`d6_worktree_lifecycle.py`) | `ALLOWLIST` label | Production users in `d6_worktree_lifecycle.py` | External production / test users | Status after Slices 1–2 | Action & `trust.json` source |
|---|---|---|---|---|---|
| `TASKS_ROOT` | `LEGACY_VERIFY` | None (`create_worktree`, `record_result`, `bind_native_session`, `rebind_device_tasks`, `retire_worktrees`, `reconcile_registry`, `release_retained_records`, `owner_approved_retain_release`, `release_expired_lease` read `trust["tasks_root"]`) | `trust_cli.py` (`verify-legacy`) and `test_d6_worktree_lifecycle.py` / `test_trust_cli.py` are outside `read_paths` (**UNKNOWN**) | Dead in `d6_worktree_lifecycle.py` | Delete in Slice 4; replaced by `trust["tasks_root"]` (`tasks_root` in `trust.json`) |
| `ORIGINS` | `LEGACY_VERIFY` (2 lines) | None (`_trusted_dispatcher_git`, `create_worktree`, `rebind_device_tasks`, `retire_worktrees`, `reconcile_registry`, `_lease_registry`, `release_retained_records`, `owner_approved_retain_release` read `trust["origins"]`) | Same as above (**UNKNOWN** outside `read_paths`) | Dead in `d6_worktree_lifecycle.py` | Delete in Slice 4; replaced by `trust["origins"]` (`repository.allowed_origins` in `trust.json`) |
| `D6_INTEGRATOR_DISPATCHER_ROOT` | `LEGACY_VERIFY` | None (`_trusted_dispatcher_git`, `create_worktree`, `resume_worktree`, `_main` read `trust["canonical_root"]`) | Same as above (**UNKNOWN** outside `read_paths`) | Dead in `d6_worktree_lifecycle.py` | Delete in Slice 4; replaced by `trust["canonical_root"]` (`canonical_root` in `trust.json`) |
| `OWNER_APPROVAL_RE` | `LEGACY_VERIFY` | None (`_parse_release_retain` and `release_retained_records` use `_retain_release_re(trust)`) | Same as above (**UNKNOWN** outside `read_paths`) | Dead in `d6_worktree_lifecycle.py` | Delete in Slice 4; replaced by `_retain_release_re(trust)` using `trust["slug"]` and `trust["retain_release_issues"]` |
| `OWNER_APPROVED_RETAIN_RELEASE_RE` | `RETAIN_RELEASE_REFS` | **STILL READ** by `_parse_owner_approved_retain_release`, `owner_approved_retain_release`, and `OWNER_APPROVED_DISCARD_RE` (via `.pattern.replace`) | Outside `read_paths` (**UNKNOWN**) | **Still read** (not `LEGACY_VERIFY`) | **Do not delete** without migrating callers to `trust["slug"]` and `trust["retain_release_issues"]` (`_retain_release_re(trust)` for retain-release; `trust["slug"]` + `r"/issues/[0-9]+#issuecomment-[0-9]+\Z"` for discard) |
| Pattern inside `_retain_release_re` | `RETAIN_RELEASE_REFS` | **STILL READ** by `_parse_release_retain`, `release_retained_records` | Same | **Still read** (already uses `trust["slug"]` and `trust["retain_release_issues"]`) | **Keep** (generic `trust.json` builder) |

---

## 3. Executable Split Plan (Slices 3–5)

### Slice 3 — Remove dead legacy CI constants from `tools/llmtool/llmtool/task_completion.py`

- **Verifiable result:** Remove `_LINUX_CHECK`, `_DOCS_CHECK`, `_REQUIRED_CHECKS`, `_NOT_RUN_CHECKS`, `_CI_RUNNER_NAMES`, `_CI_WORKFLOW_PATH`, `_CI_WORKFLOW_NAME`, `_LINUX_CI_WORKFLOW_PATH`, `_LINUX_CI_WORKFLOW_NAME`, and `_LINUX_CI_RUNNER_NAMES` from `tools/llmtool/llmtool/task_completion.py` (keeping `_MAC_CHECK` and `_GITHUB_ACTIONS_APP_ID`), remove the three `_TASK_COMPLETION` `LEGACY_VERIFY` entries from `ALLOWLIST` in `tools/llmtool/tests/test_owner_literal_guard.py`, and pass `test_owner_literal_guard.py` and `test_task_completion.py`.
- **Write:** `tools/llmtool/llmtool/task_completion.py`, `tools/llmtool/tests/test_owner_literal_guard.py` (and `tools/llmtool/tests/test_task_completion.py` if any test imports the deleted constants).
- **Read:** `docs/design/a13-trust-json-design-369.md`, `tools/llmtool/llmtool/task_completion.py`, `tools/llmtool/llmtool/trust.py`, `tools/llmtool/tests/test_owner_literal_guard.py`, `tools/llmtool/tests/test_task_completion.py` (**UNKNOWN** size, outside `read_paths`), `tools/llmtool/llmtool/task_completion_part4.py` (**UNKNOWN** size, outside `read_paths`), `tools/llmtool/llmtool/trust_cli.py` (**UNKNOWN** size, outside `read_paths`), `tools/llmtool/tests/test_trust_cli.py` (**UNKNOWN** size, outside `read_paths`).
- **Unique read bytes & method:** Exact `wc -c` / `git ls-tree -l` unavailable (`run_command` blocked in sandbox). **Proven lower bound:** `> 24,576` bytes from `read_file` of `tools/llmtool/llmtool/task_completion.py` alone (1,662 lines; `followup-contract-gw-slices-1874.md` also recorded `80,266` bytes at `b0d0a57`), plus `tools/llmtool/tests/test_owner_literal_guard.py` (313 lines) and the remaining reads (**UNKNOWN**).
- **Computed T tier:** **beyond T6** (`read_bytes > 24,576` T6 limit).
- **Prerequisites:** Slices 1–2 (`verify-legacy` removal or decoupling from `task_completion.py` legacy CI constants) must be merged first so `verify-legacy` does not break; external callers in `task_completion_part4.py`, `trust_cli.py`, and `test_task_completion.py` must be verified in `read`.
- **Tests, formal review, and CI:** `tools/llmtool/tests/test_owner_literal_guard.py`, `tools/llmtool/tests/test_task_completion.py`, `tools/llmtool/tests/test_trust_cli.py`; `check (Linux 3.14)`, `check (macOS 3.13)`, `check (docs)`; formal review by `claude-reviewer`.
- **Route:** **OpenCode** (or **AGY**). Not `gw-agent`: `read_bytes` alone exceeds T5 (`16,384`) and T6 (`24,576`).

### Slice 4 — Remove dead legacy D6 constants from `tools/llmtool/scripts/d6_worktree_lifecycle.py`

- **Verifiable result:** Remove `TASKS_ROOT`, `ORIGINS`, `D6_INTEGRATOR_DISPATCHER_ROOT`, and `OWNER_APPROVAL_RE` from `tools/llmtool/scripts/d6_worktree_lifecycle.py` (leaving `OWNER_APPROVED_RETAIN_RELEASE_RE` and `OWNER_APPROVED_DISCARD_RE` intact unless their callers are migrated to `trust["slug"]` / `trust["retain_release_issues"]`), remove the five `_D6_LIFECYCLE` `LEGACY_VERIFY` entries from `ALLOWLIST` in `tools/llmtool/tests/test_owner_literal_guard.py`, and pass `test_owner_literal_guard.py` and `test_d6_worktree_lifecycle.py`.
- **Write:** `tools/llmtool/scripts/d6_worktree_lifecycle.py`, `tools/llmtool/tests/test_owner_literal_guard.py` (and `tools/llmtool/tests/test_d6_worktree_lifecycle.py` if any test imports the deleted constants).
- **Read:** `docs/design/a13-trust-json-design-369.md`, `tools/llmtool/scripts/d6_worktree_lifecycle.py`, `tools/llmtool/llmtool/trust.py`, `tools/llmtool/tests/test_owner_literal_guard.py`, `tools/llmtool/tests/test_d6_worktree_lifecycle.py` (**UNKNOWN** size, outside `read_paths`), `tools/llmtool/llmtool/trust_cli.py` (**UNKNOWN** size, outside `read_paths`), `tools/llmtool/tests/test_trust_cli.py` (**UNKNOWN** size, outside `read_paths`).
- **Unique read bytes & method:** Exact `wc -c` / `git ls-tree -l` unavailable (`run_command` blocked in sandbox). **Proven lower bound:** `> 24,576` bytes from `read_file` of `tools/llmtool/scripts/d6_worktree_lifecycle.py` alone (3,317 lines).
- **Computed T tier:** **beyond T6** (`read_bytes > 24,576` T6 limit) and **refused for `gw-agent`** by `DEFAULT_LARGE_FILE_LINES = 2000` (`d6_worktree_lifecycle.py` is 3,317 lines `>= 2000`, triggering `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1`).
- **Prerequisites:** Slices 1–2 (`verify-legacy` removal or decoupling from `d6_worktree_lifecycle.py` legacy constants) must be merged first; external callers in `trust_cli.py` and `test_d6_worktree_lifecycle.py` must be verified in `read`.
- **Tests, formal review, and CI:** `tools/llmtool/tests/test_owner_literal_guard.py`, `tools/llmtool/tests/test_d6_worktree_lifecycle.py`, `tools/llmtool/tests/test_trust_cli.py`; `check (Linux 3.14)`, `check (macOS 3.13)`, `check (docs)`; formal review by `claude-reviewer`.
- **Route:** **OpenCode** (or **AGY**). Not `gw-agent`: `d6_worktree_lifecycle.py` exceeds `DEFAULT_LARGE_FILE_LINES = 2000` and `read_bytes` exceeds T5/T6.

### Slice 5 — PR-10 guard test (`gw-agent` T5 candidate if scoped to a new small test module)

- **Verifiable result:** Add a new small test module `tools/llmtool/tests/test_pr10_legacy_guard.py` that imports `test_owner_literal_guard` and fails if `LEGACY_VERIFY` remains in `test_owner_literal_guard.LABELS` or any `test_owner_literal_guard.ALLOWLIST` value equals `LEGACY_VERIFY`.
- **Write:** `tools/llmtool/tests/test_pr10_legacy_guard.py` (new file).
- **Read:** `tools/llmtool/tests/test_owner_literal_guard.py`.
- **Unique read bytes & method:** Exact `wc -c` / `git ls-tree -l` unavailable (`run_command` blocked in sandbox). From `read_file` of all 313 lines of `tools/llmtool/tests/test_owner_literal_guard.py`, the file is under `16,384` bytes (estimated ~11–12 KiB; exact `read_bytes + packet_bytes` must be verified by `spec_preflight.py` before dispatch).
- **Computed T tier:** **T4 or T5** (subject to `spec_preflight.py` confirming `read_bytes + packet_bytes <= 16,384`): `writes = 1 <= 4`, `reads = 1`, `turns = 1 + 2*1 + 1 = 4 <= 16`, `NEW_FILE_MIN_TIER = 4 <= 5`, and new file has `0 < 2000` lines.
- **Prerequisites:** Slices 1–4 must be merged first so all `LEGACY_VERIFY` entries are removed from `ALLOWLIST` (and `LEGACY_VERIFY` is removed from `LABELS` in `test_owner_literal_guard.py`, or the guard checks `ALLOWLIST.values()` only if `LABELS` still retains the constant).
- **Tests, formal review, and CI:** `tools/llmtool/tests/test_pr10_legacy_guard.py`, `tools/llmtool/tests/test_owner_literal_guard.py`; `check (Linux 3.14)`, `check (macOS 3.13)`, `check (docs)`; formal review by `claude-reviewer`.
- **Route:** **`gw-agent` T5 candidate** (independent, 1 new small test file, 4 turns), **conditional on** `spec_preflight.py` confirming `read_bytes + packet_bytes <= 16,384`. **Smallest blocker if `verify-legacy` CLI removal must also be guarded:** checking `trust_cli.py` or `cli.py` requires adding those files to `read`, whose byte sizes are **UNKNOWN** here; if adding them pushes `read_bytes + packet_bytes > 16,384`, route that part to **OpenCode** or **AGY**.

---

## 4. `gw-agent` Candidacy Summary

- **Slice 3 (`task_completion.py`):** **Not `gw-agent`** (`read_bytes > 24,576`, beyond T6) → **OpenCode** or **AGY**.
- **Slice 4 (`d6_worktree_lifecycle.py`):** **Not `gw-agent`** (`d6_worktree_lifecycle.py` is 3,317 lines `>= 2000` large-file limit, and `read_bytes > 24,576`, beyond T6) → **OpenCode** or **AGY**.
- **Slice 5 (PR-10 guard):** **`gw-agent` T5 candidate** only as a standalone new small test module reading `tools/llmtool/tests/test_owner_literal_guard.py` (subject to `spec_preflight.py` confirming `read_bytes + packet_bytes <= 16,384`).
