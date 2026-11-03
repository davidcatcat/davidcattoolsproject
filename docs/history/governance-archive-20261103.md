# Governance archive — 2026-11-03

This archive preserves historical workflow text removed from the current [llmtool development workflow](../../tools/llmtool/WORKFLOW.md). The Codex Cloud operating route remains in that workflow.

## Historical role and stop decisions

Earlier D7 role order and one-round S3 wording are preserved in [policy history](../../docs/history/llmtool-agent-policy-legacy-20261020.md); current tasks follow D6/D8/D8a and the 2026-10-21 Developer routing in [ai-team delivery](../../skills/ai-team-delivery/SKILL.md). D4 screening/admission applies to the llmtool-driven Host route; D4 Live Smoke is not a prerequisite for direct Developer sessions. The parked-source archive/removal scope is recorded in the [Roadmap](../../docs/history/llmtool-roadmap-archive-20261028.md#historical-role-decisions) and [README](../../tools/llmtool/README.md#archived-parked-implementations).



**A11b and A11c are the only active native delivery milestones.**
The C0–C3 names in older plans are historical outcome/measurement
labels mapped **once** in [the authoritative ROADMAP](../../tools/llmtool/ROADMAP.md),
not separate development backlogs. **A11b.2.2.2 private disposable-fixture-only F1 Git ADD** was independently accepted and merged as PR #84 (`46b17b13`). The separate [F2 real-repository mutation and A11b.3 approval design](https://github.com/davidcatcat/davidcattoolsproject/issues/97) is next; **F2 execution remains NOT AUTHORIZED**. The historical F1 records remain in [Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70).
Retain [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) separately for C1 excerpt egress privacy. Offline A11b design does not wait for full company Phase 0 / paid-token benchmarking.
Only F1's private disposable-fixture Git ADD is implemented and reviewed. **Real user-repository/public Git ADD (F2) remains NOT AUTHORIZED** pending its own approved contract, Security/QA and Mac gates.

The standalone transitional helper is documented in
[tools/ai-loop/README.md](../../tools/ai-loop/README.md); it is not an llmtool
route. This workflow covers llmtool only.


**Cutover happens in A11c, not by renaming C3:** prove on the canonical
Mac that one llmtool task can run a scoped Codex/Claude/test/repair
cycle and return a verified Draft PR or explicit STOP, with independent
Security/Functional QA and no manual prompt handoff. C1 excerpt output must
not cross any company/external LLM boundary without its final-output
safety gate and explicit authorization; other source/log/diff exports
need their own distinct trust review. Claim Token savings only after
quality-equivalent, attributable measurements for the actual route.

## AI-led team operating model

This project's delivery responsibility is documented in
[ai-team-delivery](../../skills/ai-team-delivery/SKILL.md). The **owner**
defines the tool's functionality, outcomes, constraints and acceptance,
while the **AI coordinator** acts as technical PM: task scoping, Codex or
OpenCode implementation, bounded Company LLM Developer assistance through
llmtool, independent review/QA (Claude or the temporary authorized Codex
Reviewer), follow-up tracking and authorized safe Git merges. The owner is not an agent prompt
courier or routine technical signoff authority. Only genuine product/scope/risk
decisions, or a needed Mac-local command when no execution access exists,
should interrupt the owner.

The standalone transitional helper is documented in
[tools/ai-loop/README.md](../../tools/ai-loop/README.md). The C1 local-only excerpts feature
does not authorize sending excerpts to company/external providers
(Issue #63 remains open).

A coordinator may decide a documented **nonblocking Low** disposition
within the approved product boundary and leave a concise follow-up; it
must not invent extra product blockers/one-off tools. Required failing
security, QA or Mac/runtime gates remain blocking regardless of the
owner's general merge delegation. This document describes workflow,
not an installed autonomous Mac executor or background service.

### 2026-10-17 accepted Coordinator / Integrator PM / llmtool boundary

[Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172) /
[Draft PR #173](https://github.com/davidcatcat/davidcattoolsproject/pull/173)
are the owner-approved architecture source; they do not implement new runtime rights.

1. ChatGPT Coordinator discusses owner goals, architecture and product decisions.
   For a complex approved work package, produce **one initial handoff** for the
   **canonical Integrator (= PM Agent + Integration Manager)**, which is Claude
   since 2026-10-25 (formerly the Codex Integrator; see AGENTS.md).
2. Integrator/PM decides when to use a local subagent, a distinct Worker or a
   bounded Company LLM Developer route through llmtool, and chooses dependencies,
   validation and bounded repairs. **llmtool executes an approved route/plan and records status,
   policy, usage, evidence and events; it does not independently reason about which AI
   should own complex work.** Do not add a second controller or fixed PM CLI.
3. The target Worker report -> llmtool trusted event -> **same Integrator**
   wake/resume loop must reduce owner prompt relays. Automatic original Session
   resume, trusted CLI handoff and full M2 Live E2E are **NOT_VERIFIED**;
   GitHub records alone do not awaken an existing Mac CLI.
4. Simple, clear, authorized tasks may later bypass PM through llmtool chat.
   Pure Git status/diff queries can remain deterministic; no F2 real-repository
   mutation/Commit/Push is made live by this decision.
5. Company gw is an API-only reasoning Provider, not Shell/Git authority.
   Use only a tested, privacy-authorized task class after factual and serious
   error gates; include independent QA, retries/repairs and PM token overhead
   in any net saving claim. Mark missing usage or price UNKNOWN.

### Current handoff

Follow the bounded Developer → independent formal Reviewer (other model family) → Integrator path and the dispatch-switch check in [ai-team-delivery](../../skills/ai-team-delivery/SKILL.md). D4 / llmtool-driven OpenCode through the D8a Host handoff remains background G3/M2 work, not a prerequisite for direct Developer sessions; earlier D7/S3 wording is historical.

Prior D1–D5 role text is archived with the other historical allocation decisions linked above.
