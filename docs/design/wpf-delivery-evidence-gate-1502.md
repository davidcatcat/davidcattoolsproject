# WP-F design: delivery evidence gate (#1502)

Status: **proposal** (docs only). Not a review, an acceptance or runtime evidence. No code changes.

- Source: `main` at `a2b6f4d891d89f16f3411122e25f307a54538a01`. Repair 1 is on PR #1977 head `7453964f`, which adds only this file, so code `path:line` values are unchanged. Python paths are under `tools/llmtool/llmtool/`.
- Marks per `CLAUDE.md:5`: `現行`, `待實作（WP-F）`, `待後續`, plus `UNVERIFIED` (not checked from a readable file; `review_dispatch.py`, `high_debug.py`, `task_completion.py` and tests were out of scope).

## 1. Today

- `CLAUDE.md:9`: "[NOT LIVE]: delivery to OpenCode, `gw-agent`, Reviewer, Debug, sub-agent and PM runs (待實作（WP-F）), and missing evidence blocking protected transitions."
- Design §3.1 (`wpf-role-rule-cards.md:122`): "When evidence is missing, the next protected transition refuses: dispatch claim, `task complete`, Reviewer claim or Debug entry." Design §6 (`:265`): "A dispatch, Review claim, Debug entry or `task complete` without matching evidence refuses."

Ledger (`現行`):

- A private, hash-chained file per user (`skill_ledger.py:83-84`). Levels `CONTROLLER_DELIVERED`, `HOOK_DELIVERED`, `OBSERVED_READ`, `UNVERIFIED` (`:42-43`); ten binding fields (`:44-45`).
- `pin_dispatch()` writes one immutable `DISPATCH_PIN` per Task/run/route from authenticated `main`; never evidence (`:521-557`). Only the four Developer routes may pin (`:513`; `skill_manifest.py:50-55`).
- `record_card_delivery()` is the only `CONTROLLER_DELIVERED` writer (`skill_ledger.py:560-638`). It derives cards and digest from the pin and refuses `CALLER_DIGESTS_REFUSED`, `CALLER_BASE_REFUSED`, `DISPATCH_PIN_MISSING`, `ROLE_MISMATCH` (non-Developer, `:598-599`), `PIN_MISMATCH`, `MANIFEST_DIGEST_MISMATCH`. `record()` refuses that level (`:488-489`). `record_not_delivered()` stores `NOT_DELIVERED` (Task, run, role, route, reason) as `UNVERIFIED` (`:641-667`).
- `check()` needs all ten bindings; reasons include `NOT_FOUND`, `WRONG_RUN`, `WRONG_ROLE`, `MANIFEST_CHANGED`, `REVOKED`, `EXPIRED`, `PIN_MISMATCH` (`:711-782`). TTL 86400 s (`:560`).

Delivery by route:

| Route | Code | Evidence |
|---|---|---|
| Claude Developer | Pin and cards before any store write (`task_submission_cli.py:5816`); receipt after the run (`:5908-5909`) | `CONTROLLER_DELIVERED` (`現行`) |
| AGY | `:5345`, `:5440` | `CONTROLLER_DELIVERED` (`現行`) |
| OpenCode | `:4854`, `:4961-4968` | Code writes it; `CLAUDE.md:9` says `[NOT LIVE]`. Live runs `UNVERIFIED`. |
| `gw-agent` | `on_launch` (`:4767-4784`, `:6309`) | `NOT_DELIVERED ROUTE_NOT_SUPPORTED` |
| Codex Developer | `:6691`; no card route (`:4690`); `PAUSED` (`CLAUDE.md:33`) | None |
| Reviewer, Debug | Card builders exist (`skill_manifest.py:467-494`); callers `UNVERIFIED` | None |
| PM, sub-agents | No llmtool path | None (`待後續`) |

