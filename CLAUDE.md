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

## Engineering behavior

- Inspect before modifying unfamiliar code.
- Preserve unrelated local work.
- Prefer minimal, reversible changes.
- Tests and validation are part of implementation.
- Never weaken tests or validation just to get green status.
- Git synchronization and publishing should follow the `safe-git` skill.
- Force-push and destructive history rewriting require explicit user instruction.

## Milestone checkout boundary

For future Codex milestones in this repository, use one independent full clone per
milestone under `/Users/davidsun/git/davidcataiporject/tmp/`, containing its own
writable `.git` directory. Do not use `git worktree` or place clones inside the
primary human checkout. Follow the bootstrap and preservation rules in
[Isolated milestone execution](README.md#isolated-milestone-execution).
Complete tests/docs/commit/push in the clone, then stop for external ChatGPT review;
do not merge main or automatically begin the next milestone. Provider != Role != Agent.

## Updating skills

When a recurring workflow is observed, improve the relevant existing skill before creating a new overlapping one.
