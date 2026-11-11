---
name: agy-direct-developer
description: Use when an llmtool Developer Task is routed to AGY (Antigravity CLI `agy`) for direct execution in a registered D6 worktree. Covers dispatch authorization, model and quota preflight, scope checks and hand-back evidence.
---

# AGY direct Developer

AGY is a PM-selected llmtool direct Developer route: `llmtool task delegate --provider agy --mode direct`. Its authorization comes from the current AGY row in [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) and [AGENTS.md](../../AGENTS.md); the row has been `ALLOWED` since 2026-10-31. AGY follows the same verified D6 worktree and authentication, actual-model, session and permission checks as OpenCode, and does not require Host D4/G3/M2. Each Task still needs independent formal review from another model family. This skill describes the AGY-specific procedure for one Task.

## Part 1: PM — dispatch one Task

1. **Gate.** Before dispatch, reread and validate the authoritative policy. From the canonical checkout:
   `tools/llmtool/.venv/bin/python -B -m llmtool dispatch-check agy --task <task id>`.
   Proceed only when the gate allows dispatch. Keep the result with the Task evidence.
2. **Preflight.** `agy models` lists the models and doubles as an authentication check. Check the available model and session behavior before dispatch. Check weekly and five-hour quota remaining. Never print, copy or edit AGY credentials or config. Preflight does not authorize an agent turn.
3. **D6.** Register the Task's D6 from the approved base with the Integrator-controlled procedure in [worktree lifecycle](../safe-git/worktree-lifecycle.md). Verify the registered root, branch, HEAD and clean-tree state before dispatch.
4. **Dispatch.** Dispatch the Task with `llmtool task delegate --provider agy --mode direct --owner-ref https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6683494255` and the exact read and write path allowlists. This `--owner-ref` binds the Owner's `ALLOWED` policy decision (ALLOWED date 2026-11-04); it does not make AGY an individually assigned route. The direct route may use the registered D6 without waiting for Host D4/G3/M2, under Owner decision #289's Codex-direct parity. Verify the actual AGY CLI, model, session semantics, permissions and remaining quota before execution. Keep credentials, secrets, personal/customer data and restricted logs out of prompts and evidence. The CLI's `--sandbox` flag and before/after snapshots do not replace the route's permission checks or post-run changed-path scope check.
5. **Repair.** Repair bounds and quota handling follow the [PM Continuation Contract](../ai-team-delivery/pm-continuation.md#reviewer-and-developer-quota-continuation-owner-2026-10-27). A resumed conversation or repair requires the same fresh dispatch gate and D6 binding; a conversation ID carries no authorization. Never replay an uncertain mutation.
6. **Check the result yourself.** Review the task diff and confirm every changed path is in `write_paths`. Compare the `def test_` counts before and after, look for new skip decorators and new `patch`/mock of production code under test, and reject any result that makes tests faster or greener by removing, mocking or bypassing the behavior they check. Do not run tests in the D6.
7. **Hand back.** The PM hands the result to `llmtool task advance` with an authorship note (AGY, actual model, policy decision and its `--owner-ref` URL, attempts). Formal review and merge gates follow [ai-team-delivery](../ai-team-delivery/SKILL.md). The actual model determines AGY's family: Claude models require a Codex Reviewer, Gemini models accept either a Claude or Codex Reviewer, and an unknown model family accepts neither.

## Part 2: AGY — working rules

Put these in the prompt. AGY also reads this skill and the repository `AGENTS.md`.

- Edit only the files the Task lists. Do not run any Git command that writes (commit, add, stash, checkout, branch, tag, reset, rebase, push, config); reading with `git diff`, `git log` or `git status` is fine.
- Do not delete, merge or skip tests. Do not loosen assertions. Do not mock or replace the production behavior a test exists to check. One real check per behavior must remain.
- Do not edit credentials, provider configuration, `worker-dispatch.json` or files outside the D6.
- On a permission denial, report it; do not broaden permissions, change modes or ask for an uncontained retry.
- When the Task is about speed, the PM runs the before/after test command outside the Developer's D6, using the same command and reports the remote-test receipt.
- Final answer, as plain text: changed files; what changed and why; PM-run test receipt or `UNKNOWN`; before/after numbers when relevant; anything not done or unverified, marked `UNKNOWN`.

## Done when

The PR records the AGY-specific evidence: the llmtool dispatch command (without secrets), `--owner-ref` URL, policy decision, conversation ID, actual model, usage and quota delta, attempts and any rejected attempt, exact D6 root/branch/HEAD, permission checks, and the PM's scope and no-weakening checks. The general completion gates are in [ai-team-delivery](../ai-team-delivery/SKILL.md).
