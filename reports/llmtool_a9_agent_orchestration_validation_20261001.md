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

## Remote CI and external review — 2026-10-01

Reviewed branch head before this evidence append:
`1ada1f359ba925fef6eb3afb27bfd5362b24d007`.

GitHub llmtool CI run `36851457200` PASS:
- Python 3.9.25: 501 tests, all PASS; compileall PASS; working tree clean PASS.
- Python 3.14.7: 501 tests, all PASS; compileall PASS; working tree clean PASS.
- A9-focused `test_orchestration.py`: 19 tests, all PASS on both jobs.

The repository's existing editable-package install succeeded in both CI jobs and the
new `llmtool.roles`, `llmtool.orchestration`, and `llmtool.orchestration_cli`
modules were imported by the passing tests. Static packaging review confirms these
modules are under the existing `llmtool*` package discovery, while
`tools/llmtool/orchestration/` examples are outside package data. A standalone
wheel build was NOT-RUN in this connector-driven pass; this limitation is recorded
rather than inferred as a wheel artifact result.

External ChatGPT code/security review PASS. No material blocker found. Invariants
verified: explicit role/profile mapping; no provider-role inference; shallow handoff;
readiness before credential resolution; fail-fast; one completion call per reached
step; no retry/fallback/parallelism; no shell/Git/file/code mutation; no provider-native
tool enablement; no A10 retrieval; no Anthropic special case. Existing Claude/Codex
providers remain hardened text-only runtimes when reached through Provider.complete.

The parser hard cap is 8 steps, but duplicate roles are intentionally rejected and
the registry currently has four built-in roles, so the effective A9 MVP maximum is
four steps. Delimiters mark previous output as untrusted but are not claimed to
eliminate prompt injection.

Live smoke remains NOT-TESTED because this GitHub connector cannot access the user's
local authenticated CLI sessions. No credential inspection, auth change, VPN
dependency, or paid API call was introduced merely to make validation green.

PR #10 remains DRAFT and unmerged. A10 NOT STARTED.

## Final branch CI checkpoint

CI-tested feature head: `876600e8644b69e0e3d1b962df2431117df044a3`.

GitHub llmtool CI run `36851862564` completed successfully:
- Python 3.9: 501 tests PASS; compileall PASS; Working tree is clean PASS.
- Python 3.14: 501 tests PASS; compileall PASS; Working tree is clean PASS.

The production code, A9 tests, README, and ROADMAP at that head are therefore
CI-verified. This appended report-only checkpoint does not modify
`tools/llmtool/**` and does not require another llmtool CI run.

External ChatGPT review: PASS. No material A9 blocker remains in this review.
Standalone wheel build remains explicitly NOT-RUN; package-layout/static review and
CI editable-install/import validation PASS. Live provider smoke remains NOT-TESTED
because the GitHub connector cannot access the user's local authenticated CLI session.

PR #10 remains DRAFT and unmerged by design. A10 NOT STARTED.


## Canonical host functional follow-up — 2026-10-01

Reviewed remote A9 HEAD: `17daec7f4e6b7a3ac75f97281d8050782c39fe33`;
base main: `a4c102732898c3d82b4047516753977f8ba3ce4b`.
All development, tests, wheel commands and smoke were launched from the canonical
repository. Only the prior Codex-authored A8 closeout drafts were backed up as
`/private/tmp/codex-a8-closeout-drafts-before-a9.patch` and reverted before selecting
the existing A9 remote branch. No alternate project checkout was created or used;
existing registered worktrees and unrelated untracked files remained untouched.

### Demonstrated defect and fix: A9-R1

The runner checked raw content budgets per step but checked escaped result JSON
only after all steps. A 128 KiB control-character output can exceed 1 MiB when
serialized with repeated final_content. The regression initially demonstrated that
the second profile was reached before detecting overflow. FIXED: construct and
validate each completed OrchestrationResult prefix before resolving the next profile.
The test now proves next-profile resolution, API-key lookup, client construction
and completion never occur after this overflow. No bounds were relaxed or output
truncated. Every prefix must fit the serialized result budget; escaping may cause
an output below the raw-content limit to fail. Metrics/provider transports unchanged.

### Functional, architectural and failure-path coverage

13 tests added to the original 19: strict malformed/empty/nested/duplicate-key/UTF-8
plan handling; detached frozen typed models; UTF-8 byte limits; escaped JSON and
aggregate byte budgets; invalid reply content; overflow fail-fast; explicit text/file
input never reading stdin; conflicting/empty input causing no request; second-step
failure leaving stdout empty; real readiness failure preventing credentials; unknown
profile never falling back; four registered transports using real config/factory/
wire normalization with transport I/O mocked.

