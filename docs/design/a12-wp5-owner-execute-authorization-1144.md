# A12 WP5 Phase B: Owner EXECUTE authorization record (DRAFT)

> **DRAFT, NOT ENABLED. This file grants nothing.**
>
> It is a design only. It needs Owner approval and a formal security review before any code is written or enabled. Phase A stays the live rule until then. Nothing here changes what any agent may do today.
>
> Sources on #1144:
> - Owner decision: https://github.com/davidcatcat/davidcattoolsproject/issues/1144#issuecomment-6705231625
> - Research: https://github.com/davidcatcat/davidcattoolsproject/issues/1144#issuecomment-6705471777

## Goal

Phase B lets Core read one signed Owner record from a GitHub comment and use it as an EXECUTE authorization for one Task. It reuses the identities, the one-use receipt ledger and the dispatch gate that exist today. It is not a second authorization system.

## Limits

- A login, comment text or AI claim never creates authority. Words like "Owner approves" in a comment prove nothing.
- GitHub can show that a comment came through the OpenAI connector: `performed_via_github_app.slug` is `chatgpt-codex-connector` and the author is the Owner login. But the ChatGPT model writes the comment text. So this is a required filter, not proof that the Owner chose the words.
- All agents share the Owner's macOS user and can change llmtool, including this verifier and its pins ([#926](https://github.com/davidcatcat/davidcattoolsproject/issues/926) option B, [#1019](https://github.com/davidcatcat/davidcattoolsproject/issues/1019) S2). The checks below guard against accidents and outside forgery only. They are not a security boundary against a same-user agent. Never claim they are.
- The ledger and policy code already state the same limit: they do not protect against malicious code running as the same user ([authorization_ledger.py](../../tools/llmtool/llmtool/authorization_ledger.py), [authorization_policy.py](../../tools/llmtool/llmtool/authorization_policy.py)).

## Proof factor

- Only a signing key that the Owner holds can prove the Owner made a record.
- Research options:
  - **O2:** a FIDO2 SSH key that needs a touch and a PIN for each signature.
  - **O3:** a software key on a separate device that agents cannot reach.
- With O2 on the shared macOS user, the screen that shows the record runs under the same account as the agents. A same-user process could show one record and send other bytes to the key. O3 shows the bytes on the separate device. The security review must weigh this.
- The key fingerprint is pinned in `trust.json`. This needs a `trust.json` schema change later; it is not part of this file.
- Core also checks that the pinned key is in the Owner's public GitHub SSH signing keys. Both must match. A key on GitHub that is not pinned is refused. A pinned key that is gone from GitHub is refused.
- Core verifies with OpenSSH: `ssh-keygen -Y verify` with namespace `llmtool-b-auth-v1`. The allowed-signers entry is built only from the pinned, cross-checked public key.
- Without the Owner's key, Phase B cannot be proved and is not shipped.

## Record fields

The record is a closed JSON object. Unknown fields, missing fields and duplicate keys are refused. The schema step checks types, shape and lengths. Rules marked "(step N)" are checked later, in that step of the verification order.

| Field | Type | Rule |
|---|---|---|
| `schema_version` | integer | Exactly `1`. |
| `kind` | string | Exactly `EXECUTE_AUTHORIZATION`. (`REVOKE` and `STOP` are separate kinds, below.) |
| `auth_id` | string | 32 lowercase hex characters. Random. Used once (step 9). |
| `repository` | string | Repository slug `owner/name`. Must equal the `trust.json` binding slug (step 5). |
| `issue` | integer | Positive Issue number. Must equal the Issue that holds the comment (step 10). |
| `task_id` | string | 32 lowercase hex characters. Exactly one Task (step 11). |
| `operation` | string | From a closed list. v1 allows only `DEVELOPER_DISPATCH` (step 12). |
| `scope` | array of strings | 1 to 50 entries, sorted, unique. Each is a repo-relative path (step 13). |
| `budget` | object | Integers `max_developer_runs`, `max_review_runs`, `paid_spend`. Caps (step 14): Developer runs at most 3; review runs never above the Task's own limit; paid spend exactly `0`. |
| `version` | object | `task_revision` (integer) and `base_sha` (40 lowercase hex). Must equal the Task now (step 15). |
| `issued_at` | integer | UTC epoch seconds. |
| `expires_at` | integer | UTC epoch seconds. Greater than `issued_at` and at most 86400 seconds later (step 8). |
| `key_fingerprint` | string | OpenSSH SHA256 fingerprint of the signing key. Must equal the pin. |
| `signature` | string | OpenSSH SSHSIG signature over the canonical digest. |

### Canonical bytes

