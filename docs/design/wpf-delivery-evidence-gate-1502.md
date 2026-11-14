# WP-F design: delivery evidence gate (#1502)

Status: **proposal** (docs only). Not a review, an acceptance or runtime evidence. No code changes.

- Source: `main` at `a2b6f4d891d89f16f3411122e25f307a54538a01`. Every `path:line` is on that commit. Python paths are under `tools/llmtool/llmtool/`.
- Author: Claude Developer, Task `fe0c72bd9d0843078d0c122c305f5814`.
- Marks per `CLAUDE.md:5`: `現行`, `待實作（WP-F）`, `待後續`, plus `UNVERIFIED` (not checked from a file this Task could read). `review_dispatch.py`, `high_debug.py`, `task_completion.py` and the tests were out of scope.

## 1. Today

### 1.1 Rule and design

- `CLAUDE.md:9`: "[NOT LIVE]: delivery to OpenCode, `gw-agent`, Reviewer, Debug, sub-agent and PM runs (待實作（WP-F）), and missing evidence blocking protected transitions."
- Design §3.1 (`wpf-role-rule-cards.md:122`): "When evidence is missing, the next protected transition refuses: dispatch claim, `task complete`, Reviewer claim or Debug entry."
- Design §6 (`wpf-role-rule-cards.md:265`): "A dispatch, Review claim, Debug entry or `task complete` without matching evidence refuses."

### 1.2 What the ledger records (`現行`)

- **Store.** A private, hash-chained file per user under `~/.local/state/llmtool/` (`skill_ledger.py:83-84`, `:229-241`).
- **Levels.** `CONTROLLER_DELIVERED`, `HOOK_DELIVERED`, `OBSERVED_READ`, `UNVERIFIED` (`:42-43`). Ten binding fields: Task, run, session, agent, role, provider, model, repo, head, manifest sha256 (`:44-45`).
- **Pin.** `pin_dispatch()` writes one immutable `DISPATCH_PIN` per Task/run/route with the authenticated `main` commit. A pin is never evidence (`:13-22`, `:521-557`). Pins are allowed only for the role-card routes `agy`, `claude-developer`, `gw-agent`, `opencode-developer` (`:513`; `skill_manifest.py:50-55`).
- **Append path.** `record_card_delivery()` is the only `CONTROLLER_DELIVERED` writer (`skill_ledger.py:560-638`). It derives cards and digests from the pinned commit itself. It refuses `CALLER_DIGESTS_REFUSED`, `CALLER_BASE_REFUSED`, `DISPATCH_PIN_MISSING`, `ROLE_MISMATCH` (non-Developer), `PIN_MISMATCH` and `MANIFEST_DIGEST_MISMATCH`.
- **Other refusals.** `record()` refuses that level with `CONTROLLER_DELIVERED_REFUSED` (`:488-489`). `record_not_delivered()` writes `NOT_DELIVERED` with a fixed reason, stored as `UNVERIFIED` (`:52-58`, `:641-667`).
- **Check.** `check()` needs all ten bindings and returns `VERIFIED` or a reason such as `NOT_FOUND`, `WRONG_RUN`, `WRONG_ROLE`, `MANIFEST_CHANGED`, `REVOKED`, `EXPIRED` or `PIN_MISMATCH` (`:711-782`). Receipts without `route` are not re-validated (`:690-703`). The default TTL is 86400 s (`:560`).

### 1.3 Delivery by route

| Route | Code | Evidence |
|---|---|---|
| Claude Developer | Pin and cards are built before any store write, fail closed (`task_submission_cli.py:5816`; `:4701-4706`). The receipt is written after the run (`:5908-5909`). | `CONTROLLER_DELIVERED` (`現行`) |
| AGY | `task_submission_cli.py:5345`, `:5440` | `CONTROLLER_DELIVERED` (`現行`) |
| OpenCode | `task_submission_cli.py:4852-4859`, `:4961-4968` | The code writes `CONTROLLER_DELIVERED`, but `CLAUDE.md:9` says `[NOT LIVE]`. Live runs are `UNVERIFIED`. |
| `gw-agent` | `task_submission_cli.py:4767-4784`, `:6309` | `NOT_DELIVERED ROUTE_NOT_SUPPORTED` |
| Reviewer | `build_review_cards()` exists (`skill_manifest.py:483-494`). No read file calls it. `record_card_delivery()` refuses non-Developer roles (`skill_ledger.py:598-599`). | None |
| Debug | `build_debug_cards()` exists (`skill_manifest.py:467-480`). Its caller is `UNVERIFIED`. | None |
| PM, sub-agents | No llmtool path | None (`待後續`) |

Gaps (`現行`):

