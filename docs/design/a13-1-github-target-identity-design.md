# A13.1 Design Note: Target Branch and GitHub Identity and Permissions

Scope: documentation only. No code, runtime, environment, or CI changes are implemented by this note. Any real external repository writes or credential operations require separate **Owner authorization** per [ROADMAP.md](../../tools/llmtool/ROADMAP.md). For state-location boundaries and store roots, see [a13-state-location-boundary.md](a13-state-location-boundary.md).

All proposed behaviours, schema fields, and refusal codes below are labelled **Proposed**. Unresolved design choices are marked **Open**. Code references name file and function/symbol only, with no line anchors.

---

## 1. Target Branch Replacement

Currently, llmtool hardcodes `main` as the target/base branch across PR creation, merge gates, base-commit walks, and branch-protection guards:

- `task_advance.py`:
  - `_publish_created_pr` invokes `gh pr create` with `--base main`.
  - `_merge_main_gate` and `_acceptance_carry_code` read `branches/main` via `github_get`.
  - `_publish_base_commits` and `_publish_task_scope_bases` query `git ls-remote --heads origin refs/heads/main`.
- `merge_train_build.py`:
  - `build_train` calls `task_completion.read_authenticated_main_sha` and invokes `gh pr create` with `--base main`.
- `task_review_collector.py`:
  - `collect_local_facts` and `Observation.validate` reject `branch == 'main'` with `REVIEW_BRANCH_INVALID`.
- `review_authority.py`:
  - `RegisteredAgent.__post_init__` rejects `branch_prefix == 'main'` (`REVIEW_REGISTRATION_INVALID`), and `HostAuthorshipObserver.observe` rejects `branch == 'main'` (`REVIEW_IDENTITY_NOT_ATTESTED`).

### Proposed design

1. **Project configuration**: Each registered project stores an explicit `target_branch` (**Proposed**), validated as a non-empty Git branch name without a `refs/heads/` prefix, distinct from Task and Reviewer branch prefixes.
2. **Consumer updates**:
   - `task_advance.py`: `_publish_created_pr` passes `--base <target_branch>`; `_merge_main_gate` and `_acceptance_carry_code` read `branches/<target_branch>`; `_publish_base_commits` and `_publish_task_scope_bases` query `refs/heads/<target_branch>`.
   - `merge_train_build.py`: `build_train` reads the authenticated tip of `<target_branch>` and passes `--base <target_branch>` to `gh pr create`.
   - `task_review_collector.py` (`collect_local_facts`, `Observation.validate`) and `review_authority.py` (`RegisteredAgent.__post_init__`, `HostAuthorshipObserver.observe`): forbid `branch == target_branch` and `branch_prefix == target_branch` instead of only `'main'`.
3. **Fail-closed refusals (Proposed)**: If `target_branch` is missing, malformed, or absent on `origin` / GitHub, refuse with `PROJECT_TARGET_BRANCH_INVALID`, `PUBLISH_TARGET_BRANCH_UNAVAILABLE`, or `TRAIN_TARGET_BRANCH_UNAVAILABLE`. Never fall back to `main`.
4. **Open**: Whether `task_completion.read_authenticated_main_sha` is parameterized to accept `target_branch` or replaced by a project-scoped helper, and how legacy self-development explicitly migrates `target_branch` to `main`.

---

## 2. GitHub Identity Separation (AI vs. Owner)

Currently, `github_identity.py` hardcodes `_GITHUB_LOGINS` (`davidcatcat`, `ddavidccatssun`) and `ai_gh_config_dir` (`<home>/.config/gh-ai`). `ai_github_exec.py` (`verified_ai_env`) and `review_dispatch.py` (`_gh_env`, `_resolve_github_login`) accept only `ai_login()` inside the AI-only `GH_CONFIG_DIR`. `task_advance.py` (`_owner_acceptance_code`) checks `user['login']` against `(owner_login(), ai_login())`.

### Proposed design

