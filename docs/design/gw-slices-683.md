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
  - Exact `spec_preflight.py` context sizing (`_measure_read_bytes` + `_measure_packet_parts`) measures unique `read` bytes via `git cat-file -s` at `f1b1b566` (measured by the PM because the Developer environment has no shell), plus a stated packet and repair reserve estimate: packet bytes (`goal`, `acceptance`, framing, estimated at `~1,000–2,000` B) and repair reserve (`8,192`-byte `repair_packet` refusal ceiling in `review_repair.py` + `1` bounded acceptance item, estimated at `~8,500` B, for a combined packet + repair reserve estimate of `~9,500–10,500` B).
  - Every executable slice in §2 (and the `RD2` check set in §3) already exceeds the T6 read-byte ceiling (`24,576` B) on measured `read` bytes alone before adding the packet and repair reserve estimate, so every slice is `beyond_t6`.

- **`gw-agent` candidacy: `none` for every executable slice in §2.**
  - Slice 1 (`WP-RD1`) reads `tools/llmtool/llmtool/pm_runtime_codex.py` (`21,335` B), `tools/llmtool/llmtool/providers/codex_cli.py` (`14,141` B), and `tools/llmtool/tests/test_pm_runtime_codex.py` (`10,109` B), totaling `45,585` B before packet and repair reserve (`beyond_t6`).
  - Slices 2, 3, and 4 all write `tools/llmtool/llmtool/chat_tui.py` (`239,587` B) and `tools/llmtool/tests/test_chat_tui.py` (`492,019` B). Both files exceed `DEFAULT_LARGE_FILE_LINES = 2000` (refused by `spec_preflight.py` `main` for `gw-agent`) and far exceed T5 (`16,384` B) and T6 (`24,576` B). Slices 2–4 also require Owner acceptance at merge.
  - No required read file is dropped or fragmented to fake a smaller read, and no helper is invented solely to create `gw-agent` work.

## 2. Executable split plan

### Slice 1 — `WP-RD1`: Codex PM outside a git repository (`--skip-git-repo-check`)

- **Current state on `main`:** `CodexRuntime._launch` (`tools/llmtool/llmtool/pm_runtime_codex.py`) constructs `argv` for `codex ... exec` (and `exec resume <thread_id>`) without `--skip-git-repo-check`. `CodexRuntimeTests.test_first_and_resumed_send_share_codex_thread_and_event_shapes` (`tools/llmtool/tests/test_pm_runtime_codex.py`) asserts the exact `argv` without `--skip-git-repo-check`.
- **Verifiable result:** `CodexRuntime._launch` (`tools/llmtool/llmtool/pm_runtime_codex.py`) passes `--skip-git-repo-check` on `codex exec` (matching `tools/llmtool/llmtool/providers/codex_cli.py`), and `CodexRuntimeTests` (`tools/llmtool/tests/test_pm_runtime_codex.py`) verifies both fresh and resumed `exec` invocations include `--skip-git-repo-check` and succeed when `cwd` is not a git repository.
- **Write:** `tools/llmtool/llmtool/pm_runtime_codex.py`, `tools/llmtool/tests/test_pm_runtime_codex.py`.
- **Read:** `tools/llmtool/llmtool/pm_runtime_codex.py` (`21,335` B), `tools/llmtool/llmtool/providers/codex_cli.py` (`14,141` B), `tools/llmtool/tests/test_pm_runtime_codex.py` (`10,109` B). Total: `3` reads, `2` writes; read sum `45,585` B.
- **Measured read bytes & reserve:** `45,585` B (`git cat-file -s` at `f1b1b566`, measured by the PM because the Developer environment has no shell) + estimated `~9,500–10,500` B packet and repair reserve (`goal`/`acceptance`/framing + `8,192`-byte `repair_packet` ceiling) = `~55,085–56,085` B estimated total (already `> 24,576` B T6 limit on reads alone).
- **T tier:** **`beyond_t6`** (read bytes `45,585` B exceed T6 `24,576` B before packet and reserve). `turns = 3 + 2*2 + 1 = 8` (within T5/T6 turn cap `16`); `writes = 2`.
- **Prerequisites:** None.
- **Owner acceptance gate:** None required by the matrix for `WP-RD1` beyond standard CI and formal review.
- **Tests and gates:** `tools/llmtool/tests/test_pm_runtime_codex.py`; `check (Linux 3.14)`, `check (macOS 3.13)`; formal review by `claude-reviewer`.
- **Route:** **AGY or OpenCode** (not `gw-agent`: read sum `45,585` B exceeds T5 `16,384` B and T6 `24,576` B).

