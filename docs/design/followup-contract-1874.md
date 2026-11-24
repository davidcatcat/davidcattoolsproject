# #1874 follow-up contract — review-side facts (slice 1)

#2367 P1-4, slice 1. **Design and acceptance work only: no P1 code is
written here.** This note records, from code, the **review-side** facts only
(`tools/llmtool/llmtool/review_dispatch.py` and
`tools/llmtool/llmtool/task_submission_cli.py` on this head). Everything about
the **repair side** is explicitly marked **UNKNOWN (slice 2)**. No code or test
is changed; this file is the only write.

## A1 — How the review dispatch picks the Task whose contract the Reviewer gets for a PR head

The review-side dispatch entry point is `dispatch_review` in
`tools/llmtool/llmtool/review_dispatch.py`, which delegates prompt rendering to
`run_task_review_prompt` in `tools/llmtool/llmtool/task_submission_cli.py`.

### The dispatched Task binding is caller-driven

- `dispatch_review` (`tools/llmtool/llmtool/review_dispatch.py`) takes
  `task_id` as an explicit parameter and loads that single Task via
  `store.get(task_id)`. There is **no code in `review_dispatch.py` that
  enumerates sibling Tasks sharing a D6 or selects a "newest" or "widest"
  Task** — the bound `task_id` is entirely determined by the caller.
- In `_preflight` (`tools/llmtool/llmtool/review_dispatch.py`), that Task's
  `task_binding` is validated by `task_completion.validate_task_binding`
  (with `task_id`, `run_id`, and `contract_digest`). A missing or inconsistent
  binding is refused (`REVIEW_PROMPT_BINDING_MISSING`).
- `dispatch_review` and `_verdict_binding_line`
  (`tools/llmtool/llmtool/review_dispatch.py`) bind the review claim and the
  verdict binding line to **that single Task**:
  `LLMTOOL-REVIEW-BINDING v1 task=<task_id> run=<run_id> contract=<contract_digest> head=<head>`.

### What Task contracts and prompt contents the Reviewer actually receives

When `dispatch_review` (`tools/llmtool/llmtool/review_dispatch.py`) builds the
prompt for either `codex-reviewer` or `claude-reviewer`:

1. **Rules prefix (`_review_rules`):** `dispatch_review` calls
   `_review_rules` (`tools/llmtool/llmtool/review_dispatch.py`), which reads
   role cards from the authenticated `main` commit and wraps them with
   `_REVIEW_RULES_HEADER` and `_REVIEW_RULES_FOOTER`. `dispatch_review`
   prepends this `rules_prefix` verbatim to the rendered prompt.
2. **PR and D6 scope (`run_task_review_prompt`):** `run_task_review_prompt`
   (`tools/llmtool/llmtool/task_submission_cli.py`) verifies `task_binding`,
   the executor D6 registration (`result_sha`, snapshot, and
   `result_head_admitted`), the reviewer D6 registration, and `pulls/{pr}`
   (`pr['head']['sha'] == expected_head`, plus `base_sha`). It emits:
   - `Review PR #<pr> at exact HEAD <expected_head>.`
   - `Scope: git diff <base_sha>...<expected_head>`
   - Read-only / no-mutation instructions, plus role-specific instructions for
     `Codex` (no local tests, do not post to GitHub) or `Claude` (run only
     relevant tests, post verdict and end session).
3. **Current Task contract (`run_task_review_prompt`):** `run_task_review_prompt`
   validates `task.get('pm_assignment')` via its inner `checked_contract`
   helper (requiring `schema_version == 1`, `route`, `risk`, `goal`, and 1–16
   `acceptance` items, all screened by `_screen_text`) and emits:
   - `<<< BEGIN PM TASK CONTRACT DATA >>>`
   - `Task goal: <pm_assignment['goal']>`
   - Numbered acceptance items `1. ...` from `pm_assignment['acceptance']`
   - (For `Claude`, `Verdict FAIL if any acceptance item is unmet; a missing required test is unmet.`
     inside the block)
   - `<<< END PM TASK CONTRACT DATA >>>`
