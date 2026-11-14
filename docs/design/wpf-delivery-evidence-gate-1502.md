# WP-F design: delivery evidence gate (#1502)

Status: **proposal** (docs only). Not a review, an acceptance or runtime evidence. No code changes.

- Source: `main` at `a2b6f4d891d89f16f3411122e25f307a54538a01`. Repair 1 (`7453964f`) fixed F1–F4; repair 2 (`437451aa`) separates card preparation from delivery. Code lines spot-checked at `437451aa`. Python paths are under `tools/llmtool/llmtool/`.
- Marks per `CLAUDE.md:5`: `現行`, `待實作（WP-F）`, `待後續`, plus `UNVERIFIED` (file not readable here: `review_dispatch.py`, `high_debug.py`, `task_completion.py`, the route runner, tests).

## 1. Today

- `CLAUDE.md:9`: "[NOT LIVE]: delivery to OpenCode, `gw-agent`, Reviewer, Debug, sub-agent and PM runs (待實作（WP-F）), and missing evidence blocking protected transitions."
- Design §3.1 (`wpf-role-rule-cards.md:122`): "When evidence is missing, the next protected transition refuses: dispatch claim, `task complete`, Reviewer claim or Debug entry." Design §6 (`:265`): "A dispatch, Review claim, Debug entry or `task complete` without matching evidence refuses."

Ledger (`現行`):

- Private, hash-chained, per user (`skill_ledger.py:83-84`). Levels `CONTROLLER_DELIVERED`, `HOOK_DELIVERED`, `OBSERVED_READ`, `UNVERIFIED` (`:42-43`); ten binding fields (`:44-45`).
- `pin_dispatch()` writes one immutable `DISPATCH_PIN` per Task/run/route from authenticated `main`; never evidence (`:521-557`). Only the four Developer routes pin (`:513`; `skill_manifest.py:50-55`).
- `record_card_delivery()` is the only `CONTROLLER_DELIVERED` writer (`skill_ledger.py:560-638`). It derives cards and digest from the pin and sends nothing. Refusals: `CALLER_DIGESTS_REFUSED`, `CALLER_BASE_REFUSED`, `DISPATCH_PIN_MISSING`, `ROLE_MISMATCH` (non-Developer, `:598-599`), `PIN_MISMATCH`, `MANIFEST_DIGEST_MISMATCH`. `record()` refuses that level (`:488-489`). `record_not_delivered()` stores `NOT_DELIVERED` (Task, run, role, route, reason) as `UNVERIFIED` (`:641-667`).
- `check()` reasons include `NOT_FOUND`, `WRONG_RUN`, `WRONG_ROLE`, `MANIFEST_CHANGED`, `REVOKED`, `EXPIRED`, `PIN_MISMATCH` (`:711-782`). TTL 86400 s (`:560`).

| Route | Code | Evidence |
|---|---|---|
| Claude Developer | Cards before any store write (`task_submission_cli.py:5816`); receipt after the run (`:5908-5909`) | `CONTROLLER_DELIVERED` (`現行`) |
| AGY | `:5345`, `:5440` | `CONTROLLER_DELIVERED` (`現行`) |
| OpenCode | `:4854`, `:4961-4968` | Code writes it; `CLAUDE.md:9` says `[NOT LIVE]`; live runs `UNVERIFIED` |
| `gw-agent` | `on_launch` (`:4767-4784`, `:6309`) | `NOT_DELIVERED ROUTE_NOT_SUPPORTED` |
| Codex Developer | No card route (`:4690`); `PAUSED` (`CLAUDE.md:33`) | None |
| Reviewer, Debug | Card builders (`skill_manifest.py:467-494`); callers `UNVERIFIED` | None |
| PM, sub-agents | No llmtool path | None (`待後續`) |

Gaps (`現行`): a failed receipt write only prints `<PROVIDER>_SKILL_LEDGER_OUTCOME_UNKNOWN` ("No gate reads this entry yet", `task_submission_cli.py:4750-4764`). No completion path reads the ledger (`:8216`, `:9056`). The hook (`claude_skill_hook.py:484-488`, observe mode `:47-48`) stays defence in depth (`CLAUDE.md:10`).

