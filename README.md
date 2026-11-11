# davidcattoolsproject

Personal AI-assisted engineering tools and reusable Agent Skills.

## CI knowledge base

- [CI Knowledge Base](docs/ci/README.md) — 維運問題、GitHub Actions 用量、成本分析及驗證過的修復經驗。
- 處理 CI 事件請先查本知識庫；重複可用的結論依 [debug-ci Skill](skills/debug-ci/SKILL.md) 整理。原 Issue、PR、Run 與確切 SHA 保留為可追溯證據。

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
- `ai-team-delivery` — coordinate delegated AI product delivery, independent reviews, Mac operator handoffs, technical decisions, and safe merges without treating the requirements owner as the PM.
- `validate-runtime` — validate provider/runtime connectivity and end-to-end evidence with isolated test state.
- `company-llm-delegation` — route deterministic work away from LLMs and delegate only bounded permitted triage through llmtool to the configured company provider.
- `agy-direct-developer` — run one Owner-assigned Developer Task with AGY (Antigravity CLI): assignment gate, CLI invocation, write-scope checks and hand-back.
- `artifact-verification` — verify JSON schemas, path constraints, role constants, external IDs and evidence labels before publishing reports, runbooks or handoff artifacts.

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
~/.gemini/config/skills   # AGY (Antigravity CLI) global skills
```

Because they are symlinks, updating this repository with `git pull` also updates the installed skill contents.

The installer will not overwrite a real directory that already exists at a destination path.
Run from a linked worktree, it still links the canonical checkout, so the links
survive when the worktree is removed; a skill that exists only on the worktree's
branch is therefore not linked until it reaches the canonical checkout.

After adding a **new** skill directory, run the installer again on the Mac to create
its new links. An existing symlink tracks future repo updates; `git pull` alone
does not create a link for a newly introduced skill. Check local branch/dirty
state before syncing. The standalone transitional helper is documented in
[tools/ai-loop/README.md](tools/ai-loop/README.md).

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
Owner D6 (2026-10-19) supersedes the historical G7 two-fixed-root limit:
authorized local Developer Agents may create **multiple task-scoped linked
worktrees** under the verified dedicated workspace parent, with unique owned
branches and independent runtime state. The [safe-git policy](skills/safe-git/SKILL.md)
governs simple admission, task/PR-bound completion, automatic clean removal,
and preserve-on-uncertainty. PR opening is not completion; merged-and-accepted
tasks normally retire immediately after checking no live agent, dirty/ignored
data, unpublished commits or needed evidence. Historical roots require safe
ownership transfer; the canonical Integrator/F1 root, independent QA,
shared-ref coordination, provider privacy/F2 restrictions and Cloud exception
are unchanged. GitHub policy text does not itself activate a Mac runtime.

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

- [`tools/llmtool`](tools/llmtool/README.md) — Generic, portable LLM CLI and orchestration foundation. A0–A10 and A11a are DONE/merged; QG-S1 and QG-QA1 are PASS/CLOSED. A11b is IN PROGRESS: A11b.1 PR #32, A11b.2.1 preflight PR #43, A11b.2.2.1 synthetic containment PR #49, and A11b.2.2.2 **private disposable-fixture-only F1 Git ADD PR #84 (`46b17b13`) are DONE/merged**; general F2 real user-repository mutation remains NOT AUTHORIZED. Use [ai-team-delivery](skills/ai-team-delivery/SKILL.md) for user-impact defect triage and bounded repair. P1 failure-usage preservation is DONE/merged via PR #39 (`56799527`). A11c is NOT STARTED and now uses route-by-task evidence rather than a blanket company-first assumption. A12 remains future optional. A2.3 Anthropic HTTP API remains planned. See the [roadmap](tools/llmtool/ROADMAP.md) and [workflow](tools/llmtool/WORKFLOW.md).
- [`tools/nlf`](tools/nlf/README.md) — Natural Language Find: find files from a Chinese or English description (Go, read-only, no LLM).


## llmtool TUI UI/UX decision

The Owner accepted a **UI-only** design direction on 2026-10-22, inspired by Claude Code and Codex CLI: chat-first terminal layout, polished editing and slash-command picker, compact configurable status and optional read-only detail views. See the [new TUI decision meeting](docs/meetings/2026-10-22-llmtool-tui-ui-ux-decision.md), [A3.5 roadmap](tools/llmtool/ROADMAP.md) and [current vs planned UI support](tools/llmtool/README.md#a35-ux-1ux-2--opt-in-full-screen-chat-and-truthful-status). **No new Core, routing, background, Git or approval engine is authorized as TUI work.**

## Company LLM delegation evidence

Round-2/Round-3 dogfood changed the routing policy from a generic "cheap-first" idea to
**route-by-task evidence**:

- `test-triage`: **the benchmark pipeline is VALIDATED, not the ordinary skill invocation**.
  The positive L01 result used a ~48 KiB canonical synthetic test output, complete
  deterministic FAILURE-ID coverage, an exact excerpt selector (±2 lines, capped at
  `min(32 KiB, 20%)`), and direct injection of the selected raw excerpt so Codex did
  not re-read the full output. Under that pipeline, two independent orderings reduced
  Codex tokens by 39.82% and 37.55%. Plain `llmtool ask --skill test-triage` has no
  positive savings evidence; the small-evidence Round-1 B01 configuration measured
  −103.32%.
- `log-triage`: **EXPERIMENTAL for token-saving claims**. Round 3 produced one PASS and
  one `finish_reason=length` failure; reliability was insufficient for paired Codex work.
- `context-pack`: **EXPERIMENTAL for token-saving claims**. Round 3 replay showed clean
  provenance but only 3/7 target-record recall, with an instruction/gate semantic mismatch.

For any new company-LLM delegation route—or any attempt to turn the L01 research
pipeline into normal dogfood—run a **Company-only reliability Phase 0** first. Do not
spend Codex tokens on a paired benchmark until that exact route/pipeline passes its
pre-registered Company-only gate. P1 now preserves validated usage metadata on parseable
provider failures, but it does not turn failed model output into a successful result.

## Company LLM benchmark

Run the internal LLM benchmark from the repository root:

```bash
sh scripts/llm-benchmark.sh
```

Defaults:

- Gateway: `http://192.0.2.10:4000`
- Route: `gw`
- Long-context probes: approximately 8k, 16k, 32k, and 64k tokens
- Output: a Markdown report plus raw JSON responses under `reports/`

Override settings when needed:

```bash
BASE_URL=http://192.0.2.10:4000 MODEL=gw CONTEXT_SIZES="8000 16000 32000 64000" sh scripts/llm-benchmark.sh
```

The benchmark covers long-context retention, evidence-disciplined Kubernetes log diagnosis, Git diff review, cross-file root-cause analysis, and context compression/evidence preservation.
