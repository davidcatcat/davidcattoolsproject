# Open work (`#2680` P1, `#1230` A3, `#2793` A13.0) — `gw-agent` tier 5 slice assessment

Facts in this document come from the task contract (`#2680` comment `7188692366`, `#1230` item A3, `#2793` A13.0) and from the repository files in this task's `read_paths` at `ab6b10a9312be87d67ada6ffe3b28c1984aa823f`. **Docs only:** this document is the only file created; no code or test is changed. Gates: `check (docs)` exact-head hosted docs check and formal review by `codex-reviewer`.

## 1. Tier 5 limits and how sizes were determined

- **Preflight tier 5 limits (`LADDER_SIZES`, `_match_tier`, `check_read_scope`, `large_file_lines`, `REPAIR_ROOM_MAX_ACCEPTANCE` in `tools/llmtool/scripts/spec_preflight.py`):**
  - **T5 ceiling:** max `4` written files, max `16,384` context bytes (`read_bytes + packet_bytes` from `_measure_read_bytes` and `_measure_packet_parts`), max `16` `gw-agent` turns (`turns = len(reads) + 2 * len(writes) + 1`).
  - **Large-file gate:** `DEFAULT_LARGE_FILE_LINES = 2000` (`main` in `tools/llmtool/scripts/spec_preflight.py` refuses `gw-agent` with `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1` if any `write` file has `>= 2000` newline bytes).
  - **Test read requirement:** `check_read_scope` (`tools/llmtool/scripts/spec_preflight.py`) requires at least one `test_*.py` module under `tools/llmtool/tests/` (or `scripts/` when all writes are under `scripts/`) in `read` when writing any `.py` file under `tools/` or `scripts/` (excluding `tools/llmtool/tests/`).
  - **Spec and repair reserve:** `REPAIR_ROOM_MAX_ACCEPTANCE = 15` reserves `1` of `16` acceptance slots for repair, and `review_repair.py` (`_failed_event`, `_remote_test_failure`, `_ci_failure`) caps `repair_packet` at `8,192` bytes. Together with specification text (`goal`, `acceptance`, framing, estimated at `~1,300–2,300` B as in `docs/design/gw-slices-683.md`), the combined spec + one-repair-round reserve is estimated at `~9,500–10,500` B, leaving at most `~5,884–6,884` B for `read` files under T5 (`16,384` B).
- **How sizes were determined:**
  - Because the AGY Developer environment has no shell (`git cat-file -s` / `stat().st_size` could not be executed), each admitted file's line count and UTF-8 byte size were determined directly from the full file contents read via `read_file` (with `±1` B uncertainty only for whether the final line ends with a newline).
  - No `test_*.py` module is in this task's `read_paths`, so the byte size of any required `test_*.py` read is marked `UNKNOWN` here and must be measured by the PM with `spec_preflight.py --repo` before dispatch.

## 2. Work packages that may fit Tier 5 (dispatch order)

Only one small item from the remaining open work can potentially fit T5, and its exact fit depends on the `UNKNOWN` byte size of the required `test_*.py` file. If that test module exceeds `~3,400` B, no package in this set fits T5 as-is.

### Package 1 — `#1230` A3: Remove unused module-level workflow constants in `tools/llmtool/llmtool/mac_full_suite.py`

- **Problem:** After `request_full_suite` (`tools/llmtool/llmtool/mac_full_suite.py`) was updated to call `resolve_profile(profile)` (`#2793`), the module-level constants `_WORKFLOW_FILE` and `_WORKFLOW_RUNS_PATH` in `tools/llmtool/llmtool/mac_full_suite.py` are no longer referenced anywhere in that module.
- **Acceptance:**
  - Remove the unused `_WORKFLOW_FILE` and `_WORKFLOW_RUNS_PATH` constants from `tools/llmtool/llmtool/mac_full_suite.py` while keeping `request_full_suite` and all `MAC_FULL_SUITE_*` constants unchanged.
  - Existing tests for `mac_full_suite.py` pass without modification.
