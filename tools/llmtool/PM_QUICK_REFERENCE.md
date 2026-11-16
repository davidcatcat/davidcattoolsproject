# PM Operator Quick Reference

An index for the Claude PM ([#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502) WP-H). Each item has one sentence and a link to the source that owns the rule. This page sets no rules: when it differs from a linked source, the source wins, and [CLAUDE.md](../../CLAUDE.md) wins over every skill.

On takeover, read the sources in the order set by [PM takeover](../../.claude/skills/ai-team-delivery/pm-continuation.md#pm-takeover).

## Dispatch

- **Authority:** reread and validate the Worker's switch through the dispatch gate before every dispatch, retry, repair or hand-off ([Worker dispatch](../../CLAUDE.md#worker-dispatch), [worker-dispatch.json](worker-dispatch.json)).
- **Route and Reviewer pairing:** choose an allowed Developer route and plan its cross-family formal Reviewer before dispatch ([Review](../../CLAUDE.md#review), [routing](WORKFLOW.md#current-developer-and-reviewer-routing-owner-decisions-2026-10-21-and-2026-11-11)).
- **Task spec:** one Task ID per work package, screened Task text, and every write path also listed as a read path ([Dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Spec preflight:** run [spec_preflight.py](scripts/spec_preflight.py) on the spec before `record-pm` ([Dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Bind order:** declare read and write paths at `task claude-bind`, after the record and bind steps listed in [routing](WORKFLOW.md#current-developer-and-reviewer-routing-owner-decisions-2026-10-21-and-2026-11-11).
- **Fresh install:** fast-forward the canonical checkout to `main` before dispatch ([Dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Test data rule:** every Developer spec carries the four-point rule ([test data rule](../../.claude/skills/ai-team-delivery/SKILL.md#test-data-rule-owner-763-a-2026-11-08)).
- **Background run:** start dispatch in the background and poll, never under `timeout` ([Dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Start receipt:** a dispatch counts as delivered only with an observed start receipt ([Transition record](../../.claude/skills/ai-team-delivery/pm-continuation.md#transition-record), [claude_bg_dispatch.py](scripts/claude_bg_dispatch.py)).
- **Sweep:** every scheduled sweep continues in-flight work first, then reconciles the backlog, then dispatches new Tasks ([Scheduled sweep](../../.claude/skills/ai-team-delivery/pm-continuation.md#scheduled-sweep)).
- **Sub-agent PMs:** a Mode B sub-agent PM needs a `subpm-lease` claim on paths that do not overlap other Tasks ([Sub-agent PMs](../../.claude/skills/ai-team-delivery/pm-continuation.md#sub-agent-pms-owner-d8-2026-11-02)).
- **Work packages:** plan related items as one package, and keep one Task scope and one D6 per PR ([Large work packages](../../.claude/skills/ai-team-delivery/pm-continuation.md#large-work-packages-owner-2026-11-07)).

## Normal path

- **One flow:** `llmtool task advance` runs the delivery steps one at a time; manual commands are recovery only ([Normal delivery flow](WORKFLOW.md#normal-delivery-flow)).
- **EC2 before PR:** a Developer result is not ready for review until it passes on EC2 ([Validate before review](../../.claude/skills/ai-team-delivery/pm-continuation.md#validate-a-developer-result-before-review-owner-2026-11-02)).
- **Required checks:** only `SUCCESS` passes; a check that does not apply is `NOT_RUN`, never PASS ([Merge gates](../../CLAUDE.md#merge-gates-and-process-tiers)).
- **Formal review:** request the cross-family review only for the exact head that passed CI ([Review](../../CLAUDE.md#review)).
- **Verdict record:** each verdict is a new PR comment with the marker and the `Verdict: … | HEAD:` line ([Transition record](../../.claude/skills/ai-team-delivery/pm-continuation.md#transition-record)).
- **Next decision:** record a next decision after every PASS, FAIL or BLOCKED before turning to other work ([Every result gets a next decision](../../.claude/skills/ai-team-delivery/pm-continuation.md#every-result-gets-a-next-decision)).
- **Merge:** code PRs merge only through `task advance --merge` or a merge train ([Transition record](../../.claude/skills/ai-team-delivery/pm-continuation.md#transition-record)).
- **Close-out:** stop finished sessions and retire eligible D6 worktrees after the merge ([Session close-out](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#session-close-out)).
- **Main FULL Mac:** every sweep runs `llmtool ci full-mac-due` and records its result code ([Scheduled sweep](../../.claude/skills/ai-team-delivery/pm-continuation.md#scheduled-sweep)).

## Failure recovery

- **`task advance` cannot run:** follow the manual recovery steps in order ([Manual recovery flow](../../.claude/skills/ai-team-delivery/pm-continuation.md#manual-recovery-flow)).
- **Reviewer FAIL:** send every finding to the original Developer through `llmtool task repair` ([Every result gets a next decision](../../.claude/skills/ai-team-delivery/pm-continuation.md#every-result-gets-a-next-decision)).
- **Repair claim `UNKNOWN`:** settle it only with `task repair --recover-claim`, never by editing the TaskStore ([Normal delivery flow](WORKFLOW.md#normal-delivery-flow)).
- **EC2 or CI failure:** a test failure goes back to the Developer on its own count, and an environment conclusion waits uncounted ([Normal delivery flow](WORKFLOW.md#normal-delivery-flow)).
- **BLOCKED:** confirm the cause with evidence, classify it, then retry, reassign or isolate ([BLOCKED](../../.claude/skills/ai-team-delivery/pm-continuation.md#blocked-diagnose-then-retry-reassign-or-isolate)).
- **Retry or reassignment:** retry only transient causes, and reassign only after the D6 writer is released ([Retry and reassignment](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#retry-and-reassignment)).
- **High Debug:** after the first failed correction in a finding family, run a recorded `llmtool task high-debug` before the second repair ([High Debug](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#high-debug), [AI Debug](../../CLAUDE.md#debugging-and-evidence)).
- **Quota:** a quota FAIL blocks that Worker until it expires or a relayed Owner recovery is recorded ([Quota and Reviewer continuation](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#quota-and-reviewer-continuation)).
- **Sensitive-screen refusal:** stop, record metadata only, and never reword or retry ([Sensitive-screen refusals](../../.claude/skills/ai-team-delivery/pm-continuation.md#sensitive-screen-refusals-owner-2026-11-05-981)).
- **No-change result:** without a stated refusal or a screen hit, it is not a safety refusal ([Confirmed refusals](../../.claude/skills/ai-team-delivery/pm-continuation.md#confirmed-refusals-owner-o5-1943)).
- **Owner ask or notice:** escalate only reserved decisions, and post the monitor marker before asking ([Escalate only when continuing is not allowed](../../.claude/skills/ai-team-delivery/pm-continuation.md#escalate-only-when-continuing-is-not-allowed)).
- **Missed step:** open an Issue, fix the gap and change the rule, skill or tool ([Closing process gaps](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#closing-process-gaps)).

## Limits

Values live only in the linked source; this page does not copy them.

| Limit | Single source |
| --- | --- |
| Dispatch switch per Worker | [worker-dispatch.json](worker-dispatch.json) |
| Task text size and acceptance count | [spec_preflight.py](scripts/spec_preflight.py); [Dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene) |
| Review repair cap and split | [Repair cap](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#repair-cap) |
| Test-failure repair count | [Normal delivery flow](WORKFLOW.md#normal-delivery-flow) |
| Quota FAIL freshness | [Quota and Reviewer continuation](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#quota-and-reviewer-continuation) |
| New Tasks per sweep, concurrency cap, sweep interval | [Scheduled sweep](../../.claude/skills/ai-team-delivery/pm-continuation.md#scheduled-sweep) |
| Start-receipt confirmation window | [Transition record](../../.claude/skills/ai-team-delivery/pm-continuation.md#transition-record) |
| High Debug screen record and batch size | [Sensitive-screen refusals](../../.claude/skills/ai-team-delivery/pm-continuation.md#sensitive-screen-refusals-owner-2026-11-05-981) |
| Main FULL Mac trigger | [Merge gates](../../CLAUDE.md#merge-gates-and-process-tiers) |

## Known cases

- **WP-5:** UNKNOWN; the case record is not in this Task's read paths, so the PM supplies the prevention line and link.
- **[#2194](https://github.com/davidcatcat/davidcattoolsproject/issues/2194):** UNKNOWN; the case record is not in this Task's read paths, so the PM supplies the prevention line and source link.
- **[#2202](https://github.com/davidcatcat/davidcattoolsproject/issues/2202):** UNKNOWN; the case record is not in this Task's read paths, so the PM supplies the prevention line and source link.
- **Dispatch after a failed preflight:** dispatch only after [spec_preflight.py](scripts/spec_preflight.py) passes on the exact spec you will record ([Dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Wrong High Debug arguments:** check `llmtool task high-debug --help` and the recorded Task, family and failed-correction count before you run it ([High Debug](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#high-debug)).
- **Status from intent:** report a state only from an observed receipt, record or check, never from the command you sent ([Transition record](../../.claude/skills/ai-team-delivery/pm-continuation.md#transition-record), [evidence](../../CLAUDE.md#debugging-and-evidence)).
