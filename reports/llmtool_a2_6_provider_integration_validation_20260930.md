# llmtool A2.6 provider integration validation — 2026-09-30

Status: **IMPLEMENTATION + HOST CANONICAL VALIDATION COMPLETE.** All 189 canonical
tests passed in the user-reported host run. The previous sandbox loopback-binding
limitation was environment-specific; host validation resolved the remaining
canonical gate. No implementation changes were needed after the host run.
External ChatGPT review pending; A2.6 is not merged to main. No A2.7/A3 work.
Live smoke outcomes remain unchanged, including unsuccessful CLI attempts.

## Scope and provenance

Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/a2-provider-integration`.
Initial sandbox-tested base HEAD: `8118ab439767f7799ac63b7bc5650538d4677924`, plus the uncommitted
A2.6 changes listed below. Local `origin/main` resolves to the same revision;
ahead/behind 0/0. No fetch was performed, so this is not a remote synchronization
claim. At initial sandbox validation, the branch had no configured upstream. A2.5 implementation commit `b6bd0ff` is
an ancestor (ancestry check exit 0).

Initial root/branch/status/HEAD checks matched the requested checkout. All work,
tests, documentation and smoke launches ran from the canonical repository. No
clones, worktrees, sibling repositories or alternate checkouts were created.
Existing unrelated benchmark/reasoning reports and skill `agents/` directories
remain untracked and untouched.

The repository Python imports `tools/llmtool/llmtool/__init__.py` from this checkout.
`tools/llmtool/.venv/bin/python tools/llmtool/llmtool.py --version` reports
`llmtool 0.1.0`; no installed `llmtool` executable resolves on PATH. Runtime paths:

- Claude: `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/claude`, version 2.1.285.
- Codex: `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/codex`, version 0.159.2.
  Version check also emitted a PATH-alias permission warning; no auth state was inspected.

Providers covered: `openai-compatible`, `openai`, `claude-cli`, `codex-cli`.
Anthropic API remains planned. **Provider != Role != Agent.** No new roles, agents,
orchestration, authentication discovery, tool execution or provider permissions.

## Inspection and implementation

The sorted static registry already registers all four providers consistently.
Configuration validates every profile through registry dispatch and checks readiness
only for the selected profile. Selection precedence is saved user selection, then
`LLMTOOL_PROVIDER`, then explicit `--provider`; endpoint/model overrides apply to
that selected profile. CLI overrides do not persist active selection.

The concrete defect was configure summary dispatch: it decided whether to display
Base URL using the type of the edited profile even when another profile was selected
as active. Fixed dispatch to use the actual active profile type. No architecture
redesign or provider transport changes were necessary.

Added `tools/llmtool/tests/test_provider_integration.py`: 11 test methods, with shared
fixtures and table-driven subtests across four providers. Coverage includes:

- Complete registry/factory dispatch and mixed named profiles.
- Active selection, environment selection and explicit override precedence;
  overrides isolated to the selected profile and saved config unchanged.
- Sanitized unknown profile/type failures, invalid active profile, planned type rejection.
- Inactive-profile validation; CLI endpoint/auth rejection; official OpenAI endpoint
  rejection; incompatible provider options; existing HTTP URL/auth validation.
- Config show redaction across profiles without credential environment lookup.
- Deterministic text/JSON capabilities and pure-text CLI capabilities.
- Real provider Reply normalizers with transport I/O mocked: final text, elapsed
  metadata, closed safe metadata fields, no reasoning/internal-event/key metadata leaks.
- Ask stdout/error contract and selected-profile dispatch.
- Returned shell/Git instructions printed as text with subprocess/system execution denied.
- Configure for all provider families, no runtime/HTTP calls, CLI no secret/endpoint
  prompts, preservation of other profiles, and both directions of summary type mismatch.

HTTP token metadata retains existing provider-specific names; elapsed_seconds is
shared. Capability declarations are unchanged: HTTP large_context reflects the
existing workflow support declaration, not an inferred model context-window size;
CLI providers expose only chat. Streaming, code_edit, shell and git remain false.

## Validation commands and exact results

All commands launched from the canonical root.

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest test_provider_integration -v
```

