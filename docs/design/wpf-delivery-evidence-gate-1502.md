# WP-F design: delivery evidence gate (#1502)

Status: **proposal** (docs only). Not a review, an acceptance or runtime evidence.

- Source: `main` at `a2b6f4d891d89f16f3411122e25f307a54538a01`. Repair 3 reads Task source `ec3492656843909ccb55f89b97e82c388311d1d4` (merges `origin/main`); lines spot-checked there. Python paths are under `tools/llmtool/llmtool/`.
- Marks per `CLAUDE.md:5`: `現行`, `待實作（WP-F）`, `待後續`, plus `UNVERIFIED` (not readable here: `review_dispatch.py`, `high_debug.py`, `task_completion.py`, `task_store.py`, the route runner, tests).

## 1. Today

- `CLAUDE.md:9`: "[NOT LIVE]: delivery to sub-agent and PM runs (待後續), and missing evidence blocking protected transitions (待實作（WP-F）)." It lists as live: Claude Developer, AGY and OpenCode `CONTROLLER_DELIVERED`; Reviewer and Debug card digests bound into the claim or entry; `gw-agent` `NOT_DELIVERED`.
- Design §3.1 (`wpf-role-rule-cards.md:122`): "When evidence is missing, the next protected transition refuses: dispatch claim, `task complete`, Reviewer claim or Debug entry." §6 (`:265`): "A dispatch, Review claim, Debug entry or `task complete` without matching evidence refuses."

Ledger (`現行`):

- Private, hash-chained, per user (`skill_ledger.py:83-84`). Levels `CONTROLLER_DELIVERED`, `HOOK_DELIVERED`, `OBSERVED_READ`, `UNVERIFIED` (`:42-43`); ten binding fields (`:44-45`).
- `pin_dispatch()` writes one immutable `DISPATCH_PIN` per Task/run/route from authenticated `main`, never evidence (`:521-557`); only Developer routes pin (`:513`; `skill_manifest.py:50-55`).
- `record_card_delivery()` is the only `CONTROLLER_DELIVERED` writer (`skill_ledger.py:560-638`); it derives cards and digest from the pin and sends nothing. Refusals: `CALLER_DIGESTS_REFUSED`, `CALLER_BASE_REFUSED`, `DISPATCH_PIN_MISSING`, `ROLE_MISMATCH` (non-Developer, `:598-599`), `PIN_MISMATCH`, `MANIFEST_DIGEST_MISMATCH`. `record()` refuses that level (`:488-489`). `record_not_delivered()` stores `NOT_DELIVERED` as `UNVERIFIED` (`:641-667`).
- `check()` reasons include `NOT_FOUND`, `WRONG_RUN`, `WRONG_ROLE`, `MANIFEST_CHANGED`, `REVOKED`, `EXPIRED`, `PIN_MISMATCH` (`:711-782`). TTL 86400 s (`:560`).

| Route | Code | Evidence |
|---|---|---|
| Claude Developer | Cards before any store write (`task_submission_cli.py:5816`); receipt after the run (`:5908-5909`) | `CONTROLLER_DELIVERED` (`現行`) |
| AGY | `:5345`, `:5440` | same |
| OpenCode | `:4854`, `:4961-4968` | same |
| `gw-agent` | `on_launch` (`:4767-4784`, `:6309`) | `NOT_DELIVERED ROUTE_NOT_SUPPORTED` |
| Codex Developer | No card route (`:4690`); `PAUSED` (`CLAUDE.md:33`) | None |
| Reviewer, Debug | Card builders (`skill_manifest.py:467-494`); callers `UNVERIFIED` | No ledger row (`skill_ledger.py:598-599`) |
| PM, sub-agents | No llmtool path | None (`待後續`) |

Gaps (`現行`): a failed receipt write only prints `<PROVIDER>_SKILL_LEDGER_OUTCOME_UNKNOWN` ("No gate reads this entry yet", `task_submission_cli.py:4750-4764`). No completion path reads the ledger (`:8216`, `:9056`). The creating store write (`:4898-4903`, `:5855-5858`) is today's claim. TaskStore checks Developer fields by exact shape (`task_store.py:2372-2375` at `437451aa`, per the v2 review; `UNVERIFIED` here). The hook (`claude_skill_hook.py:484-488`, observe mode `:47-48`) stays defence in depth (`CLAUDE.md:10`).