Gaps (`現行`): a receipt write failure only prints `<PROVIDER>_SKILL_LEDGER_OUTCOME_UNKNOWN` ("No gate reads this entry yet", `task_submission_cli.py:4750-4764`). No completion path reads the ledger (`:8216`, `:9056`). The hook (`claude_skill_hook.py:484-488`, observe mode `:47-48`) is defence in depth (`CLAUDE.md:10`) and unchanged.

## 2. Gate design (`待實作（WP-F）`)

### 2.1 One eligibility rule

Every transition calls one classifier on the stored run record, never on caller input:

| Class | Condition | Needs |
|---|---|---|
| `RECEIPT_REQUIRED` | Record has `card_delivery` and its `route` equals the route fixed for the record's `provider_profile` (`:4690`) | Matching receipt (§2.3) |
| `EXEMPT` | `provider_profile` is `gw-agent`, `card_delivery = {route: gw-agent, status: NOT_DELIVERED}` | After launch: that run's `NOT_DELIVERED ROUTE_NOT_SUPPORTED` row (Task, run, role, route) |
| `LEGACY_UNGATED` | No `card_delivery`, `created_at` before the fixed `SKILL_GATE_EPOCH` | Nothing; recorded |
| refuse | Anything else | — |

Refusals: `SKILL_DELIVERY_RECORD_MISSING` (no binding at or after the epoch), `_EXEMPTION_INVALID` (exemption on a delivering route, or a binding on `gw-agent`), `_EXEMPTION_MISSING` (no row), `_ROUTE_UNSUPPORTED` (Codex Developer after the epoch). Debug uses the same classes on its own record (§2.5).

### 2.2 Developer receipt lifecycle (fixes dispatch claim)

The receipt moves before the claim, so a claim cannot exist without it:

1. Pin and cards, fail closed (`現行`, `:4711-4738`).
2. The controller makes the Developer record in memory with its `session_id` (`:4865`, `:5356`, `:5827`) and `card_delivery = {route, pin_commit, prefix_sha256, session_id}`.
3. **New:** `record_card_delivery()` before any store write, with that `session_id`, the model, `snapshot.head` and the route. It records that the controller bound these exact cards into the prompt it sends; as today, not that the model read them. The post-run writes (`:4961-4968`, `:5440`, `:5908-5909`) are removed; the native session id stays in `developer.execution`.
4. **New:** `card_receipt_status()` for that binding must be `VERIFIED`, else refuse: no store write, no record, no launch. A failed append resolves on read: refuse only if no matching line persisted.
5. The creating store write (`:4898-4903`, `:5855-5858`; AGY `UNVERIFIED` line) is the dispatch claim. It stores `card_delivery`.
6. **New:** re-check immediately before launch, in `route_gate` (Claude `:5877-5889`; AGY `:5438`) and OpenCode `prelaunch_validate` (`:4915-4921`). A refusal takes the route's existing pre-launch refusal path. No provider is launched.

`gw-agent` skips 3, 4 and 6: its claim stores the exemption; its row stays on `on_launch` (`:6309`). An orphan receipt (step 5 lost a duplicate race, `:4904-4906`) has a session id no record stores, so it never matches.

### 2.3 Verifier

New read-only `skill_ledger.card_receipt_status(task_id, run_id, role, route, session_id, prefix_sha256, pin_commit)`; it never appends. `VERIFIED` only for a non-revoked `CONTROLLER_DELIVERED` row whose Task, run, role, route, session, `manifest_sha256` and `base_commit` all match, with its pin still in the chain (`:690-703`). Expiry is ignored (Q2). Codes: `SKILL_DELIVERY_RECEIPT_MISSING`, `_WRONG_RUN`, `_WRONG_SESSION`, `_WRONG_ROLE`, `_DIGEST_MISMATCH`, `_PIN_MISMATCH`, `_REVOKED`, `_LEDGER_UNREADABLE`. New `not_delivered_status(task_id, run_id, role, route)` finds an exemption row.

### 2.4 Protected transitions

