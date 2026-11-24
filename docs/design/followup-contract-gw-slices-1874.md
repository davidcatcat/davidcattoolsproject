# #1874 follow-up — Executable split plan and `gw-agent` slice assessment

Facts in this plan were read at base SHA `700d9ac5eecd71a4aae76a4ac5e400dceb04003e` from [followup-contract-1874.md](followup-contract-1874.md), [followup-advance-caller-1874.md](followup-advance-caller-1874.md), `tools/llmtool/llmtool/direct_reassignment.py`, `tools/llmtool/llmtool/review_repair.py`, `tools/llmtool/llmtool/task_submission_cli.py`, and `tools/llmtool/scripts/spec_preflight.py`. **Docs only:** this document is the only file created; no code or test is changed.

## 1. Baseline, tier limits, and resolved dependencies

- **Merged starting point:**
  - [followup-contract-1874.md](followup-contract-1874.md): review-side facts (`review_dispatch.py` `dispatch_review`, `_preflight`, `_review_rules`, `_verdict_binding_line`, `_create_d6`; `task_submission_cli.py` `run_task_review_prompt`, `_review_original_task`, `_review_ec2_line`, `_review_ci_lines`) and repair-side facts (`review_repair.py` `repair_review`, `_record_repair_task`, `_delegate_repair`, `_registered_d6`, `_merge_repair_read_paths`, `_admissible_repair_read_path`).
  - [followup-advance-caller-1874.md](followup-advance-caller-1874.md): `task_advance.py` (`advance`, `_advance_step`, `decide`, `_pre_review_action`, `_review_refusal_rerun`, `_blocked_fallback_reviewer`, `_reviewer_fallback`) preserves the caller's exact `task_id` and does not select a newest sibling Task.
- **Resolved dependency (`direct_reassignment.authoring_route`):**
  - Read from `tools/llmtool/llmtool/direct_reassignment.py` (`authoring_route`, `effective_writer_admission`, `verified_repair_result`).
  - `authoring_route` only projects the active writer identity (`provider_profile` and `owner_assignment_ref=None` when a verified handoff changed the effective `executor_identity`) onto `developer` for later repairs. It does **not** select a `task_id` for `dispatch_review` and does **not** alter `goal`, `acceptance`, `read_paths`, or `write_paths`.
