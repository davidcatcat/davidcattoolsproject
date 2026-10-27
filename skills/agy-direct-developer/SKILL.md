---
name: agy-direct-developer
description: Use when the Owner assigns a Developer Task to AGY (Antigravity CLI `agy`), either as the PM driving the AGY CLI in a D6 worktree or as AGY doing the work. Covers the Owner-assignment gate, the exact CLI invocation, write-scope checks and the hand-back report.
---

# AGY direct Developer

AGY is `OWNER_ASSIGNMENT_ONLY` in [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json). [AGENTS.md](../../AGENTS.md) owns the authority rule; this skill is the procedure for one assigned Task. llmtool has no AGY route (`task record-pm` and `task delegate` do not accept it), so the PM, or a sub-agent the PM starts, drives the CLI directly and records that limitation on the Task's PR.

## Part 1: PM — dispatch one Task

1. **Owner assignment.** Post the Owner's instruction on [#289](https://github.com/davidcatcat/davidcattoolsproject/issues/289) with a 32-hex Task id, then add `{task_id, owner_assignment_ref}` to `workers.agy.owner_assignments` through a reviewed, merged PR. Nothing else authorizes AGY.
2. **Gate.** From the canonical checkout, fast-forwarded to `origin/main`:
   `tools/llmtool/.venv/bin/python -B -m llmtool dispatch-check agy --owner-ref <#289 comment URL> --task <task id>`.
   Start only on `allowed: true` / `OWNER_ASSIGNMENT_MATCHED`.
3. **Preflight.** `agy models` lists the models and doubles as an authentication check, and a trivial `agy -p "reply OK" --output-format json` must return SUCCESS. `agy -p "/usage"` shows the weekly and five-hour quota remaining. Never print, copy or edit AGY credentials or config.
4. **D6.** Register the Task's D6 from the approved base with the Integrator-controlled procedure in [worktree lifecycle](../safe-git/worktree-lifecycle.md) (agent id such as `agy-argon-medium`). Run AGY only with that D6 root as its working directory.
5. **Run.** One non-interactive turn:

   ```sh
   agy -p "<prompt>" --model <model> --effort <effort> --sandbox \
     --output-format json --print-timeout 0 --log-file <scratchpad>/agy.log < /dev/null
   ```

   The model name comes from `agy models` (for example "argon" is `gemini-4-argon-medium`). `--mode auto` may be unavailable for the account, and print mode still edits and runs commands without prompting. The JSON reports `conversation_id`, status, `duration_seconds` and token usage, but not the model, so record the model and effort as requested values with observed `UNKNOWN`.
6. **Repair.** Repair bounds and quota handling follow the [PM Continuation Contract](../ai-team-delivery/pm-continuation.md#reviewer-and-developer-quota-continuation-owner-2026-10-27). A repair goes to the same AGY conversation with `--conversation <conversation_id>`.
7. **Check the result yourself.** AGENTS.md requires the writable-path controls to be validated **before** execution. AGY has no per-file write allowlist, so before the run confirm the controls it does have: the working directory is the D6 root, no `--add-dir`, and `--sandbox` is on. Record the missing per-file allowlist on the PR. Do not assign AGY a Task whose safety depends on per-file write enforcement. After the run, enforce the file list:
   - `git -C <D6> diff --stat` must list only the assigned files.
   - Compare the `def test_` counts before and after, and look for new skip decorators and new `patch`/mock of the production code under test.
   - Run the focused test modules from the D6 yourself.
   - Reject any result that makes tests faster or greener by removing, mocking or bypassing the behavior they check. On 2026-10-27 AGY's first attempt did exactly that and was rejected.
8. **Hand back.** The PM commits from the D6 with an authorship note (AGY, model, Owner assignment ref, attempts) and opens the PR. Formal review and merge gates follow [ai-team-delivery](../ai-team-delivery/SKILL.md); AGY's model family is Gemini, so a Claude or Codex Reviewer qualifies.

## Part 2: AGY — working rules

Put these in the prompt. AGY also reads this skill and the repository `AGENTS.md`.

- Edit only the files the Task lists. Do not commit, push, open PRs or change Git configuration.
- Do not delete, merge or skip tests. Do not loosen assertions. Do not mock or replace the production behavior a test exists to check. One real check per behavior must remain.
- Do not edit credentials, provider configuration, `worker-dispatch.json` or files outside the D6.
- When the Task is about speed, measure before and after with the same command, for example `python3 -m unittest discover -s tools/llmtool/tests -p <module>.py --durations 15` run from the D6 root, with `PYTHONPATH=tools/llmtool` when imports need it.
- Final answer, as plain text: changed files; what changed and why; tests run, with counts and results; before/after numbers when relevant; anything not done or unverified, marked `UNKNOWN`.

## Done when

The PR records the AGY-specific evidence: Owner assignment ref, the AGY command (without secrets), conversation id, usage and quota delta, the attempts and any rejected attempt, the pre-run write-control check and the PM's scope and no-weakening checks. The general completion gates are in [ai-team-delivery](../ai-team-delivery/SKILL.md).