### Slice 2 — `WP-1161b`: `/status` PM model, Codex cached tokens, and permission label

- **Current state on `main`:**
  - `ChatTUI.status_report` (`tools/llmtool/llmtool/chat_tui.py`) displays `PM session`, `runtime`, and `cwd`. For `claude-code`, it shows `Claude 帳號目錄` and `權限模式（本機設定）`; for non-Claude (`codex`), it shows only `帳號` and omits both the PM model and the permission label (`workspace-write`). For Claude PM it also omits the PM model.
  - `usage_totals`, `usage_gaps`, and `usage_status_line` (`tools/llmtool/llmtool/chat_tui_pm.py`) read only `_USAGE_KEYS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')` and have no mapping from Codex's `cached_input_tokens` to `cache_read_input_tokens` / cached-read display.
- **Verifiable result:** `ChatTUI.status_report` (`tools/llmtool/llmtool/chat_tui.py`) reports the configured/observed PM model and permission label for both Claude and Codex PMs, and `usage_totals` / `usage_gaps` / `usage_status_line` (`tools/llmtool/llmtool/chat_tui_pm.py`) map Codex `cached_input_tokens` into cached-read token totals without altering Claude token counts; unit/widget tests in `tools/llmtool/tests/test_chat_tui.py` cover `/status` output and cached-token aggregation.
- **Write:** `tools/llmtool/llmtool/chat_tui.py`, `tools/llmtool/llmtool/chat_tui_pm.py`, `tools/llmtool/tests/test_chat_tui.py`.
- **Read:** `tools/llmtool/llmtool/chat_tui.py` (`239,587` B), `tools/llmtool/llmtool/chat_tui_pm.py` (`22,802` B), `tools/llmtool/llmtool/pm_runtime_codex.py` (`21,335` B), `tools/llmtool/tests/test_chat_tui.py` (`492,019` B), `docs/meetings/2026-10-22-llmtool-tui-ui-ux-decision.md` (`7,474` B). Total: `5` reads, `3` writes; read sum `783,217` B.
- **Measured read bytes & reserve:** `783,217` B (`git cat-file -s` at `f1b1b566`, measured by the PM because the Developer environment has no shell) + estimated `~9,500–10,500` B packet and repair reserve (`goal`/`acceptance`/framing + `8,192`-byte `repair_packet` ceiling) = `~792,717–793,717` B estimated total (exceeds T6 `24,576` B on reads alone).
- **T tier:** **`beyond_t6`** (read bytes `783,217` B exceed `24,576` B; additionally `chat_tui.py` and `test_chat_tui.py` exceed `DEFAULT_LARGE_FILE_LINES = 2000`). `turns = 5 + 2*3 + 1 = 12` (within `16`); `writes = 3`.
- **Prerequisites:** None.
- **Owner acceptance gate:** **Yes** — TUI-path merge requires Owner acceptance (`#683` matrix).
- **Tests and gates:** `tools/llmtool/tests/test_chat_tui.py`; `check (Linux 3.14)`, `check (macOS 3.13)`; formal review by `claude-reviewer`; Owner acceptance before merge.
- **Route:** **AGY or OpenCode** (not `gw-agent`: `chat_tui.py` and `test_chat_tui.py` exceed `2,000` lines and `24,576` bytes, and merge requires Owner acceptance).

### Slice 3 — `WP-1162b`: Codex PM Tasks keyed by `pm-<thread>` shown in the TUI

