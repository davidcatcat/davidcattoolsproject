---
name: safe-git
description: Safely inspect, synchronize, commit, push and publish repository changes.
---

# Safe Git

Use for Git synchronization and publication. [AGENTS.md](../../AGENTS.md) owns repository-wide authority and privacy rules. Never exceed the task's repository, branch or file scope.

## Owner D6 — dynamic task-scoped worktrees

<a id="owner-d6-2026-10-19-dynamic-task-scoped-worktree-lifecycle--new-normative-policy"></a>
D6 permits additional registered, task-owned linked worktrees under the verified llmtool workspace parent; it removes the old two-root ceiling. It does not permit independent clones, arbitrary checkout locations, or shared writers. A Developer worktree must not check out `main`; Developers and Reviewers do not fetch unclaimed refs in the canonical Integrator checkout.

Before creating a worktree:
1. Verify repository identity, common Git directory, workspace parent, task owner and clean source state.
2. Use a unique task root and branch based on the approved SHA. Register the exact root/branch/owner; assign one writer.
3. Isolate runtime/import/build state. Verify root, branch, HEAD and status before work.

Retire only after merge and task acceptance, published source/evidence and final source/inventory binding, and verified absence of active sessions, owners, children or processes. Classify tracked, untracked and ignored files; check locks and repository/worktree state. Use ordinary non-forced removal only when clean and unambiguous. Preserve active, dirty, evidence-bearing or uncertain work. Never alter the canonical checkout.

<a id="owner-approved-two-agent-developer-worktree-exception-not-activated"></a>
The old G1–G7 fixed-root pilot is historical and archived in [policy history](../../docs/history/llmtool-agent-policy-legacy-20261020.md); D6 is current. The D6 worktree lifecycle details remain in the linked [Owner decision](../../docs/decisions/llmtool_dynamic_worktree_20261019.md).

## Task binding and evidence

Developer worktrees never check out `main`; Developers and Reviewers do not perform unclaimed fetches in the canonical Integrator checkout. At task start and completion, bind source, owner, root, branch, HEAD and the permitted file/inventory set. Verify task-base originals and current source; candidate code or a reviewer packet is not itself an acceptance baseline. Do not repin after drift without recording the change and rechecking affected evidence.

For runtime-bound tasks, preserve exact interpreter/import provenance, effective permissions and hook evidence separately from user-granted settings. Unknown ownership, source drift, crash, unreleased process/session or ambiguous ignored/untracked data means STOP and preserve the worktree. D6 retirement also requires no active owner/children and the accepted task's final source/inventory evidence. Published PR-head evidence must match the actual local candidate; do not infer it from a commit claim.

## Before any Git mutation

At the actual task root, record:

```sh
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short --branch
git rev-parse HEAD
git rev-parse --git-common-dir
git remote -v
```

Confirm repository, branch, HEAD, upstream and worktree ownership. Bind task-start and task-final source plus allowed-file inventory. For runtime-bound work, record interpreter/import provenance and effective permission/hook evidence separately from user-granted settings. Verify the remote PR head against the exact tested candidate (including squash publication); a commit claim is not proof. Inspect the full diff and preserve unrelated work. Unknown ownership, source drift, crash, unreleased process/session or ambiguous ignored/untracked data means stop and preserve.

## Fetch and synchronize

Fetch only when the task requires remote state and the root/branch is verified. Compare local and remote refs; do not infer remote state from stale tracking refs. Fast-forward only when the local tree is clean and strictly behind. Do not reset, rewrite published history, force push or clean. On divergence, preserve both sides and resolve in a task branch after reviewing the diff.

## Commit and publish

Stage only task-named files. Inspect staged names and the full staged diff; run `git diff --cached --check` and task-required validation. Commit only the approved scope. Push normally and verify remote branch SHA, PR head, exact CI and review provenance.

For GitHub API documentation edits, pin reads and the change branch to a known base SHA, apply authorized files sequentially using returned blob SHAs, then inspect the final remote diff. API edits do not establish local checkout or test evidence.

## PR review and merge

Before merge, verify current base/head, exact-head CI, formal non-author review and every Task Contract-required Security/QA/runtime gate. A changed head invalidates affected evidence. Merge only when delegated authority covers the PR and all required gates pass; verify the resulting commit. Do not use paid fallback or force operations.

## Cloud and evidence

Cloud uses only its task-configured checkout; verify repository, branch, HEAD and remote before mutation. Do not claim Cloud publication proves Mac execution. Record exact source SHA and results; distinguish PR/issue publication from live Session delivery.
