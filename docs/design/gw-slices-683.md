# #683 follow-up — Executable split plan and `gw-agent` slice assessment

Facts in this plan come from the `#683` final acceptance matrix (`#683 c7121221158`, 2026-11-24, quoted in the task contract) and from the repository files in this task's read scope at `main` (`70909d838e1c4db6cc56ca996533c1685d8cc58d`). **Docs only:** this document is the only file created; no code or test is changed. Gates: `check (docs)` CI and formal review by `claude-reviewer`.

## 1. Tier limits, measurement method, and `gw-agent` candidacy

- **Tier limits (`LADDER_SIZES` in `tools/llmtool/scripts/spec_preflight.py`):**
  - `LADDER_SIZES` (`(files written, context bytes, gw-agent turns)`):
    - T1: `(1, 1536, 6)`
    - T2: `(1, 3072, 10)`
    - T3: `(2, 6144, 12)`
    - T4: `(3, 10240, 16)`
    - **T5: `(4, 16384, 16)`** (`gw-agent` ceiling: max `4` written files, max `16,384` context bytes counting `gw` read bytes plus packet, max `16` turns)
    - T6: `(5, 24576, 16)` (`LADDER_MAX_TIER = 6`)
  - Turn formula in `spec_tier` / `_match_tier` (`tools/llmtool/scripts/spec_preflight.py`): `turns = len(reads) + 2 * len(writes) + 1`.
  - `NEW_FILE_MIN_TIER = 4` (`spec_tier` / `_match_tier` require at least T4 when any `write` path does not yet exist in the repo).
  - `DEFAULT_LARGE_FILE_LINES = 2000` (`main` in `tools/llmtool/scripts/spec_preflight.py` refuses `gw-agent` with `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1` if any `write` file has `>= 2000` lines).
  - `check_read_scope` (`tools/llmtool/scripts/spec_preflight.py`) requires at least one `test_*.py` module in `read` when writing any `.py` file under `tools/` or `scripts/` (excluding `tools/llmtool/tests/`), and requires every repo path named in `acceptance` to appear in `read` or `write`.
  - `REPAIR_ROOM_MAX_ACCEPTANCE = 15` (`main` in `tools/llmtool/scripts/spec_preflight.py` warns `SPEC_PREFLIGHT_WARN:ACCEPTANCE_NO_REPAIR_ROOM` above `15` items, reserving `1` of `16` slots for repair).

- **Measurement method and reserve:**
  - Exact `spec_preflight.py` context sizing (`_measure_read_bytes` + `_measure_packet_parts`) measures unique `read` bytes on `70909d838e1c4db6cc56ca996533c1685d8cc58d` via `git ls-tree -l` / `git cat-file --batch-check`, plus packet bytes (`goal`, `acceptance`, framing) and repair reserve (`8,192`-byte `repair_packet` refusal ceiling in `review_repair.py` + `1` bounded acceptance item).
  - **Exact byte counts are `UNKNOWN` in this document** because shell command execution (`run_command`) was unavailable in this environment (`agentapi: operation not permitted`), so neither `git ls-tree -l` nor `wc -c` could be run. Smallest blocker: run `git ls-tree -l 70909d838e1c4db6cc56ca996533c1685d8cc58d` (or `tools/llmtool/scripts/spec_preflight.py --repo <repo>`) on the PM/EC2 host for exact byte totals.
  - Where a file already exceeds T5/T6 without exact byte counting, this plan records the **observed lower bound** from `read_file` (46,080-byte per-read truncation and total line count) or the task contract's quoted size (`pm_runtime_codex.py` ~21 KB), and never guesses exact bytes.

