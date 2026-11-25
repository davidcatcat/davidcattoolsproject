# #1874 follow-up — Executable split plan and `gw-agent` slice assessment

Prose facts in this plan come from [followup-contract-1874.md](followup-contract-1874.md) and [followup-advance-caller-1874.md](followup-advance-caller-1874.md) at the heads named in those notes, and from `tools/llmtool/scripts/spec_preflight.py` at this task's base. Byte sizes in §3 are the PM-measured `git ls-tree -l` sizes at base `b0d0a57fc803ecf7bdab07a1b93ac42a81ddbefd`; a file absent from that table is marked **UNKNOWN**, never guessed. Code facts about `tools/llmtool/llmtool/task_submission_cli.py` are those recorded in §2 and in [followup-contract-1874.md](followup-contract-1874.md); the module is not in this task's read set. **Docs only:** this document is the only file created or modified; no code or test is changed. Gates: `check (docs)` CI and formal review by `claude-reviewer`.

## 1. Baseline, tier limits, and resolved dependencies

- **Merged starting point:**
  - [followup-contract-1874.md](followup-contract-1874.md): review-side facts (`tools/llmtool/llmtool/review_dispatch.py` `dispatch_review`, `_preflight`, `_review_rules`, `_verdict_binding_line`, `_create_d6`; `tools/llmtool/llmtool/task_submission_cli.py` `run_task_review_prompt`, `_review_original_task`, `_review_ec2_line`, `_review_ci_lines`) and repair-side facts (`tools/llmtool/llmtool/review_repair.py` `repair_review`, `_record_repair_task`, `_delegate_repair`, `_registered_d6`, `_merge_repair_read_paths`, `_admissible_repair_read_path`).
  - [followup-advance-caller-1874.md](followup-advance-caller-1874.md): `tools/llmtool/llmtool/task_advance.py` (`advance`, `_advance_step`, `decide`, `_pre_review_action`, `_review_refusal_rerun`, `_blocked_fallback_reviewer`, `_reviewer_fallback`) preserves the caller's exact `task_id` and does not select a newest sibling Task.
- **Resolved dependency (`direct_reassignment.authoring_route`):**
  - Read from `tools/llmtool/llmtool/direct_reassignment.py` (`authoring_route`, `effective_writer_admission`, `verified_repair_result`).
  - `authoring_route` only projects the active writer identity (`provider_profile` and `owner_assignment_ref=None` when a verified handoff changed the effective `executor_identity`) onto `developer` for later repairs. It does **not** select a `task_id` for `dispatch_review` and does **not** alter `goal`, `acceptance`, `read_paths`, or `write_paths`.
