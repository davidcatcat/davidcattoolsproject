# A12 WP5 Phase B: Work-Package Authorization Design (#1144)

## Requirement Source and Context
- **Requirement Source**: Owner decision relayed verbatim on [#1158 comment 6740532598](https://github.com/davidcatcat/davidcattoolsproject/pull/1158#issuecomment-6740532598).
- **Historical Context**: Closed PR #1158 (per-Task signature design) is historical reference only; the Owner dropped per-Task signatures as the primary design in favor of bounded work-package authorizations.
- **Status**: Design specification only. It enables no execution authority and does not claim Phase B is complete.

---

## 1. Architecture, Flow & Authorization Levels

### Product Goal and Execution Flow
The end-to-end loop connects ChatGPT directly to llmtool execution without repetitive human friction:
1. **Owner Command**: The Owner gives one explicit natural-language command in ChatGPT.
2. **Tool Invocation**: ChatGPT calls a connected, working GitHub tool integration.
3. **Core Verification**: llmtool Core polls or receives the envelope, verifying sender, schema, and authorization.
4. **PM Dispatch**: The existing llmtool PM workflow dispatches work to an authorized Developer.
5. **Team Convergence**: Developer implements and self-reviews -> independent Reviewer reviews -> exact-HEAD CI runs -> merge.
6. **Report Back**: The outcome is reported back to ChatGPT for the Owner to view.

Within an existing valid authorization, there are no repeated Owner prompts. No second Task store, secondary PM, external scheduler, or duplicate authorization engine is introduced.

### Four Authorization Levels
- **Level 0 (DISCUSS, READ, RECORD)**: Informational requests, reading repository status, or logging discussion. Never escalates to repository writes or code execution.
- **Level 1 (Within Existing Scope)**: An existing valid Owner authorization fully matches the repository, requirement/Issue, allowed operations, change scope, run and cost caps, Task version, and conditions. It proceeds straight into the PM flow. Repairs, redo PRs, and necessary test adjustments for the same requirement inherit this authorization, sharing the #1329 requirement-binding inheritance logic.
- **Level 2 (Scope Expansion)**: Any request exceeding existing scope, paths, caps, or operations requires a new trusted Owner authorization. This authorization binds to a bounded requirement or work package (never per PR or per retry), constrained by path scope, operation types, run caps, cost caps, and expiry. The PM never widens or synthesizes authorization.
- **Level 3 (Security & Core System Boundaries)**: Granting new credentials, broadening account rights, modifying security boundaries or authorization rules, or performing force operations on `main` strictly requires distinct, explicit Owner authorization. Standard cross-family Reviewer, exact-HEAD CI, and merge gates remain non-negotiable.

---

## 2. Owner Experience (Phone-First)

The Owner interface is lightweight, asynchronous, and fully functional on a mobile phone:
- **Steps**:
  1. State the objective or work package in ChatGPT.
  2. Inspect progress summaries and test/CI results on a phone.
  3. Confirm only when establishing a new work-package authorization (Level 2), approving high-risk changes (Level 3), or answering architectural escalations.
- **Anti-Friction Principles**:
  - The Owner is never required to touch cryptographic signing keys per Task.
  - The Owner never enters PINs or re-authorizations per repair iteration.
  - No re-authorization is demanded when a PR is redone or when `main` advances.
  - The Owner does not repeat authorized commands to the PM.

---

## 3. Distinct Identities, Platform Limits & Trust Boundaries

### Four Distinct Identities
To prevent confused-deputy and privilege-escalation defects, four identities are strictly separated:
1. **GitHub Account Identity**: Authenticated GitHub user (`user.login`, e.g., `davidcatcat` vs. machine `ddavidccatssun`).
2. **ChatGPT Tool-Call Source**: Inbound API client / connector calling GitHub tools.
3. **ChatGPT User Confirmation**: Interactive UI prompt shown to the user inside ChatGPT.
4. **llmtool-Verifiable Owner Authorization**: Cryptographically or platform-attested record verified by llmtool Core.

AI conversational text, GitHub comment text, matching the Owner's username, or an agent's self-assertion never create execution authority. Without verifiable authorization, Phase A `WAITING_OWNER` remains in force and Phase B is not satisfied.

### Research Fact A: ChatGPT Confirmations
- *References*: [OpenAI Developer Mode Guide](https://developers.openai.com/api/docs/guides/developer-mode), [ChatGPT Connectors Documentation](https://help.openai.com/en/articles/11487775-connectors-in-chatgpt).
- *Observed Behavior*: ChatGPT prompts users to confirm write actions by default, supports saving authorization per conversation, and provides a "Never ask" setting.
- *Platform Limit*: The cited docs show no signed confirmation evidence reaching the tool server, so a ChatGPT confirmation is not verifiable by llmtool.

### Research Fact B: GitHub Environment Reviewers
- *References*: [GitHub REST API Workflow Runs](https://docs.github.com/en/rest/actions/workflow-runs), [GitHub Mobile Deployment Reviews](https://github.blog/changelog/2021-04-01-reviewing-deployments-on-github-mobile).
- *Observed Behavior*: GitHub Environments with required reviewers block workflow execution until an authorized user approves via web or GitHub Mobile. `GET /repos/{owner}/{repo}/actions/runs/{run_id}/approvals` exposes approval status, user login, environment name, and review comments.
- *Platform Limit*: An environment approval can also be made through the REST API with the reviewer's token; under #926 decision B it is an accident guard, not proof that a human approved.

### Trust-Boundary Analysis (#926)
As established in issue #926 (closed under Owner decision B, issuecomment-6697226334), local agents and llmtool processes execute under the same macOS user account (`uid`) and have local filesystem access to the Owner's GitHub tokens and SSH credentials. Consequently, a GitHub approval submitted by the Owner account serves as an accident and operational guard, not a cryptographic boundary against local processes. Strong identity separation requires completing the Owner/AI account split (#1234) before GitHub approvals can be treated as verifiable human attestations.

---

## 4. Shared Primitives & Minimal Work-Package Record

### Shared Primitives (No Re-Implementation)
Phase B directly reuses verified components from Phase A and #1329:
- **Requirement-Bound Inheritance (#1329)**: Owner authorization binds to the requirement and scope, not an ephemeral PR number. Follow-up fixes, repair tasks, and re-opened PRs inherit valid scope.
- **Decision Handling**: Reuses `WAITING_OWNER`, `DISPATCH_NORMAL_FLOW`, `REFUSED_OUT_OF_SCOPE`, and `NEVER_REDISPATCH` from `github_sync_contract.py`.
- **Deduplication & Polling**: Uses the cursor and delivery ledger logic in `github_sync_poll.py` (`_ENTRY_KEYS`, `request_key`, `delivery_id`).

### Minimal Work-Package Authorization Record
A work-package authorization record contains:
- `auth_id`: Unique identifier string.
- `owner_ref`: Canonical URL reference to the Owner decision (matching `worker_dispatch.owner_comment_ref()`).
- `requirement`: Issue or milestone identifier (`issue_number`).
- `allowed_operations`: Frozenset of operation types (subset of `github_sync_contract.OPERATIONS`: `DEVELOP`, `DOCS`, `TEST_ONLY`).
- `scope`: Tuple of authorized path prefixes validated via `_valid_scope_path()`.
- `run_cap`: Maximum permitted task executions.
- `cost_cap`: Budget limit for tokens or invocations.
- `expires_at`: UTC expiry timestamp formatted per `_TIMESTAMP_FORMAT`.
- `revoked`: Boolean revocation flag.

This record grants authority only for its scoped requirement; it is never a blanket execution permission.

### Concrete Component Integration
The record interfaces directly with existing codebase functions and fields:
- `github_identity.py`: Resolves `owner_login()`, `ai_login()`, and config isolation via `ai_gh_config_dir()`.
- `github_sync_contract.py`: Parses envelopes via `parse_comment()`; validates `SyncMessage` (`request_id`, `revision`, `scope`, `operation`); computes `AuthorityDecision` using `_Authorization` fields.
- `authorization_policy.py`: Maps work packages into `PolicyContract` (`policy_id`, `task`, `kinds`, `scope`); defines `ReceiptBinding`; checks `Requirement` classifications (`NEW_COST`, `NEW_DATA_EGRESS`, `OWNER_P0_*`).
- `authorization_ledger.py`: Enforces at-most-once execution using `ReceiptBinding` (imported from `authorization_policy.py`) and atomic SQLite state transitions (`BEGIN IMMEDIATE`, `ISSUED` -> `CONSUMED` -> `EFFECT_DONE`).
- `worker_dispatch.py`: Rereads policies via `_read_policy_file()`; checks dispatch switches (`evaluate()`); verifies `owner_comment_ref()`; logs via `audit_entry(decision, action, *, binding=None, authorization_id=None)`.
- `task_store.py`: Persists task state and execution audits via `TaskStore` (`TaskBinding` imported from `action_contract.py`, `dispatch_audit`, `completion`, `pm_assignment`).

---

## 5. Verification, Test Plan & Deliverables

### The 11 Owner Test Cases
1. **Existing Valid Authorization**: Executes without prompting or friction.
2. **Multi-Task Continuation**: Multiple tasks under the same requirement run to completion under the initial authorization.
3. **PR Redo Inheritance**: Re-creating or rebasing a PR retains requirement authorization without re-authorization.
4. **Scope Expansion Refusal**: A task requesting paths outside authorized prefixes is refused (`REFUSED_OUT_OF_SCOPE`) and pauses in `WAITING_OWNER`.
5. **Expiry and Revocation**: Expired (`REASON_EXPIRED`) or revoked (`REASON_REVOKED`) records fail closed to `NEVER_REDISPATCH`.
6. **Forged Comment Blocked**: Unauthorized comments or non-whitelisted usernames trigger `SYNC_ISSUER_UNTRUSTED`.
7. **AI Impersonation Prevented**: The AI machine account (`ddavidccatssun`) cannot grant or sign Owner authorizations.
8. **Duplicate Deduplication**: Re-sent sync envelopes are flagged `LEDGER_DUPLICATE` and never re-dispatched.
9. **Core Recovery**: Crashes or interrupts recover cleanly using SQLite ledger recovery without repeating consumed effects.
10. **Missing Authorization**: Requests lacking a valid work-package authorization remain in `WAITING_OWNER`.
11. **Scoped STOP Request**: A `STOP_REQUEST` halts only its specified `task_id`; unrelated tasks and general authorizations remain unaffected.

### End-to-End Validation Requirement
Mock and unit tests are insufficient to complete WP5. Phase B requires a live end-to-end acceptance run:
`ChatGPT -> GitHub Tool -> GitHub Comment / Event -> llmtool Core Poll -> PM Dispatch -> Execution -> Status Report`.

### Minimal Implementation Work Packages
1. **WP5-B1**: Schema and validator for `WorkPackageAuthorization` record, integrated with `github_sync_contract.py`.
2. **WP5-B2**: Authorization store and inheritance binder, integrating with `authorization_ledger.py` and `task_store.py`.
3. **WP5-B3**: End-to-end integration test harness linking GitHub poll intake to PM task creation.

---

## 6. Open Questions & Security Boundaries

### Open Decisions for the Owner
1. **Approval Ingestion Source**: Choose between GitHub environment approvals (Fact B) or structured signed GitHub issue comments.
2. **Account Split Timing**: Schedule the implementation of #1234 (Owner/AI account isolation) to establish genuine multi-user trust boundaries.
3. **Default Guardrail Values**: Confirm standard defaults for work-package caps (e.g., maximum 5 runs, 7-day TTL).

### Inviolable Security Boundaries
- **No Credential Exposure**: Developer and PM agents never receive or read the Owner's GitHub personal access tokens or private SSH keys.
- **Zero New Attack Surface**: No continuous background daemons, public listening webhooks, paid ChatGPT API dependencies, or duplicate CI workflows.
- **Fail-Closed STOP**: `STOP_REQUEST` messages must strictly validate issuer and target `task_id` before modifying any execution state.
