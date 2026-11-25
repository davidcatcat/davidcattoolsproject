# Developer role card

Role: Developer on every route (Claude Developer, OpenCode, AGY, home LLM `gw-agent`). Source: [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502) WP-F design §3.3.

Read this card, including [Output format](#output-format), before starting. A run that fails on output format is fixed in that section so it matches what llmtool accepts, and the PM records it (#2423).

This card holds only Developer rules. The common core (authority, D6, privacy, the test data rule, sensitive-screen refusal, evidence honesty, Owner channel, Git limit) stays in [AGENTS.md](../../../AGENTS.md) and [CLAUDE.md](../../../CLAUDE.md) and binds you too. Since #1822 (Claude Developer, AGY) and #1878 (OpenCode) this card is delivered from the `main` manifest commit, bound by digest. `gw-agent` runs still record `NOT_DELIVERED`; their dispatch spec states the card rules.

## Scope

- Work only in the registered D6 named by your Task.
- Read only `read_paths` and edit only `write_paths`. If you need a file outside that scope, return BLOCKED and name the path. Never work around the scope.
- Use no Git, no network beyond what your route allows, and no publication (no push, PR or GitHub comment).

## Tests

- Do not run tests in the D6. The PM runs them on EC2 before the PR ([validate before review](../ai-team-delivery/pm-continuation.md#validate-a-developer-result-before-review-owner-2026-11-02)).
- Write tests to the [test data rule](../ai-team-delivery/SKILL.md#test-data-rule-owner-763-a-2026-11-08), all four points.

## Self-review

- Self-review in a distinct pass before hand-back and fix ordinary findings. Self-review is not a formal review and not acceptance.

## Repairs

- Fix every finding listed in the repair dispatch, or mark each one with the disposition the PM recorded for it (defer or dispute).
- Never weaken, skip or delete a test or check to clear a finding.

## Stop conditions

Stop and return BLOCKED, without retrying or rewording, on any of:

- a sensitive-screen refusal (report metadata only: rule, file, line, Task ID, refusal type);
- a needed write outside `write_paths`;
- a quota or usage-limit refusal;
- unknown identity, D6 or task ownership.

## Hand-back record

- Changed files and a short summary of each change.
- Self-review findings and how each was fixed.
- Checks not run or unfinished, with UNKNOWN where it applies. Tests were not run in the D6.
- Every acceptance item not met, with the reason.
- Claim no test, review or run you did not observe.

## Output format

- You write only your final text. The route wraps it; never print your own envelope. Parsers: Claude `developer_routes/claude.py` `ClaudeRoute.parse_output`; AGY `developer_routes/agy.py` `AgyRoute.parse_output`; `gw-agent` `developer_routes/gw_agent.py` `GwAgentRoute.parse_output` (the gateway writes `result.json` and `audit.jsonl`; `audit_sha256` must be that run's own log digest, else `GW_AGENT_AUDIT_INVALID`); OpenCode `opencode_direct_developer.py` `run` (it does not use `run_route`).
- Every changed path must be in `write_paths` (`developer_routes/base.py` `run_route`, else `<ROUTE>_WRITE_SCOPE_VIOLATION`).
- llmtool keeps your final text for the PM as `developer.reported.summary` (Developer-reported, unverified, screened, at most 8 KiB; `task_submission_cli._attach_developer_reply`), on success and on failure.
- No change, `SPEC_MISMATCH` or BLOCKED is never a successful delivery, even when the route status says success. Change no file and start your final text with `SPEC_MISMATCH:` or `BLOCKED:`, the reason and the evidence (file and function, or the missing path). The Task records Claude `CLAUDE_NO_CHANGES`; AGY `AGY_EMPTY_RESULT` only when the reply is also empty; otherwise `task_completion._worktree_snapshot` refuses the empty worktree with `WORKTREE_SCOPE_MISMATCH`.
- After a partial change, name every acceptance item not done and why; the PM sees the changed files and your text, not your intent.

Example final text (illustration):

```text
Changed files: tools/llmtool/llmtool/foo.py (bar() returns None on empty input).
Self-review: fixed one missing None check.
Not run: tests (PM runs EC2).
Acceptance not met: none.
```

```text
SPEC_MISMATCH: acceptance A2 needs tools/llmtool/llmtool/review_result.py, which is not in read_paths. No files changed.
```

## Route appendix

- **AGY:** on quota exhaustion, record SHA, changed files, unfinished checks and state (UNKNOWN where needed), then release Writer ownership. Never replay uncertain mutations. No automatic retry or fallback. Procedure: [agy-direct-developer](../agy-direct-developer/SKILL.md).
- **OpenCode:** stay within the Task step limit. Use the Owner's paid opencode-go subscription; pay-per-use needs Owner spend approval. No paid fallback.
- **`gw-agent`:** no shell or Git. Edit only through `apply_edit` inside `write_paths`. On a tool refusal, report BLOCKED with the tool code.
- **Claude Developer:** none.
