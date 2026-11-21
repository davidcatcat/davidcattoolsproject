# Developer role card

Role: Developer on every route (Claude Developer, OpenCode, AGY, home LLM `gw-agent`). Source: [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502) WP-F design §3.3.

Read this card, including its output-format section, before starting.

**Convergence rule:** a format failure (such as Issue #2423 Task d1decb30 `CLAUDE_OUTPUT_INVALID`) is fixed in this card's format section so it matches what llmtool accepts, and the PM records it.

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
- OpenCode also completes a qualified, actually-free pre-review from a different model family. If none is available, record the limitation and return BLOCKED. That pre-review is not the formal Reviewer gate.

## Repairs

- Fix every finding listed in the repair dispatch, or mark each one with the disposition the PM recorded for it (defer or dispute).
- Never weaken, skip or delete a test or check to clear a finding.

## Stop conditions

Stop and return BLOCKED, without retrying or rewording, on any of:

- a sensitive-screen refusal (report metadata only: rule, file, line, Task ID, refusal type);
- a needed write outside `write_paths`;
- a quota or free-limit refusal;
- unknown identity, D6 or task ownership.

## Hand-back record and output format

- Changed files and a short summary of each change.
- Self-review findings and how each was fixed.
- Checks not run or unfinished, with UNKNOWN where it applies. Tests were not run in the D6.
- Every acceptance item not met, with the reason.
- Claim no test, review or run you did not observe.

### Route execution contract and reply shapes

Backed by `tools/llmtool/llmtool/developer_routes/base.py` function `run_route`:
- **Shared execution contract (all routes):**
  - Process exit code must be 0 (`validate_exit`).
  - Git state must remain clean and unmodified (`_git_state_changed` and `_git_state_difference` check HEAD, branch, branch_ref, index, stash, refs, and git config).
  - All modified or newly created files in the worktree must strictly be in `write_paths` (`_changed_files` and `_git_status_paths`). Any edit outside `write_paths` raises `WRITE_SCOPE_VIOLATION`.
  - **No-change and SPEC_MISMATCH replies:** When returning `SPEC_MISMATCH` or when no changes are made, the Developer modifies 0 files in the worktree (`changed_files` is empty `()`), leaving all files untouched. This satisfies write scope validation.
- **Claude Developer route (`claude-developer`):**
  - Backed by `tools/llmtool/llmtool/developer_routes/claude.py` function `parse_output`.
  - Stdout must be valid JSON with unique keys and standard constants. Required fields:
    - `"type"`: must be `"result"`
    - `"subtype"`: must be `"success"` (to yield status `'DONE'`)
    - `"is_error"`: boolean `false`
    - `"session_id"`: canonical UUID string (RFC 4122)
    - `"result"`: string summary (first 4000 characters used as `summary`; holds the `SPEC_MISMATCH` statement and evidence when applicable)
    - `"usage"`: token mapping (`input_tokens`, `output_tokens`)
    - `"modelUsage"`: dictionary containing token usage for the requested model
- **Other routes (AGY, OpenCode, `gw-agent`):**
  - Backed by `tools/llmtool/llmtool/developer_routes/base.py` function `run_route`.
  - Specific CLI stdout parsing for AGY, OpenCode, and `gw-agent` is implemented in route adapter modules outside the authorized read paths of this task; per rule A5, their specific stdout schema is UNKNOWN.
  - All routes share the common execution rules enforced by `run_route`: exit code 0, writes strictly inside `write_paths` (or 0 files modified for no-change / SPEC_MISMATCH), no Git mutations, and adapter returning status `'DONE'`.

### Minimal valid Claude Developer completion example

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "session_id": "00000000-0000-0000-0000-000000000000",
  "result": "Implemented requested changes in authorized write paths.",
  "usage": {
    "input_tokens": 100,
    "output_tokens": 50
  },
  "modelUsage": {
    "claude-sonnet-5-5": {
      "outputTokens": 50
    }
  }
}
```

### Minimal valid SPEC_MISMATCH / no-change example

Worktree state: 0 files modified (working tree clean, `changed_files` empty).

Stdout summary / result:
```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "session_id": "00000000-0000-0000-0000-000000000000",
  "result": "SPEC_MISMATCH: The spec contradicts existing code in tools/llmtool/llmtool/developer_routes/base.py; no changes made.",
  "usage": {
    "input_tokens": 100,
    "output_tokens": 50
  },
  "modelUsage": {
    "claude-sonnet-5-5": {
      "outputTokens": 50
    }
  }
}
```

## Route appendix

- **AGY:** on quota exhaustion, record SHA, changed files, unfinished checks and state (UNKNOWN where needed), then release Writer ownership. Never replay uncertain mutations. No automatic retry or fallback. Procedure: [agy-direct-developer](../agy-direct-developer/SKILL.md).
- **OpenCode:** stay within the Task step limit. Use the free model only; no paid fallback.
- **`gw-agent`:** no shell or Git. Edit only through `apply_edit` inside `write_paths`. On a tool refusal, report BLOCKED with the tool code.
- **Claude Developer:** none.
