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

For llmtool development on the actual Mac with company VPN, follow
[development-time cost-aware dogfood](tools/llmtool/WORKFLOW.md#development-time-cost-aware-dogfood-available-now).
Use deterministic evidence first, prefer `llmtool git --json` for the local read-only
Git snapshot it already covers, and use the company LLM only through llmtool for
bounded triage/summarization of permitted data. This engineering dogfood does not
authorize automatic routing, code mutation, security sign-off, merge approval, or
A11c production behavior.

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
- Provider != Profile != Role != Agent != Skill. Capability/role/model identity never grants execution authority.

## Repository workflows

`AGENTS.md` is Codex-specific guidance; `CLAUDE.md` is Claude-specific guidance.
Read the relevant reusable workflow in `skills/<name>/SKILL.md` before using it.
Use `inspect-repo` for investigation, `implement-feature` for implementation,
`debug-ci` for failing checks, `review-change` for ordinary diff review,
`bug-hunt` for reproducible defect discovery, `security-review` for threat/security
assessment, `qa-validation` for public-interface QA, `validate-runtime` for live
runtime evidence, and `safe-git` for Git operations. These evidence gates are not
interchangeable. Keep one shared workflow per skill; Codex interface metadata belongs
in the skill's `agents/openai.yaml`.

Documentation-only changes require documentation validation, not application
builds. Report checks that passed and any that could not run. Do not claim remote
synchronization from local tracking references without fetching.

## Quality-gate discipline

A Task Contract/milestone decides which gates are required. Ordinary code review does
not automatically satisfy bug hunt, formal security review, QA, or live/runtime validation.
When a gate finds a real defect, preserve its reproducer/evidence, fix through the normal
implementation workflow, and re-run the affected gate. Do not start the next milestone
while a required gate is unresolved.

For llmtool, QG-S1 passed with required A11b design controls and the QG-QA1
required blockers were closed before PR #31 merged to main. A11b.1 is now under
targeted external re-review in Draft PR #32. Do not start A11b.2, merge A11b.1,
or treat A11b/A11c as complete until their remaining review/merge gates are resolved.

## Default Cloud agent roles

For future Codex Cloud tasks, use Developer `gpt-6.1-sol` / high,
independent read-only Reviewer `gpt-6.1-sol` / high, and a bounded Git agent
`gpt-5.6-luna` / low when available. Reviewer replaces the previous Astra default.
Read [the role and handoff rules](tools/llmtool/WORKFLOW.md#default-codex-cloud-agent-roles--user-decision-2026-10-04).
Keep one Git writer, verify model availability, and preserve all existing review/merge gates.