4. **Earliest sibling Task contract on the same D6 (`_review_original_task`):**
   Unlike `review_dispatch.py`, `run_task_review_prompt` **does** inspect
   sibling Tasks on the same D6 via `_review_original_task`
   (`tools/llmtool/llmtool/task_submission_cli.py`). `_review_original_task`
   scans `store.completion_d6_task_ids(executor_task)` (or `store.task_ids()`
   if `completion_d6_task_ids` is absent) for non-abandoned other Tasks whose
   `completion.executor_task_id == executor_task` and
   `created_at < task['created_at']`, and picks the **earliest** one
   (`min` by `(created_at, task_id)`). If found, `run_task_review_prompt`
   validates the original Task's `pm_assignment` with `checked_contract` and
   appends:
   - `<<< BEGIN ORIGINAL TASK CONTRACT DATA >>>`
   - `Original Task id`, `Original Task goal`, and numbered original
     `acceptance` items
   - `<<< END ORIGINAL TASK CONTRACT DATA >>>`
   - `Repair delta: current acceptance items not in the original contract: <indices or none>.`
   - `Judge the PR against both contracts; where a current item replaces an original one, the current item governs; an original item not replaced still applies.`
5. **Test evidence and verdict format (`run_task_review_prompt`):**
   - For `Codex`, `run_task_review_prompt` appends `_review_ec2_line(task, expected_head)`
     (the EC2 or GitHub-hosted `remote-test` receipt summary on `task` for
     `expected_head`, or `_REVIEW_EC2_NONE`) and `*_review_ci_lines(expected_head)`
     (latest status/conclusion for `check (Linux 3.14)`, `check (macOS 3.13)`,
     and `check (docs)`), followed by finding-format rules, PASS/non-PASS
     header/marker templates (including `binding_line` for the single bound
     `task_id`), and JSON output instructions.
   - For `Claude`, `run_task_review_prompt` appends the exact PASS/non-PASS
     verdict marker, header, `task_completion._FORMAL_ACCEPT`, and
     `binding_line` to post.
6. **Write scope (`_create_d6` and `run_task_review_prompt`):**
   - `_create_d6` (`tools/llmtool/llmtool/review_dispatch.py`) creates the
     Reviewer D6 as a **read-only** worktree at `base_sha=head` with lease
     `"read-only formal review of executor task <executor_task> at <base_sha>"`.
   - `pm_assignment` (validated by `checked_contract` in
     `run_task_review_prompt`) carries `goal` and `acceptance`, not
     `developer.write_paths`; the Reviewer prompt scopes the code under review
     to `git diff <base_sha>...<expected_head>`.

### Where the head (and hence the PR diff) comes from

- In `_preflight` (`tools/llmtool/llmtool/review_dispatch.py`) and
  `run_task_review_prompt` (`tools/llmtool/llmtool/task_submission_cli.py`),
  the executor D6 registration is read via `task_completion.read_d6_registration`
  using `completion['evidence']['common_dir']` and `executor_task`.
- `head = executor.result_sha` is checked against a clean git snapshot
  (`task_completion.collect_git_snapshot`) and against the recorded result
  (`result_head_admitted` in `tools/llmtool/llmtool/task_submission_cli.py`).
- Both `_preflight` and `run_task_review_prompt` fetch `pulls/{pr_number}` and
  refuse (`REVIEW_PROMPT_PR_HEAD_MISMATCH`) unless `pull['head']['sha'] == head`.
  The PR `base['sha']` is used by `run_task_review_prompt` for
  `Scope: git diff <base_sha>...<expected_head>`.

## What happens when several Tasks share the D6

- A follow-up Task records `completion.executor_task_id` and
  `completion.evidence.common_dir` pointing at the **same** executor D6, whose
  `result_sha` equals the PR head (`pulls/{pr}.head.sha`) reflecting the
  **accumulated** changes on that D6.
- `dispatch_review` (`tools/llmtool/llmtool/review_dispatch.py`) still binds
  the claim, `contract_digest`, and `_verdict_binding_line` to **only** the
  single caller-supplied `task_id`.
- `run_task_review_prompt` (`tools/llmtool/llmtool/task_submission_cli.py`)
  renders `Scope: git diff <base_sha>...<expected_head>` (the full PR diff),
  the bound `task_id`'s `pm_assignment`, and — when `_review_original_task`
  finds an earlier non-abandoned Task on the same `executor_task` — that
  **single earliest** Task's `pm_assignment` plus the repair delta.
