# #2094 design: how a top-level CI `timed_out` should count

Proposal only; not a review or acceptance. Source: `main` at `9416bdd02439e09d94d3c5d78f5ce407c7593edd`. Claims about today cite `path:line` at that SHA; anything not read is marked UNVERIFIED. The change is not implemented yet (#2094) and needs a PM/Owner routing call under the CI-process rules (#1485).

## 1. Today (current)

- `tools/llmtool/llmtool/task_advance.py:778`: `_CI_ENV_CONCLUSIONS = ('cancelled', 'timed_out', 'startup_failure')`.
- `_route_ci_failure` (`task_advance.py:1020`) returns `WAITING_ENV` with `CI_ENV_TIMED_OUT` when a required check's own conclusion is `timed_out` (`:1031-1033`). No repair is dispatched and the test-failure count (`test_fail_max`, `repair_policy.py:139-142`) is not spent.
- The Linux gate path routes the same conclusions before review (`task_advance.py:3316`).
- A `timed_out` check never passes, so merge still needs a later exact-head SUCCESS. Nothing re-runs CI on its own.

## 2. Problem

The test jobs run PR-controlled code. Tests that hang until the job time limit end as `timed_out`, so a PR can wait indefinitely without spending its bounded CI repair count. #2082 v92 found the same pattern for the Mac capacity exemption and already vetoes `timed_out` there. `cancelled` and `startup_failure` start before or outside the tests and are still environment signals.

## 3. Options

- **A. Count it as a test failure.** `timed_out` leaves `_CI_ENV_CONCLUSIONS` and is routed like `failure`: a repair goes back to the original Developer on the shared EC2/CI count. Simple and bounded. A real runner stall then costs one count.
- **B. Bounded wait, then count.** The first `timed_out` on a head waits as today. A second `timed_out` on the same head counts as a test failure. Needs a per-head record of timed-out runs; no automatic re-run is added.
- **C. Keep today.** Uncounted wait; rely on the merge gate and the WAITING notice. UNVERIFIED: whether `WAITING_ENV` from advance sets a `waiting` record with reason `ENV`, so that `repair_policy.py:211-224` notifies after `waiting_notify_after_hours`.

## 4. Recommendation

Option A. It closes the uncounted path with the smallest change, matches the #2082 Mac veto, and a stall that costs one count is visible and bounded by `test_fail_max`. Option B is the fallback if runner stalls prove common.

## 5. Acceptance for the implementation (rule file `task_advance.py`)

- `timed_out` on Linux or Mac routes like `failure`; `cancelled` and `startup_failure` keep `WAITING_ENV`.
- Tests: a `timed_out` check dispatches one counted repair; the count stops at `test_fail_max`; `cancelled` and `startup_failure` still wait uncounted; the Mac capacity exemption still refuses `timed_out`.
- No new CI trigger or re-run (#1485). Codex Security Review, formal cross-family review, EC2 and exact-head CI.