## 2. Gate design (`待實作（WP-F）`)

### 2.1 One eligibility rule

Every transition classifies the stored run record, never caller input:

| Class | Condition | Needs |
|---|---|---|
| `RECEIPT_REQUIRED` | `card_delivery.state` is `DELIVERED`; `route` fixed for `provider_profile` (`:4690`) | Receipt (§2.3) |
| `EXEMPT` | `gw-agent` with `card_delivery = {route: gw-agent, status: NOT_DELIVERED}` | That run's `NOT_DELIVERED` row |
| `LEGACY_UNGATED` | No `card_delivery`; `created_at` before the fixed `SKILL_GATE_EPOCH` | Nothing; recorded |
| refuse | Anything else, including `state: PREPARED` | — |

Codes: `SKILL_DELIVERY_RECORD_MISSING` (no binding after the epoch), `_CLAIM_UNCOMMITTED` (`PREPARED`), `_EXEMPTION_INVALID`, `_EXEMPTION_MISSING`, `_ROUTE_UNSUPPORTED` (Codex).

### 2.2 Developer lifecycle: reserve, hand off, then claim

The creating write becomes a reservation. The dispatch claim is a later compare-and-set that reads the receipt first.

1. Pin and cards, fail closed (`現行`, `:4711-4738`). The record gets `session_id` (`:4865`, `:5356`, `:5827`) and `card_delivery = {route, pin_commit, prefix_sha256, session_id, state: PREPARED}`.
2. **New** `prepare_card_delivery()`: today's checks (`skill_ledger.py:583-630`) append a `CARD_PREPARED` row, level `UNVERIFIED`.
3. **Reservation.** `card_preparation_status()` must match the binding, else `SKILL_DELIVERY_PREPARATION_MISSING` with no store write or launch. The creating write stores `state: PREPARED`. It keeps the duplicate-dispatch refusal (`:4904-4906`) but is not a claim; every protected transition refuses it.
4. Existing pre-launch checks, unchanged: `route_gate` (Claude `:5877-5889`, AGY `:5407-5426`), OpenCode `prelaunch_validate` (`:4915-4921`).
5. **Handoff.** Only after the provider process starts, `confirm_card_delivery()` (refactored `record_card_delivery()`, still the only writer) appends `CONTROLLER_DELIVERED` from the prepared row and pin (no caller digest, no Git read): OpenCode in `launch_callback` (`:4936-4953`), Claude and AGY in a post-spawn callback (Q8). It means a started provider got this prompt, not that the model read it. The post-run writes (`:4961-4968`, `:5440`, `:5908-5909`) go.
6. **Claim.** In the same callback `card_receipt_status()` must be `VERIFIED` for the stored binding; only then does TaskStore `commit_card_delivery()` move `PREPARED` to `DELIVERED` by compare-and-set. That write is the dispatch claim. A missing or mismatched receipt or a conflict commits nothing: the controller stops the provider where it can (Q8), the record goes `DEVELOPER_OUTCOME_UNKNOWN` with the code, `state` stays `PREPARED`.
7. **Result check.** Before any result is stored, the record must be `DELIVERED` and the receipt still `VERIFIED` (revocation), else `DEVELOPER_OUTCOME_UNKNOWN`, no artifact.

A refusal before handoff leaves at most `CARD_PREPARED` and a `PREPARED` record. `gw-agent` skips 2, 5, 6, 7: its creating write stores the exemption; its row stays on `on_launch` (`:6309`). Orphan rows (lost duplicate race) carry a session no record stores.

### 2.3 Verifiers (read-only)