- Consequently, when more than two Tasks share a D6, intermediate Tasks'
  contracts are not included in the prompt; if `_review_original_task` finds
  no earlier non-abandoned Task on the D6 (for example, if the earlier Task
  was abandoned or `store.task_ids` is absent), only the dispatched `task_id`'s
  `pm_assignment` is included. In all cases, `write_paths` is absent from the
  prompt contract data, `_review_ec2_line` checks receipts on the single
  dispatched `task`, and `LLMTOOL-REVIEW-BINDING` binds only that single
  `task_id`.

## A2 — Which #1874 symptom this explains

**Symptom #1874** (the review scope is **narrower than the PR diff** on PR
#1866 and PR #1863) is explained by the review-side facts above:

- The PR head (`pulls/{pr}.head.sha == executor.result_sha`) and the prompt's
  `Scope: git diff <base_sha>...<expected_head>` span the **accumulated**
  work of all Tasks on the shared D6.
- Meanwhile, `dispatch_review`, `_preflight`, and `_verdict_binding_line`
  (`tools/llmtool/llmtool/review_dispatch.py`) bind the review claim and
  `contract_digest` to the **single caller-supplied `task_id`**.
- On this head, `run_task_review_prompt` and `_review_original_task`
  (`tools/llmtool/llmtool/task_submission_cli.py`) partially widen the prompt
  text by appending the **earliest** non-abandoned Task's `pm_assignment` when
  one exists on the same D6, but intermediate Tasks' contracts are still not
  included, `write_paths` is not part of `pm_assignment`, and the binding
  (`task_binding.contract_digest`, `LLMTOOL-REVIEW-BINDING`, and
  `_review_ec2_line`) remains tied to the single dispatched `task_id`.

This note **reports the review-side facts only**. It does not propose or
implement a fix.

## UNKNOWN (slice 2 — repair side)

The following are **not** determinable from the read set
(`tools/llmtool/llmtool/review_dispatch.py` and
`tools/llmtool/llmtool/task_submission_cli.py`) or belong to the later repair
design, and are marked **UNKNOWN** for slice 2:

- **UNKNOWN:** which caller / CLI layer chooses the `task_id` handed to
  `dispatch_review`, and whether that caller implements the "newest Task on
  the D6" selection observed in #1874.
- **UNKNOWN:** whether a "newest-Task" caller selection is *intended* or is
  itself part of the defect to be repaired.
- **UNKNOWN:** whether a Task's `task_binding.contract_digest` and
  `LLMTOOL-REVIEW-BINDING` are intended to stay bound to one Task or be
  widened/unioned across all Tasks on the D6 (including intermediate Tasks
  between the earliest and newest).
- **UNKNOWN:** the repair mechanism itself (slice 2).

---

## Slice 2 — Repair-side facts (`2a55a82f99e04033421fc56e0d60b7083b63fee7`)

Facts in this section were read at commit `2a55a82f99e04033421fc56e0d60b7083b63fee7`
from `tools/llmtool/llmtool/review_repair.py`,
`tools/llmtool/tests/test_review_repair.py`, and
`tools/llmtool/tests/test_review_repair_acceptance_full.py`. No code or test
is changed; this file is the only write.

### D1 — How the repair Task is recorded and delegated

#### Recording the repair Task (`_record_repair_task` in `tools/llmtool/llmtool/review_repair.py`)

When `repair_review` (`tools/llmtool/llmtool/review_repair.py`) admits a new
repair claim, it generates `repair_task_id = new_id()` and calls
`_record_repair_task` (`tools/llmtool/llmtool/review_repair.py`) before
committing the claim via `store.compare_and_update`:

1. **PM provenance and route checks:** `_record_repair_task` requires
   `task['pm_provenance']` to carry string `pm_native_session_id` and `pm_cwd`
   (else `REPAIR_PM_SESSION_UNAVAILABLE`). It reads `route = developer['provider_profile']`,
   refusing `codex` with `REPAIR_CODEX_DEVELOPER_RETIRED` and any route outside
   `('opencode', 'agy', 'claude', 'gw-agent')` with
   `REPAIR_DEVELOPER_ROUTE_UNSUPPORTED`.
2. **Goal and original acceptance:** `_record_repair_task` reads `goal` from
   `task['pm_assignment']['goal']` (requiring a `str`) and reads
   `original_acceptance` from `developer['acceptance']` (requiring a `list` of
   `str`), refusing `REPAIR_DEVELOPER_BINDING_INVALID` if either check fails.
