# #2188 Start-failure retry: design

Status: design only (#1502 WP-F unblock). v2 repairs codex-reviewer v1 FAIL (F1 dispatch gate, F2 repair packet,
F3 PR disposition). Source read: `90b03dde`. Read: `task_submission_cli.py`, `task_store.py`, `task_advance.py`,
`review_repair.py`, `lineage_counts.py`, `worker_dispatch.py`, `developer_routes/claude.py`. Not read:
`developer_routes/__init__.py` (`run_route`), `source_freshness.py`, the PM helper scripts, the sensitive-paths registry
and the open PRs. Facts from those are marked (not read).

## 1. Current behaviour

- **Duplicate-dispatch refusal.** The Claude delegate refuses a Task whose `developer` is not `None`
  (`task_submission_cli.py:5818`–`:5819`). Its first write is a CAS expecting `developer: None, completion: None`
  (`:5931`–`:5935`). TaskStore refuses to clear a `developer` (`WORKER_ASSIGNMENT_CLEAR_FORBIDDEN`, `task_store.py:5850`–`:5878`).
- **The dispatch gate refuses every replacement of a BLOCKED developer.** Each developer write runs
  `worker_dispatch.authorize_task_store_change` (`task_store.py:5879`–`:5886`). A new `assignment_id` changes an identity
  field (`worker_dispatch.py:493`–`:497`, `:551`–`:556`), so the write is `REASSIGN` (`:750`–`:753`). `REASSIGN` needs an
  old state of `DEVELOPER_PENDING` and refuses `WORKER_REASSIGN_PRECONDITION` (`:919`–`:925`). The same assignment
  moved back to PENDING is `RETRY` (`:755`–`:762`), and `RETRY` allows no old state (`WORKER_REPLAY_FORBIDDEN`, `:881`–`:889`).
  A refusal still writes its BLOCKED audit entry (`task_store.py:5880`–`:5886`).
- **What a pre-start failure stores (Claude).** A `RouteError` with no route result stores `state='BLOCKED'`, the route code,
  `provider_type`/`model`, no artifact, and `execution` only when the route gave execution facts (`:6026`–`:6047`, `:6061`).
  Route codes raised before process output: `CLAUDE_PROFILE_INVALID`, `CLAUDE_ENV_INVALID`, `CLAUDE_REQUEST_INVALID`,
  `CLAUDE_CONFIG_INVALID` (`developer_routes/claude.py:68`, `:90`, `:103`–`:109`). Whether `run_route` raises them
  before the spawn is UNKNOWN (not read).
- **Lineage.** `record-pm --continues P` refuses when P is `child` or `replan` (`lineage_counts.py:299`–`:301`) or already
  has a successor (`:304`–`:314`). One REPLAN per lineage (`task_submission_cli.py:1646`–`:1650`). So a REPLAN Task whose
  delegate failed to start can be neither continued nor replaced. The fork rule stays.
- **Repair Tasks.** The claim lives on the root Task, names the repair Task by `repair_task_id`, and binds
  `packet_sha256` (`review_repair.py:846`–`:856`). The digest covers the packet *before* repair 2's High Debug fields
  are added (`:749`–`:753`, then `:827`–`:830`). The packet itself is stored nowhere. When the delegate returns (a start
  failure returns 1, `task_submission_cli.py:6063`) the claim becomes `DISPATCHED` (`review_repair.py:919`). `REPAIR_RETRY`
  marks it `FAILED` and appends a claim, which spends a repair (`:857`–`:865`).
- **Repair cap.** `_repair_run_stamp` counts a Claude start failure with `execution: None` as a run
  (`task_submission_cli.py:7410`–`:7453`). `_check_delegate_repair_cap` skips the Task being delegated (`:7358`–`:7360`).
  Repair #2 needs a root cause. A packet with `high_debug_report_sha256` satisfies it through `_check_repair_packet_rca`
  (`:7484`–`:7486`, `:7292`–`:7325`).

## 2. Start failure: definition

