---
name: implement-feature
description: Implement a feature or bug fix from an agreed requirement through tests and review. Use when the user asks to build, add, change, or fix code and expects a working result rather than only a plan.
---

# Implement Feature

Deliver a working change, not just code generation.

## Process

1. Use `inspect-repo` to understand the relevant area.
2. Restate the requested behavior and the smallest acceptance criteria.
3. Identify the public seam where behavior should be verified.
4. Implement in small vertical slices.
5. Add or update tests that verify behavior through public interfaces. For runtime/execution features, include failure-path tests: stable error classification, nonzero exit behavior, diagnostics/trace visibility, recovery/STOP policy, and secret redaction where applicable.
6. Run the narrow tests after each meaningful slice.
7. Run repository-wide required format, lint/static analysis, tests, and build checks.
8. Manually exercise the user-facing flow when practical.
9. Use `review-change` on the final diff.
10. Use `safe-git` only when the user asks to commit, synchronize, or push.

## Rules

- Match existing architecture and naming unless the requirement demands a change.
- Prefer the smallest correct implementation.
- Do not add speculative features.
- Do not weaken tests to obtain a green build.
- Preserve unrelated work.
- Provider or agent integration must not treat technical capability as permission to execute actions.
- A model or agent saying "done" is not completion evidence when deterministic validation is available; verify tests, expected artifacts, and repository/runtime state independently.
- Do not add execution before the affected workflow has a diagnosable failure path. New mutations should emit enough structured diagnostics/audit evidence to identify what ran, what failed, and why without exposing secrets.

## Completion criteria

- Requested behavior works.
- Relevant tests cover the behavior.
- Required quality gates pass.
- Final diff contains no unrelated changes.
- Known limitations and manual verification steps are reported.
