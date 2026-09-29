---
name: inspect-repo
description: Inspect a repository before making changes. Use when entering an unfamiliar codebase, planning a change, investigating architecture, or when the user says to read README, schema, existing examples, or git history first.
---

# Inspect Repository

Build a concrete map before proposing changes.

## Process

1. Inspect repository status and top-level structure.
2. Read applicable guidance files such as `CLAUDE.md`, `AGENTS.md`, README, CONTRIBUTING, and local instructions.
3. Locate the code, tests, schemas, configs, and workflows relevant to the task.
4. Find at least one existing analogous implementation when available.
5. Inspect recent history for the touched area when it can clarify intent.
6. Identify the public interface, data flow, invariants, and validation gates.
7. Before an integration, live, or smoke test, verify the exact tested checkout: branch, HEAD,
   upstream and `origin/main`, ahead/behind counts, required feature-commit ancestry, and working
   tree state. Confirm the installed executable's path and version identify that checkout.
8. Record the tested revision/version before runtime validation. Never interpret a runtime failure
   as a product regression until the tested checkout/version is proven to contain the expected
   feature.
9. Summarize only facts that materially affect the requested change.

Do not start implementation merely because a plausible file was found. Finish the relevant inspection first.

## Completion criteria

- Relevant guidance is known.
- Touched components and their relationships are identified.
- Existing conventions and analogous code are identified.
- Validation commands are known.
- When runtime validation applies, the tested revision/version is recorded first.
- Unknowns that could change the implementation are surfaced.
