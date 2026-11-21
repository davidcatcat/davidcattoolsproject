# Why `check (docs)` failures are not routed to repair (#2337)

Investigation only. This note records the code facts a fix needs; it changes no
behaviour. Trigger: PR #2336 (AGY Task 787697ae) was a documentation-only PR
whose required `check (docs)` failed. Every `llmtool task advance` call returned
`CI_FAILED` and no test-failure repair went back to the Developer.

## Expected routing

The normal flow requires an exact-head Linux workflow check before review
(`tools/llmtool/WORKFLOW.md:23-24`). For a documentation-only PR that required check
is `check (docs)` rather than `check (Linux 3.14)` ([WORKFLOW.md](../../tools/llmtool/WORKFLOW.md),
citing `tools/llmtool/WORKFLOW.md:23-24`). A required CI check that ends `failure` or
`timed_out` is a test failure and should return to the original Developer on the
EC2/CI test-failure count (`tools/llmtool/WORKFLOW.md:28`).

## Where the gate detects the failure

- [`task_advance.py`](../../tools/llmtool/llmtool/task_advance.py) `_required_linux_checks`
  (`tools/llmtool/llmtool/task_advance.py:549-569`) loads the canonical classifier from
  [`ci_path_classes.py`](../../tools/llmtool/scripts/ci_path_classes.py)
  (`tools/llmtool/llmtool/task_advance.py:536-546`, citing `scripts/ci_path_classes.py`
  UNVERIFIED) and maps the four `linux_flags()` results in order to `_LINUX_FLAG_CHECKS`
  (`tools/llmtool/llmtool/task_advance.py:532`): `check (Linux 3.14)`,
  `check (docs)`, gateway check and ai-loop check. A docs-only PR therefore has
  exactly one required check: `check (docs)`.
- `_review_ci_gate` (`tools/llmtool/llmtool/task_advance.py:704-770`) reads exact-head
  check runs, selects the newest run per required name (`tools/llmtool/llmtool/task_advance.py:756`),
  and writes the newest completed non-success run into `latest_out`
  (`tools/llmtool/llmtool/task_advance.py:765-766`). For a docs-only PR with a failed
  docs check it returns `('CI_FAILED', 'LINUX_CI_FAILED')`
  (`tools/llmtool/llmtool/task_advance.py:767`) and `latest_out['name'] == 'check (docs)'`.
- `_advance_step` (`tools/llmtool/llmtool/task_advance.py:3154-3744`) handles the
  `DISPATCH_REVIEW` step (`tools/llmtool/llmtool/task_advance.py:3375-3401`) and then
  inspects `latest_out` (`gate_latest`). Its routing condition at
  `tools/llmtool/llmtool/task_advance.py:3383` requires `gate_latest.get('name') == _LINUX_CHECK`,
  and `_LINUX_CHECK` is hard-coded to `check (Linux 3.14)`
  (`tools/llmtool/llmtool/task_advance.py:774`). A docs failure fails that condition, so
  `_route_ci_failure` (`tools/llmtool/llmtool/task_advance.py:1020-1088`, called at
  `tools/llmtool/llmtool/task_advance.py:3391`) is never called.

## Further restriction: check-run lookup hard-codes `_LINUX_CHECK`

Even if the gate routing condition at `tools/llmtool/llmtool/task_advance.py:3383` were
relaxed to admit `check (docs)`, the step still would not reach a repair. Immediately
inside that branch, `task advance` retrieves the full check-run details via
`_read_check_run(d6['result_head'], _LINUX_CHECK, gate_latest.get('id'), get)` at
`tools/llmtool/llmtool/task_advance.py:3386`, hard-coding `_LINUX_CHECK`
(`tools/llmtool/llmtool/task_advance.py:774`).

Inside `_read_check_run` (`tools/llmtool/llmtool/task_advance.py:795-807`), line
`tools/llmtool/llmtool/task_advance.py:804` validates `run.get('name') != name`.
For a `check (docs)` failure, GitHub returns the check run whose name is `'check (docs)'`.
Because `'check (docs)' != 'check (Linux 3.14)'`, the equality check fails and
`_read_check_run` returns `None` at `tools/llmtool/llmtool/task_advance.py:806`.

