# llmtool — Owner D6 dynamic task Worktree lifecycle (2026-10-19)

> **Decision source:** The requirements owner expressly instructed ChatGPT in the 2026-10-19 project conversation: 「開放worktree吧，但是要判斷什麼時候做完改刪除了」. This document is the Integrator-authored record of that chat instruction, **not** an independent GitHub comment or signature from the Owner. PR #200 implements the policy; its review comment [#6255569885](https://github.com/davidcatcat/davidcattoolsproject/pull/200#issuecomment-6255569885) raised F1–F6. Decision status: **APPROVED IN CHAT**; policy PR merge and native Mac runtime automation are **separate, not yet verified**.

## Scope

- The former G7 maximum of two fixed local developer worktrees is superseded. Authorized task-owned Developers may create multiple **linked** Git worktrees within the designated Mac parent, without asking for per-task Owner approval. The canonical Integrator/native-F1 checkout is protected and never disposable. Historical `codex` and `claude` trees are not silently transferred or removed.
- Each task has a unique branch and one actual writer/Session. The existing Integrator allocates tasks and serializes shared-ref/branch operations; llmtool does not become the semantic PM.
- Worktree retirement is a **task-completion decision**, not a clock-based cache policy. Normally retain while ACTIVE/REVIEW_PENDING/REPAIR and retire after authoritative PR merge **plus** acceptance/closure; cancellation/rejection requires an explicit keep/discard disposition. PR creation or Agent `done` alone never qualifies.
- Verify authoritative published head, no unpublished local changes, source/CI/review/evidence retention, live Session/descendant release, registered physical root and all tracked/untracked/**ignored** contents before deletion. After all checks, allow normal non-forced Git worktree remove, including registered worktree unlock; preserve on uncertainty.
- Squash-merged PRs do **not** give Git ancestry of source branch commits in `main`. Exact authoritative merged PR head SHA equal to local task HEAD is the publication proof. The Integrator alone may `git branch -D` the *exact* accepted squash-merged task branch when no other task references it and its merged-PR evidence is retained; never force-push or delete a remote branch as a cleanup shortcut.
- Allowed cleanup of untracked scratch is confined to exact positively verified paths classified disposable in task-retention evidence. Git's normal removal may silently delete ignored content, so explicit ignored-file inventory/classification is mandatory first; unknown local files/evidence block removal.
- The physical parent, host-owned registry contract and complete creation/handoff/retirement gates are normative in the [D6 worktree lifecycle reference](../../skills/safe-git/worktree-lifecycle.md). They reuse existing local Git common-dir and Integrator lifecycle; no new long-running daemon, supervisor, owner relay loop, permanent controller or release milestone.

## Authority and implementation truth

This chat Owner decision does **not** grant llmtool F2/model-initiated Git mutation, provider export/privacy exceptions, secret access, automatic PR merge, unattended process kills or removal of unknown data. Existing independent review/QA and source-preservation rules remain.

**This documentation is not a Mac runtime implementation.** Until actual host-side create/parallel-work/merged-clean-retire and interrupted/dirty-retain checks pass, report `D6_POLICY_APPROVED / D6_AUTOCLEAN_NOT_LIVE_VERIFIED`. Integrate with existing A11c/M2 workflow, without restarting G1–G7 or creating a new roadmap track.
