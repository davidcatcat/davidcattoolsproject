# Recovery deadlocks in the repair, Debug and review gates (#2415)

Status: read-only architecture investigation and proposal. Nothing here is
implemented. No code, test or rule file is changed by this document.

Source: `main` at the Task source commit 64c22204 (#2409 / PR #2410).
Request: Issue #2415, Owner request relayed on #1886 (c7029114337).

## 0. Scope and evidence rules

Read for this investigation (the Task read paths):

- `tools/llmtool/llmtool/high_debug.py`
- `tools/llmtool/llmtool/lineage_counts.py`
- `tools/llmtool/llmtool/review_repair.py`
- `tools/llmtool/llmtool/task_advance.py`
- `tools/llmtool/llmtool/task_submission_cli.py`
- `tools/llmtool/tests/test_task_delegate_repair_cap.py`

Not read, so every claim that depends on them is marked UNKNOWN:

- GitHub: Issue #2415, the #1886 comments c7018124949 and c7029114337, PRs
  #2377, #2406, #2408 and #2414 (no network for this Task).
- The Task store and its records for those PRs.
- `repair_policy.py` and `policy.json` (the exact `admit_debug`,
  `review_action`, `test_failure_action` and `admit_repair` rules and the
  Debug limits), `review_dispatch.py`, `review_result.py`,
  `owner_exception.py`, `start_failure.py` and the CI workflow files.

The incident facts below come from the Task statement. The blocking paths
come from the code on `main`. Code is named by file and function, never by
position.

## 1. Summary

The repair machinery has one way to make progress after a failure: it blames
the failure on the Developer's work item, spends one unit of a chain-wide
budget, and sends the work back to the Developer. That is correct for
Developer faults. It has no class for a failure of the tool's own gate (a
Debug launcher, a CI step limit, a lost Reviewer result). When such a gate
fails, three design rules that are each right on their own combine into a
deadlock:

1. Doubt is consumption. An outcome the tool cannot prove harmless counts
   as a used Debug, a used repair or the limit.
2. Limits cannot be raised from inside the tool. A continuation does not
   reset them, old override fields are ignored, and changing the family does
   not clear a block.
3. The gate that failed is also the only way to release the budget it used.
   Repair #2 needs a confirmed Debug, and a Debug needs a free Debug slot.