- **`gw-agent` candidacy: `none` for every executable slice in §2.**
  - Slice 1 (`WP-RD1`) writes `tools/llmtool/llmtool/pm_runtime_codex.py`, which is ~21 KB on `main` (exceeding T5 `16,384` bytes before adding `test_pm_runtime_codex.py`, packet, and repair reserve).
  - Slices 2, 3, and 4 all write `tools/llmtool/llmtool/chat_tui.py` and `tools/llmtool/tests/test_chat_tui.py`. Both files exceed `DEFAULT_LARGE_FILE_LINES = 2000` (refused by `spec_preflight.py` `main` for `gw-agent`) and exceed 46,080 bytes (over T5 `16,384` and T6 `24,576`). Slices 2–4 also require Owner acceptance at merge.
  - No required read file is dropped or fragmented to fake a smaller read, and no helper is invented solely to create `gw-agent` work.

## 2. Executable split plan

### Slice 1 — `WP-RD1`: Codex PM outside a git repository (`--skip-git-repo-check`)

- **Current state on `main`:** `CodexRuntime._launch` (`tools/llmtool/llmtool/pm_runtime_codex.py`) constructs `argv` for `codex ... exec` (and `exec resume <thread_id>`) without `--skip-git-repo-check`. `CodexRuntimeTests.test_first_and_resumed_send_share_codex_thread_and_event_shapes` (`tools/llmtool/tests/test_pm_runtime_codex.py`) asserts the exact `argv` without `--skip-git-repo-check`.
- **Verifiable result:** `CodexRuntime._launch` (`tools/llmtool/llmtool/pm_runtime_codex.py`) passes `--skip-git-repo-check` on `codex exec` (matching `tools/llmtool/llmtool/providers/codex_cli.py`), and `CodexRuntimeTests` (`tools/llmtool/tests/test_pm_runtime_codex.py`) verifies both fresh and resumed `exec` invocations include `--skip-git-repo-check` and succeed when `cwd` is not a git repository.
- **Write:** `tools/llmtool/llmtool/pm_runtime_codex.py`, `tools/llmtool/tests/test_pm_runtime_codex.py`.
- **Read:** `tools/llmtool/llmtool/pm_runtime_codex.py` (476 lines; ~21 KB per matrix), `tools/llmtool/llmtool/providers/codex_cli.py` (**UNKNOWN** bytes; outside this doc task's read scope), `tools/llmtool/tests/test_pm_runtime_codex.py` (204 lines; **UNKNOWN** exact bytes). Total: `3` reads, `2` writes.
- **Measured read bytes & reserve:** Exact sum **UNKNOWN** (`run_command` unavailable; smallest blocker: `git ls-tree -l` on the 3 files). Lower bound from contract: `pm_runtime_codex.py` alone is ~21 KB (`> 16,384` T5 limit), before `codex_cli.py`, `test_pm_runtime_codex.py`, packet, and repair reserve.
- **T tier:** **beyond T5** (at least T6 on `pm_runtime_codex.py` ~21 KB alone; exact T6 vs `beyond_t6` **UNKNOWN** until `codex_cli.py` + `test_pm_runtime_codex.py` + packet bytes are measured). `turns = 3 + 2*2 + 1 = 8` (within T5/T6 turn cap `16`); `writes = 2`.
- **Prerequisites:** None.
- **Owner acceptance gate:** None required by the matrix for `WP-RD1` beyond standard CI and formal review.
- **Tests and gates:** `tools/llmtool/tests/test_pm_runtime_codex.py`; `check (Linux 3.14)`, `check (macOS 3.13)`; formal review by `claude-reviewer`.
- **Route:** **AGY or OpenCode** (not `gw-agent`: `pm_runtime_codex.py` alone is ~21 KB, exceeding T5 `16,384` bytes).

### Slice 2 — `WP-1161b`: `/status` PM model, Codex cached tokens, and permission label

- **Current state on `main`:**
  - `ChatTUI.status_report` (`tools/llmtool/llmtool/chat_tui.py`) displays `PM session`, `runtime`, and `cwd`. For `claude-code`, it shows `Claude 帳號目錄` and `權限模式（本機設定）`; for non-Claude (`codex`), it shows only `帳號` and omits both the PM model and the permission label (`workspace-write`). For Claude PM it also omits the PM model.
  - `usage_totals`, `usage_gaps`, and `usage_status_line` (`tools/llmtool/llmtool/chat_tui_pm.py`) read only `_USAGE_KEYS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')` and have no mapping from Codex's `cached_input_tokens` to `cache_read_input_tokens` / cached-read display.
- **Verifiable result:** `ChatTUI.status_report` (`tools/llmtool/llmtool/chat_tui.py`) reports the configured/observed PM model and permission label for both Claude and Codex PMs, and `usage_totals` / `usage_gaps` / `usage_status_line` (`tools/llmtool/llmtool/chat_tui_pm.py`) map Codex `cached_input_tokens` into cached-read token totals without altering Claude token counts; unit/widget tests in `tools/llmtool/tests/test_chat_tui.py` cover `/status` output and cached-token aggregation.
- **Write:** `tools/llmtool/llmtool/chat_tui.py`, `tools/llmtool/llmtool/chat_tui_pm.py`, `tools/llmtool/tests/test_chat_tui.py`.
- **Read:** `tools/llmtool/llmtool/chat_tui.py` (4,797 lines; `> 46,080` bytes), `tools/llmtool/llmtool/chat_tui_pm.py` (515 lines; **UNKNOWN** exact bytes), `tools/llmtool/llmtool/pm_runtime_codex.py` (476 lines; ~21 KB), `tools/llmtool/tests/test_chat_tui.py` (9,495 lines; `> 46,080` bytes), `docs/meetings/2026-10-22-llmtool-tui-ui-ux-decision.md` (78 lines; **UNKNOWN** exact bytes). Total: `5` reads, `3` writes.
- **Measured read bytes & reserve:** Exact sum **UNKNOWN** (`run_command` unavailable). Proven lower bound: `chat_tui.py` (`> 46,080` B) + `test_chat_tui.py` (`> 46,080` B) + `pm_runtime_codex.py` (~21 KB) far exceeds T5 (`16,384` B) and T6 (`24,576` B) before packet and repair reserve.
- **T tier:** **beyond T6** (read bytes exceed `24,576`; additionally `chat_tui.py` and `test_chat_tui.py` exceed `DEFAULT_LARGE_FILE_LINES = 2000`). `turns = 5 + 2*3 + 1 = 12` (within `16`); `writes = 3`.
- **Prerequisites:** None.
- **Owner acceptance gate:** **Yes** — TUI-path merge requires Owner acceptance (`#683` matrix).
- **Tests and gates:** `tools/llmtool/tests/test_chat_tui.py`; `check (Linux 3.14)`, `check (macOS 3.13)`; formal review by `claude-reviewer`; Owner acceptance before merge.
- **Route:** **AGY or OpenCode** (not `gw-agent`: `chat_tui.py` and `test_chat_tui.py` exceed `2,000` lines and `24,576` bytes, and merge requires Owner acceptance).

### Slice 3 — `WP-1162b`: Codex PM Tasks keyed by `pm-<thread>` shown in the TUI

- **Current state on `main`:** `ChatTUI._read_background`, `ChatTUI._read_auto_resume`, and `ChatTUI.attention_report` (`tools/llmtool/llmtool/chat_tui.py`) pass `self.pm_target[0]` (the binding's `native_session_id`) to `read_background` and `read_attention` (`tools/llmtool/llmtool/chat_tui_status.py`). Both functions call `active_pm_inbox.pm_session_key` and `_session_records` (`tools/llmtool/llmtool/active_pm_inbox.py`), which match only `record.get('pm_session_id') == 'pm-' + native_session_id` and never resolve a Codex PM's `codex_thread_id` (`pm-<thread>`).
- **Verifiable result:** `read_background` and `read_attention` (`tools/llmtool/llmtool/chat_tui_status.py`) and `ChatTUI` background/attention/auto-resume reads (`tools/llmtool/llmtool/chat_tui.py`) include Tasks whose `pm_session_id` matches `pm-<thread>` for the connected Codex PM's `codex_thread_id` (without breaking `pm-<native_session_id>` matching or mutating TaskStore), verified by tests in `tools/llmtool/tests/test_chat_tui.py`.
- **Write:** `tools/llmtool/llmtool/chat_tui_status.py`, `tools/llmtool/llmtool/chat_tui.py`, `tools/llmtool/tests/test_chat_tui.py`.
- **Read:** `tools/llmtool/llmtool/chat_tui_status.py` (1,054 lines; **UNKNOWN** exact bytes), `tools/llmtool/llmtool/chat_tui.py` (4,797 lines; `> 46,080` bytes), `tools/llmtool/llmtool/active_pm_inbox.py` (697 lines; **UNKNOWN** exact bytes), `tools/llmtool/llmtool/pm_runtime_codex.py` (476 lines; ~21 KB), `tools/llmtool/tests/test_chat_tui.py` (9,495 lines; `> 46,080` bytes). Total: `5` reads, `3` writes.
- **Measured read bytes & reserve:** Exact sum **UNKNOWN** (`run_command` unavailable). Proven lower bound: `chat_tui.py` (`> 46,080` B) + `test_chat_tui.py` (`> 46,080` B) + `pm_runtime_codex.py` (~21 KB) far exceeds T5 (`16,384` B) and T6 (`24,576` B).
- **T tier:** **beyond T6** (read bytes exceed `24,576`; `chat_tui.py` and `test_chat_tui.py` exceed `DEFAULT_LARGE_FILE_LINES = 2000`). `turns = 5 + 2*3 + 1 = 12`; `writes = 3`.
- **Prerequisites:** None (note: `pm_runtime_codex.py` stores `codex_thread_id` in runtime state via `CodexRuntime._save_state`).
- **Owner acceptance gate:** **Yes** — Owner acceptance at merge (`#683` matrix).
- **Tests and gates:** `tools/llmtool/tests/test_chat_tui.py`; `check (Linux 3.14)`, `check (macOS 3.13)`; formal review by `claude-reviewer`; Owner acceptance before merge.
- **Route:** **AGY or OpenCode** (not `gw-agent`: `chat_tui.py` and `test_chat_tui.py` exceed `2,000` lines and `24,576` bytes, and merge requires Owner acceptance).

### Slice 4 — TUI PM-switch Task handover via `task adopt`

- **Current state on `main`:** `ChatTUI.run_pm_command` and `ChatTUI.finish_pm_connect` (`tools/llmtool/llmtool/chat_tui.py`) handle `/pm use`, `/pm new`, and `/pm connect` without calling `adopt_pm_session` (`tools/llmtool/llmtool/active_pm_inbox.py`) or `task adopt` (`tools/llmtool/llmtool/task_submission_cli.py`), and print nothing about the previous PM session's open Tasks. `active_pm_inbox.adopt_pm_session` already exists and is tested in `tools/llmtool/tests/test_pm_session_adoption.py`.
- **Verifiable result:** When switching or connecting to a replacement PM session in the same `cwd`, the TUI PM-switch flow (`ChatTUI.run_pm_command` / `ChatTUI.finish_pm_connect` in `tools/llmtool/llmtool/chat_tui.py` and `PMRuntimeAPI` in `tools/llmtool/llmtool/chat_tui_pm.py`) invokes `task adopt` / `adopt_pm_session` (or surfaces a bounded refusal/notice when the old PM is still active or has no eligible Tasks) and prints a clear summary of moved/open Tasks from the old PM, verified by tests in `tools/llmtool/tests/test_chat_tui.py` and `tools/llmtool/tests/test_pm_session_adoption.py`.
- **Write:** `tools/llmtool/llmtool/chat_tui.py`, `tools/llmtool/llmtool/chat_tui_pm.py`, `tools/llmtool/tests/test_chat_tui.py`.
- **Read:** `tools/llmtool/llmtool/chat_tui.py` (4,797 lines; `> 46,080` bytes), `tools/llmtool/llmtool/chat_tui_pm.py` (515 lines; **UNKNOWN** exact bytes), `tools/llmtool/llmtool/active_pm_inbox.py` (697 lines; **UNKNOWN** exact bytes), `tools/llmtool/llmtool/task_submission_cli.py` (12,742 lines; `> 46,080` bytes), `tools/llmtool/tests/test_chat_tui.py` (9,495 lines; `> 46,080` bytes), `tools/llmtool/tests/test_pm_session_adoption.py` (739 lines; **UNKNOWN** exact bytes). Total: `6` reads, `3` writes.
- **Measured read bytes & reserve:** Exact sum **UNKNOWN** (`run_command` unavailable). Proven lower bound: `chat_tui.py` (`> 46,080` B) + `task_submission_cli.py` (`> 46,080` B) + `test_chat_tui.py` (`> 46,080` B) far exceeds T5 (`16,384` B) and T6 (`24,576` B).
- **T tier:** **beyond T6** (read bytes exceed `24,576`; `chat_tui.py` and `test_chat_tui.py` exceed `DEFAULT_LARGE_FILE_LINES = 2000`). `turns = 6 + 2*3 + 1 = 13`; `writes = 3`.
- **Prerequisites:** `active_pm_inbox.adopt_pm_session` is already merged on `main`.
- **Owner acceptance gate:** **Yes** — `#683` decision 3 requires implementation and verification before `#683` closes (TUI-path merge requires Owner acceptance).
- **Tests and gates:** `tools/llmtool/tests/test_chat_tui.py`, `tools/llmtool/tests/test_pm_session_adoption.py`; `check (Linux 3.14)`, `check (macOS 3.13)`; formal review by `claude-reviewer`; Owner acceptance before merge.
- **Route:** **AGY or OpenCode** (not `gw-agent`: `chat_tui.py` and `test_chat_tui.py` exceed `2,000` lines and `24,576` bytes).

## 3. `RD2` status check (Codex usage over-counted on resumed threads)

- **What `main` shows (`UNKNOWN` whether Codex CLI triggers it; no mitigation present):**
  - `CodexRuntime._events` (`tools/llmtool/llmtool/pm_runtime_codex.py`) handles `turn.completed` by taking `item.get('usage')` verbatim and passing it to `self.owner._finish(record, status, result_text, usage)` and `yield event('TURN_END', ..., usage=usage)`. It does not subtract prior-turn usage when `self._thread_id` was resumed.
  - `usage_totals` (`tools/llmtool/llmtool/chat_tui_pm.py`) sums `turn.get('usage')[key]` across all `turns` returned by `read_all_turns`, with no resumed-thread deduplication or delta computation.
  - Whether `codex exec resume --json` emits cumulative thread usage or per-turn usage in `turn.completed` cannot be determined from the files in this task's `read_paths` (`pm_runtime.py` is outside `read_paths` and no live Codex CLI run is permitted here). Therefore `RD2` status on `main` is **UNKNOWN** (not fixed in `CodexRuntime._events` or `usage_totals` if Codex CLI reports cumulative usage).
- **Slice plan:** **none** (blocked pending verification, per P3: plan a slice only if a defect remains).
- **Smallest blocker:** Verify on an authorized host/PM check (reading `pm_runtime.py` and one real `codex exec` + `codex exec resume --json` `turn.completed` `usage` payload) whether `turn.completed` `usage` on a resumed thread is cumulative or per-turn. If cumulative, open an **AGY or OpenCode** slice writing `tools/llmtool/llmtool/pm_runtime_codex.py` (~21 KB, over T5) and `tools/llmtool/tests/test_pm_runtime_codex.py`.