1. **Project-scoped identity fields (Proposed)**: Replace the fixed tuple in `github_identity.py` with project-scoped `owner_login`, `ai_login`, and `ai_gh_config_dir` (validated by `github_identity` so `owner_login != ai_login` and both match `_GITHUB_LOGIN_PATTERN`, else `GITHUB_IDENTITY_INVALID`).
2. **Strict AI-only config boundary**: The AI-only `GH_CONFIG_DIR` (`ai_gh_config_dir`) must **only** authenticate as `ai_login`. Accepting `owner_login` inside the AI-only GitHub configuration is prohibited: `verified_ai_env` (`ai_github_exec.py`) and `_resolve_github_login` (`review_dispatch.py`) must continue to accept only `ai_login` and refuse `owner_login` or any other login.
3. **Separate Owner identity check (Proposed)**: Owner identity and permissions are verified through a separate Owner check (`verified_owner_env`, **Proposed**) using the Owner's own `gh` configuration environment, never `ai_gh_config_dir`. If the Owner environment authenticates as `ai_login` or any login other than `owner_login`, refuse with `OWNER_GH_LOGIN_MISMATCH` (**Proposed**) or `OWNER_GH_CONFIG_MISSING` (**Proposed**).
4. **Open**: Whether single-account users (where one person has only one GitHub account) are supported by an explicit single-account policy or must provision a separate machine account.

---

## 3. Verifiable Permission Probes and Fail-Closed Rules

Repository metadata (`gh api repos/{owner}/{repo}`) reports only role flags (`admin`, `maintain`, `push`, `triage`, `pull`). It does **not** report fine-grained token scopes (pull requests, issues, checks, Actions), branch-protection bypass rights, or cross-repository write access.

| Requirement | Probe | Verified condition | Refusal on failure / excess / uninspectable |
|---|---|---|---|
| AI login identity | `gh api user --jq .login` under `ai_github_env` | Exact match to project `ai_login` | `AI_GH_CONFIG_MISSING`, `AI_GH_LOGIN_MISMATCH`, `REVIEWER_GITHUB_LOGIN_MISMATCH` |
| Owner login identity | `gh api user --jq .login` under separate Owner env | Exact match to project `owner_login` | **Proposed** `OWNER_GH_CONFIG_MISSING`, `OWNER_GH_LOGIN_MISMATCH` |
| AI repository role | `gh api repos/{owner}/{repo} --jq .permissions` under AI env | `pull` and `push` true; `admin` false | **Proposed** `AI_PERMISSION_MISSING` (if `push` false); `AI_PERMISSION_EXCESSIVE` (if `admin` true) |
| Owner repository role | `gh api repos/{owner}/{repo} --jq .permissions` under Owner env | `admin` true | **Proposed** `OWNER_PERMISSION_MISSING` |
| Classic OAuth scopes | Response headers (`X-OAuth-Scopes`) from `gh api -i user` | Coarse scopes present; no excessive org/admin/delete scopes | **Proposed** `AI_PERMISSION_EXCESSIVE`, `OWNER_PERMISSION_EXCESSIVE` |
| Fine-grained PR / issue / checks / Actions permissions & cross-repo write isolation | **No universal read-only API** for arbitrary PATs; GitHub App installation tokens can list `/installation/repositories` | Cannot be established for arbitrary PATs via `repos/{owner}/{repo}` | **Proposed** fail closed with `GITHUB_PERMISSIONS_UNVERIFIABLE` (never report as passing) |

- **Fail-closed rule**: Whenever required permissions (pull requests, issues, checks, Actions) or absence of cross-repository write access cannot be inspected by the available probe, the check **refuses** (**Proposed** `GITHUB_PERMISSIONS_UNVERIFIABLE`) and never reports PASS.
- **Open**:
  1. Which token mechanism (e.g., GitHub App installation token with inspectable repository/permission grants vs. Owner-attested fine-grained PAT) is required to satisfy PR, issue, checks, Actions, and single-repo write verification.
  2. What constitutes an excessive Owner permission beyond `admin` on the target repository (e.g., org-wide/cross-repo scope thresholds).

---

## 4. Refusal Code Mapping and Changed/Unchanged Declarations

### Login and configuration refusals

