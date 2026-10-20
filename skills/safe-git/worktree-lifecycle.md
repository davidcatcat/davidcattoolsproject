# D6 worktree registration

This is the operative local registry specification for Owner D6. The lifecycle and safety gates remain in [safe-git](SKILL.md#owner-d6-2026-10-19-dynamic-task-scoped-worktree-lifecycle--new-normative-policy); this file supplies the physical parent and registration format.

## Physical roots

On the authorized Mac, new task-scoped linked worktrees are unique children of:

`/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/`

The parent is `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/`. Do not use the canonical Integrator checkout, an arbitrary clone, a temporary directory or another agent's root. Existing `codex` and `claude` roots require verified owner/session release and explicit transfer; never silently reuse them. Resolve the canonical Git common-dir from the canonical checkout.

## Host-owned registry

Store one local record at `<absolute-real-git-common-dir>/llmtool-worktrees/<task-id>.json`. The registry directory is owner-only mode `0700`; records are owner-only mode `0600`, created or updated atomically under serialized Integrator coordination. Validate real common-dir/root identity, a safe task ID, and absence of symlink traversal or path escape.

Each record has `schema_version=1` and: `task_id`, trusted `agent_id`, `session_ref`, `lease_ref` (verify liveness independently), `root_realpath` and physical identity, `git_common_dir`, `branch`, `base_sha`, `head_sha`, optional `pr_number`, `state`, `last_verified_at`, `evidence_refs`, exact-path `retention_paths`, `lock_state`, and `disposition`.

A missing, malformed, symlinked, conflicting or identity-mismatched record is not authority to adopt or delete an existing directory: mark `RETAIN_BLOCKED` and preserve it. This registry is a host-owned inventory, not a PID lock, model-provided grant, new daemon or permission for llmtool F2. Publish only non-sensitive task ID, branch, PR and state; keep local paths, session tokens and private inventory out of GitHub.

## Task states

Use `ACTIVE`, `REVIEW_PENDING`, `REPAIR`, `RETIRE_READY`, `RETAIN_BLOCKED` and `REMOVED`. Bind the task, actual owner/session, root, branch, approved base and current HEAD. Do not infer Session or lease liveness from a PID alone. When ownership or data status is uncertain, preserve the tree and refs; ordinary non-forced D6 cleanup follows the gates in safe-git.