- **Files to read:** `tools/llmtool/llmtool/mac_full_suite.py` (`91` lines, `~3,450` B), plus the covering `test_*.py` module under `tools/llmtool/tests/` (required by `check_read_scope` in `tools/llmtool/scripts/spec_preflight.py`; exact path and byte size `UNKNOWN` from this task's `read_paths`).
- **Files to write:** `tools/llmtool/llmtool/mac_full_suite.py` (`1` write file, `< 2,000` lines).
- **Preflight byte total and turns:**
  - `read` bytes: `~3,450` B (`mac_full_suite.py`, counted from `read_file` contents) + `UNKNOWN` B (required `test_*.py`).
  - Spec + one-repair-round reserve: `~9,500–10,500` B (`goal`/`acceptance`/framing + `8,192` B `repair_packet` ceiling).
  - Estimated total: `~12,950–13,950` B + `UNKNOWN` test module bytes. Fits T5 (`16,384` B) only if the required `test_*.py` module is `<= ~2,434–3,434` B (PM must verify via `spec_preflight.py --repo`).
  - Turns: `2 + 2*1 + 1 = 5` (within T5 turn cap `16`).
- **Resulting tier:** **T5** (conditional on `test_*.py` `<= ~2,434–3,434` B; otherwise `beyond_t5`).

## 3. Already-fixed items (not listed as open packages)

Per R4, the following items named in the source were checked against the admitted files and are already fixed:

- **`#1230` A3 — `chat_tui_theme.py` subprocess exit status (`tools/llmtool/llmtool/chat_tui_theme.py`, `repo_info`):** Already fixed. The inner `git` helper in `repo_info` runs `subprocess.run(..., check=False)` and checks `done.returncode != 0 or not isinstance(done.stdout, str)` before returning. No other `subprocess` call exists in `chat_tui_theme.py`.
- **`#2793` A13.0 — `mac_full_suite.py` CI profile consumer (`tools/llmtool/llmtool/mac_full_suite.py`, `request_full_suite`):** Already fixed. `request_full_suite` calls `resolve_profile(profile)` and reads `resolved.macos_workflow_file`. (Only the unused `_WORKFLOW_FILE` / `_WORKFLOW_RUNS_PATH` constants remain; tracked in Package 1 above.)
- **`#2793` A13.0 — `task_completion.py` CI check consumer (`tools/llmtool/llmtool/task_completion.py`, `collect_github_evidence`):** Already fixed. `collect_github_evidence` delegates CI check collection to `_collect_ci_evidence` (`task_completion_part4`). Only the unused `_MAC_CHECK` constant remains (tracked in §4 under `#1230` A3).

## 4. Work that cannot be cut to fit Tier 5 (blocked from `gw-agent`)

Every remaining open item below still exists in the admitted files, but cannot fit T5 (`16,384` B context including spec + one repair round, and `< 2,000` lines per written file).

### 4.1 `#1230` A3: `source_freshness.py` ignored subprocess exit status

- **Existing defect:** In `tools/llmtool/llmtool/source_freshness.py`, `_fetch_origin_main` starts `subprocess.Popen(['git', 'fetch', '--quiet', '--no-tags', 'origin', 'main'], ...)` and calls `process.communicate(timeout=_TIMEOUT_SECONDS)` without inspecting `process.returncode`. All other `subprocess.run` calls in `code_tree` and `source_freshness` already check `.returncode`.
- **Blocking file and size:** `tools/llmtool/llmtool/source_freshness.py` (`229` lines, `~8,300` B counted from `read_file` contents). With the `~9,500–10,500` B spec + one-repair-round reserve, `~8,300 + 9,500 = ~17,800` B already exceeds the T5 `16,384` B limit before adding the required `test_*.py` module (`check_read_scope` in `tools/llmtool/scripts/spec_preflight.py`).
- **What would unblock it:** Either dispatch directly to AGY/OpenCode (`beyond_t5`), or split `source_freshness.py` (separating pin reading/validation from `_fetch_origin_main` / `source_freshness`) so the fetch module + its test file stay under `~6,000` B total.

### 4.2 `#1230` A3: Unused names and imports in `tools/llmtool/llmtool/task_completion.py`

- **Existing defects:** In `tools/llmtool/llmtool/task_completion.py`:
  - Unused imports: `source_freshness` and `select_test_excerpts`.
  - Unused module-level constant: `_MAC_CHECK`.
  - Unused function: `_docs_only_ci` (`run_bounded_tests` and `run_bounded_worktree_tests` use `_docs_only` from `task_completion_part3`).
  - (The `task_completion_part1`–`task_completion_part4` imports at the bottom of `task_completion.py` are intentional `#2281` re-exports.)
- **Blocking file and size:** `tools/llmtool/llmtool/task_completion.py` (`1,712` lines, `~75,000` B estimated from `1,712` lines read via `read_file`), which alone exceeds T5 (`16,384` B) and T6 (`24,576` B) before adding `test_*.py` and the spec/repair reserve.
- **What would unblock it:** Dispatch directly to AGY/OpenCode, or split `task_completion.py` into smaller `< 6,000` B part modules.

### 4.3 `#2680` P1: Shared read-side classification of review claims and verdict tokens

- **Existing duplication across admitted files:**
  - `tools/llmtool/llmtool/review_result.py` (`_comment_verdict`, `comment_status_matches`, `is_verdict_too_long`, `review_kind`, `_claim_for`, `_completed_claim_result`, `diagnosis_review_body`)
  - `tools/llmtool/llmtool/task_advance.py` (`_claims`, `_is_ruling_claim`, `_non_ruling_claims`, `_head_review_claim`, `_codex_verdict_marker`, `_completed_codex_claim`, `_completed_codex_pass_claim`, `_verdict_text_version`, `_claim_version_comments`, `_may_be_review_verdict`, `_merge_pass_claim`, `_is_ruling_comment`, `_security_comment_code`, `_merge_security_claim`)
  - `tools/llmtool/llmtool/task_completion.py` (`collect_github_evidence`, `_VERDICT_ACTIVITY`, `_VERDICT_MARKER`, `_VERDICT_STATUS`)
  - `tools/llmtool/llmtool/review_dispatch.py` (`_parse_codex_review_inner`, `_comment_verdict_version`, `_find_posted_verdict`, `_find_posted_ruling`)
  - `tools/llmtool/llmtool/review_repair.py` (`claim_reviewer`, `dispute_open_on`, `withdrawn_findings`, `verdict_findings`)
- **Blocking files and sizes:**
  - `tools/llmtool/llmtool/task_advance.py` (`5,782` lines, `>= 2,000` lines, `~250,000` B) — refused by `large_file_lines` (`DEFAULT_LARGE_FILE_LINES = 2000`) in `spec_preflight.py` if written, and far exceeds T5/T6 if read.
  - `tools/llmtool/llmtool/review_dispatch.py` (`2,558` lines, `>= 2,000` lines, `~105,000` B) — refused by `large_file_lines` if written, and far exceeds T5/T6 if read.
  - `tools/llmtool/llmtool/task_completion.py` (`1,712` lines, `~75,000` B), `tools/llmtool/llmtool/review_repair.py` (`1,415` lines, `~60,000` B), and `tools/llmtool/llmtool/review_result.py` (`553` lines, `~24,000` B) each individually exceed T5 (`16,384` B) and T6 (`24,576` B) before adding `test_*.py` or the spec/repair reserve.
- **What would unblock it:** Split `task_advance.py`, `review_dispatch.py`, `task_completion.py`, `review_repair.py`, and `review_result.py` into `< 2,000`-line, `< 6,000`-byte modules, and introduce the shared read-side classifier via AGY/OpenCode first so any follow-up `gw-agent` consumer slice reads only a small classifier interface (`<= ~4,000` B), one small consumer, and one small test module.

### 4.4 `#2793` A13.0: Remaining consumers naming CI checks directly

- **Existing defects:**
  - `tools/llmtool/llmtool/task_advance.py`: `_required_linux_checks` (called without `profile` by `_review_ci_gate`) falls back to hardcoded `_LINUX_FLAG_CHECKS` (`_AI_LOOP_CHECK`, `_GATEWAY_CHECK`, `'check (Linux 3.14)'`, `'check (docs)'`); `_advance_step` checks `failed_name in _LINUX_FLAG_CHECKS`; and `_mac_capacity_cancel`, `_route_ci_failure`, and `_advance_step` compare against hardcoded `_MAC_CHECK = 'check (macOS 3.13)'` (and `_mac_capacity_cancel` also hardcodes `'.github/workflows/llmtool-mac-ci.yml'`).
  - `tools/llmtool/llmtool/review_repair.py`: `_ci_failure` checks `ci_failure['check_name'] not in CI_REPAIR_CHECKS`, where `CI_REPAIR_CHECKS` hardcodes all five check names instead of reading the CI profile.
- **Blocking files and sizes:**
  - `tools/llmtool/llmtool/task_advance.py` (`5,782` lines `>= 2,000`, `~250,000` B) — refused by `large_file_lines` and far exceeds T5/T6.
  - `tools/llmtool/llmtool/review_repair.py` (`1,415` lines, `~60,000` B) — exceeds T5 (`16,384` B) and T6 (`24,576` B) before adding `test_*.py` or the spec/repair reserve.
- **What would unblock it:** Split `task_advance.py` and `review_repair.py` into smaller `< 6,000` B modules (for example isolating CI failure routing and CI repair packet validation) before dispatching `gw-agent`, or execute `#2793` A13.0 directly on AGY/OpenCode.
