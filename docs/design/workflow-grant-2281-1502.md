# Workflow grant investigation (#2281 / #1502 item 1a)

Status: read-only investigation. No code or test change is made by this note.

Scope of evidence: only the files named in this Task's `read_paths` were read. Statements below are limited to those files. Checks in files outside that list are marked UNKNOWN and named where relevant.

## Finding in one sentence

In the inspected files, the only check that refuses a `.github/workflows` path is `action_contract._path` (`tools/llmtool/llmtool/action_contract.py`). It is reached for OpenCode Developer read and write paths through `tools/llmtool/llmtool/opencode_worker.py` and `tools/llmtool/llmtool/opencode_direct_developer.py`. The AGY, Claude and home LLM (`gw-agent`) route adapters and the shared write boundary inspected here do **not** implement that refusal. Any refusal they experience would therefore have to come from a layer outside the read scope (Task submission/Store, sensitive-path screening, or the external gateway), and that layer is UNKNOWN here.

## Read paths

| Check (file, function) | What it does with `.github/workflows` | Routes that reach it | Error code |
| --- | --- | --- | --- |
| `tools/llmtool/llmtool/action_contract.py`, `_path` (called from `tools/llmtool/llmtool/opencode_worker.py`, `_validate`) | Refuses when the first two path components are `.github` and `workflows`, compared with `casefold()`. This is a lexical check only. | OpenCode trusted-host worker (`run_worker`, `preflight`) for `job.read_paths` and `job.write_paths`. | `LLMToolError('action contract invalid; details omitted')` from `_invalid`, mapped by `reject` to `OPENCODE_SCOPE_INVALID` (`WorkerError.worker_code`). |
| `tools/llmtool/llmtool/action_contract.py`, `_path` (called from `tools/llmtool/llmtool/opencode_direct_developer.py`, `_valid_paths`) | Same lexical refusal. `_valid_paths` validates every read path with `_path` and requires the write set to be a subset of the read set. | OpenCode direct Developer CLI (`opencode_direct_developer.run`) for read paths; a `.github/workflows` write path is refused either directly through the matching read path or by the read/subset rule. | `DirectOpenCodeError('OPENCODE_SCOPE_INVALID')` (`direct_opencode_code`). |
| `tools/llmtool/llmtool/action_contract.py`, `_path` (called from `tools/llmtool/llmtool/task_lifecycle.py`, `AgentBinding.__post_init__`) | Applies the same first-two-components rule to `root`, the `common_dir` prefix and `branch`. It catches a relative `branch` such as `.github/workflows`, but not a `.github/workflows` directory nested under a normal absolute `root` (for example `/Users/.../repo/.github/workflows`), because there the first components are `Users`, not `.github`. This is an F2 task-binding check, not a Developer Task read-path list check. | Task lifecycle/agent binding construction; indirectly any caller that constructs `AgentBinding` (including `tools/llmtool/llmtool/developer_session_intake.py`). It is not reached by the Developer read-path list in the route adapters. | `LLMToolError('action contract invalid; details omitted')`. |
| `tools/llmtool/llmtool/developer_session_intake.py`, `_paths` | Does **not** refuse `.github/workflows`. It checks type, sort/uniqueness, max length, no absolute path, no backslash, no empty/`.`/`..` component, and no control characters. | `prepare_next` for a next Developer task's `read_paths` and `write_paths`. | `INTAKE_SCOPE_INVALID` when `_paths` fails or when `write_paths` is not a subset of `read_paths`. |
| `tools/llmtool/llmtool/developer_routes/base.py`, `DeveloperRoute.validate_request` | Does **not** inspect read paths at all; it validates only `write_paths` shape (see the write table). | AGY, Claude and `gw-agent` adapters via `super().validate_request`. | `<ROUTE>_REQUEST_INVALID`; concretely `AGY_REQUEST_INVALID`, `CLAUDE_REQUEST_INVALID`, `GW_AGENT_REQUEST_INVALID` from the route name. |
| `tools/llmtool/llmtool/developer_routes/agy.py`, `AgyRoute.validate_request` | No `.github/workflows` check. It adds prompt and log-path checks. | AGY route. | `AGY_REQUEST_INVALID` for its own checks; `AGY_PROFILE_INVALID` for profile checks. |
| `tools/llmtool/llmtool/developer_routes/claude.py`, `ClaudeRoute.validate_request` | No `.github/workflows` check. It adds prompt size and config checks. | Claude route. | `CLAUDE_REQUEST_INVALID`; `CLAUDE_PROFILE_INVALID` for profile checks. |
| `tools/llmtool/llmtool/developer_routes/gw_agent.py`, `GwAgentRoute.validate_request` | No `.github/workflows` check. It checks task field presence and entry types/lengths, including `read_paths`. | Home LLM (`gw-agent`) route. | `GW_AGENT_TASK_READ_PATHS_INVALID` for a malformed read list; `GW_AGENT_TASK_PATHS_INVALID` when the read list is empty or the write list differs from `write_paths`. |
| `tools/llmtool/sensitive-paths.json` | Declares `.github/` in `prefixes`. This file is data, not a check. | The consumer is not in the read scope; `tools/llmtool/llmtool/sensitive_paths.py` is protected by the same file but was not read. | UNKNOWN. |