- **In-progress dependency (not merged):**
  - The `#1874` A evidence section (PR #2708) is **not merged yet**. Any slice that depends on its conclusions is marked **pending PR #2708 acceptance** and does not use its in-progress files as a stable base.
- **Tier limits (`LADDER_SIZES` in `tools/llmtool/scripts/spec_preflight.py`):**
  - Current `gw-agent` tier: **T5** — max `4` written files, max `16384` context bytes, max `16` turns (`turns = len(reads) + 2 * len(writes) + 1` in `spec_tier`).
  - `NEW_FILE_MIN_TIER = 4` (`spec_tier` returns at least T4 when any write path is a new file).
  - `DEFAULT_LARGE_FILE_LINES = 2000` (`large_file_lines` refuses `gw-agent` writes to files with `>= 2000` lines).
  - `check_read_scope` requires at least one `test_*.py` module in `read` when writing any `.py` file under `tools/` or `scripts/` (excluding `tools/llmtool/tests/`), and requires every repo path named in `acceptance` to appear in `read` or `write`.

## 2. Estimation method and `gw-agent` candidacy

- **Byte-measurement method:** `run_command` was unavailable in this environment, so exact `wc -c` / `stat` byte totals could not be executed. Instead, sizes are reported from direct `read_file` observations at base SHA `700d9ac5eecd71a4aae76a4ac5e400dceb04003e`:
  - `tools/llmtool/llmtool/task_submission_cli.py`: `12,276` lines total; a single 777-line `read_file` chunk (around `_resume_review_result_return` through `run_task_direct_result_event`) was reported as `46,494` bytes alone.
  - `tools/llmtool/llmtool/review_repair.py`: `1,391` lines total (read in full across three `read_file` calls).
  - `tools/llmtool/scripts/spec_preflight.py`: `456` lines total (read in full).
  - `docs/design/followup-contract-1874.md`: `386` lines total (read in full).
  - `tools/llmtool/llmtool/direct_reassignment.py`: `382` lines total (read in full).
  - `docs/design/followup-advance-caller-1874.md`: `84` lines total (read in full).
  - Files outside `read_paths` (`tools/llmtool/llmtool/task_advance.py`, `tools/llmtool/llmtool/review_dispatch.py`, CLI caller modules, and `tools/llmtool/tests/test_*.py`): exact byte sizes are **UNKNOWN** (not in `read_paths`).
- **Context and repair reserve:** For any `gw-agent` spec, `LADDER_SIZES[5]` caps total context (prompt + contract + complete read files + repair reserve) at `16,384` bytes (`16 KiB`), and `REPAIR_ROOM_MAX_ACCEPTANCE = 15` (`spec_preflight.py`) reserves 1 of the 16 acceptance items for a repair attempt.
- **`gw-agent` candidates: `none`.**
  - **Smallest blocker:** Every remaining `#1874` slice either (a) requires reading large modules (`task_submission_cli.py` at `12,276` lines with `46,494` bytes in one 777-line chunk alone; `review_repair.py` at `1,391` lines) that exceed `LADDER_SIZES[5]` (`16,384` bytes) before adding context or repair reserve, (b) requires files outside `read_paths` whose sizes are `UNKNOWN`, or (c) depends on contract/authority design decisions and pending PR #2708 acceptance. No helper is invented to artificially split work into `gw-agent` slices.

## 3. Executable split plan

### Slice S1 — External CLI / orchestration caller investigation (docs-only)

- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate: requires reading `task_submission_cli.py` and other CLI/orchestration modules, exceeding T5 `16,384` bytes).
- **One verifiable result:** One design note (`docs/design/followup-caller-selection-1874.md`) recording from code which CLI or orchestration callers outside `task_advance.py` and `review_repair.py` select the `task_id` passed to `advance` or `dispatch_review`, and whether any caller selects a "newest" sibling Task on the D6.
- **Full read list:**
  - `docs/design/followup-contract-1874.md`
  - `docs/design/followup-advance-caller-1874.md`
  - `tools/llmtool/llmtool/task_submission_cli.py`
  - `tools/llmtool/llmtool/task_advance.py`
  - `tools/llmtool/llmtool/review_dispatch.py`
  - Any additional CLI/orchestration entrypoint modules under `tools/llmtool/llmtool/` or `scripts/` that call `advance` or `dispatch_review` (exact module list **UNKNOWN** from current `read_paths`; PM to enumerate before dispatch).
- **Full write list:**
  - `docs/design/followup-caller-selection-1874.md`
- **Unique read bytes + context/repair reserve + method:**
  - Observed lower bound from `read_file` at base SHA `700d9ac5eecd71a4aae76a4ac5e400dceb04003e`: `> 46,494` bytes (`task_submission_cli.py` partial chunk alone) + `docs/design/followup-contract-1874.md` (`386` lines) + `docs/design/followup-advance-caller-1874.md` (`84` lines); `task_advance.py`, `review_dispatch.py`, and other caller modules are **UNKNOWN** (outside `read_paths`). Plus context and repair reserve (at least 1 acceptance slot under `REPAIR_ROOM_MAX_ACCEPTANCE = 15`), total context exceeds `16,384` bytes.
