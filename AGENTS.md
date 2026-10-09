# Codex repository guidance

## AI team delivery and delegated authority

**Always read [ai-team-delivery](skills/ai-team-delivery/SKILL.md) at the
start of product development, review, QA handoff or Git publication.**
The user is the requirements owner/product acceptor, **not** the day-to-day
PM, source-code reviewer, prompt courier or Mac test diagnostician. ChatGPT,
when available as the authorized coordinator, owns technical planning,
Codex/Claude delegation, evidence reconciliation, routine Low-risk
dispositions and eligible PR merges under delegated scope. Codex is normally
the sole implementation writer; Claude (or a separately evidenced reviewer)
performs the independent review. The same agent's own checks do not become
independent QA or a security-review gate.

Do not ask the user to manage AI-to-AI prompts, repeatedly say "next step",
or perform routine Git/PM decisions. The user can act as the **physical Mac
operator** when the coordinator lacks that access; supply only a validated,
bounded instruction and diagnose its output yourself. Never claim that Mac,
provider or remote steps ran unless their evidence is present. The
owner reports Mac-local ai-loop now runs the latest control branch and
records invocations at `.git/ai-loop/run-history.log`, without a
per-commit `--approve` step. **AI is responsible for independent
controller review, exact-head CI/Mac gates and release BEFORE the
executable branch tip moves**, not the owner. This local behavior has
not been independently validated in this GitHub-only review; logging
alone proves neither code integrity nor safety. Respect the frozen
`--qa-low*` STOP and separate provider-egress permissions. See the
canonical [ai-team-delivery Skill](skills/ai-team-delivery/SKILL.md).

Escalate material product/risk/authority changes, not every nonblocking
technical Low. Distinguish who proposed a technical judgment, who accepted
the product direction, who authorized Git work and who actually executed
it. Apply [safe-git](skills/safe-git/SKILL.md) and the relevant
independent evidence gates before merging.

**llmtool product direction:** Read
[the single A11 roadmap](tools/llmtool/ROADMAP.md) and the canonical
[ai-team-delivery Skill](skills/ai-team-delivery/SKILL.md).
C0–C3 are legacy A-delivery labels; do not create another C
milestone/implementation stream. Continue coordinating Codex/Claude via
the AI-reviewed, automatically updated **temporary ai-loop** until A11c
real-world acceptance proves native llmtool parity. A11b.2.2.2 next
requires design and independent Security review (Issue #70);
real Git mutation is not authorized. Keep C1 excerpt export safety
(Issue #66) distinct from offline local executor design.

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
bounded triage/summarization of permitted data. Routing evidence is route/pipeline-
specific; read `skills/company-llm-delegation/SKILL.md` before making savings claims.
The positive L01 result belongs to its benchmark pipeline, not plain test-triage usage.
This engineering dogfood does not authorize automatic routing, code mutation,
security sign-off, merge approval, or A11c production behavior.

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
runtime evidence, `company-llm-delegation` for bounded Mac+VPN company-model dogfood,
and `safe-git` for Git operations. These evidence gates are not
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

For llmtool, A11a is DONE and QG-S1/QG-QA1 are PASS/CLOSED. A11b is
IN PROGRESS: A11b.1 data-only contract DONE/merged via PR #32,
A11b.2.1 read-only Git preflight DONE/merged via PR #43, and
A11b.2.2.1 synthetic containment DONE/merged via PR #49. The
A11b.2.2.2 product real Git ADD remains NOT STARTED / NOT AUTHORIZED,
pending separate design, Security review and implementation approval;
A11c is NOT STARTED and A12 is future optional. The current
[Roadmap](tools/llmtool/ROADMAP.md) is the status authority.

## Default Cloud agent roles

For future Codex Cloud tasks, use Developer `gpt-6.1-sol` / high,
independent read-only Reviewer `gpt-6.1-sol` / high, and a bounded Git agent
`gpt-5.6-luna` / low when available. Reviewer replaces the previous Astra default.
Read [the role and handoff rules](tools/llmtool/WORKFLOW.md#default-codex-cloud-agent-roles--user-decision-2026-10-04).
Keep one Git writer, verify model availability, and preserve all existing review/merge gates.
