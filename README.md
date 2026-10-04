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

- [`tools/llmtool`](tools/llmtool/README.md) — Generic, portable LLM CLI and orchestration foundation. A0–A10 mainline milestones and A11a.1–4 are completed and merged (A2.3 Anthropic API remains planned). A11a.4 persisted ask integration merged through [PR #19](https://github.com/davidcatcat/davidcattoolsproject/pull/19) (`2bade6a`); independent/external source review and post-merge main Python 3.9/3.14 CI passed. A11a.5 adds offline, read-only `runs` / `trace` on its feature branch; see [validation](reports/llmtool_a11a5_runs_trace_validation_20261004.md) for review/CI status. Overall A11a remains incomplete; A11a.6–7, A11b execution/approval/recovery, A11c integration/acceptance and optional A12 Telegram remain unstarted. See the [roadmap](tools/llmtool/ROADMAP.md).
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
