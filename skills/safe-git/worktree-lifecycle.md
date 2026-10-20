# D6 task worktree lifecycle (normative)

Owner D6 permits additional registered, task-scoped linked worktrees without the former two-root limit or per-task Owner approval. This reference owns the current local registry, creation, handoff, retention and retirement gates. The canonical Integrator/native-acceptance checkout remains protected and is never disposable. D6 does not grant F2, provider egress, secret access, force operations, automatic merge or unrestricted filesystem authority.

## Roots and registration

On the authorized Mac, new task worktrees use unique children under:

`/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/`

The parent is `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/`. Do not use the canonical checkout, an arbitrary clone, a temporary directory or another Agent's root. Existing `codex` and `claude` roots require verified owner/session release and transfer; never silently reuse them.

Resolve the real Git common-dir from the canonical checkout. Store each host-owned local record at `<absolute-real-git-common-dir>/llmtool-worktrees/<task-id>.json`. The registry directory is owner-only mode `0700`; each record is owner-only mode `0600`, created or updated atomically under serialized Integrator coordination. Validate repository/common-dir/root identity, safe task-id characters, and absence of symlink traversal or path escape.

Each `schema_version=1` record contains `task_id`, trusted `agent_id`, `session_ref`, `lease_ref` (verify liveness independently), `root_realpath` and physical identity, `git_common_dir`, `branch`, `base_sha`, `head_sha`, optional `pr_number`, `state`, `last_verified_at`, `evidence_refs`, exact-path `retention_paths`, `lock_state`, and `disposition`. Missing, malformed, conflicting or identity-mismatched records are not authority to adopt or remove a directory: set `RETAIN_BLOCKED` and preserve it. The registry is an inventory, not a PID lock or model-authored permission. Keep local paths, session tokens and private inventories out of GitHub.

## Create and hand off

1. Bind the task/Issue/PR, actual Agent and Session, unique branch, approved base SHA, canonical common-dir and destination in the registry. Confirm the destination is absent or empty, owned, free of symlink/path traversal, conflicting writers, existing branch checkouts and unexpected files.
2. Create with ordinary `git worktree add -b <unique-branch> <task-path> <approved-base>`; never use `-B` or `--force`. Verify registration and identity, then use ordinary `git worktree lock <task-path>` against incidental prune. Confirm root, common-dir, branch, HEAD and tracked/untracked/ignored inventory before writing. Keep one writer per tree and isolate venv/build/import state.
3. Record task state as `ACTIVE`, `REVIEW_PENDING`, `REPAIR`, `RETIRE_READY`, `RETAIN_BLOCKED` or `REMOVED`. Bind source, owner, root, branch, HEAD, allowed files and evidence at task start and completion.

The canonical checkout has one Integrator writer. Before a canonical-root handoff, verify the prior writer/session and descendants have released it, record the handoff, and confirm repository/root/branch/HEAD/status again. A Developer does not write in the canonical root. A PID alone is not proof of release.

## Retain or retire

A PR open, self-review PASS, elapsed time or Agent saying `done` does not authorize retirement. Retire only after the PR is merged **and** the task is accepted/closed, or after cancellation/rejection with a recorded keep/discard disposition. Keep active, review-pending or repair trees by default.

Before removal, verify the exact registered noncanonical path, owner/session/lease release, no running tests/children/reviewers, expected common-dir/branch/HEAD/lock, no Git locks, and no later unpublished work. Inspect staged, unstaged, untracked and ignored contents. Git removal can silently delete ignored files, including venvs or evidence; classify every exact retention path as `KEEP/EXPORT` with verified preservation or `DISCARD_REGENERABLE` after confirming it contains no unique data, secrets, recovery state or evidence. Unknown or ambiguous content means `RETAIN_BLOCKED`. Never kill a process to make cleanup pass.

For a squash merge, authoritative PR metadata must show merged/accepted and its final published PR head SHA must equal the local task HEAD. Preserve independent review, CI, source and retention evidence outside the tree. Only the Integrator may later delete that exact local task branch with `git branch -D`, and only for an accepted squash-merged PR with matching final head SHA and no other task/worktree/PR dependency. Do not delete remote refs or unrelated branches/tags. Cancellation of unmerged work requires its explicit recorded disposition.

When every gate passes, unlock only the exact D6-owned locked tree, recheck identity and liveness, then run ordinary `git worktree remove <exact-path>` without `--force`. Verify it is absent and record `REMOVED`, final SHA, task/PR, disposition, evidence and time. Failed unlock/removal means `RETAIN_BLOCKED`; never force-retry.

## Hard Git safety limits

Do not use `git reset --hard`, `git clean`/`clean -fd`, forced checkout/switch, `git switch -B`, force push, history rewrite, `git worktree prune`, or `rm -rf` to bypass preservation. Do not stash unrelated work or delete branches/tags outside the exact D6 squash rule above. Developers must not check out `main`; do not fetch unclaimed refs in the canonical Integrator checkout.

This policy is not Mac runtime evidence. Until the actual path demonstrates create, parallel work, accepted-task removal and interrupted/dirty preservation, report D6 auto-retirement as **NOT LIVE VERIFIED**.
