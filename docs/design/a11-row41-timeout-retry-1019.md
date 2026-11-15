# A11 row 41: role timeout, retry once, wired into task drive (#1019)

Status: design only. No code, no tests and no policy change in this PR.

Source commit: cb9633ce820e20930fbf09e728186e862f165c7a.

Read for this design: `tools/llmtool/llmtool/task_drive.py`,
`tools/llmtool/llmtool/worker_dispatch.py` and
`tools/llmtool/llmtool/repair_policy.py`. Every line number below refers to
those files at the source commit. A statement about any other file is marked
UNVERIFIED: it was not read for this design and must be checked by the PR that
touches it.

## 1. Goal

When a Developer run ends because its route hit its own time limit, and the
run left no write in the D6, `llmtool task drive` dispatches the same
assignment once more, in the same D6. The number of such retries per Task is
the policy value `timeout_continuation_max` (default 1). A timeout after that
budget is used stops the Task with one fixed code and an Owner notice (D4).
Anything that is not a clean, known timeout is never retried.

Out of scope: Reviewer and AI Debug timeouts, Codex timeouts (Codex keeps its
existing continuation, section 2.3), and any change to how a route measures
its own time limit.

## 2. Current state

### 2.1 task_drive.py: a Developer that is not finished

- The `drive` docstring says it will "stop without polling if the initial
  Developer is unfinished" (task_drive.py line 51). There is no
  timeout-specific branch anywhere in `drive` (lines 47-249).
- `drive` only reacts to the step and codes that `task_advance.advance`
  returns (lines 136-161). It never reads Developer state from the store
  itself and never dispatches a Developer on its own.
- Steps in `_WAITING_STEPS` (`WAITING_CI`, `WAITING_EXECUTOR`,
  `WAITING_REPAIR`, line 14) are polled with backoff and a GitHub quota check
  (lines 187-201). Polls use the `max_steps` budget (comment at lines 83-84).
  When the budget runs out the drive returns `MAX_STEPS` (lines 245-249).
- Any step in `_TERMINAL` (line 23, includes `STOPPED`), `_PM_STOPS`
  (line 25), `_UNKNOWN` (lines 15-18) or ending in `_UNKNOWN` is returned to
  the caller (lines 204-206).
- Any non-waiting step that carries codes is returned (lines 239-240). A
  result with no step but with codes is returned as `REFUSED`
  (lines 157-158). `DEVELOPER_ATTENTION`, `NEEDS_ATTENTION` and similar steps
  are returned (lines 241-243).
- The drive stops with `RESTART_REQUIRED` if the llmtool code tree is unknown
  or changes during the drive (lines 62-71, 86-102).
- The only flag-selected extra step is the merge: advance is called with
  `merge=True` only for that explicit final step (comment at lines 212-213,
  calls at lines 106 and 216).

So today a timed-out Developer ends the drive in one of these ways, depending
on what advance returns for it: a `*_UNKNOWN` step (lines 204-206), a step
with codes (lines 239-240), an attention step (lines 241-243), or, if advance
keeps answering `WAITING_EXECUTOR`, polling until `MAX_STEPS`
(lines 245-249). Which of these advance actually emits for each route's
timeout is UNVERIFIED (`task_advance.py` and the route adapters were not
read). In no case does the drive dispatch the Developer again.

### 2.2 worker_dispatch.py: the timeout continuation cap

