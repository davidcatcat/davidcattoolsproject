# Open work (`#2680` P1, `#1230` A3, `#2793` A13.0) — `gw-agent` tier 5 slice assessment

Facts in this document come from the task contract (`#2680` comment `7188692366`, `#1230` item A3, `#2793` A13.0) and from the repository files in this task's `read_paths` at `2172e586a405522b4eef770b6827c24071ac9113`. **Docs only:** this document is the only file created; no code or test is changed. Gates: `check (docs)` exact-head hosted docs check and formal review by `codex-reviewer`.

## 1. Tier 5 limits and how sizes were determined

- **Preflight tier 5 limits (`LADDER_SIZES`, `_match_tier`, `_measure_read_bytes`, `_measure_packet_parts`, `PACKET_EVIDENCE_KEYS`, `check_read_scope`, `large_file_lines`, `REPAIR_ROOM_MAX_ACCEPTANCE` in `tools/llmtool/scripts/spec_preflight.py`):**
  - **T5 ceiling:** max `4` written files, max `16,384` context bytes, max `16` `gw-agent` turns (`turns = len(reads) + 2 * len(writes) + 1`).
  - **How `spec_preflight.py` counts context bytes (`main`):** `context_bytes = read_bytes + packet_bytes`, where:
    - `read_bytes` (`_measure_read_bytes`) is the sum of unique `read` / `read_paths` file sizes (excluding non-existent files that are in `write_set`).
    - `packet_bytes` (`_measure_packet_parts`) sums **only** the fields listed in `PACKET_EVIDENCE_KEYS` (`context_file`, `context_files`, `packet_file`, `packet_files`, `initial_packet`, `packet`, `repair_evidence`, `repair_evidence_file`, `repair_evidence_files`, `evidence_file`, `evidence_files`, `evidence`, `findings_file`, `repair_findings`, `review_findings`, `diff_file`).
    - Specification text fields (`request`, `summary`, `goal`, `acceptance`) are screened separately by `check` against `REQUEST_LIMIT`, `2048`, and `RESPONSE_LIMIT`, and are **not** counted in `context_bytes`.
  - **Large-file gate:** `DEFAULT_LARGE_FILE_LINES = 2000` (`main` in `tools/llmtool/scripts/spec_preflight.py` refuses `gw-agent` with `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1` if any `write` file has `>= 2000` newline bytes).
  - **Test read requirement:** `check_read_scope` (`tools/llmtool/scripts/spec_preflight.py`) requires at least one `test_*.py` module under `tools/llmtool/tests/` (or `scripts/` when all code writes are under `scripts/`) in `read` when writing any `.py` file under `tools/` or `scripts/` (excluding `tools/llmtool/tests/`).
  - **Repair room:** `REPAIR_ROOM_MAX_ACCEPTANCE = 15` (`tools/llmtool/scripts/spec_preflight.py`) reserves `1` of `16` acceptance slots for repair, and `review_repair.py` (`_failed_event`, `_remote_test_failure`, `_ci_failure`) caps `repair_packet` at `8,192` bytes. Keeping room for one `8,192`-byte repair round within T5 (`16,384` B) leaves at most `8,192` B for `read_bytes` (plus `0` B `packet_bytes` on initial dispatch). Specification text (`goal`, `acceptance`, framing) is not counted by `spec_preflight.py` in `context_bytes`.
- **How sizes were determined:**
  - Because the AGY Developer environment has no shell (`git cat-file -s` / `stat().st_size` could not be executed), each admitted file's line count and byte size were determined from the full file contents read via `read_file` (with `±1` B uncertainty only for whether the final line ends with a newline).
  - No `test_*.py` module is in this task's `read_paths`, so test-file contents were not read directly. For `tools/llmtool/tests/test_mac_full_suite.py`, the exact size (`14,346` B) is taken from the v1 formal review verdict (`7216465956`); for items whose source file alone already exceeds the T5 `8,192` B read budget (with repair room) or `16,384` B T5 ceiling, the unread test-file size is marked `UNKNOWN`.

## 2. Work packages that fit Tier 5 (dispatch order)

**None.** No package from the remaining open work (`#2680` P1, `#1230` A3, `#2793` A13.0) fits Tier 5 (`16,384` B `context_bytes` including room for one `8,192`-byte repair round) under `tools/llmtool/scripts/spec_preflight.py`. The smallest verified item (`#1230` A3 unused constants in `tools/llmtool/llmtool/mac_full_suite.py`) requires reading `tools/llmtool/tests/test_mac_full_suite.py` (`check_read_scope`), which exceeds the T5 `16,384` B ceiling on `read_bytes` alone (moved to §4.1 below).

