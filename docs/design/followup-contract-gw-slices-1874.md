# #1874 follow-up — Executable split plan and `gw-agent` slice assessment

Facts in this plan were read at base SHA `8ac94844e363442a3fca01bb7770683ecc753173` from [followup-contract-1874.md](followup-contract-1874.md), [followup-advance-caller-1874.md](followup-advance-caller-1874.md), `tools/llmtool/llmtool/direct_reassignment.py`, `tools/llmtool/llmtool/review_repair.py`, `tools/llmtool/llmtool/task_submission_cli.py`, and `tools/llmtool/scripts/spec_preflight.py`. **Docs only:** this document is the only file created or modified; no code or test is changed. Gates: `check (docs)` CI and formal review by `codex-reviewer`.

## 1. Baseline, tier limits, and resolved dependencies

- **Merged starting point:**
  - [followup-contract-1874.md](followup-contract-1874.md): review-side facts (`tools/llmtool/llmtool/review_dispatch.py` `dispatch_review`, `_preflight`, `_review_rules`, `_verdict_binding_line`, `_create_d6`; `tools/llmtool/llmtool/task_submission_cli.py` `run_task_review_prompt`, `_review_original_task`, `_review_ec2_line`, `_review_ci_lines`) and repair-side facts (`tools/llmtool/llmtool/review_repair.py` `repair_review`, `_record_repair_task`, `_delegate_repair`, `_registered_d6`, `_merge_repair_read_paths`, `_admissible_repair_read_path`).
  - [followup-advance-caller-1874.md](followup-advance-caller-1874.md): `tools/llmtool/llmtool/task_advance.py` (`advance`, `_advance_step`, `decide`, `_pre_review_action`, `_review_refusal_rerun`, `_blocked_fallback_reviewer`, `_reviewer_fallback`) preserves the caller's exact `task_id` and does not select a newest sibling Task.
- **Resolved dependency (`direct_reassignment.authoring_route`):**
  - Read from `tools/llmtool/llmtool/direct_reassignment.py` (`authoring_route`, `effective_writer_admission`, `verified_repair_result`).
  - `authoring_route` only projects the active writer identity (`provider_profile` and `owner_assignment_ref=None` when a verified handoff changed the effective `executor_identity`) onto `developer` for later repairs. It does **not** select a `task_id` for `dispatch_review` and does **not** alter `goal`, `acceptance`, `read_paths`, or `write_paths`.
