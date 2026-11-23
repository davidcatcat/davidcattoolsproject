# llmtool development workflow

> Current collaboration, role and privacy authority is in [AGENTS.md](../../AGENTS.md). This file describes the normal delivery flow. Historical Codex Cloud, S1a/S1b, A11c and B-contract text is listed in the [workflow archive](../../docs/history/llmtool-workflow-archive-20261112.md) (#1502 WP-A); it is provenance, not current instructions.

## Current Developer and Reviewer routing (Owner decisions 2026-10-21 and 2026-11-11)

Claude Developer, OpenCode, AGY and home LLM `gw-agent` are Developer routes assigned by the PM only after checking [worker-dispatch.json](worker-dispatch.json); a PAUSED switch forbids dispatch regardless of live health. Codex is Reviewer and AI Debug only and writes no code (O1, #1502); `codex-developer` is `PAUSED` and review repair refuses the Codex route (WP-C). Direct OpenCode work uses a registered D6 task worktree, task-local permissions and a free DeepSeek model. D4/G3/M2 is background and does not block direct Developer work. Company `gw` (home LLM) is a Developer route only through the API Agent Gateway (`gw-agent`): it edits files inside its Task scope with line-range reads; the `company-gw` direct and proposal routes are retired (Owner 2026-11-11, #120). Preserve per-task data screening, no secrets/private data, usage and bounded-repair evidence, independent review and exact-head CI.

The formal Reviewer is from the other model family than the Developer under [AGENTS.md](../../AGENTS.md) (Codex for Claude Developer and PM changes; Claude for non-Claude work such as AGY Gemini models, OpenCode and `gw-agent`); other reviews are advisory. Formal reviews remain exact-head, read-only and subject to all applicable security, CI and Mac-native gates. Owner dispatch switches are in [worker-dispatch.json](worker-dispatch.json); the local monitor at [scripts/llmtool_monitor.py](../../scripts/llmtool_monitor.py) reports live technical state separately. The PM's next-step duty after each result is the [PM Continuation Contract](../../.claude/skills/ai-team-delivery/pm-continuation.md). Home LLM unavailability is recorded and does not block unrelated Developers.

Declare read and write paths at task claude-bind, including an admitted test module in the read paths (an unchanged test is valid). Order: commit, --record-result-task-id, --bind-native-session-task-id, then task claude-bind.


[Historical role and stop decisions are archived](../../docs/history/governance-archive-20261103.md#historical-role-and-stop-decisions).

## Normal delivery flow

`llmtool task advance` is the only normal flow (#1502 §3-1). It runs one step at a time:

1. Push the Developer's committed result branch.
2. Open or reuse the branch's PR and record its number and exact HEAD on the Task.
3. On the next advance, run the Task's admitted test modules on EC2 (`task remote-test`) using `refs/pull/<N>/head`. If EC2 fails, repair and retest the updated HEAD on the same PR.
4. Wait for exact-head Linux CI: `check (Linux 3.14)`, or `check (docs)` for a documentation-only PR, must pass before review.
5. Dispatch the formal cross-family review of that exact head.
6. After PASS, `task advance` adds the `mac-ci` label; exact-head `check (macOS 3.13)` must be SUCCESS where required.
7. Merge without syncing main when there is no conflict (M1, Owner decision #1317).

Check-status rules: required exact-head checks must be SUCCESS, non-applicable checks are NOT_RUN and skipped/cancelled/pending is never PASS — see [AGENTS.md, Merge gates](../../AGENTS.md#merge-gates-owner-decision-425-issuecomment-6434005860). Environment problems (an EC2 timeout, an unreachable runner or EC2, a cancelled or startup-failed CI check) are not test failures: `task advance` reruns an EC2 timeout once on the same commit. A required CI check that ends `timed_out` is a test failure and counts like `failure` (#2094, PR #2108). An EC2, Linux CI or Mac CI failure goes back to the original Developer as a test-failure repair, on a count kept apart from review failures (#1502 WP-D D11; chain-wide caps #2026, #2048); an environment conclusion waits uncounted, and a used-up count stops with `TEST_FAIL_LIMIT_REACHED`.

Manual commands are recovery only: use the [manual recovery flow](../../.claude/skills/ai-team-delivery/pm-continuation.md#manual-recovery-flow) when `task advance` cannot run. Review FAIL, AI Debug, split and Owner notification rules are in the [PM Continuation Contract](../../.claude/skills/ai-team-delivery/pm-continuation.md).

The first three effective formal-review repairs can proceed without High Debug.
If the third repair receives another formal FAIL, the fourth repair requires
an applicable confirmed RCA. The fifth repair has the same requirement.
A work lineage, including ROOT, split children, REPLAN and continuations,
shares three effective High Debug runs. A confirmed RCA can cover the same
blocking findings on a continuation when the previous Task and the current
formal review provide matching evidence. An unconfirmed diagnosis or changed
findings require fresh evidence. Uncertain launches count toward the budget.

### Work lineage drop and replace

Without `--final`, `llmtool task drop` is refused with
`LINEAGE_SUCCESSOR_REQUIRED` when it would make the ROOT FAILED while a legal
next step still exists (child slots left, or the lineage REPLAN unused); the
refusal performs no write. With `--final`, the drop is the explicit final
abandonment of the lineage. `--replaces` lists the child Task IDs the new
split child replaces (split) or the child Task IDs the REPLAN replaces
(replan, which must include the child being replaced), and a replaced child
is covered by the `replaces` of a CLOSED member. See
[#2373](https://github.com/davidcatcat/davidcattoolsproject/issues/2373) for the agreed change.

An interrupted AGY or OpenCode dispatch can continue on the same Task and D6.
Run the original `task delegate` command as the owning PM with its original scope.
The command automatically selects the other route when the old assignment is
still pending, the persisted launch boundary proves it never authorized spawn,
or the launcher recorded a verified stop before delivering any prompt. The
replacement still needs current Owner authorization, route and quota admission.
For an already counted repair, `task advance` uses this same delegate path.
The handoff keeps the branch, PR and repair claim, and gives the replacement a
new assignment and D6 session binding before launching it. A pending handoff
can be resumed with the same command.

`DIRECT_CHILD_LAUNCH_EVIDENCE_MISSING` identifies a historical dispatch with
no durable launch observation. `DIRECT_CHILD_SPAWN_OUTCOME_UNVERIFIED` means
spawn was authorized and its outcome still needs proof. For these records, keep
the original worktree and claim, and use the ordinary result callback if an
authentic result can be recovered. A missing PID, a clean tree, or a dead PID
alone cannot authorize another writer.

A review repair claim whose dispatch outcome was not recorded stays `UNKNOWN`, and llmtool does not treat it as dispatched.
It settles only through `llmtool task repair TASK --executor-task D6 --pr N --recover-claim REPAIR_TASK_ID` (#1928).
That sets the claim `DISPATCHED` in one locked TaskStore update, only after evidence checks E1-E5 pass: the claim, the repair Task's identity, its dispatch audit, its VERIFIED execution session, and the registered D6 and HEAD.
It dispatches nothing and changes no repair count; a failed check leaves the claim `UNKNOWN`.
Never edit the TaskStore by hand or write a receipt to settle a claim.

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

## Main CI failure (#1706)

See [#1706](https://github.com/davidcatcat/davidcattoolsproject/issues/1706). `llmtool ci main-incident scan [--pm-session]` reads main commits and CI runs from GitHub with reads only (it writes nothing to GitHub), classifies the failure (ENV, FLAKY, CODE or UNKNOWN) and records or updates the incident in a local store; the PM session that runs the first scan owns the incident.

`llmtool ci main-incident show --incident ID` prints one incident record as JSON. `llmtool ci main-incident confirm --incident ID --scope <comma-separated PR numbers> --reason <text>` records the owning PM's revert-scope confirmation, bound to the newest observation, and never reverts anything.

No revert command exists yet (#1706 R4-R7 are not built), so a confirmed incident is advice only. Handle a red main with the [debug-ci skill](../../.claude/skills/debug-ci/SKILL.md).

## References

- [Repository guidance](../../AGENTS.md)
- [PM Continuation Contract](../../.claude/skills/ai-team-delivery/pm-continuation.md)
- [Roadmap](ROADMAP.md)
- [Workflow archive](../../docs/history/llmtool-workflow-archive-20261112.md)