- A receipt write failure only prints `<PROVIDER>_SKILL_LEDGER_OUTCOME_UNKNOWN`. The code says "No gate reads this entry yet" (`task_submission_cli.py:4750-4764`).
- `run_task_complete` (`:8216-8492`) never reads the ledger.
- The hook checks `dispatch`/`complete` commands lexically (`claude_skill_hook.py:484-488`) against `OBSERVED_READ` rows (`:305-366`). It defaults to observe mode (`:47-48`) and is defence in depth only (`CLAUDE.md:10`). This design does not change the hook.

## 2. Gate design (`待實作（WP-F）`)

### 2.1 Binding at dispatch

The write that creates the Developer record adds `card_delivery`: `{route, pin_commit, prefix_sha256}`. The values come from the pin and the digest returned at `task_submission_cli.py:4738`. For `gw-agent` it is `{route, status: NOT_DELIVERED}`. The gates compare the ledger with this record, never with caller input.

### 2.2 Verifier

New read-only `skill_ledger.card_receipt_status(task_id, run_id, route, prefix_sha256, pin_commit)`. It takes the lock, reads the chain and never appends. It returns `VERIFIED` only for a non-revoked `CONTROLLER_DELIVERED` row with `route` whose Task, run, role `developer`, `manifest_sha256` and `base_commit` all match, and whose pin is still in the chain (`skill_ledger.py:690-703`). Unlike `check()`, it does not need session, model or head, because the Task record has none of them at dispatch.

### 2.3 Protected transitions

| Transition | Check point | Evidence | Refusal codes |
|---|---|---|---|
| Dispatch claim | In each delegate after `_developer_card_prefix` (`task_submission_cli.py:4854`, `:5345`, `:5816`), inside the first store write (`:4898-4903` for OpenCode). The pin and card build already fail closed (`現行`). New: store `card_delivery`. A record for the same run with a different pin or digest refuses. | Run = Task `run_id`, role `developer`, digest = prefix sha256, commit = pin. This is a binding, not proof of delivery. | Existing `<PROVIDER>_SKILL_PIN_FAILED`, `_ROLE_CARD_MISSING`, `_SKILL_TEXT_REFUSED`; new `SKILL_DELIVERY_BINDING_CONFLICT` |
| `task complete` | `run_task_complete`, inside the evidence `try` (`:8307-8471`). The code is added to `missing`. | `card_receipt_status` = `VERIFIED` for the stored binding | `SKILL_DELIVERY_RECEIPT_MISSING`, `_DIGEST_MISMATCH`, `_WRONG_RUN`, `_WRONG_ROLE`, `_PIN_MISMATCH`, `_REVOKED`, `_LEDGER_UNREADABLE`, `_RECORD_MISSING` |
| Reviewer claim | Before the review dispatch claim is written (`review_dispatch.py`, `UNVERIFIED`) | The authored run's Developer receipt. Later, also a Reviewer receipt bound to the claim (design §3.7). | Same `SKILL_DELIVERY_*` codes. No claim is written on refusal. |
| Debug entry | Before a Debug report is recorded as able to allow the second repair (`high_debug.py`, `UNVERIFIED`) | The Debug cards digest in the entry (design §3.7). No Developer receipt is needed, so Debug can still diagnose a run that lacks one. | `DEBUG_SKILL_EVIDENCE_MISSING` |

### 2.4 Routes without delivery

| Route | Behaviour | Reason |
|---|---|---|
| Claude Developer, AGY, OpenCode | Fail closed. | The code writes receipts for all three (§1.3; Q1). |
| `gw-agent` | Recorded exemption. `task complete` passes only with this run's `NOT_DELIVERED ROUTE_NOT_SUPPORTED` row. Otherwise it refuses with `SKILL_DELIVERY_EXEMPTION_MISSING`. Evidence shows `NOT_DELIVERED_EXEMPT`. | The gateway has no rules field (design §3.7). Failing closed would stop a route the PM should prefer (`CLAUDE.md:33`). |
| Reviewer | Recorded exemption for its own delivery (`reviewer_rules: NOT_DELIVERED`). The Developer receipt is still required. | There is no non-Developer ledger path (`skill_ledger.py:598-599`). Failing closed would stop all formal review. |
| Debug | Recorded exemption (`debug_rules: NOT_DELIVERED`) | Same reason. Debug is on the repair path (`CLAUDE.md:36`). |
| PM, sub-agents | Not gated (`待後續`) | No protected transition carries their runs. |

Each exemption becomes fail-closed in the PR that ships that route's delivery. An exemption is visible metadata. It is never evidence and grants nothing.

### 2.5 Legacy Tasks

