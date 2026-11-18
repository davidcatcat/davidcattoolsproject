# Why `check (docs)` failures are not routed to repair (#2337)

Investigation only. This note records the code facts a fix needs; it changes no
behaviour. Trigger: PR #2336 (AGY Task 787697ae) was a documentation-only PR
whose required `check (docs)` failed. Every `llmtool task advance` call returned
`CI_FAILED` and no test-failure repair went back to the Developer.

## Expected routing

The normal flow requires an exact-head Linux workflow check before review. For a
documentation-only PR that required check is `check (docs)` rather than
`check (Linux 3.14)` ([WORKFLOW.md](../../tools/llmtool/WORKFLOW.md)). A required
CI check that ends `failure` or `timed_out` is a test failure and should return
to the original Developer on the EC2/CI test-failure count.

## Where the gate detects the failure

- [`task_advance.py`](../../tools/llmtool/llmtool/task_advance.py) `_required_linux_checks`
  loads the canonical classifier from
  [`ci_path_classes.py`](../../tools/llmtool/scripts/ci_path_classes.py) and maps
  the four `linux_flags()` results in order to `check (Linux 3.14)`,
  `check (docs)`, the gateway check and the ai-loop check. A docs-only PR
  therefore has exactly one required check: `check (docs)`.
- `_review_ci_gate` reads the exact-head check runs, selects the newest run per
  required name, and writes the newest completed non-success run into
  `latest_out`. For a docs-only PR with a failed docs check it returns
  `('CI_FAILED', 'LINUX_CI_FAILED')` and `latest_out['name'] == 'check (docs)'`.
- `_advance_step` handles the `DISPATCH_REVIEW` step and then inspects
  `latest_out`. Its routing condition requires
  `gate_latest.get('name') == _LINUX_CHECK`, and `_LINUX_CHECK` is hard-coded
  to `check (Linux 3.14)`. A docs failure fails that condition, so
  `_route_ci_failure` is never called.

## Where the repair packet refuses the check

Even if `_route_ci_failure` were reached, it builds a `ci_failure` dictionary
and calls `review_repair.repair_review`, which calls `_ci_failure` in
[`review_repair.py`](../../tools/llmtool/llmtool/review_repair.py).
`_ci_failure` rejects any check name not in `CI_REPAIR_CHECKS`, currently
`('check (Linux 3.14)', 'check (macOS 3.13)')`, with `CI_FAILURE_INVALID`.
There are therefore two independent allowlists that exclude `check (docs)`:
the routing condition in `task_advance.py` and the packet validation in
`review_repair.py`.

## Why every call returns `CI_FAILED`

The failure path records nothing when the name does not match: no repair claim,
no repair Task, no head movement, and no Task state change. The next
`task advance` reads the same exact head, reaches `DISPATCH_REVIEW` again,
`_review_ci_gate` again returns `CI_FAILED`, and the same branch is skipped.
The result is a deterministic loop of `step: CI_FAILED` with code
`LINUX_CI_FAILED` until a human acts. This matches the PR #2336 observation.

## Counts that apply

A routed CI check is treated as a test failure, not a review failure:

- `repair_review` stores CI claims in the `<base>-ec2` family
  (`pr-<number>-ec2`), the same family as EC2 remote-test failures, so CI and
  EC2 failures share one test-failure count.
- `_route_ci_failure` consults `_test_family_used_up`, which uses
  `lineage_counts.lineage_counts(...)['test_fails']` and
  `repair_policy.test_failure_action`; the cap is the policy key
  `test_fail_max`. At the cap the step becomes `STOPPED` with
  `TEST_FAIL_LIMIT_REACHED`.
- The count is separate from the review-repair count (`repair_max`) and is
  chain-wide: `lineage_counts` includes the whole continuation chain, while a
  chain link or store scan in doubt gives `repair_policy.UNKNOWN`, never 0.
- The repair Task's bounded acceptance item uses
  `attempt/MAX_REPAIR_ATTEMPTS` from `review_repair.py`; it does not use the
  review cap.
- `auto=True` from `task advance` has no effect on a test-failure repair; the
  "one review repair per chain" rule applies only to review repairs.

## The merge path has the same gap

After a review PASS, `_merge_once` refuses with `MERGE_CHECKS_NOT_GREEN` when a
required check is not green, and `_advance_step` only attempts `_route_ci_failure`
for that refusal by reading `_MAC_CHECK` (`check (macOS 3.13)`). A docs-only PR
never requires the Mac check, so a later docs failure at merge time is returned
as a plain `MERGE_REFUSED` with `MERGE_CHECKS_NOT_GREEN` and is not routed
either. A complete fix should cover both the pre-review gate and this
post-PASS merge gate.

## Candidate fixes

1. **Extend both allowlists, narrowly.** Add `check (docs)` to
   `CI_REPAIR_CHECKS` and change the `_advance_step` routing condition from
   equality with `_LINUX_CHECK` to membership in an explicit
   `CI_REPAIR_CHECKS`-style set (sharing one definition so the two modules
   cannot drift). Risks: the docs check may not publish useful `output.title` /
   `output.summary`, so the repair excerpt can be empty and the Developer gets
   little context; and the change admits one more check name but still leaves
   gateway and ai-loop check failures unrouted.
2. **Route every required Linux-workflow check.** Replace the hard-coded name
   test with "this run's name is in the required set computed by
   `_required_linux_checks`", and validate the name in `_ci_failure` by shape
   (for example a `check (...)` name) instead of a fixed tuple. Risks: this
   changes policy for code PRs too, because gateway and ai-loop check failures
   would start spending the test-failure count and dispatching repairs; a
   `reason`/`check_name` value read from an untrusted check run must still be
   bounded; and the security review needs to accept a wider accepted-name
   surface.
3. **Keep the failure visible instead of repairing it.** Convert a docs-check
   failure to a distinct non-repair step (for example a "docs check failed"
   terminal/attention state) so the loop stops and a human is asked. Risks:
   this does not satisfy the goal of sending the failure to the original
   Developer, it adds a new step name to consumers and tests, and repeated
   calls could still look like a stall unless the state is recorded.

## What a fix needs to verify

- A docs-only PR whose `check (docs)` ends `failure` or `timed_out` produces a
  CI repair claim in the `pr-<number>-ec2` family and the packet's
  `check_name` is `check (docs)`.
- Replaying the same failed check run reuses the claim instead of spending a
  second test-failure attempt (`_ci_failure` binds the identity to Task, PR,
  head, check name and run id).
- A used-up `test_fail_max` returns `STOPPED` with `TEST_FAIL_LIMIT_REACHED`,
  not another repair.
- Environment conclusions (`cancelled`, `startup_failure`) still wait
  uncounted.
- The existing Linux and Mac routing in
  [`test_task_advance_ci_timed_out.py`](../../tools/llmtool/tests/test_task_advance_ci_timed_out.py)
  keeps its current behaviour. The `NOT_EXECUTED` handling in
  [`test_task_advance_not_executed.py`](../../tools/llmtool/tests/test_task_advance_not_executed.py)
  is unrelated to this path and should not change.

## Scope

No code or test changes are made by this note. The design leaves the choice
between candidates 1 and 2 to the Owner, because candidate 2 widens the
test-failure policy beyond the docs case.
