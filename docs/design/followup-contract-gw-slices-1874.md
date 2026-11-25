# #1874 follow-up — Executable split plan and `gw-agent` slice assessment

Facts in this plan were read at examined worktree head `20ccd4bf5c45c31a4de79c1582f19c884cfcae70` from [followup-contract-1874.md](followup-contract-1874.md), [followup-advance-caller-1874.md](followup-advance-caller-1874.md), `tools/llmtool/llmtool/direct_reassignment.py`, `tools/llmtool/llmtool/review_repair.py`, `tools/llmtool/llmtool/task_submission_cli.py`, and `tools/llmtool/scripts/spec_preflight.py`. **Docs only:** this document is the only file created or modified; no code or test is changed. Gates: `check (docs)` CI and formal review by `codex-reviewer`.

## 1. Baseline, tier limits, and resolved dependencies

- **Merged starting point:**
  - [followup-contract-1874.md](followup-contract-1874.md): review-side facts (`tools/llmtool/llmtool/review_dispatch.py` `dispatch_review`, `_preflight`, `_review_rules`, `_verdict_binding_line`, `_create_d6`; `tools/llmtool/llmtool/task_submission_cli.py` `run_task_review_prompt`, `_review_original_task`, `_review_ec2_line`, `_review_ci_lines`) and repair-side facts (`tools/llmtool/llmtool/review_repair.py` `repair_review`, `_record_repair_task`, `_delegate_repair`, `_registered_d6`, `_merge_repair_read_paths`, `_admissible_repair_read_path`).
  - [followup-advance-caller-1874.md](followup-advance-caller-1874.md): `tools/llmtool/llmtool/task_advance.py` (`advance`, `_advance_step`, `decide`, `_pre_review_action`, `_review_refusal_rerun`, `_blocked_fallback_reviewer`, `_reviewer_fallback`) preserves the caller's exact `task_id` and does not select a newest sibling Task.
- **Resolved dependency (`direct_reassignment.authoring_route`):**
  - Read from `tools/llmtool/llmtool/direct_reassignment.py` (`authoring_route`, `effective_writer_admission`, `verified_repair_result`).
  - `authoring_route` only projects the active writer identity (`provider_profile` and `owner_assignment_ref=None` when a verified handoff changed the effective `executor_identity`) onto `developer` for later repairs. It does **not** select a `task_id` for `dispatch_review` and does **not** alter `goal`, `acceptance`, `read_paths`, or `write_paths`.
- **In-progress dependency (not merged):**
  - The `#1874` A evidence section (PR #2708) is **not merged yet**. Any slice that depends on its conclusions is **pending PR #2708 acceptance** and does not use its in-progress files as a stable base.
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
  - **Requirement before assigning any tier:** Any tier assignment must construct and measure the actual complete `context_file` (and account for any repair-dispatch context) against `LADDER_SIZES` rather than relying on these estimates.

- **`gw-agent` candidacy: `UNKNOWN` (pending the read and measurement pass in §3).**
  - **What is established:**
    1. `tools/llmtool/llmtool/task_submission_cli.py` has `12,276` lines (observed via `read_file`), which is `>= DEFAULT_LARGE_FILE_LINES = 2000`. Any slice that **writes** `tools/llmtool/llmtool/task_submission_cli.py` is refused for `gw-agent` by `main` (`tools/llmtool/scripts/spec_preflight.py`) with `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1`.
    2. Contract and authority decisions (`task_binding.contract_digest`, `LLMTOOL-REVIEW-BINDING`, multi-Task contract unioning) and slices requiring large modules are `AGY/OpenCode` work (with Owner/Coordinator/PM sign-off where required), not `gw-agent` work.
    3. Per P2, no required read file is dropped or fragmented to fake a smaller read, and no helper is invented solely to create `gw-agent` work.
  - **Why overall candidacy is `UNKNOWN`:**
    - Because `task_advance.py`, `review_dispatch.py`, any external caller modules, and `tools/llmtool/tests/test_*.py` are outside `read_paths` in this run, complete per-slice `read` and `write` lists cannot be enumerated, and actual complete `context_file` byte sizes cannot be measured here. Without complete scopes and measured `context_bytes`, it is unknown whether any independent slice fits `T<=5` (`LADDER_SIZES[5] = (4, 16384, 16)`).

## 3. Executable split plan: `none`

**Executable slices:** `none`.

Per P1 and P4, no executable slice is proposed here because complete `read`/`write` lists and exact measured `context_file` byte counts cannot be established from the authorized `read_paths` and tools in this run. Rather than presenting incomplete or unmeasured candidates as an executable split plan, the smallest blockers are recorded below:

1. **Context and base-SHA byte measurement unavailable in this run:** Shell execution is not used in this run, and `spec_preflight.py` does not itself build a `context_file`, so exact per-file byte counts at the target base SHA (`wc -c` / `git ls-tree`) and actual complete `context_file` sizes cannot be measured here and are not guessed.
2. **Required code and test files lie outside `read_paths`:** `tools/llmtool/llmtool/task_advance.py`, `tools/llmtool/llmtool/review_dispatch.py`, any external CLI/orchestration caller modules under `tools/llmtool/` and `scripts/`, and `tools/llmtool/tests/test_*.py` are outside `read_paths`. Without reading those modules, complete `read` and `write` lists (including the `test_*.py` modules required by `check_read_scope` in `tools/llmtool/scripts/spec_preflight.py`) cannot be enumerated or measured.
3. **Pending PR #2708 (`#1874` A evidence section):** PR #2708 is not merged yet. Any design or implementation slice that depends on its conclusions is **pending PR #2708 acceptance** and cannot use its in-progress files as a stable base.

### Smallest unblock path

- Dispatch an `AGY/OpenCode` read/measurement pass with shell access (`git ls-tree` / `wc -c` at the target base SHA) and `read_paths` covering `tools/llmtool/llmtool/task_advance.py`, `tools/llmtool/llmtool/review_dispatch.py`, CLI/orchestration caller modules under `tools/llmtool/` and `scripts/`, and `tools/llmtool/tests/test_*.py` to enumerate complete `read`/`write` scopes, build and measure the actual complete `context_file` sizes, and then assign `T` tiers and `gw-agent` vs `AGY/OpenCode` candidacy.
- Keep any slice that depends on `#1874` A evidence **pending PR #2708 acceptance** until PR #2708 is merged.
