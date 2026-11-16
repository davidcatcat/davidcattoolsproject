# #2188 Start-failure retry: design

Status: design only (#1502 WP-F unblock). Source read: `a4420d74` (`main`). Read scope: `task_submission_cli.py`,
`task_store.py`, `task_advance.py`, `review_repair.py`, `lineage_counts.py`, `developer_routes/claude.py`.
Not read: `developer_routes/__init__.py` (`run_route`), `worker_dispatch.py`, `source_freshness.py` beyond one grep hit,
the PM helper scripts, and open PRs #2197, #2215 and #2217. Facts from those are marked (not read).

## 1. Current behaviour

- **Duplicate-dispatch refusal.** The Claude delegate refuses any Task whose `developer` is not `None`
  (`task_submission_cli.py:5818`–`:5819`), and a `DEVELOPER_DISPATCHING` record as ambiguous (`:5812`–`:5814`). Its first
  write is a CAS that expects `developer: None, completion: None` (`:5931`–`:5937`). Text Codex refuses the same way
  (`:7788`–`:7790`, `:7874`–`:7876`). `TaskStoreConflict` carries this message by default (`task_store.py:881`). TaskStore
  also refuses to clear a `developer` to `None` (`WORKER_ASSIGNMENT_CLEAR_FORBIDDEN`, `task_store.py:5850`–`:5878`). So a
  Task that got a BLOCKED `developer` before any run started can never be delegated again.
- **What a pre-start failure stores (Claude).** A `RouteError` with no route result stores `state=BLOCKED`, the route
  code, no artifact, and `execution` only when the route gave a provider condition or execution facts (`:6026`–`:6047`).
  Route-side codes raised before any process output: `CLAUDE_PROFILE_INVALID`, `CLAUDE_ENV_INVALID`,
  `CLAUDE_REQUEST_INVALID`, `CLAUDE_CONFIG_INVALID` (`developer_routes/claude.py:68`, `:90`, `:103`–`:109`). Whether
  `run_route` raises these before spawning the process is UNKNOWN (not read). Any other exception stores
  `CLAUDE_RESULT_UNKNOWN` (`:6064`–`:6070`).
- **`CONTINUES_LINEAGE_CONFLICT`.** `record-pm --continues P` refuses when P has `lineage.kind` `child` or `replan`
  (`lineage_counts.py:299`–`:301`). A lineage gets only one REPLAN (`admit_replan`, `task_submission_cli.py:1646`–`:1650`).
  A Task that already has a lineage cannot join another (`:1381`–`:1383`). So a REPLAN Task whose delegate failed to start
  cannot be continued or replaced.
- **`CONTINUES_PREDECESSOR_HAS_SUCCESSOR`.** Refused when any stored record already continues P (`lineage_counts.py:304`–`:314`),
  even when that successor never ran. Fork detection in `successor_records` (`:111`–`:119`) and `chain_in` (`:495`, `:504`)
  makes counts UNKNOWN for a second successor. That rule stays.
- **Repair-cap counting.**
  - D6 Developer cap: `_check_delegate_repair_cap` (`task_submission_cli.py:7328`–`:7380`) and the chain cap
    (`:7587`–`:7611`) count each other Task's one `developer` through `_repair_run_stamp` (`:7410`–`:7453`). A run is
    skipped only as a known prelaunch refusal (`_PRELAUNCH_REPAIR_REFUSALS`, `:7112`–`:7125`; for Claude only
    `CLAUDE_DISPATCH_DENIED`) or as a launched run that proves no work. `_launched_run_did_no_work` (`:7155`–`:7205`) needs
    `execution.cleanup_status == CONFIRMED`. So a Claude start failure with `execution: None` **counts** as a run.
  - Review-repair count: `lineage_counts` counts every `review_repair_claims` entry in any state (`lineage_counts.py:190`–`:214`).
    A repair Task whose BLOCKED developer is not `CLAUDE_NO_CHANGES` derives `UNKNOWN` (`task_advance.py:2343`–`:2361`),
    so advance reports `REPAIR_OUTCOME_UNKNOWN` (`:2459`–`:2461`). A `REPAIR_RETRY` marks the old claim `FAILED` and
    appends a new one (`review_repair.py:857`–`:865`), which spends a repair. An auto retry refuses
    `REVIEW_REPAIR_PM_REQUIRED` once any claim exists (`:810`–`:821`).
  - Repair Tasks are linked by claim, not by `continues`: `_record_repair_task` passes no `continues` (`review_repair.py:284`–`:292`,
    `task_submission_cli.py:8115`).

## 2. Start failure: definition

A Task's stored `developer` D is a **start failure** only when every check below passes. Any check that is unknown,
malformed or failing means "not a start failure" (fail closed).

1. `D.provider_profile == 'claude'`, `backend_session_semantics == 'CLI_REPORTED_SESSION_ID'`, `D.state == 'BLOCKED'`.
2. `D.error_code` is in `START_FAILURE_CODES`, which initially lists the four route-validation codes in §1. PR 1 confirms
   in `run_route` that each one is raised before the process spawns. A code that can follow a spawn is dropped
   (SPEC_MISMATCH if none remain).
3. `D.artifact`, `D.artifact_sha256` and `D.usage` are `None`. `progress`, `no_changes_diagnosis` and `blocked_reason` are
   absent, and `changed_files` is absent or `[]`.
4. `D.execution` is `None`, or it has `native_session_id`, `process_exit_code` and `stdout_sha256` all `None` and
   `cleanup_status == 'CONFIRMED'`.
5. `completion.state == 'WAITING_EXECUTOR'` and `completion.evidence.result_head is None`. The Task has no
   `completion_events`, no `review_repair_claims`, no `merge_attempt`, no `abandonment`, and no published PR.
6. **Live D6 proof (cleanup confirmed, no diff):** `_d6_unchanged_probe(...)(D.source_sha, write_paths=D.write_paths)`
   (`task_submission_cli.py:7383`–`:7407`) returns `True`. The registered D6 head equals `D.source_sha`, the tree is
   clean and no status path overlaps the write paths.

**Never a start failure:** `DEVELOPER_RESPONDED`, `DEVELOPER_OUTPUT_REJECTED`, `DEVELOPER_OUTCOME_UNKNOWN`,
`DEVELOPER_DISPATCHING` and `DEVELOPER_PENDING`. Also never: the codes `CLAUDE_RESULT_UNKNOWN`, `CLAUDE_NO_CHANGES`,
`CLAUDE_WRITE_SCOPE_VIOLATION`, `CLAUDE_OUTPUT_TOO_LARGE`, `CLAUDE_OUTPUT_INVALID`, `CLAUDE_STATUS_FAILED` and
`CLAUDE_QUOTA_EXHAUSTED`. `CLAUDE_D6_BINDING_CHANGED` is also excluded, because it is raised after a run too (`:5993`).
An Owner denial is never one (`CLAUDE_DISPATCH_DENIED`, `OWNER_DISPATCH_DENIED`: a decision, not a fault). Neither is a
committed carry, a sensitive-screen refusal, any run with a native session, usage, an artifact or a result head, or any
non-Claude route (out of scope; another route needs its own code list and a design change).
**No start failure exists** when the delegate refused before its CAS. This includes `LLMTOOL_SOURCE_STALE`
(`source_freshness.py:27`; caller not read) and the D6, gate and chain refusals before `:5931`. There `developer` stays
`None` and the same Task can simply be delegated again.

## 3. Retry entry point and record

- **Entry:** `llmtool task retry-start TASK --executor-task D6 --pm-session S` (CLI caller check as in `:7980`–`:7993`).
  It takes no scope arguments. Like `_delegate_repair` (`review_repair.py:214`–`:224`), it rebuilds the delegate request
  from the stored D: route, paths, acceptance, owner ref, egress basis, and the repair packet for a repair Task.
- **Order:** caller and quota checks, then `_check_delegate_repair_cap`. Then, inside `_delegate_chain_gate`
  (`:7614`–`:7659`, under the chain locks), the Task is re-read, §2 is re-checked on that snapshot, and every Claude-route
  gate in `run_task_claude_delegate` runs again. The write is one CAS expecting that snapshot's `record_sha256`. The
  provider is called only after it, on the existing path from `:5938`.
- **Record (one write on the same Task):**
  - New append-only field `start_failures`, at most 2 entries. Each entry is `{failed_developer: D, record_sha256,
    d6_head, retried_at, pm_session}`.
  - `developer` becomes a fresh `DEVELOPER_PENDING` dict with a new `assignment_id`, session and `created_at`, plus
    `start_retry_of: D.assignment_id`. Scope, `executor_task_id` and `source_sha` stay equal to D's.
  - `completion` is unchanged.
- **TaskStore rule:** `start_failures` changes only by that append. A dict→dict `developer` replacement of a BLOCKED
  developer is allowed only in the same write, with the archived entry equal to the stored D. Otherwise the store refuses
  `START_RETRY_INVALID`. Clearing `developer` stays forbidden (`task_store.py:5850`).
- **Refusals:** `START_RETRY_NOT_START_FAILURE` (with the failed check number), `START_RETRY_D6_CHANGED`,
  `START_RETRY_LIMIT_REACHED` (a third start failure goes to the PM), `START_RETRY_CONFLICT`. None of them write.
- **Applies to:**
  - A **first delegate** Task.
  - A **repair Task**: its root claim stays `DISPATCHED` with the same `repair_task_id`, and no claim is added.
  - A **REPLAN Task**: `lineage` is untouched, so `admit_replan` still sees exactly one REPLAN.

## 4. Counts and gates unchanged

- No `review_repair_claims` entry, no Task and no `continues` link is created. So `lineage_counts`,
  `chain_review_repairs`, `_review_gate` and the auto limit read the same values as before the retry.
- Each Task has one `developer` slot, so `_repair_run_stamp` counts the Task at most once. The retried run counts exactly
  as a first run would. `start_failures` is read by no counter. A start failure was already counted before the retry,
  so the retry resets no count.
- The repair cap, `DELEGATE_REPAIR_LIMIT_REACHED` and the High Debug requirement (`_check_repair_rca`, `:7479`–`:7568`)
  run again unchanged. Repair #2 still needs its RCA_CONFIRMED High Debug at the unchanged D6 head. The retry's newer
  `created_at` can only make a later High Debug check stricter (`:7536`).
- `REPAIR_RETRY` (new claim) and REPLAN admission stay unchanged. Retry never applies to a run that produced output.

## 5. Successor left by a refused delegate

Case: a PM helper (not read) runs `record-pm --continues P`, which creates S, and the delegate of S is then refused.

- **Refused before the CAS** (`LLMTOOL_SOURCE_STALE`, D6 binding, quota): S has `developer: None` and counts nothing
  (`:7417`). A new `record-pm --continues P` refuses `CONTINUES_PREDECESSOR_HAS_SUCCESSOR`, and that refusal is correct.
  The PM re-delegates S itself. If S's contract is wrong, the PM records `--continues S` (S has no lineage, so it is
  allowed, `lineage_counts.py:299`–`:301`), within `MAX_CHAIN`. PR 3 makes the refusal name S when S is empty
  (`developer`, `completion` and claims all empty, no lineage). The PM helper then reuses S instead of failing.
