# Codex repository guidance

## Canonical repository and pre-change checks

The ONLY project repository directory is:

```text
/Users/davidsun/git/davidcataiporject/davidcattoolsproject
```

All project work must be performed from this canonical repository: source changes,
unit tests, targeted tests, canonical tests, smoke tests, validation, documentation
work, Git inspection, and staging/commit/push when sandbox permissions permit.

Before any repository-changing work, verify:

```sh
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short --branch
```

The repository root must resolve exactly to
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
If not, STOP and report the mismatch.

Codex must NOT create or use:

- Git worktrees
- Independent clones, including milestone clones
- Sibling project repositories
- Nested Git repositories
- Project checkouts under `/tmp` or `/private/tmp`
- Project checkouts under `/Users/davidsun/git/davidcataiporject/tmp/`
- Project checkouts under any other path or alternate project workspaces

Do not invent alternate project workspaces or move this project. The former
independent-clone/worktree workflow is deprecated and must not be reused.

## Testing and runtime isolation

All project tests and validation must be launched from
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`, including unit,
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
changes in this canonical repository and report the exact host-terminal Git
commands needed for staging, committing, and pushing. Those commands must run
from the canonical repository and name only files belonging to the task.

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
