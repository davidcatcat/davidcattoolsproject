---
name: ai-team-delivery
description: Use when the PM plans and coordinates product work, dispatches Developers, manages review and QA handoffs, or oversees safe PR publication and merges.
---

# AI team delivery

Use this workflow for product work, review, QA handoff and Git publication. [AGENTS.md](../../AGENTS.md) owns current authority, roles, privacy boundaries and Owner escalation. This skill defines the work-package path; it does not add runtime capabilities or override specialist gates.

## Roles and task contract

The Integrator/PM converts the accepted goal into a bounded task and owns routing, dependencies, acceptance interpretation, evidence reconciliation and integration. After every PASS, FAIL or BLOCKED it follows the [PM Continuation Contract](pm-continuation.md). The assigned Developer is the sole writer in its registered task worktree. The independent Reviewer examines the exact candidate and evidence; the Reviewer does not edit or approve its own work. A Git publisher executes only the exact authorized publication operations.

Before dispatch, record:
- base SHA and repository/worktree identity;
- intended behavior, writable files and prohibited actions;
- assigned Developer, model and provider route;
- deterministic checks, independent review and exact-head CI required;
- provider output cap, native retry stop, timeout and cleanup requirements;
- Mac/runtime evidence required, if any.

For D8 Host/Worker admission and review controls, see the [D8 Host/Worker reference](pm-continuation-reference.md#d8-hostworker-admission-and-review).

**Developer routing (Owner direction, 2026-10-21; O1):** Codex is Reviewer and AI Debug only, never a Developer (O1; `codex-developer` is `PAUSED` in code, WP-C). PM-selected Developer routes are OpenCode, Claude Developer, AGY, and Home LLM `gw-agent` under [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json). Work in a verified registered D6 worktree with the Task Contract's file scope, bounded repairs and required self-review. D8 provider caps, retry stops, timeout and cleanup apply to llmtool Host/Worker jobs. For current dispatch, route-specific and Reviewer rules, follow [AGENTS.md](../../AGENTS.md).

**AGY direct Developer:** Follow the [AGY direct Developer skill](../agy-direct-developer/SKILL.md) and the current route authorization in [AGENTS.md](../../AGENTS.md).

## Team and execution roster

| Route | Role | Who triggers it | Limits |
| --- | --- | --- | --- |
| Codex Developer | Retired from Developer work (O1); Codex is Reviewer and AI Debug only (`codex-developer` is `PAUSED`, WP-C) | None | No new Developer work |
| Codex AI Debug | Read-only diagnostic task after two failed reviews/repairs | PM, under High Debug policy | Read-only in D6; no write authority; Codex high-tier model |
| OpenCode Developer | Implements and self-reviews the bounded Task | PM, through the current worker-dispatch gate | Registered D6, task-local permissions and currently free approved model; no paid fallback; required independent review and CI |
| Claude Developer | Implements and self-reviews the bounded Task | PM, through the current worker-dispatch gate | Registered D6, task scope, independent review and CI |
| AGY Developer | Implements and self-reviews the bounded Task | PM, through the current worker-dispatch gate (`ALLOWED` since 2026-10-31) | Verified D6; auth, actual model, session, quota and scoped permissions; no Host D4/G3/M2, automatic retry or paid fallback; formal review from another model family |
| Home LLM `gw-agent` Developer | Reads, edits and adds tests inside its Task scope through the API Agent Gateway; no shell or Git | PM, through llmtool (`gw-agent` only; the `company-gw` direct and proposal routes are retired, #120) | Line-range reads; write paths limited to the Task; independent review and CI |
| Claude Reviewer | Formal independent Reviewer paired by actual model family (reviews non-Claude work: AGY, OpenCode, gw-agent, etc.) | PM, after a pinned candidate is ready | Separate read-only context and D6 reviewer record; exact-head review; never reviews own change or same model family |
| Codex Reviewer (`codex-reviewer`) | Formal independent Reviewer paired by actual model family (reviews non-Codex work: Claude Developer/PM changes, gw-agent, etc.; may review AGY and OpenCode if Claude is unavailable) | PM, after a pinned candidate is ready | Separate read-only context and D6 reviewer record; exact-head review; never reviews own change or same model family; must be qualified and cross-family |
| EC2 remote-test runner | Runs focused remote test modules and returns a receipt via `llmtool task remote-test` | PM dispatches the task for selected focused modules | PM pre-review tool on private infrastructure, not CI; focused modules only; receipt is required evidence and does not replace formal review or CI |
| Mac CI (GitHub-hosted) | Runs Mac-relevant CI checks | PR workflow for PRs; PM adds `mac-ci` after formal PASS; main FULL only from `llmtool ci full-mac-due` (every PM sweep, 30-minute quiet period) or a finished merge train (#1485) | GitHub-hosted `macos-15` for PRs (`mac-ci-intel` label for Intel), `macos-15-intel` for main FULL; 120-minute job limit set in the workflow ([#1253](https://github.com/davidcatcat/davidcattoolsproject/issues/1253)); exact-head PR check; without `mac-ci`, the PR check is `NOT_RUN` |
| Linux CI (GitHub-hosted) | Runs the full Linux suite sharded across parallel jobs | Automatically on each PR and main push | GitHub-hosted `ubuntu-24.04`; exact-head `check (Linux 3.14)`; Linux 3.9 is manual pre-release only |

The EC2 remote-test runner is a PM pre-review tool on private infrastructure, not CI. The PM invokes it with `llmtool task remote-test` for selected focused modules; its receipt is required evidence and does not replace formal review or CI.

## Delivery flow and formal review

Developer done -> task advance: push branch -> EC2 test before the PR -> open PR -> Linux CI -> review -> PASS -> mac-ci label -> Mac CI -> merge without syncing main (M1, #1317). task advance is the only normal flow; manual commands are recovery only.

On test or CI failure, returning the failure to the Developer and tracking failure counts per task is 待實作（WP-D）.

The Developer implements the bounded packet, performs a distinct-context self-review and fixes ordinary findings before formal review. An OpenCode Developer must also complete a qualified, actually-free pre-review from a different model family before handoff. If none is available, record the limitation and mark the OpenCode Task BLOCKED. This pre-review is not independent acceptance or the formal Reviewer gate. For other current dispatch, route-specific and formal Reviewer rules, follow [AGENTS.md](../../AGENTS.md).

Submit one consolidated exact-SHA candidate to a qualified, authorized formal Reviewer from a different model family, following [AGENTS.md](../../AGENTS.md), with contract, diff, test results, CI provenance and applicable runtime evidence. Use a separate read-only Reviewer Session/context with its own D6 reviewer record, distinct from the Developer and the PM; nobody formally reviews their own change. Any other review is advisory only. The Reviewer reports severity, reproducibility, limitations and exact-SHA disposition on the PR or tracking Issue under the [Transition record](pm-continuation.md#transition-record) rule; an llmtool Task closed by `task complete --reviewer-task` uses the [formal verdict comment](formal-verdict.md). On FAIL, return bounded findings to the original Developer, rerun affected checks and request affected rereview ([continuation](pm-continuation.md)). Never merge on self-review, stale verdict or green CI alone.

**Owner acceptance is a separate gate (#1208).** Owner product acceptance and formal cross-family review are two independent gates. When a Task needs Owner acceptance, its PR needs both, plus exact-head CI. A relayed Owner acceptance never replaces formal review or CI, and a formal PASS never replaces Owner acceptance. #888 is the case that must not repeat.

**Company LLM Developer route:** Only through the API Agent Gateway (`gw-agent`); the `company-gw` direct and proposal routes are retired (Owner 2026-11-11, #120). Follow the authority, eligible task scope, privacy, execution and acceptance requirements in [AGENTS.md](../../AGENTS.md). A `gw` assignment is not evidence of live dispatch, patch application, full formal review or Task closure. Do not claim quality or net savings from one trial.

<a id="p0--sole-owner-escalation-rule-owner-decision-2026-10-10"></a>
### P0 owner escalation pointer (current in AGENTS)

Current Owner escalation is defined in [AGENTS.md](../../AGENTS.md); see the [normative defect triage reference](defect-triage.md) for issue disposition.

## Test data rule (Owner #763 A, 2026-11-08)

[Owner decision, relayed](https://github.com/davidcatcat/davidcattoolsproject/issues/763#issuecomment-6717848625). The rule binds every Developer route: Claude Developer, AGY, OpenCode, Codex (including repair runs) and home LLM `gw-agent`. It also binds every Developer spec the PM writes. llmtool adds the same rule text to each route's prompt.

1. Ordinary tests never contain password-, API-key-, token- or other credential-shaped strings.
2. A test of a refusal flow uses mock, monkeypatch or dependency injection of the screen function, and still checks the real behaviour.
3. Only tests of the sensitive detector itself may use the #763/#981 synthetic-data exception: labelled synthetic, independently reviewed, never real credentials. See [Sensitive-screen refusals](pm-continuation.md#sensitive-screen-refusals-owner-2026-11-05-981).
4. Never disable, bypass or weaken a production check for test convenience.

For what to do when the screen refuses a Task, see [Sensitive-screen refusals](pm-continuation.md#sensitive-screen-refusals-owner-2026-11-05-981).

## Defect triage and repair bounds

Fix reproducible user-impacting defects under the [normative Issue #92 triage policy](defect-triage.md), in priority order: safety/data loss, blocked core workflows, degraded user-visible behavior, then maintainability. Preserve the root cause and reproducer. Use the smallest repair that addresses it; never weaken tests to clear a finding. A blocking gate stops the affected task, not unrelated authorized work.

## Mac and live evidence

Before runtime acceptance, establish the tested checkout, exact HEAD, installed executable path/version and required feature ancestry. Run Mac-only checks only on the actual Mac. Record commands, exits, expected markers, usage/receipt/ACK and cleanup as applicable. A resident TUI PID, model assertion, fake fixture or GitHub event alone does not prove a Host-owned Worker launch. Keep source completion, CI, formal review and live acceptance separate.

Evidence must not contain credentials or restricted logs. Use redacted identifiers and exact source hashes. Apply [Owner outcome reporting v1.1](../../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) for G1–G4 milestone checkpoints. GitHub publication is durable evidence, not proof of Session wake or notification delivery; see the [dispatch receipt rule](pm-continuation.md#transition-record).

## Interim controller changes

For Mac operator handoff and frozen routes, see [Mac operator handoff and frozen paths](controller-release.md).

## Efficient handoffs

Prefer existing evidence and rerun only checks invalidated by the candidate. Bundle related fixes into one useful handoff; avoid PM micro-approvals and repeated full-context reports. Record usage and overhead when observable; otherwise use UNKNOWN.

<a id="work-package-delivery-and-in-session-reviewer-subagents-owner-decision-2026-10-14"></a>
### Work-package delivery and reviewer subagents

A Developer may use an in-session read-only Reviewer child only when the CLI supports it and effective permissions are verified. It is pre-review, not independent acceptance. Keep one writer per worktree and give the Reviewer only the pinned diff, tests and contract.

<a id="user-impact-first-defect-triage-and-bounded-delivery-owner-decision-issue-92"></a>
### User-impact-first triage

Use the [normative Issue #92 policy](defect-triage.md); preserve this legacy anchor for existing links.

<a id="2026-10-19-owner-direction--developer-owned-pre-review-economical-handoffs-and-s3-stop"></a>
### Historical D7 pre-review/S3 note

The former one-round S3 stop and paused-role assignments are historical. Current Developer routing is defined above and in the [D8/D8a decision](../../docs/decisions/llmtool_opencode_secondary_developer_20261019.md); D4 Host work remains separate background G3/M2 work.

### Review requests and Owner updates

For a formal review, publish `CLAUDE-REVIEW-REQUEST: PR #N HEAD <SHA>` (Claude Reviewer) or `CODEX-REVIEW-REQUEST: PR #N HEAD <SHA>` (Codex Reviewer) with the exact source, scope, review type and questions. This is a handoff record, not evidence that an automated reviewer ran. End each Owner-facing update with exactly one practical line: `你需要做：無` or `你需要做：<一個必要的動作或白話 yes/no 問題>`. Put technical failures and AI-owned next steps in the task/PR record; do not ask the Owner to diagnose them or relay routine prompts.

### D8 task admission and Host review

Use the task admission fields above. An OpenCode-authored Host/containment change requires independent formal Security review before that Host version admits a new Worker. A manual TUI or resident PID is not launch evidence.

## Done when

A work package is ready for handoff when its scoped changes, required checks and Developer self-review are recorded, and any remaining independent review, CI, runtime or publication gates are identified with their evidence.

## References

- [Current repository authority](../../AGENTS.md)
- [PM Continuation Contract](pm-continuation.md)
- [Formal verdict comment](formal-verdict.md)
- [Roadmap](../../tools/llmtool/ROADMAP.md)
- [safe-git](../safe-git/SKILL.md)
- [security-review](../security-review/SKILL.md), [qa-validation](../qa-validation/SKILL.md), [validate-runtime](../validate-runtime/SKILL.md)
