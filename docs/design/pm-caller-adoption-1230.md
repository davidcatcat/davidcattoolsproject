# Design Draft: Closing the `pm_caller` Adoption Race (#1230 A1)

## 1. Gap and Evidence

`tools/llmtool/llmtool/pm_caller.py` verifies caller ownership in two stages: `resolve_caller_session` determines `caller_key`, `fence_refusal_code` checks for a transfer fence via `pm_runtime.read_session_fence`, and `owner_refusal_code` reads `pm_session_id` via `store.get(task_id)`. Neither `check_caller` nor `guarded` (`invoke`) acquires the adoption lock (`pm_runtime.transfer_lock`) or binds `pm_session_id` into downstream writes.

Meanwhile, `adopt_pm_session` and `_adopt_under_lock` in `tools/llmtool/llmtool/active_pm_inbox.py` acquire `pm_runtime.transfer_lock(old_native_id, root)`, verify the old session is `STOPPED` via `pm_session_liveness.check_session`, write the fence (`pm_runtime.write_session_fence`), and move each open Task via `TaskStore.adopt_pm_session` in `tools/llmtool/llmtool/task_store.py` (which updates `pm_session_id` under `.{task_id}.lock` in `TaskStore._update_record`).

Because `pm_caller` checks run unlocked before the command's work, a concurrent `adopt_pm_session` can fence the caller's session and reassign `pm_session_id` between `check_caller` (or `guarded`'s `invoke`) and the command's store mutations or external side effects. As noted in `pm_caller.py`, `task drive` only catches an adoption on the *next* `guarded` call, and single-shot commands never re-check.

## 2. Affected Callers

- **`tools/llmtool/llmtool/task_submission_cli.py`**:
  - `run_task_advance` (`check_caller` before `task_advance.advance`)
  - `run_task_drive` and `_run_task_drive_leased` (`check_caller` before `store.advance_lease`; `pm_caller.guarded` around `advance_call` and `label_mac_ci_call`)
  - `run_task_remote_test_checked` (`check_caller` before `run_task_remote_test`)
  - `run_task_review_dispatch` (`check_caller` before `review_dispatch.dispatch_review`)
  - `run_task_repair` (`check_caller` before `recover_unknown_claim` or `review_repair.repair_review`)
  - `run_task_delegate_checked` (`check_caller` before `_auto_direct_replacement` and `run_task_delegate`)
  - `run_task_claude_bind_checked` (`check_caller` before `run_task_claude_bind`)
  - `run_task_retry_start` (`check_caller` before `start_failure.record_checks` and `run_task_claude_delegate`)
  - `run_task_dispute` (`check_caller` before `dispute_withdraw`, `dispute_arbitrate`, `dispute_open`, `dispute_defer`)
- **`tools/llmtool/llmtool/merge_train_cli.py` and `tools/llmtool/llmtool/merge_train_build.py`**:
  - `run_task_train` calls `resolve_caller_session` and `owner_refusal_code` for `build`, `merge`, and `close` (and `_OwnedAdvance.__call__` re-checks `owner_refusal_code` before each `advance` in `close`).
  - `build_train` calls `owner_refusal_code` for each member Task before `merge_train_eligibility.member_check` and `merge_train_store.create`.
- **`tools/llmtool/llmtool/main_incident_cli.py`**:
  - `_caller` (`resolve_caller_session`, `fence_refusal_code`), `_require_owner` (`owner_refusal_code`), and `_confirm` (`check_caller`). Incident records live in `main_incident_store` (`_OwnerView`), not `TaskStore`, so `adopt_pm_session` does not move incident `pm_session_id`, though `fence_refusal_code` still races with `write_session_fence`.

## 3. Option Comparison

| Dimension | Option A: Run check under adoption lock (`transfer_lock`) | Option B: Compare-and-swap (CAS) on `pm_session_id` |
| --- | --- | --- |
| **Mechanism** | Acquire `pm_runtime.transfer_lock(native_id, root)` in `pm_caller.check_caller` / `guarded` (either check-only, or held across the whole command). | Keep `check_caller` as unlocked preflight; pass `pm_session_id=caller_key` in `expected` to `TaskStore` CAS writes (`compare_and_update`, `d6_bind_compare_and_update`, `claude_bind_compare_and_update`, `_update_record`), matching `TaskStore.abandon`. |
| **Effects** | **Check-only**: closes only the gap between `fence_refusal_code` and `owner_refusal_code`; adoption can still happen right after unlock. **Command-scoped**: serializes all PM commands for a session against `adopt_pm_session` and against each other. | Atomically rejects any Task mutation if `adopt_pm_session` changed `pm_session_id` under `.{task_id}.lock`. Write-ahead claims (`d6_bind_compare_and_update`, `prepare_direct_dispatch`, review/repair/merge claims) fail before external side effects run. Concurrent commands on different Tasks do not block each other. |
| **Risks** | **Deadlock / lock-order risk**: holding `transfer_lock` across commands places a session-wide lock above `.{task_id}.lock`, `advance_lease`, `direct_dispatch_lease`, and `chain_lock` while `adopt_pm_session` takes `transfer_lock` then `.{task_id}.lock`. **Contention**: `transfer_lock` contention maps to `PM_SESSION_ADOPTION_OLD_PM_ACTIVE`, so long commands (`task drive`, `task delegate`) block adoption and sibling commands. Couples `pm_caller` to `pm_runtime` state dirs even for `_MutableStore` / `_OwnerView` callers. | **Pre-write side effects**: an operation that acts externally before its first `TaskStore` write (e.g. `remote_test` without a prior claim) only fails when it persists its result. **Plumbing surface**: requires threading `caller_key` into `TaskStore` writes across step modules and mapping `TaskStoreConflict` to `PM_CALLER_NOT_TASK_OWNER`. |
| **Affected files** | `pm_caller.py`, `task_submission_cli.py`, `merge_train_cli.py`, `merge_train_build.py`, `main_incident_cli.py`. | `task_store.py` (`_update_record`, CAS helpers), `pm_caller.py`, `task_submission_cli.py` (checked adapters and step calls), `merge_train_cli.py`, `merge_train_build.py`. |
| **Tests needed** | Contention tests between `adopt_pm_session` and `check_caller` / `guarded`; lock-ordering tests with `advance_lease`, `chain_lock`, `direct_dispatch_lease`, and `_update_record`; `_MutableStore` compatibility in `tools/llmtool/tests/test_pm_caller.py`. | `TaskStore` CAS tests where `adopt_pm_session` commits between `check_caller` and `compare_and_update` / `d6_bind_compare_and_update` / `claude_bind_compare_and_update`; adapter tests in `test_pm_caller.py` for `advance`, `drive`, `delegate`, `claude-bind`, `review-dispatch`, `repair`, `dispute`; `merge_train_cli` `close` / `build_train` adoption-race tests. |

## 4. Recommendation

**Adopt Option B (CAS on `pm_session_id` at `TaskStore` writes)** and keep `pm_caller.check_caller` / `pm_caller.guarded` as the fast preflight.

Option A either leaves the TOCTOU window open (if the lock is released after the check) or introduces session-wide serialization and lock-ordering risk across `.{task_id}.lock`, `advance_lease`, `direct_dispatch_lease`, and `chain_lock` (if held across the command). Option B reuses the existing per-Task `.{task_id}.lock` in `TaskStore._update_record` (the same pattern `TaskStore.abandon` already uses), adds zero new locks, and prevents any post-adoption Task mutation—including write-ahead claims before provider, Git, or GitHub side effects.
