# Codex repository guidance

## Canonical repository and pre-change checks

The canonical local Mac project repository directory is:

```text
/Users/davidsun/git/davidcataiporject/davidcattoolsproject
```

Local Mac project work must be performed from this canonical repository: source changes,
unit tests, targeted tests, canonical tests, smoke tests, validation, documentation
work, Git inspection, and staging/commit/push when sandbox permissions permit.

Before any repository-changing work, verify:

```sh
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short --branch
```

For local Mac work, the root must resolve exactly to
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
For user-selected Codex Cloud work, use only the checkout configured for that task.
Discover its root with Git and verify it is `davidcatcat/davidcattoolsproject`;
do not assume `/workspace` is the repository. Record environment, root, branch,
HEAD, remote identity and working-tree state. If identity/root cannot be established,
STOP before changes. Cloud is an explicit exception to the Mac path requirement,
not permission to create alternate local project checkouts.

For authorized documentation edits through the connected GitHub API, pin reads
and the change branch to a known base SHA and verify the final remote diff.
Do not claim local checkout, test or synchronization evidence from API operations.

Read [llmtool development workflow](tools/llmtool/WORKFLOW.md) when using Cloud or
handing verification between Cloud and Mac. Keep Mac-only VPN/auth/local-service
checks pending until verified on the actual Mac; complete independent Cloud work.

Codex must NOT create or use:

- Git worktrees
- Independent clones, including milestone clones
- Sibling project repositories
- Nested Git repositories
- Project checkouts under `/tmp` or `/private/tmp`
- Project checkouts under `/Users/davidsun/git/davidcataiporject/tmp/`
- Agent-created project checkouts under any other path or alternate project workspaces
  (the task-configured Codex Cloud checkout is allowed)

Do not invent alternate project workspaces or move this project. The former
independent-clone/worktree workflow is deprecated and must not be reused.

## Testing and runtime isolation

Local Mac project tests and validation must be launched from
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`; Cloud checks must use
that task's verified configured checkout. This includes unit,
targeted, canonical, smoke, provider integration, regression, Git, and documentation
validation. Do not create separate project checkouts for testing.

Test-generated temporary files may use normal temporary directories only when
required by the test/runtime itself. The llmtool `codex-cli` provider may continue
creating an ephemeral runtime cwd under `/tmp` or `/var/tmp` as part of its
security isolation. This runtime directory is NOT a project checkout and must
not contain a Git clone, worktree, or copy of the repository. Do not modify
`codex_cli.py` merely to eliminate this runtime isolation.

## Git permissions and safety

If the Codex sandbox prevents writing `.git`, for example `.git/index.lock`,
do NOT create another checkout or worktree, or move to `/tmp`. Leave source
changes in the authorized environment's repository and report the exact environment-specific Git
commands needed for staging, committing, and pushing. Those commands must run
from that repository and name only files belonging to the task.

- Preserve unrelated user work, including existing untracked files/directories.
- Do not use `git reset --hard` or `git clean -fd`.
- Do not force push or rewrite published history.
- Do not stash unrelated user work without explicit instruction.
- Inspect before modifying unfamiliar code.
- Tests and validation remain part of implementation.
- Never weaken tests just to make them pass.
- Stop for external review when the milestone requires it; do not automatically
  start the next milestone or merge main.
- Provider != Role != Agent.

## Repository workflows

`AGENTS.md` is Codex-specific guidance; `CLAUDE.md` is Claude-specific guidance.
Read the relevant reusable workflow in `skills/<name>/SKILL.md` before using it.
Use `inspect-repo` for investigation, `implement-feature` for implementation,
`debug-ci` for failing checks, `review-change` for review, and `safe-git` for Git
operations. Keep one shared workflow per skill; Codex interface metadata belongs
in the skill's `agents/openai.yaml`.

Documentation-only changes require documentation validation, not application
builds. Report checks that passed and any that could not run. Do not claim remote
synchronization from local tracking references without fetching.