- `card_receipt_status(task_id, run_id, role, route, session_id, prefix_sha256, pin_commit)`: `VERIFIED` only for a non-revoked `CONTROLLER_DELIVERED` row matching all of these, its pin still in the chain (`:690-703`). Expiry ignored (Q2).
- `card_preparation_status(...)` matches `CARD_PREPARED` (step 3 only); `not_delivered_status(task_id, run_id, role, route)` finds an exemption row.
- Codes: `SKILL_DELIVERY_RECEIPT_MISSING`, `_PREPARATION_MISSING`, `_WRONG_RUN`, `_WRONG_SESSION`, `_WRONG_ROLE`, `_DIGEST_MISMATCH`, `_PIN_MISMATCH`, `_REVOKED`, `_LEDGER_UNREADABLE`.

### 2.4 Protected transitions

| Transition | Check point | Evidence | Refusal |
|---|---|---|---|
| Dispatch claim | §2.2 step 6, after handoff, before `commit_card_delivery()` | `CONTROLLER_DELIVERED` for this `run_id`, role `developer`, session, digest, pin | `SKILL_DELIVERY_*`; existing pin and card refusals |
| `task complete` | One helper at **both** entries: `run_task_complete` (after `:8228-8231`, before `:8237`, `:8246`) and `run_task_direct_result_event` (after `:9076-9079`, before `:9081`); covers early returns, auto-callbacks, `:9379` | §2.1 class of the current Developer record | `SKILL_DELIVERY_*` |
| Reviewer claim | Before the claim write (`review_dispatch.py`, `UNVERIFIED`) | Same helper | `SKILL_DELIVERY_*`; no claim |
| Debug entry | Before a report can allow the second repair (`high_debug.py`, `UNVERIFIED`) | §2.5 | `SKILL_DELIVERY_*`, `_DEBUG_RUN_MISMATCH` |

Refusals precede any store write or PM, Reviewer or provider call, except steps 6–7, which record the run's own outcome.

### 2.5 Debug entry binding

- At the Debug claim, before launch, the controller makes a fresh `debug_run_id` and stores `debug_delivery = {debug_run_id, route: debug, status: NOT_DELIVERED}`, plus a `NOT_DELIVERED` row at launch, role `debug`. With Debug ledger delivery (later) it follows §2.2, route and role `debug`.
- At entry, `debug_run_id` must equal the claim's (`_DEBUG_RUN_MISMATCH`), then its row is checked. Report digests are ignored; one copied from another Debug run refuses `_WRONG_RUN`. The second repair is a new Developer run with its own gate.

### 2.6 Routes without delivery

| Route | Behaviour | Reason |
|---|---|---|
| Claude Developer, AGY, OpenCode | Fail closed | All write receipts |
| `gw-agent` | Recorded exemption | No rules field (design §3.7); preferred route (`CLAUDE.md:33`) |
| Reviewer | Own row exempt; Developer class applies | No non-Developer ledger path (`skill_ledger.py:598-599`) |
| Debug | Recorded exemption (§2.5) | Same; repair path (`CLAUDE.md:36`) |
| Codex Developer | Refused after the epoch | `PAUSED` (`CLAUDE.md:33`) |
| PM, sub-agents | Not gated (`待後續`) | No protected transition carries their runs |

An exemption is metadata, never evidence; it turns fail-closed when that route's ledger delivery ships.

### 2.7 Legacy, idempotence, invariant

- **Legacy.** Pre-epoch Tasks pass as `LEGACY_UNGATED`; a post-epoch record without a binding refuses. Merged work is never re-checked; replay is refused (`:8261-8262`, `:9136-9137`).
- **Idempotence.** Verifiers never append; a repeat gives the same code. Duplicate dispatch is refused (`:4904-4906`); pins are immutable (`skill_ledger.py:526-528`); a repeat `commit_card_delivery()` conflicts and changes nothing.
- **Invariant.** A record reaches `DELIVERED` (the dispatch claim) only through `commit_card_delivery()`, after reading a non-revoked `CONTROLLER_DELIVERED` row written after provider start and matching the stored Task, run, role, route, session, digest and pin; TaskStore refuses `DELIVERED` on any other write. Results, `task complete`, Reviewer claims and Debug entries need a §2.1 pass.

### 2.8 PM view and limits