## 3. Already-fixed or unconfirmed items (not listed as open packages)

Per R4, the following items named in the source were checked against the admitted files:

- **`#1230` A3 — `chat_tui_theme.py` subprocess exit status (`tools/llmtool/llmtool/chat_tui_theme.py`, `repo_info`):** Already fixed. The inner `git` helper in `repo_info` runs `subprocess.run(..., check=False)` and checks `done.returncode != 0 or not isinstance(done.stdout, str)` before returning. No other `subprocess` call exists in `chat_tui_theme.py`.
- **`#2793` A13.0 — `mac_full_suite.py` CI profile consumer (`tools/llmtool/llmtool/mac_full_suite.py`, `request_full_suite`):** Already fixed. `request_full_suite` calls `resolve_profile(profile)` and reads `resolved.macos_workflow_file`. (Only the unused `_WORKFLOW_FILE` / `_WORKFLOW_RUNS_PATH` constants and `_DEFAULT_PROFILE` import remain; tracked in §4.1 below.)
- **`#2793` A13.0 — `task_completion.py` CI check consumer (`tools/llmtool/llmtool/task_completion.py`, `collect_github_evidence`):** Already fixed. `collect_github_evidence` delegates CI check collection to `_collect_ci_evidence` (`task_completion_part4`).
- **`#1230` A3 — `task_completion.py` alleged unused names (`tools/llmtool/llmtool/task_completion.py`, `_MAC_CHECK`, `_docs_only_ci`, `source_freshness`, `select_test_excerpts`):** Not listed as a confirmed defect. `task_completion.py` re-exports and delegates to `task_completion_part1`–`task_completion_part4` (and imports `task_completion_part5` in `collect_claude_result`), which are outside this task's `read_paths`. The comment on `_MAC_CHECK` in `task_completion.py` states that the completion gate uses it, and `_docs_only_ci` is the CI path classifier for the split completion module; neither can be classified as unused without reading `task_completion_part4.py`, and `source_freshness` / `select_test_excerpts` likewise cannot be confirmed unused across `task_completion_part1`–`task_completion_part5` from `read_paths` alone.

## 4. Work that cannot be cut to fit Tier 5 (blocked from `gw-agent`)