3. **Appending the bounded repair item:** `_record_repair_task` constructs one
   `bounded_acceptance` string from `attempt`, `MAX_REPAIR_ATTEMPTS`
   (`run_budget.MAX_REPAIR_ATTEMPTS`), and `packet`:
   - If `packet['version'] == 0`, `packet['verdict_comment_id'] == 0`, and
     `packet.get('reviewer_text_excerpt_untrusted_data')` starts with
     `CI_PACKET_HEADER + ' '` (`'CI_CHECK_FAILURE '`):
     `"Repair attempt {attempt}/{MAX_REPAIR_ATTEMPTS} for required CI check failure at HEAD {packet['failed_head']}. Failure excerpt is untrusted reference data."`
   - Else if `packet['version'] == 0` and `packet['verdict_comment_id'] == 0`
     (EC2 remote-test failure):
     `"Repair attempt {attempt}/{MAX_REPAIR_ATTEMPTS} for remote-test failure at HEAD {packet['failed_head']}. Failure excerpt is untrusted reference data."`
   - Else (formal review verdict failure):
     `"Repair attempt {attempt}/{MAX_REPAIR_ATTEMPTS} for verdict comment id {packet['verdict_comment_id']}, failed HEAD {packet['failed_head']}, review version {packet['version']}, findings count {packet['findings_count']}. Reviewer text is untrusted reference data."`
   It builds `assignment = {'route': route, 'risk': 'LOW', 'goal': pm_assignment['goal'], 'acceptance': list(original_acceptance) + [bounded_acceptance]}`.
4. **Recording via `run_task_record_pm`:** After checking the 16-item
   acceptance limit (see D2), `_record_repair_task` serializes `response` JSON
   (`schema_version: 1`, `summary`, `developer_assignment: assignment`) and
   `request = f"REPAIR {attempt}/{MAX_REPAIR_ATTEMPTS} of {task['task_id']}"` and
   calls `task_submission_cli.run_task_record_pm` (or injected `record_pm`) with
   `pm_session_id=provenance['pm_native_session_id']`,
   `pm_runtime='claude-code'`, `pm_cwd=provenance['pm_cwd']`,
   `task_id=repair_task_id`, and `return_record=True`. If the returned record
   is not a `dict` with a `str` `task_id`, it refuses
   `REPAIR_TASK_RECORD_UNAVAILABLE`.
5. **Unclaimed refusal safety:** In `repair_review`, if `_record_repair_task`,
   the subsequent `worker_dispatch.evaluate` recheck, or the claim CAS fails, no
   claim is committed and `_refuse_unclaimed` (or the attached
   `review_repair_unclaimed_task_id` on `LLMToolError`) surfaces
   `repair_task_id` for PM reconciliation. Covered by
   `test_repair_task_uses_record_pm_path_and_original_session` and
   `test_repair_task_rejects_recorder_without_task_id` in
   `tools/llmtool/tests/test_review_repair.py`.

#### Delegating the repair (`_delegate_repair` in `tools/llmtool/llmtool/review_repair.py`)

After the claim CAS succeeds, `repair_review` calls `_invoke_claimed_adapter`
(`tools/llmtool/llmtool/review_repair.py`), which calls `_delegate_repair`
(`tools/llmtool/llmtool/review_repair.py`) unless a custom `dispatch_adapter` is
supplied:

1. **Binding and data-class checks:** `_delegate_repair` reads `acceptance`
   from `repair_task['pm_assignment']['acceptance']` and `read_paths` and
   `write_paths` from `developer`. If any of the three is not a `list`, it
   refuses `REPAIR_DEVELOPER_BINDING_INVALID` (`prelaunch=True`). For
   `provider_profile == 'gw-agent'`, it requires `data_class` in
   `('SYNTHETIC', 'PUBLIC_REPO')` (else `REPAIR_DATA_CLASS_INVALID`,
   `prelaunch=True`).
2. **Executor D6 verification:** `_delegate_repair` loads
   `original_task = store.get(packet['task'])` and calls `_registered_d6`
   (`tools/llmtool/llmtool/review_repair.py`), which calls
   `task_completion.read_d6_registration` and
   `task_completion.collect_git_snapshot` and requires `snapshot.head`, `root`,
   `common_dir`, and `branch` to match `registration` (else `REPAIR_HEAD_MOVED`).