The four-transport test deliberately maps grinder to OpenAI, validator to Codex,
coder to compatible HTTP, reviewer to Claude. It proves explicit plan selection wins
over active/environment profile, an unused missing credential is never resolved,
original task appears every step, only the immediately previous output is handed
forward, config bytes are unchanged, hidden reasoning is excluded, HTTP tools are
not enabled, OpenAI store:false remains, evidenced tokens are preserved and CLI
usage remains null. The execution AST test now also checks the role module and
plain `import subprocess` forms. No provider-role inference or dynamic routing.

### Exact local validation

| Gate | Discovered | Passed | Failed | Errors | Skipped | Elapsed |
|---|---:|---:|---:|---:|---:|---:|
| A9 focused source | 32 | 32 | 0 | 0 | 0 | 0.262 s |
| Affected shared tests | 278 | 278 | 0 | 0 | 0 | 31.351 s |
| Canonical Python 3.13.3 syntax + suite | 514 | 513 | 0 | 0 | 1 | 43.032 s |
| Actual Python 3.9.6 syntax + suite | 514 | 513 | 0 | 0 | 1 | 72.053 s |
| Installed-wheel A9 focused | 32 | 32 | 0 | 0 | 0 | 0.213 s |

Both make check commands exited 0; compileall PASS. One inherited filesystem skip,
no A9 skips. Tests remain offline and use fake/mocked transports and local loopback
servers. Initial A9-R1 regression failed before the fix; all final rows above passed.
Commands: unittest discover -s tools/llmtool/tests -p test_orchestration.py;
PYTHONPATH=tools/llmtool/tests unittest test_cli test_config test_provider
test_openai_provider test_claude_cli_provider test_codex_cli_provider
test_provider_integration test_terminal test_benchmark test_validator;
make -C tools/llmtool check with canonical .venv/bin/python and /usr/bin/python3.

### Wheel and user-facing CLI

Standalone wheel build PASS through existing setuptools build isolation; the first
no-build-isolation attempt could not import setuptools in the local venv. No product
dependency/build configuration changed. Wheel SHA256:
`7b03bbe63f1276251c6cc2dfabeb354d9d72476889f5357885bbbc20fc4638a3`.
All 31 packaged Python/resource files match canonical source bytes. New roles.py,
orchestration.py and orchestration_cli.py are present. Inherited A0–A8 production
modules/resources match main except the four additive CLI integration lines; examples,
benchmarks, feedback datasets and tests are excluded. Installed wheel in a temporary
artifact directory passed its own A9 focused suite; imports were verified to come
from that installation. Four-role source JSON dry-run and two-role installed-wheel
human dry-run both PASS without profile/config/credential access.

### One real two-step smoke

Claude CLI 2.1.286, existing authenticated runtime, exactly one grinder → reviewer
pipeline / two provider requests. A temporary explicitly supplied config fixture
selected claude-cli without new credentials or changing user config/HOME/auth.
Synthetic HTTP 500 task only, no repository context. Hardened existing transport
kept tools disabled; no model-suggested command was executed. No retry or fallback.
Transport/orchestration PASS: both ordered roles completed with safe result metadata;
synthetic marker was present in both results. This marker also appeared in the
original task, so it alone is not independent proof of handoff; exact shallow
handoff is independently covered by the transport integration regression.
Wall/provider latency: grinder 6102/6096 ms; reviewer 10397/10383 ms. Total smoke
16.525 s. Model identifier and token counts null; token_usage_available=false.
Raw outputs/stderr/auth data not retained in this report. No model-quality PASS
claimed; no automatic A8 evaluation. Historical A8 Claude safety FAIL remains intact.

### Security and current status

Complete follow-up diff and AST review PASS. No A9 subprocess/shell/Git/file/code
mutation, provider-native tool enablement, automatic skill selection, retry/fallback,
credential logging, raw stderr, hidden context accumulation, retrieval or approval
engine introduced. Existing runtime subprocess isolation belongs to the unchanged
provider transports; A9 itself uses Provider.complete only.

Historical CI run 36852015835 verified exact remote HEAD 17daec7 on both Python jobs,
including Offline canonical checks and Working tree is clean SUCCESS. That evidence
does not cover this production follow-up; updated-head CI pending publication.
PR #10 remains DRAFT, unmerged. Updated A9-R1 needs external ChatGPT review.
Anthropic HTTP API remains future A2.3. A10 NOT STARTED.
