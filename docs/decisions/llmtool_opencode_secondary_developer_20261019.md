# llmtool — OpenCode secondary Developer: Owner decisions D1–D5 (2026-10-19)

> **Owner decision:** ACCEPTED, 2026-10-19. **Execution evidence:** NOT IMPLEMENTED / NOT LIVE VERIFIED. The Owner separately authorized starting Git/document updates; this document is not evidence of a physical Mac worktree reassignment, model/data egress authorization, executable Adapter or A11c milestone completion.
>
> Applies within **existing A11b/A11c, M1b/M2 and G1–G4 only**; no new A milestones, third linked worktree, permanent controller, PM, daemon or automatic Cloud integration.

Authorities: [Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172), [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120), [ROADMAP](../../tools/llmtool/ROADMAP.md), [WORKFLOW](../../tools/llmtool/WORKFLOW.md), [safe-git](../../skills/safe-git/SKILL.md), [owner G1–G4](../governance/llmtool_owner_outcome_reporting_v1_1_20261019.md).

## Accepted allocation

| Role | Current owner decision |
| --- | --- |
| Codex Integrator/PM | Existing sole semantic PM and Integration Manager: task splits/routes, priorities, interpretation, merge gates, evidence, next task |
| Codex Developer | First-line primary code developer |
| OpenCode v2 / DeepSeek | Second-line code developer; DeepSeek same-model self-review, GLM independent fresh-context cross-model **pre-review** |
| Claude Code | Dedicated **independent Reviewer**: code review, functional/security assessment, tests, verification; **Claude Developer PAUSED** pending Owner token availability decision |
| Company LLM (gw) | Continue previous low-cost, per-task authorized quality-checked advice/triage/context compression, not direct Shell/Git executor |
| AGY | Backup only due to low token quota |
| Codex Cloud and Claude Cloud VM | Independent development environments; no new Mac auto-integration plan. Existing optional Codex Cloud lane stays separate from M2 |

**Boundary:** llmtool executes the Integrator-authorized plan and owns bounded Worker/Host/process lifecycle, deterministic events, source-pinned review packets, evidence, budgets and handoff, **not PM semantic decisions**. OpenCode is a tool-capable CLI Worker, **not a text-only `Provider.complete()`**. Reuse existing process lifecycle/Task/Review/Policy/Git contracts; no duplicate orchestrator.

## D1 — reassign the second fixed worktree: ACCEPTED (A)

After the paused Claude Developer releases ownership, reassign its **existing fixed physical linked worktree** (historical pathname ending `/claude`) to OpenCode Developer. **Keep that exact pathname for now**; do not rename, move, clone, copy or create a third worktree. Preserve the canonical Integrator root, existing Codex fixed root and Git common directory unchanged.

**Not active by declaration alone.** Verify old Claude Developer/session/descendants/tests actually released; preserve branch, HEAD/index, tracked/untracked/ignored data, locks and user files. Independently review the policy/execution change, then prove native Mac C2/C3 new OpenCode Session identity, sole-writer ownership, dedicated venv/import provenance, task branch and no concurrent Claude Developer writer. Unknown state is STOP/preserve, not forced cleanup. Claude **Reviewer** may read only under separately valid review authority and may not interfere with the OpenCode writer/shared refs. Ordinary safe-git and per-action Git authority remain in force. Owner need not reapprove this selected option.

## D2 — foreground loop before reliable original-session wake: ACCEPTED (A)

**First useful slice:** existing active Codex Integrator → llmtool → scoped Codex/OpenCode Worker → Developer tests/self-review (OpenCode also GLM cross-model pre-review) → Claude independent formal review → bounded fix/retest or return to **same active foreground Integrator**, with **no Owner intermediate prompt relay**.

**Full G3 still requires** real original Codex Session addressed delivery, ACK, dedup/replay, source/identity verification, stop/recovery and authorized independent release evidence. Reuse existing `codex_pickup_native` and task/handoff/ledger rather than inventing a scheduler. A foreground PASS is useful progress, **not** `M2_LIVE_VERIFIED`.

## D3 — Claude quota depletion: ACCEPTED (A)

