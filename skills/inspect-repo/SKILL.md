---
name: inspect-repo
description: Inspect a repository before making changes. Use when entering an unfamiliar codebase, planning a change, investigating architecture, or when the user says to read README, schema, existing examples, or git history first.
---

# Inspect Repository

Build a concrete map before proposing changes.

## Process

1. Inspect repository status and top-level structure.
2. Read applicable guidance files such as `CLAUDE.md`, `AGENTS.md`, README, ROADMAP, CONTRIBUTING, current Task Contract, and local instructions. Distinguish current status from historical validation reports.
3. Locate the code, tests, schemas, configs, and workflows relevant to the task.
4. Find at least one existing analogous implementation when available.
5. Inspect recent history for the touched area when it can clarify intent.
6. Identify the public interface, data flow, invariants, validation gates, and existing diagnostics/observability paths. For executable workflows, locate error types/codes, log destinations, run/trace identifiers, redaction rules, retry/fallback policy, and approval boundaries before proposing changes.
7. Before an integration, live, or smoke test, verify the exact tested checkout: branch, HEAD,
   upstream and `origin/main`, ahead/behind counts, required feature-commit ancestry, and working
   tree state. Confirm the installed executable's path and version identify that checkout.
8. Record the tested revision/version before runtime validation. Never interpret a runtime failure
   as a product regression until the tested checkout/version is proven to contain the expected
   feature.
9. Identify which evidence gate the task actually is: implementation review, bug hunt, security review, QA, runtime validation, or Git publication. Do not silently substitute one for another.
10. Summarize only facts that materially affect the requested change.

Do not start implementation merely because a plausible file was found. Finish the relevant inspection first.

## Completion criteria

- Relevant guidance is known.
- Touched components and their relationships are identified.
- Existing conventions and analogous code are identified.
- Validation commands are known.
- When runtime validation applies, the tested revision/version is recorded first.
- Unknowns that could change the implementation are surfaced.
- For executable/runtime work, the existing error/log/trace and recovery path is known before implementation starts.