At `tools/llmtool/llmtool/task_advance.py:3388-3389`, `if linux_run is None: codes.append('CI_CHECK_RUN_UNAVAILABLE')`,
and `_route_ci_failure` (`tools/llmtool/llmtool/task_advance.py:3391`) is skipped.
Therefore, any candidate fix that only relaxes the routing condition at line 3383
without also generalizing `_read_check_run` at line 3386 to pass `gate_latest.get('name')`
will fail with `CI_CHECK_RUN_UNAVAILABLE` and still never reach `_route_ci_failure`.

## Where the repair packet refuses the check

Even if `_route_ci_failure` (`tools/llmtool/llmtool/task_advance.py:1020-1088`) were reached,
it builds a `ci_failure` dictionary (`tools/llmtool/llmtool/task_advance.py:1066-1067`)
and calls `review_repair.repair_review` (`tools/llmtool/llmtool/review_repair.py:645-958`
called at `tools/llmtool/llmtool/task_advance.py:1072-1075`), which calls `_ci_failure` in
[`review_repair.py`](../../tools/llmtool/llmtool/review_repair.py)
(`tools/llmtool/llmtool/review_repair.py:693`).
`_ci_failure` (`tools/llmtool/llmtool/review_repair.py:440-487`) rejects any check name not
in `CI_REPAIR_CHECKS` at `tools/llmtool/llmtool/review_repair.py:451` with `CI_FAILURE_INVALID`
(`tools/llmtool/llmtool/review_repair.py:456`).
`CI_REPAIR_CHECKS` is currently `('check (Linux 3.14)', 'check (macOS 3.13)')`
(`tools/llmtool/llmtool/review_repair.py:433`).

Furthermore, packet validation cannot accept check names by a `check (...)` shape.
The required Linux-workflow checks defined in `task_advance.py` include
`_GATEWAY_CHECK = 'gateway tests (Linux 3.14)'`
(`tools/llmtool/llmtool/task_advance.py:530`) and `_AI_LOOP_CHECK = 'ai-loop helper tests (Python 3.14)'`
(`tools/llmtool/llmtool/task_advance.py:529`, in `_LINUX_FLAG_CHECKS` at
`tools/llmtool/llmtool/task_advance.py:532`). Neither name has a `check (...)` shape.
If packet validation accepted names by a `check (...)` shape, failures of `_GATEWAY_CHECK`
or `_AI_LOOP_CHECK` would still be refused by the packet validator at
`tools/llmtool/llmtool/review_repair.py:450-456` with `CI_FAILURE_INVALID`
(`tools/llmtool/llmtool/review_repair.py:456`). Packet validation must therefore validate
against exact required-check names rather than a shape filter.

There are therefore three independent restrictions in the pre-review CI path that exclude
`check (docs)`:
1. The routing condition in `task_advance.py` (`tools/llmtool/llmtool/task_advance.py:3383`).
2. The check-run retrieval lookup in `task_advance.py` (`tools/llmtool/llmtool/task_advance.py:3386`
   and `tools/llmtool/llmtool/task_advance.py:804`).
3. The packet validation allowlist in `review_repair.py` (`tools/llmtool/llmtool/review_repair.py:451`
   against `tools/llmtool/llmtool/review_repair.py:433`).

## Why every call returns `CI_FAILED`

The failure path records nothing when the name does not match or when `_read_check_run`
returns `None` (`tools/llmtool/llmtool/task_advance.py:3388-3389`): no repair claim,
no repair Task, no head movement, and no Task state change.
On the next `task advance` call, `decide` (`tools/llmtool/llmtool/task_advance.py:2418-2520`)
finds no matching review claims for the head (`tools/llmtool/llmtool/task_advance.py:2457-2459`),
calls `_pre_review_action` (`tools/llmtool/llmtool/task_advance.py:2397-2415`), which returns
`DISPATCH_REVIEW` (`tools/llmtool/llmtool/task_advance.py:2415`).
In `_advance_step` (`tools/llmtool/llmtool/task_advance.py:3375-3401`), `_review_ci_gate`
(`tools/llmtool/llmtool/task_advance.py:704-770`) reads the same exact head, again returns
`CI_FAILED` with `LINUX_CI_FAILED` (`tools/llmtool/llmtool/task_advance.py:767`), and the
repair routing branch is skipped.
The result is a deterministic loop of `step: CI_FAILED` with code `LINUX_CI_FAILED` until a
human acts. This matches the PR #2336 observation.