- **T tier:** Exceeds `T5` (`16,384` bytes) and `T6` (`24,576` bytes); `AGY/OpenCode` route required.
- **Prerequisites:** None (can run immediately from merged `followup-contract-1874.md` and `followup-advance-caller-1874.md`; independent of PR #2708).
- **Tests, formal review, and CI:**
  - Tests: none (docs-only).
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: `check (docs)`.

### Slice S2 — `#1874` contract and binding design decision (docs-only)

- **Route:** `AGY/OpenCode` (plus Owner/Coordinator/PM design sign-off; not a `gw-agent` candidate: contract/authority decision and large-module reads exceeding T5 `16,384` bytes).
- **One verifiable result:** One agreed design specification (updating `docs/design/followup-contract-1874.md` or adding a dedicated decision note) that resolves: (1) whether any "newest-Task" caller selection found in S1 is intended or a defect, (2) whether `task_binding.contract_digest`, `LLMTOOL-REVIEW-BINDING`, and `_review_ec2_line` stay single-Task or union across all sibling Tasks on the D6 (including intermediate Tasks and `write_paths`), and (3) how multi-Task contract unioning interacts with the 16-item acceptance limit (`REPAIR_ACCEPTANCE_FULL` in `review_repair.py` `_record_repair_task` and `REPAIR_ROOM_MAX_ACCEPTANCE = 15` in `spec_preflight.py`).
- **Full read list:**
  - `docs/design/followup-contract-1874.md`
  - `docs/design/followup-advance-caller-1874.md`
  - `docs/design/followup-caller-selection-1874.md` (from S1)
  - Merged `#1874` A evidence document (from PR #2708 once accepted)
  - `tools/llmtool/llmtool/direct_reassignment.py`
  - `tools/llmtool/llmtool/review_repair.py`
  - `tools/llmtool/llmtool/task_submission_cli.py`
  - `tools/llmtool/llmtool/review_dispatch.py`
  - `tools/llmtool/llmtool/task_advance.py`
  - `tools/llmtool/scripts/spec_preflight.py`
- **Full write list:**
  - `docs/design/followup-contract-1874.md` (or a new design decision doc named by the PM).
- **Unique read bytes + context/repair reserve + method:**
  - Observed lower bound from `read_file` at base SHA `700d9ac5eecd71a4aae76a4ac5e400dceb04003e`: `> 46,494` bytes (`task_submission_cli.py` partial chunk alone) + `review_repair.py` (`1,391` lines) + `spec_preflight.py` (`456` lines) + `direct_reassignment.py` (`382` lines) + `followup-contract-1874.md` (`386` lines) + `followup-advance-caller-1874.md` (`84` lines); S1 output, PR #2708 output, `review_dispatch.py`, and `task_advance.py` are **UNKNOWN**. Plus context and repair reserve, total context far exceeds `16,384` bytes.
- **T tier:** Exceeds `T5` (`16,384` bytes) and `T6` (`24,576` bytes); `AGY/OpenCode` route required.
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)** (do not use its in-progress files as a stable base).
  - Slice S1 (`docs/design/followup-caller-selection-1874.md`).
- **Tests, formal review, and CI:**
  - Tests: none (docs-only).
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: `check (docs)`.

### Slice S3 — P1 implementation: review-side prompt, binding, and dispatch changes

- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate: touches `task_submission_cli.py` at `12,276` lines `>= DEFAULT_LARGE_FILE_LINES` and `> 46,494` bytes in one chunk, plus contract/authority-bound logic).
- **One verifiable result:** Implemented and tested review-side P1 fix across `task_submission_cli.py` (`run_task_review_prompt`, `_review_original_task`, `_review_ec2_line`), `review_dispatch.py` (`dispatch_review`, `_preflight`, `_verdict_binding_line`), and `task_advance.py` (`_advance_step`, `decide`) matching the contract decided in Slice S2.
- **Full read list:**
  - Agreed design decision from Slice S2
  - `tools/llmtool/llmtool/task_submission_cli.py`
  - `tools/llmtool/llmtool/review_dispatch.py`
  - `tools/llmtool/llmtool/task_advance.py`
  - Relevant `tools/llmtool/tests/test_*.py` modules covering `run_task_review_prompt`, `dispatch_review`, and `task_advance` (exact test filenames **UNKNOWN** from current `read_paths`; required by `spec_preflight.py` `check_read_scope`).
