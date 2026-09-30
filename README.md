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

Skills are guidance for the coding agent, not a replacement for project-specific requirements. A project's own `CLAUDE.md`, tests, schemas, and source code remain the source of truth for that project.

## Isolated milestone execution

Future Codex milestones use a full independent Git clone under
`/Users/davidsun/git/davidcataiporject/tmp/`, with one milestone per clone.
Do not use `git worktree` for milestone execution. Each clone must contain its own
`.git` directory so Git can update its metadata within the writable checkout.
The primary human checkout is `/Users/davidsun/git/davidcataiporject/davidcattoolsproject/`;
do not modify it during milestone implementation or put milestone clones inside it.

For a future A2.6 milestone, bootstrap from a normal host terminal:

```sh
mkdir -p /Users/davidsun/git/davidcataiporject/tmp
cd /Users/davidsun/git/davidcataiporject/tmp
git clone git@github.com:davidcatcat/davidcattoolsproject.git davidcattoolsproject-a26
cd davidcattoolsproject-a26
git fetch origin
git rev-parse --show-toplevel --git-dir
git status --short --branch
git rev-parse origin/main
git ls-remote origin refs/heads/main
# Verify origin/main matches the remote main SHA before creating the branch.
git switch -c feat/a2-provider-integration origin/main
```

This is a future bootstrap example, not authorization to begin A2.6. Launch Codex
from inside the independent clone, with the clone and its `.git` directory writable.
Complete implementation, tests, documentation, commit, and push there. Normal
`git status`, `git add`, `git commit`, and `git push` must not require writes to the
primary checkout's Git metadata. Start each milestone from the latest verified
`origin/main`; if verification fails, resolve it before creating the milestone branch.

Preserve unrelated user work: do not use `git reset --hard`, `git clean -fd`, force
push, published-history rewriting, or stashing of unrelated work. Stop for external
ChatGPT review before merging main, and do not automatically start the next milestone.
Provider != Role != Agent.

A2.5 used a worktree whose source files were writable but whose Git metadata lived
under the primary repository's `.git/worktrees/`. Codex could edit documentation,
but Git could not create `index.lock` there to stage or commit it. This is an
execution-environment/worktree-layout limitation, not a Codex CLI provider failure.
See the [A2.5 validation report](reports/llmtool_a2_5_codex_cli_validation_20260930.md).

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

- [`tools/llmtool`](tools/llmtool/README.md) — LLMTool A1 core and A1.1 configure wizard are complete; live gateway smoke report is recorded. A2 provider expansion is next. Currently ships only `openai-compatible`; see the [A0–A11 roadmap](tools/llmtool/ROADMAP.md).
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
