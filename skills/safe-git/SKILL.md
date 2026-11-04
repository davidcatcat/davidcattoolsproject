---
name: safe-git
description: Use when inspecting repository state, fetching or syncing branches, staging or committing changes, pushing or publishing PRs, or safely merging reviewed work.
---

# Safe Git

Use for Git synchronization and publication. [AGENTS.md](../../AGENTS.md) owns repository-wide authority and privacy rules. Never exceed the task's repository, branch or file scope.

## Owner D6 — dynamic task-scoped worktrees

D6 permits additional registered, task-owned linked worktrees under the verified llmtool workspace parent; it removes the old two-root ceiling. Follow the normative [D6 lifecycle and registry reference](worktree-lifecycle.md) for all create, canonical-root handoff, retain and retire decisions. It does not permit independent clones, arbitrary checkout locations, or shared writers. A Developer worktree may check out only its registered task branch; never `main`, a peer task branch or `automation/ai-loop`. Developers and Reviewers do not fetch unclaimed Git in the canonical Integrator checkout.

Before creating a worktree:
1. Verify repository identity, common Git directory, workspace parent, task owner and clean source state.
2. Use a unique task root and branch based on the approved SHA. Register the exact root/branch/owner; assign one writer.
3. Isolate runtime/import/build state. Verify root, branch, HEAD and status before work.

Do not create or retire a D6 tree based only on this summary; follow the complete acceptance, inventory, ignored-file and process-release gates in the linked lifecycle reference. Preserve active, dirty, evidence-bearing or uncertain work. D6 worktree operations must not alter or retire the canonical checkout; its Integrator remains the sole writer.

The old G1–G7 fixed-root pilot is historical and archived in [policy history](../../docs/history/llmtool-agent-policy-legacy-20261020.md); D6 is current. See the linked [Owner decision](../../docs/decisions/llmtool_dynamic_worktree_20261019.md) and [D6 worktree reference](worktree-lifecycle.md) for policy attribution and registry details.

## Task binding and evidence

At task start and completion, bind source, owner, root, branch, HEAD and the permitted file/inventory set. Verify task-base originals and current source; candidate code or a reviewer packet is not itself an acceptance baseline. Do not repin after drift without recording the change and rechecking affected evidence. The existing Owner authorization for Codex `danger-full-access` / `approval_policy=never` is recorded in the [permission-mode decision](../../reports/llmtool_codex_danger_full_access_audit_decision_20261013.md) and [Issue #135](https://github.com/davidcatcat/davidcattoolsproject/issues/135#issuecomment-6152425440); it records authority, not proof of an effective Mac session mode or native audit. Reviewers may report risks but cannot veto or revoke this Owner authorization; keep authority, effective mode and audit evidence separate. It does not waive task scope, D6, secret handling, egress or Git restrictions.

For runtime-bound tasks, preserve exact interpreter/import provenance, effective permissions and hook evidence separately from user-granted settings. Before accepting a check result, bind it to the candidate SHA and verify the merge-base, source revision and artifact/test provenance; reject stale results. Unknown ownership, source drift, crash, unreleased process/session or ambiguous ignored/untracked data means STOP and preserve the worktree. Published PR-head evidence must match the actual local candidate; do not infer it from a commit claim.

## Before any Git mutation

For source/Git mutations in a claimed Mac workspace, run the required [read-only executable preflight](preflight-reference.md) with explicit, independently established expectations. Any nonzero result means preserve the state and stop; it does not authorize the workspace or replace D6 registration. GitHub API documentation edits follow their pinned-base procedure below and do not require a local Mac CLI preflight.

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

Inspect the full diff and preserve unrelated work. Verify the published PR head against the exact tested candidate (including squash publication); a commit claim is not proof.

## Fetch and synchronize

Fetch only when remote state is required and the root/branch is verified. Developers and Reviewers must not perform unclaimed Git fetches in the canonical Integrator checkout. Compare local/remote refs; never infer remote state from stale tracking refs. Fast-forward only from a clean tree that is strictly behind. If histories diverge or a conflict appears, stop and report the exact state unless the user explicitly asked to resolve it; preserve both sides. Follow the [D6 hard Git limits](worktree-lifecycle.md#hard-git-safety-limits). Never use `git stash` in a D6 because every worktree shares the same stash list.

## Commit and publish

Stage only task-named files. Inspect staged names and the full staged diff; run `git diff --cached --check` and task-required validation. Commit only the approved scope. Push normally and verify remote branch SHA, PR head, exact CI and review provenance.

For GitHub API documentation edits, pin reads and the change branch to a known base SHA, apply authorized files sequentially using returned blob SHAs, then inspect the final remote diff. API edits do not establish local checkout or test evidence. For Mac operator handoff and frozen routes, see [Mac operator handoff and frozen paths](../ai-team-delivery/controller-release.md). If the environment cannot write `.git`, do not create another checkout or move to `/tmp`; preserve source changes in the authorized repository and give the exact staging/commit/push commands for that root.

## PR review and merge

Before merge, verify current base/head, exact-head CI, formal non-author review and every Task Contract-required Security/QA/runtime gate. A changed head invalidates affected evidence. Merge only when delegated authority covers the PR and all required gates pass; verify the resulting commit. Do not use paid fallback or force operations.

## Done when

The authorized Git operation is complete, its resulting repository state is verified, and required review and CI gates are recorded.

## Cloud and evidence

Cloud uses only its task-configured checkout. Verify it is `davidcatcat/davidcattoolsproject` and record branch, HEAD and remote before mutation; if identity/root cannot be established, STOP. Never assume `/workspace`. Do not claim Cloud publication proves Mac execution. Record exact source SHA and results; distinguish PR/issue publication from live Session delivery.