Read-path conclusion: within the inspected files, a `.github/workflows` read path is refused only on the two OpenCode adapters, through the F2 `action_contract._path`. No inspected AGY, Claude or `gw-agent` adapter refuses it.

## Write paths

| Check (file, function) | What it does with `.github/workflows` | Routes that reach it | Error code |
| --- | --- | --- | --- |
| `tools/llmtool/llmtool/action_contract.py`, `_path` (called from `tools/llmtool/llmtool/opencode_worker.py`, `_validate`) | Refuses `.github/workflows` in both `job.read_paths` and `job.write_paths`. | OpenCode trusted-host worker. | `LLMToolError('action contract invalid; details omitted')`, mapped to `OPENCODE_SCOPE_INVALID`. |
| `tools/llmtool/llmtool/action_contract.py`, `_path` (called from `tools/llmtool/llmtool/opencode_direct_developer.py`, `_valid_paths`) | The function calls `_path` on read paths only, then requires `set(write_paths).issubset(read_paths)`. A `.github/workflows` write path is therefore refused by `_path` (when its read counterpart exists) or by the subset rule. | OpenCode direct Developer CLI. | `OPENCODE_SCOPE_INVALID`. |
| `tools/llmtool/llmtool/developer_routes/base.py`, `DeveloperRoute.validate_request` | Validates write-path shape only: tuple/list of non-empty relative strings with no `\`, no absolute path and no empty/`.`/`..` component. No `.github/workflows` check. | AGY, Claude and `gw-agent` adapters. | `<ROUTE>_REQUEST_INVALID` (`AGY_REQUEST_INVALID`, `CLAUDE_REQUEST_INVALID`, `GW_AGENT_REQUEST_INVALID`). |
| `tools/llmtool/llmtool/developer_routes/write_boundary.py`, `_safe_relative` and `SandboxExecBoundary._policy` | Rejects invalid relative paths, any `.git` component, symlinked writable paths, and paths intersecting Git metadata or configured denied directories. It does **not** reject `.github/workflows`. | `run_route` for any Developer route using the shared boundary (AGY, Claude, `gw-agent`; OpenCode worker/direct use their own scope checks). | A `ValueError` from the boundary is converted by `run_route` to `BLOCKED_WRITE_ISOLATION` with `blocked_reason='BOUNDARY_PREPARATION_FAILED'`. |
| `tools/llmtool/llmtool/developer_routes/agy.py`, `AgyRoute.validate_request` | No `.github/workflows` write-path check. | AGY route. | `AGY_REQUEST_INVALID` for prompt/log-path checks only. |
| `tools/llmtool/llmtool/developer_routes/claude.py`, `ClaudeRoute.validate_request` | No `.github/workflows` write-path check. | Claude route. | `CLAUDE_REQUEST_INVALID` for prompt/config checks only. |
| `tools/llmtool/llmtool/developer_routes/gw_agent.py`, `GwAgentRoute.validate_request` | No `.github/workflows` write-path check. It requires `task['write_paths']` to equal the base request's `write_paths` and checks entry types/lengths. | Home LLM (`gw-agent`) route. | `GW_AGENT_TASK_WRITE_PATHS_INVALID` for a malformed write list; `GW_AGENT_TASK_PATHS_INVALID` for the equality failure. |
| `tools/llmtool/llmtool/developer_session_intake.py`, `_paths` | No `.github/workflows` write-path check; shape and subset rules only. | Next-task intake. | `INTAKE_SCOPE_INVALID`. |
| `tools/llmtool/llmtool/task_completion.py`, `_collect_registered_result_diff` and `_worktree_snapshot` | Do not reject `.github/workflows` in the declared write list. They compare the Developer-attributed result paths against the declared `write_paths`. | Executor result completion (committed and uncommitted worktree paths). | `RESULT_SCOPE_MISMATCH` (committed result; `DEVELOPER_ARTIFACT_CHAIN_INVALID` when artifact segments are supplied) or `WORKTREE_SCOPE_MISMATCH` when a changed path is outside `write_paths`. |
| `tools/llmtool/sensitive-paths.json` | Declares `.github/` in `prefixes`; not itself a check. | Consumer outside the read scope (`tools/llmtool/llmtool/sensitive_paths.py` was not read). | UNKNOWN. |

Write-path conclusion: within the inspected files, a `.github/workflows` write path is refused only by the OpenCode adapters through F2 `action_contract._path`. The AGY, Claude and `gw-agent` route adapters and the shared OS write boundary would accept such a write path if it reached them; only `.git`, symlinks and configured denied directories are refused by the boundary. Therefore the reported universal refusal would have to be enforced before route dispatch or by the external gateway/sensitive-path layer, neither of which is readable in this Task.

## F2 action contract versus Developer Task scope checks

### F2 action-contract checks (`action_contract.py` and its callers)

- `tools/llmtool/llmtool/action_contract.py`: `_path` contains the literal `.github/workflows` refusal and raises the generic contract `LLMToolError`.
- Callers that validate Developer paths:
  - `tools/llmtool/llmtool/opencode_worker.py`: `_validate` for OpenCode worker read and write paths; maps to `OPENCODE_SCOPE_INVALID`.
  - `tools/llmtool/llmtool/opencode_direct_developer.py`: `_valid_paths` for OpenCode direct read paths and the write-subset rule; maps to `OPENCODE_SCOPE_INVALID`.
- Other F2 callers that can hit `_path` but are not Developer read/write-path checks:
  - `tools/llmtool/llmtool/task_lifecycle.py`: `AgentBinding` for root/common directory/branch.
  - `tools/llmtool/llmtool/authorization_policy.py`: `ObservedState` and `ReceiptBinding`; `_path` failure becomes `DecisionCode.INVALID_REQUEST` inside `decide`.
  - `tools/llmtool/llmtool/action_contract_cli.py`: `action`/`approval` validation catches the contract error and prints `ACTION_CONTRACT_INVALID`, returning exit status 1.

### Developer Task scope checks

- `tools/llmtool/llmtool/developer_routes/base.py`: request shape, including write paths (`<ROUTE>_REQUEST_INVALID`).
- `tools/llmtool/llmtool/developer_routes/write_boundary.py`: OS write isolation (`.git`, symlink, denied-directory checks), not `.github/workflows`.
- `tools/llmtool/llmtool/developer_session_intake.py`: route-neutral next-task path shape and write-subset check (`INTAKE_SCOPE_INVALID`).
- `tools/llmtool/llmtool/developer_routes/agy.py`, `claude.py`, `gw_agent.py`: provider-specific request checks; no `.github/workflows` refusal.
- `tools/llmtool/llmtool/task_completion.py`: result-scope checks against the declared write paths; no `.github/workflows` refusal of the declaration itself.

### Where one Issue-bound grant could be checked for all routes

The natural single place is the Developer Task admission layer, before any route adapter validates or launches: `tools/llmtool/llmtool/task_submission_cli.py` (outside the read scope) or, for the next-task handoff that is in scope, `tools/llmtool/llmtool/developer_session_intake.py` (`prepare_next`). That layer already receives the complete `read_paths` and `write_paths` lists for every route.

A grant check there can be shared, but it cannot by itself make OpenCode accept a granted workflow path: `tools/llmtool/llmtool/opencode_worker.py` and `tools/llmtool/llmtool/opencode_direct_developer.py` still call `action_contract._path`, which rejects the path unconditionally. The grant therefore has to be consulted by those callers (or by a shared helper they use) before or instead of that call. This does not require changing `tools/llmtool/llmtool/action_contract.py`; it does require changing its OpenCode callers. If the requirement is literally "no caller change", then an Issue-bound grant cannot work while `_path` remains unchanged.

## Candidate designs

### Candidate 1: admission-layer grant handed to routes

Check the Issue-bound grant once in the Developer Task admission layer (`tools/llmtool/llmtool/task_submission_cli.py`), bind it to the exact approved workflow paths, and carry the grant digest/path set through `tools/llmtool/llmtool/task_store.py` and `tools/llmtool/llmtool/developer_session_intake.py`. Route callers (`tools/llmtool/llmtool/opencode_worker.py`, `tools/llmtool/llmtool/opencode_direct_developer.py`, `tools/llmtool/llmtool/developer_routes/base.py`) accept only exact granted paths; `tools/llmtool/llmtool/action_contract.py` stays unchanged.

Risks:

- Path confusion: the grant must use the same canonical form as admission and `_path` (NFC, case handling, no duplicate components). A raw-string or case-insensitive match can authorize a different spelling.
- Cross-path: a prefix or directory grant could authorize another workflow. Grant exact file paths and require set equality, not `startswith`.
- Symlink: a granted read path may traverse a symlink. Reuse the no-follow ancestor checks already in `opencode_direct_developer._valid_paths` and `opencode_worker._scope_path_nofollow`, or reject symlinked components.
- Forged grant: a Task field or model-supplied `issue` value is not authority. Bind the grant to a host-verified Issue record and a digest over Task ID, run, contract, Issue and exact path set.

Files that would change: `tools/llmtool/llmtool/task_submission_cli.py` (outside read scope), `tools/llmtool/llmtool/task_store.py` (outside read scope), `tools/llmtool/llmtool/developer_session_intake.py`, `tools/llmtool/llmtool/opencode_worker.py`, `tools/llmtool/llmtool/opencode_direct_developer.py`, `tools/llmtool/llmtool/developer_routes/base.py`.

### Candidate 2: shared grant-aware path helper in the Developer route layer

Add one grant-aware helper in `tools/llmtool/llmtool/developer_routes/base.py` (or a new module beside it) that validates a path list against the normal shape rules plus an exact Issue-bound workflow-path set. Use it from the AGY/Claude/`gw-agent` request validation and from the OpenCode adapters. The OpenCode adapters would use the helper to decide whether to call the F2 `_path` or to accept an exact granted path. `tools/llmtool/llmtool/action_contract.py` stays unchanged.

Risks:

- Path confusion and cross-path remain the same as Candidate 1.
- The OpenCode worker does its own validation in `opencode_worker._validate` and does not use `developer_routes/base.py`; a second copy of the logic is easy to miss.
- Forged grant: the helper must receive a host-verified grant, not a flag from the request.

Files that would change: `tools/llmtool/llmtool/developer_routes/base.py`, `tools/llmtool/llmtool/opencode_worker.py`, `tools/llmtool/llmtool/opencode_direct_developer.py`, plus the grant source in `tools/llmtool/llmtool/task_submission_cli.py` or `tools/llmtool/llmtool/developer_session_intake.py`.

### Candidate 3: single grant gate in next-task intake

Check the grant once in `tools/llmtool/llmtool/developer_session_intake.py` (`prepare_next`) and carry the approved exact paths to the route run. This keeps one route-neutral admission function in the read scope.

Risks:

- Coverage gap: the inspected route adapters do not all call `prepare_next`; AGY, Claude and `gw-agent` dispatch paths are not shown in the read scope. A single check there would not prove that every route is covered.
- The OpenCode adapters still need a way to distinguish a granted path from an ordinary path; otherwise `action_contract._path` refuses it.
- Path confusion, cross-path, symlink and forged-grant risks are the same as Candidate 1.

Files that would change: `tools/llmtool/llmtool/developer_session_intake.py`, `tools/llmtool/llmtool/task_lifecycle.py` (to carry the grant if it belongs on the binding), `tools/llmtool/llmtool/opencode_worker.py`, `tools/llmtool/llmtool/opencode_direct_developer.py`, `tools/llmtool/llmtool/developer_routes/base.py`.

## Checks not inspected (outside the read scope)

- `tools/llmtool/llmtool/task_submission_cli.py`: outside this Task's `read_paths`. It is the most likely shared Developer Task admission/validation point and is also the file called out in the acceptance criteria as exceeding the OpenCode source-file limit (`tools/llmtool/llmtool/developer_routes/base.py` sets `DEFAULT_OPENCODE_MAX_SOURCE_FILE_BYTES` to 1 MiB). Its exact `.github/workflows` check and error code are UNKNOWN.
- `tools/llmtool/llmtool/task_store.py`: outside the read scope; may validate or persist Task paths. UNKNOWN.
- `tools/llmtool/llmtool/sensitive_paths.py`: outside the read scope; consumer of the `.github/` prefix in `tools/llmtool/sensitive-paths.json`. UNKNOWN.
- `tools/llmtool/developer-routes.json`: outside the read scope; route registry and limits. UNKNOWN.
- `tools/llmtool/llmtool/worker_dispatch.py`: outside the read scope; dispatch checks. UNKNOWN.
- `tools/api-agent-gateway/api_agent_gateway/`: outside the repository read scope. The `gw-agent` route passes read/write lists in `task.json` to this package, and the package itself performs the file tools (`list_dir`, `read_file`, `apply_edit` as recorded in `tools/llmtool/llmtool/developer_routes/gw_agent.py`). Any `.github/workflows` refusal for the home LLM route could be there; its code and error code are UNKNOWN here.

## Not claimed

No test, review, commit, PR or CI run is claimed by this note. The note records only what the in-scope files show and marks every uninspected layer UNKNOWN.
