---
name: start-project
description: Start a new software project with a small, testable plan and safe repository setup. Use when the user wants to create a new tool, app, service, CLI, or repository from an idea.
---

# Start Project

Turn an idea into a small first release without over-building.

## Process

1. Restate the product goal in one sentence.
2. Identify the smallest useful v0.1 and list what is explicitly out of scope.
3. Inspect the current directory and repository before creating files.
4. Choose the simplest suitable stack based on the task and existing repo conventions.
5. Define the public interface and the first testable user flows.
6. Create or update project guidance only when it captures durable rules that are not obvious from the code.
7. Implement in small vertical slices. Use the `tdd` skill when available and appropriate.
8. Run the repository's format, lint/static analysis, tests, and build commands.
9. Use `review-change` before declaring the work ready.
10. Use `safe-git` only when the user asks to commit, sync, or push.

## Completion criteria

- v0.1 scope is explicit.
- Core user flow works end to end.
- Automated checks pass.
- The built artifact is manually exercised when practical.
- README explains build, test, and usage.
- Known limitations are documented.
- No unrelated files are changed.
