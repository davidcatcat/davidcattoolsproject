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

`AGENTS.md` provides Codex-specific guidance; `CLAUDE.md` provides Claude-specific guidance.

All project development, tests, validation, documentation, and Git operations
must run from `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Do not create or use worktrees, independent or milestone clones, sibling or nested
repositories, or alternate project checkouts. The previous isolated project
checkout workflow is deprecated and must not be reused. See [AGENTS.md](AGENTS.md).

If sandbox permissions block `.git` writes, leave changes in the canonical
repository and report exact host-terminal staging/commit/push commands; do not
create another checkout. Preserve unrelated user work. Stop for external review
when required; do not automatically start A2.6 or merge main.

All unit, targeted, canonical, smoke, provider integration, regression, Git, and
documentation validation must be launched from the canonical repository.
Test-generated temporary files are allowed only when required by the test/runtime.
The `codex-cli` provider's ephemeral cwd under `/tmp` or `/var/tmp` remains a
runtime security mechanism, not a project checkout. It must contain no Git clone,
worktree, or copy of this repository. This policy does not change provider behavior.

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

- [`tools/llmtool`](tools/llmtool/README.md) — Generic, portable LLM CLI with four provider transports. A0–A6 are merged. A7 implementation/review remains on its separate, unmerged feature branch; the A0–A7 review bugfix branch is also unmerged. Anthropic API remains planned. See the [A0–A11 roadmap](tools/llmtool/ROADMAP.md).
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