## Counts that apply

A routed CI check is treated as a test failure, not a review failure:

- `repair_review` (`tools/llmtool/llmtool/review_repair.py:645-958`) stores CI claims in the
  `<base>-ec2` family (`pr-<number>-ec2`) (`tools/llmtool/llmtool/review_repair.py:494-496`,
  `tools/llmtool/llmtool/review_repair.py:720-723`), the same family as EC2 remote-test
  failures, so CI and EC2 failures share one test-failure count.
- `_route_ci_failure` (`tools/llmtool/llmtool/task_advance.py:1020-1088`) consults
  `_test_family_used_up` (`tools/llmtool/llmtool/task_advance.py:851-864`, called at
  `tools/llmtool/llmtool/task_advance.py:1059`), which uses `_test_fail_count`
  (`tools/llmtool/llmtool/task_advance.py:830-840`) backed by
  `lineage_counts.lineage_counts(...)['test_fails']` (`tools/llmtool/llmtool/task_advance.py:839-840`);
  the cap is policy key `test_fail_max` (`tools/llmtool/llmtool/review_repair.py:848-851`).
  At the cap the step becomes `STOPPED` with `TEST_FAIL_LIMIT_REACHED`
  (`tools/llmtool/llmtool/task_advance.py:1060`, `tools/llmtool/llmtool/task_advance.py:1083-1084`,
  `tools/llmtool/llmtool/review_repair.py:851`).
- The count is separate from the review-repair count (`repair_max`) and is chain-wide:
  `lineage_counts` includes the whole continuation chain (`tools/llmtool/llmtool/task_advance.py:840`,
  `tools/llmtool/llmtool/review_repair.py:836-837`), while a chain link or store scan in doubt
  gives `repair_policy.UNKNOWN`, never 0 (`tools/llmtool/llmtool/task_advance.py:837`).
- The repair Task's bounded acceptance item uses `attempt/MAX_REPAIR_ATTEMPTS` from
  `review_repair.py` (`tools/llmtool/llmtool/review_repair.py:16`,
  `tools/llmtool/llmtool/review_repair.py:253-256`, `tools/llmtool/llmtool/review_repair.py:271`);
  it does not use the review cap.
- `auto=True` from `task advance` (`tools/llmtool/llmtool/task_advance.py:1075`) has no effect on
  a test-failure repair (`tools/llmtool/llmtool/review_repair.py:661-662`,
  `tools/llmtool/llmtool/review_repair.py:794`, `tools/llmtool/llmtool/review_repair.py:824`);
  the "one review repair per chain" rule applies only to review repairs.

## The merge path has the same gap

After a review PASS, `_merge_once` (`tools/llmtool/llmtool/task_advance.py:1485-1727`) calls
`_merge_checks` (`tools/llmtool/llmtool/task_advance.py:572-666` at
`tools/llmtool/llmtool/task_advance.py:1546`), which refuses with `MERGE_CHECKS_NOT_GREEN`
(`tools/llmtool/llmtool/task_advance.py:607`, `tools/llmtool/llmtool/task_advance.py:1551`)
when a required check is not green.
`_advance_step` only attempts `_route_ci_failure` for that refusal
(`tools/llmtool/llmtool/task_advance.py:3308-3320`) by reading `_MAC_CHECK` (`check (macOS 3.13)`)
via `mac_run = _newest_check_run(d6['result_head'], _MAC_CHECK, get)` at
`tools/llmtool/llmtool/task_advance.py:3312`.
`_newest_check_run` (`tools/llmtool/llmtool/task_advance.py:810-828`) filters strictly by
`item.get('name') == name` (`tools/llmtool/llmtool/task_advance.py:820`).
A docs-only PR never requires the Mac check (`tools/llmtool/llmtool/task_advance.py:622`), so
`mac_run` is `None` (`tools/llmtool/llmtool/task_advance.py:823`), and a later docs failure at
merge time is returned as a plain `MERGE_REFUSED` with `MERGE_CHECKS_NOT_GREEN`
(`tools/llmtool/llmtool/task_advance.py:3319-3321`) and is not routed either.
The post-PASS `RUN_TASK_COMPLETE` path has the same `_MAC_CHECK` restriction
(`tools/llmtool/llmtool/task_advance.py:3604-3613` at `tools/llmtool/llmtool/task_advance.py:3606`).
A complete fix should cover both the pre-review gate and this post-PASS merge gate.