A Task's stored `developer` D is a **start failure** only when every check passes. Unknown, malformed or failing means
"not a start failure" (fail closed). Checks 1–5 read the record only. Check 6 reads the live D6.

1. `D.provider_profile == 'claude'`, `D.backend_session_semantics == 'CLI_REPORTED_SESSION_ID'`, `D.state == 'BLOCKED'`.
2. `D.error_code` is in `START_FAILURE_CODES` (the four codes in §1). PR 1 confirms in `run_route` that each is raised
   before the spawn. A code that can follow a spawn is dropped (SPEC_MISMATCH if none remain).
3. `D.artifact`, `artifact_sha256` and `usage` are `None`. `progress`, `no_changes_diagnosis` and `blocked_reason` are
   absent, and `changed_files` is absent or `[]`.
4. `D.execution` is `None`, or it has `native_session_id`, `process_exit_code` and `stdout_sha256` all `None` and
   `cleanup_status == 'CONFIRMED'`.
5. `completion.state == 'WAITING_EXECUTOR'`, `completion.evidence.result_head is None`. No `completion_events`,
   `review_repair_claims`, `merge_attempt`, `abandonment` or `published_pr`.
6. `_d6_unchanged_probe(...)(D.source_sha, write_paths=D.write_paths)` (`:7383`–`:7407`) returns `True`.

**Never a start failure:** any `DEVELOPER_*` state. Also the codes `CLAUDE_RESULT_UNKNOWN`, `CLAUDE_NO_CHANGES`,
`CLAUDE_WRITE_SCOPE_VIOLATION`, `CLAUDE_OUTPUT_TOO_LARGE`, `CLAUDE_OUTPUT_INVALID`, `CLAUDE_STATUS_FAILED`,
`CLAUDE_QUOTA_EXHAUSTED` and `CLAUDE_D6_BINDING_CHANGED` (`:5993`, raised after a run too). An Owner denial
(`CLAUDE_DISPATCH_DENIED`, `OWNER_DISPATCH_DENIED`) and a non-Claude route are never one either.
A refusal before the CAS (`LLMTOOL_SOURCE_STALE`, D6, gate, chain) leaves `developer: None`, so the Task can simply be
delegated again. That case is not a start failure.

## 3. Retry: entry, write and dispatch gate

- **Entry:** `llmtool task retry-start TASK --executor-task D6 --pm-session S` (caller check as at `:7980`–`:7993`). It
  takes no scope arguments. It rebuilds the request from D and the Task: route, `read_paths`, `write_paths`,
  `owner_assignment_ref` and `egress_basis` from D. `acceptance` comes from `pm_assignment` and must equal D's.
  For a repair Task, the packet is loaded as §4 says.
- **Order:** caller, Owner policy and quota checks, then every refusal of `run_task_claude_delegate` except `:5818`. Then,
  in `_delegate_chain_gate` (`:7614`–`:7659`, chain locks held), the Task is re-read and checks 1–6 run on that snapshot.
  For a repair Task, §4 also runs. The CAS runs last. `run_task_claude_delegate` gains an `expected_failed_developer`
  argument. With it, `:5818` accepts `previous == D` only, and the CAS (`:5931`–`:5935`) expects
  `{schema_version: 2, state: PM_RESPONDED, developer: D, completion: C}`, where C is the stored completion. The fresh
  completion built at `:5915` must equal C, else `START_RETRY_D6_CHANGED`. The CAS writes `developer` and
  `start_failures` and leaves `completion` unchanged. It stays `d6_bind_compare_and_update`. A BLOCKED developer holds
  no D6 (`task_store.py:3963`–`:3967`), so the PENDING one takes it again under the binding lock (`:5899`–`:5903`). That
  lock refuses a D6 that another live Task now holds, and `expected` reaches the gate (`:4166`–`:4170`).
- **New developer:** built as at `:5898`–`:5914` (`DEVELOPER_PENDING`, new `assignment_id` and `session_id`,
  `provider_type`/`model` `None`). `source_sha`, scope, `executor_task_id` and `owner_assignment_ref` equal D's.