- **Full write list:**
  - Subset of `tools/llmtool/llmtool/task_submission_cli.py`, `tools/llmtool/llmtool/review_dispatch.py`, `tools/llmtool/llmtool/task_advance.py`, and their `tools/llmtool/tests/test_*.py` modules as specified by Slice S2.
- **Unique read bytes + context/repair reserve + method:**
  - Observed lower bound from `read_file`: `> 46,494` bytes (`task_submission_cli.py` partial chunk alone, `12,276` lines total); `review_dispatch.py`, `task_advance.py`, Slice S2 doc, and test files are **UNKNOWN**. Exceeds `T5` (`16,384` bytes) and `DEFAULT_LARGE_FILE_LINES` (`2,000` lines).
- **T tier:** Exceeds `T5` (`16,384` bytes) and `T6` (`24,576` bytes); `AGY/OpenCode` route required.
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)**.
  - Slice S1 and Slice S2.
- **Tests, formal review, and CI:**
  - Tests: EC2 `llmtool task remote-test` on affected `tools/llmtool/tests/test_*.py` modules.
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: required exact-head CI checks (`check (Linux 3.14)`, `check (macOS 3.13)`, and `check (docs)` if docs change).

### Slice S4 — P1 implementation: repair-side / caller-side changes (conditional on Slice S2)

- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate: `review_repair.py` is `1,391` lines and exceeds `T5` `16,384` bytes, and `test_review_repair.py` / caller modules are outside `read_paths`).
- **One verifiable result:** Implemented and tested repair-side or external-caller P1 changes in `review_repair.py` (`repair_review`, `_record_repair_task`, `_delegate_repair`) and/or the caller modules identified in Slice S1, if Slice S2 determines that repair-Task contract inheritance or caller `task_id` selection must change.
- **Full read list:**
  - Agreed design decision from Slice S2
  - `tools/llmtool/llmtool/review_repair.py`
  - `tools/llmtool/llmtool/direct_reassignment.py`
  - Any caller modules identified in Slice S1
  - `tools/llmtool/tests/test_review_repair.py` and `tools/llmtool/tests/test_review_repair_acceptance_full.py` (exact sizes **UNKNOWN**; outside `read_paths`, required by `spec_preflight.py` `check_read_scope`).
- **Full write list:**
  - `tools/llmtool/llmtool/review_repair.py` (and/or caller module from Slice S1) plus `tools/llmtool/tests/test_review_repair.py` / `tools/llmtool/tests/test_review_repair_acceptance_full.py` as specified by Slice S2.
- **Unique read bytes + context/repair reserve + method:**
  - `review_repair.py` (`1,391` lines) + `direct_reassignment.py` (`382` lines) read in full via `read_file` at base SHA `700d9ac5eecd71a4aae76a4ac5e400dceb04003e`; exact byte totals for `review_repair.py` and external test/caller files (`test_review_repair.py`, `test_review_repair_acceptance_full.py`, Slice S2 doc) are **UNKNOWN** without `wc -c` / `read_paths` access, and `review_repair.py` alone already exceeds `16,384` bytes.
- **T tier:** Exceeds `T5` (`16,384` bytes) and `T6` (`24,576` bytes); `AGY/OpenCode` route required.
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)**.
  - Slice S1 and Slice S2 (and Slice S3 if repair-side changes depend on the new review-side binding).
- **Tests, formal review, and CI:**
  - Tests: EC2 `llmtool task remote-test` on `tools/llmtool/tests/test_review_repair.py` and `tools/llmtool/tests/test_review_repair_acceptance_full.py` (plus any caller tests).
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: required exact-head CI checks (`check (Linux 3.14)`, `check (macOS 3.13)`).
