---
name: safe-git
description: Safely inspect, synchronize, commit, and push Git changes. Use when the user asks to fetch, pull, sync, commit, push, update GitHub, or publish repository changes.
---

# Safe Git

Treat remote history and unrelated local work as protected state.

## Before changing Git state

Run:

```bash
git status --short --branch
git branch --show-current
git remote -v
```

Identify unrelated working-tree changes before staging anything.

## Synchronize

1. Run `git fetch origin`.
2. Compare the current branch with its upstream.
3. If integration is required, prefer a normal fast-forward when possible.
4. If histories diverge or a conflict appears, stop and report the exact state unless the user has explicitly asked you to resolve it.
5. Never discard uncommitted work to make synchronization easier.

## Validation and report provenance

Before merging or cherry-picking a feature branch, validation report, or smoke-test artifact,
check its merge base, ahead/behind counts, graph, and the revision/version actually tested. Confirm
whether the branch was based on an older main and whether the target already contains an equivalent
artifact. Preserve newer target history and avoid duplicate or stale evidence. Prefer safe normal
integration; choose the appropriate method for the branch state without rewriting published history.

## Commit

1. Run the project's required tests/checks first.
2. Review `git diff` and `git diff --staged`.
3. Stage only files belonging to the current task.
4. Use a concise commit message that describes the change.
5. Re-check `git status`.

## Push

Push the current branch only after checks pass and the intended commit is confirmed.

Hard guardrails:

- Never force-push unless the user explicitly requests it and the consequences are explained.
- Never use `git reset --hard` to resolve routine workflow problems.
- Never delete branches or tags unless explicitly requested.
- Never overwrite unrelated remote work.

## Merge evidence discipline

## Delegated PR management and audit attribution

The requirements owner has **delegated routine technical PR/merge decisions**
to the AI coordinator within accepted product goals; do not ask them to
act as PM, review each Low finding or hand-carry Codex/Claude prompts.
Follow [ai-team-delivery](../ai-team-delivery/SKILL.md). Delegation
does not waive independent gates, Mac validation, product/provider
permissions, or the local controller-SHA approval requirement.

After verifying all contract-required evidence, the coordinator can
merge an eligible exact-head PR without repeatedly asking "may I merge?".
Keep blocker/unfinished PRs Draft or Open, close genuinely obsolete PRs
with a reason, and retain immutable evidence. Do not merge a failing
controller helper just because Linux CI is green. Never treat the
Mac operator's ability to type a command as permission for that command
to run arbitrary new Git-fetched code.

In the audit trail record **technical disposition author, reviewer,
product scope confirmation, delegated merge authority, Git executor,**
and exact refs separately. Do not attribute the coordinator's detailed
risk analysis to the owner. Do not equate "delegated technical choice"
with new powers to export credentials/data, spend unapproved funds,
bypass branch protection or lower a required security gate.

Before merge, verify the latest PR/head SHA and use exact-head checks. A report-only
or documentation commit after a previously green implementation creates a new head;
verify that head's required CI rather than citing only the older run. After merge,
verify the new main SHA and any required post-merge CI. Green CI alone does not replace
a required independent review, bug hunt, security review, QA or live/runtime gate.

Keep one Git writer per publication batch when multiple agents are involved.

## Cloud/Mac evidence gate

For llmtool work, follow [the development workflow](../../tools/llmtool/WORKFLOW.md).
Publish only the authorized feature branch; branch publication does not establish
merge readiness. Verify required Cloud/CI/review/Mac results against the affected
implementation revision before merge. Keep pending required Mac checks blocking,
and never use a new checkout to bypass Git permissions.

## Completion criteria

- Local and remote state are understood.
- Required checks pass.
- Commit contains only intended changes.
- Push succeeds, or the blocking reason is reported precisely.