- `MAX_TIMEOUT_CONTINUATIONS_PER_TASK = 1` (worker_dispatch.py line 54), the
  default for the policy key `timeout_continuation_max` (#1815).
- `timeout_continuation_max()` (lines 296-303) returns
  `repair_policy.load_policy().get('timeout_continuation_max',
  MAX_TIMEOUT_CONTINUATIONS_PER_TASK)`, so an absent key gives 1. If
  `load_policy` raises `LLMToolError` it returns `None` (lines 302-303).
- `TIMEOUT_CONTINUATION` is one of the `AUDIT_ACTIONS` (lines 49-53).
- `task_store_attempts` produces `TIMEOUT_CONTINUATION` only for the Codex
  route: old `provider_profile == 'codex'`, old state
  `DEVELOPER_OUTCOME_UNKNOWN`, old `error_code == 'CODEX_TIMEOUT_WITH_CHANGES'`,
  new state `DEVELOPER_DISPATCHING`, and a new `assignment_id`
  (lines 622-631). It is placed before the identity-change check
  (lines 633-636).
- `authorize_task_store_change` checks a `TIMEOUT_CONTINUATION`
  (lines 709-752): the expected record must equal the old Developer
  (lines 717-718); provider, D6 Task, source SHA, read paths, write paths and
  acceptance must be unchanged (lines 719-723); the old execution must have
  `status == 'TIMEOUT'`, `diff_base_sha` equal to the old `source_sha` and a
  64-hex `diff_sha256` (lines 724-728); the completion must carry a new
  40-hex `result_head` (lines 729-734). Otherwise it refuses
  `WORKER_TIMEOUT_CONTINUATION_PRECONDITION` (lines 735-738).
- The cap: every earlier audit entry with the same worker key and the same
  action counts, whatever its decision and whatever its assignment
  (lines 739-742). A `None` limit refuses
  `WORKER_TIMEOUT_CONTINUATION_POLICY_INVALID` (lines 743-748); a used-up
  limit refuses `WORKER_TIMEOUT_CONTINUATION_LIMIT` (lines 749-752).
- Two kinds of audit entry share one action name. A TaskStore transition
  entry is written by `audit_entry(decision, action, binding=binding)`
  (line 804) and has no `authorization_id`. An adapter entry is written by
  `authorize_dispatch` with the supplied action (lines 434-435) and carries
  `authorization_id` only when a matching transition was found
  (lines 328-333, 417-418). The count at lines 739-742 does not tell them
  apart.
- So the existing continuation is for a Codex timeout WITH changes, on a new
  assignment. A route timeout with no changes has no path today.
- A same-assignment re-dispatch from any state other than pending is
  classified `RETRY` (lines 647-655), and every `RETRY` is refused
  `WORKER_REPLAY_FORBIDDEN` because its allowed old states are empty
  (lines 763-771). A re-dispatch from `DEVELOPER_UNAVAILABLE` is `RECOVERY`,
  capped at `MAX_RECOVERY_PER_ASSIGNMENT = 1` per assignment (line 23,
  lines 776-792).
- Dispatch authority: `evaluate` returns `OWNER_PAUSED` for a paused worker
  (line 293) and `OWNER_ASSIGNMENT_REQUIRED` when an Owner assignment row is
  missing (line 292). The adapter gate `authorize_dispatch` (lines 401-451)
  needs a matching, unconsumed transition entry with the same assignment,
  source SHA and contract digest (lines 416-431), and the record must be in
  `DEVELOPER_DISPATCHING` for that worker (lines 467-470).
- The audit holds at most `MAX_AUDIT_ENTRIES = 64` entries (line 21,
  lines 694-695).
- Identity fields that must not change on a same-assignment step are listed
  in `_DEVELOPER_IDENTITY_FIELDS` (lines 473-477); they include
  `assignment_id`, `session_id` and `source_sha`.
- Current Developer route ids: `claude` -> `claude-developer`, `agy` -> `agy`,
  `opencode` -> `opencode-developer`, `gw-agent` -> `gw-agent`
  (`worker_key_for`, lines 665-680).

UNVERIFIED: a Codex-only timeout recovery in `task_submission_cli.py` that
creates the `CODEX_TIMEOUT_WITH_CHANGES` continuation. That file was not read.
This design does not change it.

### 2.3 repair_policy.py: limits

- `OPTIONAL_POLICY_LIMITS = {'timeout_continuation_max': (0, 3)}`
  (repair_policy.py line 21): the key is optional, and when present it must be
  an int from 0 to 3 (lines 50-53); otherwise `load_policy` refuses
  `POLICY_INVALID` (lines 49 and 57). An absent key keeps the caller default
  (comment at line 20).
- The numbers live only in `policy.json` and no caller overrides them
  (lines 3-6). Whether the current `policy.json` sets
  `timeout_continuation_max` is UNVERIFIED (file not read).
- An unknown count counts as used up (`_used`, lines 67-69).
- D1 repair: `admit_repair` allows at most `repair_max` repairs, and every
  repair after the first needs `RCA_CONFIRMED` (lines 114-125).
- D4: `STOPPED`, `DROPPED` and `FAILED_FINAL` notify the Owner
  (`OWNER_NOTICE_STATES`, line 147; `owner_notice`, lines 181-183).

## 3. Proposed behaviour

### 3.1 What counts as a role timeout

A Developer result is a role timeout only if ALL of these hold. Any check
that cannot be made is a failure, never a pass.

1. `provider_profile` is one of `claude`, `agy`, `opencode`, `gw-agent`.
2. `error_code` is exactly the one fixed timeout code for that route, from
   the table below. No prefix, pattern or substring match.
3. `execution.status == 'TIMEOUT'` (the same field the Codex check reads at
   worker_dispatch.py line 725).
4. `execution.cleanup_status == 'CONFIRMED'`: the route process is known to
   be gone, so it cannot write after the check (the same field is required
   for a finished run at lines 524 and 564).
5. No write (section 3.4).
6. No `result_head` in the completion evidence and no committed result carry
   (#1148, lines 539-574) for this assignment.

Fixed route timeout codes (proposed):

| Route | Worker key | Timeout code |
| --- | --- | --- |
| claude | claude-developer | `CLAUDE_DEVELOPER_TIMEOUT` |
| agy | agy | `AGY_DEVELOPER_TIMEOUT` |
| opencode | opencode-developer | `OPENCODE_DEVELOPER_TIMEOUT` |
| gw-agent | gw-agent | `GW_AGENT_DEVELOPER_TIMEOUT` |

The strings each adapter emits today for its own time limit are UNVERIFIED
(the adapters were not read). PR 1 must confirm the exact string each route
records and put that string in the table, one per route. A route that has no
distinct timeout code stays outside the table: its timeouts stop as today
until a separate Task adds the code. This design does not invent codes inside
adapters.

Never a role timeout, even with a timeout-like message:

- quota or free-limit refusals, including the worker quota FAIL of #2148 and
  `CLAUDE_QUOTA_EXHAUSTED` (both UNVERIFIED, named in recent commit subjects
  only); a quota code wins over any timeout code;
- auth failures, route unavailable, process crash, rejected output,
  sensitive-screen refusals and gw-agent tool refusals;
- the OpenCode step limit (the model ran and may have written);
- the drive's own `MAX_STEPS` stop (lines 245-249) and any long
  `WAITING_EXECUTOR` poll: the drive never declares a timeout from wait time;
- Codex `CODEX_TIMEOUT_WITH_CHANGES`, which keeps its own path.

### 3.2 Drive behaviour

The decision is made in one pure function and enforced in two places. The
drive never writes the store directly; it only selects an explicit advance
step, as it already does for the merge (task_drive.py lines 212-216).

1. Classify (pure, no I/O): a new `role_timeout.classify(record, d6_check,
   limit)` returns one of `RETRY_READY`, `LIMIT`, `WRITE_AMBIGUOUS`,
   `POLICY_INVALID` or `NOT_TIMEOUT`. `limit` is the value of
   `timeout_continuation_max()`; `None` gives `POLICY_INVALID`. The used
   count is `count_timeout_attempts(record['dispatch_audit'])` (section
   3.3): logical attempts, not audit entries. A clean timeout with a used
   count at or above `limit` gives `LIMIT`.
2. Advance without the flag: when the Developer record classifies as a role
   timeout, advance makes no dispatch and returns:
   - `RETRY_READY`: step `DEVELOPER_TIMEOUT`, codes
     `['ROLE_TIMEOUT_RETRY_READY']`, Task state unchanged;
   - `LIMIT`: the Task moves to `STOPPED` with code `ROLE_TIMEOUT_LIMIT`;
     the D4 notice follows from `owner_notice('STOPPED')` (repair_policy.py
     lines 147, 181-183). Where the D4 notice is recorded today is
     UNVERIFIED; PR 2 uses that existing path and adds no new channel;
   - `WRITE_AMBIGUOUS`: step `DEVELOPER_ATTENTION`, codes
     `['ROLE_TIMEOUT_WRITE_AMBIGUOUS']`, Task state unchanged;
   - `POLICY_INVALID`: step `REFUSED`, codes
     `['ROLE_TIMEOUT_POLICY_INVALID']`, Task state unchanged.
3. Advance with `timeout_retry=True`: classify again from the current store
   record and a fresh D6 check. Only `RETRY_READY` continues. Then it
   pre-checks, read-only, `worker_dispatch.evaluate` for the route's worker
   key and the worker quota gate of #2148 (UNVERIFIED location). A refusal
   returns step `REFUSED` with that gate's own code (for example
   `OWNER_PAUSED`, line 293) and writes nothing, so the retry is not used.
   Otherwise it makes the TaskStore transition (audited as
   `ROLE_TIMEOUT_RETRY`, section 3.3) and then the adapter dispatch through
   `authorize_dispatch(..., action='ROLE_TIMEOUT_RETRY')` (lines 401-451).
   It returns the normal dispatched step, and the drive goes on polling
   `WAITING_EXECUTOR` as today (lines 187-201). If the outcome of the
   transition or the launch is unknown, it returns step
   `ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN`; the drive returns it as an
   `*_UNKNOWN` step (lines 204-206) and nothing is replayed.
4. Drive: a new branch, placed before the generic codes return at
   task_drive.py lines 239-240, handles step `DEVELOPER_TIMEOUT` with code
   `ROLE_TIMEOUT_RETRY_READY`:
   - read `timeout_continuation_max()`; `None` returns step `REFUSED`, codes
     `['ROLE_TIMEOUT_POLICY_INVALID']`;
   - keep a local count of retry calls made in this drive. If it already
     equals the limit, return step `DEVELOPER_ATTENTION`, codes
     `['ROLE_TIMEOUT_DRIVE_GUARD']`. This only fires if the durable count and
     the drive disagree; it is a guard, not the cap;
   - otherwise call advance once with `timeout_retry=True`. The call uses
     the `max_steps` budget and counts as a completed step, like the merge
     call (lines 216-219).
   The drive docstring (line 51) is updated to say a clean role timeout is
   retried within the policy cap.
5. Restarting the drive never resets anything: the cap is counted from the
   durable audit (section 3.3), not from drive memory.

With the default limit of 1: first timeout, one retry; second timeout,
`STOPPED` with `ROLE_TIMEOUT_LIMIT` and a D4 notice. With limit 0 the first
timeout stops the same way. With limit 2 or 3 the stop comes after exactly
that many retries. This holds because each retry is counted once, although
it writes two audit entries (section 3.3, counting rule).

### 3.3 Cap and dispatch gate (worker_dispatch.py)

- New audit action `ROLE_TIMEOUT_RETRY` in `AUDIT_ACTIONS` (lines 49-53).
- New branch in `task_store_attempts`, placed with the Codex branch before
  the identity check (lines 622-636): old record classifies as a role
  timeout, new state `DEVELOPER_DISPATCHING`, and every
  `_DEVELOPER_IDENTITY_FIELDS` value (lines 473-477) unchanged, including
  `assignment_id`, `session_id` and `source_sha`. Without this branch the
  transition would be a `RETRY` and be refused `WORKER_REPLAY_FORBIDDEN`
  (lines 763-771).
- Checks in `authorize_task_store_change`, mirroring lines 709-752:
  - the expected record equals the old Developer, the scope fields are
    unchanged and the old record is a role timeout under section 3.1, else
    `WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION`;
  - `timeout_continuation_max()` is `None`: refuse
    `WORKER_ROLE_TIMEOUT_RETRY_POLICY_INVALID`;
  - count the logical timeout attempts already in this Task record (rule
    below), for any worker key, any decision and any assignment. At or above
    the limit: refuse `WORKER_ROLE_TIMEOUT_RETRY_LIMIT`. The Codex check at
    lines 739-742 uses the same count, so a Codex continuation and a role
    retry in one Task never exceed the limit together. This makes the Codex
    check stricter in a Task that already used a role retry.

#### Counting logical attempts, not audit entries

One retry writes two audit entries with the same action: the TaskStore
transition entry from `authorize_task_store_change` (written by
`audit_entry(decision, action, binding=binding)` at line 804, so it has no
`authorization_id`) and the adapter entry from `authorize_dispatch` (written
at lines 434-435, which keeps the supplied action and adds `authorization_id`
when a matching transition was found, lines 328-333 and 417-418). Counting
every entry with the action, as lines 739-742 do today, would count each
retry twice: with limit 2 the second retry would be refused after only one
retry, and limit 3 would allow only two. The cap therefore counts logical
attempts, once each, through one pure helper,
`role_timeout.count_timeout_attempts(audit)`, used by the classifier
(section 3.2), the role retry check and the Codex check.

Explicit attempt binding for the new action:

- `audit_entry` gains an optional keyword `retry_phase`. It is required for
  action `ROLE_TIMEOUT_RETRY`, must be `TRANSITION` or `ADAPTER`, and is
  refused with `WORKER_DISPATCH_ACTION_INVALID` for any other action or
  value, the same refusal line 308 uses for a bad action.
- `authorize_task_store_change` writes the transition entry with
  `retry_phase='TRANSITION'` and never with `authorization_id`.
- `authorize_dispatch` writes the adapter entry with
  `retry_phase='ADAPTER'`, whether it is allowed or blocked, and whether or
  not a transition was found.
- `_latest_transition_authorization` (lines 362-369), for this action only,
  also requires `retry_phase == 'TRANSITION'`, so an adapter entry can never
  be taken as a transition.

Count rule, per audit entry (any decision, any worker key, any assignment):

| Entry | Counts |
| --- | --- |
| `ROLE_TIMEOUT_RETRY`, `retry_phase` `TRANSITION`, no `authorization_id` | 1 |
| `ROLE_TIMEOUT_RETRY`, `retry_phase` `ADAPTER` | 0 |
| `ROLE_TIMEOUT_RETRY`, any other shape (phase missing or unknown, or `TRANSITION` with `authorization_id`) | 1 |
| `TIMEOUT_CONTINUATION` without `authorization_id` (Codex transition entry) | 1 |
| `TIMEOUT_CONTINUATION` with `authorization_id` (adapter entry) | 0 |
| any other action | 0 |

Why each row:

- A transition entry is the logical attempt. It counts once whatever its
  decision, so a BLOCKED transition still uses budget, as the Codex check
  does today (any decision, lines 739-742). This keeps the intended
  fail-closed treatment of blocked attempts: a gate refusal (for example a
  PAUSED race inside the authoritative re-check) is not free to repeat.
- An adapter entry is the second half of an attempt whose transition entry
  already counted, or a refused launch with no transition at all (for
  example `WORKER_DISPATCH_TRANSITION_NOT_FOUND`, line 424). In both cases
  no new attempt is made, so it counts 0, allowed or blocked.
- A shape that cannot be classified counts as one, never zero, in line with
  the rule that an unknown count counts as used (`_used`, repair_policy.py
  lines 67-69).
- For `TIMEOUT_CONTINUATION`, no new field is added; the existing
  `authorization_id` split is used. Whether the Codex adapter calls
  `authorize_dispatch` with action `TIMEOUT_CONTINUATION` is UNVERIFIED
  (the adapter was not read). If it does, today's Codex check has the same
  double count and the helper fixes it; if it does not, the Codex count is
  unchanged for a Task with no role retry. An adapter entry that found no
  transition has no `authorization_id` (lines 417-418, 328-333) and so
  counts 1 for this action: an over-count, which fails closed.

Result: with limit N (0 to 3, repair_policy.py line 21), exactly N retries
are allowed per Task record, each leaving two entries, and the next timeout
stops.
- `RECOVERY` (lines 776-792) refuses a record whose `error_code` is a role
  timeout code, with `WORKER_ROLE_TIMEOUT_NOT_RECOVERY`, so a timeout cannot
  get a second budget through recovery.
- Task chain: one Task record and every Developer assignment in it,
  including review repair and reassigned assignments. The audit is per Task
  record (lines 693-695), so this is the count the files support. Child and
  REPLAN Tasks have their own records and their own budget; they are already
  bounded by `child_max` and `replan_max` (repair_policy.py lines 151-171).

### 3.4 No retry after an ambiguous or partial write

The retry runs in the same D6 on the same `source_sha`, so it must start from
an untouched tree. A role timeout is retry-ready only if both are true:

- recorded evidence: `execution.diff_base_sha == developer.source_sha` and
  `execution.diff_sha256` equals the SHA-256 of an empty diff. Whether the
  four routes record these fields today is UNVERIFIED; a route that does not
  is `WRITE_AMBIGUOUS` until it does;
- fresh D6 check at retry time, read-only, through the existing registered D6
  helper (UNVERIFIED which module): the D6 HEAD equals `source_sha`, and
  there are no staged, unstaged or untracked changes.

Any change, any mismatch, any missing field or any failed read is
`WRITE_AMBIGUOUS`: no retry, step `DEVELOPER_ATTENTION`, code
`ROLE_TIMEOUT_WRITE_AMBIGUOUS`. The PM decides from there. This covers a
gw-agent run that applied some `apply_edit` calls and a Claude run that
committed before the time limit.

## 4. Interaction with existing caps

- D1 repairs: the retry is not a `REPAIR` audit action, does not call
  `admit_repair` (repair_policy.py lines 114-125) and does not change
  `repair_count`, review FAIL counts, test failure counts or Debug counts.
  It never resets any of them, because the assignment is unchanged and the
  per-assignment counts (worker_dispatch.py lines 779-787) and lineage counts
  are read from the same records as before.
- Same D6 and assignment: identity fields stay equal (section 3.3), so the
  audit binding of assignment, source SHA and contract digest
  (lines 340-354) is the same, and the adapter gate finds the
  `ROLE_TIMEOUT_RETRY` transition entry (lines 362-369, 416-431).
- A timeout of a review repair assignment is eligible under the same rules
  and uses the same Task budget. It is still only one repair.
- Worker quota FAIL (#2148, UNVERIFIED location) and a `PAUSED` worker
  (`OWNER_PAUSED`, line 293) still refuse. A missing Owner assignment row
  still refuses `OWNER_ASSIGNMENT_REQUIRED` (line 292). The drive pre-check
  writes nothing on these refusals; the authoritative re-check inside
  `authorize_task_store_change` and `authorize_dispatch` still runs, so a
  race where the switch changes in between still refuses.
- Audit size: a retry adds two entries (transition and adapter) but counts
  as one logical attempt (section 3.3). The cap of 3 adds at most six
  entries, far below `MAX_AUDIT_ENTRIES` (line 21); the existing limit check
  (lines 694-695) still applies.
- The GitHub quota stop (task_drive.py lines 29-44) and the code-tree stop
  (lines 86-102) are unchanged.

## 5. Fixed codes

Drive and advance results:

- `ROLE_TIMEOUT_RETRY_READY`: clean timeout, retry allowed (not a stop when
  the drive acts on it).
- `ROLE_TIMEOUT_LIMIT`: the one stop code for a timeout after the budget;
  Task `STOPPED`, D4 notice.
- `ROLE_TIMEOUT_WRITE_AMBIGUOUS`: possible write; no retry; PM stop.
- `ROLE_TIMEOUT_POLICY_INVALID`: policy unreadable; no retry; PM stop.
- `ROLE_TIMEOUT_DRIVE_GUARD`: drive and durable count disagree; PM stop.
- `ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN` (step): retry write or launch outcome
  unknown; never replayed.

Dispatch gate refusals:

- `WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION`
- `WORKER_ROLE_TIMEOUT_RETRY_POLICY_INVALID`
- `WORKER_ROLE_TIMEOUT_RETRY_LIMIT`
- `WORKER_ROLE_TIMEOUT_NOT_RECOVERY`

Existing codes passed through unchanged: `OWNER_PAUSED`,
`OWNER_ASSIGNMENT_REQUIRED`, `WORKER_POLICY_*`, the #2148 quota code
(UNVERIFIED name), `GITHUB_QUOTA_LOW`, `LLMTOOL_SOURCE_CHANGED`,
`LLMTOOL_SOURCE_UNKNOWN`.

## 6. Regression tests

All tests use made-up Task records with hex ids and route names only. No
password-, API-key-, token- or other credential-shaped strings. Policy,
quota gate, D6 check, `evaluate` and advance are injected or monkeypatched;
no test disables or weakens a production check.

Attempt count (`role_timeout.count_timeout_attempts`, `audit_entry`):

1. One full retry (an ALLOWED `TRANSITION` entry and its ALLOWED `ADAPTER`
   entry) counts 1, not 2.
2. A BLOCKED `TRANSITION` entry counts 1. A BLOCKED `ADAPTER` entry counts 0,
   for each of `WORKER_DISPATCH_AUTHORIZATION_CONSUMED`,
   `WORKER_DISPATCH_TRANSITION_NOT_FOUND` and `OWNER_PAUSED`.
3. A `ROLE_TIMEOUT_RETRY` entry with no `retry_phase`, an unknown
   `retry_phase`, or `TRANSITION` together with `authorization_id` counts 1.
4. A `TIMEOUT_CONTINUATION` entry without `authorization_id` counts 1, one
   with `authorization_id` counts 0; entries with any other action count 0.
5. `audit_entry` refuses `WORKER_DISPATCH_ACTION_INVALID` for
   `ROLE_TIMEOUT_RETRY` without `retry_phase`, for an unknown `retry_phase`
   value, and for `retry_phase` on any other action.

Classifier (`role_timeout.classify`):

6. Each route's exact timeout code with clean evidence gives `RETRY_READY`.
7. A wrong-route code (for example the agy code on a claude record) gives
   `NOT_TIMEOUT`.
8. A quota code, an auth code, a crash, the OpenCode step limit and
   `CODEX_TIMEOUT_WITH_CHANGES` each give `NOT_TIMEOUT`.
9. `cleanup_status` not `CONFIRMED` gives `WRITE_AMBIGUOUS`.
10. Non-empty diff digest, `diff_base_sha` mismatch, missing diff fields,
    D6 HEAD moved, untracked file, staged change and a failed D6 read each
    give `WRITE_AMBIGUOUS`.
11. A `result_head` or a committed carry gives `WRITE_AMBIGUOUS`.
12. Limit `None` gives `POLICY_INVALID`; limit 0 gives `LIMIT` on the first
    timeout; limit 1 gives `LIMIT` after one full retry (two entries).
    Limit 2: `RETRY_READY` after one full retry, `LIMIT` after two.
    Limit 3: `RETRY_READY` after two full retries, `LIMIT` after three.

Dispatch gate (`worker_dispatch`):

13. A same-assignment transition from a role timeout to dispatching is
    classified `ROLE_TIMEOUT_RETRY`, not `RETRY`, and is allowed once at
    limit 1; its audit entry has `retry_phase` `TRANSITION` and no
    `authorization_id`.
14. A second one in the same Task at limit 1 is refused
    `WORKER_ROLE_TIMEOUT_RETRY_LIMIT`, also when the first was on another
    assignment or another worker key, and also when the first transition
    entry was BLOCKED.
15. Limit 2: transition, adapter, transition, adapter all ALLOWED (two
    retries, four entries); the third transition is refused
    `WORKER_ROLE_TIMEOUT_RETRY_LIMIT`.
16. Limit 3: three full retries (six entries) are ALLOWED; the fourth
    transition is refused `WORKER_ROLE_TIMEOUT_RETRY_LIMIT`.
17. One Codex `TIMEOUT_CONTINUATION` attempt uses the shared budget: a role
    retry is then refused at limit 1, and the reverse. At limit 2, one Codex
    attempt and one role retry are both allowed.
18. Unreadable policy refuses `WORKER_ROLE_TIMEOUT_RETRY_POLICY_INVALID`.
19. A changed identity field (assignment, session, source SHA) or scope field
    refuses `WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION`.
20. A `PAUSED` worker refuses `OWNER_PAUSED`; a missing Owner row refuses
    `OWNER_ASSIGNMENT_REQUIRED`.
21. `RECOVERY` of a role-timeout record refuses
    `WORKER_ROLE_TIMEOUT_NOT_RECOVERY`.
22. `authorize_dispatch(action='ROLE_TIMEOUT_RETRY')` passes once, writes
    `retry_phase` `ADAPTER` with the transition's `authorization_id`, and
    then refuses `WORKER_DISPATCH_AUTHORIZATION_CONSUMED`.
23. A hand-built ALLOWED `ROLE_TIMEOUT_RETRY` entry with `retry_phase`
    `ADAPTER` and no `authorization_id` is never returned by
    `_latest_transition_authorization`, so it cannot authorize a launch.
24. Existing Codex continuation tests still pass unchanged.

Advance:

25. Without the flag, each classifier outcome maps to the step and code in
    section 3.2, and only `LIMIT` changes Task state (to `STOPPED`).
26. `LIMIT` produces the D4 notice through the existing path, once.
27. With the flag, a quota FAIL or `OWNER_PAUSED` pre-check returns
    `REFUSED` with that code and writes no audit entry.
28. With the flag, the retry does not change repair, review FAIL, test
    failure or Debug counts.
29. An unknown transition or launch outcome returns
    `ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN`, and a second call does not replay.

Drive:

30. Timeout, retry, then success: one `timeout_retry=True` call, then
    normal polling to the next step.
31. Timeout, retry, timeout at limit 1: drive returns `STOPPED` with
    `ROLE_TIMEOUT_LIMIT`.
32. Limit 2 and limit 3, every run timing out: exactly 2 and exactly 3
    `timeout_retry=True` calls, then `STOPPED` with `ROLE_TIMEOUT_LIMIT`;
    the guard code is not hit.
33. Policy unreadable at the drive: `REFUSED` with
    `ROLE_TIMEOUT_POLICY_INVALID`, no advance call with the flag.
34. Advance reports `RETRY_READY` again after the drive already retried up
    to the limit: `ROLE_TIMEOUT_DRIVE_GUARD`, no further call.
35. `WRITE_AMBIGUOUS`, `POLICY_INVALID` and `OUTCOME_UNKNOWN` results are
    returned without a retry call.
36. The retry call counts against `max_steps`; a budget of exactly the
    calls used ends in `MAX_STEPS` without an extra call.
37. Code-tree change during a retry still returns `RESTART_REQUIRED`.
38. Existing drive tests still pass unchanged.

## 7. PR split

Three small PRs, in order. Each is reviewed and merged before the next.

PR 1: classifier and dispatch gate (not wired).
- `tools/llmtool/llmtool/role_timeout.py` (new): route code table,
  `count_timeout_attempts` and `classify`, pure.
- `tools/llmtool/llmtool/worker_dispatch.py`: `ROLE_TIMEOUT_RETRY` action,
  the `retry_phase` keyword in `audit_entry`, the phase in
  `authorize_task_store_change` and `authorize_dispatch`, the phase check in
  `_latest_transition_authorization`, the `task_store_attempts` branch, the
  checks and the shared logical count (also used by the Codex check), the
  `RECOVERY` refusal.
- The TaskStore module only if it validates audit entry keys and refuses
  `retry_phase` (UNVERIFIED; name it in the PR if touched).
- Tests: new count, classifier and dispatch-gate test files under
  `tools/llmtool/tests/` (exact names follow the existing convention,
  UNVERIFIED). Tests 1-24.
- Before coding, confirm the four route timeout code strings and the
  execution fields from the adapters; record them in this doc.

PR 2: advance step.
- `tools/llmtool/llmtool/task_advance.py` (path from the import at
  task_drive.py line 7): the no-flag outcomes, the `timeout_retry=True`
  step, the read-only pre-checks, `STOPPED` plus D4 notice, the outcome
  unknown step.
- The TaskStore module only if the store refuses the transition shape
  (UNVERIFIED; name it in the PR if touched).
- Tests: advance tests 25-29.

PR 3: drive wiring.
- `tools/llmtool/llmtool/task_drive.py`: the `DEVELOPER_TIMEOUT` branch, the
  local guard, the docstring update.
- Tests: drive tests 30-38.
- This doc: mark row 41 delivered, with the merged PR numbers.

## 8. Open points

- Exact timeout code strings and execution fields per route (UNVERIFIED,
  PR 1 confirms before code).
- Location and code of the #2148 worker quota gate and of the D4 notice
  path (UNVERIFIED, PR 2 confirms).
- Whether the current `policy.json` sets `timeout_continuation_max`
  (UNVERIFIED; absent means 1).
