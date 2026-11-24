# #1874 follow-up — Executable split plan and `gw-agent` slice assessment

Facts in this plan were read at base SHA `a30fe7f996fc7032b047aedd421c9a86f0002bda` from [followup-contract-1874.md](followup-contract-1874.md), [followup-advance-caller-1874.md](followup-advance-caller-1874.md), `tools/llmtool/llmtool/direct_reassignment.py`, `tools/llmtool/llmtool/review_repair.py`, `tools/llmtool/llmtool/task_submission_cli.py`, and `tools/llmtool/scripts/spec_preflight.py`. **Docs only:** this document is the only file created or modified; no code or test is changed.

## 1. Baseline, tier limits, and resolved dependencies

- **Merged starting point:**
  - [followup-contract-1874.md](followup-contract-1874.md): review-side facts (`tools/llmtool/llmtool/review_dispatch.py` `dispatch_review`, `_preflight`, `_review_rules`, `_verdict_binding_line`, `_create_d6`; `tools/llmtool/llmtool/task_submission_cli.py` `run_task_review_prompt`, `_review_original_task`, `_review_ec2_line`, `_review_ci_lines`) and repair-side facts (`tools/llmtool/llmtool/review_repair.py` `repair_review`, `_record_repair_task`, `_delegate_repair`, `_registered_d6`, `_merge_repair_read_paths`, `_admissible_repair_read_path`).
  - [followup-advance-caller-1874.md](followup-advance-caller-1874.md): `tools/llmtool/llmtool/task_advance.py` (`advance`, `_advance_step`, `decide`, `_pre_review_action`, `_review_refusal_rerun`, `_blocked_fallback_reviewer`, `_reviewer_fallback`) preserves the caller's exact `task_id` and does not select a newest sibling Task.
- **Resolved dependency (`direct_reassignment.authoring_route`):**
  - Read from `tools/llmtool/llmtool/direct_reassignment.py` (`authoring_route`, `effective_writer_admission`, `verified_repair_result`).
  - `authoring_route` only projects the active writer identity (`provider_profile` and `owner_assignment_ref=None` when a verified handoff changed the effective `executor_identity`) onto `developer` for later repairs. It does **not** select a `task_id` for `dispatch_review` and does **not** alter `goal`, `acceptance`, `read_paths`, or `write_paths`.
