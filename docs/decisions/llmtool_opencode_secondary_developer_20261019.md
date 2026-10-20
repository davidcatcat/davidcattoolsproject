# llmtool — Developer allocation decisions D1–D8 (2026-10-19, D8 2026-10-20)

## Owner D8 (supersedes conflicting D4/D7 gates below) — OpenCode autonomy; no per-call Owner approval (2026-10-20)

**Owner decision: ACCEPTED** (「同意」, 2026-10-20, in the Claude session; recorded by Claude). The Owner had authorized OpenCode early. AI-written follow-up gates turned that into repeated per-call Owner questions and stalled work for hours. D8 removes only per-call Owner-approval requirements. It does not waive technical Host admission, registered D6 worktree ownership, task-scoped file limits, privacy and credential protections, independent review, or CI. It supersedes conflicting D4/D7 wording only where that wording requires a new Owner approval for each OpenCode task.

1. **Egress authorized.** Non-sensitive source, tests, docs and Git history of this repository **may be sent to OpenCode's free models** (DeepSeek, GLM) for Developer work, self-review and pre-review. Still forbidden: credentials, secrets/API keys, VPN/auth material, personal/customer data and restricted logs. The D4 privacy/egress gate is **satisfied by this decision**.
2. **The PM admits OpenCode work itself.** The Codex Integrator/PM issues each task's OpenCode admission **without asking the Owner**, provided that: the model is free (D5 stays); the provider-side output cap is set (#229); writes are scoped to the task's files in a registered D6 worktree; and the native retry stop is in place (at most one extra request, then the job is killed; Owner accepted this 2026-10-20). An updated Host/Worker written by OpenCode still needs a distinct reviewer before it is used, but **not** an Owner approval.
3. **Ask the Owner only for:** the wrong feature/requirement, root/sudo, or changes to Mac core system settings. Everything else (routine technical choices, retries of a failed task, ordering, which agent) is resolved by the AI team and reported afterwards.
4. **Roles today:** Codex Developer is **resumed** (Owner 「恢復 Codex Developer」, 2026-10-20). OpenCode stays a Developer. Claude remains the formal independent Reviewer and owns the monitor tool. While Codex was at its 5 h limit, Claude implemented #229/#230 at the Owner's request; those still need a distinct reviewer.

Unchanged: formal independent review before merge, exact-head CI, no paid fallback, no F2 llmtool-owned Git mutation, no force operations.

### Owner D8a — local OpenCode credential use (2026-10-20)

**Owner decision: ACCEPTED** (「同意補充 a」, 2026-10-20, recorded by Claude). llmtool's Host/Worker **may read the single active OpenCode credential** (integration `opencode`) from the local OpenCode store **in memory**, and pass it to the isolated OpenCode worker it starts (e.g. via that worker's private localhost credential endpoint). This is the path the TaskA native runner used and Claude reviewed and accepted. It satisfies the D4 credential boundary for the D4 one-call smoke and for OpenCode tasks.

Still required: never print, log, persist, export or copy the credential into receipts, evidence, GitHub or any file; read only that one active row (no `auth export`, no all-credentials list); drop the reference after handing it to the worker. Config isolation through the Worker's isolated HOME/XDG state is accepted. On this Mac, no system-level or managed (MDM/profile) OpenCode configuration exists (checked 2026-10-20); a launch preflight that fails closed if such sources appear is recommended, not a precondition.


> **Owner decision:** ACCEPTED, 2026-10-19. **Execution evidence:** NOT IMPLEMENTED / NOT LIVE VERIFIED. The Owner separately authorized starting Git/document updates; this document is not evidence of a physical Mac worktree reassignment, model/data egress authorization, executable Adapter or A11c milestone completion.
>
> Applies within **existing A11b/A11c, M1b/M2 and G1–G4 only**; no new A milestones, third linked worktree, permanent controller, PM, daemon or automatic Cloud integration.

> **Superseded worktree-scope note (Owner D6, later on 2026-10-19):** The D1 constraint "no third worktree" and its fixed-root ceiling describe the decision *at that time*, not current authority. [Owner D6 dynamic worktree policy](../../skills/safe-git/SKILL.md) now permits additional task-owned linked worktrees with automatic evidence-gated retirement. D1's requirement to verify safe transfer **before reusing the historical /claude root** still applies. GitHub policy is not proof of Mac activation; provider-privacy, single-writer and review constraints remain unchanged.

