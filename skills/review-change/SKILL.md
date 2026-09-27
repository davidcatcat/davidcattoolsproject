---
name: review-change
description: Review a code change for correctness, regressions, security, maintainability, and test coverage. Use before committing important changes or when the user asks to review a diff, patch, branch, or implementation.
---

# Review Change

Review the actual diff against the requested behavior and repository rules.

## Process

1. Establish the review base and inspect the complete diff.
2. Read applicable repository guidance and the originating requirement/spec.
3. Check correctness and edge cases.
4. Check regression risk and backwards compatibility.
5. Check security and privilege boundaries where relevant.
6. Check failure handling and observability.
7. Check tests for behavioral coverage rather than implementation coupling.
8. Look for accidental scope expansion, dead code, duplicated logic, and stale docs.
9. Run or verify the canonical checks when possible.
10. Report findings ordered by severity, with concrete file/line references when available.

If no material issue is found, say so directly and identify any testing or environment gaps that remain.

## Completion criteria

- Entire intended diff was reviewed.
- Requirement and repo rules were checked.
- Material risks are called out concretely.
- Validation status is known.
