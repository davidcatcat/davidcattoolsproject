# A13.1 Project Registration, Trust, and Authorization Design

Status: Proposed design only (nothing implemented). Implementation waits for formal review of this note. Real external repository mutation requires separate Owner authorization.

Records: tracking `#2793`; Owner permission comment `7242743327`; [ROADMAP A13.1](../../tools/llmtool/ROADMAP.md); [A13.0 ProjectContext Consumer Handoff](./a13-project-context-consumer-handoff.md); [A13 trust.json design](./a13-trust-json-design-369.md).

## 1. Starting Point and Schema Conflicts to Resolve

Today `tools/llmtool/llmtool/trust.py` (`load_trust`, `verify_repository`) binds one OS user to one repository via `~/.config/llmtool/trust.json` in a single process-wide cache. `tools/llmtool/llmtool/project_context.py` (`ProjectContext.from_trust`, `ProjectContext.__init__`) defines an immutable `ProjectContext` with no production consumers yet. `tools/llmtool/llmtool/github_identity.py` (`owner_login`, `ai_login`, `ai_gh_config_dir`, `ai_github_env`, `github_identity`) fixes two GitHub logins and `~/.config/gh-ai`, while `tools/llmtool/llmtool/review_exchange.py` (`_context`, `prepare_request`, `author_root`, `review_root`) checks fixed `AUTHOR`/`REVIEWER` session UUIDs and fixed `/codex` and canonical roots.

Two existing conflicts between `tools/llmtool/llmtool/trust.py` and `tools/llmtool/llmtool/project_context.py` must be resolved by A13.1:

1. **Root-overlap invariant**: `tools/llmtool/llmtool/trust.py` (`_validate_roots`) requires `tasks_root` to be a strict child of `workspace_root`, whereas `tools/llmtool/llmtool/project_context.py` (`_validate_roots`) rejects any overlap among `canonical_root`, `workspace_root`, and `tasks_root` with `PROJECT_CONTEXT_INVALID`. A13.1 aligns `project_context.py` (`_validate_roots`) to the `trust.py` (`_validate_roots`) rule: `canonical_root` and `workspace_root` must not overlap, and `tasks_root` must be a strict child of `workspace_root`.
2. **Input shape**: `tools/llmtool/llmtool/project_context.py` (`ProjectContext.from_trust`) accepts only a `Mapping` with four keys (`canonical_root`, `workspace_root`, `tasks_root`, `repository`), whereas `tools/llmtool/llmtool/trust.py` (`load_trust`) validates seven keys and returns a `TrustConfig` dataclass. A13.1 updates `ProjectContext.from_trust` to accept `TrustConfig` (or a project trust mapping including `target_branch`) and project-scoped fields rather than rejecting seven-key trust configs.

## 2. Local Registry and Project Registration

- **Local registry**: An owner-only registry at `<OWNER_HOME>/.config/llmtool/projects.json` (indexing per-project records under `<OWNER_HOME>/.config/llmtool/projects/<project_id>.json`), read with the `O_NOFOLLOW`, `0700` directory, `0600` regular file, `nlink == 1`, and no-`.git`-ancestor checks in `tools/llmtool/llmtool/trust.py` (`_read_trust_bytes`, `load_trust`).
- **Explicit registration**: A user/Owner explicitly registers a project (e.g., `llmtool project register`) by validating `project_id` (checked via `tools/llmtool/llmtool/project_context.py` `_validate_project_id`), `canonical_root`, `workspace_root`, `tasks_root`, `allowed_origins`, `target_branch`, GitHub identity and permissions, and project policy before writing the registry entry.
- **Explicit selection and isolated state**: Every mutating or repository-sensitive operation requires an explicit `--project <project_id>` or an unambiguous registered working-tree match; unregistered or ambiguous targets fail closed. Each project stores its own trust, policy, D6 registry, tasks, logs, and evidence in project-scoped directories so projects cannot collide.

## 3. Stable Repository Identity, Roots, and Target Branch

- **Stable repository identity**: Each project binds `project_id`, `repository.slug`, `repository.allowed_origins` (exact SSH/HTTPS forms), `target_branch`, and `trust_binding_sha256` computed by `tools/llmtool/llmtool/trust.py` (`_binding`) and mirrored in `tools/llmtool/scripts/d6_worktree_lifecycle.py` (`_load_trust`).
- **Canonical root and worktree boundary**: Within each project, `canonical_root` (primary checkout, not a linked worktree) and `workspace_root` must not overlap, and `tasks_root` must be a strict child of `workspace_root` (`tools/llmtool/llmtool/trust.py` `_validate_roots`). Across projects, `canonical_root`, `workspace_root`, and `tasks_root` must not overlap any other registered project's roots.
- **Target branch**: Each project explicitly configures `target_branch` (defaulting to `main` only during explicit migration of self-development, never inferred). PR targets, merge-base checks, and review bindings use `target_branch`.

## 4. Project-Scoped Trust, Policy, GitHub Identity, and Session Guard

