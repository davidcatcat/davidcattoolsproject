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
3. **Preflight (no agent turn).** `agy models` lists the models and doubles as an authentication check. Do not start an agent turn (`agy -p "<task>"`) here: print mode can edit files and run commands, so every agent turn waits for the D6 and the write-control check below. Never print, copy or edit AGY credentials or config.
4. **D6.** Register the Task's D6 from the approved base with the Integrator-controlled procedure in [worktree lifecycle](../safe-git/worktree-lifecycle.md) (agent id such as `agy-argon-medium`). Run AGY only with that D6 root as its working directory.
5. **Write boundary, before any agent turn.** Launch AGY only through an independently reviewed, OS-enforced write boundary bound to this Task, the real D6 root, branch and HEAD, the exact permitted source paths, approved runtime paths, launcher executable and policy digest. The boundary must cover the AGY process, native file tools, shell descendants and helpers. Every other path stays unwritable, including the D6 `.git` pointer, resolved Git directory and shared Git directory, policy and evidence. Cwd, prompt rules, `--sandbox`, and before/after snapshots do not establish this boundary. If effective enforcement cannot be verified, record `BLOCKED_WRITE_ISOLATION` and start no agent turn. Keep the `agy models` preflight, Owner gate and D6 steps above unchanged. No reviewed launcher exists in the repository yet; a possible Mac design using `sandbox-exec` with a deny-default profile is deprecated and unverified, and requires a separate Task. Until a reviewed launcher is merged, every AGY write Task is `BLOCKED_WRITE_ISOLATION` unless the Owner records an explicit residual-risk acceptance changing AGENTS.md. Do not create that acceptance in this Task.

   Run smoke and `/usage` turns through the same verified boundary, with no source-write permission and explicit `--model <model> --effort <effort>`. Check weekly and five-hour quota remaining. Confirm the boundary probe results, exact runtime binding and process-release evidence. These turns do not authorize a later write-enabled turn.
6. **Run.** Before any write-enabled turn, require the `model_resolver` evidence below. Run one non-interactive turn through the verified launcher:

   ```sh
   agy -p "<prompt>" --model <model> --effort <effort> --sandbox \
     --output-format json --print-timeout 0 --log-file <scratchpad>/agy.log < /dev/null
   ```

   The model name comes from `agy models` (for example "argon" is `gemini-4-argon-medium`). `--mode auto` may be unavailable for the account, and print mode still edits and runs commands without prompting. The JSON reports `conversation_id`, status, `duration_seconds` and token usage, but not the model. The `--log-file` does: lines from `model_resolver` read `Resolving model <name>`. Record that name as the CLI-reported actual model (it is the CLI's own log, not a server attestation). If the log has no such line or names a different model than requested, or the launcher lacks verified containment evidence, stop the Task and record why. The effort is recorded as requested.
7. **Repair.** Repair bounds and quota handling follow the [PM Continuation Contract](../ai-team-delivery/pm-continuation.md#reviewer-and-developer-quota-continuation-owner-2026-10-27). A resumed conversation or repair requires the same fresh gate and verified boundary; a conversation ID carries no authorization. Never replay an uncertain mutation.
8. **Check the result yourself.** Snapshots and Git-state diffs are supplementary diagnostics, not write enforcement; review the task diff. The PM reviews the diff and scope. Compare the `def test_` counts before and after, look for new skip decorators and new `patch`/mock of production code under test, run the focused test modules from the D6, and reject any result that makes tests faster or greener by removing, mocking or bypassing the behavior they check. On 2026-10-27 AGY's first attempt did exactly that and was rejected.
9. **Hand back.** The PM commits from the D6 with an authorship note (AGY, model, Owner assignment ref, attempts) and opens the PR. Formal review and merge gates follow [ai-team-delivery](../ai-team-delivery/SKILL.md); AGY's model family is Gemini, so a Claude or Codex Reviewer qualifies.

## Part 2: AGY — working rules

Put these in the prompt. AGY also reads this skill and the repository `AGENTS.md`.

- Edit only the files the Task lists. Do not run any Git command that writes (commit, add, stash, checkout, branch, tag, reset, rebase, push, config); reading with `git diff`, `git log` or `git status` is fine.
- Do not delete, merge or skip tests. Do not loosen assertions. Do not mock or replace the production behavior a test exists to check. One real check per behavior must remain.
- Do not edit credentials, provider configuration, `worker-dispatch.json` or files outside the D6.
- On a permission denial, report it; do not broaden permissions, change modes or ask for an uncontained retry.
- When the Task is about speed, measure before and after with the same command, for example `python3 -m unittest discover -s tools/llmtool/tests -p <module>.py --durations 15` run from the D6 root, with `PYTHONPATH=tools/llmtool` when imports need it.
- Final answer, as plain text: changed files; what changed and why; tests run, with counts and results; before/after numbers when relevant; anything not done or unverified, marked `UNKNOWN`.

## Done when

The PR records the AGY-specific evidence: Owner assignment ref, the AGY command (without secrets), conversation ID, usage and quota delta, attempts and any rejected attempt, launcher and policy digest, effective-boundary probe results, exact runtime binding, process-release evidence, and the PM's scope and no-weakening checks. The pre-run evidence binds the real D6 root, branch, HEAD and exact permitted paths. The general completion gates are in [ai-team-delivery](../ai-team-delivery/SKILL.md).