- **Refused after the CAS with a start failure:** `task retry-start S` (§3).
- **Not a start failure:** the ordinary repair or PM flow. A fork is never created, and the fork rule is not relaxed.

## 6. Ordered PRs

All touch sensitive-registry files (see #2199 §2.5), so each PR needs the security review that #2199 defines.

1. **Pure predicate and store field (not wired).**
   - Files: new `tools/llmtool/llmtool/start_failure.py` (`classify(record, d6_unchanged) -> (bool, check_no)`),
     and `task_store.py` (the `start_failures` field, validator and §3 rule).
   - Also reads `developer_routes/__init__.py` to confirm `START_FAILURE_CODES`.
   - Tests: `tests/test_start_failure.py` (each §2 check, each never-start code, a probe raising), and
     `tests/test_task_store_start_failures.py` (append-only, max 2, mismatched archive refused, clear still forbidden).
2. **Entry point.**
   - Files: `task_submission_cli.py` (`run_task_retry_start` and parser; `run_task_claude_delegate` takes an expected
     failed developer for its CAS).
   - Tests: `tests/test_task_retry_start.py`. It covers a first delegate, a repair Task (claim and counts unchanged), a
     REPLAN Task (lineage unchanged), a dirty D6, a native session present, the limit, repair #2 without High Debug
     refused, and a concurrent write refused. The route runner is injected; no provider is called.
3. **Advance and continuation diagnostics.**
   - Files: `task_advance.py` (`derive_repair_state` returns `START_FAILED` for §2 checks 1–5, and the step
     `DEVELOPER_START_FAILED` stops for the PM; no automatic retry) and `lineage_counts.py` (the refusal names an
     empty successor).
   - Tests: the advance step and `check_continues` cases.

Open PRs, by number. Their content was not read in this Task, so the PM confirms each disposition before acting.

- **#2197:** keep. #2199 reports it as blocked by a comment-only security FAIL, which this design does not touch.
- **#2215:** replaced by PRs 1–2 if it adds a start-retry path or widens `_PRELAUNCH_REPAIR_REFUSALS` for Claude.
  Otherwise keep.
- **#2217:** replaced by PR 3 if it relaxes `check_continues` for an unused successor. Otherwise keep.

Tests follow Owner #763 A. They use no credential-shaped strings, inject the route, the probe and `_screen_text`
outcomes, and never weaken a check.
