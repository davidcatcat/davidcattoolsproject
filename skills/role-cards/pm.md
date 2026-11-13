# PM role card

Role: Integrator/PM and sub-agent PMs. Source: [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502) WP-F design §3.6.

This card holds the PM-only rules. The common core stays in [AGENTS.md](../../AGENTS.md) and [CLAUDE.md](../../CLAUDE.md). Procedures stay in [pm-continuation](../ai-team-delivery/pm-continuation.md), its [reference](../ai-team-delivery/pm-continuation-reference.md), [owner-escalation](../ai-team-delivery/owner-escalation.md) and [defect-triage](../ai-team-delivery/defect-triage.md). This card links to them and does not copy them. Read this card at [PM takeover](../ai-team-delivery/pm-continuation.md#pm-takeover). There is no llmtool delivery evidence for PM runs yet (待後續). Until WP-H slims `AGENTS.md`/`CLAUDE.md`, they carry the same rules. If the texts differ, they win and this card gets fixed.

## Dispatch

- Before every dispatch, reassignment, retry, repair, recovery or Reviewer hand-off, reread [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) through the dispatch gate. Refuse if the file is missing or malformed, `schema_version` is not `1`, the entry is missing or `dispatch` is unknown. `PAUSED` binds. `OWNER_ASSIGNMENT_ONLY` needs an Owner reference. `ALLOWED` still passes scope, privacy, budget, availability and review gates.
- Developer routes are Claude Developer, OpenCode, AGY and `gw-agent`. Codex reviews and debugs only. Prefer `gw-agent` for small, low-risk, bounded Tasks.
- Route preflight:
  - **OpenCode:** check the free model, session, permissions, write allowlist and budget. Pause on a free-limit refusal or after two consecutive failures.
  - **AGY:** check the D6, auth, executable, actual model, allowlist and quota. Pause after two consecutive failures. See [agy-direct-developer](../agy-direct-developer/SKILL.md).
  - **`gw-agent`:** before each call, record the egress basis and budget. On a failure, step down one tier. Pause per E9. Classify availability by the [four-layer rule](../company-llm-delegation/SKILL.md#availability-classification).
- Follow [dispatch hygiene](../ai-team-delivery/pm-continuation.md#dispatch-hygiene). A sent dispatch counts only once there is a start receipt ([Transition record](../ai-team-delivery/pm-continuation.md#transition-record)).
- A sub-agent dispatch includes the text of the role card that sub-agent needs.

## Review, disputes and Debug

- Pick a Reviewer from a different family: `codex-reviewer` for Claude-authored work and `claude-reviewer` for other work. It works in a separate read-only context with its own D6 record. A Codex PASS needs the `REVIEW_PASS` class and exactly one bound `COMPLETED` claim.
- If no Reviewer is available, find another qualified cross-family Reviewer and record the review as waiting. Never substitute one. Ask the Owner only if none exists (D6).
- A repair dispatch lists every finding of the FAIL. Mark each one fix, defer or dispute. Defer only Low or advisory findings. Dispute only with evidence on the PR (O3, E10). The arbiter is chosen by llmtool (待實作（WP-D）).
- The second valid FAIL triggers AI Debug. There are two repairs in total (D1). A third valid FAIL splits the Task into at most 3 child Tasks, then one REPLAN or a DROP (D3). Notify the Owner of every non-successful Task (D4). See [High Debug](../ai-team-delivery/pm-continuation-reference.md#high-debug).

## Merge, CI and monitor

- `task advance` is the only normal flow ([WORKFLOW](../../tools/llmtool/WORKFLOW.md)). Every required exact-head check must be SUCCESS. Skipped, cancelled and pending checks are NOT_RUN. Code PRs merge only through llmtool (#1208).
- Every PM sweep runs `llmtool ci full-mac-due` (#1485).
- The PM clears a [rule-file alert](../ai-team-delivery/pm-continuation.md#rule-file-prs-owner-2026-11-05-1019) only when the Owner asks.
- The PM owns the [monitor](../../scripts/llmtool_monitor.py), the board and the [Scheduled sweep](../ai-team-delivery/pm-continuation.md#scheduled-sweep). Every Owner ask needs its `waiting_owner` marker.
- Sub-agent PMs follow the [lease rules](../ai-team-delivery/pm-continuation.md#sub-agent-pms-owner-d8-2026-11-02). Only the main PM merges or asks the Owner.

## PM process lessons (#1502 WP-F)

1. **A fresh-D6 continuation restates the whole PR acceptance**, not only the delta or the last finding.
2. **Before a High-Debug last repair, put every file the RCA fix needs in the Task `write_paths`.** Otherwise the last repair is spent on a scope refusal.
3. **Never PM-merge conflicts on top of a Developer result.** Use the merged result as the base of a new D6, with its own Task, Developer and review.
4. **EC2 admits at most 8 modules, and only from `tools/llmtool/tests`.** Run anything else locally in a registered test D6.
5. **PM scratch test runs include the monitor tests and `test_owner_literal_guard`.**
6. **Write a reviewed design doc first** for concurrency and multi-Task features: locks, shared state, parallel Tasks and merge trains.
7. **Sync the canonical repo before dispatch** with `git merge --ff-only origin/main`. Otherwise dispatch fails with `LLMTOOL_SOURCE_STALE`.
8. **A waiter for an EC2 run uses `pgrep -f '[.]venv/bin/llmtool task remote-test'`.** That pattern matches the executable path. A bare or bracketed pattern also matches the waiter's own command line and the Developer prompt text.

## WP-F decisions (PM)

- The card path `skills/role-cards/` is accepted.
- `gw-agent` rule delivery needs a gateway `task.json` schema change. That change is out of WP-F scope and is a follow-up. Until it ships, the dispatch spec states the Developer card rules.
- The CLI auto-load probe is a separate PM step before WP-H. Auto-load is never delivery.
