# #1874 follow-up — `task_advance.py` review-dispatch caller facts

Facts in this note were read at `main` SHA `b7ac6c98808d4cefb2303b0fda84fc6b24a54dd6` from `tools/llmtool/llmtool/task_advance.py` and [followup-contract-1874.md](followup-contract-1874.md). **Docs only:** no code or test is changed, no fix is designed here, and no live verification is claimed. This document is the only file created; `docs/design/followup-contract-1874.md` is unchanged.

## D2 — Caller `task_id` preservation (no newest-sibling selection)

`tools/llmtool/llmtool/task_advance.py` **keeps the exact `task_id` that the caller passed**. It does **not** enumerate sibling Tasks sharing a D6 and does **not** select a "newest" sibling Task.

- `advance` (`tools/llmtool/llmtool/task_advance.py`) takes `task_id` as a required keyword-only argument, acquires `store.advance_lease(task_id)`, builds `_external_action_guard(store, task_id)`, wraps `dispatch_review_call or review_dispatch.dispatch_review` with `_guarded_call`, and passes that same `task_id` to `_advance_step`.
- `_advance_step` (`tools/llmtool/llmtool/task_advance.py`) loads `record = store.get(task_id)` and uses that `task_id` throughout the step, including when calling `invoke` (`dispatch_review_call or review_dispatch.dispatch_review`).
- The only other Task records `_advance_step` reads via `store.get` are `claim['repair_task_id']` entries from `record['review_repair_claims']` (to derive `repair_state` via `derive_repair_state` and `unknown_reason` via `unknown_repair_reason`); it never substitutes a repair Task or any other sibling Task for `task_id`.

## D1 — How `task_advance.py` reaches `DISPATCH_REVIEW` and what it passes

### Common provenance of `task_id`, `executor_task`, and `pr_number`

Before entering any `DISPATCH_REVIEW` branch, `_advance_step` (`tools/llmtool/llmtool/task_advance.py`) resolves:

- **`task_id`:** the `task_id` parameter passed to `advance` and `_advance_step`.
- **`executor_task`:** read from `record.get('completion', {}).get('executor_task_id')` (`completion_executor`; `advance` refuses a caller-supplied `executor_task` unless `merge=True` and `owner_exception` is set). `_d6_snapshot(record, executor_task)` reads the D6 registration and git snapshot, supplying `d6['result_head']`, `d6['root']`, and `d6['branch']`.
- **`pr_number` (`number`):** resolved by `_pr_number(record, pr_number)`:
  1. the caller-supplied `pr_number` if not `None`;
  2. else `record['published_pr']['pr']` if valid;
  3. else `claims[-1].get('pr')` from `_non_ruling_claims(record)`.
  If `number` is still `None` and `d6` is clean with `recorded_result_head == result_head`, `_advance_step` falls back to `_find_published_pr` for `(d6['branch'], d6['result_head'])` (and records it with `store.record_published_pr` when the PR is open and the step is not `PUBLISH` or `DEVELOPER_ATTENTION`).

### Pre-dispatch gates after `step == 'DISPATCH_REVIEW'`

Once `step` becomes `'DISPATCH_REVIEW'` (in any of the three branches below), `_advance_step` (`tools/llmtool/llmtool/task_advance.py`) runs three gates before invoking `dispatch_review`:

1. `_review_ci_gate` (`tools/llmtool/llmtool/task_advance.py`): checks exact-head required Linux CI checks and may change `step` to `'WAITING_CI'`, `'STOPPED'`, or `'CI_FAILED'` (and route a failure via `_route_ci_failure`).
2. `_pr_changed_test_modules` (`tools/llmtool/llmtool/task_advance.py`): when `gate_read.get('checks_readable') is True`, checks added/changed test modules on the PR and may re-evaluate `_pre_review_action` with `extra_modules=pr_test_modules` (potentially changing `step` to e.g. `'RUN_REMOTE_TEST'`).
3. `_merge_security_claim` (`tools/llmtool/llmtool/task_advance.py`): if `step` is still `'DISPATCH_REVIEW'`, checks exact-head security verdicts and may change `step` to `REVIEW_SECURITY_UNKNOWN` or `REVIEW_SECURITY_NOT_PASS`.

### Branch 1 — Normal dispatch