## 2. Gate design (`待實作（WP-F）`)

### 2.1 One eligibility rule

Every transition classifies the stored run record, never caller input:

| Class | Condition | Needs |
|---|---|---|
| `RECEIPT_REQUIRED` | `card_delivery` present; its `route` is the one fixed for `provider_profile` (`:4690`) | Handoff receipt (§2.3) |
| `EXEMPT` | `gw-agent` with `card_delivery = {route: gw-agent, status: NOT_DELIVERED}` | After launch, that run's `NOT_DELIVERED` row |
| `LEGACY_UNGATED` | No `card_delivery`; `created_at` before the fixed `SKILL_GATE_EPOCH` | Nothing; recorded |
| refuse | Anything else | — |

Codes: `SKILL_DELIVERY_RECORD_MISSING` (no binding after the epoch), `_EXEMPTION_INVALID` (wrong route for it), `_EXEMPTION_MISSING` (no row), `_ROUTE_UNSUPPORTED` (Codex after the epoch).

### 2.2 Developer lifecycle: preparation, then handoff

Preparation is not delivery. The claim sits between them:

1. Pin and cards, fail closed (`現行`, `:4711-4738`). The in-memory record gets `session_id` (`:4865`, `:5356`, `:5827`) and `card_delivery = {route, pin_commit, prefix_sha256, session_id}`.
2. **New** `prepare_card_delivery()`: today's derivation and checks (`skill_ledger.py:583-630`) append a `CARD_PREPARED` row, level `UNVERIFIED`. It is a reservation that no verifier accepts.
3. **Claim check.** The reservation must match the binding, else `SKILL_DELIVERY_PREPARATION_MISSING`, with no store write and no launch. The creating store write (`:4898-4903`, `:5855-5858`) is the claim and stores `card_delivery`.
4. Existing pre-launch checks, unchanged: `route_gate` (Claude `:5877-5889`, AGY `:5426`) and OpenCode `prelaunch_validate` (`:4915-4921`).
5. **Handoff.** Only after the provider process starts, `confirm_card_delivery()` (refactored `record_card_delivery()`, still the only writer) appends `CONTROLLER_DELIVERED`. It copies the prepared row and pin, with no caller digest and no Git read. OpenCode calls it in `launch_callback` (`:4936-4953`, after spawn); Claude and AGY in a post-spawn callback (Q8). It means the controller handed this prompt to a started provider, not that the model read it. The post-run writes (`:4961-4968`, `:5440`, `:5908-5909`) go.
6. **Result check.** Before any result is stored, `card_receipt_status()` must be `VERIFIED`, else `DEVELOPER_OUTCOME_UNKNOWN` with `SKILL_DELIVERY_RECEIPT_MISSING` and no artifact. A failed step-5 append never stops the launch; it resolves here on read.

A refusal before handoff (steps 3–4, failed spawn) leaves at most `CARD_PREPARED`, which never verifies. `gw-agent` skips 2, 3, 5 and 6; its claim stores the exemption and its row stays on `on_launch` (`:6309`). Orphan rows (lost duplicate race, `:4904-4906`) carry a session no record stores.

### 2.3 Verifiers (read-only)

- `card_receipt_status(task_id, run_id, role, route, session_id, prefix_sha256, pin_commit)`: `VERIFIED` only for a non-revoked `CONTROLLER_DELIVERED` row matching all of these, with its pin still in the chain (`:690-703`). Expiry ignored (Q2).
- `card_preparation_status(...)` (same arguments) matches `CARD_PREPARED`, for step 3 only. `not_delivered_status(task_id, run_id, role, route)` finds an exemption row.
- Codes: `SKILL_DELIVERY_RECEIPT_MISSING`, `_PREPARATION_MISSING`, `_WRONG_RUN`, `_WRONG_SESSION`, `_WRONG_ROLE`, `_DIGEST_MISMATCH`, `_PIN_MISMATCH`, `_REVOKED`, `_LEDGER_UNREADABLE`.