- A Developer record without `card_delivery` whose `created_at` (`task_submission_cli.py:4874`) is before a fixed `SKILL_GATE_EPOCH` is legacy. `task complete` records `LEGACY_UNGATED` and does not refuse.
- A record without `card_delivery` at or after the epoch refuses with `SKILL_DELIVERY_RECORD_MISSING`, so dropping the field cannot fake legacy status.
- Merged work is never re-checked. The gate runs only on transitions that have not happened yet, and `task complete` already refuses replay (`:8261-8262`). Pre-pin rows stay unvalidated (`skill_ledger.py:22-23`).

### 2.6 Idempotence

- The verifier is read-only, so a repeated `task complete` gives the same code for the same ledger. Lock-time torn-append repair is the existing recovery (`skill_ledger.py:8-9`).
- An `OUTCOME_UNKNOWN` append resolves on read: if the line persisted, the gate passes.
- A duplicate dispatch is already refused (`task_submission_cli.py:4904-4906`), and pins are immutable (`skill_ledger.py:526-528`).

### 2.7 Invariant

For a run on a delivering route, no protected transition succeeds without a non-revoked `CONTROLLER_DELIVERED` receipt. The receipt must match the Task, run, role `developer`, route, prefix digest and pinned commit stored at dispatch, with its pin still in the chain. For a non-delivering route, the transition needs that run's `NOT_DELIVERED` row or a recorded exemption, and the Task view shows it.

### 2.8 What the PM sees

`task complete` exits 1 and sets `WAITING_EXECUTOR`, because these codes do not start with `REVIEW_`, `PR_` or `CI_`. It prints the Task view with `missing_gates` (`task_submission_cli.py:8478-8492`). The remedy is a new Developer run. Nobody can write `CONTROLLER_DELIVERED` by hand (`skill_ledger.py:488-489`).

### 2.9 Limits

- No new authority: a receipt allows nothing; it only removes one refusal.
- No change to the dispatch gate, D6 binding, tests, review, CI or merge gates.
- The ledger's same-user tamper limits still apply (`skill_ledger.py:3-7`).

## 3. Implementation plan (≤ 4 files per PR; test modules under `tools/llmtool/tests/`, names proposed)

1. **Verifier.** Files: `skill_ledger.py` and 1 test module. Adds `card_receipt_status` and `not_delivered_status`. Tests: positive; missing receipt; wrong digest; wrong run (another run of the same Task); wrong role; pin mismatch; revoked; `NOT_DELIVERED` never verifies; repeated call leaves the ledger bytes unchanged.
2. **Dispatch binding.** Files: `task_submission_cli.py` and 1 test module. Stores `card_delivery` for all four Developer routes. Tests: binding written in the creating write; conflicting digest refuses with `SKILL_DELIVERY_BINDING_CONFLICT`; repeated delegate refused as a duplicate; pin failure writes no record.
3. **`task complete` gate.** Files: `task_submission_cli.py` and 1 test module. Adds `SKILL_GATE_EPOCH` and the codes. Tests: positive; missing receipt; wrong digest; wrong run; wrong role; legacy Task passes as `LEGACY_UNGATED`; post-epoch record without binding refuses; `gw-agent` with and without its `NOT_DELIVERED` row; repeated call gives the same result.
4. **Reviewer claim gate.** Files: `review_dispatch.py` and 1 test module. Tests: receipt present passes; missing receipt, wrong run or wrong digest refuses with no claim written; exemption recorded; repeated call gives the same result.
5. **Debug entry gate.** Files: `high_debug.py` and 1 test module. Tests: digest present passes; missing digest refuses once delivery is live; exemption recorded before that; repeated call gives the same result.
6. **Status docs.** Files: `CLAUDE.md` and `AGENTS.md` (rule files, PM-owned). Mark the blocking `現行` only after PR 3 merges.

Tests follow the test data rule (`common-core.md` §4): no credential-shaped strings; ledger home and Git reads injected; no production check weakened.

## 4. Open questions for the PM (recommended default in bold)

1. `CLAUDE.md:9` says OpenCode is `[NOT LIVE]`, but the code delivers (§1.3). **Fail closed. Confirm with one observed run, then fix `CLAUDE.md:9` in PR 6.**
2. Receipts expire after 24 h (`skill_ledger.py:560`), and a review cycle can take longer. **The gate ignores expiry and honours revocation.**
3. Does `task complete` run on the same host and user as `task delegate`? The ledger is local to the user (`skill_ledger.py:83-84`). `UNVERIFIED`. **Require the same host. Elsewhere, refuse with `_LEDGER_UNREADABLE`.**
4. Does a repair keep the Task `run_id`? `UNVERIFIED`. **Bind to the current Developer record's `card_delivery`.**
5. Should a PM be able to waive a lost receipt? **No. Re-run the Developer. Any exception is an Owner decision.**
6. Epoch value. **The merge time of PR 3, as a fixed constant.**