- **How it reaches `DISPATCH_REVIEW`:**
  1. `_advance_step` calls `decide(record, d6, pr, related)` (`tools/llmtool/llmtool/task_advance.py`; re-called with `docs_only=True` if `step in _EC2_DERIVED_STEPS` and `_pr_docs_only` returns `True`).
  2. In `decide`, when `_non_ruling_claims(record)` has no claim matching `pr == chosen_pr` and `head_sha == result_head`, `decide` calls `_pre_review_action(record, d6, pr, related, result_head, docs_only, extra_modules)`.
  3. `_pre_review_action` returns `'DISPATCH_REVIEW'` when the PR is not merged and either `docs_only` is `True` or `_remote_test_status` returns `'PASS'` or `'ADMISSION_DENIED'`.
- **Arguments passed to `invoke` (`dispatch_review_call or review_dispatch.dispatch_review`):**
  - `task_id`: caller's `task_id`.
  - `executor_task`: `record['completion']['executor_task_id']`.
  - `pr_number`: `number` (from `_pr_number` or `_find_published_pr`).
  - `version`: computed in `_advance_step` from `claims = _claims(record)` (all claims, including ruling claims) as `max(versions, default=0) + 1`, where `versions` is `[c.get('version', 0) for c in claims if type(c) is dict and type(c.get('version')) is int]`.
  - No `reviewer` or `refusal_rerun` keyword argument is passed.

### Branch 2 — Refusal rerun

- **How it reaches `DISPATCH_REVIEW`:**
  1. In `decide` (`tools/llmtool/llmtool/task_advance.py`), when matching non-ruling claims exist for `(chosen_pr, result_head)`, `decide` takes `claim = max(matching, key=lambda item: item.get('version', 0))`. If `claim.get('state') == 'UNKNOWN'` and `_failure_reason(claim.get('failure_reason'))` is truthy:
     - if more than one non-ruling claim exists at `result_head`, `decide` returns `'REVIEW_UNUSABLE_STOPPED'`;
     - otherwise `decide` calls `_pre_review_action` and returns `'REVIEW_REDISPATCH'` when `_pre_review_action` returns `'DISPATCH_REVIEW'`.
  2. In `_advance_step`, when `step in ('REVIEW_REDISPATCH', 'REVIEW_UNUSABLE_STOPPED')`, it loads `candidate = _head_review_claim(record, number, d6['result_head'])` (highest-version non-ruling claim for `(number, d6['result_head'])`) and calls `_review_refusal_rerun(record, candidate, d6, pr, store, get)`.
  3. `_review_refusal_rerun` (`tools/llmtool/llmtool/task_advance.py`) verifies `candidate['state'] == 'UNKNOWN'` with a valid `_failure_reason`, a valid non-rerun refusal receipt from `review_dispatch.read_refusal_receipt`, matching `d6['result_head']` and `pr['head']['sha']`, matching `record['task_binding']['contract_digest']`, no live reviewer process (`_reviewer_process_live`) across `_claims(record)` for that PR and head, and `_review_verdict_absent(candidate, github_get)`. On pass, it returns `rerun_version = max(versions, default=0) + 1` over `_claims(record)`.
  4. `_advance_step` sets `step = 'REVIEW_REDISPATCH'`, `rerun_claim = candidate`, `rerun_contract = binding.get('contract_digest')`, and `redispatch_reason = _failure_reason(candidate.get('failure_reason'))`.
  5. In the `if step == 'REVIEW_REDISPATCH':` block, if `rerun_version is not None`, `step` becomes `'DISPATCH_REVIEW'`. (If `rerun_version is None` and `unusable = _head_review_claim(record, number, d6['result_head'])` passes `_review_verdict_absent(unusable, get)`, `step` also becomes `'DISPATCH_REVIEW'` without `rerun_version`.)
  6. Inside `elif step == 'DISPATCH_REVIEW':`, when `rerun_version is not None`, `_advance_step` calls `review_dispatch.mark_refusal_rerun(store=store, task_id=task_id, claim=rerun_claim, contract_digest=rerun_contract, rerun_version=rerun_version)` before calling `invoke` (stopping as `'REVIEW_UNUSABLE_STOPPED'` with `REVIEW_RERUN_RECORD_FAILED` if recording fails).