### 2.4 Protected transitions

| Transition | Check point | Evidence | Refusal |
|---|---|---|---|
| Dispatch claim | §2.2 step 3 (before claim), step 6 (before result) | Reservation, then handoff receipt: this `run_id`, role `developer`, session, digest, pin | `SKILL_DELIVERY_*`; existing `<PROVIDER>_SKILL_PIN_FAILED`, `_ROLE_CARD_MISSING`, `_SKILL_TEXT_REFUSED` |
| `task complete` | One helper at **both** entries: `run_task_complete` after `:8228-8231`, before `:8237`, `:8246`; `run_task_direct_result_event` after `:9076-9079`, before `:9081` and `:9131-9135`. Covers early returns `:8244`, `:8252`, auto-callbacks and `:9379` | §2.1 class of the current Developer record | `SKILL_DELIVERY_*` |
| Reviewer claim | Before the claim write (`review_dispatch.py`, `UNVERIFIED`) | Same helper; own delivery recorded `NOT_DELIVERED` | `SKILL_DELIVERY_*`; no claim |
| Debug entry | Before a report can allow the second repair (`high_debug.py`, `UNVERIFIED`) | §2.5 | `SKILL_DELIVERY_*`, `_DEBUG_RUN_MISMATCH` |

Refusals precede any store write or PM, Reviewer or provider call, except step 6, which records the run's own outcome.

### 2.5 Debug entry binding

- At the Debug claim, before launch, the controller makes a fresh `debug_run_id` and stores `debug_delivery`. Neither comes from the report or a caller.
- Delivered (later): pin with route `debug`, `build_debug_cards()`, then §2.2 preparation and handoff, role and route `debug` (fixed pairing). Binding `{debug_run_id, route, session_id, pin_commit, prefix_sha256}`.
- Until then: `debug_delivery = {debug_run_id, route: debug, status: NOT_DELIVERED}` plus a `NOT_DELIVERED` row at launch for that `debug_run_id`, role `debug`.
- At entry, its `debug_run_id` must equal the claim's (`_DEBUG_RUN_MISMATCH`); then `card_receipt_status` (role `debug`) or `not_delivered_status` checks the claim's binding. Report digests are ignored. A digest copied from another Debug run matches only that run, so it refuses `_WRONG_RUN`.
- No Developer receipt is needed. The second repair is a new Developer run with its own gate.

### 2.6 Routes without delivery

| Route | Behaviour | Reason |
|---|---|---|
| Claude Developer, AGY, OpenCode | Fail closed | All write receipts (Q1) |
| `gw-agent` | Recorded exemption everywhere | No rules field (design §3.7); preferred route (`CLAUDE.md:33`) |
| Reviewer | Own delivery exempt; Developer class applies | No non-Developer ledger path (`skill_ledger.py:598-599`) |
| Debug | Recorded exemption (§2.5) | Same; repair path (`CLAUDE.md:36`) |
| Codex Developer | Refused after the epoch | `PAUSED` (`CLAUDE.md:33`) |
| PM, sub-agents | Not gated (`待後續`) | No protected transition carries their runs |

An exemption becomes fail-closed in the PR that ships that route's delivery. It is visible metadata, never evidence.

### 2.7 Legacy, idempotence, invariant

- **Legacy.** Tasks created before the epoch pass every transition as `LEGACY_UNGATED`. A post-epoch record without a binding refuses, so legacy status cannot be faked. Merged work is never re-checked; replay is already refused (`:8261-8262`, `:9136-9137`).
- **Idempotence.** Verifiers never append; a repeat gives the same code. Duplicate dispatch is refused (`:4904-4906`); pins are immutable (`skill_ledger.py:526-528`).
- **Invariant.** A claim needs its matching reservation. No Developer result, `task complete` path, Reviewer claim or Debug entry succeeds unless its run is `RECEIPT_REQUIRED` with a non-revoked `CONTROLLER_DELIVERED` row written after provider start and matching the stored Task, run, role, route, session, digest and pin; or `EXEMPT` with its `NOT_DELIVERED` row; or `LEGACY_UNGATED`.