So the only exit is outside the tool: a code fix that changes how the tool
reads its own evidence (as #2407 did for not-started Debugs), or an Owner
exception. The existing Owner exception for a merge, in
`task_advance._merge_once`, replaces the formal review PASS for the head.
That is much wider than the fault, which was one miscounted unit.

The recommendation (section 6) is to make two kinds of change together:

- narrow classifier fixes for the three known classes;
- a small, Owner-authorized, evidence-bound gate-fault record. It can
  reclassify exactly one recorded item as tool-caused, and only with
  positive evidence that the gate caused it. Matching identifiers are not
  enough. It never raises a limit, adds a PASS, skips the root-cause rule
  or merges anything.

## 2. The normal recovery flow on `main`

```
failure on a head
  |-- review FAIL with a blocking finding --> review_repair.repair_review
  |       (_review_gate: repair #1 free; #2 needs RCA_CONFIRMED Debug on the
  |        failed head; #3..repair_max covered by that Debug; then escalate)
  |-- EC2 FAIL receipt ---------------------> repair_review(remote_test_receipt)
  |-- required CI check 'failure'/'timed_out'
  |       --> task_advance._route_ci_failure --> repair_review(ci_failure)
  |       (EC2 and CI share one '<base>-ec2' count, test_fail_max)
  |-- CI 'cancelled'/'startup_failure' ----> WAITING_ENV, uncounted, no re-run
  |-- Reviewer result unclear -------------> REVIEW_OUTCOME_UNKNOWN (stop)
  `-- Developer start failure (#2188) -----> DEVELOPER_START_FAILED,
                                            PM runs `task retry-start`
```

Every arrow into `repair_review` spends a unit. The unit is counted across
the whole continuation chain by `lineage_counts.lineage_counts` and
`lineage_counts.chain_review_repairs`. The delegate checks the counts again
in `task_submission_cli._check_delegate_repair_cap`, which applies the
review and EC2 limits in `_check_repair_limits` and the root-cause rule in
`_check_repair_rca`.

The test file asserts the limits as of `main`: `repair_max` 5 and
`test_fail_max` 5, so at most 6 review runs per D6
(`test_repository_policy_allows_five_review_and_five_test_repairs`).

## 3. Invariants that take part

| ID | Invariant | Where on `main` |
|----|-----------|-----------------|
| INV-1 | Fail closed on counts: a count is never 0 by doubt; UNKNOWN is the limit; any doubtful Debug entry is used. | `lineage_counts` module docstring; `high_debug._debug_entry_used`; `review_repair._review_gate` |
| INV-2 | Limits are chain-wide; a continuation never resets a limit. | `lineage_counts` module docstring, `lineage_counts.chain_debug_used`, `lineage_counts.check_continuation_link` ("No override") |
| INV-3 | No in-band override: old per-Task override fields are ignored; changing the family never clears a screen block. | tests `test_existing_override_field_is_ignored`, `LegacyDataTests`; `high_debug._require_screen_cleared` |
| INV-4 | Root cause before repair #2 (D1): a RCA_CONFIRMED Debug on the failed head. | `review_repair._review_gate` (REPAIR_DEBUG_REQUIRED); `task_submission_cli._check_repair_rca` |
| INV-5 | One AI Debug per ROOT or REPLAN Task, counted over the chain; split children share a group limit. | `high_debug._admit_debug` docstring; `high_debug._reserve_debug`; `high_debug._group_debug_used` |
| INV-6 | A failed exact-head required CI check is repaired like an EC2 FAIL and shares its count (D11). | `review_repair._ci_failure`; `task_advance._route_ci_failure` |
| INV-7 | CI is never re-run or re-requested; only ENV conclusions wait uncounted. | `task_advance._CI_ENV_CONCLUSIONS` and its comment |
| INV-8 | No second Reviewer while a verdict may exist; one head gets one redispatch. | `task_advance.decide`; `task_advance._review_verdict_absent` |
| INV-9 | The Owner never acts on GitHub; Owner decisions are relayed by the PM. | common core rule 7; `AGENTS.md` / `CLAUDE.md` Owner channel |

None of these rules is wrong for a Developer fault. All the deadlocks below
come from applying them to a fault in the tool's own gate.

## 4. Incidents

### 4.1 PR #2406 and PR #2377: `HIGH_DEBUG_DEBUG_PARENT_USED`

**Observed (Task statement).** An AI Debug on each PR was refused with
`HIGH_DEBUG_DEBUG_PARENT_USED`, and an Owner exception was needed.

**Blocking path on `main`.**

1. `high_debug.run_high_debug` calls `_admit_debug(task, entries)` before any
   D6 or provider call. After that, `_reserve_debug` runs under the chain
   lock (`lineage_counts.acquire_chain_lock` on
   `high_debug._debug_kind_and_key`).
2. Both count used entries with `high_debug._debug_entry_used`.
   `_reserve_debug` also counts the other chain Tasks' `high_debug` lists
   through `lineage_counts.chain_debug_used` (INV-2).
3. `_debug_entry_used` returns True for every entry except a RUN_FAILED
   entry whose report is a not-started code (`_DEBUG_NOT_STARTED`:
   quota, auth, classified D6 create failure). Since #2367 / PR #2407, a
   launcher pre-launch `ROLE_MODEL_NOT_ALLOWED` with nothing observed is
   also not used (`_DEBUG_PRELAUNCH_REFUSED`, `_prelaunch_refused`). Any
   other doubt counts as used (INV-1).
4. `repair_policy.admit_debug` returns a code, and `_admit_debug` /
   `_reserve_debug` raise `'HIGH_DEBUG_' + code`. That code is
   `DEBUG_PARENT_USED` here. The exact condition is in `repair_policy`
   (not read). The `_admit_debug` docstring says one Debug per ROOT or
   REPLAN Task (INV-5).

**Why it deadlocks.** Repair #2 needs a RCA_CONFIRMED Debug on the failed
head (INV-4). It checks this twice: `review_repair._review_gate` refuses
REPAIR_DEBUG_REQUIRED, and the delegate refuses
DELEGATE_HIGH_DEBUG_TASK_REQUIRED. The one Debug slot is already held by an
entry that produced no diagnosis. Nothing in the tool can free it (INV-3).
A continuation Task inherits the used slot (INV-2), and a new family does
not help because the count is not per family. The chain cannot move.

**What `main` has changed since.** Commit 2d0b0522 (#2367, PR #2407): "a
not-started AI Debug does not use up the chain's Debug". It reads stored
evidence again at count time, so it frees qualifying old entries without a
data change. A classifier fix of this kind is the safest way to make a
recovery change.

**What still blocks on `main`.** These Debug outcomes are tool or
infrastructure faults that still use the slot:

- An entry left `RESERVED`. In `run_high_debug`, an unclassified create
  failure refuses with detail `reservation=RESERVED`, and a failed
  `finalize_high_debug` adds `reservation=UNFINALIZED`. A crashed process
  also leaves a reservation. `_debug_entry_used` treats any status other
  than RUN_FAILED as used.
- `HIGH_DEBUG_RUN_FAILED` from a provider timeout
  (`subprocess.TimeoutExpired` falls into the generic `except Exception`)
  or any runner exception.
- `HIGH_DEBUG_REPORT_INVALID` from a screen false positive. The screen
  block can be cleared through `record_screen_disposition` or
  `confirm_screen_batch`, but the Debug entry stays used. The chain then
  has a cleared screen and no Debug left.
- `HIGH_DEBUG_MODEL_UNVERIFIED` and `HIGH_DEBUG_WROTE_FILES`.

**What the stored entry can and cannot prove.** Counting these entries as
used is correct on the evidence stored today. Neither shape proves that no
provider process ran:

- `run_high_debug` writes the `RESERVED` entry (report
  `HIGH_DEBUG_RESERVED`, model, effort and usage `UNKNOWN`) before the D6
  create. Nothing updates it until `failed` or the final
  `finalize_high_debug` call, and both run after the runner returns or
  raises. A process that crashes during or after the provider run leaves the
  same `RESERVED` entry as one that crashed before the D6 create. A
  `finalize_high_debug` failure inside `failed` (detail
  `reservation=UNFINALIZED`) can also come after a provider run.
- `failed` writes RUN_FAILED with model, effort and usage still `UNKNOWN`
  after a provider run too. Examples: a Codex non-zero exit
  (`_codex_failure_code`), oversized Codex output, and a
  `subprocess.TimeoutExpired` from `_run_codex` or `_run_claude`, which
  lands in the generic `except Exception`.
- The only shapes that prove "not started" today are the ones
  `_debug_entry_used` already frees: `_DEBUG_NOT_STARTED` and
  `_prelaunch_refused`. Each is tied to one code path that runs before the
  launcher calls `subprocess.run`.

**Conflicting invariants.** INV-1 and INV-5 together say that a doubtful
Debug uses the one Debug. INV-4 says that the next repair needs a Debug.
INV-2 and INV-3 say that nothing inside the tool can resolve the conflict.

**UNKNOWN.** Which entries the affected Tasks stored, and whether the
refusal came from `_admit_debug` or from `_reserve_debug`. Commit #2407
suggests a not-started entry, but this investigation cannot confirm it. The
values of `parent_debug_max` and `group_debug_max`. How the Owner exception
was applied.

### 4.2 PR #2408: a CI step timeout routed as a test failure

**Observed (Task statement).** A CI step timeout on PR #2408 was classified
as a test failure.

**Blocking path on `main`.**

1. `task_advance._advance_step` sends the newest exact-head Linux run to
   `_route_ci_failure` when its conclusion is `failure`, `timed_out` or
   an ENV conclusion. This happens at the DISPATCH_REVIEW gate. The Mac run
   is routed the same way after PASS, in the RUN_TASK_COMPLETE and merge
   paths.
2. `_route_ci_failure` treats only `cancelled` and `startup_failure` as
   ENV (`_CI_ENV_CONCLUSIONS`, INV-7). Both `failure` and `timed_out`
   continue to repair. `_mac_capacity_cancel` explicitly returns None
   when the `select` job or a `shards` job ended `timed_out`, so a Mac
   timeout is never treated as a capacity cancel.
3. It builds `ci_failure` with `'conclusion': 'failure'` whatever the
   real conclusion was. `review_repair._ci_failure` then binds the claim
   identity to `conclusion: failure`. The stored claim cannot show later
   that the check timed out.
4. `review_repair.repair_review` stores the claim in the `<base>-ec2`
   family and counts it against `test_fail_max` (INV-6), then dispatches
   the original Developer with the excerpt.

**Why it deadlocks.** A step time limit is part of the CI configuration, not
the Developer's change. The Developer usually cannot fix it inside the
Task's write paths (UNKNOWN for #2408). Each repair round spends a test
unit. A repair with no change leads to REPAIR_RESULT_NOT_PUBLISHED
(`_route_ci_failure` on action PUBLISH) or REPAIR_RETRY. When the count is
used up, the path ends at `TEST_FAIL_LIMIT_REACHED`. The tool must not
re-run CI (INV-7). So no step inside the tool can produce a green check on
the same head.

**Conflicting invariants.** INV-6 classifies by the conclusion string only.
INV-7 allows an uncounted wait only for two conclusions and forbids the
re-run that would settle the case. INV-1 also applies:
`repair_review`'s test branch refuses REPAIR_LIMIT_REACHED both when the
count is used up and when it is UNKNOWN, and `_advance_step` reports both
as TEST_FAIL_LIMIT_REACHED. A PM cannot tell a store doubt from a spent
budget.

**What `main` has changed since.** Commit 64c22204 (#2409, PR #2410): "llmtool
CI uses GitHub's default timeouts". The commit title points to a workflow
change, which is outside the read paths. The classifier in
`_route_ci_failure` is unchanged on `main`, so any future `timed_out`
conclusion is still a counted Developer repair.

**UNKNOWN.** Whether the #2408 run ended `timed_out` or `failure`. Whether
the GitHub API reliably tells a step timeout from a test failure without
reading logs. How the Owner exception c7018124949 was applied. Note that
`_merge_once` still runs `_merge_checks` under an Owner exception.

### 4.3 PR #2414: `REVIEW_OUTCOME_UNKNOWN` with no recovery command

**Observed (Task statement).** `task advance` stopped at
REVIEW_OUTCOME_UNKNOWN on PR #2414 and named no recovery command.

**Blocking path on `main`.** `task_advance.decide` returns
REVIEW_OUTCOME_UNKNOWN when:

- the newest claim on the head is `CLAIMED`, or `UNKNOWN` without a
  known `failure_reason` (`_failure_reason`);
- a REVIEW_REDISPATCH cannot prove the verdict is absent:
  `_review_verdict_absent` returns False when the comment read fails,
  returns 100 or more comments ("A full page may hide later comments"), or
  finds any comment that `_may_be_review_verdict` considers verdict-shaped
  (head SHA plus a verdict marker, any author);
- a relayed verdict was posted but not read back
  (REVIEWER_VERDICT_RELAY_UNCONFIRMED in `_advance_step`);
- a REVIEWER event exists whose outcome is none of BLOCKED, FAIL or PASS
  (the final return of `decide`).

`_advance_step` then appends only the step name and any failure reason to
`codes`. It sets `next` to the same step name. Unlike DEVELOPER_START_FAILED
(which returns the repair Task for `task retry-start`) or REVIEW_NOT_POSTED
(which returns a resume hint), it names no actor, command or evidence to
collect. The record does not change, so every later advance returns the same
stop.

**Why it deadlocks.** INV-8 correctly refuses to start a second Reviewer
while a verdict may exist. But the tool cannot settle "may exist" with
evidence it can read. A claim that never left `CLAIMED`, for example after
a crash between the claim write and the dispatch, has no failure reason and
so never qualifies for the one redispatch. A PR with 100 or more comments
can never prove absence. Related reads have the same limit:
`review_repair._failed_event` refuses REVIEW_RESULT_COMMENTS_INVALID and
`task_advance._review_outcomes` skips the claim. Under INV-9 the Owner
cannot act on GitHub to resolve it.

**Conflicting invariants.** INV-8 (safety: no Reviewer shopping) against
liveness (every stop should name who acts next). Liveness is not a coded
rule. Only the #2188 START_FAILED path models it.

**UNKNOWN.** The claim state and the comment count on PR #2414. Whether
`review_dispatch` or `review_result` provides a reconcile command (outside
the read paths).

### 4.4 The common pattern

In every case a gate G guards a budget or progress item B, and these three
things hold:

1. When G itself fails, the failure is recorded as a spent B or a stop
   (INV-1, INV-6, INV-8).
2. B cannot be restored or bypassed inside the tool (INV-2, INV-3, INV-7).
3. The only ways forward are G succeeding on the same item, a code change
   to how G reads its evidence, or an Owner exception.

The code fix itself is delivered through the same pipeline. A fix PR is a
different chain, so it is not blocked by the stuck chain's budget. It can
still hit the same gate defect. Whether that happened for #2407 or #2410 is
UNKNOWN.

## 5. What the current flow can and cannot recover from

"Recovers" means the tool reaches a state where a named actor can continue
without an Owner exception or a code change.

| Failure class | Recovers? | Evidence on `main` |
|---|---|---|
| Review FAIL with blocking findings (Developer fault) | Yes, up to `repair_max`, then escalation | `review_repair._review_gate` |
| EC2 FAIL with executed failures | Yes, up to `test_fail_max` | `review_repair._remote_test_failure`; `task_advance._remote_fail_action` |
| Required CI `failure` caused by a real test failure | Yes, on the shared test count | `task_advance._route_ci_failure` |
| CI `cancelled` / `startup_failure`; Mac capacity cancel | Waits uncounted; the tool never re-runs CI. Who re-runs it: UNKNOWN | `_CI_ENV_CONCLUSIONS`; `_mac_capacity_cancel` |
| CI `timed_out`, or a step timeout reported as `failure` | **No**: counted Developer repair, ends at TEST_FAIL_LIMIT_REACHED or REPAIR_RESULT_NOT_PUBLISHED | `_route_ci_failure` |
| EC2 TIMEOUT | One automatic re-run, then NEEDS_ATTENTION with no named command | `_remote_test_timeout_count`; `_pre_review_action` |
| EC2 SHA_MISMATCH / NOT_EXECUTED | NEEDS_ATTENTION; recovery command UNKNOWN | `_remote_test_status`; `_advance_step` |
| Remote test host unavailable | Yes, waits and retries | `_REMOTE_TEST_RETRY_CODES` |
| Debug not started (quota, auth, classified D6 create, pre-launch model refusal) | Yes, the slot is not used | `_DEBUG_NOT_STARTED`; `_prelaunch_refused` |
| Debug quota/auth on Codex | Yes, one Claude fallback, else HIGH_DEBUG_WAITING | `run_high_debug_with_fallback` |
| Debug left RESERVED, timed out, crashed, MODEL_UNVERIFIED, WROTE_FILES | **No**: the slot is used, and repair #2 then cannot be admitted | `_debug_entry_used`; `run_high_debug` |
| Debug report screen false positive | Partly: the screen block clears through disposition, but the Debug slot stays used | `record_screen_disposition`; `confirm_screen_batch`; `_debug_entry_used` |
| Debug RCA_UNCONFIRMED / BLOCKED (real result) | Designed escalation (split, PM decision, final). Exact mapping UNKNOWN (`repair_policy`) | `_review_gate` via `review_action` |
| 8 or more Debug entries on a Task, not-started ones included | Hard stop HIGH_DEBUG_LIMIT (a bound, by design) | `run_high_debug` |
| Review claim CLAIMED, or UNKNOWN without a failure reason | **No**: REVIEW_OUTCOME_UNKNOWN, no command | `decide` |
| Review unusable with a known failure reason | One redispatch, then REVIEW_UNUSABLE_STOPPED | `decide`; `_review_verdict_absent` |
| PR with 100 or more comments | **No**: absence and the FAIL event cannot be proved | `_review_verdict_absent`; `_failed_event`; `_review_outcomes` |
| Reviewer route unavailable | One fallback, then REVIEWER_UNAVAILABLE | `_reviewer_fallback`; `_blocked_fallback_reviewer` |
| Repair Developer start failure | Yes: PM runs `task retry-start`; not counted | `_start_failed_repair`; `_recorded_start_failure` |
| Repair Developer launched, did no work | Yes, not counted | `_repair_run_stamp`; `LaunchedNoWorkRepairCountTests` |
| Repair outcome UNKNOWN | Stop REPAIR_OUTCOME_UNKNOWN; command UNKNOWN | `_remote_fail_action`; `_advance_step` |
| Chain in doubt (fork, unreadable record, cycle) | **No** in `review_repair`: reported as REPAIR_LIMIT_REACHED. The delegate reports DELEGATE_REPAIR_COUNT_UNAVAILABLE. An empty successor can be re-delegated (#2188 section 6) | `lineage_counts.lineage_counts`; `check_continues` |
| Chain lock busy | Yes, transient refusal | `acquire_chain_lock` |
| Policy unreadable | Refuses until `policy.json` is fixed (outside the tool) | `review_repair._policy`; `_check_delegate_repair_cap` |

Rows marked **No** are the classes where a tool or infrastructure fault
currently needs a code change or an Owner exception.

## 6. Is a bounded recovery mechanism needed?

### 6.1 Option A: none, fix each classifier

Each known class gets a classifier fix like #2407, which reads evidence
again at count time:

- Debug: record write-ahead evidence that the provider was not launched
  (section 6.4 item 2), and free only entries that carry it. A `RESERVED` or
  `UNKNOWN`-observed RUN_FAILED shape alone is not enough (section 4.1).
- CI: record the real conclusion, then decide which conclusions are ENV.
  `timed_out` cannot simply become ENV, because a Developer change that
  makes a test hang also ends `timed_out`.
- Review: name a next command, and prove absence with a full paginated
  comment read.

For:

- no new authority;
- no new record type;
- a re-read fix also applies to old records that already hold the evidence,
  as #2407 did. A fix that needs a new evidence field helps only records
  written after it.

Against:

- every new defect class deadlocks until its fix is written, reviewed and
  merged;
- meanwhile the only exit is the Owner merge exception, which waives the
  formal review PASS for the whole head (`_merge_once` builds
  `review_exception` instead of `_merge_pass_claim`);
- some evidence is already lost: the CI claim stores `conclusion: failure`
  for a timeout, so a classifier cannot recover it later;
- two exceptions in 24 hours (Issue #2415) suggest new classes will keep
  appearing while the gates change quickly.

### 6.2 Option B: a gate-fault record (proposal)

A gate-fault record says that one already recorded item was caused by the
tool, not the Developer. It changes how that one item is counted. For a CI
item only, it also allows the one bounded re-run described below. Nothing
else changes. It follows two existing precedents:

- the #981 / #1097 screen disposition: an Owner-referenced Issue comment, a
  closed classification set, append-only history, digest binding, metadata
  only;
- the #2188 start-failure path: a recorded proof, the PM decides, and the
  item is not counted.

**Item kinds (closed set).** The classes are defined by the item that is
reclassified, not by the cause. This way a future cause on the same item
kind is covered without a new rule.

| Kind | Item it may bind | Effect |
|---|---|---|
| `DEBUG_NOT_LAUNCHED` | One `high_debug` entry | `_debug_entry_used` treats it as not used. One fresh Debug may be admitted on the same head. |
| `CI_GATE_FAULT` | One `-ec2` repair claim built from a CI check | The claim is left out of `test_fails`, and one bounded re-run of the bound check is allowed (see below). |
| `REVIEW_RESULT_LOST` | One review claim on one PR, head and version | Only with proof that the original dispatch and session have ended, the claim becomes eligible for the existing single REVIEW_REDISPATCH, as if it had a failure reason. |

Each kind refuses unless its evidence below holds. The evidence must show
that the gate caused the item. A shape that a Developer fault or a real
provider run could also produce is never enough. Any read that fails, is
incomplete or is in doubt refuses.

**`DEBUG_NOT_LAUNCHED` evidence.** The record needs positive proof that the
provider process was never started for that entry. As section 4.1 shows, the
stored shapes cannot give that proof today. A `RESERVED` entry and a
RUN_FAILED entry with model, effort and usage `UNKNOWN` are both written
after real provider runs too. So this kind depends on new write-ahead
evidence (section 6.4 item 2):

- Before `run_high_debug` calls the runner, it updates its own `RESERVED`
  entry to a launch stage (for example `launch: STARTED`). This is a
  compare-and-update under the chain lock. If the write fails, the launch is
  refused, so a provider process never runs without the marker.
- The record may bind only an entry whose status is still `RESERVED` and
  that has no launch marker. Under the same chain lock it appends the
  gate-fault event. The launcher's launch-stage write refuses if a gate-fault
  event exists for its entry. Without this check, a slow but live process
  could launch after its slot was freed, and two Debugs would run.
- Entries recorded before the marker existed carry no proof. They stay used,
  and the record refuses them. For those entries the Owner exception stays
  the only exit.
- An entry with a launch marker never qualifies, whatever happened after the
  launch (timeout, crash, MODEL_UNVERIFIED, WROTE_FILES, UNFINALIZED). A
  Debug that ran counts as used even when the tool caused the failure.
  Whether the Owner wants a different rule for "launched, no result" is an
  open policy question (section 7). This proposal does not decide it.
- RCA_CONFIRMED, RCA_UNCONFIRMED, BLOCKED and REPORT_INVALID entries never
  qualify.

Once the marker exists, freeing a `RESERVED` entry without a marker is
plain classifier logic (Option A) and needs no Owner record. The record
kind would cover only a fault the classifier has not yet been taught.
Whether such a fault exists for Debug entries is UNKNOWN.

**`CI_GATE_FAULT` evidence.** A real failed test has the same claim packet,
check run ID, head and check name as a gate fault. Matching those fields
proves only which item is bound, not what caused the failure. So the record
also needs all of the following, each read again by ID at recording time:

1. The check run, read with `task_advance._read_check_run`, is the bound
   completed exact-head run, and its real conclusion is recorded. The stored
   claim says `conclusion: failure` whatever the real conclusion was
   (section 4.2), so the real value must come from this read.
2. The trust checks of `task_advance._mac_capacity_cancel` apply to every
   required check, not only Mac:
   - the PR changes no `.github/` file, so the workflow is not the
     Developer's change;
   - the workflow run is on the same head and on the expected workflow path;
   - the jobs list is complete, and the bound job is the one named in the
     check's details URL.
3. Fault localisation over the whole dependency set of the check, not only
   the bound job. A required check job need not run any tests itself. The
   v3 formal review of this note reports that `check (Linux 3.14)` on
   `main` is an aggregate job whose only step fails when a separate shard
   job ran failing tests. This note has not verified it (the workflow files
   are outside the read paths). The rule must therefore hold for any job
   shape:
   - **Job set.** The bound check job plus every job in its `needs`
     closure. The closure comes from a per-check table in `policy.json`
     that is bound to the digest of the workflow file on `main`. It is never
     taken from the run. A check with no table entry refuses. A jobs list
     whose job names do not match the table, or that is incomplete, refuses.
     `_mac_capacity_cancel` already reads all jobs of the workflow run and
     requires a complete list (`total_count` equal to the list length). The
     same read applies here, with full pagination.
   - **Positive test evidence.** The table names the test-executing steps
     of each job in the set. Every one of them must be present in the run,
     with conclusion `success`. `skipped` is allowed only where the table
     records that the workflow skips that step by design (for example a
     step that runs on one matrix leg only). A missing, `cancelled`,
     `failure` or `timed_out` test step refuses, including one cancelled
     because a sibling job failed. If the table names no test-executing step
     for the set, the check is not eligible, and the record refuses. An empty
     list is never vacuously satisfied.
   - **Failure origin on an allow-list.** The table also names gate-only
     steps: steps whose result does not depend on the PR head's content (for
     example runner setup or a cache step). Which steps qualify is UNKNOWN
     until the workflows are read. A step that installs, builds, lints or
     inspects the head is not gate-only, even if it runs no tests. Every
     step in the set that did not conclude `success` (or an allowed
     `skipped`) must be either a gate-only step or an aggregate step. An
     aggregate step is one the table marks as only reading the results of
     other jobs, such as a "require every shard to pass" step. An aggregate
     step's failure is never the origin: it qualifies only when every failed
     job it reads failed in a gate-only step. A failure, timeout or cancel
     anywhere else refuses.

   This excludes executed test failures in any job the check depends on. A
   real failed or hanging test fails or times out a listed test step in some
   job of the set, and the record then refuses. A Developer change that
   breaks a non-test step that reads the head (an install, a syntax check, a
   clean-tree check) is not on the allow-list, so it refuses too.

   The rule has a cost. A gate-only step that fails before a job's test
   steps run (for example runner setup) leaves those test steps skipped or
   missing, so the positive-evidence rule refuses. Only a gate-only failure
   after every test step in the set has passed can qualify. This is
   deliberate: without CI test evidence the tool cannot rule out a Developer
   fault, and the EC2 receipt in item 4 does not replace it. Setup failures
   that GitHub reports as `cancelled` or `startup_failure` already wait
   uncounted (INV-7). Others stay a counted repair or need the Owner
   exception.
4. An exact-head EC2 PASS receipt for the same head (`ran_total` above 0,
   `failures` empty), as independent evidence that the suite passes on that
   head. This is in addition to item 3, not a replacement. EC2 and CI need
   not run the same steps.

A timeout inside a test-executing step, in any job of the set, never
qualifies. The tool reads step conclusions, not logs. From those it cannot
tell a slow-runner limit from a Developer change that made a test hang. That
class stays a counted repair. It is resolved only by a configuration or
classifier fix like #2410, or by the Owner exception. Whether the #2408
timeout was inside a test-executing step is UNKNOWN. If it was, this kind
would not have covered #2408.

The per-check table is new policy data. It must be written from the
workflow files on `main` and reviewed with them, and a change to a workflow
file changes the digest, so the table refuses until it is updated. Until the
table exists, no CI item can qualify, and `CI_GATE_FAULT` refuses every
record.

**`CI_GATE_FAULT` route to a successful exact-head check.** Leaving the
claim out of `test_fails` does not make the required check pass. The head
still has a failed exact-head check, and every merge path still runs
`_merge_checks`. On `main` a new check result can come only from a CI re-run
(forbidden by INV-7) or from a new head. PUBLISH never merges `main` into
the head (the #2367 M1 comment in `task_advance._publish_task`), so a new
head needs a Developer change. That is no route, because the Developer has
nothing to fix.
So the proposal defines one route and makes it an explicit, bounded
exception to INV-7. The Owner must approve it as part of the policy:

- After the record exists, `llmtool task advance` (never a person) may
  request one re-run of the failed jobs of the bound workflow run, through
  its trusted GitHub path. The record binds that re-run: one per record,
  never repeated, and never for a check without a record.
- The new run must be a new completed exact-head run of the same check name.
  It is then routed by the normal `_route_ci_failure` classifier. A
  `success` lets the normal gates continue. Any other result is a normal
  counted repair or ENV wait. No second record may bind the same head and
  check.
- Until the re-run's result exists, `task advance` stops with a named code
  (for example `CI_GATE_FAULT_RERUN_PENDING`) that names the record and the
  check run, not a silent WAITING_ENV.
- Whether a re-run uses the workflow version of the original run or a later
  one is UNKNOWN (the workflow files and their triggers were not read). If
  it uses the original version, a re-run clears only a transient gate fault
  (a lost runner, an infrastructure step). A configuration fault that only a
  later `main` fixes then fails the re-run in the same way. That result is
  routed normally as a counted repair, and no second record may bind it, so
  the bound stays intact. For that case no route exists inside the tool.
  Creating one would need a new step that moves the head onto the fixed
  `main`. That step would change the PUBLISH rule above, needs its own Owner
  decision, and is not part of this proposal.

**`REVIEW_RESULT_LOST` evidence.** A claim state of CLAIMED or UNKNOWN does
not show that the original Reviewer has stopped. A claim is written as
CLAIMED before the Reviewer is started and stays CLAIMED while the dispatch
runs. An UNKNOWN claim can also still hold a native session ID. If the fault
were recorded during an active dispatch, a second Reviewer could start on
the same head, which is the Reviewer shopping that INV-8 forbids. So an
absent verdict alone is never enough. All of the following must hold, and
any doubt refuses:

1. **Positive proof that the original dispatch and its session have
   ended.** The record must bind the claim's dispatch identity and, where
   one exists, its native session ID, and show that the dispatch process is
   no longer running and that the session is closed or cannot return a
   result. Age is not evidence: a claim older than the Reviewer timeout may
   still have a live run. A claim with no stored process or session
   identity gives no such proof. What `review_dispatch` and `review_result`
   store for this (process ID, session ID, start or end marker, heartbeat)
   and whether the native session state can be queried: UNKNOWN (those
   files were not read). Where the stored data cannot give the proof, this
   kind needs new write-ahead and end evidence in the same way as
   `DEBUG_NOT_LAUNCHED` (section 6.4 item 2): `review_dispatch` would
   record a dispatch-ended marker, written under the chain lock after the
   Reviewer process exits, and a claim without that marker stays
   ineligible. Claims written before the marker exists stay ineligible.
2. **A late result is fenced out.** Once the record exists, any later
   result or verdict from the original dispatch for that head and version
   is not accepted as a second result. How `review_result` would enforce
   this is UNKNOWN and is a design point for the follow-up Task.
3. A complete paginated comment read of the PR shows no verdict-shaped
   comment (by `_may_be_review_verdict`) for that head and version. This is
   read again at the moment of recording and again at the redispatch.
4. The other evidence a REVIEW_REDISPATCH needs today stays required.

Until item 1 can be met, `REVIEW_RESULT_LOST` is left out of the mechanism,
and a CLAIMED claim with no ended-dispatch proof stays at
REVIEW_OUTCOME_UNKNOWN. The narrow fix in section 6.4 item 4, which names
the actor and the evidence to collect, still applies.

**Who may invoke it.**

- Only the PM, through one llmtool subcommand. A suggested name is
  `llmtool task gate-fault`; the name is open.
- Every record needs an Owner decision. Under INV-9 the PM records it as a
  relayed Issue comment (`actor=PM`), and the subcommand takes that comment
  URL in the same form as `_SCREEN_REF` / the DELEGATE_HIGH_DEBUG_REF check.
- Developers, Reviewers and AI Debug runs can never invoke it. How the
  subcommand checks that its caller is the PM (for example the Task's
  `pm_provenance` session) is an open design point.
- It performs no Git mutation, no merge and no CI request. The one
  `CI_GATE_FAULT` re-run is requested later by `task advance`, not by this
  subcommand.

**What it binds.** Metadata and digests only, never text:

- the Task ID, the chain key (`lineage_counts.chain_key`) and the Task
  `record_sha256` at recording (a compare-and-update, under the chain lock);
- the kind and the item identity:
  - Debug: entry index, `report_sha256`, `examined_sha`, `d6`,
    `recorded_at`, the status `RESERVED` and the absence of a launch marker;
  - CI: `repair_task_id`, `packet_sha256`, head, check name and check run
    ID, plus the fault evidence: the real conclusion, the workflow run ID,
    the workflow file digest, the digest of the per-check table entry, the
    ID, name and conclusion of every job in the dependency set, the step
    names and conclusions of each of those jobs (names and conclusions
    only, never output), the steps found as the failure origin, and the EC2
    receipt identity;
  - Review: PR, head, version and claim state, the claim's dispatch
    identity and native session ID (where one exists), the dispatch-ended
    evidence, and the comment count of the complete read;
- the llmtool `main` commit that produced the fault;
- the Owner ref, and a required fix-Issue ref for the gate defect;
- a recorded-at time.

**Limits.**

- One record per item. It is created once, using the hard-link pattern of
  `high_debug._create_private`, and a repeat refuses.
- Per chain: at most one record per kind and two in total. These numbers are
  proposals for the Owner and would live in `policy.json` beside the other
  limits.
- Store-wide per kind: a small rolling cap (proposal: 3 per 7 days). After
  it, the kind refuses until the Owner changes the policy, which forces the
  gate defect to be fixed rather than worked around.
- It never changes `repair_max`, `test_fail_max` or the Debug limits. It
  never adds a review PASS, a test PASS or a confirmed RCA, never skips INV-4
  (the new Debug must still be RCA_CONFIRMED), never merges, and never
  applies to a Developer-attributed item: a review FAIL with blocking
  findings, an EC2 FAIL with executed failures, or a CI check with a
  test-executing step that failed, timed out, was cancelled or is missing in
  any job the check depends on.
- Its only exception to INV-7 is the one bound `CI_GATE_FAULT` re-run. That
  exception exists only if the Owner approves it. Without that approval,
  `CI_GATE_FAULT` is left out, because without a re-run it has no route to
  a passing check.
- It never frees a Debug entry that may have launched a provider process.
- It does not replace the #981 screen procedure. A screen refusal keeps its
  own disposition path.
- Any doubt in the binding refuses with a fixed code and writes nothing.

**How it is recorded.**

- An append-only event beside the Task store, like
  `high-debug-screen-history`. The original entry or claim is never edited
  or deleted; the counters consult the event.
- A metadata-only activity comment on the PR or Issue, posted through the
  trusted path that `high_debug._post_record` uses, with the same UNPOSTED
  local fallback.
- Every `task advance` result that relied on a record carries a code (for
  example GATE_FAULT_APPLIED), so the effect is never silent.

**How it is reviewed.**

- The formal Reviewer of the next head receives the head's gate-fault
  records in the review packet and may FAIL a misapplied one.
- The PM's progress report lists every open record with its fix Issue.
- A self-review of a record is not acceptance (common core rule 6).

**Why this is not a permanent Debug or repair-cap bypass.**

- It restores at most the one unit that one bound item used. The limits
  themselves never move, and a chain can use only a fixed number of records.
- It applies only to items with positive evidence that the gate caused
  them: a Debug that write-ahead evidence shows never launched, a CI failure
  whose origin is a listed gate-only step while every test-executing step
  in the check's dependency jobs passed, on a head whose suite also passed
  on EC2, or a review claim whose original dispatch and session are proven ended and
  that has no verdict on a complete read. Matching identifiers
  alone never qualify. RCA outcomes, findings, launched Debugs and executed
  or hanging tests are excluded by precondition.
- Each record needs an Owner decision and names a fix Issue. The store-wide
  cap per kind stops it from becoming a habit, so the lasting fix stays a
  classifier change like #2407.
- It is narrower than today's exit. The Owner merge exception waives the
  formal review for a head. A gate-fault record keeps review, CI and the
  root-cause rule in force and changes only one count or one redispatch
  eligibility.

### 6.3 Recommendation

Do Option A for the three known classes and add Option B as the fallback for
the next unknown class, subject to an Owner decision. Option A alone keeps
the Owner merge exception as the only exit, and that exception is the
broader bypass. Option B alone would turn known defects into repeated manual
records. Until the Owner decides, the existing exception stays as it is.

### 6.4 Narrow fixes that do not need the mechanism

These are follow-up candidates, not part of this Task. Each needs its own
Task, tests and review.

1. Record the real CI conclusion in the CI claim identity and packet,
   instead of forcing `failure` in `task_advance._route_ci_failure`.
   `review_repair._ci_failure` refuses CI_FAILURE_INVALID for any
   conclusion other than `failure`, so it changes too. Do not make
   `timed_out` ENV in `_CI_ENV_CONCLUSIONS` on the conclusion alone,
   because a hanging test also ends `timed_out`. Any ENV rule for timeouts
   needs the dependency-set step evidence of the `CI_GATE_FAULT` kind.
2. In `high_debug.run_high_debug`, write a launch marker into the
   `RESERVED` entry by compare-and-update under the chain lock before the
   runner is called, and refuse the launch if the write fails. Then
   `high_debug._debug_entry_used` can free a `RESERVED` entry with no
   marker, but only after a terminal "abandoned" event has been recorded
   under the same lock, which makes any later launch of that entry refuse.
   Age is not evidence. A `RESERVED` entry older than the Debug run timeout
   (`high_debug._debug_timeout`) may still have run its provider, so it
   stays used. Entries written before the marker exists stay used.
3. In `review_repair.repair_review`, refuse with a distinct code when
   `test_fails` or `review_repairs` is UNKNOWN, so a store doubt is not
   reported as REPAIR_LIMIT_REACHED / TEST_FAIL_LIMIT_REACHED. The delegate
   already separates DELEGATE_REPAIR_COUNT_UNAVAILABLE.
4. Make REVIEW_OUTCOME_UNKNOWN, REPAIR_OUTCOME_UNKNOWN and NEEDS_ATTENTION
   in `task_advance._advance_step` return the actor, the evidence needed and
   the command, as DEVELOPER_START_FAILED does.
5. Replace the single 100-comment page in `_review_verdict_absent`,
   `review_repair._failed_event` and `_review_outcomes` with a complete
   paginated read that has a fixed upper bound.

## 7. Open questions and UNKNOWN values

- The stored `high_debug` entries, repair claims and review claims of the
  Tasks behind PRs #2377, #2406, #2408 and #2414: UNKNOWN.
- The content of the Owner exceptions #1886 c7018124949 and c7029114337, and
  which incident each covered: UNKNOWN beyond the Task statement.
- The exact `repair_policy.admit_debug`, `review_action`,
  `test_failure_action` and `admit_repair` rules, and the Debug limits in
  `policy.json`: UNKNOWN (not read). `repair_max` 5 and `test_fail_max` 5 are
  known from the test file.
- Whether `review_dispatch` / `review_result` offer a claim reconcile
  command: UNKNOWN.
- What `review_dispatch` / `review_result` store that could prove an
  original Reviewer dispatch and its native session have ended (process
  identity, session ID, end marker), and whether a late result can be
  fenced out: UNKNOWN. `REVIEW_RESULT_LOST` depends on this.
- What `owner_exception.verify` checks: UNKNOWN. From `task_advance` it is
  known only that it replaces the review PASS and that `_merge_checks` still
  runs.
- What PR #2410 changed beyond its title: UNKNOWN (workflow files not
  read).
- Whether the fix PRs #2407 and #2410 needed exceptions themselves: UNKNOWN.
- How the gate-fault subcommand would check that its caller is the PM:
  open design point.
- Whether a Debug that launched but returned no result (timeout, crash,
  MODEL_UNVERIFIED, WROTE_FILES) should ever free its slot: Owner policy
  question. This proposal keeps it used.
- Whether the GitHub jobs API reports a step-level timeout as its own step
  conclusion or only as `failure`: UNKNOWN.
- The content of the `CI_GATE_FAULT` per-check table: UNKNOWN (workflow
  files not read). That covers the `needs` closure of each required check,
  which steps in those jobs execute tests, which are aggregate steps, which
  are gate-only, and which test steps a matrix skips by design. The v3
  formal review reports that `check (Linux 3.14)` is an aggregate over a
  separate shard job; this note has not verified it.
- Whether any step of the llmtool workflows is truly gate-only, so that
  `CI_GATE_FAULT` could ever qualify: UNKNOWN. If none is, the kind covers
  no case and should be dropped.
- Whether a requested re-run of failed jobs uses the original workflow
  version, and whether its check runs get new IDs: UNKNOWN.
- Whether the #2408 timeout was inside a test-executing step: UNKNOWN.
- Whether the Owner accepts the one bound re-run as an exception to INV-7:
  Owner decision. Without it, `CI_GATE_FAULT` is dropped from the proposal.
