# WP-F design: delivery evidence gate (#1502)

Status: **proposal** (docs only). Not a review, an acceptance or runtime evidence.

- Source: Task source `fbeb2f1e` (#1997 head). Every line cited below from a file in this Task's read list was read at that commit for this revision. Python paths are under `tools/llmtool/llmtool/`. Tests go in `tools/llmtool/tests/`.
- Marks per `CLAUDE.md:5`: `現行` (current), `待實作（WP-F）` (decided, ships with WP-F), `待後續` (decided, out of scope). `UNVERIFIED` marks only files outside this Task's read list: `review_dispatch.py`, `review_repair.py`, `high_debug.py`, `providers/cli_lifecycle.py`, the design `wpf-role-rule-cards.md`, and existing tests.
- Repair 5 fixes #1997 v2 F1. The claim now follows a per-route **delivery boundary**, not the process start (§1.2, §2.2). The plan is split into PRs of at most 4 files (§3). Kept from the accepted v2: reservation, then receipt, then claim; TaskStore schema before any producer; updates that preserve `DELIVERED`.

## 1. Today (`現行`)

### 1.1 Rules and ledger

- `CLAUDE.md:9`: "[NOT LIVE]: delivery to sub-agent and PM runs (待後續), and missing evidence blocking protected transitions (待實作（WP-F）)." `CLAUDE.md:10`: hooks are defence in depth. The real gates are dispatch, `task complete` and merge.
- Design §3.1 and §6 (`wpf-role-rule-cards.md`, `UNVERIFIED` here): without matching evidence, a dispatch claim, `task complete`, a Reviewer claim or a Debug entry refuses.
- Ledger: private, hash-chained, per user under `Path.home()` (`skill_ledger.py:83-84`). Evidence levels are at `:42-43`; the ten binding fields are at `:44-45`. An append can raise after its line persists (`:9-10`).
- `pin_dispatch()` writes one immutable `DISPATCH_PIN` per Task/run/route from authenticated `main` (`:521-557`). A repeat call returns the first pin (`:532-534`). Only routes in the role-card table can pin (`:513`; `skill_manifest.py:50-55`).
- `record_card_delivery()` is the only writer of `CONTROLLER_DELIVERED` (`skill_ledger.py:560-638`). It derives the cards and their digest from the pin (`:603-630`). It refuses: `CALLER_DIGESTS_REFUSED`, `CALLER_BASE_REFUSED`, `DISPATCH_PIN_MISSING`, `ROLE_MISMATCH` (`:598-599`), `PIN_MISMATCH`, `MANIFEST_DIGEST_MISMATCH`. `record()` refuses that level (`:488-489`). `record_not_delivered()` stores a row with level `UNVERIFIED` (`:641-667`).
- `check()` refusal reasons include `NOT_FOUND`, `WRONG_RUN`, `WRONG_ROLE`, `MANIFEST_CHANGED`, `REVOKED`, `EXPIRED` and `PIN_MISMATCH` (`:711-782`). A row with level `UNVERIFIED` never verifies (`:752-753`). A row without a route is never re-validated (`:693-697`). The receipt TTL is 86400 s (`:560`).
- TaskStore validates the whole record on every write (`task_store.py:3513-3525`). The developer key set must be one exact shape, after removing the optional keys in `_DEVELOPER_OPTIONAL_FIELDS` (`:294`, `:2372-2375`). Dispatch takes the developer slot only from `developer: None` (`task_submission_cli.py:4900`, `:5386`, `:5857`). The OpenCode launch write copies the stored developer (`task_store.py:4025`).
- Gaps: a failed receipt only prints `<PROVIDER>_SKILL_LEDGER_OUTCOME_UNKNOWN` (`task_submission_cli.py:4750-4764`). No completion path reads the ledger (`:8216`, `:9056`). The receipt binds the provider's reported session (`:4756`), not the Task's `developer.session_id`. The hook observes and does not enforce (`claude_skill_hook.py:47-48`, `:484-488`).

### 1.2 Where the prompt reaches the provider (F1)

| Route | Prompt channel | Today's receipt | Why process start is not delivery |
|---|---|---|---|
| Claude | argv after `--` (`developer_routes/claude.py:56-61`); stdin is `DEVNULL` (`developer_routes/base.py:294`) | After the run returns (`task_submission_cli.py:5908-5909`) | `on_launch` runs **before** `Popen` (`base.py:784-785`, `:794`). There is no hook after spawn. |
| AGY | argv `-p` (`developer_routes/agy.py:44-47`) | `:5440`, which binds the verified `result.model` | Same as Claude |
| OpenCode | stdin pipe (`opencode_direct_developer.py:853-856`) | `:4961-4968` | `launch_callback` (`:861-867`) runs before any stdin byte is written (`:873-891`). A `BrokenPipeError` or `OSError` sets `offset = len(payload)` (`:886-887`), so a failed write looks complete. |
| `gw-agent` | Route fields, no cards | `NOT_DELIVERED` row from `on_launch` (`:6309`), before spawn | Exempt. Append failures are ignored (`:4782-4784`). |

The v2 design confirmed delivery "after the provider process starts". On OpenCode that point comes before the prompt is sent. On Claude and AGY no callback exists at that point. F1 holds.

### 1.3 Developer writers

| Writer | Code | Card route |
|---|---|---|
| Claude, AGY, OpenCode delegate | `:5719`, `:5268`, `:4805` | `_SKILL_CARD_ROUTES` (`:4690-4691`) |
| `gw-agent` delegate | `:6016` | None (exempt) |
| Codex direct, proposal delegate | `:6579`, `:7573-7588` | None. `codex-developer` is `PAUSED` (`CLAUDE.md:33`). |
| `task claude-bind` | `:2262-2316`: binds a Claude session that llmtool did not launch, with no provider call | None (Q8) |

## 2. Gate design (`待實作（WP-F）`)

### 2.1 Eligibility (one classifier, `skill_gate.py`)

Each transition reads the stored record, never caller input.

| Class | Condition | Needs |
|---|---|---|
| `RECEIPT_REQUIRED` | `developer.card_delivery.state == DELIVERED` and `route == _SKILL_CARD_ROUTES[provider_profile]` | `card_receipt_status` returns `VERIFIED` (§2.3) |
| `EXEMPT` | `gw-agent` record whose `card_delivery == {route: gw-agent, state: NOT_DELIVERED}` | `not_delivered_status` finds that run's row |
| `LEGACY_UNGATED` | No `card_delivery`, and `developer.created_at` (`:4874`, `:5363`, `:5834`, `:7587`) is before `SKILL_GATE_EPOCH` | Nothing |
| refuse | Anything else: `PREPARED`, no binding after the epoch, Codex, `claude-bind`, an unknown route, an unreadable ledger | — |

Codes: `SKILL_DELIVERY_RECORD_MISSING`, `_CLAIM_UNCOMMITTED` (`PREPARED`), `_EXEMPTION_INVALID`, `_EXEMPTION_MISSING`, `_ROUTE_UNSUPPORTED`, plus the §2.3 verifier codes.

### 2.2 Developer lifecycle: reserve, deliver, claim

1. **Pin and cards** (`現行`, `:4711-4738`), fail closed, before any store write.
2. **Prepare.** `prepare_card_delivery(entry, repo_root)` runs today's checks (`skill_ledger.py:583-630`). It appends a `CARD_PREPARED` row, level `UNVERIFIED`, with the binding: Task, run, `developer.session_id` (`:4865`, `:5356`, `:5827`), agent, role `developer`, provider, the requested model, repo, head, prefix digest, route, pin. It returns the row's `prepared_id`.
3. **Reservation.** `card_preparation_status()` must match, else the command refuses with `SKILL_DELIVERY_PREPARATION_MISSING`: no store write, no launch. The creating write stores `card_delivery = {route, pin_commit, prefix_sha256, session_id, prepared_id, state: PREPARED}` in the same write as the developer. The duplicate-dispatch refusal stays (`:4904-4906`, `:5388-5389`, `:5859-5860`). `PREPARED` is not a claim: every protected transition refuses it.
4. **Pre-launch checks**, unchanged: `route_gate` (Claude `:5877-5889`, AGY `:5407-5418`) and OpenCode `prelaunch_validate` (`:4915-4921`). These checks compare the whole developer (`:5867`, `:5396`), so the local copy carries the binding.
5. **Delivery boundary.** The route calls `on_delivered` once, at the first point where the controller has handed the **whole** prompt to the provider:

   | Route | Boundary | Not a boundary (no confirm; refuse) |
   |---|---|---|
   | Claude, AGY | `subprocess.Popen` returned in `_default_runner` and `lifecycle.own(process)` succeeded (`base.py:294-297`). `Popen` raises if exec fails, so a return means the kernel accepted the whole argv, prompt included. Call it as the first statement of the inner `try` (`:299`), so the `finally` at `:323-326` closes the pipes. | `Popen` raised (ENOENT, E2BIG and so on): `<ROUTE>_RUNNER_FAILED` (`:804-806`). Pre-spawn refusals: no callback (`:662-791`). |
   | OpenCode | All `len(payload)` bytes accepted by `os.write` and stdin closed without error (`opencode_direct_developer.py:883-890`), called right after `key.fileobj.close()` | Any write or close error: `OPENCODE_PROMPT_DELIVERY_INCOMPLETE`, replacing the swallow at `:886-887`. Stdin not finished at timeout (`:880-881`), output cap (`:902-904`) or `TOOL_DENIED` (`:916-917`): that route code. Loop ended with delivery not marked: `OPENCODE_PROMPT_DELIVERY_INCOMPLETE`. |

   - The hook is a keyword passed only to the provider `execute(...)` at `base.py:794`. It is never passed to the `runner=` given to `boundary.verify` (`:770-771`), whose probes also spawn processes.
   - If `on_delivered` was passed but did not fire, for example because an injected `route_runner` (`task_submission_cli.py:5431`, `:5900`) ignores it, `run_route` returns no `RouteResult` and raises `<ROUTE>_DELIVERY_UNCONFIRMED`.
   - `launch_callback` stays as it is and only records the pid (`:4936-4953`).
   - What the boundary proves: the started process got the whole prompt (argv), or the whole prompt is in its stdin pipe. For argv routes, `argv[0]` may be the boundary wrapper (`base.py:783`); if the wrapper fails to exec the provider, the run exits non-zero (`:915-917`) and step 7 stores nothing. It does not prove that the model read the cards.
6. **Confirm and claim** inside `on_delivered`:
   - `confirm_card_delivery(prepared_id, task_id, run_id, session_id, repo_root)` re-derives the cards from the pin. It takes every other binding field from the prepared row, so the caller supplies no model, head or digest. It appends `CONTROLLER_DELIVERED` with `prepared_id`.
   - `card_receipt_status()` must then return `VERIFIED` for the stored binding.
   - Only then does TaskStore `commit_card_delivery()` move `PREPARED` to `DELIVERED` by compare-and-set. **That write is the dispatch claim.**
   - The caller then refreshes its local developer copy: Claude `developer` (`:5863`), AGY `developer` (`:5392`), OpenCode `developer` and `expected_developer` (`:4913`, `:4950`). A later stale `PREPARED` write would otherwise be refused as a downgrade (§2.7).
   - If any of the three raises (including an append that raised after its line persisted, `skill_ledger.py:9-10`), the callback raises `SKILL_DELIVERY_*`. Nothing is committed, the provider is stopped, and `state` stays `PREPARED`.
   - Stopping: Claude and AGY leave through `lifecycle.__exit__` (`base.py:327-328`); OpenCode through `_reject` inside `with lifecycle` (`opencode_direct_developer.py:837`). The cleanup outcome is recorded as `cleanup_status` (`base.py:329`; `opencode_direct_developer.py:929-932`). How `CLIProcessLifecycle` kills the process is `UNVERIFIED` (not readable here).
   - The record keeps each route's existing post-spawn failure state, with `error_code` set to the code: OpenCode `DEVELOPER_OUTCOME_UNKNOWN` (`task_submission_cli.py:4969-4970`); Claude and AGY `BLOCKED` (`:5949`, `:5488`). No artifact is stored.
7. **Result check.** Before any result write, the stored record must be `DELIVERED` and the receipt must still be `VERIFIED`. The result writes are Claude `:5992` and the committed carry `:5924`, AGY `:5512`, OpenCode `:5097`. A revoked receipt or a record that never left `PREPARED` (for example, a runner that never fired the hook) gives `DEVELOPER_OUTCOME_UNKNOWN`, `SKILL_DELIVERY_*` and no artifact. The post-run receipt writes (`:4961-4968`, `:5440`, `:5908-5909`) are removed.

`gw-agent` skips steps 2 and 5–7. Its creating write stores the exemption. The `on_launch` row (`:6309`) becomes fail-closed: an append error raises a `RouteError`, which `run_route` passes through before spawn (`base.py:786-788`), so the run never launches. A refusal before the boundary leaves at most a `CARD_PREPARED` row and a `PREPARED` record. An orphan row (from a lost duplicate race or an append that persisted and then raised) carries a session or `prepared_id` that no committed record stores.

### 2.3 Verifiers (read-only)

- `card_receipt_status(task_id, run_id, role, route, session_id, prefix_sha256, pin_commit, prepared_id)` returns `VERIFIED` only for a non-revoked `CONTROLLER_DELIVERED` row that matches every argument, whose `CARD_PREPARED` row precedes it, and whose pin is still in the chain (`skill_ledger.py:690-703`). Expiry is ignored (Q2).
- `card_preparation_status(...)` is used at step 3 only. `not_delivered_status(task_id, run_id, role, route)` checks an exemption.
- Codes: `SKILL_DELIVERY_RECEIPT_MISSING`, `_PREPARATION_MISSING`, `_WRONG_RUN`, `_WRONG_SESSION`, `_WRONG_ROLE`, `_DIGEST_MISMATCH`, `_PIN_MISMATCH`, `_REVOKED`, `_LEDGER_UNREADABLE` (lock, read or broken chain).
- Verifiers append nothing. Like `check()`, they may repair a torn append under the lock (`:7-9`, `:679-680`).

### 2.4 Protected transitions

| Transition | Check point | Evidence | Refusal |
|---|---|---|---|
| Dispatch claim | §2.2 step 6, after the boundary, before `commit_card_delivery()` | Receipt for this run, role `developer`, session, digest, pin, `prepared_id` | `SKILL_DELIVERY_*` |
| `task complete` | One helper at **both** entries: `run_task_complete` (after `:8228-8231`, before `:8237`, `:8246`) and `run_task_direct_result_event` (after `:9076-9079`, before `:9081`). It also covers the auto-callbacks (`:5946`, `:6003`) and `:9379`. | §2.1 class of the current record | `SKILL_DELIVERY_*` |
| Reviewer claim | Before the claim write (`review_dispatch.py`, `UNVERIFIED`) | Same helper | `SKILL_DELIVERY_*`; no claim is written |
| Debug entry | Before a report can allow the second repair (`high_debug.py`, `UNVERIFIED`) | §2.5 | `SKILL_DELIVERY_*`, `_DEBUG_RUN_MISMATCH` |

`DELIVERED` is a claim marker, not evidence: every later transition re-reads the ledger. Refusals come before any store write or PM, Reviewer or provider call. The only exception is steps 6–7, which record the run's own outcome.

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

- **Fail closed.** Each of these refuses, and none is retried by the controller:
  - an unreadable ledger, broken chain, lock failure or different `HOME` (Q3);
  - a missing or mismatched prepared row or receipt;
  - an append that raised;
  - an incomplete prompt write;
  - a boundary that did not fire;
  - a commit conflict;
  - an unknown route, or a post-epoch record without a binding.

  The remedy is a new run. `CONTROLLER_DELIVERED` cannot be written by hand (`skill_ledger.py:488-489`), and recovery never commits (Q1).
- **TaskStore rules.**
  - `card_delivery` joins the optional developer keys (`task_store.py:294`, `:2372-2375`) and has its own strict check.
  - When the developer slot goes from `None` to a dict, or the `assignment_id` changes (a new assignment), the binding may only be `PREPARED`, the `gw-agent` exemption, or absent.
  - Under the same `assignment_id`, every write must keep the stored binding (or its absence) byte-for-byte. Only `commit_card_delivery()` (a trusted flag, changes `{developer}` only, `PREPARED` to `DELIVERED`, nothing else changed) may differ.
  - The launch write (`:4003-4027`) and the callback writes (`task_submission_cli.py:9119-9123`, `:9285-9372`) spread the stored developer, so they keep the binding.
- **Old records.**
  - Records without `card_delivery` validate and round-trip unchanged; no migration, no rewrite.
  - Pre-epoch Tasks, including ones in flight at upgrade, pass as `LEGACY_UNGATED`. Merged work is never re-checked; replay is still refused (`:8261-8262`, `:9136-9137`).
  - A post-epoch record written by an older llmtool has no binding, so it refuses with `_RECORD_MISSING`.
  - Old ledger rows still serve `check()`, but never satisfy `card_receipt_status`: they have no `prepared_id`, and they bind the provider session (`:4756`) or no route (`skill_ledger.py:693-697`).
- **Idempotence.**
  - Verifiers never append.
  - A repeat `confirm_card_delivery()` for one `prepared_id` refuses and appends nothing.
  - A repeat `commit_card_delivery()` conflicts and changes nothing.
  - Duplicate dispatch is refused (`:4904-4906`), and pins are immutable (`skill_ledger.py:526-528`).
- **Invariant.** A record reaches `DELIVERED` only through `commit_card_delivery()`. That call follows a `VERIFIED` read of a `CONTROLLER_DELIVERED` row written inside that run's `on_delivered`, which fires only after the whole prompt crossed the route's boundary. The row matches the stored Task, run, role, route, session, digest, pin and `prepared_id`. Results, `task complete`, Reviewer claims and Debug entries need a §2.1 pass.

### 2.8 PM view and limits

- The command exits non-zero with the code.
  - A refused reservation leaves `PM_RESPONDED` with `developer: None`.
  - A refused claim or result leaves the route's failure state (§2.2 step 6), with `state: PREPARED` or no artifact.
  - Any other refusal leaves the Task unchanged.
- No new authority. No change to the dispatch, D6, test, review, CI or merge gates. The same-user tamper limits remain (`skill_ledger.py:3-7`).

## 3. Implementation plan: small ordered PRs (`待實作（WP-F）`)

Rules for every PR:
- At most 4 files.
- Tests follow `common-core.md` §4: no credential-shaped strings; the ledger home and Git reads are injected or monkeypatched; no production check is weakened.
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
  - `record_opencode_launch` keeps the binding.
  - A repeat commit conflicts.
  - **A legacy record without the field round-trips unchanged.**

**PR 3. Delivery boundary hooks (no gate yet).** Files: `developer_routes/base.py`, `opencode_direct_developer.py`, `tests/test_route_delivery_boundary.py` (new), `tests/test_opencode_delivery_boundary.py` (new).
- `run_route(..., on_delivered=None)`, with the `_default_runner` call after `lifecycle.own`. OpenCode `run(..., delivered_callback=None)` and the incomplete-write refusal (§2.2 step 5).
- Accept: the callback fires once, only after the boundary.
- Regression tests:
  - **Spawn succeeds but prompt delivery fails:** stdin raises `BrokenPipeError` after a partial write. Result: `OPENCODE_PROMPT_DELIVERY_INCOMPLETE`, `launch_callback` called, `delivered_callback` not called, `attempted=True`.
  - Short writes that complete: the callback fires once.
  - Timeout, output cap or `TOOL_DENIED` before stdin finishes: no callback.
  - `Popen` failure: no callback, `RUNNER_FAILED`.
  - Boundary probes never fire the hook.
  - A callback that raises stops the process and returns no result.
  - A custom runner that ignores the hook gives `<ROUTE>_DELIVERY_UNCONFIRMED`.
  - Without a callback, behaviour is unchanged apart from the incomplete-write refusal.
- Test children are a local interpreter process, never a provider.

**PR 4. Gate module and the OpenCode claim.** Files: `skill_gate.py` (new: §2.1 classifier, result check), `task_submission_cli.py`, `tests/test_skill_gate.py` (new), `tests/test_dispatch_claim_opencode.py` (new).
- Steps 2–7 for OpenCode. Removes `:4961-4968`.
- Accept: the claim commits only after the boundary, and then the result write passes.
- Regression tests:
  - **Missing reservation: no record, no launch.**
  - **Prompt write fails: no claim, `PREPARED`, `DEVELOPER_OUTCOME_UNKNOWN`, no artifact.**
  - **Confirm append fails (including one that persisted then raised): no claim.**
  - Another run's or session's receipt refuses.
  - Wrong digest or role refuses.
  - A commit conflict commits nothing.
  - **Commit, then the result write (`:5097`) passes with `DELIVERED` and a refreshed `expected_developer`.**
  - Revocation before the result: no artifact.
  - Repeat dispatch refused.
  - Orphan row ignored.

**PR 5. Claude and AGY claim, `gw-agent` exemption.** Files: `task_submission_cli.py`, `tests/test_dispatch_claim_claude_agy.py` (new), `tests/test_dispatch_gw_agent_exemption.py` (new).
- Steps 2–7 through `on_delivered`. Removes `:5440` and `:5908-5909`. The `gw-agent` creating write stores the exemption, and `on_launch` becomes fail-closed.
- Accept: the same as PR 4 for both routes.
- Regression tests:
  - **Spawn fails: no claim.**
  - **The injected `route_runner` never fires the hook: `PREPARED`, `<ROUTE>_DELIVERY_UNCONFIRMED`, no artifact.**
  - **Commit, then the result (`:5992`, `:5512`) and the committed carry (`:5924`) pass.**
  - Claim refused: `BLOCKED` with the code.
  - Confirm binds the requested model.
  - `gw-agent`: a failed row append means no launch; with the row, the exemption is stored.

**PR 6. Retire the post-run writer.** Files: `skill_ledger.py`, `tests/test_skill_ledger_card_gate.py`.
- `record_card_delivery` is removed, so `confirm_card_delivery` is the only `CONTROLLER_DELIVERED` writer.
- Accept: no caller is left. `record()` still refuses that level.

**PR 7. Completion gate.** Files: `skill_gate.py` (`SKILL_GATE_EPOCH` = PR 5 merge time), `task_submission_cli.py`, `tests/test_task_complete_skill_gate.py` (new).
- Regression tests:
  - Positive case.
  - Missing receipt; wrong digest, run or role; `PREPARED`.
  - Legacy record passes; post-epoch record without a binding refuses; `claude-bind` after the epoch refuses.
  - `gw-agent` with its row passes, without it refuses; Codex refuses.
  - **Both early returns (`:8237`, `:8246`) refuse with no PM or Reviewer call.**
  - Auto-callbacks (`:5946`, `:6003`).
  - Repeat gives the same code with no write.

**PR 8. Reviewer claim.** Files: `review_dispatch.py` (`UNVERIFIED`), a new test.
- Receipt passes. Missing receipt, wrong run, digest or role refuse. **`gw-agent` with a row passes, without one refuses; legacy passes.** Repeat.

**PR 9. Debug ledger.** Files: `skill_ledger.py`, `skill_manifest.py`, a new test.
- Adds the `debug` route with a fixed role pairing. A mismatch refuses.

**PR 10. Debug entry.** Files: `high_debug.py` (`UNVERIFIED`) and a new test. Add `task_store.py` and a test if `debug_delivery` is a TaskStore field, under the same schema and legacy rules.
- A matching row passes. A missing row or another run's row refuses. **A digest from another Debug run refuses with `_WRONG_RUN`.** Wrong role, legacy, repeat.

**PR 11. Status docs.** Files: `CLAUDE.md`, `AGENTS.md` (PM-owned).
- Mark dispatch and completion blocking as `現行` only after PR 7 merges. Mark Reviewer and Debug after PR 8 and PR 10.

## 4. Open questions for the PM (default in bold)

1. A crash before the claim leaves `PREPARED`. Recovery is `_recover_direct_child_dispatch` (`:4813`), whose body was not read for this revision. **Treat as the route's failure state; never commit in recovery; re-run.**
2. Receipts expire after 24 h, and review can take longer. **Ignore expiry, honour revocation.**
3. Do gated commands run as the delegating user? The ledger lives under `Path.home()` (`skill_ledger.py:83-84`). **Require it; any other `HOME` refuses.**
4. A run's `run_id` is created only at `record-pm` (`:7759`, `:7854`), so a repair dispatch on the same Task reuses the run's pin (`skill_ledger.py:532-534`). How `review_repair.py` clears the developer slot is `UNVERIFIED`. **Bind per assignment; keep the immutable pin; the session separates receipts.**
5. PM waiver for a lost receipt? **No; re-run. Exceptions are Owner decisions.**
6. Epoch? **PR 5 merge time, as a fixed constant.**
7. Log refusals in `missing_gates`? **No; refusals stay write-free.**
8. `task claude-bind` (`:2262-2316`) carries no delivery evidence. **Refuse after the epoch. An exemption is an Owner decision.**
