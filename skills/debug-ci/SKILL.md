---
name: debug-ci
description: Diagnose and minimally fix CI failures. Use when GitHub Actions, tests, builds, validation, lint, Terraform validation, or other pipeline checks fail.
---

# Debug CI

Find the first meaningful failure, reproduce it, and make the smallest correct fix.

## Process

1. Identify the exact failing workflow, job, step, command, and commit.
2. Read the first actionable error rather than treating downstream failures as independent causes. If the failing system provides a run/task ID, structured error code, trace, or persisted diagnostic record, correlate those first instead of relying only on the final console message.
3. Inspect the relevant schema/config/code plus an existing passing analogue.
4. Compare recent changes around the failing area.
5. Reproduce locally when the repository provides a canonical command.
6. Form a concrete hypothesis tied to evidence. Distinguish user/config/environment/provider/model/tool/workflow/validation/safety failures when the system exposes those layers; do not collapse every failure into a generic application bug.
7. Apply the minimum change that fixes the root cause.
8. Re-run the narrow failing check, then the broader canonical validation suite.
9. Review the final diff for accidental changes.
10. Use `safe-git` only if the user asks to commit or push.

## Rules

- Do not weaken tests, schemas, linters, or validation merely to make CI green.
- Do not delete failing coverage unless the user explicitly changes the requirement.
- Prefer a minimal patch over opportunistic refactoring during incident repair.

## Completion criteria

- Root cause is explained.
- The original failure is reproducible or otherwise evidenced.
- The targeted check passes after the fix.
- Canonical broader validation passes.
- Remaining uncertainty is explicit.
- Available run/trace evidence and the failure layer are recorded when the system supports them.