- **In-progress dependency (not merged):**
  - The `#1874` A evidence section (PR #2708) is **not merged yet**. Any candidate slice that depends on its conclusions is marked **pending PR #2708 acceptance** and does not use its in-progress files as a stable base.
- **Tier limits (`LADDER_SIZES` in `tools/llmtool/scripts/spec_preflight.py`):**
  - `LADDER_SIZES`: T1 `(1, 1536, 6)`, T2 `(1, 3072, 10)`, T3 `(2, 6144, 12)`, T4 `(3, 10240, 16)`, **T5 `(4, 16384, 16)`** (current `gw-agent` tier: max `4` written files, max `16384` context bytes, max `16` turns), T6 `(5, 24576, 16)`.
  - Turn formula in `spec_tier`: `turns = len(reads) + 2 * len(writes) + 1`.
  - `NEW_FILE_MIN_TIER = 4` (`spec_tier` returns at least T4 when any write path does not yet exist in the repo).
  - `DEFAULT_LARGE_FILE_LINES = 2000` (`large_file_lines` and `main` refuse `gw-agent` writes to files with `>= 2000` lines).
  - `REPAIR_ROOM_MAX_ACCEPTANCE = 15` (`main` warns `ACCEPTANCE_NO_REPAIR_ROOM` when `len(spec["acceptance"]) > 15`, reserving 1 of the 16 acceptance items for a repair attempt).
  - `check_read_scope` requires at least one `test_*.py` module in `read` when writing any `.py` file under `tools/` or `scripts/` (excluding `tools/llmtool/tests/`), and requires every repo path named in `acceptance` to appear in `read` or `write`.

## 2. Byte-measurement method, reserve rule, and `gw-agent` candidacy

- **Byte-measurement method and environment blocker:**
  - `run_command` is unavailable in this sandbox (`agentapi: operation not permitted`), so exact `wc -c`, `stat`, or `git ls-tree` byte totals could not be executed at base SHA `a30fe7f996fc7032b047aedd421c9a86f0002bda`.
  - Line counts observed directly via `read_file` for the files in `read_paths`:
    - `tools/llmtool/llmtool/task_submission_cli.py`: `12,276` lines (`>= DEFAULT_LARGE_FILE_LINES = 2000`).
    - `tools/llmtool/llmtool/review_repair.py`: `1,391` lines.
    - `tools/llmtool/scripts/spec_preflight.py`: `456` lines.
    - `docs/design/followup-contract-1874.md`: `386` lines.
    - `tools/llmtool/llmtool/direct_reassignment.py`: `382` lines.
    - `docs/design/followup-advance-caller-1874.md`: `84` lines.
  - Files outside `read_paths` (`tools/llmtool/llmtool/task_advance.py`, `tools/llmtool/llmtool/review_dispatch.py`, CLI/orchestration caller modules, `tools/llmtool/tests/test_*.py`, and future outputs from PR #2708 / S1 / S2) cannot be read or measured from `read_paths` alone.
- **Context and repair reserve calculation:**
  - Under `spec_preflight.py` (`LADDER_SIZES`, `spec_tier`, `REPAIR_ROOM_MAX_ACCEPTANCE`), a `gw-agent` spec's `context_bytes` (prompt + contract + complete unique read files + repair reserve) must fit `<= 16,384` bytes at T5, with `len(writes) <= 4`, `turns <= 16`, and `len(acceptance) <= 15` (reserving 1 slot for a repair attempt).
  - Because `run_command` is unavailable to measure exact byte sizes of existing files and required files lie outside `read_paths` or do not exist yet, complete unique read byte totals + context/repair reserve are **unmeasurable (`BLOCKED`)** from this Task's `read_paths` alone.
- **`gw-agent` candidates: `none`.**
  - **Smallest blocker:**
    1. `tools/llmtool/llmtool/task_submission_cli.py` is `12,276` lines (`>= DEFAULT_LARGE_FILE_LINES = 2000`), which `spec_preflight.py` refuses for `gw-agent` writes.
    2. All remaining `#1874` work requires reading large modules (`task_submission_cli.py`, `review_repair.py`), files outside `read_paths` (`task_advance.py`, `review_dispatch.py`, caller modules, `tools/llmtool/tests/test_*.py`), or contract/authority decisions and unmerged PR #2708 conclusions.
    3. No helper is invented to artificially split work into `gw-agent` slices.

## 3. Executable split plan (`none` immediately dispatchable; blocked candidates)

Per P4, no candidate slice below is presented as an immediately dispatchable executable slice because complete `read`/`write` lists and exact unique read byte measurements cannot be produced from `read_paths` alone (`run_command` is blocked, required modules are outside `read_paths`, and PR #2708 is not merged yet). Instead, each candidate states its target result, route, prerequisites, required gates, and **smallest blocker**.

**Immediately dispatchable executable slices:** `none`.

### Candidate S1 — External CLI / orchestration caller investigation (docs-only, `BLOCKED`)

- **Status:** `BLOCKED` (not dispatchable from current `read_paths`; exact caller modules and file byte totals are unmeasurable here).
- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate: requires reading `tools/llmtool/llmtool/task_submission_cli.py` at `12,276` lines plus caller modules).
- **One verifiable result:** One design note (`docs/design/followup-caller-selection-1874.md`) recording from code which CLI or orchestration callers outside `task_advance.py` (`advance`) and `review_repair.py` (`repair_review`) select the `task_id` passed to `advance` or `dispatch_review`, and whether any caller selects a "newest" sibling Task on the D6.
- **Known read inputs (incomplete):**
  - `docs/design/followup-contract-1874.md`
  - `docs/design/followup-advance-caller-1874.md`
  - `tools/llmtool/llmtool/task_submission_cli.py`
  - `tools/llmtool/llmtool/task_advance.py` (outside `read_paths`)
  - `tools/llmtool/llmtool/review_dispatch.py` (outside `read_paths`)
- **Full write list:** `docs/design/followup-caller-selection-1874.md` (new file; `NEW_FILE_MIN_TIER = 4` in `spec_preflight.py`).
- **Smallest blocker:** Enumerating all CLI/orchestration callers of `advance` and `dispatch_review` under `tools/llmtool/` and `scripts/` requires read access outside `read_paths` (and `run_command` is unavailable to search or run `wc -c` / `git ls-tree`). To unblock: PM/tooling enumerates the exact caller module paths and measures the unique byte total + context/repair reserve of the complete `read` list before dispatch.
- **T tier:** Unmeasured (`BLOCKED`); expected to exceed `T5` (`16,384` bytes) and `T6` (`24,576` bytes) due to `task_submission_cli.py` (`12,276` lines).
- **Prerequisites:** None on PR #2708 (can be unblocked immediately once the caller `read` list is enumerated and measured).
- **Tests, formal review, and CI:**
  - Tests: none (docs-only).
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: `check (docs)`.

### Candidate S2 — `#1874` contract and binding design decision (docs-only, `BLOCKED`)

- **Status:** `BLOCKED` (pending PR #2708 acceptance and Candidate S1 output).
- **Route:** `AGY/OpenCode` (plus Owner/Coordinator/PM design sign-off; not a `gw-agent` candidate: contract/authority decision and large-module reads).
- **One verifiable result:** One agreed design specification resolving: (1) whether any "newest-Task" caller selection found in Candidate S1 is intended or a defect, (2) whether `task_binding.contract_digest`, `LLMTOOL-REVIEW-BINDING`, and `_review_ec2_line` stay single-Task or union across all sibling Tasks on the D6 (including intermediate Tasks and `write_paths`), and (3) how multi-Task contract unioning interacts with the 16-item acceptance limit (`_record_repair_task` in `tools/llmtool/llmtool/review_repair.py` and `REPAIR_ROOM_MAX_ACCEPTANCE = 15` in `tools/llmtool/scripts/spec_preflight.py`).
- **Known read inputs (incomplete):**
  - `docs/design/followup-contract-1874.md`
  - `docs/design/followup-advance-caller-1874.md`
  - `tools/llmtool/llmtool/direct_reassignment.py`
  - `tools/llmtool/llmtool/review_repair.py`
  - `tools/llmtool/llmtool/task_submission_cli.py`
  - `tools/llmtool/scripts/spec_preflight.py`
  - `tools/llmtool/llmtool/review_dispatch.py` (outside `read_paths`)
  - `tools/llmtool/llmtool/task_advance.py` (outside `read_paths`)
  - Future outputs: `docs/design/followup-caller-selection-1874.md` (Candidate S1) and merged `#1874` A evidence (PR #2708).
- **Write target (pending S2 scope):** `docs/design/followup-contract-1874.md` (or a dedicated decision document named by the PM).
- **Smallest blocker:** **Pending PR #2708 acceptance** (`#1874` A evidence section is not merged yet, per P3) and **pending Candidate S1** (`followup-caller-selection-1874.md` does not exist yet), so complete `read` paths and exact unique byte totals + context/repair reserve cannot be measured.
- **T tier:** Unmeasured (`BLOCKED`); requires `AGY/OpenCode` due to `task_submission_cli.py` (`12,276` lines) and contract/authority decisions.
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)** (do not use its in-progress files as a stable base).
  - Candidate S1 (`docs/design/followup-caller-selection-1874.md`).
- **Tests, formal review, and CI:**
  - Tests: none (docs-only).
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: `check (docs)`.

### Candidate S3 — P1 implementation: review-side prompt, binding, and dispatch changes (`BLOCKED`)

- **Status:** `BLOCKED` (pending PR #2708 acceptance and Candidate S2 design decision).
- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate: touches `tools/llmtool/llmtool/task_submission_cli.py` at `12,276` lines `>= DEFAULT_LARGE_FILE_LINES` and contract/authority-bound logic).
- **One verifiable result:** Implemented and tested review-side P1 fix across `task_submission_cli.py` (`run_task_review_prompt`, `_review_original_task`, `_review_ec2_line`), `review_dispatch.py` (`dispatch_review`, `_preflight`, `_verdict_binding_line`), and `task_advance.py` (`_advance_step`, `decide`) matching the contract decided in Candidate S2.
- **Smallest blocker:** **Pending PR #2708 acceptance** and **pending Candidate S2**; until Candidate S2 specifies the exact changes, the exact `write` files and the required `tools/llmtool/tests/test_*.py` modules (`check_read_scope` in `spec_preflight.py`) cannot be named or measured for unique read bytes + context/repair reserve.
- **T tier:** Unmeasured (`BLOCKED`); requires `AGY/OpenCode` (`task_submission_cli.py` is `12,276` lines `>= DEFAULT_LARGE_FILE_LINES`).
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)**.
  - Candidate S1 and Candidate S2.
- **Tests, formal review, and CI:**
  - Tests: EC2 `llmtool task remote-test` on the affected `tools/llmtool/tests/test_*.py` modules.
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: required exact-head CI checks (`check (Linux 3.14)`, `check (macOS 3.13)`, and `check (docs)` if docs change).

### Candidate S4 — P1 implementation: repair-side / caller-side changes (`BLOCKED`)

- **Status:** `BLOCKED` (pending PR #2708 acceptance, Candidate S1, and Candidate S2).
- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate: `tools/llmtool/llmtool/review_repair.py` is `1,391` lines, external caller/test modules are outside `read_paths`, and scope depends on Candidate S2).
- **One verifiable result:** Implemented and tested repair-side (`tools/llmtool/llmtool/review_repair.py` `repair_review`, `_record_repair_task`, `_delegate_repair`) or external-caller P1 changes if Candidate S2 determines that repair-Task contract inheritance or caller `task_id` selection must change.
- **Smallest blocker:** **Pending PR #2708 acceptance**, **pending Candidate S1** (exact caller modules unknown), and **pending Candidate S2** (whether repair-side or caller-side writes are needed is undecided), preventing complete `read`/`write` lists, `test_*.py` selection, and unique read byte measurement + context/repair reserve.
- **T tier:** Unmeasured (`BLOCKED`); requires `AGY/OpenCode`.
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)**.
  - Candidate S1 and Candidate S2 (and Candidate S3 if repair-side changes depend on the new review-side binding).
- **Tests, formal review, and CI:**
  - Tests: EC2 `llmtool task remote-test` on `tools/llmtool/tests/test_review_repair.py`, `tools/llmtool/tests/test_review_repair_acceptance_full.py`, and any affected caller tests.
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: required exact-head CI checks (`check (Linux 3.14)`, `check (macOS 3.13)`).
