# llmtool A2.5 — Codex CLI Provider Validation

Date: 2026-09-30. Branch: `feat/a2-codex-cli-provider`.
Base `origin/main` and starting HEAD: `1e9e69c9947b7e5b466d877b85682aa113e2f9ba`.
The initial worktree was clean, on the expected branch and base. All work used
`/private/tmp/davidcattoolsproject-a25`; no other checkout was modified.

## Outcome and validation scope

Implementation, documentation, targeted tests and canonical checks are complete.
Successful live inference and independent macOS sandbox enforcement are **pending external
host-terminal verification**. The user explicitly adjusted the validation contract to permit
implementation/commit/push with this pending validation. A2.6 was not started; main was not merged.

Actual PATH resolution: `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/codex`.
`codex --version`: `codex-cli 0.159.2` (accepted actual version, replacing expected 0.158.0).
Inspected `codex --help`, `codex exec --help`, and sandbox help before fixing the contract.
No authentication files, keychain credentials, environment dumps, or unrelated auth state were inspected.

## Configuration and capabilities

```yaml
providers:
  local-codex:
    type: codex-cli
    # model: your-model  # optional
    timeout_seconds: 120
active_provider: local-codex
```

Executable selection is exclusively `shutil.which("codex")`; trusted PATH order selects the
installation. User/project config cannot supply executable paths, cwd, environment, sandbox,
approval policy or arbitrary argv. Non-null `base_url`, `api_key` and `api_key_env` are rejected
following existing CLI-provider conventions. Only empty `provider_options: {}` is accepted.
Model values use a bounded ASCII identifier allowlist and cannot start with an option prefix.
The shared config model dispatches validation through the registered implementation; it needs
no provider-specific fields. Project config retains its existing model/timeout-only permissions.
The configure wizard skips endpoint/API-key input and performs no CLI or network request.

Capabilities: `chat=true`; `large_context=false`, `reasoning_control=false`, `streaming=false`,
`code_edit=false`, `shell=false`, `git=false`. This implementation does not provide large-context
workflow support or a tested reasoning-option mapping; CLI/model window size alone is insufficient.
Generic temperature/max_tokens are stored, not sent/enforced. Provider != Role != Agent.
Authentication belongs to the user's existing Codex CLI login/session; llmtool does not manage it.

## Actual runtime contract

The prompt is omitted from argv and sent only through stdin. The fixed prefix is:

```text
<PATH-resolved-codex> --no-daemon --ask-for-approval never exec
  --ignore-user-config --ignore-rules --ephemeral --strict-config
  --skip-git-repo-check --color never --cd <isolated-runtime-cwd>
```

Each following setting is a separate `-c` argument/value pair:

```toml
default_permissions="llmtool-text"
permissions.llmtool-text.extends=":read-only"
permissions.llmtool-text.filesystem={":root"="deny",":minimal"="read",":tmpdir"="deny",":slash_tmp"="deny",":workspace_roots"={"."="read"}}
permissions.llmtool-text.network.enabled=false
project_doc_max_bytes=0
web_search="disabled"
features.shell_tool=false
features.unified_exec=false
features.shell_snapshot=false
features.multi_agent=false
features.hooks=false
features.apps=false
features.memories=false
features.remote_plugin=false
features.code_mode.enabled=false
shell_environment_policy.inherit="none"
allow_login_shell=false
model_reasoning_summary="none"
hide_agent_reasoning=true
show_raw_agent_reasoning=false
```

Append `--model <validated-model>` only when configured; the final argv element is `-`.
The invocation uses an argv list, `shell=False`, `close_fds=True`, `start_new_session=True`,
and explicit subprocess `cwd` matching `--cd`. Text mode is selected by omitting `--json`.
Only final stdout is returned; stderr is captured/discarded, never forwarded. No internal
JSONL events, tool traces, or reasoning streams are returned. Reasoning summaries are suppressed.

Nonblocking selector I/O concurrently transports stdin and drains both output pipes. Each
output stream has its own 1 MiB bound. A request deadline bounds execution, including held-open
descendant pipes; timeout/overflow kills the process group and reaps the child. Nonzero exits,
missing executable, startup failure, invalid UTF-8, empty output and overflow produce normalized
errors without prompt, raw stderr, secret or environment values. Temporary runtime cleanup occurs
on success/failure; ephemeral CLI mode avoids session-file persistence, but CLI-owned global
runtime/auth state is not removed by llmtool.

## Isolation and sandbox design

A new empty directory is created under system `/tmp`, with `/var/tmp` fallback, outside the
caller's repository. The provider locates only the `.git` boundary with metadata checks; it
never runs Git or reads caller source. Inherited TMPDIR is ignored when choosing the directory.
No repository contents are copied/mounted; prompts are not written to files. Explicit cwd,
AGENTS.md suppression, and ignoring user config/rules prevent ordinary caller-context discovery.