- **Resolved dependency (PR #2708):**
  - PR #2708's content was merged via #2713, so the `#1874` A evidence is **no longer pending**. No slice in §3 is gated on PR #2708, and this document does not treat any in-progress file as a stable base.
- **Tier limits (`LADDER_SIZES` in `tools/llmtool/scripts/spec_preflight.py`):**
  - `LADDER_SIZES` (`(files written, context bytes, gw-agent turns)`):
    - T1: `(1, 1536, 6)`
    - T2: `(1, 3072, 10)`
    - T3: `(2, 6144, 12)`
    - T4: `(3, 10240, 16)`
    - **T5: `(4, 16384, 16)`** (current `gw-agent` tier: max `4` written files, max `16,384` context bytes, max `16` turns)
    - T6: `(5, 24576, 16)` (`LADDER_MAX_TIER = 6`)
  - Turn formula in `spec_tier` (`tools/llmtool/scripts/spec_preflight.py`): `turns = len(reads) + 2 * len(writes) + 1`.
  - `NEW_FILE_MIN_TIER = 4` (`spec_tier` returns at least T4 when any write path does not yet exist in the repo).
  - `DEFAULT_LARGE_FILE_LINES = 2000` (`large_file_lines` and `main` in `tools/llmtool/scripts/spec_preflight.py` check `target.read_bytes().count(b"\n") >= large_file_limit` **only** for `spec["write"]` when `spec.get("provider") == "gw-agent"`, refusing with `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1`; it does **not** apply to `spec["read"]`).
  - `REPAIR_ROOM_MAX_ACCEPTANCE = 15` (`main` in `tools/llmtool/scripts/spec_preflight.py` warns `SPEC_PREFLIGHT_WARN:ACCEPTANCE_NO_REPAIR_ROOM` when `len(spec["acceptance"]) > 15`, reserving 1 of the 16 acceptance items for `_record_repair_task` in `tools/llmtool/llmtool/review_repair.py`).
  - `check_read_scope` (`tools/llmtool/scripts/spec_preflight.py`) requires at least one `test_*.py` module in `read` when writing any `.py` file under `tools/` or `scripts/` (excluding `tools/llmtool/tests/`), and requires every repo path named in `acceptance` to appear in `read` or `write`.

## 2. Context/repair reserve method and `gw-agent` candidacy

- **Context measurement and reserve estimates (`tools/llmtool/scripts/spec_preflight.py` and `tools/llmtool/llmtool/review_repair.py`):**
  - In `main` (`tools/llmtool/scripts/spec_preflight.py`), `context_bytes` is measured only via `os.path.getsize(context_file)` when `spec.get("context_file")` names an existing file (otherwise `0`), and passed to `spec_tier`. `spec_preflight.py` does **not** construct or verify a file containing the rendered prompt, task contract, and read files.
  - **Unmeasured reserve estimates at T5 (`16,384` `context_bytes` max) — estimates only, not eligibility bounds:**
    1. **Prompt and contract reserve (unsourced estimate):** `2,048` bytes is an illustrative estimate for `goal`, up to `15` `acceptance` items (`REPAIR_ROOM_MAX_ACCEPTANCE = 15`), and framing instructions. Subtracting this estimate from `16,384` leaves `14,336` bytes as a rough read-byte estimate, not a measured bound.
    2. **Repair reserve (estimate from packet refusal threshold):** `_record_repair_task` (`tools/llmtool/llmtool/review_repair.py`) appends `1` `bounded_acceptance` item (reserving 1 of the 16 acceptance slots; `~200–300` bytes estimated), and `_failed_event` (`tools/llmtool/llmtool/review_repair.py`) truncates the reviewer excerpt to `MAX_EXCERPT_CHARS = 2000` and **refuses** (`REPAIR_PACKET_INVALID`) if the JSON-encoded `repair_packet` exceeds `8,192` bytes. `8,192` is a packet encoding refusal threshold, not a measured number of bytes added to the worker `context_file`. Using `8,192 + 300 = 8,492` bytes as a rough repair-headroom estimate would leave `5,844` bytes (`16,384 - 2,048 - 8,492`), which is an **estimate only and cannot establish T5 eligibility**.
  - **Requirement before assigning any tier:** Every slice in §3 instead uses the complete `read` list's table-measured bytes as a lower bound. Where that lower bound alone already exceeds a ladder row (all three slices exceed T6), the tier and the `gw-agent` refusal follow without estimating the rendered prompt bytes; no estimate in this subsection is used as an eligibility bound.

- **`gw-agent` candidacy: `none` for every executable slice in §3 (measured, not estimated).**
  - **What is established:**
    1. `read_file` on the earlier examined head reported `12,276` lines for `tools/llmtool/llmtool/task_submission_cli.py`, which is `>= DEFAULT_LARGE_FILE_LINES = 2000`; its b0d0a57 line count is **UNKNOWN**. A slice that writes that file therefore cannot clear the `gw-agent` large-file check in `main` (`tools/llmtool/scripts/spec_preflight.py`, `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1`) on the available facts, and Slice 1 is beyond T6 regardless.
    2. Contract and authority decisions (`task_binding.contract_digest`, `LLMTOOL-REVIEW-BINDING`, multi-Task contract unioning) and slices requiring large modules are `AGY/OpenCode` work (with Owner/Coordinator/PM sign-off where required), not `gw-agent` work.
    3. No required read file is dropped or fragmented to fake a smaller read, and no helper is invented solely to create `gw-agent` work.
  - **Measured conclusion:** the smallest complete read set (Slice 3) is `19,408 + 7,965 = 27,373` table bytes before its **UNKNOWN** design-note bytes. That already exceeds both the T5 context limit `16,384` and the T6 limit `24,576`, so no independent slice fits `T<=5` (`LADDER_SIZES[5] = (4, 16384, 16)`). Slices 1 and 2 also exceed the T6 turn ceiling; §3 records each disqualifier.

## 3. Executable split plan

Base for every byte count: `b0d0a57fc803ecf7bdab07a1b93ac42a81ddbefd`, from the PM-measured `git ls-tree -l` table. `turns` is `len(reads) + 2 * len(writes) + 1` (`spec_tier` in `tools/llmtool/scripts/spec_preflight.py`). `T` is the smallest row of `LADDER_SIZES` that fits writes, table read bytes and turns on the `gw-agent` rule, or **beyond T6** when no row fits (`_match_tier` returns `None`; `main` prints `required=beyond_t6`). Non-`gw-agent` routes ignore turns in `_match_tier`, but every slice below also exceeds the T6 byte limit, so its tier is the same on either rule. The read bytes are the sum of the unique `read` files that appear in the table; design-note reads are marked **UNKNOWN** and are not counted.

### Slice 1 — Reviewer prompt carries every sibling Task contract

- **Result:** `run_task_review_prompt` (`tools/llmtool/llmtool/task_submission_cli.py`) renders every non-abandoned sibling Task contract on the executor D6, not only `_review_original_task`'s single earliest Task; a three-Task test fails on the current prompt and passes after the change.
- **Write:** `tools/llmtool/llmtool/task_submission_cli.py`, `tools/llmtool/tests/test_task_review_prompt.py`, `tools/llmtool/tests/test_task_submission_cli.py`.
- **Read:** [followup-contract-1874.md](followup-contract-1874.md) (**UNKNOWN**), [followup-advance-caller-1874.md](followup-advance-caller-1874.md) (**UNKNOWN**), `tools/llmtool/llmtool/task_submission_cli.py` (660,930), `tools/llmtool/llmtool/review_dispatch.py` (110,602), `tools/llmtool/tests/test_task_submission_cli.py` (357,672), `tools/llmtool/tests/test_task_review_prompt.py` (46,575), `tools/llmtool/tests/test_review_dispatch.py` (142,982), `tools/llmtool/tests/test_review_dispatch_cards.py` (14,415), `tools/llmtool/tests/test_review_dispatch_large_stream.py` (8,998), `tools/llmtool/tests/test_review_dispatch_runtime_codes.py` (4,434), `tools/llmtool/tests/test_claude_review_route.py` (32,447). That is 11 reads.
- **Measured read bytes:** `660,930 + 110,602 + 357,672 + 46,575 + 142,982 + 14,415 + 8,998 + 4,434 + 32,447 = 1,379,055`; the two design notes are **UNKNOWN**. The measured total alone exceeds the T6 limit `24,576`.
- **T tier:** **beyond T6**. The table read bytes already exceed T6, and 18 turns (`11 + 2*3 + 1`) exceed the T6 turn limit `16`; 3 writes fit the T6 file count only.
- **Prerequisites:** the PM records the widening rule (all non-abandoned sibling contracts; a current item still replaces an original item, as `_review_original_task`'s existing delta text states) in the slice task contract. [followup-contract-1874.md](followup-contract-1874.md) records the intended binding as **UNKNOWN**, so this is a decision input, not an assumption. PR #2708 is merged via #2713 and is not a gate.
- **Tests and gates:** `tools/llmtool/tests/test_task_review_prompt.py`, `tools/llmtool/tests/test_task_submission_cli.py`, `tools/llmtool/tests/test_review_dispatch.py`, `tools/llmtool/tests/test_review_dispatch_cards.py`, `tools/llmtool/tests/test_review_dispatch_large_stream.py`, `tools/llmtool/tests/test_review_dispatch_runtime_codes.py`, `tools/llmtool/tests/test_claude_review_route.py`; `check (Linux 3.14)`, `check (macOS 3.13)`, `check (docs)`, and formal review by `claude-reviewer`.
- **Route:** **OpenCode** (AGY is the permitted alternative). `gw-agent` is refused by `_match_tier`/`spec_tier` because T is beyond T6. Independently, `main` in `tools/llmtool/scripts/spec_preflight.py` cannot clear `DEFAULT_LARGE_FILE_LINES = 2000` for the written `task_submission_cli.py`: `read_file` on the earlier examined head recorded 12,276 lines, and the b0d0a57 line count is **UNKNOWN**.

### Slice 2 — Binding validation for the widened contract

- **Result:** `dispatch_review`, `_preflight` and `_verdict_binding_line` (`tools/llmtool/llmtool/review_dispatch.py`) emit the widened contract set in the review claim and in `LLMTOOL-REVIEW-BINDING`, and `validate_task_binding` (`tools/llmtool/llmtool/task_completion.py`) accepts that set and refuses a mismatch; accept/refuse tests cover the emitted marker and the refusal.
- **Write:** `tools/llmtool/llmtool/review_dispatch.py`, `tools/llmtool/llmtool/task_completion.py`, `tools/llmtool/tests/test_review_dispatch.py`, `tools/llmtool/tests/test_task_completion.py`.
- **Read:** [followup-contract-1874.md](followup-contract-1874.md) (**UNKNOWN**), [followup-advance-caller-1874.md](followup-advance-caller-1874.md) (**UNKNOWN**), `tools/llmtool/llmtool/review_dispatch.py` (110,602), `tools/llmtool/llmtool/task_submission_cli.py` (660,930), `tools/llmtool/llmtool/task_completion.py` (80,266), `tools/llmtool/llmtool/review_repair.py` (68,819), `tools/llmtool/tests/test_review_dispatch.py` (142,982), `tools/llmtool/tests/test_review_dispatch_cards.py` (14,415), `tools/llmtool/tests/test_review_dispatch_large_stream.py` (8,998), `tools/llmtool/tests/test_review_dispatch_runtime_codes.py` (4,434), `tools/llmtool/tests/test_review_repair.py` (102,904), `tools/llmtool/tests/test_review_bind_failure_reason.py` (15,379), `tools/llmtool/tests/test_review_bind_retry.py` (7,658), `tools/llmtool/tests/test_task_completion.py` (425,241). That is 14 reads.
- **Measured read bytes:** `110,602 + 660,930 + 80,266 + 68,819 + 142,982 + 14,415 + 8,998 + 4,434 + 102,904 + 15,379 + 7,658 + 425,241 = 1,642,628`; the two design notes are **UNKNOWN**. The measured total alone exceeds the T6 limit `24,576`.
- **T tier:** **beyond T6**. 23 turns (`14 + 2*4 + 1`) exceed the T6 turn limit `16` and the table read bytes exceed the T6 byte limit; 4 writes fit the T6 file count `5`.
- **Prerequisites:** Slice 1's widening rule and prompt shape must be merged first, because this slice binds and validates that shape.
- **Tests and gates:** `tools/llmtool/tests/test_review_dispatch.py`, `tools/llmtool/tests/test_review_dispatch_cards.py`, `tools/llmtool/tests/test_review_dispatch_large_stream.py`, `tools/llmtool/tests/test_review_dispatch_runtime_codes.py`, `tools/llmtool/tests/test_task_completion.py`, `tools/llmtool/tests/test_review_repair.py`, `tools/llmtool/tests/test_review_bind_failure_reason.py`, `tools/llmtool/tests/test_review_bind_retry.py`; `check (Linux 3.14)`, `check (macOS 3.13)`, `check (docs)`, and formal review by `claude-reviewer`.
- **Route:** **OpenCode** (AGY is the permitted alternative). `gw-agent` is refused because 23 turns exceed the T6 limit `16` and the table read bytes exceed the T6 byte limit, before the large-file rule; the written files' b0d0a57 line counts are **UNKNOWN**.

### Slice 3 — `authoring_route` exit coverage and malformed-input refusals

- **Result:** `tools/llmtool/tests/test_direct_reassignment.py` covers `authoring_route` exits A1, A2, B, C, D and E plus the skip branches of `effective_writer_admission` that §3 of [followup-contract-1874.md](followup-contract-1874.md) lists as uncovered; a malformed `root` or a verified repair missing `completion`/`evidence` produces an `LLMToolError` refusal instead of an uncaught `AttributeError`/`KeyError`/`TypeError`, and the suite passes.
- **Write:** `tools/llmtool/llmtool/direct_reassignment.py`, `tools/llmtool/tests/test_direct_reassignment.py`.
- **Read:** [followup-contract-1874.md](followup-contract-1874.md) (**UNKNOWN**), `tools/llmtool/llmtool/direct_reassignment.py` (19,408), `tools/llmtool/tests/test_direct_reassignment.py` (7,965). That is 3 reads.
- **Measured read bytes:** `19,408 + 7,965 = 27,373`; the design note is **UNKNOWN**. The measured total alone exceeds the T5 limit `16,384` and the T6 limit `24,576`.
- **T tier:** **beyond T6**. 2 writes and 8 turns are within the T6 limits, but the table read bytes exceed the T6 byte limit.
- **Prerequisites:** none beyond the merged slice-3 facts in [followup-contract-1874.md](followup-contract-1874.md); PR #2708 is not a gate.
- **Tests and gates:** `tools/llmtool/tests/test_direct_reassignment.py`; `check (Linux 3.14)`, `check (macOS 3.13)`, `check (docs)`, and formal review by `claude-reviewer`.
- **Route:** **OpenCode** (AGY is the permitted alternative). `gw-agent` is refused because the measured table read bytes `27,373` already exceed the T5 context limit `16,384`; this is the smallest complete read set in the plan. The written files' b0d0a57 line counts are **UNKNOWN**, so the `DEFAULT_LARGE_FILE_LINES = 2000` rule is not the deciding reason.

### Blocked remaining work (not an executable slice)

- **Caller selection outside `task_advance.py`.** [followup-advance-caller-1874.md](followup-advance-caller-1874.md) proves `advance`/`_advance_step` preserve the caller's `task_id`, but neither note names the module that chooses that `task_id`. No read source here enumerates the CLI/orchestration caller modules, so a complete `read` list cannot be stated without guessing; this item is therefore not offered as a slice. Smallest blocker: authorize a caller-inventory read scope (entrypoints under `tools/llmtool/llmtool/` and `scripts/`) or name the caller module in the task contract. This is the only remaining `#1874` work item for which this plan cannot state a complete read list.

### `gw-agent` conclusion

No executable slice fits `gw-agent`. The smallest complete read set (Slice 3: `19,408 + 7,965 = 27,373` table bytes before its **UNKNOWN** design note) already exceeds the T5 context limit `16,384` and the T6 limit `24,576`. Slice 1 additionally needs 18 turns (limit `16`) and writes `task_submission_cli.py`, recorded at 12,276 lines via `read_file` on the earlier examined head; Slice 2 needs 23 turns (limit `16`) and its 1,642,628 table read bytes exceed T6. All three route to OpenCode (AGY is the permitted alternative), and no read file is dropped or split to manufacture a smaller slice.