| Transition | Check point | Evidence (run, role, digest, commit) | Refusal |
|---|---|---|---|
| Dispatch claim | §2.2 steps 4 and 6 | Receipt for this Task `run_id`, role `developer`, controller session, prefix digest, pin | `SKILL_DELIVERY_*`; existing `<PROVIDER>_SKILL_PIN_FAILED`, `_ROLE_CARD_MISSING`, `_SKILL_TEXT_REFUSED` |
| `task complete` | One helper at **both** entry points: `run_task_complete` after `:8228-8231`, before the direct-callback branches (`:8237`, `:8246`); `run_task_direct_result_event` after `:9076-9079`, before `:9081` and the `_resume_reviewer_handoff` branch (`:9131-9135`). Covers both early returns (`:8244`, `:8252`), delegate auto-callbacks (`:5108` … `:7025`) and `:9379`. | §2.1 class of the current Developer record | `SKILL_DELIVERY_*` |
| Reviewer claim | Before the claim is written (`review_dispatch.py`, `UNVERIFIED`) | Same helper and class. Own delivery: recorded `reviewer_rules: NOT_DELIVERED`. | `SKILL_DELIVERY_*`; no claim |
| Debug entry | Before a Debug report is recorded as able to allow the second repair (`high_debug.py`, `UNVERIFIED`) | §2.5 | `SKILL_DELIVERY_*`, `_DEBUG_RUN_MISMATCH` |

Every refusal comes before any store write or PM, Reviewer or provider call, so recovery branches keep their state.

### 2.5 Debug entry binding

- At the Debug claim, before launch, the controller makes a fresh `debug_run_id` and stores `debug_delivery` in the claim record. Neither comes from the report or a caller.
- Delivered (when Debug delivery ships): `pin_dispatch(task_id, debug_run_id, 'debug')`, `build_debug_cards()`, then `record_card_delivery()` with role and route `debug` (code fixes the route-role pairing). Binding `{debug_run_id, route, session_id, pin_commit, prefix_sha256}`, checked as in §2.2 steps 3–4 before the claim.
- Until then (exemption): `debug_delivery = {debug_run_id, route: debug, status: NOT_DELIVERED}` plus a `NOT_DELIVERED ROUTE_NOT_SUPPORTED` row for that `debug_run_id`, role `debug`, at launch.
- At entry, the entry's `debug_run_id` must equal the claim's (`SKILL_DELIVERY_DEBUG_RUN_MISMATCH`); then the claim's binding is checked with `card_receipt_status` (role `debug`) or `not_delivered_status`. A digest in the report is ignored. A digest copied from another Debug run matches only that run's receipt (other run and session), so it refuses `_WRONG_RUN`.
- No Developer receipt is needed, so Debug can diagnose a run that lacks one. The second repair is a new Developer run with its own dispatch gate.

### 2.6 Routes without delivery

| Route | Behaviour | Reason |
|---|---|---|
| Claude Developer, AGY, OpenCode | Fail closed | All write receipts (Q1) |
| `gw-agent` | Recorded exemption at every transition | No rules field (design §3.7); a preferred route (`CLAUDE.md:33`) |
| Reviewer | Own delivery exempt; Developer class still applies | No non-Developer ledger path (`skill_ledger.py:598-599`) |
| Debug | Recorded exemption (§2.5) | Same; on the repair path (`CLAUDE.md:36`) |
| Codex Developer | Refused after the epoch | Route is `PAUSED` (`CLAUDE.md:33`) |
| PM, sub-agents | Not gated (`待後續`) | No protected transition carries their runs |

Each exemption becomes fail-closed in the PR that ships that route's delivery. It is visible metadata, never evidence, and grants nothing.

### 2.7 Legacy, idempotence, invariant

