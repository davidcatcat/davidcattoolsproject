---
name: ai-team-delivery
description: Use when planning or carrying out product work, code changes, reviews, QA handoffs, repository publication, or safe PR merges.
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
- provider output cap, native retry stop, timeout and cleanup requirements;
- Mac/runtime evidence required, if any.

For OpenCode, D8 authorizes the PM to admit qualifying tasks without Owner-per-call approval. Confirm the actually-free, enabled route at admission (never infer current price from a historical `$0.00` record), D8-authorized non-sensitive data class, provider cap, retry stop and D6 write scope. The PM admits the task and screens each file set for secrets/restricted data; a file-set change within the authorized class needs no repeated per-file Owner approval ([Issue #120 direction](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6289116138)). Do not generalize D8 to another provider, paid route or data class. Any OpenCode-authored Host or Worker update requires a distinct review before use. Admission or containment changes require formal Security review before the updated Host admits another Worker.

**Developer routing (Owner direction, 2026-10-21):** Codex Developer is the default while quota is usable. When quota is exhausted, or the PM routes a task to OpenCode, direct OpenCode Developer work requires its own registered D6 worktree, OpenCode project permissions, a Task Contract, PR, independent Claude review and CI. The launch must demonstrate the output ceiling in the actual provider request and enforce at most one extra provider request before terminating the job. OpenCode v2.0.32 documents `limit.output` as a model limit used for context accounting, not proof of an exact request cap; its native retry schedule permits up to 10 retries unless a verified hook vetoes them. No direct-session launch proving both controls is established here. The specific follow-up before this route is used is a task-scoped, independently reviewed launch guard that clamps the outbound request, uses a verified retry hook to deny attempt 3 and later, and applies a fixed timeout with process cleanup. Until that guard passes those checks, use Codex when available or defer direct OpenCode. D4 / llmtool-driven OpenCode through the D8a Host handoff is separate background G3/M2 work and does not gate development. See the [official model-limit documentation](https://docs.opencode.ai/docs/providers/) and [v2.0.32 retry source](https://github.com/anomalyco/opencode/blob/v2.0.32/packages/core/src/session/runner/retry.ts).

## Developer convergence and formal review

The Developer implements the bounded packet, runs relevant deterministic checks, performs a distinct-context self-review and fixes ordinary findings before formal review. OpenCode must complete a qualified, actually-free different-model pre-review. If no qualified route is available, record the concrete limitation and mark that OpenCode task BLOCKED; the PM should route unrelated authorized work while resolving the missing gate. Pre-review is not independent acceptance.

Submit one consolidated exact-SHA candidate to the non-author Reviewer with contract, diff, test results, CI provenance and applicable runtime evidence. The Reviewer reports severity, reproducibility, limitations and disposition on the PR or tracking Issue with the exact reviewed SHA; this posting is still not a Worker ACK or Owner notification. On FAIL, return bounded findings to the original Developer, rerun affected checks and request affected rereview. If formal Claude review is unavailable or its quota is exhausted, keep the exact-SHA candidate in a bounded backlog; do not self-approve or substitute a Developer review. Never merge on self-review, stale verdict or green CI alone.

After review and exact-head CI pass, reconcile the tested SHA against the current target, assess integration changes, and merge only when every task-required gate passes under [safe-git](../safe-git/SKILL.md). A relevant source change invalidates affected checks and review. Use company-gw only for a qualified, privacy-authorized, bounded advisory route; it is not a formal Reviewer and cannot make unsupported net-savings claims.

<a id="p0--sole-owner-escalation-rule-owner-decision-2026-10-10"></a>
### P0 owner escalation pointer (current in AGENTS)

Current Owner escalation is defined in [AGENTS.md](../../AGENTS.md); see the [normative defect triage reference](defect-triage.md) for issue disposition.

## Defect triage and repair bounds

Fix reproducible user-impacting defects under the [normative Issue #92 triage policy](defect-triage.md), in priority order: safety/data loss, blocked core workflows, degraded user-visible behavior, then maintainability. Preserve the root cause and reproducer. Use the smallest repair that addresses it; never weaken tests to clear a finding. A blocking gate stops the affected task, not unrelated authorized work.

## Mac and live evidence

Before runtime acceptance, establish the tested checkout, exact HEAD, installed executable path/version and required feature ancestry. Run Mac-only checks only on the actual Mac. Record commands, exits, expected markers, usage/receipt/ACK and cleanup as applicable. A resident TUI PID, model assertion, fake fixture or GitHub event alone does not prove a Host-owned Worker launch. Keep source completion, CI, formal review and live acceptance separate.

<a id="owner-approved-outcome-reporting-2026-10-19"></a>
Evidence must not contain credentials or restricted logs. Use redacted identifiers and exact source hashes. Apply [Owner outcome reporting v1.1](../../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) for G1–G4 milestone checkpoints. GitHub publication is durable evidence, not proof of Session wake or notification delivery.

<a id="mac-operator-handoff-controller-trust-and-privacy"></a>

## Interim controller changes

Changes to the temporary ai-loop controller and frozen routes follow the [controller release and Mac handoff gate](controller-release.md).

## Efficient handoffs

Prefer existing evidence and rerun only checks invalidated by the candidate. Bundle related fixes into one useful handoff; avoid PM micro-approvals and repeated full-context reports. Record usage and overhead when observable; otherwise use UNKNOWN.

<a id="work-package-delivery-and-in-session-reviewer-subagents-owner-decision-2026-10-14"></a>
### Work-package delivery and reviewer subagents

A Developer may use an in-session read-only Reviewer child only when the CLI supports it and effective permissions are verified. It is pre-review, not independent acceptance. Keep one writer per worktree and give the Reviewer only the pinned diff, tests and contract.

<a id="user-impact-first-defect-triage-and-bounded-delivery-owner-decision-issue-92"></a>
### User-impact-first triage

Use the [normative Issue #92 policy](defect-triage.md); preserve this legacy anchor for existing links.

<a id="owner-d7-2026-10-19-supersession-opencode-primary-developer"></a>
<a id="2026-10-19-owner-direction--developer-owned-pre-review-economical-handoffs-and-s3-stop"></a>
### Historical D7 pre-review/S3 note

The former one-round S3 stop and paused-role assignments are historical. Current Developer routing is defined above and in the [D8/D8a decision](../../docs/decisions/llmtool_opencode_secondary_developer_20261019.md); D4 Host work remains separate background G3/M2 work.

### Review requests and Owner updates

For a formal Claude review, publish `CLAUDE-REVIEW-REQUEST: PR #N HEAD <SHA>` with the exact source, scope, review type and questions. This is a handoff record, not evidence that an automated reviewer ran. End each Owner-facing update with exactly one practical line: `你需要做：無` or `你需要做：<一個必要的動作或白話 yes/no 問題>`. Put technical failures and AI-owned next steps in the task/PR record; do not ask the Owner to diagnose them or relay routine prompts.

<a id="d8-task-admission-and-no-self-attestation"></a>
### D8 task admission and Host review

Use the task admission fields above. An OpenCode-authored Host/containment change requires independent formal Security review before that Host version admits a new Worker. A manual TUI or resident PID is not launch evidence.

## References

- [Current repository authority](../../AGENTS.md)
- [Roadmap](../../tools/llmtool/ROADMAP.md)
- [llmtool development workflow](../../tools/llmtool/WORKFLOW.md), for Cloud handoff or Mac/Cloud verification
- [safe-git](../safe-git/SKILL.md)
- [security-review](../security-review/SKILL.md), [qa-validation](../qa-validation/SKILL.md), [validate-runtime](../validate-runtime/SKILL.md)
