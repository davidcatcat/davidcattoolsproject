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
7. Summarize only facts that materially affect the requested change.

Do not start implementation merely because a plausible file was found. Finish the relevant inspection first.

## Completion criteria

- Relevant guidance is known.
- Touched components and their relationships are identified.
- Existing conventions and analogous code are identified.
- Validation commands are known.
- Unknowns that could change the implementation are surfaced.
