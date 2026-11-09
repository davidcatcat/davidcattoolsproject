# A12 WP5 Minimal Environment Approval PoC Design (#1144)

## Requirement Source and Purpose
- **Requirement Source**: Owner decision 1 relayed verbatim on [#1144 comment 6744205705](https://github.com/davidcatcat/davidcattoolsproject/issues/1144#issuecomment-6744205705).
- **Foundation**: Builds upon [docs/design/a12-wp5-phase-b-workpackage-authorization-1144.md](file:///Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/wp5b-d2-envpoc-agy-1144-20261109/docs/design/a12-wp5-phase-b-workpackage-authorization-1144.md).
- **Scope**: Minimal Proof-of-Concept (PoC) design only. It enables no execution authority and establishes that GitHub Environment Approval is a workflow job gate, not a ready work-package authorization system.

---

## 1. Owner Requirements Restated
- **Identities**: Owner is `davidcatcat`; AI machine account is `ddavidccatssun` (via [`github_identity.py`](file:///Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/wp5b-d2-envpoc-agy-1144-20261109/tools/llmtool/llmtool/github_identity.py)).
- **Reviewer Restriction**: The required reviewer is the Owner account (`davidcatcat`) only.
- **Rule Enforcement**: Prevent self-review is enabled and admin bypass is disabled (no bypass allowed).
- **Authorization Binding**: Each authorization strictly binds repository, Issue, work scope, allowed operations, run cap, cost cap, and expiry.
- **Digest Binding**: Content is bound by an unchangeable SHA-256 digest displayed in the workflow run name that the Owner inspects on a phone before approving.
- **Core Verification**: llmtool Core verifies the approval status, approving account, workflow run, package version, and content via the GitHub REST API.
- **No Inferred Authority**: Conversational comments, PM notes, or confirmation text from AI or ChatGPT never constitute Owner authorization.
- **Fail-Closed**: Any missing, partial, or invalid authorization results in `WAITING_OWNER`.
- **Granularity**: Exactly one approval per bounded work package.
- **Trust Boundary**: Until issue #1234 isolation acceptance, this PoC serves strictly as test and accident protection, never verified human execution authority.

---

## 2. GitHub Platform Facts
- **References**:
  - Deployments and Environments: https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments
  - Manage Environments: https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments
  - Workflow Runs REST API: https://docs.github.com/en/rest/actions/workflow-runs
- **Reviewer Limits**: Up to six required reviewers can be configured per environment.
- **Plan Constraints**: On Free, Pro, and Team plans, required reviewers and the admin-bypass toggle function only for public repositories (this repository is public).
- **Prevent Self-Review**: When enabled, the user who triggers or starts the workflow run cannot approve the deployment.
- **Admin Bypass**: The setting "Allow administrators to bypass configured protection rules" defaults to ON and must be explicitly turned OFF.
- **Deployment Branches**: Environment deployment can be restricted to selected branches (specifically `main`).
- **Configuration Privilege**: Only a repository owner/admin can configure environment protection rules.
- **Auto-Creation Caveat**: Referencing a non-existent environment in a workflow automatically creates it with no protection rules; anyone with workflow edit rights can cause this.
- **API Approvals Resource**: `GET /repos/{owner}/{repo}/actions/runs/{run_id}/approvals` returns an array of approval objects containing `environments`, `state` (`approved`, `rejected`, `pending`), `user`, and `comment`.
- **Run Object Inputs**: The run object returned by `GET /repos/{owner}/{repo}/actions/runs/{run_id}` contains `display_title` (derived from `run-name`), but omits `workflow_dispatch` inputs.
- **API Approval Capability**: `POST /repos/{owner}/{repo}/actions/runs/{run_id}/pending_deployments` permits a required reviewer to approve or reject deployments via the API using their own credentials.

---

## 3. Minimal Owner Setup Steps
All setup steps are manual Owner actions performed in GitHub repository settings (AI never alters settings):
1. Navigate to **Settings** > **Environments** and create an environment named `owner-approval`.
2. Under **Required reviewers**, add `davidcatcat` only.
3. Check the box for **Prevent self-review**.
4. Uncheck the box for **Allow administrators to bypass configured protection rules**.
5. Under **Deployment branches**, select **Selected branches** and configure rule to allow `main` only.
6. Do not configure any environment secrets or environment variables.

> [!IMPORTANT]
> The `owner-approval` environment must exist with these exact protection rules in place BEFORE the PoC workflow is merged to `main` or executed. Otherwise, GitHub will auto-create the environment without protection rules.

---

## 4. PoC Workflow Outline
The PoC workflow will be implemented in a separate reviewed Task with CI.
- **Path**: `.github/workflows/wp5-env-approval-poc.yml`
- **Trigger**: `workflow_dispatch` only, accepting inputs:
  - `issue`: Target Issue number string.
  - `digest`: 64-character hexadecimal SHA-256 string.
- **Run Name**: `run-name: "WP5 Env Approval PoC - Issue ${{ inputs.issue }} - ${{ inputs.digest }}"`
- **Concurrency**: `concurrency: wp5-env-approval-${{ inputs.issue }}`
- **Permissions**: Empty mapping (`permissions: {}`).
- **Job Structure**:
  - Runs on `ubuntu-latest`.
  - Environment: `owner-approval`.
  - Isolation: No checkout (`actions/checkout` omitted), no third-party actions, no GitHub-owned actions, no secrets accessed.
  - Execution: Single shell step printing `inputs.issue` and `inputs.digest` to `$GITHUB_STEP_SUMMARY`.

---

## 5. Digest Binding & Phone Approval Flow
1. **Comment Posting**: The AI posts the work-package record (canonical JSON plus a human-readable summary for mobile viewing) as a comment on the target Issue.
2. **Digest Computation**: The SHA-256 hash of the canonical JSON bytes forms the 64-character hexadecimal digest.
3. **Dispatch**: The workflow is dispatched with the Issue number and digest, embedding both in the `run-name` (`display_title`).
4. **Mobile Approval**: The Owner reads the summary comment on a mobile phone, inspects the matching digest in GitHub Mobile's deployment approval prompt, and approves or rejects the run.
5. **Byte Persistence**: llmtool Core stores the exact record bytes at dispatch time. Any subsequent edit to the comment changes the computed hash, causing immediate refusal.

---

## 6. Core Verification Rules
llmtool Core polls the workflow run and validates all of the following conditions before proceeding. If any check fails, Core remains in `WAITING_OWNER`:
1. **Approval State**: `GET .../actions/runs/{run_id}/approvals` returns an entry with `state == "approved"`.
2. **Reviewer Identity**: The approving user `login` strictly matches `github_identity.owner_login()` (`davidcatcat`).
3. **Environment Match**: The approved environment name is exactly `owner-approval`.
4. **Workflow Identity**: The workflow path is `.github/workflows/wp5-env-approval-poc.yml` dispatched from `refs/heads/main`.
5. **Event Type**: The triggering event is `workflow_dispatch`.
6. **Title Binding**: The run's `display_title` contains the full 64-character hexadecimal digest.
7. **Digest Integrity**: Recomputing the SHA-256 digest of the locally stored record bytes yields the exact digest found in `display_title`.
8. **Comment Provenance**: The record comment author equals `github_identity.ai_login()` (`ddavidccatssun`) and its `updated_at` timestamp matches `created_at` (no post-run edits).
9. **Caps and Lifetime**: The record has not expired, has not been revoked, and does not exceed its run or cost caps.

---

## 7. Key Findings & Sequencing (#1234)
- **Self-Review Deadlock Finding**: Because the AI currently runs and dispatches workflows under the Owner's identity (`davidcatcat`), GitHub's "Prevent self-review" rule blocks the Owner from approving their own initiated runs.
- **Pre-requisite Ordering**:
  1. Complete #1234 to provide the AI with its isolated `ddavidccatssun` GitHub authentication (`gh` CLI credentials).
  2. The Owner manually configures the `owner-approval` environment with protection rules in repository settings.
  3. Implement, review, and merge the PoC workflow Task (`.github/workflows/wp5-env-approval-poc.yml`).
- **Co-located Token Finding**: Prior to #1234 acceptance, any local process sharing the Mac user account could theoretically invoke `POST .../pending_deployments` using the Owner's local token. Environment approval is thus an accident guard, not cryptographic proof of human approval, until account isolation is complete.

---

## 8. PoC Acceptance Tests
1. **Valid Approval**: Successful Owner approval on a phone via GitHub Mobile is fetched and verified by Core -> proceeds.
2. **Explicit Rejection**: Rejection on phone sets workflow to rejected -> Core remains in `WAITING_OWNER`.
3. **AI Self-Approval Blocked**: Attempted approval by `ddavidccatssun` is rejected by GitHub environment protection rules.
4. **Unapproved Run Refused**: A run without approval or in a different environment fails Core verification -> `WAITING_OWNER`.
5. **Digest Mismatch Refused**: A modified comment record, altered input, or digest mismatch fails Core verification -> `WAITING_OWNER`.
6. **Non-Main Ref Refused**: A workflow run dispatched from a feature branch instead of `main` fails Core verification -> `WAITING_OWNER`.

---

## 9. Limits and Open Items
- **Platform Capability**: Environment approvals gate Actions workflow jobs; they do not automatically execute local llmtool Tasks or replace PM dispatch contracts.
- **Mobile Dependency**: Push notifications for deployment reviews rely on the Owner having the GitHub Mobile app installed and notifications enabled.
- **Scope Boundary**: One approval strictly covers one bounded work package. Any scope extension requires a new work package and distinct approval.
- **No New Credentials**: The PoC introduces no webhooks, long-running background daemons, paid APIs, or additional secrets.