Keep previously authorized Codex/OpenCode Developer work moving with tests and own pre-review. Place candidates requiring Claude formal review into **logical** `WAITING_CLAUDE_REVIEW`, retaining original Task Contract, candidate exact SHA, observed tests and review scope. This is not proof that a durable enum/store currently implements that state. Integrator bounds pending review inventory and prioritizes it; on quota recovery review the *current* exact candidate (revalidate stale HEAD). Never call self-review formal independent PASS, silently merge unreviewed changes, auto-pay for alternate models, auto-restart Claude Developer or allow unbounded backlog.

## D4 — read entire **non-sensitive** llmtool repository: ACCEPTED (B)

OpenCode Developer and allowed internal Reviewers may inspect/search **all non-sensitive project source, tests, documentation, skill instructions and relevant Git history**, even beyond the task's writable file allowlist. This **does not** expand write authority, project directory boundary, Git authorization, or any other Provider's egress scope.

Do not read/export private credentials, secrets, API keys, internal VPN/auth material, customer/personal data or restricted logs/diffs; check sensitive material wherever it appears, including Git history, shell/test stdout and subagent context. **Repo inspection ≠ permission to export every inspected byte to a free external model.** Verify exact selected provider privacy/retention terms and actual egress route authorization, redaction controls and negative tests before source is sent to external models. Verify installed OpenCode **v2** permissions for file read/search, shell, edit, subagent/task and especially external-directory effects: prompt-only secrecy controls and default vendor configuration are insufficient; scoped host isolation may be necessary. No arbitrary local Mac filesystem access.

## D5 — free models only unless Owner separately authorizes spend: ACCEPTED (A)

Prefer role-qualified currently free OpenCode models. If DeepSeek/GLM `-free` IDs disappear, change price, quota or performance, qualify an available free substitute with capability/quality/privacy checks. **Never silently switch to paid API** or report historical US$0.00 as current price. If none qualifies, mark OpenCode unavailable and let Integrator allocate authorized work to Codex. Preserve Claude formal independent review and actual usage/cost or UNKNOWN.

## Implementation dependencies: existing A11 only

1. **Docs/policy and independent review:** record accepted D1–D5 and adjust safe-git's existing root-transfer rule plus roadmap/workflow/Agent pointers. GitHub PR is not a live Mac CLI notification.
2. **Mac D1 + D4 gates:** independently verify old Claude ownership release, existing-root turnover, effective OpenCode v2 permissions/externals, selected free-model vendor privacy/egress basis; no third checkout.
3. **A11c Worker integration:** reuse real Host/CLI lifecycle, budgets, source-based results and cancellation; run a bounded Mac OpenCode Worker in the activated root with DeepSeek self-review, fresh-context GLM pre-review, repair/test evidence. Historical OpenCode Lab 79-pass/unpublished work is **not** a production llmtool Worker gate.
4. **Claude formal review:** Host (not model claims) collects original contract, exact Diff/base/head SHA, test/CI evidence, produces trusted packet; Claude FAIL returns bounded concrete fixes to original Developer, PASS resumes active Integrator. Keep current no-infinite-retry and source freshness rules.
5. **G1–G4 unchanged:** real M1b source adoption, real authorized Worker, complete original Integrator wake/ACK/repair and independent Review E2E, then three real Mac-supervised packages with Owner GO/LIMITED GO/NO-GO. Do not claim these gates pass from a docs PR/fixture.

**Parallel, not prerequisites for useful initial slice:** existing company-gw route qualification, A11b F2 direct llmtool-repo mutation, AGY backup and Cloud. Worker-owned Git within separately authorized registered workspace is not blanket-gated on F2; any real **llmtool-owned** Git mutation remains F2-gated.

## Acceptance vs reality

All **D1–D5 architecture decisions ACCEPTED**. Physical OpenCode ownership, effective privacy controls, actual free-model availability, runtime Adapter, trusted cross-CLI delivery, candidate queue durability, G1/G2/G3/G4 and any merge are **NOT VERIFIED**. Require source-pinned independent technical review and native Mac evidence where applicable; only material new risk/cost/product choices return to Owner. Routine authorized engineering work belongs to Integrator.