Every remaining open item below is confirmed in the admitted files (or blocked by an admitted file's size), and cannot fit T5 (`16,384` B `context_bytes` including `8,192` B repair room, and `< 2,000` lines per written file).

### 4.1 `#1230` A3: Unused workflow constants in `tools/llmtool/llmtool/mac_full_suite.py`

- **Existing defect:** After `request_full_suite` (`tools/llmtool/llmtool/mac_full_suite.py`) was updated to call `resolve_profile(profile)` (`#2793`), the module-level constants `_WORKFLOW_FILE` and `_WORKFLOW_RUNS_PATH` (and the `_DEFAULT_PROFILE` import from `.mac_full_due`, which is referenced only by `_WORKFLOW_FILE`) are no longer used anywhere in `mac_full_suite.py`.
- **Blocking files and sizes:**
  - `tools/llmtool/llmtool/mac_full_suite.py`: `91` lines, `3,565` B.
  - `tools/llmtool/tests/test_mac_full_suite.py`: required by `check_read_scope` (`tools/llmtool/scripts/spec_preflight.py`); `14,346` B per v1 formal review verdict `7216465956` (not in this task's `read_paths`, so not directly read).
  - **Preflight byte total and turns:** `read_bytes = 3,565 + 14,346 = 17,911` B (`packet_bytes = 0` on initial dispatch, or `+ 8,192` B `repair_packet` cap = `26,103` B on repair), which exceeds the T5 `16,384` B ceiling even before repair room. Turns: `2 + 2*1 + 1 = 5` (within T5 cap `16`).
- **What would unblock it:** Either dispatch directly to AGY/OpenCode (`beyond_t5`), or split `tools/llmtool/tests/test_mac_full_suite.py` so the test module covering `mac_full_suite.py` is `<= 4,627` B (keeping `mac_full_suite.py` + `test_*.py` + `8,192` B repair room `<= 16,384` B).

### 4.2 `#1230` A3: `source_freshness.py` ignored subprocess exit status

- **Existing defect:** In `tools/llmtool/llmtool/source_freshness.py`, `_fetch_origin_main` starts `subprocess.Popen(['git', 'fetch', '--quiet', '--no-tags', 'origin', 'main'], ...)` and calls `process.communicate(timeout=_TIMEOUT_SECONDS)` without inspecting `process.returncode`. All other `subprocess.run` calls in `code_tree` and `source_freshness` already check `.returncode`.
- **Blocking file and size:** `tools/llmtool/llmtool/source_freshness.py` (`229` lines, `~8,300` B counted from `read_file` contents). With the `8,192` B `repair_packet` reserve, `~8,300 + 8,192 = ~16,492` B already exceeds the T5 `16,384` B limit before adding the required `test_*.py` module (`check_read_scope` in `tools/llmtool/scripts/spec_preflight.py`; unread test size `UNKNOWN`).
- **What would unblock it:** Either dispatch directly to AGY/OpenCode (`beyond_t5`), or split `source_freshness.py` (separating pin reading/validation from `_fetch_origin_main` / `source_freshness`) so the fetch module + its test file stay `<= 8,192` B total.

### 4.3 `#2680` P1: Shared read-side classification of review claims and verdict tokens

- **Existing duplication across admitted files:**
  - `tools/llmtool/llmtool/review_result.py` (`_comment_verdict`, `_codex_formal_pass`, `comment_status_matches`, `is_verdict_too_long`, `review_kind`, `_claim_for`, `_completed_claim_result`, `diagnosis_review_body`, `finding_ids`, `blocking_findings`)
  - `tools/llmtool/llmtool/task_completion.py` (`collect_github_evidence`, `_VERDICT_ACTIVITY`, `_VERDICT_MARKER`, `_VERDICT_STATUS`)
  - `tools/llmtool/llmtool/review_repair.py` (`claim_reviewer`, `verdict_findings`, `dispute_open_on`, `withdrawn_findings`)
  - `tools/llmtool/llmtool/task_advance.py` (`5,782` lines) and `tools/llmtool/llmtool/review_dispatch.py` (`2,558` lines) also participate in claim/verdict reading and dispatch.
- **Blocking files and sizes:**
  - `tools/llmtool/llmtool/task_advance.py` (`5,782` lines, `>= 2,000` lines, `> 16,384` B) — refused by `large_file_lines` (`DEFAULT_LARGE_FILE_LINES = 2000`) in `spec_preflight.py` if written, and far exceeds T5/T6 if read.
  - `tools/llmtool/llmtool/review_dispatch.py` (`2,558` lines, `>= 2,000` lines, `> 16,384` B) — refused by `large_file_lines` if written, and far exceeds T5/T6 if read.
  - `tools/llmtool/llmtool/task_completion.py` (`1,712` lines, `> 16,384` B), `tools/llmtool/llmtool/review_repair.py` (`1,415` lines, `> 16,384` B), and `tools/llmtool/llmtool/review_result.py` (`553` lines, `> 16,384` B) each individually exceed T5 (`16,384` B) before adding `test_*.py` (`UNKNOWN`) or the `8,192` B repair reserve.
- **What would unblock it:** Split `task_advance.py`, `review_dispatch.py`, `task_completion.py`, `review_repair.py`, and `review_result.py` into `< 2,000`-line, `< 6,000`-byte modules, and introduce the shared read-side classifier via AGY/OpenCode first so any follow-up `gw-agent` consumer slice reads only a small classifier interface, one small consumer, and one small test module (`<= 8,192` B combined `read_bytes`).

### 4.4 `#2793` A13.0: Remaining consumers naming CI checks directly

- **Existing defects:**
  - `tools/llmtool/llmtool/review_repair.py`: `_ci_failure` checks `ci_failure['check_name'] not in CI_REPAIR_CHECKS`, where `CI_REPAIR_CHECKS` hardcodes all five check names (`'check (Linux 3.14)'`, `'check (docs)'`, `'gateway tests (Linux 3.14)'`, `'ai-loop helper tests (Python 3.14)'`, `'check (macOS 3.13)'`) instead of reading the CI profile.
  - `tools/llmtool/llmtool/task_advance.py`: `5,782` lines (`>= 2,000` lines, `> 16,384` B) — any remaining direct CI check consumer in `task_advance.py` is already blocked from `gw-agent` by file size.
- **Blocking files and sizes:**
  - `tools/llmtool/llmtool/task_advance.py` (`5,782` lines `>= 2,000`, `> 16,384` B) — refused by `large_file_lines` and exceeds T5/T6.
  - `tools/llmtool/llmtool/review_repair.py` (`1,415` lines, `> 16,384` B) — exceeds T5 (`16,384` B) before adding `test_*.py` (`UNKNOWN`) or the `8,192` B repair reserve.
- **What would unblock it:** Split `review_repair.py` (and `task_advance.py`) into smaller `< 6,000` B modules (for example isolating CI repair packet validation) before dispatching `gw-agent`, or execute `#2793` A13.0 directly on AGY/OpenCode.
