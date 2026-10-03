---
name: validate-runtime
description: Use when validating a provider, gateway, CLI runtime, VPN-dependent service, external model backend, or end-to-end runtime connectivity before declaring an integration operational.
---

# Validate Runtime

Collect evidence for the exact runtime and environment under test. Keep reachability,
authentication, inference, and end-to-end claims separate.

## Process

1. **Establish the tested revision.** Record repository, branch, HEAD, expected feature commit or
   version, working-tree state, and installed executable path/version. Verify the executable
   belongs to the checkout that contains the feature.
2. **Isolate test state.** Use a temporary HOME and temporary config where practical. Avoid changing
   real user config and do not discover credentials implicitly.
3. **Separate validation layers.** Record routing, DNS, TCP, TLS, HTTP reachability, authentication,
   model inference, CLI/backend session, repository access, and end-to-end workflow independently.
4. **Use the smallest real test that proves the requirement.** Prefer an exact output marker and a
   low-cost request. Avoid paid or high-cost workloads, destructive operations, and arbitrary file
   mutation.
5. **Check the output and diagnostics contract.** Verify exit code, stdout/stderr, safe metadata,
   and available run/task identifiers. Inspect persisted trace/error records only when explicitly
   enabled; ephemeral runs must not be reported as having saved traces. Apply the leakage and
   compatibility checks in [review-change](../review-change/SKILL.md).
6. **Check network coexistence.** For VPN-dependent or internal services, test required internal and
   external endpoints plus required source control. Check DNS and the destination route/interface;
   record the selected route when split tunneling may apply.
7. **Preserve security boundaries.** Never dump env/`printenv`, cat credential files, read keychains
   or SSH keys, dump kubeconfigs or other secret-bearing config, or print Authorization headers.
   Do not change production, VPN, DNS, routes, proxy, or system settings to make a test pass.
8. **Classify each result precisely:** `PASS`, `FAIL`, `SKIPPED-CONFIG`, `SKIPPED-AUTH`,
   `SKIPPED-ENVIRONMENT`, or `NOT-TESTED`. A skipped check is not a pass.
9. **Attach minimal evidence to every PASS:** for example, tested revision/version, HTTP status,
   exit code, exact output marker, safe metadata, or route/interface.
10. **Exercise one safe failure path when diagnostics are part of the requirement.** Confirm that normal output is actionable, the structured error/trace identifies the failing layer, debug/verbose mode does not expose secrets, and retry/fallback/STOP behavior matches policy.
11. **Decide against explicit prerequisites.** Declare an end-to-end prerequisite PASS only after
    every required layer and dependency has been tested successfully. Record anything not tested.

## Rules

- Reachability is not inference. An authentication failure after successful TLS may still prove
  network reachability, but does not prove authentication or inference.
- Mock success is not live success. A model session proves that session only; do not infer another
  provider or backend is healthy without testing it.
- Prefer temporary isolated config over the user's real config. Never mutate production or
  network/system settings merely to make a smoke test pass.
- Report skipped checks explicitly and keep the final conclusion within the evidence collected.

## Environment handoff

For llmtool Cloud/Mac work, follow
[the development workflow](../../tools/llmtool/WORKFLOW.md).
Run checks requiring the user's VPN, existing CLI sessions, macOS isolation or local
services on the actual Mac. Record unavailable Cloud checks precisely, then hand off
the exact implementation SHA, prerequisites, commands and expected markers.
Do not transfer auth files or replace required live evidence with mocks. Relevant
code/config/test changes invalidate affected previous runtime evidence.

## Completion criteria

- Tested revision and executable version are known.
- Validation layers are distinguished and required real inference was performed when applicable.
- Output, error, and available diagnostic/trace streams meet the expected contract without sensitive data leakage.
- Skipped checks are explicit and the final conclusion matches the evidence.