- **`start_failures`** (new top-level field, append-only, at most 2): `{failed_developer: D, record_sha256, retry_assignment_id,
  d6_head, retried_at, pm_session_id}`.
- **Gate action `START_RETRY` (`worker_dispatch.py`, F1).**
  - Added to `AUDIT_ACTIONS` (`:50`–`:54`) and to the stored-audit action list (`task_store.py:2362`–`:2365`).
  - `task_store_attempts` classifies it after the role-timeout branch and before `REASSIGN` (`:743`–`:753`): old
    `provider_profile == 'claude'`, old `state == 'BLOCKED'`, new `state == 'DEVELOPER_PENDING'` gives
    `[('claude-developer', 'START_RETRY')]`. Today every such write is refused (§1), so the new branch removes no refusal.
  - `authorize_task_store_change` runs `evaluate` as usual (`:820`–`:825`), then `_start_retry_valid(current, changes,
    expected, old, new)`, modelled on `_role_timeout_retry_valid` (`:633`–`:663`). It requires all of these:
    - `expected['developer'] == old` and `expected['completion'] == current['completion']`.
    - `start_failure.record_checks(current)` passes checks 1–5.
    - `set(changes) == {'developer', 'start_failures'}`.
    - The new developer is PENDING with `provider_type`, `model`, `artifact`, `execution`, `usage` and `error_code` `None`.
    - Its `assignment_id` is 32 hex and differs from D's and every archived one. Its `session_id` differs from D's.
    - It equals D in `selection_source`, `provider_profile`, `backend_session_semantics`, `source_sha`,
      `source_sha_binding`, `owner_assignment_ref`, `executor_task_id`, `read_paths`, `write_paths`, `acceptance`
      and `egress_basis`.
    - `changes['start_failures']` is the stored list plus one entry with `failed_developer == old`,
      `retry_assignment_id == new.assignment_id` and `record_sha256 == current.record_sha256`.
    - `_assignment_audit_binding(current, new)` (`:355`–`:369`) is not `None`.
  - Any miss refuses `WORKER_START_RETRY_PRECONDITION`. A Task with `MAX_START_RETRIES_PER_TASK` (2) earlier ALLOWED
    `START_RETRY` entries refuses `WORKER_START_RETRY_LIMIT`. Both are audited like every denial (`task_store.py:5880`–`:5886`).
  - Live D6 check 6 stays in the CLI, as the role-timeout design keeps its fresh D6 check out of the gate (`:634`–`:636`).
- **After the CAS:** the existing PENDING→DISPATCHING write (`:5940`–`:5941`) is an initial provider binding
  (`worker_dispatch.py:508`–`:521`). It is audited `DISPATCH` (`:764`–`:772`) under the new assignment's binding. So the
  adapter's transition lookup (`:377`–`:386`, `:434`–`:448`) works as on a first delegate, and nothing else changes.
- **TaskStore rule (`task_store.py`).** `start_failures` and `repair_packet` (§4) join `FIELDS` (`:101`–`:108`). They are
  validated by the optional-field pattern (`:2029`–`:2051`). `start_failures` changes only by that one append, in a write
  whose stored `developer` equals the new entry's `failed_developer`. Otherwise the store refuses `START_RETRY_INVALID`.
  Clearing `developer` stays forbidden (`:5850`).
- **CLI refusals (no write):** `START_RETRY_NOT_START_FAILURE` (with the check number), `START_RETRY_D6_CHANGED`,
  `START_RETRY_LIMIT_REACHED` (a third failure goes to the PM), `START_RETRY_CONFLICT`, plus the §4 codes.
- **Applies to:** a first-delegate Task, a REPLAN Task (`lineage` untouched, so `admit_replan` still sees one REPLAN),
  and a repair Task (§4).

## 4. Repair Task retry (F2)

