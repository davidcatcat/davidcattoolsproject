# Repository Guidance

This repository is the user's toolbox and reusable Agent Skills library.

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

## Engineering behavior

- Inspect before modifying unfamiliar code.
- Preserve unrelated local work.
- Prefer minimal, reversible changes.
- Tests and validation are part of implementation.
- Never weaken tests or validation just to get green status.
- Git synchronization and publishing should follow the `safe-git` skill.
- Force-push and destructive history rewriting require explicit user instruction.

## Quality-gate routing

- Use `review-change` for ordinary diff/implementation review.
- Use `bug-hunt` when the goal is active defect discovery with reproduction evidence.
- Use `security-review` for threat modeling, trust boundaries, exploitability and security requirements.
- Use `qa-validation` for public/user-facing functional and acceptance testing.
- Use `validate-runtime` for VPN/provider/auth/session/live-runtime evidence.
- Use `company-llm-delegation` for bounded development-time delegation to the company LLM through llmtool; it is not a quality gate.
- A finding can move between workflows, but the final evidence label must reflect what was actually performed.

For llmtool's current roadmap, A11a is DONE; QG-S1 and QG-QA1 are PASS/CLOSED
per their merged reports and formal QA round-2 APPROVE. A11b is IN PROGRESS:
A11b.1 Typed Action & Approval Contract is DONE/merged via PR #32 as a data-only
slice, A11b.2 is NOT STARTED, and execution remains unavailable. QG-S1 future
controls remain required. A11c is NOT STARTED and A12 is future optional.

On the actual Mac with company VPN, use `company-llm-delegation` for the
cost-aware dogfood path: deterministic evidence first, bounded permitted company
LLM triage through llmtool second, and strong verifier judgement last. Routing evidence
is route/pipeline-specific; read that skill's "Evidence status by route" before assuming
a token-saving claim. In particular, the L01 benchmark pipeline—not plain
`test-triage` invocation—is what has positive savings evidence. Company model output
cannot authorize code changes, security/QA PASS or merge.

## Updating skills

When a recurring workflow is observed, improve the relevant existing skill before creating a new overlapping one. Current specialist engineering gates are `review-change`, `bug-hunt`, `security-review`, `qa-validation`, and `validate-runtime`; do not collapse them back into one generic review.