- Remove the `signature` field. Serialize the rest as UTF-8 JSON with sorted keys and no spaces (separators `,` and `:`).
- Take the SHA-256 digest of those bytes. The Owner signs that digest only after seeing the full record in plain text.
- The exact digest encoding given to `ssh-keygen` is fixed at implementation and checked in the security review.
- Maximum expiry: `expires_at - issued_at` is at most 86400 seconds. This value is an open question for the Owner.

### Transport

- The record travels in exactly one `llmtool-sync` fenced block.
- The block sits in one unedited comment on the bound Issue.
- Zero blocks or more than one block is refused.

## Verification order

The PM passes Core only a comment id. Core fetches the comment itself. The checks run in this order. The first failure wins. Each failure returns one fixed code. The result never echoes record fields, signatures or key material.

1. **Feature off.** Phase B is disabled → `B_FEATURE_OFF`.
2. **Envelope parse.** Exactly one `llmtool-sync` block; valid JSON with no duplicate keys; within the size limit → else `B_ENVELOPE_INVALID`.
3. **Provenance.** Author is the Owner login from `github_identity` → else `B_PROVENANCE_LOGIN`. The connector slug is `chatgpt-codex-connector` → else `B_PROVENANCE_APP`. The comment has no edit → else `B_PROVENANCE_EDITED`.
4. **Schema.** Fields, types and lengths match the table → else `B_SCHEMA_INVALID`. A `kind` of `DISCUSS`, `RECORD` or `EXECUTE_REQUEST` → `B_KIND_NOT_EXECUTE` (Phase A handles those as before).
5. **Repository.** `repository` equals the trust binding → else `B_REPOSITORY_MISMATCH`.
6. **Key pinned.** `key_fingerprint` equals the `trust.json` pin → else `B_KEY_NOT_PINNED`. The pinned key is in the Owner's GitHub signing keys → else `B_KEY_NOT_ON_GITHUB`. The key list cannot be read → `B_KEY_UNVERIFIED`.
7. **Signature.** `ssh-keygen -Y verify` passes over the canonical digest with namespace `llmtool-b-auth-v1` → else `B_SIGNATURE_INVALID`.
8. **Time window.** `now < issued_at` → `B_NOT_YET_VALID`. `now >= expires_at` → `B_EXPIRED`. Window over 86400 seconds → `B_EXPIRY_TOO_LONG`.
9. **auth_id unused and not revoked.** Already in the ledger → `B_AUTH_ID_USED`. Revoked → `B_REVOKED`.
10. **Issue.** `issue` equals the comment's Issue → else `B_ISSUE_MISMATCH`.
11. **Task.** The Task exists in this repository and is linked to that Issue → else `B_TASK_MISMATCH`.
12. **Operation.** In the closed list → else `B_OPERATION_NOT_ALLOWED`.
13. **Scope.** Every path is repo-relative, NFC, with no `..` and no leading `/` → else `B_SCOPE_INVALID`. Every path is inside the Task's own write paths → else `B_SCOPE_WIDER`.
14. **Budget.** Within the v1 caps and within the Task's own budget; paid spend is 0 → else `B_BUDGET_EXCEEDED`.
15. **Version.** `task_revision` and `base_sha` equal the Task now → else `B_VERSION_STALE`.

Then:

- Core issues one ledger receipt for this `auth_id` and moves it ISSUED → CONSUMED. A ledger failure (missing, insecure, locked store, clock rollback) → `B_LEDGER_UNAVAILABLE`, and nothing is dispatched.
- Then Core calls `worker_dispatch.evaluate` and every normal gate, unchanged. A refusal → `B_DISPATCH_REFUSED`. The receipt stays consumed; the record is not reused.

## Non-escalation rules

- `DISCUSS`, `RECORD` and `EXECUTE_REQUEST` never become EXECUTE. Only a signed record can.
- The PM passes only a comment id, never fields. Any other input to the entry point → `B_INPUT_INVALID`.
- The PM never holds the key. The PM never creates, edits or widens a record.
- Scope and budget must be within the Task's own write paths and budget, never wider. Any mismatch refuses. There is no nearest match.
- One record covers one Task in one repository.
- A valid record never satisfies `OWNER_ASSIGNMENT_ONLY` by itself and never overrides `PAUSED`. [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) stays the sole source of dispatch authority.

## Replay and recovery