- **Persisted packet.** From PR 2, the delegate's first CAS (`:5931`–`:5935`) also writes `repair_packet`, the exact
  `_validate_repair_packet` output (`:5261`–`:5286`, `:5875`–`:5877`) sent to the provider, High Debug fields included.
  - The store accepts `repair_packet` only in the write that sets `developer` from `None`, and never changes or removes it
    (`REPAIR_PACKET_IMMUTABLE`). Its shape is the same as `:5261`–`:5286`, with canonical JSON ≤ 16384 bytes.
  - Its text is reviewer or High Debug text the store already keeps (`task_store.py:2244`, `high_debug.report`).
  - A repair Task recorded before PR 2 has no `repair_packet`. Its retry refuses `START_RETRY_REPAIR_PACKET_UNAVAILABLE`,
    and the PM uses `REPAIR_RETRY`.
- **Root claim lookup.** As in `review_repair.py:713`–`:734`, retry-start reads every Task in `store.task_ids()` and
  collects each `review_repair_claims` entry whose `repair_task_id` equals the retried Task T.
  - Exactly one root R with exactly one claim C is required. A read error, or zero or several matches, refuses
    `START_RETRY_REPAIR_ROOT_UNKNOWN`.
  - C must be `DISPATCHED` and the last claim of its family in R. Its `executor_task` must equal `D.executor_task_id`,
    and its `head_sha` must equal `D.source_sha`. Otherwise the retry refuses `START_RETRY_REPAIR_CLAIM_INVALID`.
  - `repair_packet` present while no claim names T also refuses `START_RETRY_REPAIR_CLAIM_INVALID`.
- **Packet binding** (`start_failure.repair_packet_matches(P, C, R)`, pure). Any miss refuses
  `START_RETRY_REPAIR_PACKET_MISMATCH`.
  - P's identity fields match C and R: `P.task == R.task_id`, `P.pr == C.pr`, `P.executor_task == C.executor_task` and
    `P.failed_head == C.head_sha`. A review claim has `comment_id` set: `P.version == C.version` and
    `P.verdict_comment_id == C.comment_id`. A test claim has `comment_id: None` (`:851`): both are 0.
  - **Base digest.** B is P without `high_debug_report_untrusted_data` and `high_debug_report_sha256`.
    - A CI packet is a test packet whose excerpt starts with `CI_CHECK_FAILURE ` (the test at `:251`–`:252`). Its
      header (`:454`–`:455`) must parse exactly, with a check name in `CI_REPAIR_CHECKS`. The identity digest rebuilt
      from it (`:469`–`:474`) must equal `C.packet_sha256`.
    - Every other packet (review, EC2 remote test): `sha256(canonical(B)) == C.packet_sha256` (`:751`–`:753`).
  - **High Debug binding (repair 2 and 3).**
    - If C has `high_debug_report_sha256`, then `P.high_debug_report_sha256` equals it. R must also hold an entry E with
      `family == C.family`, `no_pr` not `True`, `status == 'RCA_CONFIRMED'`, `examined_sha == C.head_sha` and
      `report_sha256 == C.high_debug_report_sha256`. This is the store's own binding (`task_store.py:2299`–`:2310`).
    - `P.high_debug_report_untrusted_data == E.report[:6000]` (`review_repair.py:828`–`:830`).
    - If C has no such field, P has neither High Debug field.
- **Dispatch.** The bound P is passed as `repair_packet`, so the delegate's `_check_repair_rca` takes the packet path
  (`:7484`–`:7486`) and re-checks R's High Debug entry (`:7292`–`:7325`).
- **No repair claim is added.** The retry writes T only. R, C (still `DISPATCHED`, same `repair_task_id`) and every count
  stay unchanged.

## 5. Counts and gates unchanged

- No claim, Task or `continues` link is created, so `lineage_counts`, `chain_review_repairs`, `_review_gate` and the auto
  limit read what they read before. `start_failures` is read by no counter.
- One `developer` slot per Task, so `_repair_run_stamp` counts T at most once. The retried run counts as a first run would,
  and the failed start already counted for other Tasks. T's own cap check skips T (`:7358`–`:7360`).
- `DELEGATE_REPAIR_LIMIT_REACHED`, repair #2's RCA requirement, `REPAIR_RETRY` and REPLAN admission stay unchanged.

## 6. Successor left by a refused delegate

