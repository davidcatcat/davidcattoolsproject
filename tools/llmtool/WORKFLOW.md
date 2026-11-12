# llmtool development workflow

> Current collaboration, role and privacy authority is in [AGENTS.md](../../AGENTS.md). This file describes the normal delivery flow. Historical Codex Cloud, S1a/S1b, A11c and B-contract text is listed in the [workflow archive](../../docs/history/llmtool-workflow-archive-20261112.md) (#1502 WP-A); it is provenance, not current instructions.

## Current Developer and Reviewer routing (Owner decisions 2026-10-21 and 2026-11-11)

Claude Developer, OpenCode, AGY and home LLM `gw-agent` are Developer routes assigned by the PM only after checking [worker-dispatch.json](worker-dispatch.json); a PAUSED switch forbids dispatch regardless of live health. Codex is Reviewer and AI Debug only and writes no code (O1, #1502); disabling Codex Developer in code is 待實作（WP-C）. Direct OpenCode work uses a registered D6 task worktree, task-local permissions and a free DeepSeek model. D4/G3/M2 is background and does not block direct Developer work. Company `gw` (home LLM) is a Developer route only through the API Agent Gateway (`gw-agent`): it edits files inside its Task scope with line-range reads; the `company-gw` direct and proposal routes are retired (Owner 2026-11-11, #120). Preserve per-task data screening, no secrets/private data, usage and bounded-repair evidence, independent review and exact-head CI.

The formal Reviewer is from the other model family than the Developer under [AGENTS.md](../../AGENTS.md) (Codex for Claude Developer and PM changes; Claude for non-Claude work such as AGY Gemini models, OpenCode and `gw-agent`); other reviews are advisory. Formal reviews remain exact-head, read-only and subject to all applicable security, CI and Mac-native gates. Owner dispatch switches are in [worker-dispatch.json](worker-dispatch.json); the local monitor at [scripts/llmtool_monitor.py](../../scripts/llmtool_monitor.py) reports live technical state separately. The PM's next-step duty after each result is the [PM Continuation Contract](../../skills/ai-team-delivery/pm-continuation.md). Home LLM unavailability is recorded and does not block unrelated Developers.

Declare read and write paths at task claude-bind, including an admitted test module in the read paths (an unchanged test is valid). Order: commit, --record-result-task-id, --bind-native-session-task-id, then task claude-bind.


[Historical role and stop decisions are archived](../../docs/history/governance-archive-20261103.md#historical-role-and-stop-decisions).

## Normal delivery flow

`llmtool task advance` is the only normal flow (#1502 §3-1). It runs one step at a time:

1. Push the Developer's committed result branch.
2. Run the Task's admitted test modules on EC2 (`task remote-test`) before the PR is opened.
3. Open the PR.
4. Wait for exact-head Linux CI: `check (Linux 3.14)`, or `check (docs)` for a documentation-only PR, must pass before review.
5. Dispatch the formal cross-family review of that exact head.
6. After PASS, `task advance` adds the `mac-ci` label; exact-head `check (macOS 3.13)` must be SUCCESS where required.
7. Merge without syncing main when there is no conflict (M1, Owner decision #1317).

A required check must be SUCCESS. A check that does not apply to the PR is recorded NOT_RUN; skipped, cancelled or pending is never PASS. Environment problems (timeout, runner or EC2 unreachable) are not test failures: `task advance` reruns an EC2 timeout once on the same commit. Returning EC2, Linux CI and Mac CI failures to the original Developer as test-failure repairs, counted apart from review failures, is 待實作（WP-D）; today `task advance` stops at `CI_FAILED` on a CI failure.

Manual commands are recovery only: use the [manual recovery flow](../../skills/ai-team-delivery/pm-continuation.md#manual-recovery-flow) when `task advance` cannot run. Review FAIL, AI Debug, split and Owner notification rules are in the [PM Continuation Contract](../../skills/ai-team-delivery/pm-continuation.md).

## Owner outcome reports and decision boundary (2026-10-19)

Read the [Owner-approved outcome and reporting contract](../../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) when planning a milestone, review handoff or release disposition. Reuse Issue #120 and existing PR evidence. The Claude PM provides progress on request, reports major events, submits a short **evidence-backed** G1/M1b, G2/Worker Live, G3/M2 closed-loop or G4/three-task Mac-pilot report when the corresponding outcome actually passes, and escalates **only** changes to product/scope, permissions/data egress, costs, accepted material risk or the final GO/LIMITED GO/NO-GO. Otherwise keep authorized development moving. Two completed task packages with no real gate progress trigger a priority correction, not extra process or an automatic STOP. Report/notification/Owner ACK are separate observed states; a GitHub comment cannot wake ChatGPT or a Mac CLI. This policy does not add a daemon, tool, product gate or llmtool F2/Host authority.

llmtool executes an approved route and plan; it does not decide which AI owns complex work. Do not add a second controller or fixed PM CLI. Required failing security, QA or Mac/runtime gates remain blocking regardless of the Owner's general merge delegation.

[Historical AI-led operating model decisions are archived](../../docs/history/governance-archive-20261103.md#ai-led-team-operating-model).

## Quality-gate model

Engineering evidence is split by purpose. Do not use one green result as a substitute
for a different required gate.

| Gate / skill | Question it answers |
| --- | --- |
| `review-change` | Does this diff correctly implement the requirement without obvious regressions or scope drift? |
| `bug-hunt` | Can we reproduce any observable defect in the current implementation? |
| `security-review` | What assets/trust boundaries/attack paths exist, and are there exploitable weaknesses or design gaps? |
| `qa-validation` | Does the product behave correctly through its public/user-facing interfaces across the defined matrix? |
| `validate-runtime` | Does the exact runtime/environment/provider/VPN/auth/inference layer actually work? |
| `safe-git` | Is the exact reviewed/tested revision safely published/merged with current CI evidence? |

Not every change needs every gate. The pinned Task Contract/milestone declares which
are required. A confirmed finding is fixed via normal implementation with a permanent
regression test where appropriate, then the affected gate is re-run.

## References

- [Repository guidance](../../AGENTS.md)
- [PM Continuation Contract](../../skills/ai-team-delivery/pm-continuation.md)
- [Roadmap](ROADMAP.md)
- [Workflow archive](../../docs/history/llmtool-workflow-archive-20261112.md)
