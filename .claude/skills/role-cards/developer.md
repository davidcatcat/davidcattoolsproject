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
  - Process exit code must be `0` (`code == 0`, checked after `validate_exit`, `quota_exhausted`, `validate_status`, and `verified_model`).
  - Git state must remain clean and unmodified (`_git_state_difference`, `_git_signature`, and `capture_snapshot` check `head`, `branch`, `branch_ref`, `git_dir`, `index`, `stash`, `pointer`, `config_worktree`, `config_common`, and `refs`).
  - All modified, newly created, or untracked/ignored files in the worktree must strictly be in `write_paths`, with no symlink changes (`_changed_files`, `_git_status_paths`, and `_changed_symlink`). Any edit outside `write_paths` raises `<ROUTE>_WRITE_SCOPE_VIOLATION`.
  - Model verification (`verified_model`) must return a non-`None` string equal to the requested model (`value['model']`).
  - **No-change and `SPEC_MISMATCH` replies:** When returning `SPEC_MISMATCH` or when no changes are made, the Developer modifies `0` files in the worktree (`changed_files` is empty `()`), leaves Git state clean, exits `0` with route success status, and puts the `SPEC_MISMATCH` statement and evidence (or no-change reason) in the summary/result field.

- **Claude Developer route (`claude-developer`):**
  - Backed by `tools/llmtool/llmtool/developer_routes/claude.py` functions `ClaudeRoute.parse_output`, `ClaudeRoute.validate_status`, `ClaudeRoute.verified_model`, and `ClaudeRoute._normalized_usage`.
  - Stdout must be a single UTF-8 JSON object with unique keys and standard constants (`_unique_pairs`, `_reject_constant`). Required fields:
    - `"type"`: `"result"`
    - `"subtype"`: string; must be `"success"` (with `"is_error": false`) to yield status `'DONE'`
    - `"is_error"`: boolean `false`
    - `"session_id"`: canonical RFC 4122 UUID string (`_is_canonical_uuid`)
    - `"result"`: string summary (optional in `parse_output`, defaults to `""`; first 4000 characters used as `summary`; holds the `SPEC_MISMATCH` statement and evidence when applicable)
    - `"usage"`: dict with non-negative int `"input_tokens"` and `"output_tokens"` (optional `"cache_read_input_tokens"`, `"cache_creation_input_tokens"`) for `CLI_USAGE` evidence
    - `"modelUsage"`: non-empty dict mapping `"claude-*"` model names to dicts with positive int `"outputTokens"` (checked by `verified_model`)

- **AGY Developer route (`agy`):**
  - Backed by `tools/llmtool/llmtool/developer_routes/agy.py` functions `AgyRoute.parse_output`, `AgyRoute.verified_model`, `AgyRoute.provider_basis`, and `AgyRoute.quota_exhausted`.
  - Stdout must be a single UTF-8 JSON object with unique keys and standard constants. Required fields:
    - `"conversation_id"`: string matching `[A-Za-z0-9_-]{8,128}`
    - `"status"`: string; `"SUCCESS"` for normal completion (`provider_basis` returns `'AGY_STATUS'` only when `status == 'SUCCESS'` and `conversation_id` is valid; `"RESOURCE_EXHAUSTED"` or quota regex in `status`/`error`/stderr triggers `AGY_QUOTA_EXHAUSTED`)
    - `"response"` or `"result"`: optional string (`response` takes precedence over `result`; first 4000 characters used as `summary`)
    - `"usage"`: optional dict of `str -> int` entries
  - Model verification (`verified_model`) reads `model_resolver...Resolving model <name>` from the CLI `--log-file` (`log_text`), not stdout JSON.

- **Home LLM `gw-agent` route (`gw`):**
  - Backed by `tools/llmtool/llmtool/developer_routes/gw_agent.py` functions `GwAgentRoute.collect`, `GwAgentRoute.parse_output`, `GwAgentRoute.validate_exit`, `GwAgentRoute.validate_status`, `GwAgentRoute.verified_model`, and `_normalize_gw_agent_usage`.
  - `gw-agent` does not parse CLI stdout JSON; `collect` reads `result.json` (<= 1 MiB, mode `0600`) and `audit.jsonl` (<= 4 MiB, mode `0600`) from the private `tmpdir`.
  - `result.json` must be a strict JSON object with unique keys and standard constants, containing all 13 required keys:
    - `"schema_version"`: `1` (int)
    - `"status"`: `"DONE"` (exit `0`), `"BLOCKED"` (exit `3`), `"MODEL_ERROR"` (exit `4`), `"POLICY_REFUSED"` (exit `5`), or `"BUDGET_EXCEEDED"` (exit `6`); process exit code must match (`validate_exit`)
    - `"code"`: `null` when `status == "DONE"`; non-empty string matching `[A-Z0-9_]{1,64}` when `status != "DONE"`
    - `"run_id"`: 24-char lowercase hex string (`[0-9a-f]{24}`)
    - `"session_id"`: string or `null`
    - `"task_id"`: string matching `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` and equal to the requested `task['task_id']`
    - `"changed_paths"`: list of relative POSIX path strings (no leading `/`, `\`, or `.`/`..`/empty segments)
    - `"summary"`: string (first 4000 characters used as `summary`)
    - `"served_model"`: string matching `[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}`, not `"UNKNOWN"` and not `"gw"` when `status == "DONE"`
    - `"usage"`: dict (either `{"prompt_tokens", "completion_tokens", "total_tokens"}` or `{"status": "REPORTED", "input_tokens", "output_tokens"}` with non-negative ints `<= 50000000` for `GATEWAY_USAGE`)
    - `"turns"`: int
    - `"budget_used"`: dict or `null`
    - `"audit_sha256"`: 64-char lowercase hex string matching `audit.verify(audit.jsonl)`

- **OpenCode Developer route (`opencode`):**
  - Shares the common execution contract enforced by `tools/llmtool/llmtool/developer_routes/base.py` function `run_route`.
  - Route-specific stdout/output parsing for OpenCode is implemented in modules outside the authorized `read_paths` of this Task, so its route-specific stdout schema is `UNKNOWN`.

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

### Minimal valid AGY Developer completion example

```json
{
  "conversation_id": "conv_0123456789abcdef",
  "status": "SUCCESS",
  "response": "Implemented requested changes in authorized write paths.",
  "usage": {
    "input_tokens": 100,
    "output_tokens": 50
  }
}
```

### Minimal valid `gw-agent` `result.json` completion example

```json
{
  "schema_version": 1,
  "status": "DONE",
  "code": null,
  "run_id": "0123456789abcdef01234567",
  "session_id": "0123456789abcdef0123456789abcdef",
  "task_id": "task-1",
  "changed_paths": [],
  "summary": "Implemented requested changes in authorized write paths.",
  "served_model": "qwen3-coder-30b",
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "total_tokens": 150
  },
  "turns": 2,
  "budget_used": {},
  "audit_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

### Minimal valid SPEC_MISMATCH / no-change example (Claude Developer)

Worktree state: `0` files modified (working tree clean, `changed_files` empty `()`), exit code `0`.

Stdout JSON:
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
