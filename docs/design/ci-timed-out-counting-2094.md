# #2094 design: how a top-level CI `timed_out` should count

Proposal only; not a review or acceptance. Source: `main` at `9416bdd02439e09d94d3c5d78f5ce407c7593edd`. Claims about today cite `path:line` at that SHA; anything not read is marked UNVERIFIED. The change is not implemented yet (#2094) and needs a PM/Owner routing call under the CI-process rules (#1485).

## 1. Today (current)

- `tools/llmtool/llmtool/task_advance.py:778`: `_CI_ENV_CONCLUSIONS = ('cancelled', 'timed_out', 'startup_failure')`.
- `_route_ci_failure` (`task_advance.py:1020`) returns `WAITING_ENV` with `CI_ENV_TIMED_OUT` when a required check's own conclusion is `timed_out` (`:1031-1033`). No repair is dispatched and the test-failure count (`test_fail_max`, `repair_policy.py:139-142`) is not spent.
- The Linux gate path routes the same conclusions before review (`task_advance.py:3316`).
- A `timed_out` check never passes, so merge still needs a later exact-head SUCCESS. Nothing re-runs CI on its own.

## 2. Problem

The test jobs run PR-controlled code. Tests that hang until the job time limit end as `timed_out`, so a PR can wait indefinitely without spending its bounded CI repair count. #2082 v92 found the same pattern for the Mac capacity exemption and already vetoes `timed_out` there. `cancelled` and `startup_failure` start before or outside the tests and are still environment signals.
