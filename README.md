# davidcattoolsproject

Personal AI-assisted engineering tools and reusable Agent Skills.

## Skills

This repository currently provides:

- `start-project` — turn a new idea into a small testable v0.1.
- `inspect-repo` — inspect an unfamiliar repository before changing it.
- `implement-feature` — implement a change through tests and final validation.
- `debug-ci` — diagnose CI failures and apply the smallest correct fix.
- `review-change` — review a diff for correctness, regression, security, and tests.
- `safe-git` — safely fetch, synchronize, commit, and push Git changes.
- `validate-runtime` — validate provider/runtime connectivity and end-to-end evidence with isolated test state.

Each skill is stored at:

```text
skills/<skill-name>/SKILL.md
```

## Install locally

From this repository:

```bash
chmod +x scripts/install-skills.sh
./scripts/install-skills.sh
```

The installer symlinks the skills into:

```text
~/.claude/skills
~/.agents/skills
```

Because they are symlinks, updating this repository with `git pull` also updates the installed skill contents.

The installer will not overwrite a real directory that already exists at a destination path.

## Recommended workflow

For a new project, start with the project goal and use `start-project`.

A typical engineering flow is:

```text
start-project
  -> inspect-repo
  -> implement-feature
  -> review-change
  -> safe-git
```

For failures in an existing project:

```text
inspect-repo
  -> debug-ci
  -> review-change
  -> safe-git
```

Skills are guidance for the coding agent, not a replacement for project-specific requirements. A project's own `AGENTS.md`, `CLAUDE.md`, tests, schemas, and source code remain the source of truth for that project.

## Canonical repository workflow

Local Mac work uses `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
When the user chooses Codex Cloud, use only the repository checkout configured for
that Cloud task; discover its actual root instead of assuming `/workspace` is a
Git repository. Verify repository identity, branch, HEAD and working-tree state.
This Cloud exception does not authorize worktrees, independent/milestone clones,
sibling/nested repositories, or alternate local Mac checkouts.

Follow the [llmtool development workflow](tools/llmtool/WORKFLOW.md) for Cloud development,
Mac-required verification, evidence handoff and merge gates. Complete independent
Cloud work before handing off checks that require the user's Mac. Mock/CI success
does not replace required live verification. Preserve unrelated user work and
stop at the milestone's review gate; do not automatically merge or start the next milestone.

If Git writes are blocked, leave changes in the authorized checkout and provide
exact commands for that environment; do not create another checkout.
Test-generated temporary files and the `codex-cli` provider's empty ephemeral
runtime cwd remain allowed security/test mechanisms, never project checkouts.
This development policy does not change llmtool provider capabilities or runtime isolation.

## Adding a new skill

Create:

```text
skills/<new-skill>/SKILL.md
```

Minimum frontmatter:

```yaml
---
name: new-skill
description: What this skill does and the concrete situations that should trigger it.
---
```

Keep the skill focused, define a clear process, and end with checkable completion criteria.

## Tools

- [`tools/llmtool`](tools/llmtool/README.md) — Generic, portable LLM CLI and orchestration foundation. A0–A10 mainline milestones and A11a.1–6 are merged (A2.3 Anthropic API remains planned). A11a.6 offline doctor/failure matrix and narrow M1/M3 persistence hardening merged via [PR #24](https://github.com/davidcatcat/davidcattoolsproject/pull/24) as `6b7809ace69cdcbe6030e94cd9ee816b6d22710e`; post-merge [CI 37213447952](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37213447952) passed Python 3.9/3.14. A11a.7 final acceptance is in progress on `feat/a11a7-final-acceptance-20261004`, pending external review and gate merge; overall **A11a NOT DONE**. See [final acceptance](reports/llmtool_a11a7_final_validation_20261004.md) for current evidence and deferred limits; original validation/review reports remain historical. A11b Task Contract execution gates, A11c integration and optional A12 Telegram remain NOT STARTED. See the [roadmap](tools/llmtool/ROADMAP.md) and [workflow](tools/llmtool/WORKFLOW.md).
- [`tools/nlf`](tools/nlf/README.md) — Natural Language Find: find files from a Chinese or English description (Go, read-only, no LLM).


## Company LLM benchmark

Run the internal LLM benchmark from the repository root:

```bash
sh scripts/llm-benchmark.sh
```

Defaults:

- Gateway: `http://10.29.7.58:4000`
- Route: `gw`
- Long-context probes: approximately 8k, 16k, 32k, and 64k tokens
- Output: a Markdown report plus raw JSON responses under `reports/`

Override settings when needed:

```bash
BASE_URL=http://10.29.7.58:4000 MODEL=gw CONTEXT_SIZES="8000 16000 32000 64000" sh scripts/llm-benchmark.sh
```

The benchmark covers long-context retention, evidence-disciplined Kubernetes log diagnosis, Git diff review, cross-file root-cause analysis, and context compression/evidence preservation.