- `GITHUB_IDENTITY_INVALID` (`github_identity.py`, `GitHubIdentityError`): **Configuration/registration-time** validation error when `owner_login == ai_login`, either login fails `_GITHUB_LOGIN_PATTERN`, or `ai_gh_config_dir` is invalid. It does not replace runtime `gh` checks.
- `AI_GH_CONFIG_MISSING` (`ai_github_exec.py`, `verified_ai_env`): **Unchanged code, changed input source** — resolves `ai_gh_config_dir` from the active project registration instead of `<home>/.config/gh-ai`.
- `AI_GH_LOGIN_MISMATCH` (`ai_github_exec.py`, `verified_ai_env`): **Unchanged code, changed comparison** — compares `gh api user --jq .login` against the project's `ai_login`.
- `REVIEWER_GITHUB_LOGIN_MISMATCH` (`review_dispatch.py`, `_resolve_github_login`): **Unchanged code, changed comparison** — `_gh_env` and `_resolve_github_login` use the project's `ai_gh_config_dir` and `ai_login`.

### Repository and registration refusals

- `REVIEW_REPOSITORY_INVALID` (`task_review_collector.py`, `collect_local_facts`): **Changed** — validates `origin` against the registered project's authorized origin instead of the single hardcoded repository in `origin_allowed`.
- `REVIEW_REGISTRATION_INVALID`:
  - **Changed** in `review_authority.py` (`RegisteredAgent.__post_init__`): validates `origin` and `branch_prefix` against the project's registered origin and `target_branch` instead of fixed `origin_allowed` and `'main'`.
  - **Unchanged** in `task_review_collector.py` (`collect_local_facts` worktree lock/branch registration checks) and `review_authority.py` (`_decode_host`, `load_host_registration` device/inode/duplicate checks).

---

## 5. Self-Development GitHub Configuration Migration

Moving self-development from `<home>/.config/gh-ai` (`github_identity.py`, `ai_gh_config_dir`) to a project-scoped GitHub configuration directory does not automatically authenticate that directory.

- llmtool must **never** silently copy credentials from `<home>/.config/gh-ai` or `<home>/.config/gh` into a project directory.
- Any credential copy or migration step requires **explicit Owner authorization** (or direct user authentication via `gh auth login` into the new directory).
- **Open**: Whether self-development retains `<home>/.config/gh-ai` via an explicit project registration pointing to that path (avoiding credential copying) or uses an Owner-authorized one-time authentication/migration step into the project-scoped directory.

---

## 6. Author and Reviewer Guard and Reviewer Root Change

Currently, `review_exchange.py` hardcodes `AUTHOR`, `REVIEWER`, `author_root()` (`workspace_root + '/codex'`), and `review_root()` (`canonical_root`) in `_context` and `prepare_request` (`EXCHANGE_RECIPIENT_INVALID`). Existing D6 registration records (`task_completion.read_d6_registration`) hold **no `role` field**.

### Authoritative records for replacement guard

To replace the hardcoded `AUTHOR`/`REVIEWER` constants and fixed roots, the guard must bind against existing authoritative records:

1. **Task record** (`task_store.py`, `TaskStore.get`): `task_id`, `run_id`, `developer` (`executor_task_id`, `session_id`, `provider_profile`), and `review_dispatch_claim` (`reviewer_task`, `native_session_id`, `reviewer`, `state`).
2. **D6 registration record** (`task_completion.py`, `read_d6_registration`): `task_id`, `root`, `common_dir`, `branch`, `state`, `session_ref` (noting that `role` is absent today).
3. **Host registration and expectations** (`review_authority.py`, `HostRegistration`, `RegisteredAgent`, `HostAuthorshipObserver.observe`; `task_lifecycle.py`, `Expectations`): `agents` (`agent_id`, `root`, `common_dir`, `branch_prefix`), `reviewer_role`, and `expect.authored_by` (`Role.DEVELOPER` vs. `Role.REVIEWER`).

### Declared changes and open item

- **Declared change**: `review_exchange.py` (`author_root`, `review_root`, `_context`, `prepare_request`) changes from fixed `workspace_root + '/codex'` and `canonical_root` to project-registered Developer and Reviewer D6 worktree roots.
- **Open**: Whether to add an explicit `role` field to D6 registration records (requiring a D6 schema update in `d6_worktree_lifecycle.py` and `task_completion.read_d6_registration`) or derive Developer vs. Reviewer role strictly from the Task record (`developer.executor_task_id` vs. `review_dispatch_claim[].reviewer_task`) and `HostRegistration`.
