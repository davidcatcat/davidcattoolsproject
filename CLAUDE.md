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
- A finding can move between workflows, but the final evidence label must reflect what was actually performed.

For llmtool's current roadmap, A11a is DONE. QG-S1 Security Baseline / Threat Model is
the next gate, followed by QG-QA1 Formal A11a QA; A11b does not start before both gates pass.

## Updating skills

When a recurring workflow is observed, improve the relevant existing skill before creating a new overlapping one. Current specialist engineering gates are `review-change`, `bug-hunt`, `security-review`, `qa-validation`, and `validate-runtime`; do not collapse them back into one generic review.
