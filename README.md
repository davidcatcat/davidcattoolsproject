# davidcattoolsproject

Personal AI-assisted engineering tools and reusable Agent Skills.

## Skills

This repository currently provides:

- `start-project` — turn a new idea into a small testable v0.1.
- `inspect-repo` — inspect an unfamiliar repository before changing it.
- `implement-feature` — implement a change through tests and final validation.
- `debug-ci` — diagnose CI failures and apply the smallest correct fix.
- `review-change` — review a diff for correctness, regressions, maintainability, tests, and validation provenance.
- `bug-hunt` — search specifically for observable correctness/concurrency/security defects and require reproduction evidence before fixes.
- `security-review` — threat-model and security-review a change/system using evidence, trust boundaries, CWE/CVSS where applicable, and OWASP verification references.
- `qa-validation` — exercise public/user-facing behavior with explicit PASS/FAIL/BLOCKED/NOT-TESTED evidence.
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

Skills are deliberately separated by evidence type. A normal code review is not a
bug hunt; a bug hunt is not a security assessment; QA tests the product through its
public surface instead of re-reading the implementation.

Typical implementation flow:

```text
start-project (new projects only)
  -> inspect-repo
  -> implement-feature
  -> review-change
  -> safe-git
```

For important milestone/release gates, add only the gates required by the task:

```text
review-change
  -> bug-hunt          # observable defects; reproduce before fixing
  -> security-review   # when a security baseline/threat model or changed attack surface matters
  -> qa-validation     # public/user-facing functional and failure-path validation
  -> safe-git
```

For CI/runtime failures:

```text
inspect-repo
  -> debug-ci or validate-runtime
  -> review-change
  -> safe-git
```

Confirmed findings flow back through `implement-feature` with a failing regression
test/reproducer first, then the relevant gate is re-run. Do not force every small
documentation change through security review or QA; the Task Contract/milestone
defines which gates are required.

Skills are guidance for the coding agent, not a replacement for project-specific
requirements. A project's own `AGENTS.md`, `CLAUDE.md`, tests, schemas, source,
Task Contract and current roadmap remain authoritative.

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
skills/<new-skill>/agents/openai.yaml
```

`SKILL.md` is the shared workflow/source of truth. `agents/openai.yaml` contains
interface metadata only (display name, short description, default prompt) and must
not duplicate the workflow.

Minimum SKILL.md frontmatter:

```yaml
---
name: new-skill
description: What this skill does and the concrete situations that should trigger it.
---
```

Keep the skill focused, define a clear process, and end with checkable completion criteria.

## Tools

- [`tools/llmtool`](tools/llmtool/README.md) — Generic, portable LLM CLI and orchestration foundation. A0–A10 and A11a are DONE/merged; QG-S1 Security Baseline / Threat Model is next, then QG-QA1 Formal QA before A11b. A2.3 Anthropic HTTP API remains planned. See the [roadmap](tools/llmtool/ROADMAP.md) for the authoritative current milestone snapshot and the [workflow](tools/llmtool/WORKFLOW.md) for engineering gates.
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
