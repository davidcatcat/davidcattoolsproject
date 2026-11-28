# A13.1 Project Registration, Trust, and Authorization Design

Status: Proposed design only (not implemented). Implementation waits for formal review of this note. Any real external repository mutation requires separate Owner authorization.

Records: tracking `#2793`; Owner permission comment `7242743327`; [ROADMAP A13.1](../../tools/llmtool/ROADMAP.md); [A13.0 ProjectContext Consumer Handoff](./a13-project-context-consumer-handoff.md); [A13 trust.json design](./a13-trust-json-design-369.md).

## 1. Problem and Starting Point

Today `tools/llmtool/llmtool/trust.py` (`load_trust`, `verify_repository`) binds one OS user to one repository via `~/.config/llmtool/trust.json`, caching a single `TrustConfig` in `_TRUST_CACHE`. `tools/llmtool/llmtool/project_context.py` defines `ProjectContext` and `ProjectContext.from_trust` (`from_trust`), an immutable, side-effect-free value with no production consumers yet. Meanwhile, `tools/llmtool/llmtool/github_identity.py` (`owner_login`, `ai_login`, `ai_gh_config_dir`, `github_identity`, `ai_github_env`) fixes two GitHub logins and one `~/.config/gh-ai` directory, and `tools/llmtool/llmtool/review_exchange.py` (`_context`, `prepare_request`) guards identity against fixed `AUTHOR` and `REVIEWER` session UUIDs.

A13.1 replaces this single-repository assumption with explicit, project-scoped registration, trust, policy, target branch, and GitHub identity backed by a local registry.

## 2. Local Registry and Project Registration

- **Local registry**: An owner-only registry under `<OWNER_HOME>/.config/llmtool/projects.json` (or per-project files under `<OWNER_HOME>/.config/llmtool/projects/<project_id>.json` indexed by a registry manifest), read with the same `O_NOFOLLOW` / `0700` dir / `0600` regular-file / `nlink == 1` / no-`.git`-ancestor guarantees used in `tools/llmtool/llmtool/trust.py` (`_read_trust_bytes`, `load_trust`).
- **Explicit registration**: Registering a project requires explicit user/Owner action (e.g., `llmtool project register`) that validates `canonical_root`, `allowed_origins`, `workspace_root`, `tasks_root`, `target_branch`, GitHub identity, and project policy, then records the entry under a unique `project_id` (ASCII `1..100` chars per `ProjectContext` in `tools/llmtool/llmtool/project_context.py`).
- **Explicit selection**: Every mutating or repository-sensitive command requires an explicit `--project <project_id>` or an unambiguous registered working-tree match; ambiguous or unregistered targets fail closed.
- **Isolated state**: Each registered project stores its own trust config, D6 worktree registry, task state, logs, and evidence under project-scoped directories so projects cannot collide or read each other's state.

## 3. Stable Repository Identity, Roots, and Target Branch

Starting from `TrustConfig` (`tools/llmtool/llmtool/trust.py`) and `ProjectContext` (`tools/llmtool/llmtool/project_context.py`):

- **Stable repository identity**: Each registered project binds `project_id`, `repository.slug`, `repository.allowed_origins` (exact SSH/HTTPS forms), and `trust_binding_sha256` (computed by `tools/llmtool/llmtool/trust.py` `_binding` over `canonical_root`, `workspace_root`, `tasks_root`, `slug`, `allowed_origins`, extended to include `project_id` and `target_branch`).
- **Canonical root and worktree boundary**: `canonical_root`, `workspace_root`, and `tasks_root` retain the non-overlap and physical-path invariants in `tools/llmtool/llmtool/trust.py` (`_validate_roots`) and `tools/llmtool/llmtool/project_context.py` (`_validate_roots`). Across projects, `canonical_root` and `workspace_root` must not overlap another registered project's roots. `canonical_root` must remain the primary checkout (not a linked worktree).
- **Target branch**: Add an explicit `target_branch` field (defaulting to `main` only during explicit migration of the self-development project, never inferred implicitly) so PRs, merge-base checks, and CI/review bindings target the project's configured branch rather than assuming `main`.

## 4. Project-Scoped Trust, Policy, and GitHub Identity