- **Legacy.** A Task created before the epoch passes every transition as `LEGACY_UNGATED`, including Reviewer claim and Debug entry. Dropping the binding after the epoch refuses, so it cannot fake legacy status. Merged work is never re-checked: the gate runs only on transitions not yet taken, and replay is already refused (`:8261-8262`, `:9136-9137`).
- **Idempotence.** Verifiers are read-only, so a repeat gives the same code. Duplicate dispatch is refused (`:4904-4906`); pins are immutable (`skill_ledger.py:526-528`).
- **Invariant.** No dispatch claim, `task complete` path, Reviewer claim or Debug entry succeeds unless its run's class is `RECEIPT_REQUIRED` with a non-revoked receipt matching the stored Task, run, role, route, session, digest and pin, or `EXEMPT` with its run's `NOT_DELIVERED` row, or `LEGACY_UNGATED`.

### 2.8 PM view and limits

- The command exits non-zero with the code. A refused dispatch leaves no Developer record (Task stays `PM_RESPONDED`); a pre-launch refusal leaves the route's existing blocked state; other refusals leave the Task unchanged. The remedy is a new run; nobody can write `CONTROLLER_DELIVERED` by hand (`skill_ledger.py:488-489`).
- No new authority: a receipt only removes one refusal. No change to the dispatch gate, D6 binding, tests, review, CI or merge gates. Same-user tamper limits remain (`skill_ledger.py:3-7`).

## 3. Implementation plan (≤ 4 files per PR; test modules under `tools/llmtool/tests/`)

1. **Verifier.** `skill_ledger.py` + test. Positive; missing; wrong digest, run, session, role; pin mismatch; revoked; `NOT_DELIVERED` never verifies; repeated call leaves ledger bytes unchanged.
2. **Dispatch claim.** `task_submission_cli.py` + test. Classifier, pre-claim receipt, binding, pre-launch re-check. Positive; **missing receipt (append not persisted) refuses with no record and no launch**; wrong digest; another run's or session's receipt; wrong role; revoked before launch, no launch; `gw-agent` stores the exemption; orphan receipt never matches; repeated delegate refused.
3. **Completion.** `task_submission_cli.py` + test; `SKILL_GATE_EPOCH`. Positive; missing; wrong digest, run, role; legacy; post-epoch record without binding; `gw-agent` with and without its row; invalid exemption; Codex; **both early returns (`:8237`, `:8246`) refuse with no PM or Reviewer call**; auto-callback refuses; repeated call same code, Task unchanged.
4. **Reviewer claim.** `review_dispatch.py` + test. Receipt passes; missing, wrong run, digest or role refuse with no claim; **`gw-agent` exempt passes, without its row refuses; legacy passes**; repeated call.
5. **Debug ledger.** `skill_ledger.py`, `skill_manifest.py` + test. `debug` pin route, fixed route-role pairing. Debug receipt positive; each mismatched pairing refused.
6. **Debug entry.** `high_debug.py` + test. Exemption with row passes; row missing or for another `debug_run_id` refuses; receipt passes; missing; wrong digest; **digest copied from another Debug run refuses `_WRONG_RUN`**; wrong role; entry run differs from claim; legacy; repeated call.
7. **Status docs.** `CLAUDE.md`, `AGENTS.md` (PM-owned). Mark blocking `現行` only after PR 3 merges.

Tests follow the test data rule (`common-core.md` §4): no credential-shaped strings; ledger home and Git reads injected; no check weakened.

## 4. Open questions for the PM (recommended default in bold)

1. `CLAUDE.md:9` says OpenCode is `[NOT LIVE]`, but the code delivers. **Fail closed; confirm with one observed run; fix the text in PR 7.**
2. Receipts expire after 24 h; review can take longer. **Ignore expiry, honour revocation.**
3. Does every gated command run on the delegating host and user? The ledger is per user (`skill_ledger.py:83-84`). `UNVERIFIED`. **Require it; elsewhere refuse `_LEDGER_UNREADABLE`.**
4. Does a repair keep the Task `run_id`? `UNVERIFIED`. **Bind to the current Developer record's `card_delivery`.**
5. PM waiver for a lost receipt? **No. Re-run. Any exception is an Owner decision.**
6. Epoch value? **Merge time of PR 2, as a fixed constant.**
7. Should a refusal also be written to `missing_gates`? **No. Keep refusals write-free so recovery branches keep their state.**
