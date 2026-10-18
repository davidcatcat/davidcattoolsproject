# llmtool — Owner 目標、階段成果報告與決策制度 v1.1

> Owner confirmed: 2026-10-19. This document is the **management/reporting contract**, not a new product milestone, controller, daemon, workflow engine, notification channel, permission grant or acceptance evidence.
>
> Authority: the existing [A11 ROADMAP](../../tools/llmtool/ROADMAP.md), [WORKFLOW](../../tools/llmtool/WORKFLOW.md), [ai-team-delivery](../../skills/ai-team-delivery/SKILL.md), [safe-git](../../skills/safe-git/SKILL.md) and Issue [#120](https://github.com/davidcatcat/davidcattoolsproject/issues/120) keep their technical gates. This document defines **when to report outcomes and seek Owner decisions**.

## 1. Product outcome and division of labor

**Outcome:** `llmtool` materially participates in developing itself, with scoped Integrator/PM → Developer → distinct non-author Reviewer → repair/tests → Integrator integration. The Owner discusses direction with ChatGPT, notifies usability problems and decides material product/risk questions; the Owner must not become the day-to-day prompt courier, PM, reviewer or release engineer.

- **ChatGPT:** Owner-facing requirements, product trade-off analysis and independent high-level progress review **when actually invoked/connected**.
- **Mac Codex Integrator (= PM):** technical sequencing, already-authorized task routing, independent review, release-gate reconciliation, eligible PR merge, outcome and decision reports.
- **Codex Developer / Claude Reviewer / Codex Cloud:** perform bounded assigned tasks in their respective authorized environments; self-review never replaces non-author review.
- **llmtool:** execute and record authorized routes, lifecycle, evidence and handoff. **It does not replace the PM's semantic planning.** Native Codex/Claude communication proven separately is not proof that llmtool has automated the same workflow.

Quality and correctness outrank apparent token savings. Route company-gw only for a task class and data scope with actual permission and measured quality; report true usage or UNKNOWN. Do not create work just to raise PR/test/document counts.

## 2. Owner-approved principles

1. **Every accepted phase outcome requires a report backed by actual evidence.** A plan, fake test, GitHub comment, CI-only pass or model assertion cannot replace a missing Live/native gate.
2. **Escalate only material Owner decisions.** The Integrator resolves routine fixes, scoped task decomposition, review scheduling, qualified merges and nonblocking Low defects within prior authority.
3. **Useful first, unattended later.** Reach a usable llmtool on the Mac before investing in 24-hour hosting, extra daemons/controllers or general mobile automation.
4. **M2 begins with owner-controlled Mac pilot.** The Mac may sleep/shut down or lose VPN/network; bounded, truthful STOP/recovery is acceptable. Do not demand a 24/7 host as prerequisite.

## 3. Owner-facing outcome checkpoints (map to existing A11c gates)

These are **reporting checkpoints**, not additional A milestones or new serial engineering dependencies. Parallel work that is separately authorized may continue.

| Owner checkpoint | Objective evidence required | Report title |
| --- | --- | --- |
| **G1 — M1b adopted change** | One real llmtool task; permitted, provenance-bound llmtool analysis; Developer demonstrably adopts a useful proposal into a scoped source/test change; actual tests, non-author review, Git/CI, and eligible integration verified. Record what was adopted/rejected and any Owner prompt relays. | M1b 真實開發成果 |
| **G2 — M2 real Worker** | At least one independently authorized live Worker invocation with actual host/session identity, approved input destination/scope, one-call/ledger and spend/budget controls, truthful provider usage, returned result, verification and cleanup. Fake-only or read-only proof does not pass. | M2 Worker Live 驗收 |
| **G3 — M2 independent-agent E2E** | One real, authorized Integrator → Developer → distinct non-author Reviewer → bounded repair/tests → integrator Draft PR or truthful STOP cycle **without intermediate Owner prompt forwarding**. Verify actual delivery, ACK, wake, result consumption and failure/replay handling. Native peer messaging alone is **partial evidence**, not llmtool M2 acceptance. | 三 Agent 自動閉環驗收 |
| **G4 — Mac supervised pilot** | After applicable authorizations/gates, run **three real work packages of different types** using existing Mac/registered agents. Record per-task outcome, correctness/review, incident/recovery, successful integrations or scoped STOP, intervention count, actual usage/cost and missing evidence. Owner receives GO / LIMITED GO / NO-GO recommendation for Mac controlled use. | Mac 受控試運行與正式使用建議 |

**G4 scope:** existing Mac under Owner control, approved repo/worktrees/models/data/permissions only. No 24-hour uptime requirement. If sleep, shutdown, VPN loss, credentials or process failure occurs, observe bounded STOP or permitted recovery; do not claim unattended durability. No expansion of Git F2, safety hook installation, provider spend, secret egress, external server or production permission by this document. **Owner decides GO / LIMITED GO / NO-GO** for official controlled use; 24/7 unattended service is a subsequent separate decision.

**Current evidence rule:** previously recorded main/CI, company advisory, native peer ACK and GitHub PR merges are historical evidence for their exact scope. Never retrospectively mark G1–G4 PASS without each row's required actual evidence.

## 4. Reporting triggers, destinations and delivery truth

The **existing Integrator** owns reports; do not install a second PM/reporter.

- **On each G1–G4 acceptance:** publish an outcome report immediately after verifying all required evidence, referencing exact issue/PR/review/CI/runtime identities. Report VERIFIED, INCOMPLETE, BLOCKED or NOT_TESTED accurately. Continuing already-approved parallel work does not wait for Owner to acknowledge a routine report.
- **On a material Owner choice:** immediately publish a decision brief and request the minimal necessary authorization. Only dependent actions wait; unrelated already-authorized work continues.
- **Weekly:** publish **one compact consolidated project pulse**, reusing the existing GitHub tracking issue (normally #120) rather than a new report-only PR or issue per week. No fabricated day/hour; scheduling or push notification is a separate capability.
- **Stalled delivery:** if **two completed work packages in succession** yield no verifiable progress toward the current outcome gate, the Integrator records a scope/priority correction and either changes work allocation within existing authority or escalates a genuine product trade-off. This is not a mandatory stop for every defect or a license to bypass failed controls.
- **Delivery channel:** GitHub issue/PR report is a **durable ledger**, **not** evidence that the Owner, ChatGPT or a Mac CLI was notified/woken. State separately `report_published`, `notification_attempted`, `notification_delivered`, `owner_acknowledged` with observed evidence or UNKNOWN. Until a real notification channel is configured and verified, the Owner reads via GitHub or ChatGPT-initiated/explicitly scheduled checks. Do not claim background ChatGPT execution.

## 5. Mandatory concise report shape

Use a single comment/report per checkpoint, not multiple management documents:

```text
OWNER OUTCOME REPORT — G[1–4] / [milestone] / [date]
Objective: [what became usable]
Status: VERIFIED | INCOMPLETE | BLOCKED | NOT_TESTED
Delivered: [real code/function/PR or observed usable behavior]
Evidence: [exact SHA, tests, non-author review, Mac/Live, usage/cost or UNKNOWN]
Critical path: [next necessary unsatisfied gates; feature-scoped blockers]
Next: [already-authorized work / accountable agent]
Owner decision: NONE | REQUIRED [option A/B/C, recommendation, impact]
Notification: GitHub published; actual delivery/ACK [verified or UNKNOWN]
```

Keep links rather than duplicating raw CI/logs, private tokens or large inventories. Count model cost and Owner interventions only when observed; unknown is not zero.

## 6. Decision authority and escalation boundary

**Integrator may decide without asking Owner:** task splitting, assigning eligible Mac/Cloud agents, scoped low-risk fixes, appropriate testing, review sequencing, technical repairs, nonblocking LOW deferral with owner/action, already-authorized model/data routes and eligible reviewed PR merges. A newly discovered bug is not automatically an Owner decision.

**Owner must decide before dependent action:** material product/architecture/scope or priority change; new major feature off the approved critical path; greater spend/new provider billing; wider data export, credentials, dangerous permission or accepted security risk; major unresolved release trade-off; declaring formal GO / LIMITED GO / NO-GO or substantially changing deployment/hosting. Present source-based issue, user impact, options, recommendation, cost/risk and default HOLD/continue-safe-work disposition. No response is **not** consent.

**Existing independent Security/Functional/QA, exact-source CI, Mac acceptance, worktree ownership and tool authorization gates remain binding.** A blocker specific to hook installation does not block unrelated text advisory; a BLOCKED real paid Worker cannot be reported as a completed M2. Do not invent micro-gates that recheck unchanged accepted evidence or delay progress for nonblocking LOW items.

## 7. Adoption, without a new system

1. Integrator adopts the above report and decision format in the existing GitHub work ledger and its normal tasks.
2. Map G1–G4 to the existing A11c/M1b/M2 evidence; don't open separate features or alter the roadmap numbering.
3. After each report, record next permitted work and any pending Owner decision; report real completion, not activity volume.
4. Do not claim the policy itself implements notifications, an automated ChatGPT callback, 24-hour service, live llmtool routing, F2 mutation or automatic release.

**Approval:** Owner confirmed the four governance principles and Mac-first supervised M2 pilot in ChatGPT on 2026-10-19. **Implementation:** this Git record is a documentation/management rule proposal requiring ordinary non-author review and appropriate repository integration. It is not evidence of G1–G4 passing.