- **In-progress dependency (not merged):**
  - The `#1874` A evidence section (PR #2708) is **not merged yet**. Any slice that depends on its conclusions is marked **pending PR #2708 acceptance** and does not use its in-progress files as a stable base.
- **Tier limits (`LADDER_SIZES` in `tools/llmtool/scripts/spec_preflight.py`):**
  - `LADDER_SIZES` (`(files written, context bytes, gw-agent turns)`):
    - T1: `(1, 1536, 6)`
    - T2: `(1, 3072, 10)`
    - T3: `(2, 6144, 12)`
    - T4: `(3, 10240, 16)`
    - **T5: `(4, 16384, 16)`** (current `gw-agent` tier: max `4` written files, max `16,384` context bytes, max `16` turns)
    - T6: `(5, 24576, 16)` (`LADDER_MAX_TIER = 6`)
  - Turn formula in `spec_tier`: `turns = len(reads) + 2 * len(writes) + 1`.
  - `NEW_FILE_MIN_TIER = 4` (`spec_tier` returns at least T4 when any write path does not yet exist in the repo).
  - `DEFAULT_LARGE_FILE_LINES = 2000` (`large_file_lines` and `main` in `tools/llmtool/scripts/spec_preflight.py` check `target.read_bytes().count(b"\n") >= large_file_limit` **only** for `spec["write"]` when `spec.get("provider") == "gw-agent"`, refusing with `SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1`; it does **not** apply to `spec["read"]`).
  - `REPAIR_ROOM_MAX_ACCEPTANCE = 15` (`main` warns `SPEC_PREFLIGHT_WARN:ACCEPTANCE_NO_REPAIR_ROOM` when `len(spec["acceptance"]) > 15`, reserving 1 of the 16 acceptance items for `_record_repair_task`).
  - `check_read_scope` requires at least one `test_*.py` module in `read` when writing any `.py` file under `tools/` or `scripts/` (excluding `tools/llmtool/tests/`), and requires every repo path named in `acceptance` to appear in `read` or `write`.

## 2. Byte-measurement method, context/repair reserve, and `gw-agent` candidacy

- **Available measurements at base SHA `8ac94844e363442a3fca01bb7770683ecc753173`:**
  - Exact `wc -c` / `git ls-tree` commands could not be executed in this workspace because shell execution is unavailable here, so exact per-file byte counts are recorded as **unmeasured (`UNKNOWN`)** rather than guessed.
  - Observed line counts from `read_file` for the files in `read_paths`:
    - `tools/llmtool/llmtool/task_submission_cli.py`: `12,276` lines (`>= DEFAULT_LARGE_FILE_LINES = 2000`; reading only the first `800` lines already exceeds both T5's `16,384`-byte and T6's `24,576`-byte `context_bytes` limits).
    - `tools/llmtool/llmtool/review_repair.py`: `1,391` lines.
    - `tools/llmtool/scripts/spec_preflight.py`: `456` lines.
    - `docs/design/followup-contract-1874.md`: `386` lines.
    - `tools/llmtool/llmtool/direct_reassignment.py`: `382` lines.
    - `docs/design/followup-advance-caller-1874.md`: `84` lines.
  - Files outside `read_paths` (`tools/llmtool/llmtool/task_advance.py`, `tools/llmtool/llmtool/review_dispatch.py`, external CLI/orchestration caller modules, `tools/llmtool/tests/test_*.py`, and future outputs from PR #2708 / S1 / S2) cannot be read or measured from `read_paths` alone.
- **Context and repair reserve method (`spec_preflight.py` and `review_repair.py`):**
  - In `spec_preflight.py` `main`, `context_bytes` is measured from `context_file` (`os.path.getsize(context_file)`), which holds the rendered prompt, task contract, and full contents of every unique file in `read`.
  - **Numeric reserve at T5 (`16,384` `context_bytes` max):**
    1. **Prompt and contract reserve:** `2,048` bytes for `goal`, up to `15` `acceptance` items (`REPAIR_ROOM_MAX_ACCEPTANCE = 15`), `TEXT_DEVELOPER_CONTEXT_SEPARATOR` (`task_submission_cli.py`), and framing instructions. This leaves at most **`14,336` bytes** for unique read files on an initial dispatch.
    2. **Repair reserve:** `_record_repair_task` (`review_repair.py`) appends `1` `bounded_acceptance` item (`~200–300` bytes, reserving 1 of the 16 acceptance slots), and `_failed_event`, `_remote_test_failure`, and `_ci_failure` (`review_repair.py`) build a `repair_packet` capped at **`8,192` bytes** (`MAX_EXCERPT_CHARS = 2000`). Reserving `8,192 + 300 = 8,492` bytes for a worst-case repair dispatch leaves at most **`5,844` bytes** (`16,384 - 2,048 - 8,492`) for unique read files when full repair headroom is included.
- **`gw-agent` candidates: `none`.**
  - **Why no slice is a `gw-agent` candidate:**
    1. Every remaining `#1874` slice (S1, S2, S3, S4) requires reading `tools/llmtool/llmtool/task_submission_cli.py` in full (or, for S4, `review_repair.py` plus caller/test modules and S2 decisions). Even `task_submission_cli.py` alone (`12,276` lines; `> 24,576` bytes in its first `800` lines) exceeds T5's `16,384`-byte `context_bytes` cap (`14,336` bytes after prompt/contract reserve; `5,844` bytes after repair reserve) and T6's `24,576`-byte cap.
    2. S3 additionally writes `tools/llmtool/llmtool/task_submission_cli.py` (`12,276` lines `>= DEFAULT_LARGE_FILE_LINES = 2000`), which `spec_preflight.py` `main` refuses for `gw-agent` writes (`SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1`).
    3. S2, S3, and S4 also require contract/authority decisions or depend on unmerged PR #2708 conclusions.
    4. No file is dropped or fragmented to fake a smaller read, and no helper is invented solely to create `gw-agent` work.

## 3. Executable split plan (`none` immediately dispatchable; blocked candidates)

Per P4, no candidate slice below is immediately dispatchable (`none`) because exact byte totals cannot be measured without shell access, external caller modules and `test_*.py` modules lie outside `read_paths`, and PR #2708 is not merged yet. Each candidate states its verifiable result, known/missing read and write scope, reserve method, tier assessment, prerequisites, required gates, and **smallest blocker**.

**Immediately dispatchable executable slices:** `none`.

### Candidate S1 — External CLI / orchestration caller investigation (docs-only, `BLOCKED`)

- **Status:** `BLOCKED` (not immediately dispatchable; external caller modules and exact byte totals are unenumerated/unmeasured from `read_paths`).
- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate). Note: `spec_preflight.py` `main` checks `DEFAULT_LARGE_FILE_LINES = 2000` **only** on `spec["write"]`, and S1 writes a new docs file (`NEW_FILE_MIN_TIER = 4`), so the 2,000-line write check does **not** block S1. S1 is excluded from `gw-agent` on its **read-byte budget**: S1 must read `tools/llmtool/llmtool/task_submission_cli.py` in full (`12,276` lines; `> 24,576` bytes in the first `800` lines alone), which exceeds T5's `16,384`-byte `context_bytes` limit (`14,336` bytes after the `2,048`-byte prompt/contract reserve; `5,844` bytes after the `8,492`-byte repair reserve) and T6's `24,576`-byte limit.
- **One verifiable result:** One design note (`docs/design/followup-caller-selection-1874.md`) recording from code which CLI or orchestration callers outside `task_advance.py` (`advance`) and `review_repair.py` (`repair_review`) select the `task_id` passed to `advance` or `dispatch_review`, and whether any caller selects a "newest" sibling Task on the D6.
- **Known read list (incomplete):**
  - `docs/design/followup-contract-1874.md` (`386` lines; exact bytes `UNKNOWN`)
  - `docs/design/followup-advance-caller-1874.md` (`84` lines; exact bytes `UNKNOWN`)
  - `tools/llmtool/llmtool/task_submission_cli.py` (`12,276` lines; `> 24,576` bytes)
  - `tools/llmtool/llmtool/task_advance.py` (outside `read_paths`)
  - `tools/llmtool/llmtool/review_dispatch.py` (outside `read_paths`)
  - External CLI/orchestration caller modules under `tools/llmtool/` and `scripts/` (unenumerated; outside `read_paths`)
- **Full write list:** `docs/design/followup-caller-selection-1874.md` (new file; `NEW_FILE_MIN_TIER = 4`).
- **Context and repair reserve:** `2,048` bytes prompt/contract + `8,492` bytes repair reserve (`10,540` bytes total reserve), applied against `LADDER_SIZES` (`16,384` bytes at T5).
- **T tier:** Exceeds T5 (`16,384` bytes) and T6 (`24,576` bytes) due to `task_submission_cli.py` alone; exact total unmeasured (`BLOCKED`).
- **Smallest blocker:** Enumerating all CLI/orchestration callers of `advance` and `dispatch_review` under `tools/llmtool/` and `scripts/` (outside `read_paths`) and measuring exact unique read bytes at base SHA before dispatch.
- **Prerequisites:** None on PR #2708.
- **Tests, formal review, and CI:**
  - Tests: none (docs-only).
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: `check (docs)`.

### Candidate S2 — `#1874` contract and binding design decision (docs-only, `BLOCKED`)

- **Status:** `BLOCKED` (pending PR #2708 acceptance and Candidate S1 output).
- **Route:** `AGY/OpenCode` (plus Owner/Coordinator/PM design sign-off; not a `gw-agent` candidate: contract/authority decision and full `task_submission_cli.py` read exceeding T5 `16,384` bytes).
- **One verifiable result:** One agreed design specification resolving: (1) whether any "newest-Task" caller selection found in Candidate S1 is intended or a defect, (2) whether `task_binding.contract_digest`, `LLMTOOL-REVIEW-BINDING`, and `_review_ec2_line` stay single-Task or union across all sibling Tasks on the D6 (including intermediate Tasks and `write_paths`), and (3) how multi-Task contract unioning interacts with the 16-item acceptance limit (`_record_repair_task` in `tools/llmtool/llmtool/review_repair.py` and `REPAIR_ROOM_MAX_ACCEPTANCE = 15` in `tools/llmtool/scripts/spec_preflight.py`).
- **Known read list (incomplete):**
  - `docs/design/followup-contract-1874.md` (`386` lines; exact bytes `UNKNOWN`)
  - `docs/design/followup-advance-caller-1874.md` (`84` lines; exact bytes `UNKNOWN`)
  - `tools/llmtool/llmtool/direct_reassignment.py` (`382` lines; exact bytes `UNKNOWN`)
  - `tools/llmtool/llmtool/review_repair.py` (`1,391` lines; exact bytes `UNKNOWN`)
  - `tools/llmtool/llmtool/task_submission_cli.py` (`12,276` lines; `> 24,576` bytes)
  - `tools/llmtool/scripts/spec_preflight.py` (`456` lines; exact bytes `UNKNOWN`)
  - `tools/llmtool/llmtool/review_dispatch.py` (outside `read_paths`)
  - `tools/llmtool/llmtool/task_advance.py` (outside `read_paths`)
  - Future outputs: `docs/design/followup-caller-selection-1874.md` (Candidate S1) and merged `#1874` A evidence (PR #2708).
- **Write list (pending S2 scope):** `docs/design/followup-contract-1874.md` (or a dedicated decision document named by the PM).
- **Context and repair reserve:** `2,048` bytes prompt/contract + `8,492` bytes repair reserve (`10,540` bytes total reserve).
- **T tier:** Exceeds T5 (`16,384` bytes) and T6 (`24,576` bytes); exact total unmeasured (`BLOCKED`).
- **Smallest blocker:** **Pending PR #2708 acceptance** (`#1874` A evidence section is not merged yet, per P3) and **pending Candidate S1** (`followup-caller-selection-1874.md` does not exist yet), so complete `read` paths and exact unique byte totals cannot be measured.
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)** (do not use its in-progress files as a stable base).
  - Candidate S1 (`docs/design/followup-caller-selection-1874.md`).
- **Tests, formal review, and CI:**
  - Tests: none (docs-only).
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: `check (docs)`.

### Candidate S3 — P1 implementation: review-side prompt, binding, and dispatch changes (`BLOCKED`)

- **Status:** `BLOCKED` (pending PR #2708 acceptance and Candidate S2 design decision).
- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate: writes `tools/llmtool/llmtool/task_submission_cli.py` at `12,276` lines `>= DEFAULT_LARGE_FILE_LINES = 2000`, which `spec_preflight.py` `main` refuses for `gw-agent`, and exceeds T5 `16,384` context bytes).
- **One verifiable result:** Implemented and tested review-side P1 fix across `tools/llmtool/llmtool/task_submission_cli.py` (`run_task_review_prompt`, `_review_original_task`, `_review_ec2_line`), `tools/llmtool/llmtool/review_dispatch.py` (`dispatch_review`, `_preflight`, `_verdict_binding_line`), and `tools/llmtool/llmtool/task_advance.py` (`_advance_step`, `decide`) matching the contract decided in Candidate S2.
- **Read/write list:** Incomplete (`BLOCKED` pending S2). Requires the modified modules above plus their corresponding `tools/llmtool/tests/test_*.py` modules (`check_read_scope` in `spec_preflight.py`).
- **Context and repair reserve:** `2,048` bytes prompt/contract + `8,492` bytes repair reserve (`10,540` bytes total reserve).
- **T tier:** Exceeds T5 (`16,384` bytes) and T6 (`24,576` bytes); exact total unmeasured (`BLOCKED`).
- **Smallest blocker:** **Pending PR #2708 acceptance** and **pending Candidate S2**; until Candidate S2 specifies the exact changes, the exact `write` files and required `tools/llmtool/tests/test_*.py` modules cannot be named or measured.
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)**.
  - Candidate S1 and Candidate S2.
- **Tests, formal review, and CI:**
  - Tests: EC2 `llmtool task remote-test` on the affected `tools/llmtool/tests/test_*.py` modules.
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: required exact-head CI checks (`check (Linux 3.14)`, `check (macOS 3.13)`, and `check (docs)` if docs change).

### Candidate S4 — P1 implementation: repair-side / caller-side changes (`BLOCKED`)

- **Status:** `BLOCKED` (pending PR #2708 acceptance, Candidate S1, and Candidate S2).
- **Route:** `AGY/OpenCode` (not a `gw-agent` candidate: depends on Candidate S2 contract/authority decisions, reads `tools/llmtool/llmtool/review_repair.py` at `1,391` lines plus external caller and `test_*.py` modules outside `read_paths`).
- **One verifiable result:** Implemented and tested repair-side (`tools/llmtool/llmtool/review_repair.py` `repair_review`, `_record_repair_task`, `_delegate_repair`) or external-caller P1 changes if Candidate S2 determines that repair-Task contract inheritance or caller `task_id` selection must change.
- **Read/write list:** Incomplete (`BLOCKED` pending S1 and S2). Requires `tools/llmtool/llmtool/review_repair.py`, affected caller modules, and `tools/llmtool/tests/test_review_repair.py` / `tools/llmtool/tests/test_review_repair_acceptance_full.py` (`check_read_scope` in `spec_preflight.py`).
- **Context and repair reserve:** `2,048` bytes prompt/contract + `8,492` bytes repair reserve (`10,540` bytes total reserve).
- **T tier:** Unmeasured (`BLOCKED` pending S1 and S2 scope).
- **Smallest blocker:** **Pending PR #2708 acceptance**, **pending Candidate S1** (exact caller modules unknown), and **pending Candidate S2** (whether repair-side or caller-side writes are needed is undecided), preventing complete `read`/`write` lists and unique read byte measurement.
- **Prerequisites:**
  - **Pending acceptance of `#1874` A evidence section (PR #2708)**.
  - Candidate S1 and Candidate S2 (and Candidate S3 if repair-side changes depend on the new review-side binding).
- **Tests, formal review, and CI:**
  - Tests: EC2 `llmtool task remote-test` on `tools/llmtool/tests/test_review_repair.py`, `tools/llmtool/tests/test_review_repair_acceptance_full.py`, and any affected caller tests.
  - Formal review: cross-family formal review (`claude-reviewer` or `codex-reviewer` from a different model family than the authoring model).
  - CI: required exact-head CI checks (`check (Linux 3.14)`, `check (macOS 3.13)`).