3. **Read-path extension for failing tests:** Before delegation, `repair_review`
   may extend `developer['read_paths']` via `_merge_repair_read_paths` and
   `_admissible_repair_read_path` (`tools/llmtool/llmtool/review_repair.py`)
   using `extra_read_paths` (or `ci_failure['failing_modules']` when
   `extra_read_paths is None`), adding up to `_MAX_EXTRA_READ_PATHS` (16)
   admissible non-sensitive test module paths (matching
   `_ADMISSIBLE_TEST_READ_PATH`, not in `write_paths`, and not
   `sensitive_paths.is_sensitive`) up to `max(len(merged), _MAX_TOTAL_READ_PATHS)`
   (32).
4. **Passed delegation arguments:** `_delegate_repair` constructs an
   `argparse.Namespace` with:
   - `task_id=repair_task['task_id']`
   - `provider=profile` (`'direct'` mode for `codex`, `agy`, `claude`,
     `gw-agent`; `'proposal'` otherwise)
   - `owner_ref=developer.get('owner_assignment_ref')`, `selection_source='PM'`
   - `executor_task=executor_task`, `cwd=registration.root` (the registered
     executor D6 worktree)
   - `read_path=list(read_paths)`, `write_path=list(write_paths)`
   - `acceptance=list(acceptance)` (from `repair_task['pm_assignment']['acceptance']`)
   - `egress_basis=developer.get('egress_basis')`, `repair_packet=packet`,
     `data_class=data_class`
   It invokes `task_submission_cli.run_task_delegate` (or injected `delegate`)
   wrapped in `task_submission_cli._delegate_with_scope_advice` so findings
   naming files outside `read_paths` are recorded as advice without altering or
   blocking the repair's read scope. Covered by
   `test_default_adapter_rebuilds_original_direct_developer_request`,
   `test_agy_uses_the_owner_gated_repair_route`,
   `test_gw_agent_repair_delegate_args`,
   `test_gw_agent_repair_refuses_missing_or_unknown_data_class`,
   `test_gw_agent_repair_uses_the_data_class_stored_at_delegate_time`, and
   `test_repair_delegate_refuses_unregistered_or_moved_d6_before_provider` in
   `tools/llmtool/tests/test_review_repair.py`.

### D2 — The 16-item acceptance limit

- In `_record_repair_task` (`tools/llmtool/llmtool/review_repair.py`),
  `assignment['acceptance']` is `list(original_acceptance) + [bounded_acceptance]`,
  where `original_acceptance` comes from `developer['acceptance']` and
  `bounded_acceptance` adds 1 item.
- Immediately after constructing `assignment`, `_record_repair_task` checks
  `if len(assignment['acceptance']) > 16: _refuse('REPAIR_ACCEPTANCE_FULL')`.
- Consequently, `developer['acceptance']` can hold at most **15** items (yielding
  16 total items in the recorded `repair_task`). When `developer['acceptance']`
  already has 16 items, appending the bounded repair item yields 17 items and
  triggers `REPAIR_ACCEPTANCE_FULL` before `run_task_record_pm` is called (and
  before any repair claim is committed).
- Covered in `tools/llmtool/tests/test_review_repair_acceptance_full.py`
  (`ReviewRepairAcceptanceFullTests`):
  - `test_sixteen_original_items_refuse_acceptance_full` verifies that 16
    items in `developer['acceptance']` raise `LLMToolError` with
    `review_repair_code == 'REPAIR_ACCEPTANCE_FULL'` and leave
    `record_pm` uncalled.
  - `test_fifteen_original_items_succeeds_and_records_acceptance` verifies that
    15 items in `developer['acceptance']` succeed, call `record_pm` once, and
    produce a 16-item `developer_assignment['acceptance']` ending with
    `'Repair attempt 1/...'`.
  - `test_non_list_developer_acceptance_refuses_binding_invalid` and
    `test_missing_pm_assignment_goal_refuses_binding_invalid` cover the
    `REPAIR_DEVELOPER_BINDING_INVALID` guards in `_record_repair_task`.

### D3 — Inheritance of goal, acceptance, and scope across multiple repairs in one lineage

- **Root-Task enforcement:** `repair_review`
  (`tools/llmtool/llmtool/review_repair.py`) scans `store.task_ids()` and
  refuses `REPAIR_ROOT_TASK_REQUIRED` if any other Task's `review_repair_claims`
  already names `task_id` as a `repair_task_id` (covered by
  `test_child_task_named_by_root_claim_refuses_without_writes_or_calls` in
  `tools/llmtool/tests/test_review_repair.py`). Every repair in a lineage is
  therefore initiated against the root `task_id` passed to `repair_review`.
