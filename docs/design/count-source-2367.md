# #2367 count-source design note (P1-2)

Status: design and acceptance work only. This note changes no code and no test. P1 code is not yet authorized.

## Evidence and scope

- #2367 records the count mismatch between the per-D6 delegate repair cap and the
  chain-wide lineage counts. #2122 records that a replacement Task does not
  inherit counts.
- The Owner's 5-repair decision was relayed on #2367: `repair_max` and
  `test_fail_max` are both 5 (PR #2382). `policy.json` is outside this read
  scope, so its exact contents are `UNKNOWN` here; the values are taken from
  the Task evidence.
- Read scope for this note:
  - `docs/design/count-source-2367.md`
  - `tools/llmtool/llmtool/lineage_counts.py`
  - `tools/llmtool/llmtool/repair_policy.py`
  - `tools/llmtool/llmtool/task_submission_cli.py`
- No code reference below uses a line anchor. References name the file and the
  function or constant.

## 1. Count sites in the read scope

The sites are grouped by the three roles named in the acceptance: the delegate
per-D6 repair cap, the repair-limit checks, and the lineage count users. A
fifth group lists adjacent counts that must not be folded into the repair
ledger. The unit column uses runs, claims, families or chain; `none` marks a
boolean gate rather than a count.

### 1.1 Delegate per-D6 repair cap

| Site | What is computed or checked | Unit | Notes |
| --- | --- | --- | --- |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_check_delegate_repair_cap` | Developer runs on one D6, found through `store.completion_d6_task_ids(executor_task)` | runs, per D6 | Skips the current Task, a Task with no Developer, a recorded start failure, a known prelaunch refusal, and a launched run proven to have done no work. EC2 classification comes from repair claims. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_collect_ec2_repair_task_ids` | Adds the `repair_task_id` values of a record's `review_repair_claims` to the new and legacy EC2 sets | claims | A malformed claims list or claim adds nothing, so that run stays on the review count. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_repair_run_stamp` | Assignment timestamp and count eligibility of one Developer run | runs | A malformed developer or assignment time refuses `DELEGATE_REPAIR_COUNT_UNAVAILABLE`. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_launched_run_did_no_work` | Boolean proof that a launched run did no work | runs | Requires no artifact, confirmed cleanup, the recorded source SHA still at the D6 head, a clean tree, and no status path overlapping the write paths. It can accept a launched `DEVELOPER_OUTCOME_UNKNOWN` run, so it does not prove the provider was never reached and must not decide `START_FAILED`. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_check_repair_limits` | EC2 count against `test_fail_max`; review count against `1 + repair_max` | runs, per D6 | Review runs are every counted run except new EC2 repair runs; a legacy EC2 run still counts as a review run. This is the run-based threshold that differs from the claim-based lineage count. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_check_repair_rca` | Uses the count and assignment stamps to require a confirmed root cause from repair #2 on | runs | Not a count source; it consumes the run count and stamps. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_check_repair_packet_rca` | Binds a repair packet to a stored claim or High Debug entry at the failed head | claims | Not a count source; it is a claim consumer. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `run_task_delegate` | Calls `_check_delegate_repair_cap` on each direct route before the route claim | runs | The caller applies the same per-D6 check on all routes. |

### 1.2 Repair-limit checks in `repair_policy.py`

| Site | What is computed or checked | Unit | Notes |
| --- | --- | --- | --- |
| `tools/llmtool/llmtool/repair_policy.py`, `_used` | Coerces a malformed or `UNKNOWN` count to the supplied limit | any caller count | The fail-closed helper: an unknown count is treated as used up. |
| `tools/llmtool/llmtool/repair_policy.py`, `review_fail_count` | Valid review FAILs, at most one per reviewed HEAD | runs, one per reviewed HEAD | This is a review-verdict count, not an effective-repair count. It can change when a HEAD is re-reviewed; it must not be used as the monotonic repair counter. |
| `tools/llmtool/llmtool/repair_policy.py`, `review_action` | Checks `fail_count` against `review_fail_max`, and the child group-debug count against `group_debug_max` | runs, one per reviewed HEAD; debug runs | Decision function; it does not derive the repair count. |
| `tools/llmtool/llmtool/repair_policy.py`, `admit_repair` | Checks caller-supplied `repair_count` against `repair_max` | claims, effective repairs | The caller supplies the count today; there is no single source. |
| `tools/llmtool/llmtool/repair_policy.py`, `test_failure_action` | Checks caller-supplied `test_fail_count` against `test_fail_max` | claims, effective test repairs | The caller supplies the count today; there is no single source. |
| `tools/llmtool/llmtool/repair_policy.py`, `repair_attempts_max` | `max(repair_max, test_fail_max)` for the stored claim attempt range and the run budget bound | limit, not a count | The only intended limit derivation for those two callers. |
| `tools/llmtool/llmtool/repair_policy.py`, `admit_debug` | Parent and group debug counts | debug runs | Adjacent to the repair policy; it is not a repair or test-failure count. |

### 1.3 Lineage and chain count users

| Site | What is computed or checked | Unit | Notes |
| --- | --- | --- | --- |
| `tools/llmtool/llmtool/lineage_counts.py`, `lineage_counts` | `review_repairs`, `review_heads`, `test_fails`, and `chain` over a chain, optionally including successors | claims for review and test counts; runs, one per reviewed HEAD, for `review_heads`; chain for `chain` | Each Task of the chain adds every review-family claim to `review_repairs` and every EC2 claim to `test_fails`. A Task alone in its chain keeps family-local counting. |
| `tools/llmtool/llmtool/lineage_counts.py`, `chain_review_repairs` | Review claims over the chain, including successors, excluding `-ec2` families | claims | The auto review repair limit consumes this value. |
| `tools/llmtool/llmtool/lineage_counts.py`, `chain_debug_used` | Used High Debug entries over the chain | debug runs | Adjacent to the repair count; it is not a repair or test-failure count. |
| `tools/llmtool/llmtool/lineage_counts.py`, `_unknown` | Returns `UNKNOWN` for chain, review repairs, review heads, and test fails | none | The fail-closed sentinel for lineage reads. |
| `tools/llmtool/llmtool/lineage_counts.py`, `chain_records` | The Task, its predecessors, and the link validity | chain, in Tasks | Defines the continuation chain boundary. |
| `tools/llmtool/llmtool/lineage_counts.py`, `successor_records` | The Tasks that continue the Task, and fork/cycle checks | chain, in Tasks | Successors are counted like predecessors when requested. |
| `tools/llmtool/llmtool/lineage_counts.py`, `chain_key` | The oldest Task ID of the chain, used as the chain lock key | chain, one key per chain | A chain in doubt returns the Task's own ID; the caller then sees `UNKNOWN` counts. |
| `tools/llmtool/llmtool/lineage_counts.py`, `store_records` | A bounded snapshot of every stored Task | chain, in Tasks | A read or shape doubt returns `None`. |
| `tools/llmtool/llmtool/lineage_counts.py`, `chain_in` | A Task's chain and key in a snapshot | chain, in Tasks | Detects forks, cycles, and over-long chains. |
| `tools/llmtool/llmtool/lineage_counts.py`, `d6_task_ids` | Tasks whose `completion.executor_task_id` is the D6 | chain, in Tasks | The D6 membership helper used by the delegate cap. |
| `tools/llmtool/llmtool/lineage_counts.py`, `delegate_chain` | The chains of a delegated Task and its D6 Tasks | chain, one record per member plus chain keys | A Task in two chains is `None`. |
| `tools/llmtool/llmtool/lineage_counts.py`, `recorded_result_heads` and `check_continuation_link` | Result heads recorded by a Task and whether a base SHA is a result of a Task outside the chain | chain, in heads | Not a repair count; a lineage gate that consumes chain membership. |

### 1.4 Lineage count users in `task_submission_cli.py`

| Site | What is computed or checked | Unit | Notes |
| --- | --- | --- | --- |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_delegate_chain_view` | Builds `store_records`, `d6_task_ids`, and `delegate_chain` | chain | Returns `None` on any doubt. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_check_chain_repair_count` | Counts runs over every view member except the current Task; EC2 sets come from claims | runs, across the chain | The chain-wide Developer repair cap. It is still run-based, not claim-based. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_delegate_chain_gate` | Recomputes the view under chain locks, applies the chain cap and the continuation-link check, then calls the claim | chain, then runs and claims | Any changed key or read doubt refuses `DELEGATE_REPAIR_COUNT_UNAVAILABLE` before a write. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_start_retry_count` | Earlier start retries from `start_failures` or ALLOWED `START_RETRY` audit entries | runs, start retry attempts | Adjacent cap. A start retry is not a repair attempt and must not enter the repair ledger. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `run_task_retry_start` | Checks the start-retry cap and then the delegate repair cap | runs, start retry attempts then repair runs | Consumer of both counts. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `_completion_repair_artifact_segments` | Requires each claim family to hold attempts 1 through n in stored order | families, claims per family | It audits the artifact chain; it is not a cap on effective repairs. |
| `tools/llmtool/llmtool/task_submission_cli.py`, `run_task_complete` | `TEST_FAILED` is a boolean missing-gate entry, not a count | none | Listed so it is not mistaken for a test-failure counter. |

### 1.5 Adjacent counts kept separate

These sites compute or check a count, but not a repair or test-failure count.
They must not be folded into the repair ledger.

| Site | What is computed or checked | Unit | Notes |
| --- | --- | --- | --- |
| `tools/llmtool/llmtool/repair_policy.py`, `admit_debug` and `tools/llmtool/llmtool/lineage_counts.py`, `chain_debug_used` | Parent and group debug use | debug runs | Separate from the repair budgets. |
| `tools/llmtool/llmtool/repair_policy.py`, `admit_split` | Child count against `child_max` | families, split children | Separate lineage structure limit. |
| `tools/llmtool/llmtool/repair_policy.py`, `admit_replan` | REPLAN use against `replan_max` | families, one REPLAN per lineage | Separate lineage structure limit. |
| `tools/llmtool/llmtool/repair_policy.py`, `review_fail_count` | Review FAIL heads | runs, one per reviewed HEAD | Review-verdict count, not a monotonic repair count. |

## 2. How the counts differ today

1. **Unit.** The per-D6 cap counts Developer runs. The chain helpers and
   `repair_policy.admit_repair`/`test_failure_action` count repair claims, but
   the policy functions receive the count from their caller, so no single
   source exists. A run that never wrote a repair claim and a claim whose
   repair run is no longer countable can therefore produce different answers.
2. **Threshold.** `_check_repair_limits` compares the review run count with
   `1 + repair_max`, because the original run is included in the run count.
   `admit_repair` compares a repair count with `repair_max`. The same policy
   value is applied to different offsets.
3. **Test repairs.** `_check_repair_limits` counts runs whose Task is in the
   EC2 repair-Task sets. `lineage_counts` counts every EC2 claim directly.
   `test_failure_action` then compares a third, caller-supplied value with
   `test_fail_max`.
4. **Scope.** `_check_delegate_repair_cap` finds Tasks by
   `completion.executor_task_id` on one D6. The chain helpers follow
   `continues` links and D6 membership. Neither follows `replaces` or a
   parent link for a replacement Task, and `_start_retry_count` is per Task.
   A replacement Task can therefore start at zero.
5. **Unknowns.** `lineage_counts` returns `UNKNOWN` on a doubtful chain.
   `repair_policy._used` turns that into the limit. The per-D6 run scan has
   its own skips and refusals, so a doubtful run is not consistently treated
   as used.

## 3. Proposed single count source

### 3.1 One ledger, one resolver

The single count source is an append-only **work-item repair ledger**. The
ledger is data; the only code that may read it for a policy count is one
resolver in `tools/llmtool/llmtool/lineage_counts.py`, proposed as
`work_item_repair_counts(store, work_item)`. `repair_policy.py` remains the only
source of limits and decisions. No caller keeps a parallel tally.

A ledger entry describes one repair attempt or its pre-call reservation, not a
Developer run and not a review verdict:

- `work_item`: the work-item key the 5-repair policy applies to.
- `seq`: the next per-work-item, per-kind sequence number.
- `kind`: `review` or `test`.
- `state`: `RESERVED`, `DISPATCHED`, `COMPLETED`, `FAILED`, `START_FAILED`, or
  `UNKNOWN`.
- `task_id` and `executor_task`: where the attempt ran.
- `claim_ref`, `head_sha`, and `family`: the claim binding, when one exists.
- `recorded_at`: the append time.

The initial Developer run of a work item is not an entry, and a start retry is
not a charged entry (`tools/llmtool/llmtool/task_submission_cli.py` keeps that
cap in `_start_retry_count`). Every provider call of an effective repair
attempt first appends one `RESERVED` entry. That
entry is **charged** once the launch is confirmed, or once reconciliation
cannot prove the call never started. It is never charged when the existing
start-failure proof shows the provider was not reached: the reservation then
moves to `START_FAILED`, which is terminal. A recorded start failure is
therefore still a ledger entry, but not a counted repair. An attempt that is
not provably a start failure is charged; the ledger is conservative.

The only proof for `START_FAILED` is proof that the provider was never
reached: a recorded start failure through
`tools/llmtool/llmtool/task_submission_cli.py`, `_recorded_start_failure` (the
record-only `start_failure.record_checks` proof), or a known prelaunch refusal
as matched in `_repair_run_stamp`. It is not `_launched_run_did_no_work`: that
helper can accept a launched `DEVELOPER_OUTCOME_UNKNOWN` run from its record
and an unchanged D6 tree, so it does not prove the provider was never
reached. A launched run with an `UNKNOWN` outcome stays charged as `UNKNOWN`,
even when it left no work. Any weaker or missing evidence leaves the
reservation counted.

### 3.2 Counts and thresholds

- `review_repairs` is the number of charged `review` entries in the work-item
  ledger: entries in `DISPATCHED`, `COMPLETED`, `FAILED` or `UNKNOWN`, and no
  `START_FAILED` entry.
- `test_fails` is the number of charged `test` entries in the work-item ledger.
- Both are compared directly with `repair_max` and `test_fail_max`. The
  `1 + repair_max` offset disappears because the original run is not an entry.
- A `RESERVED` entry is not yet charged, but it is counted as used for
  admission so that two concurrent dispatches cannot both spend the last
  repair: admission compares charged entries plus in-flight reservations with
  the limit. Releasing a reservation to `START_FAILED` frees only that
  in-flight slot and does not reduce the charged count. A reservation that
  reconciliation can neither confirm nor disprove is charged as `UNKNOWN`.
- The per-D6 check and the chain check are two **views** over the same
  entries, not two counters. The work-item view is the policy boundary and
  carries inheritance. The D6 view is a lookup over entries that ran on that
  D6. A dispatch is refused if either view is at or over its limit; the D6
  view must not give a work item a fresh budget when it moves to another D6.
- `review_fail_count` stays a separate review-verdict count. It decides
  whether a FAIL is repaired or escalated; it is not the monotonic repair
  counter.

### 3.3 Invariants

1. **Monotonic.** Entries are appended, never removed or renumbered, and the
   charged count (entries in `DISPATCHED`, `COMPLETED`, `FAILED` or `UNKNOWN`)
   never decreases. A `RESERVED` entry becomes charged when the launch is
   confirmed or the outcome is `UNKNOWN`; a `START_FAILED` entry can never
   become charged later. The entry count, the sequence numbers and the charged
   count are all monotone. An in-flight reservation is outside the charged
   count; it is counted as used for admission only and is never a recorded
   repair.
2. **UNKNOWN is used.** A missing, malformed, unreadable, cyclic, forked, or
   ambiguous work-item link or ledger makes the affected count `UNKNOWN`.
   `repair_policy._used` maps it to the limit, so the dispatch refuses. A
   reservation that reconciliation can neither confirm nor disprove is also
   charged as `UNKNOWN`. A count is never zero by doubt.
3. **One attempt, one entry.** Exactly one `RESERVED` entry is appended before
   the provider call of each effective repair attempt. A repair claim binds to
   exactly one charged entry; a `START_FAILED` entry, for a call that never
   reached the provider, carries no claim. A duplicate claim, a claim with no
   charged entry, a charged entry with two claims, or a claim bound to a
   `START_FAILED` entry is `UNKNOWN`.
4. **Same source everywhere.** `_check_delegate_repair_cap`,
   `_check_chain_repair_count` and `_delegate_chain_gate` in
   `tools/llmtool/llmtool/task_submission_cli.py`, and the policy decisions,
   all read the same resolver. There is no run tally, claim tally, or per-D6
   tally that can disagree with it.
5. **No incidental counts.** Start retries, debug counts, split child counts,
   replan counts, and review FAIL heads remain separate counts with their own
   limits. They must not be written into, or read from, the repair ledger.

### 3.4 Replacement and continuation inheritance

The ledger is keyed by the **work item**, not by the Task ID or the D6.

- A continuation Task created with `continues` resolves to the same work-item
  key as its predecessor. It reads the same ledger; nothing is copied and
  nothing resets.
- A replacement Task created with `replaces` (for example a REPLAN) resolves
  to a work item that inherits the replaced work items' ledger. The inherited
  counter per kind is the highest known value among the replaced work items;
  a replacement never starts below a predecessor that used more repairs. If
  any replaced work item is `UNKNOWN`, the inherited value is `UNKNOWN`.
- A split child remains its own work item, as the current policy docstring
  says. It starts at zero only when it neither continues nor replaces another
  work item. Sharing a D6 with a sibling is not an inheritance link.
- The `replaces` link is checked in the same locked pass as the `continues`
  link. A missing, conflicting, or ambiguous link is `UNKNOWN`, not a fresh
  budget.
- A replacement Task that moves to a different D6 carries the work-item
  ledger with it. The D6 view does not reset the work-item view.

Open question for the PM/Owner: when one replacement Task replaces several
work items, should the inherited value be the maximum (proposed) or the sum?
The maximum keeps one 5-repair budget per work item; the sum would treat the
replacement as carrying several budgets at once. The note proposes the maximum
and marks the final choice as a policy decision.

## 4. Files a later code PR would change

All changes below are design targets only. No code is changed by this note.

| File | Expected change | Scope |
| --- | --- | --- |
| `tools/llmtool/llmtool/lineage_counts.py` | Add the work-item ledger resolver and the work-item closure, and make the existing chain counts views over it. Keep the fail-closed chain and lock helpers. | Read scope. |
| `tools/llmtool/llmtool/repair_policy.py` | Keep `repair_max`, `test_fail_max`, `review_fail_max`, and the decision functions. Remove the need for caller-supplied repair counts, and retire `repair_attempts_max` if the ledger makes it redundant. | Read scope. |
| `tools/llmtool/llmtool/task_submission_cli.py` | Replace the counting in `_check_delegate_repair_cap`, `_check_repair_limits`, `_check_chain_repair_count`, `_delegate_chain_gate`, `_repair_run_stamp`, and `_collect_ec2_repair_task_ids` with the resolver. Append the `RESERVED` entry before the provider call, charge it on a confirmed launch or `UNKNOWN`, and bind only the record-only start-failure proof (`_recorded_start_failure` or a known prelaunch refusal) to `START_FAILED`. Never use `_launched_run_did_no_work` for `START_FAILED`, because it can accept a launched `DEVELOPER_OUTCOME_UNKNOWN` run. | Read scope. |
| `tools/llmtool/llmtool/review_repair.py` | Bind review and EC2 claims to ledger entries and stop supplying an independent repair count. | UNKNOWN: outside the read scope. |
| `tools/llmtool/llmtool/task_store.py` | Persist and validate the ledger and the `continues`/`replaces` work-item links. | UNKNOWN: outside the read scope. |
| `tools/llmtool/llmtool/task_advance.py` | Move the test-failure count named in the `lineage_counts` docstring onto the resolver. | UNKNOWN: outside the read scope. |
| `tools/llmtool/llmtool/remote_test.py` | Record or bind EC2/CI test-failure repairs to ledger entries if it is a count caller. | UNKNOWN: outside the read scope. |
| Tests under `tools/llmtool/tests/` | Add coverage for monotonicity, `UNKNOWN` as used, per-D6 and chain views agreeing, continuation inheritance, replacement inheritance, and the max-versus-sum decision. Exact test paths are not in the read scope. | UNKNOWN: outside the read scope. |

If the later PR needs a file not listed here, that file is `UNKNOWN` until the
PM adds it to a read scope. No change to `policy.json` is proposed: the Task
evidence says both limits stay 5 (PR #2382), and its exact contents remain
`UNKNOWN` because it is outside the read scope.

## 5. Acceptance check

- A1: section 1 lists the delegate per-D6 cap, the repair-limit checks, the
  lineage count users, and the adjacent counts, each with file and function
  and with its unit.
- A2: section 3 proposes one append-only work-item ledger and one resolver,
  with monotonic and `UNKNOWN`-as-used invariants, direct comparison to the
  5-repair limits, and replacement inheritance by work-item key.
- A3: section 4 lists the files a later code PR would change; files outside
  the read scope are marked `UNKNOWN`.
- A4: this note changes no code and no test, writes only this file, and uses
  file-plus-function references with no line anchors.

## 6. Uncertainty

- The exact persistence location of the ledger (a Task record field, a store
  index, or both) is `UNKNOWN` until `task_store.py` is in a read scope.
- The call sites in `review_repair.py`, `task_store.py`, `task_advance.py`, and
  `remote_test.py` are `UNKNOWN` for the same reason.
- The max-versus-sum inheritance rule for a multi-work-item replacement is a
  policy choice for the PM/Owner; this note proposes max.
- Whether a separate D6-level safety ceiling beyond the work-item policy is
  still wanted is `UNKNOWN`. If it is, it should be a separately named policy
  limit, not a second interpretation of `repair_max`.