- **Project-scoped trust and policy**: Split global trust into per-project trust and policy records. Self-development retains its existing CI/runner/approval gates; external projects define explicit project-scoped validation/CI policy (optional `local`/`github`/`remote` checks, `approval_refs` optional for external projects as modeled in `tools/llmtool/llmtool/project_context.py` `_REPOSITORY_OPTIONAL_KEYS`). Target repository files remain untrusted and never grant execution, credentials, or policy overrides.
- **GitHub identity and permissions (`github_identity.py`)**: Replace the hardcoded `_GITHUB_LOGINS` in `tools/llmtool/llmtool/github_identity.py` (`owner_login`, `ai_login`, `github_identity`) with project-scoped `owner_login` and `ai_login` read from the selected project's registration, still validated against `_GITHUB_LOGIN_PATTERN` and `owner_login != ai_login` (`GitHubIdentityError`). Replace the single global `~/.config/gh-ai` path in `ai_gh_config_dir` and `ai_github_env` with a project-scoped (or project-bound identity-scoped) `GH_CONFIG_DIR` under the user's private config tree so credentials never leak across projects.
- **Session identity guard (`review_exchange.py`)**: Replace the fixed `AUTHOR` and `REVIEWER` session UUID constants checked in `tools/llmtool/llmtool/review_exchange.py` (`_context`, `prepare_request`) with project-scoped and task-bound developer/reviewer identities resolved from the active `ProjectContext` and task dispatch/expectation records, alongside `author_root` and `review_root` derived from that project's `workspace_root` and `canonical_root`.

## 5. Migration from Self-Development (Explicit, No Implicit Fallback)

- Existing self-development uses `~/.config/llmtool/trust.json`. Migration is explicit (e.g., `llmtool project migrate-legacy` or `llmtool project register --from-legacy-trust`): it reads the existing `trust.json`, registers it under an explicit `project_id` with `target_branch: "main"` and the current GitHub logins, and marks the legacy file migrated.
- **No implicit fallback**: If the local registry is missing or a target is not registered, commands fail closed. `load_trust` (`tools/llmtool/llmtool/trust.py`) and `_load_trust` (`tools/llmtool/scripts/d6_worktree_lifecycle.py`) must never silently fall back to `~/.config/llmtool/trust.json` or hardcoded constants when project resolution fails.

## 6. Changed Refusals and Negative Cases (A13.1 Acceptance)

### Which current repository and origin refusals change

- `tools/llmtool/llmtool/trust.py` (`load_trust`, `verify_repository`): Currently loads one global `_TRUST_CACHE` and rejects any other repo with `TRUST_REPOSITORY_INVALID`. Under A13.1, `load_trust` resolves the explicit `project_id` from the registry (returning `ProjectContext` via `ProjectContext.from_trust` alongside project trust), and `verify_repository` verifies `canonical_root` and `origin` against that selected project's `allowed_origins`.
- `tools/llmtool/scripts/d6_worktree_lifecycle.py` (`_load_trust`, `_trusted_dispatcher_git`, `create_worktree`, `_trust_binding_status`, `_require_trust_binding`): Currently compare against the single global trust (`D6_ORIGIN_INVALID`, `D6_REPOSITORY_INVALID`, `ENV_BOOTSTRAP_SOURCE_UNTRUSTED`, `D6_TRUST_ROTATED`). Under A13.1, these checks bind to the selected `project_id` and its `ProjectContext` / `trust_binding_sha256`.

### Negative cases required by A13.1

1. **Unregistered target**: Repository, directory, or `project_id` not in the local registry is rejected (`PROJECT_UNREGISTERED`); no implicit fallback.
2. **Ambiguous context**: Multiple matching registrations or missing `--project` when context cannot uniquely resolve is rejected (`PROJECT_CONTEXT_AMBIGUOUS`).
3. **Wrong origin**: Remote `origin` does not match the selected project's `allowed_origins` (`TRUST_REPOSITORY_INVALID` / `D6_ORIGIN_INVALID`).
4. **Wrong root or worktree boundary**: `canonical_root` is a linked worktree, overlaps `workspace_root`/`tasks_root`, or belongs to a different path (`TRUST_REPOSITORY_INVALID` / `TRUST_PATHS_OVERLAP` / `D6_REPOSITORY_INVALID`).
5. **Stale identity**: Project `trust_binding_sha256` rotated after worktree/task creation (`D6_TRUST_ROTATED`).
6. **Cross-project access**: A `task_id`, worktree, GitHub identity, or evidence artifact from project A presented under project B is rejected (`PROJECT_CONTEXT_MISMATCH` / `PROJECT_CONTEXT_INVALID`).

## 7. Gates

- Exact-head hosted docs check (`check (docs)`).
- Formal review by `codex-reviewer` before any A13.1 implementation begins.
