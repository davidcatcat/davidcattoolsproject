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

### Lock, start or environment: 2

- `6bf7cdaa` — `AGY_GIT_STATE_CHANGED_SNAPSHOT`, 0 changed files, no continues.
  A git-state snapshot guard fired; the code names a git-state check, so this is
  an environment or lock symptom, not a code or test failure. Whether the guard
  fired before work began or after it is UNKNOWN, because the emitter is outside
  this read scope.
- `e3fa75be` — `AGY_GIT_STATE_CHANGED_SNAPSHOT`, 0 changed files, no continues.
  Same code and the same UNKNOWN timing.

Neither run is a defect in the Task's code or tests.

### Real code or test failure: 0

No run carries a failed review verdict, EC2 receipt or CI failure code. The
handlers for those cases (`repair_policy.test_failure_action`, and the test
branch of `review_repair.repair_review`) are not the reported outcome here.

### UNKNOWN: 4

- `d3345cb1` — `AGY_EXIT_NONZERO`, 0 changed files, no continues. The code says
  a process exited non-zero; that is neither a provider error nor a failed
  code/test check. Zero changed files does not prove no work happened: a process
  can exit after doing work without changing a file. Nothing in this read scope
  fixes the phase, so start-time and after-work exit are both possible and the
  group is UNKNOWN.
- `66009faf` — `AGY_EXIT_NONZERO`, 0 changed files, continues `361f9d74`. Same
  code and the same UNKNOWN phase; the recorded continuation does not show when
  in the run the exit happened.
- `9808642f` — `AGY_TIMEOUT`, 0 changed files, no continues. The code says the
  run timed out. A timeout may end a run during or after work, and a process can
  do work without changing a file, so the phase is UNKNOWN rather than
  established as before work.
- `45e55c20` — `DEVELOPER_OUTPUT_SENSITIVE`, state `DEVELOPER_OUTPUT_REJECTED`,
  0 changed files, no continues. This is an output-screen refusal after model
  output. It is not a provider quota, not a start error and not a code or test
  failure; the screen's decision and the rejected content are outside this
  read scope, so the cause stays UNKNOWN.

Emitter caveat: none of the `AGY_*` or screen codes appears in either read
file. The grouping above uses the code name and the PM-quoted fields; where
each emitter or first handler sits is outside scope and is UNKNOWN, and the
changed-file count alone cannot establish when a run stopped. The only
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
non-effective repair. The in-scope code therefore gives no rule either way, so
the effective-repair answer for these codes is UNKNOWN here.

The confirmed claim behaviour is in `review_repair.repair_review`: it commits
the repair claim in `review_repair_claims` before the provider adapter is
called. On an adapter exception, `review_repair._update_claim_state` sets the
claim to `UNKNOWN` and keeps it; when the adapter returns and the run later ends
as a failure, the claim stays `DISPATCHED`. A re-dispatch of the same packet
reuses that claim (the `existing` match includes `CLAIMED`, `DISPATCHED` and
`UNKNOWN`), but the claim is never released. A stored claim is not by itself
proof of an effective repair: the counting rule lives in `lineage_counts`,
outside this read scope.

Per error code:

- `AGY_QUOTA_EXHAUSTED` — UNKNOWN. If the run was a repair dispatch,
  `review_repair.repair_review` had committed a claim before the provider call,
  so the claim remains (`UNKNOWN` or `DISPATCHED`) and stays a stored claim.
  `review_repair._review_gate` then passes `repair_count`/`counts` to
  `repair_policy.admit_repair` and `repair_policy.review_action`; whether
  `lineage_counts` treats that claim state as effective is outside scope, so
  this note does not conclude that an effective repair was used.
  `review_repair.repair_review` also refuses a new repair when
  `worker_availability.fresh_quota_fail` is true, before any claim, so a known
  quota refusal can prevent a later claim instead of committing one. Whether any
  of the six runs was itself a repair dispatch is also outside scope.
- `AGY_EXIT_NONZERO` — UNKNOWN. A non-zero exit raised after the claim commit
  leaves a claim (adapter exception gives `UNKNOWN` via
  `review_repair._update_claim_state`; a returned failed run leaves
  `DISPATCHED`); a failure before the commit leaves none
  (`review_repair._refuse_unclaimed` from `review_repair._record_repair_task`,
  or `REPAIR_HEAD_MOVED` from `review_repair._registered_d6` and
  `review_repair._current_d6_head`). Which side these two runs fall on depends
  on the AGY runner and the TaskStore, outside scope, so an effective repair is
  not confirmed.
- `AGY_TIMEOUT` — UNKNOWN. A timeout raised through the adapter follows the
  exception path (`review_repair._update_claim_state` sets `UNKNOWN`, claim
  kept); a timeout enforced before the claim commit leaves none. The split is
  outside scope, so an effective repair is not confirmed.
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

## 3. Code path where a provider or start failure could spend a repair

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

The confirmed effect of a failure after step 2 is that the stored claim is not
released by this module: an adapter exception leaves it `UNKNOWN`, a returned
failed run leaves it `DISPATCHED`. Whether that stored claim is then counted as
an effective repair is not decided in either read file; `lineage_counts` and
its state filter are outside this read scope, so an effective repair is UNKNOWN
rather than confirmed. A comparable failure before step 2 (a record refusal
surfaced by `review_repair._refuse_unclaimed`, a head check, or the
policy/quota gate) leaves no claim in this path. The only in-scope path where a
provider or start failure could be counted as a used repair is therefore a
claim persisted at step 2 that `lineage_counts` later reads as effective; that
link is UNKNOWN. Whether the failed run is also charged to the run budget
depends on `run_budget` and `repair_policy.repair_attempts_max`; that part is
outside this read scope and is UNKNOWN.

## 4. Summary table

Counted as effective repair is UNKNOWN on every row for two reasons: the
quoted TaskStore fields do not show which runs were repair dispatches, and the
state filter that decides "effective" is in `lineage_counts`, outside this
read scope. What is confirmed from the read code is claim persistence, not an
effective-repair count: `review_repair.repair_review` commits the claim before
calling the provider and never releases it, so a failure after that commit
leaves a stored claim. A run that was not a repair dispatch held no claim from
this path, and a failure raised before the claim commit leaves no claim there.
The in-scope code does not classify these errors and does not turn a stored
claim into a confirmed used repair, so the per-run answer cannot be narrowed
further here.

| Task id | Group | Counted as effective repair |
| --- | --- | --- |
| 787f13ed | Provider (quota) | UNKNOWN |
| d3345cb1 | UNKNOWN (exit timing) | UNKNOWN |
| 6bf7cdaa | Lock/start/environment | UNKNOWN |
| 9808642f | UNKNOWN (timeout timing) | UNKNOWN |
| e3fa75be | Lock/start/environment | UNKNOWN |
| 45e55c20 | UNKNOWN (sensitive output) | UNKNOWN |
| d62de6f7 | Provider (quota) | UNKNOWN |
| db2fd113 | Provider (quota) | UNKNOWN |
| da2ac4df | Provider (quota) | UNKNOWN |
| 8a86c682 | Provider (quota) | UNKNOWN |
| 1360cf87 | Provider (quota) | UNKNOWN |
| 66009faf | UNKNOWN (exit timing) | UNKNOWN |