- `auth_id` is consumed once in the existing authorization ledger.
- The same comment delivered again (for example after a restart) is refused with `B_AUTH_ID_USED`. A second comment with the same `auth_id` is refused the same way.
- A crash after CONSUMED leaves the row CONSUMED. `recover()` turns it into EFFECT_UNKNOWN. Core reports `B_EFFECT_UNKNOWN` and never replays it. The PM checks real state; any further dispatch needs a new signed record.
- Expired, revoked or duplicate records map to the Phase A class `NEVER_REDISPATCH`.
- **REVOKE.** A signed `REVOKE` record names one `auth_id`. It passes the same steps 1 to 8 for its own fields. It wins over any later use of that `auth_id`. The ledger's `redeem()` does not check revocation itself, so Core checks it at step 9 and again just before consume. A REVOKE after CONSUMED stops further runs counted under that record. It does not claim to undo effects that already happened.

## STOP

- A STOP is its own signed record kind. It passes steps 1 to 8, then its target check.
- A STOP names one `task_id`. The Task must exist in this repository and be linked to the same Issue. Otherwise → `B_STOP_TARGET_INVALID`.
- A STOP stops only that Task. It never stops other Tasks, PRs or processes.
- A **signed** STOP acts at once.
- A STOP with provenance only (Owner login and connector, but no valid signature) does not act in Core. For a STOP, a step 6 or step 7 failure returns `B_STOP_UNSIGNED` instead of the key or signature code. It goes to the PM as an urgent request. The PM may already stop its own Tasks.
- A STOP from an untrusted source fails provenance and is not forwarded as a STOP.
- Open Owner question: is this unsigned STOP policy right?

## Reuse, no second system

