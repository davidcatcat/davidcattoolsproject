---
name: qa-validation
description: Perform formal product or milestone QA through public/user-facing interfaces. Use when the user asks for QA, functional testing, acceptance testing, release validation, or a structured test matrix after implementation/review/bug fixes.
---

# QA Validation

Test the product as a user/system would use it. QA is evidence about behavior, not
another source-code review.

## Before testing

1. Pin the exact repository/release revision, configuration and environment.
2. Read the public contract, acceptance criteria, known limitations and already-closed regressions.
3. Define the QA matrix before seeing results. Mark each case required or optional and identify environment prerequisites.
4. Use isolated/synthetic test state where practical. Never point destructive tests at production or unrelated user data.

## QA buckets

Select the buckets relevant to the product/milestone:

- **Smoke:** startup/help/basic happy path and installation/package sanity.
- **Functional:** documented commands/options, input forms, output schemas and exit codes.
- **Failure/recovery:** malformed input, missing dependencies, provider/network/config errors, interruption and retry/recovery behavior promised by the contract.
- **State/concurrency:** repeated runs, parallel operations, persistence/capacity, cleanup/restart and stale state.
- **Security/privacy smoke:** verify promised redaction, permission/fail-closed behavior and absence of obvious secret leakage. Deep threat modeling/exploit review belongs to `security-review`.
- **Cross-environment:** supported Python/OS/runtime/provider combinations that are actually required.
- **Regression:** re-run permanent tests/reproducers for previously fixed user-visible bugs.

## Execution

1. Exercise public CLI/API/UI seams first; avoid relying on implementation-private calls as acceptance evidence.
2. Record exact command/input, environment, expected behavior, actual behavior, exit/status and safe artifacts.
3. Classify every case as:
   - `PASS`
   - `FAIL`
   - `BLOCKED` (required prerequisite unavailable)
   - `NOT-TESTED` (not executed; reason required)
4. A failure is a bug candidate, not automatically a confirmed bug. Produce a minimal public-interface reproducer and hand it to `bug-hunt`/triage before modifying production code.
5. After a confirmed bug is fixed, re-run the failed QA case plus affected regression/canonical coverage.
6. Keep coverage limits explicit. Passing QA proves the tested matrix, not absence of all bugs.

## Runtime-dependent QA

Use `validate-runtime` for VPN/provider/auth/session/reachability evidence and keep
reachability, authentication, inference and end-to-end behavior distinct. Mock evidence
cannot replace a required live acceptance case. For llmtool Mac + VPN development
dogfood, use [company-llm-delegation](../company-llm-delegation/SKILL.md) for bounded
QA-output clustering/summarization. That delegation cannot assign the authoritative
PASS/FAIL verdict or replace raw public-interface evidence and independent verification.

## Rules

- Do not change acceptance criteria after seeing failures.
- Do not mark BLOCKED/NOT-TESTED as PASS.
- Do not "fix while testing" without first preserving the failing reproducer/evidence.
- Do not broaden a QA run into penetration testing or architecture redesign; hand those concerns to `security-review`.
- Preserve unrelated data and restore only test state that the QA plan created.

## Completion criteria

- The matrix and required environments are documented.
- Every required case is PASS or has a clearly blocking unresolved defect/prerequisite.
- Failed cases have minimal reproducers and triage status.
- Fixed failures have regression evidence.
- Known limitations and not-tested areas are explicit.
- Final release/milestone conclusion is bounded to the tested revision and matrix.