- **Arguments passed to `invoke` (`dispatch_review_call or review_dispatch.dispatch_review`):**
  - `task_id`: caller's `task_id`.
  - `executor_task`: `record['completion']['executor_task_id']`.
  - `pr_number`: `number` (from `_pr_number` or `_find_published_pr`).
  - `version`: `rerun_version` (from `_review_refusal_rerun`, i.e., `max(versions, default=0) + 1` over `_claims(record)`), or `max(versions, default=0) + 1` over `_claims(record)` if `rerun_version is None`.
  - `refusal_rerun=True` (only when `rerun_version is not None`).

### Branch 3 — Alternate-Reviewer branch

`task_advance.py` has two alternate-Reviewer paths to `dispatch_review`:

1. **Pre-dispatch `REVIEWER_ATTENTION` fallback (`_blocked_fallback_reviewer`):**
   - **How it reaches `DISPATCH_REVIEW`:** When `decide` returns `'REVIEWER_ATTENTION'` (newest matching claim has a `REVIEWER` event with `outcome == 'REVIEW_BLOCKED'`) and `not reviewer_session_done`, `_advance_step` calls `blocked_reviewer = _blocked_fallback_reviewer(record, d6['result_head'])`. `_blocked_fallback_reviewer` checks that `_non_ruling_claims(record)` has exactly one claim at `head_sha == head`, computes `family = task_completion._reviewer_author_family(record.get('developer'))`, selects `alternate = 'claude-reviewer' if family == 'claude' else 'codex-reviewer'`, and validates it with `review_dispatch._select_reviewer(record, alternate)`. If non-`None`, `_advance_step` sets `step = 'DISPATCH_REVIEW'` and appends `('REVIEW_BLOCKED', 'REVIEWER_FALLBACK')` to `codes`.
   - **Arguments passed to `invoke`:** `task_id` (caller's `task_id`), `executor_task` (`record['completion']['executor_task_id']`), `pr_number` (`number`), `version` (`max(versions, default=0) + 1` over `_claims(record)`), and `reviewer=blocked_reviewer`. (Because `blocked_reviewer` is set, P1 `_reviewer_fallback` is skipped if this call raises.)

2. **In-dispatch route-unavailable fallback (`_reviewer_fallback`):**
   - **How it runs:** Inside `elif step == 'DISPATCH_REVIEW':`, when `blocked_reviewer` is `None` and the initial `invoke(...)` raises an exception, `_advance_step` calls `_reviewer_fallback(record, exc)`. If `getattr(exc, 'review_dispatch_code', None)` is in `_REVIEWER_ROUTE_UNAVAILABLE` (`'ROLE_MODEL_NOT_ALLOWED'`, `'REVIEWER_ACCOUNT_UNCONFIGURED'`, `'REVIEWER_NOT_ALLOWED'`), `_reviewer_fallback` computes `alternate = 'claude-reviewer' if family == 'claude' else 'codex-reviewer'` via `task_completion._reviewer_author_family(record.get('developer'))` and validates it with `review_dispatch._select_reviewer(record, alternate)`. If `alternate` is non-`None`, `_advance_step` retries `invoke(...)`.
   - **Arguments passed to `invoke` on retry:** the same `task_id` (caller's `task_id`), `executor_task` (`record['completion']['executor_task_id']`), `pr_number` (`number`), `version` (`rerun_version` if set, else `max(versions, default=0) + 1` over `_claims(record)`), `**rerun_kwargs` (`refusal_rerun=True` if `rerun_version is not None`), plus `reviewer=alternate`.

## D3 — Follow-up dependencies

The following are outside `tools/llmtool/llmtool/task_advance.py` and remain open follow-up dependencies (see [followup-contract-1874.md](followup-contract-1874.md)):

- Which CLI or orchestration caller outside `task_advance.py` selects the `task_id` passed to `advance` (and whether that caller selects a "newest" sibling Task on the D6).
- How `direct_reassignment.authoring_route` behaves when resolving reassigned Developer routes (noted as an open dependency in [followup-contract-1874.md](followup-contract-1874.md)).
- The overall `#1874` fix design (whether caller selection, `task_binding.contract_digest`, `LLMTOOL-REVIEW-BINDING`, or multi-Task contract unioning should change).