Authorities: [Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172), [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120), [ROADMAP](../../tools/llmtool/ROADMAP.md), [WORKFLOW](../../tools/llmtool/WORKFLOW.md), [safe-git](../../skills/safe-git/SKILL.md), [owner G1–G4](../governance/llmtool_owner_outcome_reporting_v1_1_20261019.md).

## Owner D7 override (2026-10-19): OpenCode PRIMARY, Codex Developer PAUSED

The requirements Owner instructed ChatGPT in the 2026-10-19 conversation
to switch primary development to OpenCode CLI because of Codex Developer
token consumption, review prior Codex source, then pause Codex Developer.
This is an attributed chat decision, **not a signed GitHub comment**.
[PR #202 source/offline handover](https://github.com/davidcatcat/davidcattoolsproject/pull/202#issuecomment-6258306864)
accepted the adapter as a foundation at the time; PRs #202 and #204
have since **merged** after source/offline and original Mac Claude
Security/Functional/native QA. A real host-owned OpenCode `run()`, S3
and same-active-Integrator receipt are **still NOT VERIFIED**.
Existing Codex Integrator/PM and separate Codex Cloud lane are not
blanket-paused by this Developer-only instruction.

OpenCode writes in its registered D6 `tasks/a11c-opencode-193-s3`
worktree, without taking over historical `/claude`. It develops/tests,
runs DeepSeek own-context review then genuinely qualified *free*
different-model pre-review, then independent Claude Code formal
security/functional/native review. Claude FAIL returns to OpenCode
for bounded fix/rerun/delta-review, not Codex Developer; PASS goes
to Codex Integrator for final integration/merge. Missing model,
privacy/auth, free-pricing or actual Mac runner means BLOCKED,
not an automatic paid API or Codex Developer fallback.
No new controller/milestone or simulated live claim. Stop after
the first accepted S3 and report.

**D4 bootstrapping clarification (independent Claude #203 review):**
Any different OpenCode read/write file set needs new exact-file D4
source/egress admission **before** the call. A Host/containment code
change written by OpenCode cannot authorize its own next invocation:
independent original Claude formal Security review must accept the
actual changed Host code before the Host may use it to admit a Worker.
The initial host-contained S3 task uses the already independently
reviewed PR #204 Host and exact admitted two-file source window,
without the Owner's manual TUI acting as the Host Worker.

## Accepted allocation

| Role | Current owner decision |
| --- | --- |
| Codex Integrator/PM | Existing sole semantic PM and Integration Manager: task splits/routes, priorities, interpretation, merge gates, evidence, next task |
| Codex Developer | **PAUSED** as of Owner D7 after frozen PR #202. No new source changes, tests, subagents or PRs unless Owner explicitly resumes |
| OpenCode v2 / DeepSeek | **PRIMARY Developer** under Owner D7; DeepSeek separate-context self-review, qualified free different-model cross-review, then Claude formal independent review |
| Claude Code | Dedicated **independent Reviewer**: code review, functional/security assessment, tests, verification; **Claude Developer PAUSED** pending Owner token availability decision |
| Company LLM (gw) | Continue previous low-cost, per-task authorized quality-checked advice/triage/context compression, not direct Shell/Git executor |
| AGY | Backup only due to low token quota |
| Codex Cloud and Claude Cloud VM | Independent development environments; no new Mac auto-integration plan. Existing optional Codex Cloud lane stays separate from M2 |

**Boundary:** llmtool executes the Integrator-authorized plan and owns bounded Worker/Host/process lifecycle, deterministic events, source-pinned review packets, evidence, budgets and handoff, **not PM semantic decisions**. OpenCode is a tool-capable CLI Worker, **not a text-only `Provider.complete()`**. Reuse existing process lifecycle/Task/Review/Policy/Git contracts; no duplicate orchestrator.

## HISTORICAL D1 — reassign the second fixed worktree: superseded as S2 prerequisite by D6/D7

**HISTORICAL D1 option, not the current critical path:** if the historical physical `/claude` root is ever reused, independently verify actual paused Claude Developer release first. Under D6/D7, **use the already-registered new OpenCode task worktree instead**; leave `/claude` unchanged, and do not clone or force-transfer old data. Preserve the canonical Integrator root, existing Codex fixed root and Git common directory unchanged.

**Not active by declaration alone.** Verify old Claude Developer/session/descendants/tests actually released; preserve branch, HEAD/index, tracked/untracked/ignored data, locks and user files. Independently review the policy/execution change, then prove native Mac C2/C3 new OpenCode Session identity, sole-writer ownership, dedicated venv/import provenance, task branch and no concurrent Claude Developer writer. Unknown state is STOP/preserve, not forced cleanup. Claude **Reviewer** may read only under separately valid review authority and may not interfere with the OpenCode writer/shared refs. Ordinary safe-git and per-action Git authority remain in force. Owner need not reapprove this selected option.

## D2 — foreground loop before reliable original-session wake: ACCEPTED (A)

**First useful slice:** existing active Codex Integrator → llmtool → scoped Codex/OpenCode Worker → Developer tests/self-review (OpenCode also GLM cross-model pre-review) → Claude independent formal review → bounded fix/retest or return to **same active foreground Integrator**, with **no Owner intermediate prompt relay**.

**Full G3 still requires** real original Codex Session addressed delivery, ACK, dedup/replay, source/identity verification, stop/recovery and authorized independent release evidence. Reuse existing `codex_pickup_native` and task/handoff/ledger rather than inventing a scheduler. A foreground PASS is useful progress, **not** `M2_LIVE_VERIFIED`.

## D3 — Claude quota depletion: ACCEPTED (A)

**HISTORICAL D3 role wording superseded by D7:** only OpenCode Developer may continue authorized source/test/pre-review work; Codex Developer is PAUSED. Place candidates requiring Claude formal review into **logical** `WAITING_CLAUDE_REVIEW`, retaining original Task Contract, candidate exact SHA, observed tests and review scope. This is not proof that a durable enum/store currently implements that state. Integrator bounds pending review inventory and prioritizes it; on quota recovery review the *current* exact candidate (revalidate stale HEAD). Never call self-review formal independent PASS, silently merge unreviewed changes, auto-pay for alternate models, auto-restart Claude Developer or allow unbounded backlog.

## D4 — read entire **non-sensitive** llmtool repository: ACCEPTED (B)

OpenCode Developer and allowed internal Reviewers may inspect/search **all non-sensitive project source, tests, documentation, skill instructions and relevant Git history**, even beyond the task's writable file allowlist. This **does not** expand write authority, project directory boundary, Git authorization, or any other Provider's egress scope.

Do not read/export private credentials, secrets, API keys, internal VPN/auth material, customer/personal data or restricted logs/diffs; check sensitive material wherever it appears, including Git history, shell/test stdout and subagent context. **Repo inspection ≠ permission to export every inspected byte to a free external model.** Verify exact selected provider privacy/retention terms and actual egress route authorization, redaction controls and negative tests before source is sent to external models. Verify installed OpenCode **v2** permissions for file read/search, shell, edit, subagent/task and especially external-directory effects: prompt-only secrecy controls and default vendor configuration are insufficient; scoped host isolation may be necessary. No arbitrary local Mac filesystem access.

For OpenCode Developer, DeepSeek self-review and GLM pre-review, any repository read/search result or command output entering agent context is **external provider egress**. Do not begin repository inspection through these agents until the D4 privacy/egress gate is recorded for the exact selected models and permitted data scope; before that, use only content explicitly cleared for that provider. [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) and [company delegation](../../skills/company-llm-delegation/SKILL.md) preserve their separate route-specific boundaries and do not grant OpenCode source export permission.

## D5 — free models only unless Owner separately authorizes spend: ACCEPTED (A)

Prefer role-qualified currently free OpenCode models. If DeepSeek/GLM `-free` IDs disappear, change price, quota or performance, qualify an available free substitute with capability/quality/privacy checks. **Never silently switch to paid API** or report historical US$0.00 as current price. If none qualifies, mark OpenCode BLOCKED and report to Integrator; **do not automatically reactivate Codex Developer**. Preserve Claude formal independent review and actual usage/cost or UNKNOWN.

## Implementation dependencies: existing A11 only

1. **Docs/policy and independent review:** record accepted D1–D5 and adjust safe-git's existing root-transfer rule plus roadmap/workflow/Agent pointers. GitHub PR is not a live Mac CLI notification.
2. **Mac D1 + D4 gates:** independently verify old Claude ownership release, existing-root turnover, effective OpenCode v2 permissions/externals, selected free-model vendor privacy/egress basis; no third checkout.
3. **A11c Worker integration:** reuse real Host/CLI lifecycle, budgets, source-based results and cancellation; run a bounded Mac OpenCode Worker in the activated root with DeepSeek self-review, fresh-context GLM pre-review, repair/test evidence. Historical OpenCode Lab 79-pass/unpublished work is **not** a production llmtool Worker gate.
4. **Claude formal review:** Host (not model claims) collects original contract, exact Diff/base/head SHA, test/CI evidence, produces trusted packet; Claude FAIL returns bounded concrete fixes to original Developer, PASS resumes active Integrator. Keep current no-infinite-retry and source freshness rules.
5. **G1–G4 unchanged:** real M1b source adoption, real authorized Worker, complete original Integrator wake/ACK/repair and independent Review E2E, then three real Mac-supervised packages with Owner GO/LIMITED GO/NO-GO. Do not claim these gates pass from a docs PR/fixture.

**Parallel, not prerequisites for useful initial slice:** existing company-gw route qualification, A11b F2 direct llmtool-repo mutation, AGY backup and Cloud. Worker-owned Git within separately authorized registered workspace is not blanket-gated on F2; any real **llmtool-owned** Git mutation remains F2-gated.

## 2026-10-19 Owner clarification — Codex token efficiency and this round's forced stop

**ACCEPTED operational clarification**, not new D1–D5 options: the Owner observed rapid Codex Integrator ↔ Codex Developer handoffs and high token consumption, and reiterated that the then-active Developers needed to **own internal self-review, ordinary bug repair and test verification** (current Owner D7 pauses Codex Developer) before handing off a complete meaningful candidate. Codex Developer **optionally** invokes an isolated-context Reviewer subagent when the expected risk reduction justifies actual Codex quota consumption and the tool/budget permits; OpenCode runs DeepSeek self-review and an authorized free cross-model Reviewer. These are pre-review; Claude Code remains formal independent Reviewer. Claude FAIL goes to the original Developer's bounded correction loop, not repetitive PM micro-decisions. Existing Codex Integrator remains sole PM and final Integrator, receiving scoped Task Contract outcomes rather than full repeated analysis. No need for Owner to relay ordinary handoffs.

The company `gw` was **never removed**: deterministic tests/collectors first, then the existing privacy-authorized, evidence-qualified company-gw route for suitable bounded classification, log/test triage and context summaries, with actual independent fact checks. Unqualified route results cannot promise Token saving or decide architecture, correctness, Git or formal QA. Assess **net** token/cost per actual work package including Integrator/Developer/subagents/Claude/retries, log missing usage as `UNKNOWN`, and avoid a new tracking system. Company VPN unavailable is a route status, not a stop on unrelated work.

**Mandatory this-round stop at S3:** S1 = PR #198 review/merge; S2 = registered D6 OpenCode task-root and real Worker launch/permission proof (historical second-root transfer not required); S3 = one real OpenCode llmtool code-change package with self/cross-model pre-review, repaired/tested source, **Claude independent PASS**, and trusted result handed back to the **same active foreground** Codex Integrator for integration **without Owner manual prompt relay**. After S3 is genuinely accepted, stop creating new tasks and issue an exact-evidence phase report. Do **not** continue automatically into inactive original Session wake or G4 Mac three-package trials; those remain subsequent roadmap work, and S3 alone **is not full G3/M2 LIVE_VERIFIED**. On unsatisfied mandatory gate, record `BLOCKED` with evidence and stop affected work; don't fabricate PASS or pursue unlimited retry. Details and authoritative repair/stop rules: [ai-team-delivery](../../skills/ai-team-delivery/SKILL.md#2026-10-19-owner-direction--developer-owned-pre-review-economical-handoffs-and-s3-stop).

## Acceptance vs reality

All **D1–D5 architecture decisions ACCEPTED**. Physical OpenCode ownership, effective privacy controls, actual free-model availability, runtime Adapter, trusted cross-CLI delivery, candidate queue durability, G1/G2/G3/G4 and any merge are **NOT VERIFIED**. Require source-pinned independent technical review and native Mac evidence where applicable; only material new risk/cost/product choices return to Owner. Routine authorized engineering work belongs to Integrator.