PASS: 11 tests, exit 0.

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest test_provider_integration test_capabilities test_config test_configure test_provider test_claude_cli_provider test_codex_cli_provider
```

PASS: 148 tests, exit 0. Includes existing subprocess timeout, bounded-output,
argv/security and sanitized-error regression coverage; existing tests unchanged.

```sh
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
```

Syntax compileall PASS. Canonical test discovery: 189 methods.
Previous sandbox canonical run: 148 tests passed, 2 class setup errors, make exit 2 (unittest exit 1).
`test_cli.CLITests` (26 methods) and `test_openai_provider.OpenAIProviderTests`
(15 methods) failed setup at `ThreadingHTTPServer(("127.0.0.1", 0), Handler)` with
`PermissionError: [Errno 1] Operation not permitted`. Those 41 tests did not run;
this is an environment restriction, not evidence of a provider regression or pass.
No tests were skipped, deleted or weakened to bypass this restriction.

Subsequent host canonical validation, as reported by the user:

```text
Ran 189 tests in 23.278s
OK
```

All 189 canonical tests passed. This successful host result resolves the remaining
canonical gate and confirms the prior loopback-binding limitation was specific to
the sandbox environment. No implementation changes were needed after the host run.
The host-tested revision and shell exit code were not separately supplied; this
report records the exact user-provided test output without claiming an agent rerun.

`git diff --check`: PASS, exit 0. New untracked files also checked separately for
whitespace errors. Documentation validation: PASS, exit 0; three documents and
16 local Markdown links checked during initial sandbox validation, along with the then-pending status and the
Provider != Role != Agent boundary.

## Live smoke results

Smoke used checkout Python and the actual CLI `main(['ask', ...])` with real
factory/providers. A test harness supplied only an in-memory resolved config to
avoid writing user configuration; transport/runtime were not mocked. Prompt:
`Return exactly A2_6_SMOKE_OK. Plain text only. Do not use tools.` Timeout: 30 seconds.
No credential files, keychain or auth state inspected, no environment dumps and
no paid OpenAI request. Only marker equality and sanitized llmtool errors recorded.

| Provider | Result | Evidence / limitation |
|---|---|---|
| openai-compatible | NOT-TESTED | Existing user config resolves to incomplete default profile; base_url not configured. Deployment examples were not loaded automatically. |
| claude-cli | ATTEMPTED, unsuccessful | llmtool exit 1, empty stdout, sanitized `Claude CLI exited with status 1`; raw runtime stderr withheld. No successful inference demonstrated. |
| codex-cli | ATTEMPTED, unsuccessful | llmtool exit 1, empty stdout, sanitized `Codex CLI exited with status 1`; raw runtime stderr withheld. No successful inference demonstrated. |
| openai | NOT-TESTED | No prior paid API authorization in this session; mock/integration coverage used. Credential availability not investigated. |

CLI failures are recorded without attributing them to authentication or a product
defect. Runtime restrictions may apply; raw stderr/auth inspection was deliberately
not used for diagnosis. Earlier milestone live passes are historical evidence only.
Codex retains its ephemeral external runtime cwd security boundary; no repository
copy/clone/worktree was placed there. Smoke produced no additional repository changes
beyond the intended A2.6 edits; Git status was checked before/after.

## Security review and limitations

Source review and passing offline regressions confirm unchanged shell=False,
fixed argv, bounded output, timeout handling and sanitized errors. No raw stderr,
reasoning event or HTTP error body forwarding was added. No credential discovery,
automatic model-command execution, filesystem mutation or Git mutation was added.
Tests verify returned command text stays output text. This is source/offline evidence;
unsuccessful live attempts do not establish new live isolation or inference evidence.
Existing trusted runtime dependencies and HTTP socket-timeout limitations remain.

The implementation changed configure.py and added provider integration tests, plus
README.md, ROADMAP.md and this report. This host-validation update changes only the
three documentation files; provider code and tests are unchanged.
Implementation + canonical validation are complete. **Provider != Role != Agent.**

## Completion and review boundary

The user-reported host run resolved the canonical gate. Live smoke remains:
openai-compatible NOT-TESTED; claude-cli attempted, unsuccessful; codex-cli
attempted, unsuccessful; openai NOT-TESTED. No unsuccessful attempt is a PASS.

Documentation validation for this update: `git diff --check`, `git diff --stat`,
full diff inspection, and confirmation that only README.md, ROADMAP.md and this
report changed. No application build or test rerun is required for these
documentation-only changes.

Commit message: `docs(llmtool): record A2.6 host validation`.
Target branch: `feat/a2-provider-integration`. This records the intended Git action;
commit/push success must be confirmed separately after execution.

Stop for external ChatGPT review. A2.6 is not merged to main; do not merge main or
start A2.7/A3.
