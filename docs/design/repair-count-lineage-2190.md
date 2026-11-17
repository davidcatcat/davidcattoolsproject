# #2190 Repair-count lineage: design

Status: design only (Owner A, #1502 WP-D). No code or test is changed by this Task. Source read:
`62186a801c811940cdcc53f4e81a8d58cc643100`. Read: `docs/design/start-failure-retry-2188.md`,
`lineage_counts.py`, `repair_policy.py`, `review_repair.py`, `task_advance.py`, `task_store.py`,
`task_submission_cli.py`, `policy.json`, `tests/test_lineage_counts.py`.

## 1. Goal and non-goals

**Goal:** the repair-cap and test-failure-cap counts are one count per root lineage, summed over
every family key, PR, D6 and Developer route. The family string is a label on a claim, never a
count boundary for a cap.

**Non-goals / unchanged:**

- Caps do not move: `repair_max` is 2, `test_fail_max` is 2, and the Developer run cap is
  `1 + repair_max` (3) review runs or `test_fail_max` (2) test-failure runs per root lineage
  (`tools/llmtool/policy.json:3`, `:8`; `task_submission_cli.py:7599`, `:7606`).
- `UNKNOWN` still means the limit (`repair_policy.py:16`, `:67`-`:69`).
- No sensitive-screen, gate, lock, fork or no-work check is relaxed.
- The #2188 start-failure definition is not widened (`docs/design/start-failure-retry-2188.md:39`-`:60`).
- The D1 RCA requirement and the review-FAIL escalation count keep today's scope: family-local for
  a Task alone and chain-wide for a chain (`review_repair.py:550`-`:594`;
  `lineage_counts.py:210`-`:242`). They are review-evidence rules, not the repair or test-failure
  cap, and the named single-Task test pins this.

## 2. Today's two gates

### 2.1 Delegate run cap: per D6, then per chain

`_check_delegate_repair_cap` runs before each route dispatch (`task_submission_cli.py:7912`,
`:7926`, `:7935`, `:7941`, `:7948`, `:7951`, `:7982`, `:8040`; `retry-start` at `:8388`).

- It reads the Tasks whose completion names this D6 (`store.completion_d6_task_ids(executor_task)`,
  `:7483`). Every other Task on that D6 is stamped by `_repair_run_stamp` (`:7493`-`:7513`).
- `_repair_run_stamp` skips a missing Developer, a known pre-launch refusal, and a launched run
  that `_launched_run_did_no_work` proves did nothing (`:7545`-`:7588`; skip rules `:7557`-`:7579`).
  Otherwise it counts the run once by its `created_at` (`:7580`-`:7588`).
- `_launched_run_did_no_work` requires a blocked/interrupted outcome, no artifact, empty
  `changed_files`, `execution.cleanup_status == 'CONFIRMED'`, and
  `d6_unchanged(source_sha, write_paths=...)` (`:7274`-`:7327`; the probe call is `:7325`).
- `_d6_unchanged_probe` evaluates the live D6 once, lazily, per gate call, and is not stored
  (`:7518`-`:7542`; `head == source_sha` and a clean tree at `:7536`-`:7540`).
- `_check_repair_limits` applies the run limits (`:7591`-`:7611`): an EC2 repair Task takes
  `test_fail_max` (`:7594`-`:7600`); every other run takes `1 + repair_max` (`:7602`-`:7607`);
  from the second counted run onward, repair #2's RCA rule also runs (`:7608`-`:7611`).

`_delegate_chain_gate` adds the chain gate at the route claim sites (`:7789`-`:7834`; its order is
described at `:7790`-`:7798`; call sites include gw-agent `:6320`, codex direct `:6864`, background
`:7915`, codex text `:8042`). It:

1. builds a view of the delegated Task's chain and the chains of its D6 Tasks
   (`_delegate_chain_view`, `:7746`-`:7759`; `lineage_counts.store_records`, `d6_task_ids`,
   `delegate_chain`, `lineage_counts.py:464`-`:598`);
2. takes every chain lock in sorted order, then rebuilds the view on fresh reads (`:7808`-`:7820`);
3. counts every view member except the Task being delegated (`_check_chain_repair_count`,
   `:7762`-`:7786`) and applies the same `_check_repair_limits` (`:7785`);
4. refuses a base SHA that is a result head outside the chain (`:7824`-`:7825`), then writes the
   claim under the locks (`:7826`).

The chain counter evaluates `d6_unchanged` only for runs whose completion names the current D6;
a run on another D6 is passed a probe that always says "changed" (`:7776`-`:7780`). So a proven
no-work run on another D6 still counts, and the proof is re-derived later rather than recorded.

### 2.2 Claim gate: review_repair, lineage_counts, store, advance

`review_repair.repair_review` takes `family=None` and `auto` (`review_repair.py:645`-`:649`).

- `base_family` defaults to `pr-<number>`; a supplied family must match
  `^[a-z0-9._-]{1,64}$` and must not end in `-ec2` (`:715`-`:719`). The stored family is
  `base_family` for a review repair and `base_family + '-ec2'` for a remote-test or CI repair
  (`:720`-`:725`).
- The `--family` CLI option is the caller-facing way to supply that `family` argument. Its parser
  is outside this Task's read paths, so its exact line is UNKNOWN here; the validation and use
  inside `review_repair.py` are the lines above.
- The Task's own stored claims are filtered by family, with legacy EC2 claims added to the test
  count (`:749`-`:762`). `attempt` is numbered within the stored family (`:852`-`:853`), and an
  identity is `(pr, head_sha, version, attempt)` (`:854`-`:860`).
- Under the chain lock, `lineage_counts.lineage_counts(..., base_family=base_family,
  include_successors=True)` supplies the counts (`:814`-`:837`). Review repairs go through
  `_review_gate` (`:520`-`:594`, call at `:841`); test repairs compare `counts['test_fails']` with
  `test_fail_max` (`:848`-`:851`).
- The claim and its CAS are written only after the count and the repair Task record (`:862`-`:881`,
  `:883`-`:916`). An auto review replay is admitted only when the whole chain holds exactly one
  review claim (`_auto_replay_claim`, `:600`-`:642`; the chain counter is
  `lineage_counts.chain_review_repairs`, `lineage_counts.py:245`-`:276`).

`lineage_counts.lineage_counts` is family-local only for a Task alone in its chain
(`local = len(records) == 1`, `lineage_counts.py:210`-`:212`): the review count is only
`base_family`, and tests are only `base_family + '-ec2'` plus legacy EC2 claims in `base_family`
(`:221`-`:224`). In a chain it counts every non-`-ec2` family as review and every `-ec2` (plus
legacy EC2) as a test (`:228`-`:229`). A missing link, a fork or a chain over `MAX_CHAIN` returns
all `UNKNOWN` (`:167`-`:169`, `:199`-`:208`).

`task_store` also limits one Task record's claims: at most 3 claims per stored family, with attempts
`1..n` in stored order (`task_store.py:2350`-`:2397`; per-family limit `:2392`-`:2397`; identity
uniqueness `:2386`-`:2389`). This is a record-shape guard, not the policy count; it is per Task,
not per lineage.

`task_advance._test_fail_count` delegates to the same `lineage_counts` call with
`include_successors=True` (`task_advance.py:830`-`:840`), and `_test_family_used_up` treats its
`UNKNOWN`/limit result as used up (`:851`-`:864`), which the advance path reports as `STOPPED`.
Advance and `review_repair` therefore agree for a chain, but both inherit the single-Task
family-local case.

## 3. Where the current code differs from the one-count model

These are actual behaviours, recorded rather than treated as a spec mismatch:

1. **The run gate is keyed by D6 first.** `_check_delegate_repair_cap` only sees Tasks whose
   completion names that D6 (`task_submission_cli.py:7483`), so a Task whose completion names
   another D6 is absent from this gate; the chain gate covers only Tasks reachable through
   `continues` and the current D6's Tasks (`:7746`-`:7786`).
2. **The run gate has no family/PR dimension at all.** It separates EC2 repair Tasks from review
   runs (`:7594`-`:7607`); family is not read. The family boundaries exist in the claim gate.
3. **A no-work proof is not recorded.** It is recomputed from the live D6 at gate time
   (`:7518`-`:7542`), and is deliberately false for a run on another D6 (`:7779`-`:7780`). A later
   D6 head move therefore makes an unchanged run count, and re-running the gate can change the
   count without any new run.
4. **A Task alone in its chain is family-local.** `lineage_counts.py:210`-`:212` and `:221`-`:229`
   keep another PR family out of the manual review count and out of `_test_fail_count`; the same
   Task in a chain counts every family. `chain_review_repairs` already counts every family
   (`:245`-`:276`), so auto replay and manual repair disagree for a single Task.
5. **The store's per-family cap is per Task record.** `task_store.py:2350`-`:2397` caps each
   record's family at 3 and numbers attempts per family; it does not sum across lineage Tasks.
6. **A REPLAN or child cannot be continued.** `check_continues` refuses a predecessor whose
   lineage kind is `child` or `replan` with `CONTINUES_LINEAGE_CONFLICT`
   (`lineage_counts.py:325`-`:327`), and refuses any predecessor that already has a successor
   (`:330`-`:343`). One REPLAN per lineage is the policy (`repair_policy.py:162`-`:171`;
   `docs/design/start-failure-retry-2188.md:26`-`:28`). A REPLAN that fails before doing work has
   no link by which a successor can inherit its counts.
7. **`CLAUDE_STATUS_FAILED` is not a #2188 start failure.** The #2188 design excludes it because
   it can be raised after a spawn (`docs/design/start-failure-retry-2188.md:56`). `retry-start`
   therefore refuses it through `start_failure.record_checks` (`task_submission_cli.py:8350`-`:8352`)
   and again through `start_failure.classify` under the CAS (`:8303`), even when the run did no work.
8. **A generated repair Task has no chain link.** `_record_repair_task` creates it through
   `record-pm` without `continues` (`review_repair.py:269`-`:295`). Its only association is the
   owning Task's `review_repair_claims[].repair_task_id` (`:861`-`:866`), so today it is a
   different chain from the Task that spent the claim.
9. **The claim gate locks a chain key, not a root.** Admission locks
   `lineage_counts.chain_key` (`review_repair.py:814`-`:837`), which follows only `continues`
   (`lineage_counts.py:150`-`:164`). Two Tasks joined only by `lineage_inherits` would take
   different locks while spending the same count.
10. **An in-place rerun needs a new dispatch authorization.** The text-codex route refuses any
    existing developer (`task_submission_cli.py:7959`-`:7965`); `retry-start` gets past that only
    for the Claude route through its `expected_failed_developer` hook (`:8390`-`:8392`). A
    route-agnostic rerun therefore needs its own authorized dispatch action, not a bypass.

## 4. Design

### 4.1 One root lineage key

Add a pure `lineage_ledger.py` (new module) and a write-once store link:

- `root_of(records, task_id)` returns one root ID by walking every parent edge of a Task:
  `continues` (one predecessor), `lineage_inherits.root_task_id` (zero or one) and, for a legacy
  generated repair Task, every `review_repair_claims[].repair_task_id` on another Task that names
  it. All edges of one Task must lead to the same root. A cycle, a self link, a Task named by two
  different roots, a conflicting `lineage_inherits`, a root with more than
  `MAX_DELEGATE_MEMBERS` (32) Tasks, or a read doubt returns `None`; the caller then uses
  `UNKNOWN`. Several inherited children of one root (for example two repair Tasks of one Task) are
  siblings, not a fork: only `continues` keeps the one-successor rule (`lineage_counts.py:107`-
  `:151`, `:300`-`:343`).
- `lineage_inherits` is a write-once top-level Task field, e.g.
  `{root_task_id, kind: 'REPLAN'|'ROOT'|'REPAIR', recorded_at, pm_session_id}`. `task record-pm`
  writes it under the same continues lock as `check_continues` (`task_submission_cli.py:8502`-
  `:8510`), and it joins the contract digest as `continues` does (contract-digest tests at
  `tests/test_lineage_counts.py:604`-`:634`). A generated repair Task is created by
  `review_repair._record_repair_task` through `record-pm` without `continues`
  (`review_repair.py:269`-`:295`); that call passes `root_task_id` and kind `REPAIR`, so the new
  Task joins the root at its creation site. The claim already stores `repair_task_id`
  (`review_repair.py:861`-`:866`); for legacy repair Tasks with no `lineage_inherits`, that claim
  edge is the parent link and no record is rewritten.
- A REPLAN, new ROOT or generated REPAIR Task that inherits counts must carry this link (or, for a
  legacy repair Task, be named by the claim). Without it, a REPLAN/new ROOT is a new independent
  lineage and inherits nothing. `admit_replan` and its one-REPLAN rule are unchanged
  (`repair_policy.py:162`-`:171`).

### 4.2 One ledger per root, derived from records

Add an append-only `developer_runs` list to each Task (bounded; each entry records
`assignment_id, task_id, d6_task_id, route, source_sha, state, error_code, no_work_proof,
counted, recorded_at`). The current `developer` slot stays the latest run for compatibility; a
legacy Task with no `developer_runs` contributes its single `developer` once.

`lineage_ledger.counts(records, root)` sums, exactly once over every Task with that root:

- `review_repairs`: every claim whose family does not end in `-ec2` (legacy EC2 claims in a base
  family included), on any PR and in any family;
- `test_fails`: every `-ec2` claim plus every legacy EC2 claim (`comment_id is None` in the base
  family; `review_repair.py:499`-`:509`), on any PR and in any family;
- `runs`: every `developer_runs` entry for the root, on every D6 and every route, with `counted`
  true. A `no_work_proof` that is valid and says "unchanged at the run's own D6" sets
  `counted=false`; anything absent, malformed or `UNKNOWN` counts.
- `review_heads`: one entry per failed HEAD, as `lineage_counts.py:230`-`:241` does now. This is
  reported for evidence; D1 and the review-FAIL escalation keep the scope in §4.4.

A Task that appears twice is counted once. A conflicting root (the `continues`,
`lineage_inherits` and claim edges of one Task imply different roots), a cycle or a read doubt
returns `UNKNOWN` for every value, never `0` (`repair_policy.py:16`). The root's members include
generated repair Tasks reached only through a claim edge. The existing `chain_review_repairs` and
`lineage_counts` chain path remain the fallback for records without the new fields; the new gate
uses `lineage_ledger` only when it can build the whole root, otherwise it refuses
`DELEGATE_REPAIR_COUNT_UNAVAILABLE` / `REPAIR_COUNT_UNAVAILABLE`.

### 4.3 The no-work proof is evaluated once and recorded

At the end of a launched run, on that run's own D6, evaluate the existing proof
(`_launched_run_did_no_work`, `task_submission_cli.py:7274`-`:7327`, with the lazy probe
`:7518`-`:7542`) and persist it in the same append that records the run:

```
no_work_proof = {source_sha, d6_task_id, d6_head, tree_clean, write_paths_untouched,
                 checked_at, evidence_sha256}
```

`evidence_sha256` covers the proof without itself. The store accepts the proof only in the append
that adds the matching run, and never changes or removes it. Gate counting reads the stored proof;
it never re-runs `d6_unchanged` against the current tree. Therefore:

- a D6 that later moves, is reused, or is replaced neither resets nor inflates the count;
- a run on another D6 is judged by its own recorded proof, not by the counting D6;
- a forged or missing proof cannot be read as "no work": doubt counts.

For records written before this field, the first gate running on that run's own D6 evaluates the
proof once and writes a `run_proofs` entry keyed by `assignment_id` and the developer
`record_sha256`, under the root lock (§4.1). If no gate has run on the run's own D6, the run counts
(doubt). Once written a proof is not recomputed.

### 4.4 Caps and UNKNOWN

The delegate gates call the same ledger and then `_check_repair_limits`; the claim gate applies
the same policy numbers:

- review runs: refuse at `>= 1 + repair_max` (`task_submission_cli.py:7606`);
- EC2/CI repair runs: refuse at `>= test_fail_max` (`:7599`);
- review claims: `repair_max` repairs (the existing escalation at a new valid FAIL is unchanged,
  `review_repair.py:556`-`:588`);
- test claims: refuse at `>= test_fail_max` (`repair_policy.py:139`-`:142`);
- `UNKNOWN` is substituted by the limit (`repair_policy.py:67`-`:69`).

The run cap is a count of runs, not Tasks: the original run plus two repairs is three. The claim
cap is a count of repairs, not the original run. This distinction is unchanged.

The cap counts come from `lineage_ledger`. The D1 RCA requirement and the review-FAIL escalation
count keep today's scope: for a Task alone they stay family-local, and for a chain they use the
chain count (`review_repair.py:550`-`:594`; `lineage_counts.py:210`-`:242`). `_review_gate`
therefore takes the lineage `review_repairs` for `spent` and the existing `lineage_counts`
`review_repairs`/`review_heads` for `review_action` and `admit_repair`. That is a review-evidence
rule, not a cap, and the named single-Task test pins it. No cap is loosened.

### 4.5 Family is a label only

Family is a label for the cap. Claim `family` stays as stored, because old records and the
per-family RCA binding use it (`review_repair.py:569`-`:594`; `task_store.py:2398`-`:2426`). The
policy cap count comes only from `lineage_ledger`; no cap filters it by `base_family`. The D1 RCA
binding and the store's per-family 3-claim guard still use family (`task_store.py:2392`-`:2397`),
so that guard can only refuse earlier, never admit more. The claim's `attempt` remains a per-family
sequence for store compatibility, while admission uses the lineage count, so a new family starts
at attempt 1 without resetting the root's cap count. The existing identity check
`(pr, head_sha, version, attempt)` stays (`review_repair.py:854`-`:860`), so a new family cannot
reuse an existing identity; that refusal is tighter, not a bypass. This is why
`test_a_single_task_chain_counts_review_claims_of_every_pr_family` keeps passing unchanged: its
manual case still uses the family-local D1 path, while the cap is now lineage-wide.

### 4.6 Rerunning a REPLAN whose route failed with no work

Today such a Task is stuck: `CONTINUES_LINEAGE_CONFLICT` (`lineage_counts.py:325`-`:327`), one
REPLAN per lineage (`repair_policy.py:162`-`:171`), and `CLAUDE_STATUS_FAILED` is not a #2188 start
failure (`docs/design/start-failure-retry-2188.md:56`).

Add `task retry-run TASK` (or extend `retry-start` with a distinct mode) that:

1. requires the latest `developer_runs` entry to be a terminal route failure with a recorded valid
   `no_work_proof` on the run's own D6; `CLAUDE_STATUS_FAILED` is accepted only through this proof,
   never by widening #2188;
2. refuses if there is an artifact, a claim, a PR, a merge attempt, or a D6 head/path mismatch;
3. appends a new run (new assignment, same Task, same `lineage_inherits` root, same D6 scope) and
   does **not** create a Task, a `continues` link, a REPLAN, or a claim;
4. counts the failed run as `counted=false` and the new run normally. If the retry also does no
   work, it is again `counted=false`; a per-Task retry bound (2, matching #2188) sends the third
   failure to the PM.
5. uses the normal dispatch authorization, not a bypass: `pm_caller.check_caller` (`:8344`-
   `:8347`), the Owner policy `worker_dispatch.evaluate` (`:8360`-`:8362`), a fresh quota check
   (`:8363`-`:8368`), `_check_delegate_repair_cap` (`:8388`) and `_with_quota_observation`
   (`:8389`). The route launcher receives the `expected_failed_developer` hook that `retry-start`
   already passes (`:8390`-`:8392`), so it accepts exactly the failed run and still refuses any
   other existing developer. This needs a distinct dispatch action (e.g. `RUN_RETRY`) in
   `worker_dispatch.authorize_dispatch` and its `dispatch_audit` (`:8090`-`:8096` shows the
   `'DISPATCH'` action), because the text-codex route otherwise refuses any existing developer
   (`:7959`-`:7965`). #2188 `start_failure` is not used; the recorded no-work proof is the only
   admission.

This reruns in place without resetting or bypassing any count, and the root lineage still sees
every earlier counted run and claim. A REPLAN with no recorded `lineage_inherits` cannot prove
inheritance; `retry-run` refuses it rather than silently treating it as a fresh root, and the PM
records the link or starts a genuinely new Task with its own counts.

## 5. Ordered PRs

Each PR is small, built from `main`, and keeps the caps above. Tests follow Owner #763 A: no
credential-shaped strings; route/probe outcomes are injected; no production check is weakened.

1. **Pure ledger and link model (not wired).**
   - Files: new `tools/llmtool/llmtool/lineage_ledger.py`; `task_store.py` (new optional
     `lineage_inherits`, `developer_runs`, `run_proofs` fields, write-once validation).
   - Tests: new `tests/test_lineage_ledger.py`: root walk with `continues`, with
     `lineage_inherits`, with a legacy claim edge, and mixed; a generated repair Task with no
     `continues` joins its claim's root; two sibling repair Tasks share one root and are not a
     fork; conflicting roots, a cycle, a fork on `continues`, a malformed link and a root over the
     member bound -> `UNKNOWN`; every family/PR is counted; legacy records fall back to one run; a
     no-work proof counts zero and an invalid proof counts one; caps are unchanged; a second Task
     in the root is not double-counted.
2. **Record runs and no-work proofs; wire the run gates.**
   - Files: `task_submission_cli.py` (`_check_delegate_repair_cap` reads the delegated Task's root
     from `lineage_ledger` instead of only `completion_d6_task_ids`, so a run on another D6 is
     still in the count; `_repair_run_stamp`, `_check_chain_repair_count`, `_delegate_chain_gate`,
     run-end append); `task_store.py`.
   - Tests: new `tests/test_lineage_run_gate.py`: a run on D6 A counts once on D6 B; a recorded
     no-work proof stays zero after the D6 moves; a later counted run makes exactly one more; a
     missing/invalid/`UNKNOWN` proof counts; two Tasks joined only by `lineage_inherits` take one
     root lock (not two chain keys); a generated repair Task on D6 B still counts the root's run
     on D6 A; the existing root-lock refusals still return
     `DELEGATE_REPAIR_COUNT_UNAVAILABLE`; a same-D6 run is still counted as before.
3. **Wire the claim gates and advance.**
   - Files: `review_repair.py` (`repair_review` cap count from `lineage_ledger`; the admission
     lock is the root key from `root_of`, and membership and counts are re-read and re-validated
     inside that lock before the claim CAS; `_review_gate` keeps its D1/head path;
     `_auto_replay_claim` takes the same root key); `lineage_counts.py` (the local mode stays as the
     D1/legacy helper and is not used for a cap; `acquire_chain_lock`/`acquire_chain_locks` take
     root keys); `task_advance.py` (new `_lineage_test_fail_count` used by
     `_test_family_used_up`; `_test_fail_count` stays as the compatibility helper); `task_store.py`
     (family stays a label for the cap; per-family guard unchanged).
     The current gate locks `lineage_counts.chain_key` (`review_repair.py:814`-`:837`), which
     follows only `continues` (`lineage_counts.py:150`-`:164`); two Tasks sharing an inherited root
     would take different locks and could both spend the same count. Every member of a root takes
     that root's one key, and a changed root or membership is `UNKNOWN`.
   - Tests: new `tests/test_lineage_claim_gate.py`: two review claims in different families/PRs
     reach the same root cap; a legacy EC2 claim counts on both; two test claims in different
     families/PRs reach the same root test cap; `UNKNOWN` refuses; the store's per-family 3 cap
     still refuses; no claim is written on refusal. A concurrency case has two Tasks in separate
     `continues` chains joined only by an inherited root, with one repair already spent: two
     concurrent admissions take the same root lock, exactly one succeeds, and the total stays
     `repair_max`. A root key that changes under the lock refuses before any count or write. The
     existing D1 tests stay unchanged and green.
4. **Inheritance and the REPLAN rerun.**
   - Files: `task_submission_cli.py` (`record-pm` writes `lineage_inherits`; `retry-run` parser and
     command; `run_task_retry_start` if extended; the retry dispatch authorization);
     `review_repair.py` (`_record_repair_task` passes the root and kind `REPAIR` into `record-pm`);
     `worker_dispatch.py` (a distinct `RUN_RETRY` action in `authorize_dispatch` and the
     `dispatch_audit`); route launchers that accept `expected_failed_developer`;
     `lineage_counts.py` (`check_continues` stays unchanged; diagnostics may name the link);
     `task_advance.py` (a no-work route failure returns `DEVELOPER_START_FAILED`/PM step, not an
     automatic retry).
   - Tests: new `tests/test_lineage_inherits.py`: a REPLAN with the link inherits the root count;
     a new ROOT with the link inherits; one without it does not; a generated repair Task is
     recorded with `lineage_inherits` kind `REPAIR` and, after moving to another D6, still adds to
     the root's run count; the one-REPLAN rule still refuses a second REPLAN;
     `CLAUDE_STATUS_FAILED` without a no-work proof is refused; with one, the same Task is rerun in
     place and its counts do not reset; a retry that does work adds exactly one run. Dispatch
     authorization cases: the retry is refused when the Owner policy or fresh quota refuses, when
     the existing developer is not the failed run, and when the retry action is not authorized; an
     allowed retry records the `RUN_RETRY` audit entry before the run.

## 6. Tests that must keep passing

The following are existing behaviour and must stay green with unchanged assertions:

- `tests/test_lineage_counts.py:207` `test_counts_sum_over_two_and_three_tasks_any_pr`
- `:225` `test_own_claims_count_as_before_only_in_the_base_family` (the helper keeps its
  single-Task family-local result; the new lineage cap is tested separately)
- `:236` `test_a_continuation_counts_its_own_claims_on_every_pr`
- `:255` `test_advance_test_fail_count_includes_successors`
- `:276` `test_an_ancestor_fork_is_in_doubt_from_every_member`
- `:767` `test_repairs_used_up_across_the_chain_escalate`
- `:771` `test_unknown_chain_counts_as_the_limit`
- `:786` `test_a_continuations_own_repair_on_another_pr_counts`
- `:798` `test_test_failures_are_counted_across_the_chain`
- `:956` `test_two_concurrent_repairs_in_one_chain_admit_exactly_one`
- `:1020` `test_a_chain_lock_that_cannot_be_taken_refuses_before_any_count`
- `:1145` `test_fail_count_reaches_stopped_across_a_continuation`
- `:1267` `test_every_review_claim_of_every_chain_task_and_family_counts`
- `:1454` `test_an_auto_replay_is_refused_when_the_chain_has_another_review_claim`
- `:1543` `test_a_single_task_chain_counts_review_claims_of_every_pr_family` (auto, ec2-only, none
  and the manual D1 case all stay as they are; the cap is tested separately)
- `:1614` `test_auto_has_no_effect_on_a_test_failure_repair`

The new tests must also prove: no cap loosening (a run or claim at the old limit still refuses),
`UNKNOWN` refuses, a D6 change cannot change a recorded proof, and a REPLAN/new ROOT without a
link cannot inherit.

## 7. Docs and CI

This Task writes only `docs/design/repair-count-lineage-2190.md`. The CI path classifier maps a
docs-only PR to `check (docs)` (`task_advance.py:532`, `:622`, `:739`-`:741`), so the required
check is `check (docs)`. It has not been run here; the PM runs it before review.

## 8. Uncertainty

- The `--family` argparse line is outside this Task's read paths; its exact line is UNKNOWN.
  `review_repair.py`'s validation and use of `family` are cited above.
- `worker_dispatch.py` is outside this Task's read paths, so its accepted action set and audit
  shape are UNKNOWN. `RUN_RETRY` is a proposed action name; the cited `'DISPATCH'` use is from
  `task_submission_cli.py:8090`-`:8096`.
- No test was run in the D6. No existing assertion is expected to change: the cap becomes
  lineage-wide, while the D1/review-FAIL path and the single-Task helper keep their current scope.

