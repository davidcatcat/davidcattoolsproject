# #2302 inventory: per-model allow-list gating

Read-only inventory and split proposal for Owner #2302. This is not a review or acceptance. Source: main at dd18d0fd9534fd536bc0a2c976fd5748bdcdb570. Unless noted otherwise, all paths are under `tools/llmtool/`. This note changes no code; it records call-site facts, findings and a P1-P6 split so the later Developers can work from the repository.

## 1. Gate core

- `developer_routes/__init__.py` `46-102` performs format and family checks. Keep these as provider-format validation, not as model-list membership.
- `_role_models` `118-137` treats any role entry whose model list length is not one (`len != 1`) as invalid.
- `_allowed_models_ok` `140-153` requires exactly one allowed value and that value must equal `default_model`.
- `check_route_model` `170-184` gates a route model.
- `check_registry_role_model` `187-221` gates a registry role model.
- `developer-routes.json`: each route `default_model` is its sole `allowed_models` entry, and `roles.*` entries have only `allowed_models` (no `default_model`).

## 2. Enforcement points

| File | Lines | Role |
| --- | --- | --- |
| `role_config.py` | `281-314`, `317-429` | `check_role_model` validates a role model; `resolve_role` resolves the effective model in precedence order CLI > env `LLMTOOL_ROLE_<ROLE>_MODEL` > user `roles.<role>.model` > repo default. |
| `owner_override.py` | `42` | Owner override path for a role model. |
| `task_submission_cli.py` | `4642-4653`, `4675`, `4691`, `4696`, `4724`, `4736-4760`, `6247-6252` | Developer delegate model selection and failure persistence on the task delegate paths. |
| `review_dispatch.py` | `139-164`, `320-322`, `682-684`, `723-725` | Reviewer model selection for review dispatch and review runs. |
| `review_authority.py` | `35-47` | Review authority check tied to the reviewer model. |
| `providers/claude_cli.py` | `403-407` | Claude provider model selection. |
| `high_debug.py` | `997-1021`, `1136` | High Debug model selection; line `1136` defaults to `gpt-6.1-sol`. |
| `tools/llmtool/scripts/claude_bg_dispatch.py` | `168-179`, `431-439` | Background Claude dispatch model selection. |
| `roles_cli.py` | `25-54` | `llmtool roles` output; no model source is shown. |
| `task_advance.py` | `2524-2525` | Uses `ROLE_MODEL_NOT_ALLOWED` as the Reviewer-fallback trigger. |

## 3. Key findings

1. The `owner_override` path is dead in production. The delegate call sites at `task_submission_cli.py` `4937`, `5401`, `5882`, `6252` pass no `owner_override` and no `cli=`, and `task delegate` has no `--model`, so only env and user config reach Developer routes.
2. Repair, advance and drive re-resolve the model from current config (`review_repair.py` `195` -> `run_task_delegate`), so a config change mid-Task changes a repair's model. #2302 wants a started Task to keep its model.
3. `task_completion.py` `2236-2279`: claude and codex families come from the route name; opencode and gw-agent unknown models fall back to `other`, so an unknown family passes as cross-family. The Reviewer family comes from the reviewer key, and the review claim stores no reviewer model.
4. Keeping the refusal code name `ROLE_MODEL_NOT_ALLOWED` avoids touching `task_advance.py`.

## 4. Model recording today

OpenCode is not in `role_config.ROUTE_ROLES` (`18-24`). Its model comes from the resolved role model, else the `opencode-developer` provider profile, else `opencode_direct_developer.MODEL` (line `30`). The `developer-routes.json` `opencode.default_model` is unused at dispatch and there is no observed-model check. AGY and Claude observed model is checked in `developer_routes/base.py` `1228-1234`. High Debug already records `model_requested` and `model_observed`.

## 5. Guidance drift

- `guidance_drift_check.py` (in the `tools/llmtool` scripts directory) `420-470` raises `MODEL_NOT_ALLOWED`, and schemas `189-198` and `213-217` require `allowed_models`.
- `tests/test_guidance_drift_check.py` `271` pins the `CLAUDE.md` line 36 AGY model to `routes.agy.default_model`.
- `CLAUDE.md`, `AGENTS.md` and `.claude/skills/**` are rule files in `sensitive-paths.json`, so a `CLAUDE.md` PR is a rule-file PR.

## 6. Proposed split

| PR | Files | Behaviour | Rule files | Order |
| --- | --- | --- | --- | --- |
| P1 | `guidance_drift_check.py` (tools/llmtool scripts directory) plus drift and caps tests | Drop doc-model allow-list coupling; make `allowed_models` optional; accept `roles.*.default_model`; drop `tests/test_guidance_drift_check.py` test `271`. | No | First; parallel |
| P2 | `developer_routes/__init__.py`, `developer-routes.json`, `role_config.py`, `owner_override.py` | Format and family checks only; no list membership. | Yes | After P1 |
| P3 | `task_submission_cli.py`, `developer_routes/agy.py`, `developer_routes/claude.py`, `opencode_direct_developer.py` | `task delegate --model` is one-shot and never persisted; a repair keeps the recorded model; `MODEL_UNSUPPORTED` with no fallback; OpenCode default from the route. | Yes | Serial |
| P4 | `task_store.py`, `task_submission_cli.py`, `task_completion.py`, `review_dispatch.py`, `review_result.py` | Record requested, source, observed and family; an unknown family is `UNKNOWN` and gets no formal PASS. | Yes | Serial after P3 |
| P5 | `roles_cli.py` | `llmtool roles` shows model source, default and last observed model. | No | Parallel |
| P6 | `CLAUDE.md` line 36, `guidance-drift.json` hash, remove deprecated `allowed_models` | Update the rule-file model line, its pinned hash, and remove deprecated `allowed_models`. | Yes | After P1 and P2 |

## 7. CLAUDE.md hash

`guidance-drift.json` pins the sha256 of the `CLAUDE.md` bytes. Claude Developer cannot run commands and gw-agent has no shell, so either the PM computes the sha256 read-only on the D6 file at the result SHA, or the repair copies the `HASH_STALE` digest from the EC2 `test_real_repository_guidance_is_current` output. Keep the `CLAUDE.md` edit and its hash in one Task.

## 8. Unchanged by every PR

- worker-dispatch `PAUSED`/`ALLOWED` gate;
- D6 and write scope;
- OpenCode free-only;
- gw-agent data classes and budget;
- credential isolation;
- Codex security plus formal review plus EC2 plus CI for each PR.
