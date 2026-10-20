# Archived llmtool role and policy text from main d41600f (through 2026-10-20)

> **HISTORICAL — NOT CURRENT AUTHORITY.** This file preserves dated role decisions, pilot conditions and one-round stop text removed from always-loaded instructions during the D8 cleanup. Current authority is in [AGENTS.md](../../AGENTS.md), [ai-team-delivery](../../skills/ai-team-delivery/SKILL.md), and [safe-git](../../skills/safe-git/SKILL.md). This archive is not an execution gate.

Source snapshot: canonical main at `d41600ff98568770fb918fdf4a3309dd144a0dfe`, immediately after PR #233. Git history retains every source file at that revision.

## D1–D7 allocation decision record at the source snapshot

# llmtool — Developer allocation decisions D1–D8 (2026-10-19, D8 2026-10-20)

## Owner D8 (supersedes conflicting D4/D7 gates below) — OpenCode autonomy; no per-call Owner approval (2026-10-20)

**Owner decision: ACCEPTED** (「同意」, 2026-10-20, in the Claude session; recorded by Claude). The Owner had authorized OpenCode early. AI-written follow-up gates turned that into repeated per-call Owner questions and stalled work for hours. D8 removes only per-call Owner-approval requirements. It does not waive technical Host admission, registered D6 worktree ownership, task-scoped file limits, privacy and credential protections, independent review, or CI. It supersedes conflicting D4/D7 wording only where that wording requires a new Owner approval for each OpenCode task.

1. **Egress authorized.** Non-sensitive source, tests, docs and Git history of this repository **may be sent to OpenCode's free models** (DeepSeek, GLM) for Developer work, self-review and pre-review. Still forbidden: credentials, secrets/API keys, VPN/auth material, personal/customer data and restricted logs. The D4 privacy/egress gate is **satisfied by this decision**.
2. **The PM admits OpenCode work itself.** The Codex Integrator/PM issues each task's OpenCode admission **without asking the Owner**, provided that: the model is free (D5 stays); the provider-side output cap is set (#229); writes are scoped to the task's files in a registered D6 worktree; and the native retry stop is in place (at most one extra request, then the job is killed; Owner accepted this 2026-10-20). An updated Host/Worker written by OpenCode still needs a distinct reviewer before it is used, but **not** an Owner approval.
3. **Ask the Owner only for:** the wrong feature/requirement, root/sudo, or changes to Mac core system settings. Everything else (routine technical choices, retries of a failed task, ordering, which agent) is resolved by the AI team and reported afterwards.
4. **Roles today:** Codex Developer is **resumed** (Owner 「恢復 Codex Developer」, 2026-10-20). OpenCode stays a Developer. Claude remains the formal independent Reviewer and owns the monitor tool. While Codex was at its 5 h limit, Claude implemented #229/#230 at the Owner's request; those still need a distinct reviewer.

Unchanged: formal independent review before merge, exact-head CI, no paid fallback, no F2 llmtool-owned Git mutation, no force operations.

### Owner D8a — local OpenCode credential use (2026-10-20)

**Owner decision: ACCEPTED** (「同意補充 a」, 2026-10-20, recorded by Claude). llmtool's Host/Worker **may read the single active OpenCode credential** (integration `opencode`) from the local OpenCode store **in memory**, and pass it to the isolated OpenCode worker it starts (e.g. via that worker's private localhost credential endpoint). The Owner identifies this as the TaskA native-runner credential path. The cited [Claude review](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6280821579) accepted only a runner helper delta; it records zero credential reads, did not execute the runner, and did not rereview unchanged product source or tests. It is not evidence of a live credential handoff or inference.

Still required: never print, log, persist, export or copy the credential into receipts, evidence, GitHub or any file; read only that one active row (no `auth export`, no all-credentials list); drop the reference after handing it to the worker. Config isolation through the Worker's isolated HOME/XDG state is accepted. The Owner reported that this Mac had no system-level or managed (MDM/profile) OpenCode configuration as of 2026-10-20; a launch preflight that fails closed if such sources appear is recommended, not a precondition.


> **Owner decision:** ACCEPTED, 2026-10-19. **Execution evidence:** NOT IMPLEMENTED / NOT LIVE VERIFIED. The Owner separately authorized starting Git/document updates; this document is not evidence of a physical Mac worktree reassignment, model/data egress authorization, executable Adapter or A11c milestone completion.
>
> Applies within **existing A11b/A11c, M1b/M2 and G1–G4 only**; no new A milestones, third linked worktree, permanent controller, PM, daemon or automatic Cloud integration.

> **Superseded worktree-scope note (Owner D6, later on 2026-10-19):** The D1 constraint "no third worktree" and its fixed-root ceiling describe the decision *at that time*, not current authority. [Owner D6 dynamic worktree policy](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/safe-git/SKILL.md) now permits additional task-owned linked worktrees with automatic evidence-gated retirement. D1's requirement to verify safe transfer **before reusing the historical /claude root** still applies. GitHub policy is not proof of Mac activation; provider-privacy, single-writer and review constraints remain unchanged.

Authorities: [Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172), [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120), [ROADMAP](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/tools/llmtool/ROADMAP.md), [WORKFLOW](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/tools/llmtool/WORKFLOW.md), [safe-git](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/safe-git/SKILL.md), [owner G1–G4](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md).

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