## Candidate fixes

1. **Extend both allowlists and generalize check-run lookup.** Add `check (docs)` to
   `CI_REPAIR_CHECKS` (`tools/llmtool/llmtool/review_repair.py:433`) and change the
   `_advance_step` routing condition at `tools/llmtool/llmtool/task_advance.py:3383` from
   equality with `_LINUX_CHECK` to membership in an explicit `CI_REPAIR_CHECKS`-style set
   (sharing one definition so the two modules cannot drift).
   To account for the restriction at `tools/llmtool/llmtool/task_advance.py:3386`, change
   `_read_check_run(d6['result_head'], _LINUX_CHECK, gate_latest.get('id'), get)` to pass
   `gate_latest.get('name')` instead of hard-coded `_LINUX_CHECK`, so `_read_check_run`
   matches `run.get('name') == name` at `tools/llmtool/llmtool/task_advance.py:804`.
   To account for the packet validator restriction at `tools/llmtool/llmtool/review_repair.py:450-456`,
   this candidate avoids shape matching entirely and validates strictly against exact check names,
   because required checks like `_GATEWAY_CHECK` (`tools/llmtool/llmtool/task_advance.py:530`)
   and `_AI_LOOP_CHECK` (`tools/llmtool/llmtool/task_advance.py:529`) do not share a `check (...)` shape.
   In addition, update the merge path at `tools/llmtool/llmtool/task_advance.py:3312` and
   `tools/llmtool/llmtool/task_advance.py:3606` to inspect failed required checks instead of
   only `_MAC_CHECK`.
   Risks: the docs check may not publish useful `output.title` / `output.summary`
   (`tools/llmtool/llmtool/task_advance.py:781-793`), so the repair excerpt can be empty
   and the Developer gets little context; and the change admits one more check name but
   still leaves gateway and ai-loop check failures unrouted.
2. **Route every required Linux-workflow check.** Replace the hard-coded name test at
   `tools/llmtool/llmtool/task_advance.py:3383` with "this run's name is in the required
   set computed by `_required_linux_checks`" (`tools/llmtool/llmtool/task_advance.py:549-569`).
   To account for the restriction at `tools/llmtool/llmtool/task_advance.py:3386`, pass
   the matched run's actual name `gate_latest.get('name')` to `_read_check_run` instead of
   `_LINUX_CHECK`, avoiding the mismatch refusal at `tools/llmtool/llmtool/task_advance.py:804`.
   To account for the packet validator restriction at `tools/llmtool/llmtool/review_repair.py:450-456`,
   remove any shape option (such as `check (...)`), because `_GATEWAY_CHECK`
   (`tools/llmtool/llmtool/task_advance.py:530`) and `_AI_LOOP_CHECK`
   (`tools/llmtool/llmtool/task_advance.py:529`) do not have that shape and would be refused with
   `CI_FAILURE_INVALID` (`tools/llmtool/llmtool/review_repair.py:456`); instead, specify validation
   in `_ci_failure` (`tools/llmtool/llmtool/review_repair.py:451`) against the exact required-check
   names (for example against an exported set of required workflow checks such as
   `_LINUX_FLAG_CHECKS` at `tools/llmtool/llmtool/task_advance.py:532` or a shared definition
   instead of a fixed two-element tuple).
   Generalize the merge-gate lookup at `tools/llmtool/llmtool/task_advance.py:3312` to query
   the newest run of whichever required check failed.
   Risks: this changes policy for code PRs too, because gateway and ai-loop check failures
   would start spending the test-failure count (`tools/llmtool/llmtool/review_repair.py:848-851`)
   and dispatching repairs; a `reason`/`check_name` value read from an untrusted check run
   must still be bounded (`tools/llmtool/llmtool/review_repair.py:466-473`); and the security
   review needs to accept a wider accepted-name surface.
