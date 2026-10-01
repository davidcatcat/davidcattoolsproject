# llmtool A9 agent orchestration validation — 2026-10-01

## Revision and scope

Base main: `a4c102732898c3d82b4047516753977f8ba3ce4b`.
Branch: `feat/a9-agent-orchestration`.
Implementation commits currently end at `fa15b5cc1e3a2f36afa9f302e44a26269d5b9bac` before this documentation commit.
A0–A8 remain merged. A9 implements only provider-independent role/profile text
orchestration. A10 NOT STARTED.

The user's canonical local checkout contained three superseded uncommitted A8
closeout drafts, so this A9 branch was created and authored directly from the reviewed
remote main without modifying, deleting, stashing, or pruning any local worktree/file.

## Architecture

New production modules:
- `roles.py`: fixed declarative roles grinder/validator/coder/reviewer.
- `orchestration.py`: strict models, shallow handoff, sequential runner.
- `orchestration_cli.py`: explicit plan CLI and offline structural dry-run.

Provider != Profile != Role != AgentStep. An AgentStep is one role bound explicitly
to one named provider profile for one bounded text completion. Provider/model names
never imply a role. The A9 LLM validator role cannot replace or override A6.

Plans contain exactly `steps`; each step contains exactly `role` and `provider`.
There are 1–8 steps and duplicate roles are rejected in the MVP. Parsing reuses A7's
strict bounded JSON parser: duplicate keys, NaN/Infinity, depth/UTF-8/schema violations
fail closed. No DAG, conditions, loops, eval, executable data, dynamic code loading,
or automatic plan discovery.

## Handoff and provider boundary

Each step receives the original user task plus only the immediately previous role
output. Previous output is explicitly labeled untrusted model-generated data.
Earlier outputs are not accumulated. A10 retrieval/memory is not implemented.

Each live step resolves its explicit profile using
`resolve_config({"provider": profile}, require_configured=True)`, then resolves the
credential and creates the provider. This preserves readiness-before-credential
ordering. Each provider is called once. Failure is fail-fast: no retry, fallback,
profile substitution, or later provider call.

A9 never executes model output. It adds no subprocess, shell/Git/filesystem mutation,
patch application, test execution, approval engine, provider-native tools, or code-edit
capability. Provider capability metadata is not authorization.

## Bounds and metadata

- task <= 1 MiB UTF-8
- step output <= 128 KiB UTF-8
- accumulated step output <= 512 KiB
- plan/result JSON <= 1 MiB
- steps <= 8

Runtime metadata reuses A8 normalization: safe model identifier, separate wall/provider
latency, and provider-evidenced token counts only. Unknown metadata is discarded;
unavailable tokens are null and are never estimated.

Human display uses the existing terminal-safe boundary. JSON intentionally preserves
model-generated text as machine data.

## CLI

`llmtool orchestrate --plan PLAN --dry-run [--json]` validates structure only and
does not read task input, config, credentials, instantiate providers, or access network.
Normal mode accepts direct text, `--file`, or stdin via the existing input contract.

Repository-only example plans live under `tools/llmtool/orchestration/` and are not
part of package data.

## Anthropic boundary

Anthropic HTTP API remains A2.3 planned/unimplemented. A9 contains no Anthropic
special case; future registered providers can use the same profile contract.

## Validation status

`tests/test_orchestration.py` covers strict models, role/provider separation,
shallow handoff, explicit profile ordering, readiness before credentials, fail-fast
behavior, no retry/fallback/later provider, metadata allowlisting, dry-run isolation,
terminal-safe display, and dangerous command strings remaining inert.

Canonical GitHub CI Python 3.9 / 3.14 is pending the final branch head. A dedicated
local live smoke is NOT-TESTED because the GitHub connector does not have access to
the user's local authenticated Claude/Codex sessions; no credential inspection or
paid API call is fabricated. Packaging is expected to include the three new
`llmtool/*.py` modules through existing package discovery while excluding repository
example plans; CI/package evidence will be appended after verification.

A9 is NOT merged. External ChatGPT review pending. A10 NOT STARTED.