For OpenCode Developer, DeepSeek self-review and GLM pre-review, any repository read/search result or command output entering agent context is **external provider egress**. Do not begin repository inspection through these agents until the D4 privacy/egress gate is recorded for the exact selected models and permitted data scope; before that, use only content explicitly cleared for that provider. [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) and [company delegation](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/company-llm-delegation/SKILL.md) preserve their separate route-specific boundaries and do not grant OpenCode source export permission.

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

**Mandatory this-round stop at S3:** S1 = PR #198 review/merge; S2 = registered D6 OpenCode task-root and real Worker launch/permission proof (historical second-root transfer not required); S3 = one real OpenCode llmtool code-change package with self/cross-model pre-review, repaired/tested source, **Claude independent PASS**, and trusted result handed back to the **same active foreground** Codex Integrator for integration **without Owner manual prompt relay**. After S3 is genuinely accepted, stop creating new tasks and issue an exact-evidence phase report. Do **not** continue automatically into inactive original Session wake or G4 Mac three-package trials; those remain subsequent roadmap work, and S3 alone **is not full G3/M2 LIVE_VERIFIED**. On unsatisfied mandatory gate, record `BLOCKED` with evidence and stop affected work; don't fabricate PASS or pursue unlimited retry. Details and authoritative repair/stop rules: [ai-team-delivery](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md#2026-10-19-owner-direction--developer-owned-pre-review-economical-handoffs-and-s3-stop).

## Acceptance vs reality

All **D1–D5 architecture decisions ACCEPTED**. Physical OpenCode ownership, effective privacy controls, actual free-model availability, runtime Adapter, trusted cross-CLI delivery, candidate queue durability, G1/G2/G3/G4 and any merge are **NOT VERIFIED**. Require source-pinned independent technical review and native Mac evidence where applicable; only material new risk/cost/product choices return to Owner. Routine authorized engineering work belongs to Integrator.

## Prior AGENTS D7 role and stop text

## Owner D7 — OpenCode PRIMARY / Codex Developer PAUSED (2026-10-19)