### 2.8 PM view and limits

- The command exits non-zero with the code. A refused claim leaves no Developer record (`PM_RESPONDED`); a pre-launch refusal keeps the route's blocked state; step 6 leaves `DEVELOPER_OUTCOME_UNKNOWN`; others leave the Task unchanged. Remedy: a new run. `CONTROLLER_DELIVERED` cannot be written by hand (`skill_ledger.py:488-489`).
- No new authority; no change to dispatch, D6, test, review, CI or merge gates. Same-user tamper limits remain (`skill_ledger.py:3-7`).

## 3. Implementation plan (≤ 4 files per PR; tests in `tools/llmtool/tests/`)

1. **Ledger.** `skill_ledger.py` + test. Prepare then confirm verifies; `CARD_PREPARED` alone never verifies; confirm without it refuses; missing; wrong digest, run, session, role; pin mismatch; revoked; `NOT_DELIVERED` never verifies; repeat leaves bytes unchanged.
2. **Dispatch claim.** `task_submission_cli.py` + test (+ route runner if it needs a post-spawn hook, `UNVERIFIED`). Positive; **missing reservation: no record, no launch**; **refusal before handoff (gate denied, boundary, D6 change, spawn failure) leaves no verifying row**; **failed handoff append: `DEVELOPER_OUTCOME_UNKNOWN`, no artifact**; wrong digest; other run's or session's receipt; wrong role; `gw-agent` exemption stored; orphan never matches; repeat refused.
3. **Completion.** `task_submission_cli.py` + test; `SKILL_GATE_EPOCH`. Positive; missing; wrong digest, run, role; legacy; post-epoch without binding; `gw-agent` with and without row; invalid exemption; Codex; **both early returns (`:8237`, `:8246`) refuse with no PM or Reviewer call**; auto-callback refuses; repeat gives the same code.
4. **Reviewer claim.** `review_dispatch.py` + test. Receipt passes; missing, wrong run, digest, role refuse; **`gw-agent` exempt passes, without row refuses; legacy passes**; repeat.
5. **Debug ledger.** `skill_ledger.py`, `skill_manifest.py` + test. `debug` route, fixed pairing; mismatched pairing refused.
6. **Debug entry.** `high_debug.py` + test. Exemption with row passes; missing or other `debug_run_id` row refuses; receipt passes; missing; wrong digest; **digest copied from another Debug run refuses `_WRONG_RUN`**; wrong role; entry run differs; legacy; repeat.
7. **Status docs.** `CLAUDE.md`, `AGENTS.md` (PM-owned). Mark blocking `現行` only after PR 3 merges.

Tests follow `common-core.md` §4: no credential-shaped strings; ledger home and Git reads injected; no check weakened.

## 4. Open questions for the PM (default in bold)

1. `CLAUDE.md:9` says OpenCode is `[NOT LIVE]`; code delivers. **Fail closed; confirm by one observed run; fix text in PR 7.**
2. Receipts expire in 24 h; review can take longer. **Ignore expiry, honour revocation.**
3. Does every gated command run as the delegating host user (`skill_ledger.py:83-84`)? `UNVERIFIED`. **Require it; else `_LEDGER_UNREADABLE`.**
4. Does a repair keep the Task `run_id`? `UNVERIFIED`. **Bind to the current record's `card_delivery`.**
5. PM waiver for a lost receipt? **No; re-run. Exceptions are Owner decisions.**
6. Epoch? **PR 2 merge time, fixed constant.**
7. Log refusals in `missing_gates`? **No; refusals stay write-free.**
8. Does `run_route`'s `on_launch` (`:4770-4773`) fire after spawn? `UNVERIFIED`. **Confirm after spawn, as OpenCode; else PR 2 adds a post-spawn hook.**
