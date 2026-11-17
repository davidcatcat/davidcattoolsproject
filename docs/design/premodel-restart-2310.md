# #2310: delegating again a Task refused before any model call (investigation)

Status: read-only investigation. No code or test change. Source SHA `e8f327cb`.

Trigger: a Claude write attempt (Task `eea41023`, #2310 comment 6959374595) to let a
Task that was refused before any model call be delegated again ended with no change
and no stated reason. This note gives the facts the next attempt needs.

Scope of the reading: `tools/llmtool/llmtool/task_submission_cli.py`,
`tools/llmtool/llmtool/task_store.py`, `tools/llmtool/llmtool/developer_routes/gw_agent.py`,
`tools/llmtool/tests/test_task_submission_cli.py` and
`tools/llmtool/tests/test_gw_agent_route_wiring.py`. Other modules named below
(`start_failure.py`, `worker_dispatch.py`, `lineage_counts.py`, the shared
`run_route`) were outside this Task's read paths; what they do is marked UNKNOWN.

## 1. How a delegate records an assignment and refuses a duplicate

Each direct route uses the same two layers: a pre-check on a fresh read, then one
compare-and-swap (CAS) claim under the chain locks.

1. **Pre-check (no write).** The route reads the Task and refuses when a developer
   already exists:
   - gw-agent: `task_submission_cli.py:6231-6240`. A stored gw-agent developer in
     `DEVELOPER_DISPATCHING` refuses `gw-agent outcome is ambiguous; replay refused`.
     Any other non-null `developer` refuses
     `developer assignment already exists; duplicate dispatch refused`.
   - Claude: `task_submission_cli.py:5897-5911`. This works the same way, except
     that with `expected_failed_developer` (the #2188 start retry only) the stored
     developer must equal that failed developer (`START_RETRY_CONFLICT`).
   - Codex text route: `task_submission_cli.py:8061-8063`.
2. **Claim CAS.** `_delegate_chain_gate` (`task_submission_cli.py:7887-7932`) takes
   the chain locks and re-runs the chain repair count (`_check_chain_repair_count`,
   `:7860-7884`) and the continuation-link check. It then calls the claim, which is
   `store.d6_bind_compare_and_update(... expected={'schema_version': 2,
   'state': 'PM_RESPONDED', 'developer': None, 'completion': None},
   developer=..., completion=...)`.
   - gw-agent: `:6417-6424`. Claude: `:6038-6051`. Codex: `:8139-8149`.
   - The claim writes a new `developer` (`state='DEVELOPER_PENDING'`, a fresh
     `assignment_id` from `new_id()`; gw-agent `:6384-6401`, Claude `:6002-6018`).
     In the same write it writes `completion` (`state='WAITING_EXECUTOR'`, bound to
     the D6; gw-agent `:6402-6416`, Claude `:6019-6033`).
   - If the CAS loses, `TaskStoreConflict` is raised. Its default message is the
     same duplicate text (`task_store.py:951-957`), and the routes re-raise it as
     `developer assignment already exists; duplicate dispatch refused`.
3. **Store backstops** in `TaskStore.compare_and_update` (`task_store.py:6050-6090`):
   - Setting `developer` back to `None` is always refused. The refusal is audited
     as a `REASSIGN` entry with decision `BLOCKED` and code
     `WORKER_ASSIGNMENT_CLEAR_FORBIDDEN` (`:6050-6078`).
   - Every developer change goes through
     `worker_dispatch.authorize_task_store_change` (`:6079-6087`). A denial is
     written to `dispatch_audit` before it is raised. The rules inside it are
     UNKNOWN (`worker_dispatch.py` was not read).
   - `start_failures` may change only by one `START_RETRY` append
     (`_check_start_failures_write`, `:2145-2166`).
   - A newly gained live D6 needs the binding lock (`:6103-6107`).
   - The audit `action` enum is fixed at `task_store.py:2522-2525`. A new action
     name needs a schema change there.
4. Tests that pin the duplicate refusal:
   - `test_task_submission_cli.py:3603-3607`: Codex, a second delegate after a
     success.
   - `test_task_submission_cli.py:4656-4660`: Codex dead-child recovery, then a
     re-delegate is refused with no run.

## 2. What a pre-model refusal leaves in the Task record

"Pre-model" here means refused before any provider/model call. Two cases:

**A. Refused before the claim CAS.** Nothing is written to the Task, and
`developer` and `completion` stay `None`. Only the raised `LLMToolError` reports the
refusal, so the Task can be delegated again as it is.

- gw-agent refusals before the claim (`task_submission_cli.py:6222-6416`):
  - mode, context, `--data-class` and timeout checks;
  - the PM assignment and route match;
  - D6 binding and `D6_CLAUDE_RESERVED`;
  - text screens;
  - the gateway profile and budgets (`gateway profile is unavailable or invalid`);
  - the role/model check (`_gw_agent_developer_profile`, `:6348`);
  - prompt size.
- Claude refusals before the claim (`:5884-6044`):
  - the owner ref;
  - `D6_CLAUDE_AGENT_REQUIRED`;
  - `worker_dispatch.evaluate`;
  - screens;
  - route config and executable;
  - `BLOCKED_WRITE_ISOLATION`;
  - prompt and skill-card size.
- The CLI caller check and the quota refusal (`run_task_delegate_checked`,
  `:8253-8278`) also write no Task.

**B. Refused after the claim CAS, before the model call.** Here the Task is stuck.
`developer` is non-null, so every later `task delegate` hits the duplicate refusal
from section 1.

- **gw-agent** (`persist_failure`, `task_submission_cli.py:6427-6463`). Triggers:
  - the DISPATCHING write is denied or rejected (`:6482-6492`);
  - `authorize_dispatch`, `GwAgentRoute(...)` or `validate_profile` fails
    (`:6493-6505`);
  - the owner gate inside `run_route` refuses (`route_gate`, `:6517-6524`). This is
    stored as `DEVELOPER_DISPATCH_DENIED` (`:6600`);
  - `SessionCallSite` / `WiringRefused` refuses (`:6597-6598`);
  - a `RouteError` is raised by the route's own validation or preparation:
    `validate_request` (`gw_agent.py:273`), `_prepare_files` (`gw_agent.py:353`),
    `_load_credential` (`gw_agent.py:455`) and the session home and lock
    (`gw_agent.py:765-972`). It is stored as `DEVELOPER_<code>`
    (`task_submission_cli.py:6599-6609`). Whether `run_route` raises these before
    the launch in every case is UNKNOWN (`run_route` was not read).

  What is left:
  - `state` stays `PM_RESPONDED`.
  - `developer.state` is `DEVELOPER_UNAVAILABLE` with an `error_code` (max 64
    chars), `artifact`/`usage` set to `None`, and `execution` set to `None`. The
    exception is a route quota refusal, which keeps only its parsed condition
    (`:6448-6452`).
  - `provider_type`/`model` are `None` when the DISPATCHING write never landed.
    After it landed they are `gw-agent-cli` and the model (comment at `:6429-6432`).
  - `completion` stays the `WAITING_EXECUTOR` record from the claim:
    `persist_failure` writes `developer` only (`:6455-6457`).
  - `dispatch_audit` keeps the `DISPATCH` entries, including a `BLOCKED` one for a
    gate denial (`task_store.py:6079-6087`).
  - The D6 is **not** held: `_d6_bind_holds` treats only `DEVELOPER_PENDING`,
    `DEVELOPER_DISPATCHING`, and `DEVELOPER_OUTCOME_UNKNOWN` without confirmed
    cleanup as live (`task_store.py:4123-4142`).
    `test_gw_agent_route_wiring.py:2324-2337` shows a new Task binding the same D6
    after a `DEVELOPER_UNAVAILABLE` run.
  - Store shape rule for `DEVELOPER_UNAVAILABLE`: `error_code` is required, and
    `artifact` and `usage` must be `None` (`task_store.py:3195-3198`). The per-path
    shapes are pinned by `test_gw_agent_route_wiring.py:2255-2285`.
- **Claude** (`task_submission_cli.py:6052-6192`). Triggers:
  - the owner gate in `GatedBoundary.verify` / `route_gate` refuses (`:6070-6092`).
    This is stored as `CLAUDE_DISPATCH_DENIED` with
    `blocked_reason='OWNER_DISPATCH_DENIED'` (`:6142`, `:6165-6166`);
  - a binding change before the call (`:6058-6069`);
  - any `RouteError` raised before a result exists.

  What is left:
  - `developer.state='BLOCKED'`, with `provider_type='claude-cli'`, the model,
    `error_code` and `artifact=None`. Unless the owner gate denied the run,
    `execution` may come from `_route_failure_execution` and the failure evidence
    from `_claude_attach_failure_evidence` (`:6149-6164`).
  - `completion` stays `WAITING_EXECUTOR`.
  - The D6 **stays held**: `_claude_d6_holds` holds whenever the developer or
    completion names the D6 and no `abandonment` exists, whatever the state
    (`task_store.py:4112-4120`). A new Task on that D6 therefore needs the old one
    abandoned first. The #2188 `task retry-start` path
    (`task_submission_cli.py:8425-8490`) re-delegates the **same** Task for this
    reason.

The existing same-Task restart (#2188) is Claude-only:

- `_validate_start_failures` requires `failed_developer.provider_profile ==
  'claude'` and `state == 'BLOCKED'` (`task_store.py:2102-2125`).
- `_check_start_failures_write` requires a `('claude-developer', 'START_RETRY')`
  attempt (`task_store.py:2164-2165`).
- At most `MAX_START_FAILURES = 2` archive entries are kept
  (`task_store.py:112`).
- `run_task_retry_start` evaluates `claude-developer` only (`:8458`).
- The start-failure classifier `start_failure.classify` / `record_checks` decides
  what counts as a start failure. Its checks 1-6 are UNKNOWN here
  (`start_failure.py` was not read).

The two read test files hold no retry-start test, so where those tests live is
UNKNOWN.

### Why the earlier attempt may have stopped without a change

The cause is UNKNOWN from these files. Two facts bear on it:

- **Needed files outside the scope.** Any same-Task restart for a non-Claude route
  must change code that the read files show is hard-coded to Claude:
  `task_store.py:2112-2113` and `:2164`. It also needs `worker_dispatch.py`
  (`task_store_attempts`, `authorize_task_store_change`,
  `MAX_START_RETRIES_PER_TASK`) and `start_failure.py`. A Developer whose
  `write_paths` lacked those files would have to stop.
- **How a Claude no-change run is recorded.** It is stored as `CLAUDE_NO_CHANGES`.
  A `no_changes_diagnosis` is attached only from the run's parsed CLI result, and
  a reason that repeats the prompt is stored as UNKNOWN (`:6110-6114`,
  `:6156-6161`). This fits "no stated reason".

The next dispatch should list those modules in `read_paths`/`write_paths`.

## 3. Repair counts that bear on any restart

- The D6 cap and the chain cap both skip the delegated Task's own record
  (`task_submission_cli.py:7591-7593`, `:7866-7868`). A same-Task restart is never
  counted against itself, but every other Task on the D6 or in the chain is.
- `_repair_run_stamp` (`:7643-7686`) skips a run in two cases:
  1. Its `(profile, state, error_code)` is in `_PRELAUNCH_REPAIR_REFUSALS`
     (`:7266-7279`) and `artifact`, `artifact_sha256`, `execution` and `usage` are
     all `None`.
  2. `_launched_run_did_no_work` proves no work (`:7372-7425`). This needs
     `execution.cleanup_status == 'CONFIRMED'` plus a clean, unmoved D6. For a
     gw-agent `DEVELOPER_UNAVAILABLE` record, the code must also be in
     `_POST_RUN_NO_WORK_GW_AGENT_REFUSALS` (`:7294-7352`).
- Some pre-model gw-agent codes are in neither list, so they **count** as a run.
  Examples: `DEVELOPER_OWNER_PAUSED` (pinned by
  `test_gw_agent_route_wiring.py:2256-2259`),
  and `DEVELOPER_DISPATCH_PERSISTENCE_REJECTED`. The `WiringRefused` codes are
  UNKNOWN, because `gw_agent_wiring` was not read. Most gw-agent
  pre-model failures also have `execution=None`, so path 2 can never apply to them.
- The `task_submission_cli.py` line references in the comments of
  `_POST_RUN_NO_WORK_GW_AGENT_REFUSALS` (`6394`, `6404`, `6406`, `6506`) are stale.
  The handlers are now at `:6487-6502`. The `gw_agent.py` line references there
  were not re-checked.
- `_start_retry_count` (`:8288-8299`) uses the larger of the archive length and
  the number of `ALLOWED` `START_RETRY` audit entries. Unreadable data counts as
  the limit.

## 4. Candidate approaches

### A. Extend the #2188 same-Task start retry to gw-agent

Accept a gw-agent `DEVELOPER_UNAVAILABLE` pre-model failure as `failed_developer`.
Archive it in `start_failures` and replace it by a new PENDING assignment in one CAS,
as Claude does (`_start_retry_claim`, `:8368-8422`).

- **Counts:** the archived developer leaves `record['developer']`, and
  `_repair_run_stamp` reads only that field. Archiving a failure that was counted
  (see section 3) would therefore **undercount** the D6 and chain caps. The retry
  must stay limited to proven no-work failures (the classifier's D6-unchanged
  check) or keep counting archived entries.
- **Review gates:** the review prompt uses `pm_assignment` and an optional
  original Task (`:1820-1906`), and it is unchanged by a same-Task retry. The new
  developer must keep the `_START_RETRY_SCOPE_FIELDS` (`:8282-8285`) so that the
  reviewed scope cannot widen. gw-agent also stores `data_class` (`:6400`), which
  should be added to the kept fields.
- **Dispatch gating:**
  - The store's validators and START_RETRY attempt check need a gw-agent worker
    key (`task_store.py:2112-2113`, `:2164`).
  - `worker_dispatch` must authorise `START_RETRY` for `gw-agent` (UNKNOWN today).
  - The gw-agent `route_gate` accepts only `OWNER_POLICY_ALLOWED` (`:6521`), so a
    retry after an owner-gate denial will be refused again until the policy
    changes. That is correct, but it uses one audit entry per attempt
    (`MAX_AUDIT_ENTRIES`, `task_store.py:6060`).
- **Files:** `task_store.py`, `worker_dispatch.py`, `start_failure.py`, and
  `task_submission_cli.py` (`run_task_gw_agent_delegate` needs an
  `expected_failed_developer` path like `:5905-5911`), plus tests.

### B. Restart as a new Task on the same D6 (no same-Task re-delegation)

Keep the duplicate refusal. The PM records a new Task on the same D6, either a
`continues` link or a lineage replan.

- For gw-agent this already works, because a `DEVELOPER_UNAVAILABLE` run holds no
  D6 (`test_gw_agent_route_wiring.py:2324-2337`).
- For Claude the old Task must be abandoned first (`_claude_d6_holds`), which is
  why #2188 exists.
- **Counts:** the failed Task stays in the D6/chain count unless its code is
  listed in `_PRELAUNCH_REPAIR_REFUSALS` (section 3). Without that, one pre-model
  refusal uses a review round, and the third run becomes repair #2, which needs a
  root cause (`:7704-7709`). The fix is to list the proven pre-model codes. Each
  code must be backed by line evidence that it is raised before the launch.
- **Review gates:** a continuation Task is reviewed against its original Task's
  acceptance (`:1820-1906`). `check_continuation_link` (`lineage_counts.py`,
  UNKNOWN) may refuse a new Task that lacks the link.
- **Dispatch gating:** an ordinary `DISPATCH` with no store schema change. This
  approach has the lowest risk, and no production check is weakened.

### C. Allow clearing `developer` back to `None` after a pre-model refusal

- **Counts:** the failed record disappears. That loses the evidence and can
  undercount if a failure is misclassified.
- **Review gates:** no trace is left of the earlier assignment.
- **Dispatch gating:** this directly weakens `WORKER_ASSIGNMENT_CLEAR_FORBIDDEN`
  (`task_store.py:6050-6078`), a production check. It would also have to reset
  `completion`, which the claim CAS expects to be `None`. **Not recommended.**

### Recommendation

Take B first: list the proven pre-model gw-agent codes in
`_PRELAUNCH_REPAIR_REFUSALS`, with evidence for each code. Take A only if the Owner
needs a same-Task restart for gw-agent. Give that dispatch `worker_dispatch.py` and
`start_failure.py` in scope.
