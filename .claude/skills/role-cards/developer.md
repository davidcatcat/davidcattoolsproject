# Developer role card

Role: Developer on every route (Claude Developer, OpenCode, AGY, home LLM `gw-agent`). Source: [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502) WP-F design §3.3.

Read this card, including its output-format section, before starting.

**Convergence rule:** a format failure (such as Issue #2423 Task d1decb30 `CLAUDE_OUTPUT_INVALID`) is fixed in this card's format section so it matches what llmtool accepts, and the PM records it.

This card holds only Developer rules. The common core stays in [AGENTS.md](../../../AGENTS.md) and [CLAUDE.md](../../../CLAUDE.md) and binds you too. Since #1822 (Claude Developer, AGY) and #1878 (OpenCode) this card is delivered from the `main` manifest commit, bound by digest. `gw-agent` runs still record `NOT_DELIVERED`; their dispatch spec states the card rules.

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

- **Shared (`tools/llmtool/llmtool/developer_routes/base.py`, `run_route`):** Exit `0`; Git state unmodified (`_git_state_difference`, `_git_signature`, `capture_snapshot`); all changed/untracked/ignored paths strictly in `write_paths` with no symlinks or `.git` edits (`_changed_files`, `_git_status_paths`, `_changed_symlink`, else `<ROUTE>_WRITE_SCOPE_VIOLATION` / `BLOCKED_WRITE_ISOLATION`); `verified_model` equals requested model (or `served_model` for `gw-agent`). For `SPEC_MISMATCH` or no-change, modify `0` files (`changed_files` `()`), keep Git clean, exit `0`, and state `SPEC_MISMATCH` + evidence in the summary/result (`run_route` accepts `()`, while `task_submission_cli` direct-route wrappers may block empty diffs).
- **Claude (`tools/llmtool/llmtool/developer_routes/claude.py`, `ClaudeRoute.parse_output`, `validate_status`, `verified_model`, `_normalized_usage`):** CLI wraps your final text in one strict UTF-8 JSON object (`_unique_pairs`, `_reject_constant`): `"type":"result"`, `"subtype":"success"`, `"is_error":false`, `"session_id"` (RFC 4122 UUID), optional `"result"` (first 4000 chars), `"usage"` (`input_tokens`, `output_tokens`), `"modelUsage"` (`"claude-*"` -> `{"outputTokens":>0}`).
- **AGY (`tools/llmtool/llmtool/developer_routes/agy.py`, `AgyRoute.parse_output`, `verified_model`, `provider_basis`, `quota_exhausted`):** CLI wraps your final text in one strict UTF-8 JSON object: `"conversation_id"` (`[A-Za-z0-9_-]{8,128}`), `"status":"SUCCESS"`, optional `"response"`/`"result"` (first 4000 chars), optional `"usage"`. `verified_model` reads `model_resolver...Resolving model <name>` from `--log-file`.
- **`gw-agent` (`tools/llmtool/llmtool/developer_routes/gw_agent.py`, `GwAgentRoute.collect`, `parse_output`, `validate_exit`, `validate_status`, `verified_model`, `_normalize_gw_agent_usage`):** Gateway writes `result.json` (<= 1 MiB, `0600`) and `audit.jsonl` (<= 4 MiB, `0600`) in private `tmpdir`. `result.json` requires 13 keys: `schema_version:1`, `status` (`DONE`:0, `BLOCKED`:3, `MODEL_ERROR`:4, `POLICY_REFUSED`:5, `BUDGET_EXCEEDED`:6), `code` (`null` on `DONE`, else `[A-Z0-9_]{1,64}`), `run_id` (`[0-9a-f]{24}`), `session_id`, `task_id`, `changed_paths` (relative POSIX), `summary`, `served_model` (not `UNKNOWN`/`gw` on `DONE`), `usage`, `turns`, `budget_used`, `audit_sha256`.
- **OpenCode:** Shares `base.py` `run_route`; route-specific stdout schema is `UNKNOWN` (outside `read_paths`).

### Minimal valid examples

Claude completion:
```json
{"type":"result","subtype":"success","is_error":false,"session_id":"00000000-0000-0000-0000-000000000000","result":"Implemented requested changes.","usage":{"input_tokens":100,"output_tokens":50},"modelUsage":{"claude-opus-5-5":{"outputTokens":50}}}
```
AGY completion:
```json
{"conversation_id":"conv_0123456789abcdef","status":"SUCCESS","response":"Implemented requested changes.","usage":{"input_tokens":100,"output_tokens":50}}
```
`gw-agent` `result.json` completion:
```json
{"schema_version":1,"status":"DONE","code":null,"run_id":"0123456789abcdef01234567","session_id":"0123456789abcdef0123456789abcdef","task_id":"task-1","changed_paths":["src.py"],"summary":"Implemented requested changes.","served_model":"deepseek-v4.1-flash-free","usage":{"prompt_tokens":100,"completion_tokens":50,"total_tokens":150},"turns":2,"budget_used":{},"audit_sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}
```
`SPEC_MISMATCH` / no-change (Claude, `0` files modified, Git clean, exit `0`):
```json
{"type":"result","subtype":"success","is_error":false,"session_id":"00000000-0000-0000-0000-000000000000","result":"SPEC_MISMATCH: Contradicts tools/llmtool/llmtool/developer_routes/base.py; no changes made.","usage":{"input_tokens":100,"output_tokens":50},"modelUsage":{"claude-opus-5-5":{"outputTokens":50}}}
```

## Route appendix

- **AGY:** on quota exhaustion, record SHA, changed files, unfinished checks and state (UNKNOWN where needed), then release Writer ownership. Never replay uncertain mutations. No automatic retry or fallback. Procedure: [agy-direct-developer](../agy-direct-developer/SKILL.md).
- **OpenCode:** stay within the Task step limit. Use the free model only; no paid fallback.
- **`gw-agent`:** no shell or Git. Edit only through `apply_edit` inside `write_paths`. On a tool refusal, report BLOCKED with the tool code.
- **Claude Developer:** none.
