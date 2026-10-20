---
name: ai-team-delivery
description: Coordinate AI-led development for a requirements owner who delegates technical PM, coding, review, QA and safe PR merges.
---

# AI team delivery

Use this workflow for product work, review, QA handoff and Git publication. [AGENTS.md](../../AGENTS.md) owns current authority, roles, privacy boundaries and Owner escalation. This skill defines the work-package path; it does not add runtime capabilities or override specialist gates.

## Roles and task contract

The Integrator/PM converts the accepted goal into a bounded task and owns routing, dependencies, acceptance interpretation, evidence reconciliation and integration. The assigned Developer is the sole writer in its registered task worktree. The independent Reviewer examines the exact candidate and evidence; the Reviewer does not edit or approve its own work. A Git publisher executes only the exact authorized publication operations.

Before dispatch, record:
- base SHA and repository/worktree identity;
- intended behavior, writable files and prohibited actions;
- assigned Developer, model and provider route;
- deterministic checks, independent review and exact-head CI required;
- provider output cap, native retry cap of at most one extra request followed by job termination, timeout and cleanup requirements;
- Mac/runtime evidence required, if any.

For OpenCode, D8 authorizes the PM to admit qualifying tasks without Owner-per-call approval. Confirm the free route, D8-authorized non-sensitive data class, provider cap, native retry limit (at most one extra request, then kill the job) and D6 write scope. A changed file set inside that approved data class does not require a new Owner egress decision. An OpenCode-authored Host/containment change requires independent Security review before that Host version admits another Worker.

## Developer convergence and formal review

The Developer implements the bounded packet, runs relevant deterministic checks, performs a distinct-context self-review and fixes ordinary findings before formal review. OpenCode must complete a qualified, actually-free different-model pre-review. If no qualified route is available, record the concrete limitation and mark that OpenCode task BLOCKED; the PM should route unrelated authorized work while resolving the missing gate. Pre-review is not independent acceptance.

Submit one consolidated exact-SHA candidate to the non-author Reviewer with contract, diff, test results, CI provenance and applicable runtime evidence. The Reviewer reports severity, reproducibility, limitations and disposition. On FAIL, return bounded findings to the original Developer, rerun affected checks and request affected rereview. Never merge on self-review, stale verdict or green CI alone.

After review and exact-head CI pass, reconcile the tested SHA against the current target, assess integration changes, and merge only when every task-required gate passes under [safe-git](../safe-git/SKILL.md). A relevant source change invalidates affected checks and review. Use company-gw only for a qualified, privacy-authorized, bounded advisory route; it is not a formal Reviewer and cannot make unsupported net-savings claims.

## Defect triage and repair bounds

Fix reproducible user-impacting defects in priority order: safety/data loss, blocked core workflows, degraded user-visible behavior, then maintainability. Preserve the root cause and reproducer. Use the smallest repair that addresses it; never weaken tests to clear a finding. A blocking gate stops the affected task, not unrelated authorized work.

## Mac and live evidence

Before runtime acceptance, establish the tested checkout, exact HEAD, installed executable path/version and required feature ancestry. Run Mac-only checks only on the actual Mac. Record commands, exits, expected markers, usage/receipt/ACK and cleanup as applicable. A resident TUI PID, model assertion, fake fixture or GitHub event alone does not prove a Host-owned Worker launch. Keep source completion, CI, formal review and live acceptance separate.

Evidence must not contain credentials or restricted logs. Use redacted identifiers and exact source hashes. GitHub publication is durable evidence, not proof of Session wake or notification delivery.

## Efficient handoffs

Prefer existing evidence and rerun only checks invalidated by the candidate. Bundle related fixes into one useful handoff; avoid PM micro-approvals and repeated full-context reports. Record usage and overhead when observable; otherwise use UNKNOWN.

<a id="work-package-delivery-and-in-session-reviewer-subagents-owner-decision-2026-10-14"></a>
### Work-package delivery and reviewer subagents

A Developer may use an in-session read-only Reviewer child only when the CLI supports it and effective permissions are verified. It is pre-review, not independent acceptance. Keep one writer per worktree and give the Reviewer only the pinned diff, tests and contract.

<a id="user-impact-first-defect-triage-and-bounded-delivery-owner-decision-issue-92"></a>
### User-impact-first triage

Preserve the report, user impact, reproducible evidence and affected scope. Prioritize safety and core workflow impact before polish. Reassess scope after repeated no-progress packages; do not create a new milestone or tool to avoid a concrete repair.

<a id="owner-d7-2026-10-19-supersession-opencode-primary-developer"></a>
<a id="2026-10-19-owner-direction--developer-owned-pre-review-economical-handoffs-and-s3-stop"></a>
### Historical D7 pre-review/S3 note

The former one-round S3 stop and paused-role assignments are historical. Current work follows the Task Contract, independent review, exact-head CI and applicable live gates; no blanket stop follows one milestone.

<a id="d8-task-admission-and-no-self-attestation"></a>
### D8 task admission and Host review

Use the task admission fields above. An OpenCode-authored Host/containment change requires independent formal Security review before that Host version admits a new Worker. A manual TUI or resident PID is not launch evidence.

## References

- [Current repository authority](../../AGENTS.md)
- [Roadmap](../../tools/llmtool/ROADMAP.md)
- [llmtool development workflow](../../tools/llmtool/WORKFLOW.md), for Cloud handoff or Mac/Cloud verification
- [safe-git](../safe-git/SKILL.md)
- [security-review](../security-review/SKILL.md), [qa-validation](../qa-validation/SKILL.md), [validate-runtime](../validate-runtime/SKILL.md)