- **Refused before the CAS:** S has `developer: None` and counts nothing (`:7417`). `CONTINUES_PREDECESSOR_HAS_SUCCESSOR`
  is correct, and the PM re-delegates S. PR 3 makes that refusal name S when S is empty (no `developer`, `completion`,
  claims or lineage).
- **Refused after the CAS with a start failure:** `task retry-start S`. Otherwise the ordinary flow applies; no fork.

## 7. Ordered PRs

Each PR changes llmtool gate code. The PM checks its files against the rule-file set of #2199 §3.6 and gets that
design's security review where it applies (registry not read). Tests follow Owner #763 A: no credential-shaped
strings. They inject the route, the probe and `_screen_text` outcomes, and never weaken a check.

1. **Gate, store and pure predicate (not wired).** Built from `main`.
   - Files: new `tools/llmtool/llmtool/start_failure.py` (`START_FAILURE_CODES`, `record_checks`, `classify(record,
     d6_unchanged)`, `repair_packet_matches`). `worker_dispatch.py` (`START_RETRY`, `_start_retry_valid`,
     `MAX_START_RETRIES_PER_TASK`). `task_store.py` (action list, `start_failures`, `repair_packet`, §3 and §4 rules).
   - Confirms `START_FAILURE_CODES` in `developer_routes/__init__.py`.
   - Tests:
     - `tests/test_start_failure.py`: each §2 check, each never-start code, a raising probe, and each packet-binding
       miss (base digest, CI identity, High Debug digest, report text, missing E).
     - `tests/test_worker_dispatch_start_retry.py` (gate refusals): a valid retry is ALLOWED and audited under the new
       binding. Each of these refuses `WORKER_START_RETRY_PRECONDITION`: unbound `expected`, a non-start code, a native
       session, a reused `assignment_id`, a changed scope field, `completion` in the changes, and a wrong archive entry.
       A third retry refuses `WORKER_START_RETRY_LIMIT`, and a PAUSED policy refuses with its own code. A non-Claude
       BLOCKED→PENDING still refuses `WORKER_REASSIGN_PRECONDITION`. The following PENDING→DISPATCHING write is
       `DISPATCH`, and `authorize_dispatch` finds it.
     - `tests/test_task_store_start_failures.py`: append-only, max 2, archive mismatch refused, clear still forbidden,
       a refused write keeps its BLOCKED audit entry, `repair_packet` write-once.
2. **Wiring.**
   - Files: `task_submission_cli.py` (`repair_packet` persisted at the first CAS, `run_task_retry_start` and parser,
     `expected_failed_developer`).
   - Tests: `tests/test_task_retry_start.py`: first delegate, REPLAN (lineage unchanged), dirty D6, native session,
     limit, concurrent write, a repair-1 retry, root-claim refusals (none, two, `UNKNOWN` or `FAILED` claim), and a
     pre-PR 2 repair Task (packet unavailable).
   - **Successful repair-2 retry:** root R holds attempt 2 `DISPATCHED` with `high_debug_report_sha256` H and an
     `RCA_CONFIRMED` entry with digest H at the failed head. T holds the persisted packet and a `CLAUDE_CONFIG_INVALID`
     start failure. With the probe and the route runner injected, the retry is ALLOWED and the injected route receives
     the packet with H. R's claims are byte-identical afterwards, `lineage_counts` is unchanged, and T ends
     `DEVELOPER_RESPONDED`. A packet with the wrong H refuses `START_RETRY_REPAIR_PACKET_MISMATCH`.
3. **Advance and continuation diagnostics.** `task_advance.py` (`derive_repair_state` returns `START_FAILED` for checks
   1–5, and step `DEVELOPER_START_FAILED` stops for the PM with no automatic retry). `lineage_counts.py` (the refusal
   names an empty successor). Tests: the advance step and `check_continues` cases.

**Open PRs (PM disposition, final):**

- **#2215 and #2217:** closed unmerged. Replaced by PR 1, built fresh from `main`. None of their commits are reused, and
  their remaining scope is carried by PRs 2–3 of this plan.
- **#2197:** keep. This plan does not touch it. It follows #2199 §5 migration.
