# WP-F design: delivery evidence gate (#1502)

Status: **proposal** (docs only). Not a review, an acceptance or runtime evidence.

- Source: Task source `7384a63e`. Every line cited below from a file in this Task's read list was read at that commit for this revision. Python paths are under `tools/llmtool/llmtool/`. Tests go in `tools/llmtool/tests/`.
- Marks per `CLAUDE.md:5`: `現行` (current), `待實作（WP-F）` (decided, ships with WP-F), `待後續` (decided, out of scope). `UNVERIFIED` marks only files outside this Task's read list: `review_dispatch.py`, `review_repair.py`, `high_debug.py`, `providers/cli_lifecycle.py`, the design `wpf-role-rule-cards.md`, and existing tests.
- History.
  - Repair 5 fixed #1997 v2 F1: the claim follows a per-route **delivery boundary**, not the process start.
  - Repair 6 (#2136, D3 child) fixes two findings from the v1 review of Repair 5:
    - F1: on Claude and AGY, the process that `Popen` starts is the sandbox wrapper, not the provider. The boundary moves to the provider's own result (§1.2, §2.2 step 5).
    - F2: a claim commit can raise after it has persisted. §2.2.1 defines that uncertain outcome.
  - Kept: reservation, then receipt, then claim; the TaskStore schema before any producer; updates that preserve `DELIVERED`; the OpenCode boundary; the PR order (§3).

## 1. Today (`現行`)

### 1.1 Rules and ledger

- `CLAUDE.md:9`: "[NOT LIVE]: delivery to sub-agent and PM runs (待後續), and missing evidence blocking protected transitions (待實作（WP-F）)." `CLAUDE.md:10`: hooks are defence in depth. The real gates are dispatch, `task complete` and merge.
- Design §3.1 and §6 (`wpf-role-rule-cards.md`, `UNVERIFIED` here): without matching evidence, a dispatch claim, `task complete`, a Reviewer claim or a Debug entry refuses.
- Ledger: private, hash-chained, per user under `Path.home()` (`skill_ledger.py:83-84`). Evidence levels are at `:42-43`; the ten binding fields are at `:44-45`. An append can raise after its line persists (`:9-10`).
- `pin_dispatch()` writes one immutable `DISPATCH_PIN` per Task/run/route from authenticated `main` (`:521-557`). A repeat call returns the first pin (`:532-534`). Only routes in the role-card table can pin (`:513`; `skill_manifest.py:50-55`).
- `record_card_delivery()` is the only writer of `CONTROLLER_DELIVERED` (`skill_ledger.py:560-638`). It derives the cards and their digest from the pin (`:603-630`). It refuses: `CALLER_DIGESTS_REFUSED`, `CALLER_BASE_REFUSED`, `DISPATCH_PIN_MISSING`, `ROLE_MISMATCH` (`:598-599`), `PIN_MISMATCH`, `MANIFEST_DIGEST_MISMATCH`. `record()` refuses that level (`:488-489`). `record_not_delivered()` stores a row with level `UNVERIFIED` (`:641-667`).
- `check()` refusal reasons include `NOT_FOUND`, `WRONG_RUN`, `WRONG_ROLE`, `MANIFEST_CHANGED`, `REVOKED`, `EXPIRED` and `PIN_MISMATCH` (`:711-782`). A row with level `UNVERIFIED` never verifies (`:752-753`). A row without a route is never re-validated (`:693-697`). The receipt TTL is 86400 s (`:560`).
- TaskStore validates the whole record on every write (`task_store.py:3553`, called from `_write` at `:3561`). The developer key set must be one exact shape, after removing the optional keys in `_DEVELOPER_OPTIONAL_FIELDS` (`:320`, `:2408-2411`). Dispatch takes the developer slot only from `developer: None` (`task_submission_cli.py:4956`, `:5443`, `:5918`). The OpenCode launch write copies the stored developer (`task_store.py:4079`).
- Gaps:
  - A failed receipt only prints `<PROVIDER>_SKILL_LEDGER_OUTCOME_UNKNOWN` (`task_submission_cli.py:4796-4819`).
  - No completion path reads the ledger. The only ledger calls are at `:4755`, `:4809` and `:4832`, none in `run_task_complete` (`:8410`) or `run_task_direct_result_event` (`:9250`).
  - The receipt binds the provider's reported session (`:4811`), not the Task's `developer.session_id`.
  - The hook observes and does not enforce by default (`claude_skill_hook.py:18-21`, `:47-48`, `:494-500`).

### 1.2 Where the prompt reaches the provider (F1)

| Route | Prompt channel | Today's receipt | Why process start is not delivery |
|---|---|---|---|
| Claude | argv after `--` (`developer_routes/claude.py:56-61`); stdin is `DEVNULL` (`developer_routes/base.py:344`) | After the run returns (`task_submission_cli.py:5969-5970`) | `on_launch` runs **before** `Popen` (`base.py:843-844`, `:853`). The process `Popen` starts is the wrapper, not the provider (below). |
| AGY | argv `-p` (`developer_routes/agy.py:44-47`) | After the run returns (`:5497-5498`), binding the verified `result.model` | Same as Claude |
| OpenCode | stdin pipe (`opencode_direct_developer.py:853-856`). No wrapper: `argv[0]` is the OpenCode executable (`:833-834`). | After the run returns (`:5017-5018`), and after an attempted failed run that reported a session (`:5020-5024`) | `launch_callback` (`:861-867`) runs before any stdin byte is written (`:873-891`). A `BrokenPipeError` or `OSError` sets `offset = len(payload)` (`:886-887`), so a failed write looks complete. |
| `gw-agent` | Route fields, no cards | `NOT_DELIVERED` row from `on_launch` (`:6374`), before spawn | Exempt. Append failures are ignored (`:4837-4839`). |

**The wrapper (F1).** On Claude and AGY, `run_route` builds `argv = prefix + route argv` (`base.py:842`).
- On macOS the write boundary's prefix is `[/usr/bin/sandbox-exec, -p, <profile>]` (`write_boundary.py:52`, `:194`, `:390-394`).
- On any other platform the boundary is unsupported, and the run refuses before spawn (`write_boundary.py:41-46`; `base.py:834-837`). Claude also refuses earlier, at `task_submission_cli.py:5844-5845`.
- So on every Claude and AGY run, `Popen` returning (`base.py:344-347`) proves only that `sandbox-exec` started with the prompt in its argv. The wrapper must then start the provider.
- If that second start fails, `Popen` has still returned. The run fails later (no provider result, non-zero exit), but a claim made at `Popen` is already committed.

The v2 design confirmed delivery "after the provider process starts". On OpenCode that point comes before the prompt is sent. Repair 5 moved the Claude and AGY boundary to `Popen` returning, which is the wrapper starting, not the provider. Both v2 F1 and v1 F1 hold against those boundaries.

### 1.3 Developer writers

| Writer | Code | Card route |
|---|---|---|
| Claude, AGY, OpenCode delegate | `:5779`, `:5324`, `:4860` | `_SKILL_CARD_ROUTES` (`:4734-4735`) |
| `gw-agent` delegate | `:6080` | None (exempt) |
| Codex direct, proposal delegate | `:6644`, `:7766-7781` | None. `codex-developer` is `PAUSED` (`CLAUDE.md:33`). |
| `task claude-bind` | `:2272-2361`: binds a Claude session that llmtool did not launch, with no provider call | None (Q8) |

## 2. Gate design (`待實作（WP-F）`)

### 2.1 Eligibility (one classifier, `skill_gate.py`)

Each transition reads the stored record, never caller input.

| Class | Condition | Needs |
|---|---|---|
| `RECEIPT_REQUIRED` | `developer.card_delivery.state == DELIVERED` and `route == _SKILL_CARD_ROUTES[provider_profile]` | `card_receipt_status` returns `VERIFIED` (§2.3) |
| `EXEMPT` | `gw-agent` record whose `card_delivery == {route: gw-agent, state: NOT_DELIVERED}` | `not_delivered_status` finds that run's row |
| `LEGACY_UNGATED` | No `card_delivery`, and `developer.created_at` (`:4929`, `:5419`, `:5894`, `:7780`) is before `SKILL_GATE_EPOCH` | Nothing |
| refuse | Anything else: `PREPARED`, no binding after the epoch, Codex, `claude-bind`, an unknown route, an unreadable ledger | — |

Codes: `SKILL_DELIVERY_RECORD_MISSING`, `_CLAIM_UNCOMMITTED` (`PREPARED`), `_EXEMPTION_INVALID`, `_EXEMPTION_MISSING`, `_ROUTE_UNSUPPORTED`, plus the §2.3 verifier codes.

### 2.2 Developer lifecycle: reserve, deliver, claim

1. **Pin and cards** (`現行`, `:4738-4793`), fail closed, before any store write.
2. **Prepare.** `prepare_card_delivery(entry, repo_root)` runs today's checks (`skill_ledger.py:583-630`). It appends a `CARD_PREPARED` row with level `UNVERIFIED` and returns the row's `prepared_id`. The row holds the binding:
   - Task, run, `developer.session_id` (`:4920`, `:5412`, `:5887`), agent, role `developer`;
   - provider, the requested model, repo, head;
   - prefix digest, route and pin.
3. **Reservation.** `card_preparation_status()` must match, else the command refuses with `SKILL_DELIVERY_PREPARATION_MISSING`: no store write, no launch. The creating write stores `card_delivery = {route, pin_commit, prefix_sha256, session_id, prepared_id, state: PREPARED}` in the same write as the developer. The duplicate-dispatch refusal stays (`:4960-4962`, `:5445-5446`, `:5920-5921`). `PREPARED` is not a claim: every protected transition refuses it.
4. **Pre-launch checks**, unchanged: `route_gate` (Claude `:5938-5950`, AGY `:5464-5475`) and OpenCode `prelaunch_validate` (`:4971-4977`). The Claude and AGY checks compare the whole developer (`:5928`, `:5453`), so the local copy carries the binding.
5. **Delivery boundary.** The route calls `on_delivered` once, at the first point where llmtool has evidence that the **provider itself** holds the whole prompt:

   | Route | Boundary | Not a boundary (no confirm; refuse) |
   |---|---|---|
   | Claude, AGY | **Provider result.** Inside `run_route`, after every provider-result check has passed and before the `RouteResult` is built (between `base.py:977` and `:978`). The checks: the process exited 0 (`:973-977`). Stdout parsed as the provider's own result with its session id: Claude `type: result` with a canonical UUID `session_id` (`claude.py:107-110`); AGY a valid `conversation_id` (`agy.py:84-87`). The status check passed (`base.py:959-964`; Claude needs `DONE`, `claude.py:166-168`). The provider's own model evidence equals the requested model (`base.py:965-972`): Claude `modelUsage` with output tokens (`claude.py:146-164`); AGY the model resolved in its log (`agy.py:99-103`), read from the provider's `--log-file` (`agy.py:47`; `base.py:877-889`). | `Popen` returned: only the wrapper started. The wrapper started but could not start the provider: there is no provider result or model evidence, so the run ends `<ROUTE>_OUTPUT_INVALID`, `_MODEL_UNVERIFIED` or `_EXIT_NONZERO`. Any other `RouteError` before `:978`: timeout, `RUNNER_FAILED` (`:863-865`), write-scope violation, quota, interruption. Pre-spawn refusals: no callback (`:721-850`). |
   | OpenCode | All `len(payload)` bytes accepted by `os.write`, and stdin closed without error (`opencode_direct_developer.py:883-890`). Called right after `key.fileobj.close()`. | Any write or close error: `OPENCODE_PROMPT_DELIVERY_INCOMPLETE`, replacing the swallow at `:886-887`. Stdin not finished at timeout (`:880-881`), output cap (`:902-904`) or `TOOL_DENIED` (`:916-917`): that route code. Loop ended with delivery not marked: `OPENCODE_PROMPT_DELIVERY_INCOMPLETE`. |

   - **Claude and AGY: what the boundary proves.** The provider executable ran inside the wrapper and finished a model turn for the requested model, from the argv the wrapper passed on.
     - The prompt is one element of that argv (`claude.py:56-61`; `agy.py:44-47`). `execve` takes an argv whole or fails.
     - llmtool does not observe the provider's argv. The boundary probes show, just before launch, that this wrapper passes its argv tail to the program it starts. They run `[sandbox-exec, -p, <same profile>, python3, -c, <probe>]` through the same runner. The allowed-write probe and the `/dev/null` probe pass only with exit 0 (`write_boundary.py:236-242`, `:378-385`), and exit 0 requires the probe program from that argv tail to have run.
     - A wrapper failure cannot pass this boundary: the wrapper cannot write the provider's result or the provider's model evidence.
   - **OpenCode: what the boundary proves.** The whole prompt is in the stdin pipe of the provider's own process. There is no wrapper (`opencode_direct_developer.py:833-834`, `:853`).
   - Neither boundary proves that the model read the cards.
   - **Claude and AGY confirm only after the provider has run.** Edits the provider made in the D6 are not a result until the result write, and that write needs `DELIVERED` (step 7). If the claim is refused, the edits stay unaccepted and there is no artifact, as on every `BLOCKED` path today.
   - `on_delivered` is a `run_route` keyword, and `run_route` calls it itself. It is never passed to a runner, so the boundary probes, which use the same runner (`base.py:829-830`), cannot fire it.
   - If the provider call returns without `on_delivered` having fired and committed, the CLI raises `<ROUTE>_DELIVERY_UNCONFIRMED` and stores no result. Example: an injected `route_runner` (`task_submission_cli.py:5488`, `:5961`) that ignores the hook.
   - `launch_callback` stays as it is and only records the pid (`:4992-5009`).
6. **Confirm and claim** inside `on_delivered`:
   - `confirm_card_delivery(prepared_id, task_id, run_id, session_id, repo_root)` re-derives the cards from the pin. It takes every other binding field from the prepared row, so the caller supplies no model, head or digest. It appends `CONTROLLER_DELIVERED` with `prepared_id`.
   - `card_receipt_status()` must then return `VERIFIED` for the stored binding.
   - Only then does TaskStore `commit_card_delivery()` move `PREPARED` to `DELIVERED` by compare-and-set. **That write is the dispatch claim.** It is called at most once per `prepared_id` and never replayed.
   - On success the caller refreshes its local developer copy: Claude `developer` (`:5924`), AGY `developer` (`:5449`), OpenCode `developer` and `expected_developer` (`:4965-4969`, `:5006`). Otherwise a later stale `PREPARED` write would be refused as a downgrade (§2.7).
   - If the confirm or the receipt check raises, the callback raises `SKILL_DELIVERY_*`. This includes an append that raised after its line persisted (`skill_ledger.py:9-10`). The commit is not called, and `state` stays `PREPARED`.
   - If `commit_card_delivery()` raises, its outcome is uncertain. §2.2.1 handles it.
   - Stopping:
     - OpenCode: the callback runs mid-run, so a raise leaves through `_reject` inside `with lifecycle` (`opencode_direct_developer.py:837`), which stops the child.
     - Claude and AGY: the provider has already exited (step 5), so nothing is left to stop. `run_route` returns no `RouteResult`.
     - The cleanup outcome is recorded as `cleanup_status` (`base.py:379`, `:707`; `opencode_direct_developer.py:929-932`). How `CLIProcessLifecycle` kills a process is `UNVERIFIED`.
   - The record keeps each route's existing post-spawn failure state, with `error_code` set to the code. No artifact is stored.
     - OpenCode: `DEVELOPER_OUTCOME_UNKNOWN` (`task_submission_cli.py:5025-5029`).
     - Claude and AGY: `BLOCKED` (`:6012`, `:5547`).
7. **Result check.** Before any result write, the stored record must be `DELIVERED` and the receipt must still be `VERIFIED`.
   - The result writes are Claude `:6056` and the committed carry (`:5985-5986`), AGY `:5572`, and OpenCode `:5153-5154`.
   - A revoked receipt, or a record that never left `PREPARED` (for example, a runner that never fired the hook), gives `DEVELOPER_OUTCOME_UNKNOWN`, `SKILL_DELIVERY_*` and no artifact.
   - The post-run receipt writes are removed: OpenCode `:5017-5018` and `:5020-5024`, AGY `:5497-5498`, Claude `:5969-5970`.

`gw-agent` skips steps 2 and 5–7.
- Its creating write (`:6248-6252`) stores the exemption.
- The `on_launch` row (`:6374`) becomes fail-closed: an append error raises a `RouteError`, which `run_route` passes through before spawn (`base.py:845-847`), so the run never launches.

Leftovers from a refusal:
- A refusal before the boundary leaves at most a `CARD_PREPARED` row and a `PREPARED` record.
- An orphan row comes from a lost duplicate race or from an append that persisted and then raised. It carries a session or `prepared_id` that no committed record stores.

### 2.2.1 Uncertain claim commit (F2)

**Today (`現行`).** `TaskStore._write` (`task_store.py:3559-3591`) runs in this order:
1. It writes and fsyncs a temporary file (`:3566-3571`).
2. It renames the temporary over the record (`:3577`; `_replace_at`, `:3594-3612`).
3. It fsyncs the directory (`:3578`).

An `OSError` from that directory fsync becomes `TASK_STORE_UNAVAILABLE` (`:3583-3584`), but the new record is already visible to readers. So a raise from `commit_card_delivery()` does not mean "not committed". Only the compare-and-set conflict, raised under the lock before any write (`:5411-5413`), certainly wrote nothing.

**Rule (`待實作（WP-F）`).** Any exception from `commit_card_delivery()` is an uncertain commit. It is handled in this order:

1. **No replay.** No code calls `commit_card_delivery()` again for that `prepared_id`: not the callback, the route, the CLI or recovery, and not after any reconcile result.
2. **Stop the provider.** The callback raises `SKILL_DELIVERY_COMMIT_UNCERTAIN`, and stopping follows §2.2 step 6. On OpenCode the child is stopped mid-run. On Claude and AGY the provider has already exited, and `run_route` returns no `RouteResult`. No path writes a result.
3. **Reconcile, read-only.** After the provider has stopped, the caller makes one `store.get(task_id)` (`task_store.py:3704-3711`) and compares the stored developer with its local copy:
   - `reconcile=DELIVERED`: same `assignment_id`, and `card_delivery` equals the expected binding with `state: DELIVERED`. The commit persisted.
   - `reconcile=PREPARED`: same `assignment_id`, and `card_delivery` equals the reservation (`state: PREPARED`, same `prepared_id`). The commit did not persist.
   - `reconcile=UNKNOWN`: the read raised, or anything else was found (another assignment, another binding, no developer).
4. **Record the outcome.**
   - `DELIVERED` or `PREPARED`:
     - The caller replaces its local developer (and OpenCode `expected_developer`) with the stored one.
     - It then writes the route's failure state (§2.2 step 6) with `error_code = SKILL_DELIVERY_COMMIT_UNCERTAIN`, by compare-and-set against the stored developer.
     - The write keeps the stored binding byte-for-byte (§2.7), so **a persisted `DELIVERED` is preserved** and never downgraded. No artifact is stored.
     - If that failure write raises, the command reports `failure_write=UNKNOWN`, exits non-zero and does not retry.
   - `UNKNOWN`:
     - The run makes no further store write, because a write from a stale copy could downgrade or overwrite what is stored.
     - The command exits non-zero and **reports the uncertainty**: `SKILL_DELIVERY_COMMIT_UNCERTAIN reconcile=UNKNOWN`, with Task, run, assignment and `prepared_id` only.
     - The run leaves the stored record as it is. For this assignment that is `DEVELOPER_DISPATCHING`, because the commit changes only `card_delivery`. The next dispatch refuses, or recovers without a commit:
       - Claude refuses replay (`task_submission_cli.py:5796-5798`).
       - AGY refuses with `DIRECT_CHILD_LIVENESS_UNKNOWN` (`:5334-5338`, `:6496-6498`).
       - OpenCode recovery checks that the child is dead, then writes `DEVELOPER_OUTCOME_UNKNOWN` from the stored developer (`:6509-6527`), so it keeps whatever binding is stored.
   - Every case prints its `reconcile=` value.
5. **Why the run always fails.** The store has just failed a write, and a `DELIVERED` record whose directory fsync failed may not survive a host crash. Both outcomes fail closed:
   - A record that reverts to `PREPARED` passes no gate (§2.1).
   - A `DELIVERED` record in a failure state has no artifact, so the direct result path refuses it (`task_submission_cli.py:9261-9269`), as it refuses every failure state today. `DELIVERED` is still true there: the boundary fired, so the provider got the cards.
   - The `CONTROLLER_DELIVERED` row stays in the ledger. It never satisfies a gate for a `PREPARED` record. The remedy is a new run (§2.7).

### 2.3 Verifiers (read-only)

- `card_receipt_status(task_id, run_id, role, route, session_id, prefix_sha256, pin_commit, prepared_id)` returns `VERIFIED` only for a `CONTROLLER_DELIVERED` row that meets all of these:
  - it is not revoked and matches every argument;
  - its `CARD_PREPARED` row comes before it;
  - its pin is still in the chain (`skill_ledger.py:690-703`).

  Expiry is ignored (Q2).
- `card_preparation_status(...)` is used at step 3 only. `not_delivered_status(task_id, run_id, role, route)` checks an exemption.
- Codes: `SKILL_DELIVERY_RECEIPT_MISSING`, `_PREPARATION_MISSING`, `_WRONG_RUN`, `_WRONG_SESSION`, `_WRONG_ROLE`, `_DIGEST_MISMATCH`, `_PIN_MISMATCH`, `_REVOKED`, `_LEDGER_UNREADABLE` (lock, read or broken chain).
- Verifiers append nothing. Like `check()`, they may repair a torn append under the lock (`:7-9`, `:679-680`).

### 2.4 Protected transitions

| Transition | Check point | Evidence | Refusal |
|---|---|---|---|
| Dispatch claim | §2.2 step 6, after the boundary, before `commit_card_delivery()` | Receipt for this run, role `developer`, session, digest, pin, `prepared_id` | `SKILL_DELIVERY_*` |
| `task complete` | One helper at **both** entries: `run_task_complete` (after `:8417-8425`, before the early returns at `:8438` and `:8446`) and `run_task_direct_result_event` (after `:9258-9273`, before `:9274`). This also covers the auto-callbacks (`:5164`, `:5584`, `:6009`, `:6067`) and the Reviewer resume (`:9328`). | §2.1 class of the current record | `SKILL_DELIVERY_*` |
| Reviewer claim | Before the claim write (`review_dispatch.py`, `UNVERIFIED`) | Same helper | `SKILL_DELIVERY_*`; no claim is written |
| Debug entry | Before a report can allow the second repair (`high_debug.py`, `UNVERIFIED`) | §2.5 | `SKILL_DELIVERY_*`, `_DEBUG_RUN_MISMATCH` |

`DELIVERED` is a claim marker, not evidence: every later transition re-reads the ledger. Refusals come before any store write or PM, Reviewer or provider call. The only exceptions are steps 6–7 and §2.2.1, which record the run's own outcome.

### 2.5 Debug entry binding

- At the Debug claim, before launch, the controller makes a fresh `debug_run_id` and stores `debug_delivery = {debug_run_id, route: debug, status: NOT_DELIVERED}`. It appends a `NOT_DELIVERED` row with role `debug` at launch. Once Debug ledger delivery ships, Debug follows §2.2 with route and role `debug`.
- At entry, `debug_run_id` must equal the claim's, else `_DEBUG_RUN_MISMATCH`; then its row is checked. Digests in a report are ignored. A digest copied from another Debug run refuses with `_WRONG_RUN`.

### 2.6 Routes

| Route | Behaviour | Reason |
|---|---|---|
| Claude Developer, AGY, OpenCode | Fail closed | All three send cards (§1.2) |
| `gw-agent` | Recorded exemption | No rules field; preferred route (`CLAUDE.md:33`) |
| Reviewer | Its own row is exempt; the Developer class applies | No non-Developer ledger path (`skill_ledger.py:598-599`) |
| Debug | Recorded exemption (§2.5) | Same; repair path (`CLAUDE.md:36`) |
| Codex direct, proposal delegate | Refused after the epoch (`_ROUTE_UNSUPPORTED`) | `PAUSED` (`CLAUDE.md:33`) |
| `task claude-bind` | Refused after the epoch (`_RECORD_MISSING`) | llmtool did not launch it, so no cards were sent (Q8) |
| PM, sub-agents | Not gated (`待後續`) | No protected transition carries their runs |

An exemption is metadata, never evidence. It becomes fail-closed when that route's ledger delivery ships.

### 2.7 Fail-closed behaviour, compatibility, invariant

- **Fail closed.** Each of these refuses, and the controller retries none of them:
  - an unreadable ledger, broken chain, lock failure or different `HOME` (Q3);
  - a missing or mismatched prepared row or receipt;
  - an append that raised;
  - an incomplete prompt write;
  - a Claude or AGY run without a passing provider result (including a wrapper that started but could not start the provider);
  - a boundary that did not fire;
  - a commit conflict, or any other commit exception (§2.2.1);
  - an unknown route, or a post-epoch record without a binding.

  The remedy is a new run. `CONTROLLER_DELIVERED` cannot be written by hand (`skill_ledger.py:488-489`), and recovery never commits (Q1).
- **TaskStore rules.**
  - `card_delivery` joins the optional developer keys (`task_store.py:320`, `:2408-2411`) and has its own strict check.
  - When the developer slot goes from `None` to a dict, or the `assignment_id` changes (a new assignment), the binding may only be `PREPARED`, the `gw-agent` exemption, or absent.
  - Under the same `assignment_id`, every write must keep the stored binding (or its absence) byte-for-byte. Only `commit_card_delivery()` may differ. It takes a trusted flag, changes only `{developer}`, and moves `PREPARED` to `DELIVERED` with nothing else changed.
  - These writes spread the stored developer, so they keep the binding:
    - the launch write (`:4057-4081`);
    - the callback writes (`task_submission_cli.py:9313-9317`; `_persist_direct_callback`, `:8783-8787`);
    - OpenCode recovery (`:6514`).
- **Old records.**
  - Records without `card_delivery` validate and round-trip unchanged; no migration, no rewrite.
  - Pre-epoch Tasks, including ones in flight at upgrade, pass as `LEGACY_UNGATED`. Merged work is never re-checked. Replay is still refused (`:8455-8456`, `:9330-9331`).
  - A post-epoch record written by an older llmtool has no binding, so it refuses with `_RECORD_MISSING`.
  - Old ledger rows still serve `check()` but never satisfy `card_receipt_status`. They have no `prepared_id`, and they bind the provider session (`:4811`) or no route (`skill_ledger.py:693-697`).
- **Idempotence.**
  - Verifiers never append.
  - A repeat `confirm_card_delivery()` for one `prepared_id` refuses and appends nothing.
  - `commit_card_delivery()` is called at most once per `prepared_id`. Reconcile only reads (§2.2.1). A repeat call, if one were made, conflicts and changes nothing.
  - Duplicate dispatch is refused (`:4960-4962`), and pins are immutable (`skill_ledger.py:526-528`).
- **Invariant.** A record reaches `DELIVERED` only through a single `commit_card_delivery()` call, never replayed.
  - That call follows a `VERIFIED` read of a `CONTROLLER_DELIVERED` row written inside that run's `on_delivered`.
  - `on_delivered` fires only at the route's boundary. For Claude and AGY, that is after the provider's own verified result. For OpenCode, it is after the whole prompt is in the provider's stdin pipe.
  - The row matches the stored Task, run, role, route, session, digest, pin and `prepared_id`.
  - If the commit raises, the stored record is whatever the read-only reconcile finds, and the run never writes a result.
  - Results, `task complete`, Reviewer claims and Debug entries need a §2.1 pass.

### 2.8 PM view and limits

- The command exits non-zero with the code.
  - A refused reservation leaves `PM_RESPONDED` with `developer: None`.
  - A refused claim or result leaves the route's failure state (§2.2 step 6), with `state: PREPARED` or no artifact.
  - An uncertain commit (§2.2.1) leaves one of two outcomes, and the output names the `reconcile=` value:
    - the route's failure state with `SKILL_DELIVERY_COMMIT_UNCERTAIN` and the reconciled binding (`DELIVERED` or `PREPARED`);
    - `DEVELOPER_DISPATCHING` with `reconcile=UNKNOWN`.
  - Any other refusal leaves the Task unchanged.
- No new authority. No change to the dispatch, D6, test, review, CI or merge gates. The same-user tamper limits remain (`skill_ledger.py:3-7`).

## 3. Implementation plan: small ordered PRs (`待實作（WP-F）`)

Rules for every PR:
- At most 4 files.
- Tests follow `common-core.md` §4: no credential-shaped strings; the ledger home, Git reads and file-system faults are injected or monkeypatched; no production check is weakened.
- Test children are local interpreter processes, never a provider. Fake provider output uses fixed, non-secret values.
- The PM runs the tests on EC2 before review.
- New test files are named here. Existing test files are `UNVERIFIED`.

Order:
- PR 1, PR 2 and PR 3 are independent of each other; all three merge before PR 4.
- PR 4 → PR 5. PR 6 and PR 7 each follow PR 5. PR 8 follows PR 7.
- PR 9 → PR 10, independent of PRs 4–8.
- PR 11 follows PR 7.

**PR 1. Ledger prepare, confirm and verifiers.** Files: `skill_ledger.py`, `tests/test_skill_ledger_card_gate.py` (new).
- Adds `prepare_card_delivery`, `confirm_card_delivery`, `card_receipt_status`, `card_preparation_status` and `not_delivered_status`. `record_card_delivery` is unchanged until PR 6.
- Accept: prepare, then confirm gives `VERIFIED`.
- Regression tests:
  - `CARD_PREPARED` alone never verifies here or in `check()`.
  - Confirm without prepare refuses.
  - Confirm with a mismatched Task, run or session refuses.
  - Wrong digest, run, session, role, pin or `prepared_id` refuses.
  - A revoked receipt refuses.
  - A broken chain refuses with `_LEDGER_UNREADABLE`.
  - An old-format receipt (no `prepared_id`, provider session) does not verify.
  - A repeat confirm appends no bytes.
  - Verifiers append nothing.

**PR 2. TaskStore schema, before any producer.** Files: `task_store.py`, `tests/test_task_store_card_delivery.py` (new).
- Adds the strict `card_delivery` check (§2.7), `commit_card_delivery()` and the binding-preservation rule.
- Accept: the `PREPARED` and exemption shapes pass.
- Regression tests:
  - Refused: an unknown key, a wrong type, non-hex digest or pin, `session_id` different from `developer.session_id`, and `DELIVERED` on a new assignment or a plain update.
  - Refused: a downgrade (a stale `PREPARED` write after commit) and any binding change under the same `assignment_id`.
  - **Commit, then the result and completion updates pass with `DELIVERED` kept.**
  - **Commit persists, then raises (F2).** The directory `os.fsync` after the replace (`task_store.py:3577-3578`) is monkeypatched to raise `OSError`. Then:
    - `commit_card_delivery()` raises `TASK_STORE_UNAVAILABLE`;
    - a fresh `get()` shows `DELIVERED`;
    - a second commit conflicts and writes nothing;
    - a stale `PREPARED` write is refused;
    - a failure-state write from the re-read developer passes with `DELIVERED` kept.
  - Commit raises before the replace (the temporary write is made to fail): `get()` shows `PREPARED`.
  - `record_opencode_launch` keeps the binding.
  - **A legacy record without the field round-trips unchanged.**

**PR 3. Delivery boundary hooks (no gate yet).** Files: `developer_routes/base.py`, `opencode_direct_developer.py`, `tests/test_route_delivery_boundary.py` (new), `tests/test_opencode_delivery_boundary.py` (new).
- Adds `run_route(..., on_delivered=None)`. It is called after the provider-result checks and before the `RouteResult` (§2.2 step 5), never inside a runner.
- Adds OpenCode `run(..., delivered_callback=None)` and the incomplete-write refusal.
- Accept: the callback fires once, only after the boundary.
- Regression tests, `run_route`:
  - **Wrapper starts, provider exec fails (F1).**
    - Setup: a test boundary, passed as `run_route`'s `boundary` argument, returns a prefix that runs a local interpreter wrapper script. The script tries to exec a path that does not exist and exits non-zero. The real `_default_runner` starts it.
    - Result: the wrapper process started (`on_launch` called) but `on_delivered` was not called. There is a `RouteError` (`<ROUTE>_OUTPUT_INVALID`, `_MODEL_UNVERIFIED` or `_EXIT_NONZERO`) and no `RouteResult`.
  - The wrapper starts a local fake provider that prints a result for another model, prints no model evidence, or exits non-zero: no callback.
  - The wrapper starts a local fake provider that prints a valid result for the requested model and exits 0: the callback fires once, before the `RouteResult` is built.
  - `Popen` failure: no callback, `RUNNER_FAILED`.
  - Boundary probes never fire the hook.
  - A callback that raises: no `RouteResult`, the callback's code.
- Regression tests, OpenCode:
  - **Spawn succeeds but prompt delivery fails.** Stdin raises `BrokenPipeError` after a partial write. Result: `OPENCODE_PROMPT_DELIVERY_INCOMPLETE`, `launch_callback` called, `delivered_callback` not called, `attempted=True`.
  - Short writes that complete: the callback fires once.
  - Timeout, output cap or `TOOL_DENIED` before stdin finishes: no callback.
  - A callback that raises stops the process and returns no result.
- Without a callback, behaviour is unchanged, apart from the incomplete-write refusal.

**PR 4. Gate module and the OpenCode claim.** Files: `skill_gate.py` (new: §2.1 classifier, result check, read-only `reconcile_card_commit()` for §2.2.1), `task_submission_cli.py`, `tests/test_skill_gate.py` (new), `tests/test_dispatch_claim_opencode.py` (new).
- Steps 2–7 and §2.2.1 for OpenCode. Removes `:5017-5018` and `:5020-5024`.
- Accept: the claim commits only after the boundary, and then the result write passes.
- Regression tests:
  - **Missing reservation: no record, no launch.**
  - **Prompt write fails: no claim, `PREPARED`, `DEVELOPER_OUTCOME_UNKNOWN`, no artifact.**
  - **Confirm append fails (including one that persisted then raised): no claim.**
  - Another run's or session's receipt refuses.
  - Wrong digest or role refuses.
  - **Commit persists, then raises (F2):**
    - the provider is stopped;
    - the commit is called once;
    - there is no result write and no artifact;
    - the record is `DEVELOPER_OUTCOME_UNKNOWN` with `SKILL_DELIVERY_COMMIT_UNCERTAIN` and `DELIVERED` kept;
    - the output shows `reconcile=DELIVERED`.
  - Commit raises without persisting, or conflicts: `PREPARED` kept, `reconcile=PREPARED`, nothing committed.
  - **Commit raises and the reconcile read fails:**
    - no further store write and a non-zero exit, with `reconcile=UNKNOWN`;
    - the record stays `DEVELOPER_DISPATCHING`;
    - a later dispatch recovers through `_recover_direct_child_dispatch` with the stored binding kept and no commit.
  - `reconcile_card_commit()` unit cases: `DELIVERED`; `PREPARED`; `UNKNOWN` for another assignment, another binding or a read error. It never writes.
  - **Commit, then the result write (`:5153-5154`) passes with `DELIVERED` and a refreshed `expected_developer`.**
  - Revocation before the result: no artifact.
  - Repeat dispatch refused.
  - Orphan row ignored.

**PR 5. Claude and AGY claim, `gw-agent` exemption.** Files: `task_submission_cli.py`, `tests/test_dispatch_claim_claude_agy.py` (new), `tests/test_dispatch_commit_uncertain_claude_agy.py` (new), `tests/test_dispatch_gw_agent_exemption.py` (new).
- Steps 2–7 and §2.2.1 through `on_delivered`. Removes `:5497-5498` and `:5969-5970`.
- The `gw-agent` creating write stores the exemption, and `on_launch` becomes fail-closed.
- Accept: the same as PR 4 for both routes.
- Regression tests, claim (`test_dispatch_claim_claude_agy.py`):
  - **Wrapper starts, provider exec fails (F1), both routes.** The PR 3 test boundary is used through a monkeypatched `for_platform`. Result:
    - no `CONTROLLER_DELIVERED` row for the `prepared_id` (no receipt);
    - `commit_card_delivery()` never called and nothing committed: the record stays `PREPARED`;
    - `BLOCKED` with the route code, and no artifact.
  - **The injected `route_runner` never fires the hook: `PREPARED`, `<ROUTE>_DELIVERY_UNCONFIRMED`, no artifact.**
  - **Commit, then the result (`:6056`, `:5572`) and the committed carry (`:5985-5986`) pass.**
  - A later `BLOCKED` path after the commit (for example `CLAUDE_NO_CHANGES`) keeps `DELIVERED`.
  - Claim refused: `BLOCKED` with the code.
  - Confirm binds the requested model.
- Regression tests, uncertain commit (`test_dispatch_commit_uncertain_claude_agy.py`), both routes:
  - **Commit persists, then raises:** the commit is called once, the record is `BLOCKED` with `SKILL_DELIVERY_COMMIT_UNCERTAIN`, `DELIVERED` is kept, there is no artifact, and the output shows `reconcile=DELIVERED`.
  - Commit raises without persisting: `PREPARED` kept, `reconcile=PREPARED`.
  - Reconcile read fails: no further store write, the record stays `DEVELOPER_DISPATCHING`, and the output shows `reconcile=UNKNOWN`. The next delegate refuses: Claude with replay refused, AGY with `DIRECT_CHILD_LIVENESS_UNKNOWN`.
- Regression tests, `gw-agent`: a failed row append means no launch; with the row, the exemption is stored.

**PR 6. Retire the post-run writer.** Files: `skill_ledger.py`, `tests/test_skill_ledger_card_gate.py`.
- `record_card_delivery` is removed, so `confirm_card_delivery` is the only `CONTROLLER_DELIVERED` writer.
- Accept: no caller is left. `record()` still refuses that level.

**PR 7. Completion gate.** Files: `skill_gate.py` (`SKILL_GATE_EPOCH` = PR 5 merge time), `task_submission_cli.py`, `tests/test_task_complete_skill_gate.py` (new).
- Regression tests:
  - Positive case.
  - Missing receipt; wrong digest, run or role; `PREPARED`.
  - Legacy record passes; post-epoch record without a binding refuses; `claude-bind` after the epoch refuses.
  - `gw-agent` with its row passes, without it refuses; Codex refuses.
  - **Both early returns (`:8438`, `:8446`) refuse with no PM or Reviewer call.**
  - Auto-callbacks (`:5164`, `:5584`, `:6009`, `:6067`).
  - Repeat gives the same code with no write.

**PR 8. Reviewer claim.** Files: `review_dispatch.py` (`UNVERIFIED`), a new test.
- A receipt passes. A missing receipt, or a wrong run, digest or role, refuses. **`gw-agent` with a row passes, without one refuses; legacy passes.** Repeat.

**PR 9. Debug ledger.** Files: `skill_ledger.py`, `skill_manifest.py`, a new test.
- Adds the `debug` route with a fixed role pairing. A mismatch refuses.

**PR 10. Debug entry.** Files: `high_debug.py` (`UNVERIFIED`) and a new test. If `debug_delivery` is a TaskStore field, also `task_store.py` and a test, under the same schema and legacy rules.
- A matching row passes. A missing row or another run's row refuses. **A digest from another Debug run refuses with `_WRONG_RUN`.** Wrong role, legacy, repeat.

**PR 11. Status docs.** Files: `CLAUDE.md`, `AGENTS.md` (PM-owned).
- Mark dispatch and completion blocking as `現行` only after PR 7 merges. Mark Reviewer and Debug after PR 8 and PR 10.

## 4. Open questions for the PM (default in bold)

1. A crash before the claim leaves `PREPARED`. Today's recovery:
   - OpenCode: `_recover_direct_child_dispatch` (`:6494-6530`) spreads the stored developer into `DEVELOPER_OUTCOME_UNKNOWN` (`:6514`, `:6522-6527`), so it keeps the binding and never commits.
   - AGY: refused with `DIRECT_CHILD_LIVENESS_UNKNOWN` (`:6496-6498`).
   - Claude: replay refused (`:5796-5798`).

   **Treat as the route's failure state; never commit in recovery; re-run.**
2. Receipts expire after 24 h, and review can take longer. **Ignore expiry, honour revocation.**
3. Do gated commands run as the delegating user? The ledger lives under `Path.home()` (`skill_ledger.py:83-84`). **Require it; any other `HOME` refuses.**
4. A run's `run_id` is created only when the Task is created (`task submit` `:7953`, `record-pm` `:8048`). So a repair dispatch on the same Task reuses the run's pin (`skill_ledger.py:532-534`). How `review_repair.py` clears the developer slot is `UNVERIFIED`. **Bind per assignment; keep the immutable pin; the session separates receipts.**
5. PM waiver for a lost receipt? **No; re-run. Exceptions are Owner decisions.**
6. Epoch? **PR 5 merge time, as a fixed constant.**
7. Log refusals in `missing_gates`? **No; refusals stay write-free.**
8. `task claude-bind` (`:2272-2361`) carries no delivery evidence. **Refuse after the epoch. An exemption is an Owner decision.**