- The command exits non-zero with the code. A refused reservation leaves `PM_RESPONDED`; a refused claim or result leaves `DEVELOPER_OUTCOME_UNKNOWN`; others leave the Task unchanged. Remedy: a new run; `CONTROLLER_DELIVERED` cannot be written by hand (`skill_ledger.py:488-489`).
- No new authority; no change to dispatch, D6, test, review, CI or merge gates. Same-user tamper limits remain (`skill_ledger.py:3-7`).

## 3. Implementation plan (≤ 4 files per PR; tests in `tools/llmtool/tests/`)

1. **Ledger.** `skill_ledger.py` + test. Prepare then confirm verifies; `CARD_PREPARED` alone never does; confirm without it refuses; missing; wrong digest, run, session, role, pin; revoked; repeat changes no bytes.
2. **TaskStore schema, before any producer.** `task_store.py` + test. Optional `developer.card_delivery` in the exact-shape check: `{route, pin_commit, prefix_sha256, session_id, state}` (§2.1 route, hex pin and digest, `developer.session_id`, `PREPARED` or `DELIVERED`) or the `gw-agent` exemption. Create and update accept only `PREPARED` or the exemption and never change a stored binding; new `commit_card_delivery()` is the only path to `DELIVERED`. Tests: both shapes pass; unknown key, wrong type, session mismatch, `DELIVERED` on create or update refused; commit once, repeat conflicts; **legacy record without the field round-trips unchanged**.
3. **Dispatch claim.** `task_submission_cli.py` + test (+ route runner for a post-spawn hook, `UNVERIFIED`). Claim commits only after the receipt; **missing reservation: no record, no launch**; **missing receipt (started, append fails): no claim, `PREPARED`, `DEVELOPER_OUTCOME_UNKNOWN`, no artifact**; other run's or session's receipt; wrong digest; wrong role; refusal before handoff; commit conflict; `gw-agent` exemption; orphan; repeat dispatch refused.
4. **Completion.** `task_submission_cli.py` + test; `SKILL_GATE_EPOCH`. Positive; missing; wrong digest, run, role; `PREPARED`; legacy; post-epoch without binding; `gw-agent` with and without row; Codex; **both early returns (`:8237`, `:8246`) refuse with no PM or Reviewer call**; auto-callback; repeat.
5. **Reviewer claim.** `review_dispatch.py` + test. Receipt passes; missing, wrong run, digest, role refuse; **`gw-agent` row passes, none refuses; legacy passes**; repeat.
6. **Debug ledger.** `skill_ledger.py`, `skill_manifest.py` + test. `debug` route, fixed pairing; mismatch refused.
7. **Debug entry.** `high_debug.py` + test, plus `task_store.py` + test if `debug_delivery` is a TaskStore field (`UNVERIFIED`; same schema and legacy rules). Row passes; missing or other run's row refuses; **digest from another Debug run refuses `_WRONG_RUN`**; wrong role; legacy; repeat.
8. **Status docs.** `CLAUDE.md`, `AGENTS.md` (PM-owned). Mark blocking `現行` only after PR 4 merges.

Tests follow `common-core.md` §4: no credential-shaped strings; ledger home and Git reads injected; no check weakened.

## 4. Open questions for the PM (default in bold)

1. A crash before the claim leaves `PREPARED`; recovery (`:4813`) is `UNVERIFIED`. **Treat as `DEVELOPER_OUTCOME_UNKNOWN`; never commit in recovery; re-run.**
2. Receipts expire in 24 h; review can take longer. **Ignore expiry, honour revocation.**
3. Do gated commands run as the delegating user (`skill_ledger.py:83-84`)? `UNVERIFIED`. **Require it; else `_LEDGER_UNREADABLE`.**
4. Does a repair keep the `run_id`? `UNVERIFIED`. **Bind to the current `card_delivery`.**
5. PM waiver for a lost receipt? **No; re-run. Exceptions are Owner decisions.**
6. Epoch? **PR 3 merge time, fixed constant.**
7. Log refusals in `missing_gates`? **No; refusals stay write-free.**
8. Does `on_launch` (`:4770-4773`) fire after spawn; can Claude and AGY stop a provider? `UNVERIFIED`. **Claim after spawn; else PR 3 adds a hook. Without a stop, step 7 still stores nothing.**
