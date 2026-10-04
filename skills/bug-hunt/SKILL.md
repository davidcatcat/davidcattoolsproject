---
name: bug-hunt
description: Search an existing implementation for observable correctness, concurrency, state, resource, security, or error-handling defects. Use when the user asks to find bugs, audit for bugs, or perform a focused defect hunt before QA. Do not use as a synonym for ordinary code review.
---

# Bug Hunt

Find real defects, not a long list of preferences.

## Boundary

A bug hunt is narrower than general review and different from QA:

- **review-change** checks a diff against requirements and engineering quality;
- **bug-hunt** actively searches for observable incorrect behavior in the current implementation;
- **security-review** focuses on attacker-controlled paths, trust boundaries, and exploitable weaknesses;
- **qa-validation** exercises the product through public/user-facing interfaces.

Do not silently switch between these goals.

## Process

1. Establish the exact repository, branch/base, HEAD, working-tree state, task contract and current known limitations.
2. Read the relevant source, tests, contracts and prior findings. Treat historical reports as evidence for their recorded revision, not proof about the current HEAD.
3. Build a failure-oriented map: state transitions, error paths, concurrency, interruption, resource limits, malformed inputs, partial writes, cleanup, recovery and cross-feature interactions.
4. Search for observable failures such as crashes, wrong results, data loss/corruption, stale/inconsistent state, deadlock/race, resource leak, security/privacy bypass, wrong exit/error/status, unsafe mutation or contract violation.
5. For each candidate, reproduce before proposing a production change. Prefer deterministic focused tests/probes, fault injection, bounded subprocess interruption and isolated state.
6. Compare expected behavior with actual behavior and tie the expectation to the current contract/spec/public behavior.
7. Classify every finding:
   - `CONFIRMED BUG` — reproduced on the current reviewed revision.
   - `LIKELY BUG` — strong evidence, but one material reproduction/proof step is missing.
   - `THEORETICAL RISK` — plausible concern without demonstrated incorrect behavior.
   - `NOT A BUG / EXPECTED BEHAVIOR` — documented limitation or behavior consistent with the contract.
8. Do not modify production code during a read-only bug hunt. If the user separately authorizes fixes, hand confirmed findings to `implement-feature`: failing regression test first, minimal fix second, focused regression then canonical tests.
9. Re-run the relevant bug-hunt reproducer after the fix; do not accept "tests are green" as proof that the original defect is closed.

## Finding evidence

Each confirmed/likely finding should include:

- severity and confidence;
- file/function/location;
- trigger/preconditions;
- expected vs actual behavior;
- current reviewed revision;
- minimal reproducer or failing test;
- whether existing tests detect it;
- whether it is a regression;
- overlap with known/deferred findings.

No reproducer or concrete failure path normally means no production change.

## Rules

- Exclude style, naming, formatting, pure refactor preference and generic test-coverage suggestions unless they demonstrate a real failure.
- Do not inflate severity to make a report look useful.
- Known documented limitations are not "new bugs" unless the current contract is actually violated.
- Preserve unrelated user artifacts and test data.
- Do not broaden a focused bugfix into architecture work, QA, security redesign or the next milestone.

## Completion criteria

- Reviewed revision and scope are explicit.
- Confirmed bugs have reproduction evidence.
- Likely/theoretical items are clearly separated from confirmed defects.
- Known limitations checked but not violated are recorded as such.
- The report states `CONFIRMED BUG COUNT: N`.
- If fixes were separately authorized, each fixed confirmed bug has a permanent regression test and the original reproducer passes.