3. **Keep the failure visible instead of repairing it.** Convert a docs-check failure at
   `tools/llmtool/llmtool/task_advance.py:3383` to a distinct non-repair step (for example a
   "docs check failed" terminal/attention state) so the loop stops and a human is asked.
   To account for the restriction at `tools/llmtool/llmtool/task_advance.py:3386`, this approach
   intentionally stops before calling `_read_check_run` or `_route_ci_failure`
   (`tools/llmtool/llmtool/task_advance.py:3391`); if check metadata is logged, it reads
   `gate_latest.get('name')` rather than assuming `_LINUX_CHECK` to prevent lookup failure at
   `tools/llmtool/llmtool/task_advance.py:804`.
   To account for the packet validator restriction at `tools/llmtool/llmtool/review_repair.py:450-456`,
   this approach does not route any check to `review_repair.repair_review` or `_ci_failure`, so
   non-matching check names such as `_GATEWAY_CHECK` (`tools/llmtool/llmtool/task_advance.py:530`) or
   `_AI_LOOP_CHECK` (`tools/llmtool/llmtool/task_advance.py:529`) never reach packet validation.
   Risks: this does not satisfy the goal of sending the failure to the original Developer,
   it adds a new step name to consumers and tests, and repeated calls could still look
   like a stall unless the state is recorded.

## What a fix needs to verify

- A docs-only PR whose `check (docs)` ends `failure` or `timed_out` produces a
  CI repair claim in the `pr-<number>-ec2` family (`tools/llmtool/llmtool/review_repair.py:720-723`)
  and the packet's `check_name` is `check (docs)` (`tools/llmtool/llmtool/review_repair.py:465-473`).
- The check-run lookup at `tools/llmtool/llmtool/task_advance.py:3386` successfully retrieves
  the `check (docs)` run via `_read_check_run` without triggering `CI_CHECK_RUN_UNAVAILABLE`
  (`tools/llmtool/llmtool/task_advance.py:3388-3389`).
- If Candidate 2 is chosen, packet validation in `_ci_failure` (`tools/llmtool/llmtool/review_repair.py:450-456`)
  accepts exact names for all required Linux checks, including `_GATEWAY_CHECK`
  (`tools/llmtool/llmtool/task_advance.py:530`) and `_AI_LOOP_CHECK`
  (`tools/llmtool/llmtool/task_advance.py:529`), without refusing `CI_FAILURE_INVALID`
  (`tools/llmtool/llmtool/review_repair.py:456`).
- Replaying the same failed check run reuses the claim instead of spending a second
  test-failure attempt (`_ci_failure` binds the identity to Task, PR, head, check name
  and run id at `tools/llmtool/llmtool/review_repair.py:481-486`, replayed at
  `tools/llmtool/llmtool/review_repair.py:768-777`).
- A used-up `test_fail_max` returns `STOPPED` with `TEST_FAIL_LIMIT_REACHED`
  (`tools/llmtool/llmtool/task_advance.py:1060`, `tools/llmtool/llmtool/review_repair.py:851`),
  not another repair.
- Environment conclusions (`cancelled`, `startup_failure`) still wait uncounted
  (`tools/llmtool/llmtool/task_advance.py:778`, `tools/llmtool/llmtool/task_advance.py:1031-1033`).
- The existing Linux and Mac routing in
  [`test_task_advance_ci_timed_out.py`](../../tools/llmtool/tests/test_task_advance_ci_timed_out.py)
  (UNVERIFIED lines) keeps its current behaviour. The `NOT_EXECUTED` handling in
  [`test_task_advance_not_executed.py`](../../tools/llmtool/tests/test_task_advance_not_executed.py)
  (UNVERIFIED lines) is unrelated to this path and should not change.

## Scope

No code or test changes are made by this note. The design leaves the choice
between candidates 1 and 2 to the Owner, because candidate 2 widens the
test-failure policy beyond the docs case.