- [github_identity](../../tools/llmtool/llmtool/github_identity.py): the fixed Owner and AI logins.
- The Phase A `github_sync` contract parser: reads the `llmtool-sync` block.
- [authorization_ledger](../../tools/llmtool/llmtool/authorization_ledger.py) receipts: one-use consume, EFFECT_UNKNOWN recovery. Phase B adds a ledger layout in the same family as the provider and notification layouts, not a new store.
- A `REMOTE_OWNER` HostIssuer class in [authorization_policy](../../tools/llmtool/llmtool/authorization_policy.py) (design in [#97](https://github.com/davidcatcat/davidcattoolsproject/issues/97) and [#120](https://github.com/davidcatcat/davidcattoolsproject/issues/120); see also the [A11b F2 contract](a11b-f2-action-contract-97.md)). It does not exist yet.
- The `trust.json` pin for the key fingerprint.
- [worker_dispatch](../../tools/llmtool/llmtool/worker_dispatch.py) `evaluate`, unchanged.
- OpenSSH `ssh-keygen`, already on the host.
- Nothing edits [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json), [CLAUDE.md](../../CLAUDE.md), [AGENTS.md](../../AGENTS.md) or the rule files in [sensitive-paths.json](../../tools/llmtool/sensitive-paths.json).

## Owner decisions and open questions

- Key type: O2 or O3.
- Hardware purchase. This is spend.
- Registering the signing key. This is a credential.
- A separate macOS user ([#926](https://github.com/davidcatcat/davidcattoolsproject/issues/926) option A). This needs root, which AI never uses.
- Maximum expiry (draft: 86400 seconds).
- Allowed operations beyond `DEVELOPER_DISPATCH`.
- Unsigned STOP policy.
- The connector's own repository write permissions (contents, workflows). This is a security-review item.
- A formal security review before enabling.

## Negative test design

Each row is one future test. Each test asserts refusal with the listed code. "Valid" means a record that passes every other check.

| # | Case | Input | Expected code |
|---|---|---|---|
| 1 | Feature off | Valid signed record, feature disabled | `B_FEATURE_OFF` |
| 2 | AI login | Valid record posted by the AI login | `B_PROVENANCE_LOGIN` |
| 3 | Owner via gh, no app | Owner login, no `performed_via_github_app` | `B_PROVENANCE_APP` |
| 4 | Other app | Owner login, a different app slug | `B_PROVENANCE_APP` |
| 5 | Third login | Valid record posted by another login | `B_PROVENANCE_LOGIN` |
| 6 | Owner marker text | AI comment saying the Owner authorizes | `B_PROVENANCE_LOGIN` |
| 7 | Edited | Owner and connector comment, edited after posting | `B_PROVENANCE_EDITED` |
| 8 | No block | Comment with no `llmtool-sync` block | `B_ENVELOPE_INVALID` |
| 9 | Two blocks | Comment with two `llmtool-sync` blocks | `B_ENVELOPE_INVALID` |
| 10 | Duplicate key | JSON with a repeated key | `B_ENVELOPE_INVALID` |
| 11 | Unknown field | Extra field in the record | `B_SCHEMA_INVALID` |
| 12 | DISCUSS asks to execute | `kind` `DISCUSS` with execute wording | `B_KIND_NOT_EXECUTE` |
| 13 | RECORD asks to execute | `kind` `RECORD` with execute wording | `B_KIND_NOT_EXECUTE` |
| 14 | EXECUTE_REQUEST | `kind` `EXECUTE_REQUEST`, no signature | `B_KIND_NOT_EXECUTE` |
| 15 | Signature field missing | No `signature` field | `B_SCHEMA_INVALID` |
| 16 | Signature empty | `signature` is empty or not SSHSIG | `B_SIGNATURE_INVALID` |
| 17 | Wrong key | Signed by another key; fingerprint names the pin | `B_SIGNATURE_INVALID` |
| 18 | Tampered | One field changed after signing | `B_SIGNATURE_INVALID` |
| 19 | Wrong namespace | Signed with a namespace other than `llmtool-b-auth-v1` | `B_SIGNATURE_INVALID` |
| 20 | Key on GitHub, not pinned | Fingerprint of an unpinned GitHub key | `B_KEY_NOT_PINNED` |
| 21 | Pinned key gone from GitHub | Pin set; key removed from GitHub | `B_KEY_NOT_ON_GITHUB` |
| 22 | Key list unreadable | GitHub key list fetch fails | `B_KEY_UNVERIFIED` |
| 23 | Expired | `now >= expires_at` | `B_EXPIRED` |
| 24 | Not yet valid | `now < issued_at` | `B_NOT_YET_VALID` |
| 25 | Expiry too long | Window of 86401 seconds | `B_EXPIRY_TOO_LONG` |
| 26 | Replay by two comments | Second comment with the same `auth_id` | `B_AUTH_ID_USED` |
| 27 | Redelivery after restart | Same comment id processed again after restart; ledger shows one consume | `B_AUTH_ID_USED` |
| 28 | Crash after CONSUMED | Process killed after consume; restart runs `recover()` | `B_EFFECT_UNKNOWN` |
| 29 | Revoked | Signed REVOKE for `auth_id` exists | `B_REVOKED` |
| 30 | Other repository | `repository` is another slug | `B_REPOSITORY_MISMATCH` |
| 31 | Other Issue | `issue` differs from the comment's Issue | `B_ISSUE_MISMATCH` |
| 32 | Other Task | `task_id` not linked to this Issue | `B_TASK_MISMATCH` |
| 33 | Stale Task revision | `task_revision` older than the Task | `B_VERSION_STALE` |
| 34 | Stale base SHA | `base_sha` differs from the Task | `B_VERSION_STALE` |
| 35 | Merge operation | `operation` is a merge | `B_OPERATION_NOT_ALLOWED` |
| 36 | Policy change operation | `operation` changes policy | `B_OPERATION_NOT_ALLOWED` |
| 37 | Credential operation | `operation` touches credentials | `B_OPERATION_NOT_ALLOWED` |
| 38 | Absolute path | A `scope` path starts with `/` | `B_SCOPE_INVALID` |
| 39 | Dot-dot path | A `scope` path contains `..` | `B_SCOPE_INVALID` |
| 40 | Scope too large | 51 paths | `B_SCHEMA_INVALID` |
| 41 | Scope wider than Task | A path outside the Task's write paths | `B_SCOPE_WIDER` |
| 42 | Runs over cap | `max_developer_runs` is 4 | `B_BUDGET_EXCEEDED` |
| 43 | Budget over Task | Runs above the Task's own budget | `B_BUDGET_EXCEEDED` |
| 44 | Paid spend | `paid_spend` above 0 | `B_BUDGET_EXCEEDED` |
| 45 | PM-supplied widened field | PM passes a comment id plus a wider scope | `B_INPUT_INVALID` |
| 46 | STOP for another Task | Signed STOP for a Task not linked to the Issue | `B_STOP_TARGET_INVALID` |
| 47 | STOP for another repository | Signed STOP with another slug | `B_REPOSITORY_MISMATCH` |
| 48 | STOP from untrusted source | STOP posted by the AI login | `B_PROVENANCE_LOGIN` |
| 49 | Unsigned STOP | Owner and connector STOP, no valid signature; Core stops nothing | `B_STOP_UNSIGNED` |
| 50 | Dispatch PAUSED | Valid record; Worker is `PAUSED` | `B_DISPATCH_REFUSED` |
| 51 | Owner assignment missing | Valid record; Worker is `OWNER_ASSIGNMENT_ONLY` with no listed assignment | `B_DISPATCH_REFUSED` |
| 52 | Ledger unavailable | Ledger store missing or insecure | `B_LEDGER_UNAVAILABLE` |

## Tests

There is no test code in this PR. Tests go in `tools/llmtool/tests/test_github_sync_b_authorization.py` only after the Owner approves implementation. Each table row becomes one test that asserts refusal. Fake-only results never count as acceptance.