- **Project-scoped trust and policy**: Each project has its own trust and validation/CI policy. Self-development keeps its existing CI/runner/approval gates; external projects configure optional local/GitHub/remote checks and optional `approval_refs` (`tools/llmtool/llmtool/project_context.py` `_validate_approval_refs`). Target repository files remain untrusted and never grant execution, credentials, or policy overrides.
- **GitHub identity and permissions (design only)**: Replace fixed logins in `tools/llmtool/llmtool/github_identity.py` (`owner_login`, `ai_login`, `github_identity`) with project-scoped `owner_login` and `ai_login` (`owner_login != ai_login`), and replace `~/.config/gh-ai` in `ai_gh_config_dir` and `ai_github_env` with a project-scoped `GH_CONFIG_DIR` under `<OWNER_HOME>/.config/llmtool/projects/<project_id>/gh`.
  - *Required permissions*: `ai_login` requires repository push/pull-request/comment/check-read permissions on `repository.slug` and must not hold admin/bypass or cross-repository write scopes unless explicitly authorized; `owner_login` holds repository owner/admin/merge authority.
  - *Checks and refusals*: Registration and pre-mutation checks verify via `gh` under the project's `GH_CONFIG_DIR` that the authenticated login matches `ai_login` (or `owner_login` for owner-gated actions) and that `repository.slug` permissions are neither missing (`GITHUB_PERMISSION_DENIED`) nor excessive (`GITHUB_PERMISSION_EXCESSIVE`); login mismatches or `owner_login == ai_login` fail with `GITHUB_IDENTITY_INVALID`.
- **Session identity guard (design only)**: Replace the fixed `AUTHOR`/`REVIEWER` session UUIDs and fixed roots in `tools/llmtool/llmtool/review_exchange.py` (`_context`, `prepare_request`, `author_root`, `review_root`) with project-scoped, task-bound developer/reviewer identities and roots derived from the active `ProjectContext` and task dispatch/expectation records.

## 5. Migration from Self-Development (Explicit, No Implicit Fallback)

- Existing self-development uses `~/.config/llmtool/trust.json`. Migration is explicit (e.g., `llmtool project register --from-legacy-trust`): it reads the legacy file, writes an explicit `project_id` entry with `target_branch` set to `main` and the existing GitHub logins, and marks the legacy file migrated.
- **No implicit fallback**: If the local registry is missing or a target is unregistered, `tools/llmtool/llmtool/trust.py` (`load_trust`) and `tools/llmtool/scripts/d6_worktree_lifecycle.py` (`_load_trust`) fail closed and never silently fall back to `~/.config/llmtool/trust.json` or hardcoded constants.

## 6. Changed Refusals and Negative Cases (A13.1 Acceptance)

- **Refusals that change**:
  - `tools/llmtool/llmtool/trust.py` (`load_trust`, `verify_repository`): Instead of a single global cache returning `TRUST_MISSING`/`TRUST_REPOSITORY_INVALID` against one repo, `load_trust` resolves an explicit `project_id` (`PROJECT_UNREGISTERED` if absent, `PROJECT_CONTEXT_AMBIGUOUS` if unresolved) and `verify_repository` validates `canonical_root` and `origin` against that project.
  - `tools/llmtool/scripts/d6_worktree_lifecycle.py` (`_load_trust`, `_trusted_dispatcher_git`, `create_worktree`, `_trust_binding_status`, `_require_trust_binding`, `_recheck_trust_after_lock`): Instead of binding `D6_ORIGIN_INVALID`, `D6_REPOSITORY_INVALID`, `D6_WORKSPACE_ROOT_INVALID`, `ENV_BOOTSTRAP_SOURCE_UNTRUSTED`, and `D6_TRUST_ROTATED` to one global `trust.json`, all checks bind to the selected project's registry entry and `trust_binding_sha256`.
- **Required negative cases**:
  1. Unregistered target (`PROJECT_UNREGISTERED`); no implicit fallback.
  2. Ambiguous context (`PROJECT_CONTEXT_AMBIGUOUS`).
  3. Wrong origin (`TRUST_REPOSITORY_INVALID` / `D6_ORIGIN_INVALID`).
  4. Wrong root or worktree boundary, including cross-project root overlap (`TRUST_REPOSITORY_INVALID` / `TRUST_PATHS_OVERLAP` / `D6_REPOSITORY_INVALID`).
  5. Wrong identity: invalid/mismatched GitHub login or missing/excessive permissions (`GITHUB_IDENTITY_INVALID`, `GITHUB_PERMISSION_DENIED`, `GITHUB_PERMISSION_EXCESSIVE`), or mismatched exchange session/root (`EXCHANGE_RECIPIENT_INVALID`).
  6. Stale identity: rotated `trust_binding_sha256` or mid-run change (`D6_TRUST_ROTATED`, `TRUST_CHANGED_DURING_RUN`).
  7. Cross-project access: task, worktree, `GH_CONFIG_DIR`, or evidence from another project (`PROJECT_CONTEXT_MISMATCH` / `PROJECT_CONTEXT_INVALID`).

## 7. Gates

- Exact-head hosted docs check.
- Formal review before any A13.1 implementation begins.