**New operative role order supersedes all older Codex-first text.**
OpenCode CLI/DeepSeek becomes the default local Developer. The previous
Codex Developer completed [PR #202](https://github.com/davidcatcat/davidcattoolsproject/pull/202);
its source is frozen and it must not write, retest, start Reviewer
subagents or new PRs without a further Owner decision.
The Codex Integrator remains PM/launch/receipt/merge owner when available,
not a routine code writer. Claude Code stays formal independent Reviewer.

Review chain: OpenCode tests/fixes → separate-context DeepSeek self-review →
fresh-context **authorized free different-model** review (GLM only if
actually free; otherwise qualified free alternative or BLOCKED) →
independent Claude Security/Functional/QA; FAIL returns to **OpenCode**
for bounded repair/retest/affected rereview. Only formal PASS plus
required CI/native evidence goes to Codex Integrator for merge.

Reuse merged PR #202's accepted adapter and merged PR #204's formally reviewed Host/F2 source; the registered D6
`tasks/a11c-opencode-193-s3` root holds an Owner-launched manual session,
which is **not** accepted as a Host-owned S3 invocation. No new controller, automatic paid
fallback, F2/egress waiver or historical `/claude` root takeover.
GitHub dispatch is not a Mac CLI launch; stop and report after actual S3 PASS.
**OpenCode must not self-authorize its Host.** Follow the normative
[exact-source D4 and independent Host review gate](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md#owner-d7-2026-10-19-historical-see-d8d8a--opencode-primary-developer)
before any new provider read or use of updated admission/containment code.


**Always read [ai-team-delivery](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md) at the
start of product development, review, QA handoff or Git publication.**
The user is the requirements owner/product acceptor, **not** the day-to-day
PM, source-code reviewer, prompt courier or Mac test diagnostician. ChatGPT,
when available as the authorized coordinator, owns technical planning,
Codex/Claude delegation, evidence reconciliation, routine Low-risk
dispositions and eligible PR merges under delegated scope. **Owner D7 supersedes this: OpenCode CLI/DeepSeek is PRIMARY Developer; Codex Developer is PAUSED after frozen PR #202.** The existing D6 task worktree makes the historical `/claude` transfer unnecessary for new tasks. Claude (or a separately evidenced reviewer)
performs the independent review. The same agent's own checks do not become
independent QA or a security-review gate.

Do not ask the user to manage AI-to-AI prompts, repeatedly say "next step",
or perform routine Git/PM decisions. The user can act as the **physical Mac
operator** when the coordinator lacks that access; supply only a validated,
bounded instruction and diagnose its output yourself. Never claim that Mac,
provider or remote steps ran unless their evidence is present. The
owner confirms that `.git/ai-loop/run.sh` is the original tracked
Git-version shortcut, not a custom launcher. On the control branch
`scripts/ai-loop.sh` fetches the current Git revision and runs
`scripts/ai_loop.py` from a revision-scoped cache. AI, not the
owner, owns independently reviewed staged controller releases,
exact-candidate helper CI and applicable Mac acceptance **before**
moving the executable control tip. Ordinary technical revisions
do not require owner-by-owner confirmation. This Git source inspection
does not establish actual local Mac invocation/checkout evidence.
 Respect the frozen
`--qa-low*` STOP and separate provider-egress permissions. See the
canonical [ai-team-delivery Skill](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md).

Escalate material product/risk/authority changes, not every nonblocking
technical Low. Distinguish who proposed a technical judgment, who accepted
the product direction, who authorized Git work and who actually executed
it. Apply [safe-git](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/safe-git/SKILL.md) and the relevant
independent evidence gates before merging.

**Owner reporting (approved 2026-10-19):** follow [Owner outcome reporting v1.1](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) for evidence-backed M1b/M2/Mac-pilot milestone reports, one weekly compact pulse and material Owner decisions. Integrator handles routine work and independent gates; do not confuse a GitHub report with a notification delivered to ChatGPT or the Owner. The policy creates no new controller, authority or release milestone.

**Owner D1–D5 (2026-10-19):** Read [OpenCode developer allocation](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/docs/decisions/llmtool_opencode_secondary_developer_20261019.md) and [safe-git](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/safe-git/SKILL.md). **D7 overrides this historical role order: OpenCode PRIMARY, Codex Developer PAUSED.** D6 task root is independent of the historical `/claude` transfer; Owner D6 allows additional registered task-scoped worktrees without a two-root ceiling. Claude Developer PAUSED, Claude Code remains formal Reviewer, company-gw unchanged, AGY backup, Cloud integration undecided. Non-sensitive whole-repo **reading** is not secret/egress/writing permission; never silently use paid fallback. Foreground active-Integrator handoff comes before separately verified original-session wake/G3.

**Owner token-efficiency + S3 stop (2026-10-19):** Before delegating llmtool work read [Developer-owned pre-review and S3 STOP](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md#2026-10-19-owner-direction--developer-owned-pre-review-economical-handoffs-and-s3-stop). **OpenCode Developer** must implement, test, self-review and fix ordinary findings before formal independent Claude Code review; **Codex Developer is PAUSED** and must not start a Reviewer subagent or new work. Prefer deterministic tests, qualified free-model pre-review and formal Claude QA. **OpenCode Developer must** perform its authorized qualified free cross-model pre-review (or record BLOCKED if no permitted qualified model is available). Codex Integrator/PM assigns complete work packages, avoids micro-handoff/token churn and handles final gates/integration, not routine duplicate code review. Company-gw participates only for route-qualified, privacy-authorized, actually useful low-cost advisory work; measure whole-work-package net token effects or `UNKNOWN`. **Current execution round forcibly STOPS on real OpenCode foreground S3 acceptance and evidence report**; do not automatically begin inactive-session wake or G4. This is not a new milestone, permission or waiver of independent QA.

**llmtool product direction:** Read
[the single A11 roadmap](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/tools/llmtool/ROADMAP.md) and the canonical
[ai-team-delivery Skill](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md).
C0–C3 are legacy A-delivery labels; do not create another C
milestone/implementation stream. Continue coordinating Codex/Claude via
the AI-reviewed, automatically updated **temporary ai-loop** until A11c
real-world acceptance proves native llmtool parity. A11b.2.2.2 private
**disposable-fixture-only F1 Git ADD is DONE/merged via PR #84** with
independent Security/Functional QA. F2 user-repository Git mutation is
**not authorized** until a separately reviewed approval/execution contract.
Apply the canonical user-impact-first defect triage in the Skill;
keep C1 excerpt export safety
(Issue #66) distinct from offline local executor design.

## Prior ai-team-delivery D7 role and admission text

## Owner D7 (2026-10-19) HISTORICAL; see D8/D8a — OpenCode PRIMARY Developer

**Codex Developer is PAUSED after PR #202 review**, with no new
source edits, repair attempts, tests or Codex Reviewer subagents.
OpenCode v2/DeepSeek owns current A11c/S3 implementation work; merged
PR #202's Worker and merged PR #204's Host/F2 code have independent
source/native acceptance. The original D6 root also hosted the Owner's
manual TUI, so its output **cannot** be called a host-contained S3 run.
Existing Codex Integrator continues semantic PM, real dispatch/receipt,
integration and release authority if actually available, not routine
source bug fixing. Do not confuse issue comments with Session wake.

**Mandatory review path:** OpenCode code + actual tests/ordinary repairs →
distinct-context DeepSeek self-review → fresh-context permitted and
actually free different-model review → original independent Claude Code
Security/Functional/QA → on FAIL, back to **OpenCode** for bounded
fix/retest and affected formal rereview → formal PASS with native/CI
gates to Codex Integrator for final merge. Same-model or free cross-model
checks never substitute for Claude formal verdict. Missing real free
model, credentials, model pricing or provider egress grant is BLOCKED:
do not silently pay or re-enable Codex Developer. Keep existing S3
STOP-after-first-verified-cycle instruction.

**D4 exact-file bootstrapping and no self-attestation:** Before **every**
OpenCode invocation, a changed read/write set versus the last accepted
source admission requires its **own new exact-file D4 admission** before
that invocation, including source screening and provider privacy/price
checks. Any OpenCode-authored Host/containment change (permissions,
negative probes, source/diff guard or free-price check) **must pass
independent formal Claude Security review before being used to admit any
OpenCode invocation**. The first host-contained S3 invocation must use
the already reviewed/merged PR #204 Host (with G2 search/network probes)
for the explicitly admitted two-file task. A manual Owner TUI and/or
OpenCode's own review is not evidence of Host containment; use a clean,
sole-owned task runtime without a concurrent interactive writer, and
record actual Host `run()`, model usage, process cleanup and same-active
Integrator receipt. Never mark S3 PASS from code merge alone.


This is the shared collaboration contract for this repository. Read it together
with `AGENTS.md`, `CLAUDE.md`, the task's pinned contract, and specialist skills.
It governs **people, decisions and handoffs**, not new llmtool runtime capabilities,
implicit shell permissions, or an always-running service.

## Prior ai-team-delivery token/quota and S3 stop section

### 2026-10-19 Owner direction — Developer-owned pre-review, economical handoffs and S3 STOP

This is the **normative operational rule** for the existing A11c workflow, not a new milestone, permission grant, model router or controller. It supplements [D1–D5](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/docs/decisions/llmtool_opencode_secondary_developer_20261019.md), existing bounded convergence and [company LLM delegation](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/company-llm-delegation/SKILL.md).

**One meaningful work package, not repeated micro-relays:**
1. The **existing Codex Integrator/PM** defines one bounded, independently valuable Task Contract: base SHA/allowed writable files, intended behavior, acceptance/CI/native tests, spend/egress limits, existing Worker root/identity and stop triggers. PM assigns OpenCode Developer (primary); Codex Developer is paused pending a new Owner decision, and does **not** reopen planning after each ordinary code/test step. The Integrator retains routing and final integration/acceptance authority, but is **not** the routine bug fixer or duplicate line-by-line reviewer.
2. **OpenCode Developer owns local convergence before formal review**: implement the entire authorized packet, run relevant deterministic tests, conduct distinct-context DeepSeek self-review and a **qualified actually-free different-model fresh-context** review (GLM only if free and authorized; otherwise another qualified free model or BLOCKED), fix ordinary findings, and rerun affected tests. **Codex Developer is PAUSED**, including its Reviewer subagents. Same-model and cross-model internal pre-reviews are **not** distinct non-author formal Claude Security/Functional/QA acceptance.
3. Gather **one candidate packet per meaningful reviewed revision**: original contract, base/head SHA, complete relevant diff, targeted/full test evidence, relevant failure/fix history, own pre-review and unresolved findings, actual/UNKNOWN usage, and any company-gw evidence with raw source links. Trusted Host/Integrator verifies SHA and raw data; model prose is not a signing authority.
4. Submit the completed candidate **once** to formal independent **Claude Code Reviewer** (per actual applicable gate); Claude reviews source, behavior, tests and safety, not merely self-review claims. On FAIL, **return specific findings to the original Developer** for bounded fixes and affected rerun/re-review, instead of PM repeatedly re-reading or reclassifying each micro-fix. If a materially new SHA invalidates a formal verdict, only the affected formal review must be refreshed. Preserve root-cause attempt limits and mandatory CI/native gates.
5. The **Integrator resumes for genuine cross-task conflicts, substantive scope/authority/budget change, exhausted repair policy, formal Reviewer PASS and final integration**. Don't route normal compiler errors, ordinary tests, self-review findings or minor fix attempts through Integrator. Where the actual transport cannot yet deliver directly, report that limitation honestly; GitHub issue comments are **not** live CLI dispatch.

**Company LLM must participate where genuinely qualified and worthwhile, not as a mandatory extra reviewer:** deterministic collectors first; use existing privacy-approved, route-qualified `company-gw` for bounded test/log classification, failure grouping, and source-grounded summaries/context trimming **only when reliability and net savings are supported for that specific route**. The historical L01 `test-triage` experiment does **not** validate the ordinary `ask --skill test-triage` route or generic `context-pack` as token-saving. Do not send secrets/unauthorized bytes, bypass the company VPN/egress gates, let `gw` execute Git/Shell, trust a summary over original evidence, or claim blanket `company-first`. When VPN/route/cost data are unavailable, mark `UNAVAILABLE`/`UNKNOWN` and continue unrelated authorized work; request the Owner's VPN action **only if genuinely necessary** for a blocked approved outcome.

**Measure, don't invent savings:** at complete work-package level record when observable Integrator/Developer/reviewer usage (or `UNKNOWN`), company route actually invoked and classified work, OpenCode model/free status if verified, *number of actual Agent handoffs*, repeat-read/review/retry causes, final code/test outcome and Claude gate. Compare total premium-token/cost **including PM, subagents, formal review, retry and repair**, not isolated company-response tokens. Reuse existing `runs`, `trace`, provider accounting and Git/CI receipts; no new metrics service, polling daemon, fixed percentage promise or additional blocker solely for measurement.

**This delivery round's exact STOP — S3, not G3:** S1 = current architecture PR #198 independently reviewed and merged; S2 = approved D6 registered OpenCode task-root plus tested Worker launch/permission/data route (historical `/claude` transfer not required); S3 = **one real llmtool source-change work package** in which OpenCode develops, pre-reviews/cross-reviews, fixes and tests; **Claude independently verifies**; result with actual commit/Review/usage/relay evidence reaches the **same active foreground Integrator without the Owner ferrying prompts**; Integrator legitimately integrates/records acceptance. On **all S3 criteria actually verified**, **stop new work-package dispatch immediately**, publish one exact-evidence outcome report (or explain still-unmet G1/G2/G3/G4), and await the next Owner-directed delivery round. Do **not** automatically start inactive original-Session wake work, the G4 three-package pilot, a broad controller, or other unrelated enhancements. Active-foreground S3 PASS is **not full original-Session G3/M2 PASS**. On an unresolved authority/egress/mandatory-review gate or policy-exhausted root-cause, STOP the affected task, preserve evidence, report `BLOCKED`; other explicitly authorized non-dependent work remains eligible within the current scope. This S3 stop overrides generic “keep developing indefinitely” instructions.

**Codex quota-exhaustion and reliable Reviewer publication (Owner observation 2026-10-19):** The Owner observed that the shared **Codex 5-hour quota was exhausted** while actual independent Claude Reviewer results were locally completed but not visible to the Integrator. Four session-to-session delivery attempts reportedly failed because the Integrator session address changed. **Once the Owner says Codex quota is exhausted, STOP all Codex Integrator, Developer and Codex subagent inference; do not ask the Owner to reopen them, retransmit large prompts or spend to reconcile results.** Preserve in-progress Git/worktree files and task evidence. This is a pause of dependent Codex execution, not permission to bypass formal reviews or authorize external OpenCode source egress.

- **Claude formal Reviewer publishes directly and promptly to the applicable GitHub PR/Issue**, with exact reviewed candidate SHA, Security/Functional findings, severity, test commands/results, reproducibility/flakiness and disposition, regardless of whether a direct Integrator Session notification succeeds. Avoid holding completed review evidence solely in local context or waiting for an obsolete session endpoint. A GitHub comment is a durable **record**, **not** real-time wake/receipt; recovery requires the actual Integrator to re-read the published evidence later.
- Prefer **read existing evidence and only run missing or invalidated tests**. Do not re-run unchanged Claude-reviewed Mac-native tests just to duplicate the reviewer, and do not force Codex Developer self-review subagents where the formal Claude gate and deterministic checks are already sufficient. Never reuse a test PASS across a changed relevant SHA without checking whether it remains applicable.
- Recorded case: [Issue #193](https://github.com/davidcatcat/davidcattoolsproject/issues/193) has now received [Claude's independent exact-SHA Mac review](https://github.com/davidcatcat/davidcattoolsproject/issues/193#issuecomment-6255041305). Source Security/Functional PASS and 300/300 native tests were recorded, but **CHANGES_REQUESTED** remains for a Mac-only 2/15 EPERM cleanup-test flake; its F2 transient false-UNKNOWN is deferred. Only an affected re-review after repair can clear F1. [PR #198](https://github.com/davidcatcat/davidcattoolsproject/pull/198) merged before the **original Mac Claude session's** [post-merge ACCEPT](https://github.com/davidcatcat/davidcattoolsproject/pull/198#issuecomment-6255040914). A **separate bounded Claude review** had already approved the corrected seven-file candidate; its [Integrator reconciliation](https://github.com/davidcatcat/davidcattoolsproject/pull/198#issuecomment-6254628720) was published at 20:15:19Z before the 20:19:38Z merge. These are distinct review contexts; do not infer that the held original session published before merge or that no independent Claude pre-merge evidence existed. Reuse each exact-source verdict within its actual scope; no duplicate review is needed.
- During Codex quota exhaustion, ChatGPT Coordinator may perform **explicitly authorized GitHub-only documentation and evidence management** using its own connector without activating any Mac CLI, OpenCode model, or paid fallback. Company-gw and OpenCode may only perform tasks actually authorized, reachable and technically qualified; they are **not assumed to replace the disconnected Codex PM/Worker**. Resume Codex only after usable quota and Owner direction, using a **short SHA-pinned incremental status summary**, not a repeated full-history megaprompt.
- Stop collecting the same usage and handoff telemetry from expensive Codex models. Record verified counters when readily available or `UNKNOWN`, and reconcile actual quota consumption later from provider-native usage; do not add another model call just to produce a cost accounting report.

## Prior safe-git G1–G7 fixed-root pilot text

## HISTORICAL G1–G7: two-fixed-root pilot (NOT ACTIVATED under its original policy)

**HISTORICAL / SUPERSEDED by Owner D6 as to root count, root provisioning,
worktree unlock/remove and routine safe cleanup.** These older requirements
are retained as evidence, not current limits. Still enforce one writer per
worktree, canonical-only integration/native F1, no `main` checkout by
Developers, no unclaimed fetch in canonical, registered session/branch
ownership, no arbitrary clones, and no automatic F2, egress or merge grant.
The explicit anchor above preserves older intra-repository references.

The requirements owner explicitly approved **at most two fixed, reviewed linked Git
worktrees** for trusted Codex CLI and Claude CLI development in
[Issue #110](https://github.com/davidcatcat/davidcattoolsproject/issues/110#issuecomment-6106421018).
This is a narrowly scoped exception to the former canonical-only prohibition,
**not** general permission to create project checkouts, clones, Git archive copies
or arbitrary temporary worktrees.

**HISTORICAL — superseded for root count and removal by D6.** Operational mode was ACTIVE only when the independently reviewed activation-status
documentation PR containing this record had merged into `main`. While this
record exists only on a PR branch, the published `main` policy remains
**DISABLED**. Once merged, ordinary development is permitted to exactly one
trusted Codex CLI in its fixed Codex worktree and one trusted Claude CLI in its
fixed Claude worktree, each with a single owner/writer and an independently
verified clean task boundary. The canonical integrator/native-acceptance root
remains separate; activation is NOT permission for any additional project root.

**G7 activation evidence and immutable source bindings:**
- Requirements-owner [narrow two-root approval in #110](https://github.com/davidcatcat/davidcattoolsproject/issues/110#issuecomment-6106421018); accepted policy base
  `2ba7925d1bb7bc22732642f2358b1ba0aab5718c` (prior `main`),
  reviewed G1 v2.1 [plan](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6107047473)
  SHA256 `38708c1ec0ba77510cc66b008290a3bdf4404a62b7434ce0705ce371d3a0806d`,
  and G1 [independent review](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6107190863).
- G5 [independent native review](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6111243764)
  and [Coordinator acceptance](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6111265049):
  two overlapping real developer windows; separate own-root venvs, frozen
  SHA-bound PRs #119 (`0cec8f47`) and #121 (`d9706353`) with CI/review.
- G6 approved [native Annex DRAFT 2.1](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6112962040)
  ROW-block SHA256 `12bf30daf33ee2a203de44e305172c4dc86d38f7ff94ba584dc7ed345df65118`;
  r01-r07/r09 [independently accepted](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113673728);
  r08 invocation SHA256 `bc3048632aadecb2b5534eb3c97d9e2547c21fd353ec99a7cd16d47fcaf8c1d3`
  [independently accepted](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6114027126);
  r10 is evidence-only, satisfied by merged PR #125 (`2ba7925d1bb7bc22732642f2358b1ba0aab5718c`),
  [exact-head CI](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38199694423)
  and [independent Mac evidence approval](https://github.com/davidcatcat/davidcattoolsproject/issues/122#issuecomment-6118492559).
  See [G6 Coordinator consolidation](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6118538797).
- G7 independent [activation-evidence verdict: PASS_ACTIVATE_TWO_ROOTS, conditional
  C1-C6](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6118588175).
  This activation-status PR itself still requires a **different, independent
  exact-HEAD Security/Functional documentation review and applicable CI before
  merge**. A review comment or OPEN/Draft PR does not activate anything.

This historical section heading is deliberately retained to preserve existing
Markdown anchors; the **Operational mode** line above, only on merged `main`,
determines the actual state.

Canonical integrator and required native acceptance root (must never be moved
or cleaned as part of allocation):
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

The **only** eligible fixed developer roots, during an explicitly authorized
#115 limited pilot or after formal activation:
- Codex CLI: `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex`
- Claude CLI: `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude`

### 2026-10-19 Owner D1: OpenCode takes over the existing second fixed root — NOT YET LIVE

[Owner D1–D5 decision](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/docs/decisions/llmtool_opencode_secondary_developer_20261019.md) approves **reassigning ownership, not adding a worktree**. The existing physical root ending `/claude` will remain at that pathname but become the exclusive OpenCode Developer root **only after** independent Security/Functional review of the ownership rule and source-bound **Mac native release/C2/C3 evidence**. Its earlier two-root G7 acceptance proves the historical Codex+Claude layout; it does not automatically prove transfer to OpenCode. Do not treat a GitHub decision or document merge as physical activation.

First independently verify the paused Claude Developer's real Session/descendants/tests are released; inspect exact root/common-dir, branch/HEAD/index, tracked/untracked/ignored files, Git locks, permissions and dedicated venv/import identity. Preserve old work, retain unknown data and STOP if another writer or incomplete release is found. Only then admit **one** task-bound OpenCode writer in that root, with distinct OpenCode CLI/Session/task/branch evidence. Claude **Reviewer** must use separately authorized *read-only* review context and may not interfere with OpenCode Git refs, worktree or tests. The paused Claude **Developer** must not keep writing in the second root.

Owner D4 permits OpenCode to **read the entire non-sensitive repository**, not secrets, arbitrary external Mac directories, automatic provider data egress, writes outside the authorized task or llmtool F2. Verify the installed OpenCode **v2** permission schema, effective read/search/shell/task/external-directory effects, provider-specific privacy/egress authorization, redaction and negative leakage tests **before real external source transfer**. Prompt-only rules are not an OS boundary. Preserve all the C2/C3, single-writer, shared-ref and existing Git-authority gates below; **no unreviewed historical-root takeover, rename, clone, forced branch operation or automatic paid fallback**; the earlier no-third-worktree and no-cleanup limits are **SUPERSEDED by D6** for newly registered task roots.

For OpenCode/DeepSeek and GLM pre-review, repository reads/searches and command outputs entering agent context **are external provider egress**. Record the D4 gate for the exact models and permitted data scope **before the first such repository inspection**; until then only explicitly provider-cleared content may be used. [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) and [company delegation](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/company-llm-delegation/SKILL.md) retain their separate route-specific limits and supply no implicit OpenCode source-export grant.

### Immediate single-checkout handoff (active now)

Before branch/source/Git writes in the shared canonical root, verify its exact
root, branch, HEAD, remote, tracked/untracked state and whether another agent's
test/child process is using it. The writer posts
`CHECKOUT-CLAIM <agent> <task-branch> <observed-HEAD>` in the task's GitHub
handoff. A PR publication or review request is **not** a release. When source
is published and all owned tests/descendants have stopped, return safely to
`main` only if clean, verify tracked/untracked data and HEAD, then post
`CHECKOUT-RELEASE <agent> main <HEAD>`. If unsafe, post HOLD with evidence
without switching/cleaning. Audit comments are **not atomic locks**: the next
agent must independently verify exclusive local ownership and fresh state.
Never infer current local ownership from a historical comment or bare PID.

### HISTORICAL — pre-activation limited Mac pilot (not repeated for D6)

Before any real-project worktree is created, the #115 native plan must be
independently Security/Functional reviewed at exact source/command revision.
A coordinator's separate, explicit **PILOT_SCOPE_AUTHORIZED** record in #115
must identify the named Mac executor, approved immutable policy SHA, exact
two fixed paths, owned task branches, allowed preflight/provision/test commands
and stop/retention criteria. A generic issue comment, review request, expired
CLAIM/RELEASE, or this docs change is NOT pilot authority. The executor must
independently confirm the current canonical checkout, no other writer or
descendants, owner/permissions, no symlink/path escape, Git common-dir/branch
inventory and preservation of all unknown data. If any identity, plan or
permission differs, STOP without provisioning or changing any Git state.

**Only within that approved pilot scope**, its named executor may provision
and test the two exact linked worktrees while normal developer parallel use
remains DISABLED. The pilot may run bounded overlapping **test/fixture-only
or otherwise separately approved small development tasks**, without touching
canonical source/user data or converting the pilot into unrestricted feature
development. The pilot never authorizes llmtool F2 model-initiated mutation.
A verified fail-closed test or recovered interrupted session is evidence to
review, not an automatic activation. The coordinator must obtain an
independent native gate verdict before any ACTIVE-status PR.

### HISTORICAL G7 controls — fixed-root provisioning/removal superseded by D6

- **HISTORICAL / SUPERSEDED by D6:** only the named, approved integrator/provisioner formerly staged a linked worktree at
  one of the two exact, empty, verified roots. Check parent ownership, 0700
  permissions, symlink/ACL risks, canonical common-dir identity and existing
  Git worktree/branch inventory first. Preexisting data or ambiguous identity
  means STOP/preserve; no overwrite, auto-adopt, prune or delete.
- The canonical checkout keeps local `main`. **Neither agent worktree may
  check out `main`**, another agent's task branch or `automation/ai-loop`.
  Each agent owns one worktree and one unique task branch at a time; verify
  root, common-dir, HEAD, branch, staged/unstaged/untracked/ignored contents
  and allowlisted files before every mutation. No concurrent writers in a
  single worktree; do not switch a root underneath running tests.
- Each developer worktree needs its **own venv, editable install, build/cache
  and test state**, not a symlink to the canonical venv. Record
  `sys.executable`, `llmtool.__file__`, root and exact source SHA; if
  imports resolve to canonical or a peer worktree, STOP and invalidate that
  test evidence. Do not assume `-I` protects a shared editable install.
- Confine stage/commit to explicit allowed paths, push only one's owned ref
  (no force), publish Draft PR and freeze exact HEAD for review. An independent
  reviewer cannot self-approve authored changes. Shared worktree provisioning,
  ref integrations, destructive Git maintenance and canonical Mac native
  acceptance stay serialized with the integrator. Bounded ordinary
  `git fetch origin` is permitted within an authorized worktree subject to
  known-safe Git environment/config and conflict/lock handling.
- No `reset --hard`, `clean`, arbitrary stash, forced checkout/push,
  `gc`, `prune`, shared config mutation or unattended process kill.
  **SUPERSEDED by D6:** the old blanket ban on `worktree unlock/remove`
  and ordinary task cleanup; only D6's registered, evidence-gated path
  is permitted. Unregistered worktree remove/move/repair remains forbidden.
  Preserve dirty, ignored and unknown user data, commits and active descendants
  on crash or failed validation. Fall back to the single canonical writer on uncertainty.
- Git linked worktrees share refs and object storage. Same-UID permissions
  and advisory ownership are **cooperative race protection, not an OS security
  sandbox**. This exception does **not** authorize llmtool F2/model-initiated
  real-repository mutation, credential access, new provider egress, admin/root
  operations or CI/Runner/ai-loop controller changes.
- Before general two-agent use, record a **real Mac pilot** showing canonical
  branch/HEAD/index and user files unchanged; distinct worktree HEAD/index and
  peer-independent changes during overlapping small tasks; correct dedicated
  venv/import paths; no branch/owner/symlink bypass; preservation after a
  simulated interruption; two SHA-bound Draft PR/CI/review paths. Native F1
  containment acceptance must still execute only from canonical. If a
  required check is absent/failed, keep parallel mode DISABLED and preserve
  all work.

## Prior ROADMAP D7 role/status block

## 2026-10-19 Owner D7 (HISTORICAL; see D8) — operative Developer priority

OpenCode CLI/DeepSeek is now the **PRIMARY Developer**. **Codex
Developer is PAUSED** after source review of frozen
[PR #202](https://github.com/davidcatcat/davidcattoolsproject/pull/202)
at `c421c3a`; no further costly Codex Developer source/repair/reviewer
calls. The existing Codex Integrator remains semantic PM/actual
OpenCode launcher/receipt owner/final merger when available.
Reuse merged PR #202 Worker and merged PR #204 reviewed Host/F2 source;
these remain **NOT LIVE VERIFIED** as a genuine llmtool-owned dispatch.
Do not reuse an Owner-active manual TUI root as a Host-owned Worker
without independent liveness, attribution and clean ownership checks.
Next prove one real
source-change/tests/DeepSeek self-review/qualified free different-model
pre-review/independent Claude formal PASS/Integrator receipt cycle.
Older D1–D5 Codex-first language below is HISTORICAL under D7.
D6 auto-retirement is not yet Mac-live verified; it is not a blocker
for a safely registered new task. No new A milestone, extra daemon,
provider-export/F2 waiver or auto paid fallback. Stop at actual S3
PASS with evidence; do not infer live proof from docs or fake CI.

**D4/Host launch condition:** exact-file re-admit any new source read/write
set **before** OpenCode provider inference. No OpenCode-authored Host/
containment patch may authorize itself: require independent Claude
Security ACCEPT before its changed permission, diff, negative-probe
or price guards admit any subsequent invocation. The reviewed PR #204
Host is the first allowed S3 host candidate; its code merge is not
proof that `run()` or same-Integrator ACK happened.

## Prior WORKFLOW D7 and forced-stop blocks

## Owner D7 — OpenCode PRIMARY; Codex Developer PAUSED (HISTORICAL; see D8)

[PR #202](https://github.com/davidcatcat/davidcattoolsproject/pull/202)
delivered a source/offline-reviewed Worker adapter `c421c3a` and is
**merged**, while PR #204 has since merged the independently reviewed
foreground Host and #193 F2 fix into source. **No host-owned live S3
invocation has yet been verified.** OpenCode remains primary Developer
for the remaining real S3 execution package
on the registered D6 `tasks/a11c-opencode-193-s3` root. Pause all
Codex Developer author/subagent work; do not rewrite source already
independently accepted. Codex Integrator stays PM/launcher/merge owner.

OpenCode develop/test → DeepSeek separate-context self-review →
authorized actually free different-model pre-review → independent
Claude formal source/security/native review → Claude FAIL back to
OpenCode repair, PASS to Integrator with exact-SHA CI and Mac evidence.
No implicit F2, privacy-export, paid-model, historical root takeover
or Session wake grant. Stop after one actual S3 PASS and report.

**Bootstrapping boundary:** apply the normative D4 exact-file
re-admission and independent formal Claude Security review of any
OpenCode-authored Host/containment code **before** using that code to
admit a new OpenCode invocation. PR #204 code is reviewed/merged, but
the Owner's manual TUI authored the candidate, so its activity is not
host-contained S3. The first trusted Host run retains the exact two-file
D4 admission and has no concurrent manual writer.

### 2026-10-19 Owner token-efficiency correction — Developer pre-review and S3 stop

The normative rules for **one meaningful dispatch → Developer's own isolated-context pre-review/fix/test → one formal Claude review → Integrator acceptance** and **the mandatory end-of-round S3 STOP** are in [ai-team-delivery](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md#2026-10-19-owner-direction--developer-owned-pre-review-economical-handoffs-and-s3-stop). They apply **now** to all already-authorized A11c work, with OpenCode PRIMARY and Codex Developer PAUSED under Owner D7, and are not conditional on a new llmtool feature.

**Integrator must not become a second routine Developer/Reviewer** and must not bounce every failing test or minor review comment between expensive Codex sessions. A Developer may conduct its own separate-context Reviewer subagent pre-check and bounded fixes, while Claude remains a distinct **formal** reviewer. The company `gw` route remains an explicitly qualified, bounded **low-cost advisory** for appropriate test/log triage and summaries, **not** a new mandatory model layer or independent formal QA. Capture actual work-package-level total usage, relay count, correction/overhead and missing values as `UNKNOWN`; do not make unsupported saving claims or add a new telemetry service.

**S1** architecture PR #198 review/merge; **S2** reviewed registered D6 OpenCode task-root and real Worker launch/permissions (without compulsory historical `/claude` takeover); **S3** one real OpenCode source/test/pre-review → Claude formal review → original **active foreground** Codex Integrator return and integration, with no Owner relay. **After S3 verified, stop dispatching new tasks and report.** Inactive original-Session wake and G4 remain future rounds, despite the previously approved full G3/M2 outcome target. A documentation merge, fake fixture, self-review or GitHub comment does not prove the S3 Live acceptance. The previous generic guidance to keep progressing does not override this explicit current-round stop point.
