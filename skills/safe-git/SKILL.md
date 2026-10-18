---
name: safe-git
description: Safely inspect, synchronize, commit, and push Git changes. Use when the user asks to fetch, pull, sync, commit, push, update GitHub, or publish repository changes.
---

# Safe Git

Treat remote history and unrelated local work as protected state.
Detailed normative policy lives in three sibling references. They were moved
verbatim from this file; reading the summary below never overrides them.

| Reference | Read it when |
| --- | --- |
| [worktree-exception.md](worktree-exception.md) | Working in a Mac developer worktree, CHECKOUT-CLAIM/RELEASE, task turnover (C2–C5), Codex permission mode |
| [preflight-reference.md](preflight-reference.md) | Running or changing the daily read-only `preflight.py` gate, baselines, candidates, reports |
| [delegated-merge.md](delegated-merge.md) | Merging a PR as coordinator, ai-loop controller promotion, audit attribution |

## Owner-approved two-agent developer worktree exception (NOT ACTIVATED)

The heading is kept so existing links keep resolving. **Current state:** the G7
activation-status PR #126 is merged (`83be9f6`), so the two fixed developer
roots are active under [worktree-exception.md](worktree-exception.md). Summary:

- At most two fixed linked worktrees (one Codex, one Claude); no other clone,
  worktree or archive copy. The canonical Mac root
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject` stays the
  integrator and native-acceptance root.
- One owner/writer and one unique task branch per worktree; never check out
  `main` or a peer branch there; own venv per root.
- Shared canonical root: post `CHECKOUT-CLAIM` / `CHECKOUT-RELEASE`; comments
  are records, not atomic locks.
- Crash, unknown writer, dirty or ambiguous state means STOP and preserve, then
  fall back to the single canonical writer.
- This exception never authorizes llmtool F2 real-repository mutation.

## Before changing Git state

In a claimed Mac workspace, run the daily read-only gate first:
`python3 -B -I <reviewed-source>/skills/safe-git/preflight.py ...` with
independently pinned expected values. Exit 0 prints `PASS: read-only preflight`;
any nonzero exit means preserve state and stop. Full arguments, baseline format
and limits: [preflight-reference.md](preflight-reference.md).

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

Before merge, verify the latest PR/head SHA and use exact-head checks. A report-only
or documentation commit after a previously green implementation creates a new head;
verify that head's required CI rather than citing only the older run. After merge,
verify the new main SHA and any required post-merge CI. Green CI alone does not replace
a required independent review, bug hunt, security review, QA or live/runtime gate.

Keep one Git writer per publication batch when multiple agents are involved.

Coordinator merge delegation, controller promotion and audit attribution:
[delegated-merge.md](delegated-merge.md).

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
