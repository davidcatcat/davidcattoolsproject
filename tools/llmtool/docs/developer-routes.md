# Direct Developer routes

`developer_routes` provides a provider-neutral contract for direct CLI Developer
adapters. A route validates its profile and request, builds the provider argv and
environment, parses bounded output, verifies the actual model from provider
evidence, and identifies quota exhaustion. `run_route` owns the common execution
sequence and returns a `RouteResult` or a content-free typed `RouteError`.

## Check order and write boundary

The caller supplies a dispatch gate. It must re-read the authoritative local
worker dispatch policy for every run and accept AGY only when
`worker_dispatch.evaluate('agy', owner_ref, task_id)` reports
`OWNER_ASSIGNMENT_MATCHED`. The common runner then checks the registered D6
branch, HEAD, and clean state. It prepares and probes the operating-system write
boundary before starting the CLI. A failed or unsupported boundary stops the
run. The route receives the boundary prefix before its argv, so its provider
arguments cannot replace that boundary. The runner checks Git identity and the
changed file set after execution, then parses output, verifies the model, handles
quota, and returns the result.

AGY argv after the boundary prefix is exactly:

```text
agy -p PROMPT --model MODEL --effort EFFORT --sandbox --output-format json --print-timeout 0 --log-file LOG
```

AGY does not receive `--add-dir` or `--mode`. The current verified write
boundary uses Apple's `sandbox-exec` and is supported only on macOS. Other
operating systems report an unsupported boundary and cannot start the provider.

## Configuration

`developer-routes.json` has schema version `1` and a `routes` map. The AGY entry
contains only `executable`, `default_model`, `default_effort`, `denied_dirs`,
and `state_dirs`. The executable is the command name `agy`; model and effort are
validated tokens. `.gemini` is a home-relative denied directory name, never an
absolute personal path. `state_dirs.policy` is `private-temp-only`; runtime
state is confined to the private temporary directory created for the run.
Unknown keys, unsupported values, malformed JSON, and invalid schema versions
fail closed.

## Add a route

1. Add a provider entry to the versioned route config using relative policy
   names and a documented state-directory policy. Extend the strict loader for
   the explicitly supported schema.
2. Add a `DeveloperRoute` subclass with stable `name`, `worker_key`, and model
   `family` values.
3. Implement profile validation, argv construction, a minimal environment,
   strict bounded output parsing, actual-model verification, and quota detection.
4. Keep all provider arguments behind the `run_route` boundary prefix. Never
   grant path access from provider options.
5. Add fake-runner tests for exact argv, dispatch refusal, D6 refusal, failed
   boundary, output parsing, model verification, quota, and changed-file scope.
6. Run the route contract and write-boundary tests on an admitted environment,
   then obtain the required independent review and exact-head CI evidence.

Codex and Claude routes may migrate to this contract later. Their existing
routes keep their current execution path until that migration is separately
authorized and completed.