- **Current state on `main`:** `ChatTUI._read_background`, `ChatTUI._read_auto_resume`, and `ChatTUI.attention_report` (`tools/llmtool/llmtool/chat_tui.py`) pass `self.pm_target[0]` (the binding's `native_session_id`) to `read_background` and `read_attention` (`tools/llmtool/llmtool/chat_tui_status.py`). Both functions call `active_pm_inbox.pm_session_key` and `_session_records` (`tools/llmtool/llmtool/active_pm_inbox.py`), which match only `record.get('pm_session_id') == 'pm-' + native_session_id` and never resolve a Codex PM's `codex_thread_id` (`pm-<thread>`).
- **Verifiable result:** `read_background` and `read_attention` (`tools/llmtool/llmtool/chat_tui_status.py`) and `ChatTUI` background/attention/auto-resume reads (`tools/llmtool/llmtool/chat_tui.py`) include Tasks whose `pm_session_id` matches `pm-<thread>` for the connected Codex PM's `codex_thread_id` (without breaking `pm-<native_session_id>` matching or mutating TaskStore), verified by tests in `tools/llmtool/tests/test_chat_tui.py`.
- **Write:** `tools/llmtool/llmtool/chat_tui_status.py`, `tools/llmtool/llmtool/chat_tui.py`, `tools/llmtool/tests/test_chat_tui.py`.
- **Read:** `tools/llmtool/llmtool/chat_tui_status.py` (`47,463` B), `tools/llmtool/llmtool/chat_tui.py` (`239,587` B), `tools/llmtool/llmtool/active_pm_inbox.py` (`34,828` B), `tools/llmtool/llmtool/pm_runtime_codex.py` (`21,335` B), `tools/llmtool/tests/test_chat_tui.py` (`492,019` B). Total: `5` reads, `3` writes; read sum `835,232` B.
- **Measured read bytes & reserve:** `835,232` B (`git cat-file -s` at `f1b1b566`, measured by the PM because the Developer environment has no shell) + estimated `~9,500–10,500` B packet and repair reserve (`goal`/`acceptance`/framing + `8,192`-byte `repair_packet` ceiling) = `~844,732–845,732` B estimated total (exceeds T6 `24,576` B on reads alone).
- **T tier:** **`beyond_t6`** (read bytes `835,232` B exceed `24,576` B; `chat_tui.py` and `test_chat_tui.py` exceed `DEFAULT_LARGE_FILE_LINES = 2000`). `turns = 5 + 2*3 + 1 = 12`; `writes = 3`.
- **Prerequisites:** None (note: `pm_runtime_codex.py` stores `codex_thread_id` in runtime state via `CodexRuntime._save_state`).
- **Owner acceptance gate:** **Yes** — Owner acceptance at merge (`#683` matrix).
- **Tests and gates:** `tools/llmtool/tests/test_chat_tui.py`; `check (Linux 3.14)`, `check (macOS 3.13)`; formal review by `claude-reviewer`; Owner acceptance before merge.
- **Route:** **AGY or OpenCode** (not `gw-agent`: `chat_tui.py` and `test_chat_tui.py` exceed `2,000` lines and `24,576` bytes, and merge requires Owner acceptance).

### Slice 4 — TUI PM-switch Task handover via `task adopt`

- **Current state on `main`:** `ChatTUI.run_pm_command` and `ChatTUI.finish_pm_connect` (`tools/llmtool/llmtool/chat_tui.py`) handle `/pm use`, `/pm new`, and `/pm connect` without calling `adopt_pm_session` (`tools/llmtool/llmtool/active_pm_inbox.py`) or `task adopt` (`tools/llmtool/llmtool/task_submission_cli.py`), and print nothing about the previous PM session's open Tasks. `active_pm_inbox.adopt_pm_session` already exists and is tested in `tools/llmtool/tests/test_pm_session_adoption.py`.
- **Verifiable result:** When switching or connecting to a replacement PM session in the same `cwd`, the TUI PM-switch flow (`ChatTUI.run_pm_command` / `ChatTUI.finish_pm_connect` in `tools/llmtool/llmtool/chat_tui.py` and `PMRuntimeAPI` in `tools/llmtool/llmtool/chat_tui_pm.py`) invokes `task adopt` / `adopt_pm_session` (or surfaces a bounded refusal/notice when the old PM is still active or has no eligible Tasks) and prints a clear summary of moved/open Tasks from the old PM, verified by tests in `tools/llmtool/tests/test_chat_tui.py` and `tools/llmtool/tests/test_pm_session_adoption.py`.
- **Write:** `tools/llmtool/llmtool/chat_tui.py`, `tools/llmtool/llmtool/chat_tui_pm.py`, `tools/llmtool/tests/test_chat_tui.py`.
- **Read:** `tools/llmtool/llmtool/chat_tui.py` (`239,587` B), `tools/llmtool/llmtool/chat_tui_pm.py` (`22,802` B), `tools/llmtool/llmtool/active_pm_inbox.py` (`34,828` B), `tools/llmtool/llmtool/task_submission_cli.py` (`660,256` B), `tools/llmtool/tests/test_chat_tui.py` (`492,019` B), `tools/llmtool/tests/test_pm_session_adoption.py` (`37,331` B). Total: `6` reads, `3` writes; read sum `1,486,823` B.
- **Measured read bytes & reserve:** `1,486,823` B (`git cat-file -s` at `f1b1b566`, measured by the PM because the Developer environment has no shell) + estimated `~9,500–10,500` B packet and repair reserve (`goal`/`acceptance`/framing + `8,192`-byte `repair_packet` ceiling) = `~1,496,323–1,497,323` B estimated total (exceeds T6 `24,576` B on reads alone).
- **T tier:** **`beyond_t6`** (read bytes `1,486,823` B exceed `24,576` B; `chat_tui.py` and `test_chat_tui.py` exceed `DEFAULT_LARGE_FILE_LINES = 2000`). `turns = 6 + 2*3 + 1 = 13`; `writes = 3`.
- **Prerequisites:** `active_pm_inbox.adopt_pm_session` is already merged on `main`.
- **Owner acceptance gate:** **Yes** — `#683` decision 3 requires implementation and verification before `#683` closes (TUI-path merge requires Owner acceptance).
- **Tests and gates:** `tools/llmtool/tests/test_chat_tui.py`, `tools/llmtool/tests/test_pm_session_adoption.py`; `check (Linux 3.14)`, `check (macOS 3.13)`; formal review by `claude-reviewer`; Owner acceptance before merge.
- **Route:** **AGY or OpenCode** (not `gw-agent`: `chat_tui.py` and `test_chat_tui.py` exceed `2,000` lines and `24,576` bytes).

## 3. `RD2` status check (Codex usage over-counted on resumed threads)

- **What `main` shows (`UNKNOWN` whether Codex CLI triggers it; no mitigation present):**
  - `PMRuntime.open` (`tools/llmtool/llmtool/pm_runtime.py`) delegates `runtime == 'codex'` to `CodexRuntime(self)`. `CodexRuntime._launch` (`tools/llmtool/llmtool/pm_runtime_codex.py`) runs `codex ... exec resume <thread_id> --json ... -` when `self._thread_id` is set, and `CodexRuntime._events` (`tools/llmtool/llmtool/pm_runtime_codex.py`) takes `item.get('usage')` verbatim on `turn.completed` and passes it to `self.owner._finish(record, status, result_text, usage)` and `yield event('TURN_END', ..., usage=usage)` without subtracting prior-turn usage.
  - `PMRuntime._finish` (`tools/llmtool/llmtool/pm_runtime.py`) records `record['usage'] = usage if isinstance(usage, dict) else {}` verbatim and persists it via `PMRuntime._persist_turn` (`tools/llmtool/llmtool/pm_runtime.py`), with no resumed-thread delta or subtraction. (`_TurnParser.feed` in `tools/llmtool/llmtool/pm_runtime.py` handles only Claude `stream-json` `result` lines, not Codex `turn.completed`.)
  - `usage_totals` (`tools/llmtool/llmtool/chat_tui_pm.py`) sums `turn.get('usage')[key]` across all `turns` returned by `PMRuntimeAPI.read_all_turns`, with no resumed-thread deduplication or delta computation.
  - Neither `pm_runtime.py` nor `pm_runtime_codex.py` on `main` can decide whether `codex exec resume --json` emits cumulative thread usage or per-turn usage in `turn.completed`. Therefore `RD2` status on `main` remains **UNKNOWN**.
- **Check read set & tier (PM measurement via `git cat-file -s` at `f1b1b566`):** `tools/llmtool/llmtool/pm_runtime.py` (`91,712` B), `tools/llmtool/llmtool/pm_runtime_codex.py` (`21,335` B), `tools/llmtool/llmtool/chat_tui_pm.py` (`22,802` B), `tools/llmtool/tests/test_pm_runtime_codex.py` (`10,109` B); sum `145,958` B (+ estimated `~9,500–10,500` B packet and repair reserve), **`beyond_t6`**.
- **Slice plan:** **none** (blocked pending verification, per P3: plan a slice only if a defect remains).
- **Smallest blocker:** Needs one real `codex exec resume --json` `turn.completed` payload to determine whether `usage` on a resumed thread is cumulative or per-turn. If cumulative, open an **AGY or OpenCode** slice (`beyond_t6`, writing `tools/llmtool/llmtool/pm_runtime_codex.py` and `tools/llmtool/tests/test_pm_runtime_codex.py`).
