# AGY Developer stability audit (#2367)

Read-only findings note for the Owner's AGY decision. It changes no code and no
test; this note is the only file written.

## Scope and evidence

- Owner AGY stability directive relayed on
  [#2367](https://github.com/davidcatcat/davidcattoolsproject/issues/2367#issuecomment-7003217396)
  and PM audit step 1 on
  [#2367](https://github.com/davidcatcat/davidcattoolsproject/issues/2367#issuecomment-7003237009).
- PM-quoted TaskStore fields for 12 non-successful AGY Developer runs on
  `gemini-3.8-flash-medium`, as task id / error code / changed files /
  continues. PM audit step 1 also quoted 96 of 108 AGY Tasks since 2026-11-17
  ending `DEVELOPER_RESPONDED`.
- Code read for this note: `tools/llmtool/llmtool/repair_policy.py` and
  `tools/llmtool/llmtool/review_repair.py`.

The code that emits or first handles the `AGY_*` codes (the AGY runner and
worker dispatch), and the modules `worker_availability`, `lineage_counts`,
`run_budget` and the TaskStore, are outside this read scope. Everything that
depends on them is marked UNKNOWN. References below name the file and function;
there are no line anchors. `repair_policy.<name>` is a function in
`tools/llmtool/llmtool/repair_policy.py`, and `review_repair.<name>` is a
function in `tools/llmtool/llmtool/review_repair.py`.

## 1. Cause group per run

### Provider (quota, model or service): 6

- `787f13ed` — `AGY_QUOTA_EXHAUSTED`, 7 changed files, no continues. Quota is
  a provider-side limit; the changed files show work done before exhaustion,
  not a failing test or verdict.
- `d62de6f7` — `AGY_QUOTA_EXHAUSTED`, 1 changed file, continues `8b2e7ac2`.
- `db2fd113` — `AGY_QUOTA_EXHAUSTED`, 0 changed files, continues `36003537`.
- `da2ac4df` — `AGY_QUOTA_EXHAUSTED`, 0 changed files, no continues.
- `8a86c682` — `AGY_QUOTA_EXHAUSTED`, 0 changed files, continues `835ac8a4`.
- `1360cf87` — `AGY_QUOTA_EXHAUSTED`, 1 changed file, continues `8a86c682`.

All six end on the provider's quota error; none carries a code, test or review
failure code. The changed-file counts differ, but the terminating condition is
provider capacity.

### Lock, start or environment: 5

- `d3345cb1` — `AGY_EXIT_NONZERO`, 0 changed files, no continues. A process
  exit with no changed file points to failure at start, before work.
- `66009faf` — `AGY_EXIT_NONZERO`, 0 changed files, continues `361f9d74`. The
  same start-time exit, with a continuation already recorded.
- `6bf7cdaa` — `AGY_GIT_STATE_CHANGED_SNAPSHOT`, 0 changed files, no continues.
  A git-state snapshot guard fired; this is an environment or lock symptom.
- `e3fa75be` — `AGY_GIT_STATE_CHANGED_SNAPSHOT`, 0 changed files, no continues.
- `9808642f` — `AGY_TIMEOUT`, 0 changed files, no continues. A timeout with no
  changed file means the timeout ended the run before any work.

None of these five is a defect in the Task's code or tests; each is a guard or
process outcome around the start of a run.

### Real code or test failure: 0

No run carries a failed review verdict, EC2 receipt or CI failure code. The
handlers for those cases (`repair_policy.test_failure_action`, and the test
branch of `review_repair.repair_review`) are not the reported outcome here.

### UNKNOWN: 1

- `45e55c20` — `DEVELOPER_OUTPUT_SENSITIVE`, state `DEVELOPER_OUTPUT_REJECTED`,
  0 changed files, no continues. This is an output-screen refusal after model
  output. It is not a provider quota, not a start error and not a code or test
  failure; the screen's decision and the rejected content are outside this
  read scope, so the cause stays UNKNOWN.

Emitter caveat: none of the `AGY_*` or screen codes appears in either read
file. The grouping above uses the code name and the PM-quoted fields; where
each emitter or first handler sits is outside scope and is UNKNOWN. The only
in-scope handling is that `review_repair.repair_review` treats
`DEVELOPER_OUTPUT_REJECTED` as a Developer state from which a repair may still
be dispatched.

## 2. Does the repair policy count these runs as effective repairs?

Repair policy counts repairs, not Developer runs.

- `repair_policy.repair_attempts_max` derives the bound from `repair_max`
  and `test_fail_max`.
- `repair_policy.admit_repair` refuses once the used count reaches
  `repair_max`.
- `repair_policy.review_fail_count` and `repair_policy.review_action` count
  valid review FAILs per reviewed HEAD, not provider or start errors.
- `repair_policy.test_failure_action` counts code-caused test failures.

None of these functions takes an AGY, quota, exit, timeout or git-state input,
and neither read file maps such an error to a refund, a free retry or a
non-effective repair. No in-scope code says that a provider or start failure
is not a used repair.

`review_repair.repair_review` commits the repair claim in
`review_repair_claims` before the provider adapter is called. On an adapter
exception, `review_repair._update_claim_state` sets the claim to `UNKNOWN` and
keeps it; when the adapter returns and the run later ends as a failure, the
claim stays `DISPATCHED`. A re-dispatch of the same packet reuses that claim
(the `existing` match includes `CLAIMED`, `DISPATCHED` and `UNKNOWN`), but the
claim is never released.

Per error code:

- `AGY_QUOTA_EXHAUSTED` — UNKNOWN. If the run was a repair dispatch,
  `review_repair.repair_review` had committed a claim before the provider call,
  so the claim remains (`UNKNOWN` or `DISPATCHED`) and is part of the Task's
  stored attempts. `review_repair._review_gate` then passes
  `repair_count`/`counts` to `repair_policy.admit_repair` and
  `repair_policy.review_action`; whether `lineage_counts` treats that
  claim state as effective is outside scope. `review_repair.repair_review` also
  refuses a new repair when `worker_availability.fresh_quota_fail` is true,
  before any claim, so a known quota refusal can prevent a later claim instead
  of spending one. Whether any of the six runs was itself a repair dispatch is
  also outside scope.
- `AGY_EXIT_NONZERO` — UNKNOWN. A non-zero exit after the claim commit leaves
  a claim; a failure before the commit leaves none
  (`review_repair._refuse_unclaimed` from `review_repair._record_repair_task`,
  or `REPAIR_HEAD_MOVED` from `review_repair._registered_d6` and
  `review_repair._current_d6_head`). Which side these two runs fall on depends
  on the AGY runner and the TaskStore, outside scope.
- `AGY_TIMEOUT` — UNKNOWN. A timeout raised through the adapter follows the
  exception path (`review_repair._update_claim_state` sets `UNKNOWN`, claim
  kept); a timeout enforced before the claim commit leaves none. The split is
  outside scope.
- `AGY_GIT_STATE_CHANGED_SNAPSHOT` — UNKNOWN. The snapshot checks visible in
  review_repair (`review_repair._registered_d6` and
  `review_repair._current_d6_head`, both `REPAIR_HEAD_MOVED`) run before the
  claim; the emitter of this code is outside scope, so whether it fired before
  or after the claim commit is UNKNOWN.
- `DEVELOPER_OUTPUT_SENSITIVE` — UNKNOWN. `review_repair.repair_review`
  accepts `DEVELOPER_OUTPUT_REJECTED` as a state from which a repair may be
  dispatched, so the rejection alone does not block a later repair. Whether
  this run held a repair claim (the screen runs after model output, so after
  any claim commit) and whether it counts as used is decided outside scope.

## 3. Code path where a provider or start failure spends a repair

In `review_repair.repair_review` (`tools/llmtool/llmtool/review_repair.py`):

1. Under the chain lock, `lineage_counts` supplies the counts and
   `review_repair._review_gate` admits the repair through
   `repair_policy.review_action` and `repair_policy.admit_repair`.
2. The claim is built and appended to `review_repair_claims`;
   `review_repair._record_repair_task` records the bounded repair Task; the
   Owner switch is rechecked; `store.compare_and_update` commits the claim.
3. Only after that commit does the adapter call the provider ("The provider is
   called only after the claim CAS succeeded").
4. Any adapter exception marks the claim `UNKNOWN` and keeps it; a returned
   run that ends as a provider or start failure leaves it `DISPATCHED`.
5. The next dispatch numbers its attempt from the family's stored claims
   (`stored`, then `attempts`) and passes `counts` to
   `review_repair._review_gate`; `repair_policy.admit_repair` refuses at
   `repair_max` and `repair_policy.review_action` escalates by kind.

So a quota refusal, non-zero exit, timeout or snapshot abort raised after
step 2 spends a stored repair claim even though no code was repaired. A
comparable failure before step 2 (a record refusal surfaced by
`review_repair._refuse_unclaimed`, a head check, or the policy/quota gate)
spends none. Whether the failed run is also charged to the run budget depends
on `run_budget` and `repair_policy.repair_attempts_max`; that part is outside
this read scope and is UNKNOWN.

## 4. Summary table

Counted as effective repair is UNKNOWN on every row for two reasons: the
quoted TaskStore fields do not show which runs were repair dispatches, and the
state filter that decides "effective" is in `lineage_counts`, outside this
read scope. Two points are certain from the read code: a run that was not a
repair dispatch never held a repair claim, so it is not counted on either
repair count, and a failure raised before the claim commit spends nothing. The
in-scope code does not classify these errors, and it does not release a
committed claim, so the per-run answer cannot be narrowed further here.

| Task id | Group | Counted as effective repair |
| --- | --- | --- |
| 787f13ed | Provider (quota) | UNKNOWN |
| d3345cb1 | Lock/start/environment | UNKNOWN |
| 6bf7cdaa | Lock/start/environment | UNKNOWN |
| 9808642f | Lock/start/environment | UNKNOWN |
| e3fa75be | Lock/start/environment | UNKNOWN |
| 45e55c20 | UNKNOWN (sensitive output) | UNKNOWN |
| d62de6f7 | Provider (quota) | UNKNOWN |
| db2fd113 | Provider (quota) | UNKNOWN |
| da2ac4df | Provider (quota) | UNKNOWN |
| 8a86c682 | Provider (quota) | UNKNOWN |
| 1360cf87 | Provider (quota) | UNKNOWN |
| 66009faf | Lock/start/environment | UNKNOWN |
