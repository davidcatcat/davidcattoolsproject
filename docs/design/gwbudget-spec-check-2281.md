# #2281 spec check: home LLM token-budget removal versus current code

Read-only investigation. Task `e27a50b599c249248e3f46b3f7cbc557`, source SHA
`078702af1976bf27db6a3d61b593ad04d5fbacae`. Context: Claude Developer Tasks
`f047af7e` and `f7b236a6` on the #2281 token-budget removal spec ended with no
change and no kept reply, and the Owner reads this as a possible spec error
(#2324). This note says whether that spec matches the code.

No code or test was changed. No test was run. Line numbers are at the source
SHA above.

## Verdict

**SPEC_MISMATCH.** The current code needs the two token budgets
(`max_input_tokens`, `max_output_tokens`) at three independent layers, and
tests pin each one. Two of the files that make and enforce those budgets are
not in the investigated file set. A token-budget removal limited to the files
listed under [Files checked](#files-checked) cannot pass its own tests (see
M1 to M4). The rules that check the budgets also disagree with each other
(M5).

## Limits of this check

- **The #2281 spec text is not in this Task's read paths.** The goal,
  acceptance and read/write scope of Tasks `f047af7e` and `f7b236a6` are
  UNKNOWN to this note. This note takes the spec intent from its title: remove
  the token budgets from the home LLM (`gw-agent`) route. It assumes the spec's
  scope is the same as, or a subset of, the files listed below. If the spec
  names more write paths, the PM should compare them with the "needed, not in
  scope" list in M1.
- Why the two earlier runs ended with no change and no reply is UNKNOWN. No
  run log was read.
- Files outside the read paths were found by file name only (glob). Their
  contents were not read.

## Files checked

| File | Token-budget role |
| --- | --- |
| `tools/api-agent-gateway/api_agent_gateway/contract.py` | Gateway Task contract; requires the token keys |
| `tools/api-agent-gateway/tests/test_budgets.py` | Gateway tests that enforce the token budgets |
| `tools/llmtool/developer-routes.json` | `gw-agent` route budgets, including the token keys |
| `tools/llmtool/llmtool/developer_routes/__init__.py` | Route config loader; requires token keys |
| `tools/llmtool/llmtool/developer_routes/gw_agent.py` | Route request check; requires token keys |
| `tools/llmtool/llmtool/gw_agent_sessions.py` | No budget reference (no match for `budget`, `token` or `2281`) |
| `tools/llmtool/llmtool/gw_agent_wiring.py` | No budget reference; scope-digest check only (see M6) |
| `tools/llmtool/scripts/guidance_drift_check.py` | Rule-file schema; token keys already optional |
| `tools/llmtool/tests/test_developer_route_gw_agent.py` | Pins token keys in the route loader and request check |
| `tools/llmtool/tests/test_guidance_drift_check.py` | Pins the drift-check budget schema |
| `tools/llmtool/tests/test_gw_agent_route_wiring.py` | Pins the forwarded budget key set and multiplier resolution |

## Mismatches

### M1. Code that makes and enforces the budgets is outside the file set

- **The budget builder is missing.** The Task `budgets` sent to the gateway
  are built in `run_task_gw_agent_delegate` in
  `tools/llmtool/llmtool/task_submission_cli.py`. That module is not in the
  read paths. Evidence:
  - the tests patch `task_submission_cli.load_route_config` and call
    `task_submission_cli.run_task_gw_agent_delegate`
    (`tools/llmtool/tests/test_gw_agent_route_wiring.py:1029`, `:1057`,
    `:1149`, `:1156`);
  - that function turns `max_input_tokens_multiplier` ×
    `context_cap_tokens` into `max_input_tokens`
    (`test_gw_agent_route_wiring.py:1243-1247`, `:2072-2076`). This happens in
    neither `gw_agent.py` nor `gw_agent_wiring.py`.

  Removing the token budgets means editing this function.
- **The gateway enforcement code is missing.** The gateway refuses with
  `INPUT_TOKEN_BUDGET_EXCEEDED` and `OUTPUT_TOKEN_BUDGET_EXCEEDED`
  (`tools/api-agent-gateway/tests/test_budgets.py:45-49`, `:51-57`). It also
  sets the per-request `max_tokens` from the budget
  (`test_budgets.py:57` expects `max_tokens == 100` when
  `max_output_tokens=100`). Neither behaviour is in `contract.py`. The module
  that holds them is not in the read paths. By file name only, it is probably
  `tools/api-agent-gateway/api_agent_gateway/agent.py`; this is UNKNOWN.
- **The gateway test helper is missing.** The default Task budgets used by
  every gateway test come from `GatewayCase.task()`, imported from `helpers`
  (`test_budgets.py:6`, `:11`). That file is not in the read paths.
- **The README golden vector is missing.** `gw_agent.py:150` says the scope
  digest "mirrors ... the README's canonical JSON golden vector". The digest
  covers `budgets` (`gw_agent.py:156`). `tools/api-agent-gateway/README.md` is
  not in the read paths. Whether its vector contains the token keys is
  UNKNOWN.

Files needed but not in scope: `tools/llmtool/llmtool/task_submission_cli.py`,
the gateway enforcement module (probably `api_agent_gateway/agent.py`), the
gateway `tests/helpers.py`, and possibly `tools/api-agent-gateway/README.md`.

### M2. The gateway contract needs exactly the six budget keys

- `contract.py:28-35`: `BUDGET_LIMITS` lists `max_input_tokens` (`:30`) and
  `max_output_tokens` (`:31`).
- `contract.py:145`: `parse_task` refuses any `budgets` whose key set is not
  exactly `set(BUDGET_LIMITS)`, with `POLICY_REFUSED TASK_INVALID`.

If llmtool stops sending the token keys and the gateway is not changed, every
gateway run is refused before the first model call. Changing `contract.py`
also changes the contract for every gateway caller, not only the home LLM
route. The spec would have to say whether the keys are removed from the
contract or made optional. Which one the spec says is UNKNOWN.

The gateway tests that pin enforcement are `test_budgets.py:45-49` (input) and
`:51-57` (output, including the per-request `max_tokens` at `:57`). A removal
must delete or rewrite them. Under the Developer rules, a Developer may not
delete a test unless the spec explicitly asks for it.

### M3. The llmtool route layers need the token keys

- **Route config loader.** `developer_routes/__init__.py:145-151` accepts
  exactly one of two key sets. Both need input and output token entries,
  either direct or as multipliers. Any other set makes `load_route_config`
  fail with `ROUTE_CONFIG_INVALID` (`:388`). The token range checks are at
  `:167-194`.
- **Route request check.** `developer_routes/gw_agent.py:38-44`:
  `_GATEWAY_BUDGETS` lists both token keys (`:40-41`). `validate_request`
  refuses a missing key with `GW_AGENT_TASK_BUDGET_<KEY>_INVALID` (`:337-340`)
  and refuses any other key set (`:341-342`).
- **Shipped config.** `developer-routes.json:46-53` sets
  `max_input_tokens: 262144` (`:48`) and `max_output_tokens: 65536` (`:49`).
  These equal 8 × `context_cap_tokens` and 4 × the profile
  `max_output_tokens` (`:38-39`).

All three must change together. If only the JSON drops the keys, the loader
refuses the whole route file. That affects every route, not only `gw-agent`,
because `load_route_config` raises for the whole file.

### M4. llmtool tests pin the token keys

A removal must rewrite these tests. None is in a "remove" list that this note
can see.

- `test_developer_route_gw_agent.py:83-84` and `:246-249`: request and route
  fixtures carry the token keys.
- `test_developer_route_gw_agent.py:286-375`
  (`test_route_config_validates_gw_agent_budgets`):
  - direct token keys are accepted (`:304-315`);
  - multiplier keys are accepted (`:317-325`);
  - multipliers over the ceiling are refused (`:327-340`);
  - multipliers without profile caps are refused (`:342-348`);
  - `max_input_tokens: 0` is refused (`:364-365`).
- `test_gw_agent_route_wiring.py:1085-1087`: asserts the forwarded key set is
  exactly the six keys, including both token keys. `:1090-1091` asserts their
  values.
- `test_gw_agent_route_wiring.py:1097-1156` and `:1971-2030`: refusal cases
  for out-of-range multipliers (`:1134-1139`, `:2008-2013`).
- `test_gw_agent_route_wiring.py:1158-1247` and `:2032-2076`: multiplier
  resolution into `max_input_tokens` and `max_output_tokens`. These two tests
  share a name but are in different classes (`GwAgentRouteWiringTests` at
  `:50`, `GwAgentRealStoreTests` at `:1428`), so neither hides the other.
- `test_guidance_drift_check.py:394-404`, `:718-729`, `:740-751`: the
  drift-check schema accepts and range-checks the token keys and multipliers.

### M5. The budget checks disagree with each other

- `scripts/guidance_drift_check.py:231-244`:
  - requires only `max_turns`, `max_wall_seconds`, `max_tool_result_bytes`
    and `max_sent_bytes`;
  - lists all four token keys as optional (`:238-243`);
  - therefore accepts budgets with **no** token keys, and accepts mixed sets
    such as `max_input_tokens_multiplier` with direct `max_output_tokens` (the
    shape used at `test_guidance_drift_check.py:743-745`).
- `developer_routes/__init__.py:150` refuses both of those shapes.

So the docs/drift check already allows a token-free `gw-agent` budget, but the
loader, the route check (`gw_agent.py:341`) and the gateway
(`contract.py:145`) refuse it. Any spec that says "the drift check enforces
the budget shape", or that leaves the drift check out of scope, conflicts with
the loader.

### M6. Removal changes the scope digest (consequence, not a defect)

- `budgets` is part of the scope digest on both sides: `contract.py:103-113`
  and `gw_agent.py:149-158`.
- The digest goes into `authorization.json` (`gw_agent.py:418`).
- On a repair run, the digest is compared with the session binding
  (`gw_agent_wiring.py:134-139`).

Removing the token keys changes every digest. A repair run that resumes a
session bound before the change is refused with
`DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE`. The spec should say whether that
is acceptable or whether those sessions must be swept first. Whether the spec
says this is UNKNOWN.

## Items that match

- `max_turns` ceiling 1000, which accepts 512 (#2281 L9), is the same in every
  layer:
  - `contract.py:29`;
  - `gw_agent.py:39`;
  - `developer_routes/__init__.py:153`;
  - `guidance_drift_check.py:233`;
  - tests at `test_budgets.py:27-39`, `test_developer_route_gw_agent.py:574-575`
    and `:587-592`, and `test_guidance_drift_check.py:734-739`.
- The non-token budget ranges are the same in the gateway, the route check,
  the loader and the drift check. These are `max_wall_seconds`,
  `max_tool_result_bytes` and `max_sent_bytes`: `contract.py:32-34`,
  `gw_agent.py:41-43`, `__init__.py:155-166`, `guidance_drift_check.py:234-236`.
- The profile `context_cap_tokens` and `max_output_tokens`
  (`developer-routes.json:38-39`, `contract.py:255-258`) are per-call profile
  caps, not Task budgets. `CONTEXT_EXHAUSTED` (`test_budgets.py:82-85`)
  depends on the profile, not on the budgets. A token-budget removal does not
  need to touch them.
- `gw_agent_sessions.py` and `gw_agent_wiring.py` have no direct budget
  dependency. Only M6 applies.

## What a matching spec would need

These are inputs for the PM. They are not decisions.

1. Name `tools/llmtool/llmtool/task_submission_cli.py` and the gateway
   enforcement module and test helper in read and write paths. Also name
   `tools/api-agent-gateway/README.md` if its golden vector has token keys.
2. Choose one: remove the token keys from the gateway contract, or make them
   optional there (`contract.py:28-35`, `:145`). Also say what per-request
   `max_tokens` becomes without the output budget (`test_budgets.py:57`).
3. List the tests in M2 and M4 that will be removed or rewritten, so a
   Developer is not blocked by the rule against deleting tests.
4. Make the loader and the drift-check schema agree on one budget key set
   (M5).
5. State how existing `gw-agent` session bindings are handled after the digest
   changes (M6).