- **Source of `goal`, `acceptance`, and `read_paths` / `write_paths`:**
  - `repair_review` loads `task = store.get(task_id)` and resolves
    `developer = direct_reassignment.authoring_route(store, task, task.get('developer'), executor_task)`.
  - **Goal:** `_record_repair_task` takes `goal` from `task['pm_assignment']['goal']`
    on that root `task`.
  - **Acceptance:** `_record_repair_task` takes `original_acceptance` from
    `developer['acceptance']` (not from any previous `repair_task`) and appends
    only the single `bounded_acceptance` item for the current `attempt`. Earlier
    repairs' `bounded_acceptance` items do not accumulate across repair Tasks.
  - **Read/write scope:** `_delegate_repair` takes `read_paths` and
    `write_paths` from that same `developer` dict (with `read_paths` optionally
    extended for that single call by `_merge_repair_read_paths`).
- **Lineage counting vs. contract inheritance:** `repair_review` reads
  `lineage_counts.lineage_counts` (with `include_successors=True`),
  `lineage_counts.chain_debug_used`, and `start_failure.inherited_rca` for
  repair/debug caps and inherited `RCA_CONFIRMED` reports across a continuation
  chain, while the new `repair_task`'s `goal`, `acceptance`, `read_paths`, and
  `write_paths` still come from the `task` and `developer` passed to
  `repair_review`.
- **Claim replay:** When `repair_review` finds a live matching claim on `task`
  (or a replacement candidate under `#2438`), it replays via
  `_replay_claimed_test_repair` or `_delegate_repair` using the existing
  `repair_task` without calling `_record_repair_task` again.

### D4 — Mapping to #1874 symptoms and remaining UNKNOWNs

1. **Shared D6 across sibling Tasks:** `_record_repair_task` creates a new
   `repair_task_id` with its own `pm_assignment`, and `_delegate_repair`
   executes it against the **same** registered executor D6 (`executor_task`,
   `cwd=registration.root`). This produces multiple sibling Tasks on one D6.
2. **Why `review_repair.py` does not cause "newest-Task" `dispatch_review` selection:**
   When `repair_review` runs with `auto_review=True` and `_current_d6_head`
   advances past `packet['failed_head']` (tested by
   `test_auto_review_calls_next_version` and
   `test_auto_review_waits_until_repair_is_published` in
   `tools/llmtool/tests/test_review_repair.py`), it calls
   `dispatch_review(store=store, task_id=task_id, executor_task=executor_task, pr_number=pr_number, version=packet_version + 1)`
   with the **root `task_id`**, not `repair_task_id`. Thus, `review_repair.py`
   does not itself pass a newer repair `task_id` to `dispatch_review`.
3. **Why repair Tasks have non-accumulating contracts and no `write_paths` in `pm_assignment`:**
   Each `repair_task` recorded by `_record_repair_task` carries
   `developer['acceptance']` plus only its own single `bounded_acceptance` item
   (capped at 16 total items, refusing `REPAIR_ACCEPTANCE_FULL` above 15
   original items), and its `pm_assignment` does not carry `write_paths`. If an
   external caller dispatches `dispatch_review` on a later sibling Task,
   intermediate repair Tasks' `bounded_acceptance` items are not accumulated
   into that Task's `pm_assignment` (and slice 1 showed `_review_original_task`
   adds only the single earliest non-abandoned Task).

#### Remaining UNKNOWNs (outside the slice 2 read set)

- **UNKNOWN:** which CLI or orchestration caller outside `review_repair.py`
  selects the `task_id` passed to `dispatch_review` when `auto_review` is not
  used, and whether that caller selects the newest Task on the D6.
- **UNKNOWN:** what `direct_reassignment.authoring_route` reads or modifies when
  resolving `developer` across reassigned routes (its implementation is outside
  the slice 2 read set).
- **UNKNOWN:** whether a "newest-Task" caller selection is intended or a defect,
  and how cross-D6 / multi-Task contract integration (`task_binding.contract_digest`,
  `LLMTOOL-REVIEW-BINDING`, or unioning contracts across sibling Tasks) is
  intended to work in the P1 fix.
