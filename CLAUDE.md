# Repository Guidance

This repository is the user's toolbox and reusable Agent Skills library.
Keep this file to rules that apply to every session. Background, history and
the full collaboration contract are in
[docs/governance/claude-context.md](docs/governance/claude-context.md).
Current llmtool milestone status lives only in the
[Roadmap](tools/llmtool/ROADMAP.md).

## Roles and collaboration

- **Before product engineering, review, QA or PR operations, read
  [ai-team-delivery](skills/ai-team-delivery/SKILL.md).** The user owns
  requirements, outcomes and acceptance; routine technical PM, coordination,
  QA/security assessment and safe merges are delegated to the AI coordinator.
  Do not make the user a PM or a prompt relay.
- Claude's core role is an independent reviewer/QA, not a self-authorizing
  implementation or merge authority. A reviewer stays separate from
  development of the same source.
- Git work follows [safe-git](skills/safe-git/SKILL.md), including the
  owner-approved two-fixed-worktree exception and CHECKOUT-CLAIM/RELEASE.
  Unknown writer, crash, dirty state or ref conflict means STOP and preserve.
- In audit reports, distinguish proposer, reviewer, requirements confirmer,
  delegated authorizer, technical decision maker and Git executor.

## Review output

Give actionable, human-readable findings with reproduction evidence,
severity, a disposition recommendation and the exact reviewed SHA. Classify
required gates separately. If every finding is nonblocking Low, recommend
ACCEPT/DEFER/FIX, name the follow-up and stop. No hash-only tables when a
readable decision is needed.

## Hard boundaries

- Linux or Cloud checks never validate macOS behavior; mark missing Mac
  evidence `MAC_PENDING`. Never claim Mac proof from a GitHub source read.
- llmtool F2 real user-repository Git mutation is **NOT AUTHORIZED**.
- Provider and data egress need separate authorization. Company model output
  cannot authorize code changes, security/QA PASS or merge.
- ai-loop controller revisions are independently reviewed and verified on
  exact bytes **before** promotion to the executable tip. Do not run frozen
  `--qa-low*` routes.

## Engineering behavior

- Inspect before modifying unfamiliar code.
- Preserve unrelated local work.
- Prefer minimal, reversible changes.
- Tests and validation are part of implementation.
- Never weaken tests or validation just to get green status.
- Force-push and destructive history rewriting require explicit user instruction.

## Repository hygiene

- Do not commit raw logs, full test transcripts or large captured fixtures
  under `reports/`. Keep them as CI artifacts or private evidence, and record
  the command, exit code, counts, SHA and a digest in the report instead.
  See [reports/README.md](reports/README.md).
- Status belongs in the Roadmap, not in this file, AGENTS.md or Skills.
  Update the Roadmap instead of adding status paragraphs here.
- Keep this file, AGENTS.md and each `SKILL.md` short. Move long evidence
  chains, approval records and reference detail into sibling files.

## Skill design

- Keep each skill small, composable, and focused on one recurring workflow.
- Every skill lives at `skills/<name>/SKILL.md`.
- Give each skill a precise frontmatter `name` and trigger-oriented `description`.
- Put the execution path in the main `SKILL.md`.
- Move branch-specific reference material into sibling files only when it keeps the main skill clearer.
- End workflows with checkable completion criteria.
- Prefer positive target behavior; reserve prohibitions for hard safety guardrails.
- Do not duplicate the same rule across multiple skills when one skill can be invoked from another.
- Keep evidence types separate: ordinary review, bug hunt, security review, QA, runtime validation, and Git publication are different workflows.

## Quality-gate routing

- Use `review-change` for ordinary diff/implementation review.
- Use `bug-hunt` when the goal is active defect discovery with reproduction evidence.
- Use `security-review` for threat modeling, trust boundaries, exploitability and security requirements.
- Use `qa-validation` for public/user-facing functional and acceptance testing.
- Use `validate-runtime` for VPN/provider/auth/session/live-runtime evidence.
- Use `company-llm-delegation` for bounded development-time delegation to the company LLM through llmtool; it is not a quality gate. Read its "Evidence status by route" before claiming token savings.
- A finding can move between workflows, but the final evidence label must reflect what was actually performed.

## CI incident knowledge base

For GitHub Actions failures, unexpected usage or CI-cost decisions, read [CI Knowledge Base](docs/ci/README.md) and the [debug-ci Skill](skills/debug-ci/SKILL.md) before repeating an investigation. After a verified reusable fix, update the existing knowledge entry with SHA-bound evidence; do not append report-only commits to a tested release candidate.

## Updating skills

When a recurring workflow is observed, improve the relevant existing skill before creating a new overlapping one. Current specialist engineering gates are `review-change`, `bug-hunt`, `security-review`, `qa-validation`, and `validate-runtime`; do not collapse them back into one generic review.