The custom `llmtool-text` profile extends the read-only sandbox and denies root/temp reads,
reopening only Codex's minimal system paths and the isolated workspace as readable. Tool network
access is disabled. **Do not add `--sandbox read-only`: older sandbox settings override named
profiles and would lose the restricted-read boundary.** This precedence and filesystem rules
are documented in [official Codex permissions](https://learn.chatgpt.com/docs/permissions).
Feature controls are documented in [the configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

Shell/unified-exec, snapshots, multi-agent, hooks, apps, memories, remote plugins, code mode and
web search are disabled as defense in depth. This is **not a claim that all tools are disabled**.
Isolation plus the restricted read-only sandbox form the execution boundary. Fake tests do not
prove OS enforcement, nor containment of a malicious PATH executable.

The child environment is allowlisted: PATH, HOME, USER, LOGNAME, LANG, LC_ALL, LC_CTYPE, CODEX_HOME,
SSL_CERT_FILE and SSL_CERT_DIR when set, plus TMPDIR set to the isolated cwd. It excludes project
secrets/API keys, PWD, Git overrides, Node startup injection and daemon/remote endpoints.
Existing HOME/CODEX_HOME preserve CLI-owned authentication lookup; auth contents are never read
or copied by llmtool. The E2E smoke used a temporary llmtool HOME and pointed CODEX_HOME at the
existing CLI state directory without inspecting it.

The CLI itself is a trusted runtime and accesses its authentication/model network outside its
tool sandbox. Global skill/runtime state may still load. System/managed Codex policy and future
CLI behavior are dependencies: external validation must verify the effective restricted profile,
including that no older system sandbox setting overrides it. User-config-based custom gateways
and environment proxy/API-key authentication are outside this first provider's supported contract.
The implementation targets the project's POSIX/macOS/Linux environments.

## Automated validation

Final targeted command, from `tools/llmtool`:

```sh
.venv/bin/python -m unittest discover -s tests -p test_codex_cli_provider.py -v
```

**PASS: 33 tests in 7.329s, OK.** Tests cover registration/factory, all capabilities, argv controls,
stdin (including a prompt larger than pipe capacity), success, shell=False, explicit cwd,
empty/no-copy runtime directory, caller repository integrity, failed Git discovery, timeout and
descendant pipe lifetime, both output limits, UTF-8, nonzero/empty/missing executable, sanitization,
config/model injection, options rejection, API-field rejection, no auth/prompt file reads, wizard,
environment allowlisting and cleanup. Relative fake-CLI mutation attempts affect only the isolated
runtime and are removed; the fake repository, including its Git files, is byte-for-byte unchanged.
These tests verify provider wiring and mutation isolation, not kernel access denial for arbitrary
absolute paths or malicious executables.

Final canonical command, from repository root:

```sh
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
```

**PASS: 178 tests in 23.244s, OK; compileall also passed; exit code 0.**
Earlier runs exposed one outdated registry assertion (updated to include the implemented provider)
and a shell-redirection run encountered restricted loopback binding; the exact canonical command
without redirection subsequently passed. Existing test coverage was preserved; unsupported-type
checks now use an actually unsupported placeholder. An initial targeted run from the wrong import
cwd and too-short fake-process startup deadlines were corrected before the passing runs.

The ignored `.venv` uses Python 3.13.3 and PyYAML 6.0.3 reused from an existing local validation
environment. Package-index network access was unavailable; no dependency manifest changed.

## Live attempts and mutation evidence

Before the smokes, `git status --short` contained only expected A2.5 README/roadmap, wizard,
registry, test expectation updates, and new Codex provider/tests. These implementation changes
were already present and are distinct from inference-caused mutation.

Direct smoke: invoke the actual PATH-resolved CLI with the provider's exact argv/cwd/environment
and bounded transport helper, without llmtool configuration/conversation serialization.
Prompt: `Reply with exactly:\nCODEX_DIRECT_OK`.
**BLOCKED-ENVIRONMENT:** CLI exit 1; normalized result `Codex CLI exited with status 1`;
no final text/marker. Sanitized diagnostic categorization found initialization failure and
`Operation not permitted`, without unknown/invalid argument categories. Raw diagnostics were
never exposed or written into this repository. This is not a successful direct model response.

E2E smoke: temporary user config outside the repository, `type: codex-cli`, then the project's
entrypoint with `ask --provider local-codex 'Reply with exactly: CODEX_CLI_OK'`, invoked from the
caller repository. **BLOCKED-ENVIRONMENT:** exit 1, stdout empty, stderr exactly
`llmtool: Codex CLI exited with status 1\n`. No required marker was returned. No unsafe fallback,
credential inspection or weakened sandbox was attempted.

Before/direct-after/E2E-after fingerprints were identical:

- Filesystem SHA-256: `04da0e3f760086c08b2e6baa9beb5f7954a1cb86d1cc61803620400c92275ca0`
- Git state SHA-256: `854d8c87a4d421139f384549f4bc3eb92252f5f48e8883b53a152f4866f4c2e2`
- Workspace file count: 1053

Filesystem fingerprint covered repository files (including ignored validation artifacts), paths,
permissions, symlink targets and contents; Git fingerprint covered status, HEAD, refs, tracked and
staged diffs, index, per-worktree HEAD and HEAD reflog. No fingerprint recorded secret file contents.
The expected implementation changes remained identical across the attempts; later test/report
edits were authored deliberately after those smoke snapshots.

| Check | Observed result |
|---|---|
| credential leak | NO |
| reasoning leak | NO |
| prompt echo | NO |
| raw stderr leak | NO |
| filesystem mutation from inference | NO |
| git mutation from inference | NO |

These NO results describe the failed startup attempts and fake tests, not successful live-model
coverage or proven kernel isolation. Successful direct/E2E markers remain pending externally.

## External host-terminal verification required

The harmless `codex sandbox -c 'sandbox_mode="read-only"' /usr/bin/true` probe exited 71 with
`sandbox-exec: sandbox_apply: Operation not permitted` inside this nested Codex environment.
A named-profile sandbox probe also reached the same kernel refusal. Neither establishes a defect
in the host-terminal Codex installation or proves enforcement here.

On an unrestricted host terminal, verify the same named profile denies reading caller source,
AGENTS.md and Git files by absolute path, rejects writes and tool network access, and cannot
escalate with approval never. Verify effective policy despite system/managed configuration.
Then repeat direct and llmtool exact-marker smokes with the same runtime contract, recording
before/after filesystem/Git fingerprints and all six checklist results. Keep these pending until
that external evidence exists. Stop after branch publication for external ChatGPT review; do not
merge main or begin A2.6.
