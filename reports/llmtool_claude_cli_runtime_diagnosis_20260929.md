# Claude CLI Runtime Validation

Updated: 2026-09-30

## Tested revision

- Repository: `davidcatcat/davidcattoolsproject`
- Validation branch: `feat/a2-claude-cli-provider`
- A2.4 implementation HEAD validated: `cd55e88f887c6a4b6b3db64bc177badd1bc2c373`
- `origin/main`: `7653a80c4462b10099ac944d3e2ed65068de6541`
- A2.4 feature branch at final manual smoke: `cd55e88f887c6a4b6b3db64bc177badd1bc2c373`
- Feature branch relative to main at final smoke: ahead 5, behind 0
- Manual smoke used a temporary llmtool HOME/config; `git status --short` was
  clean after the smoke.

## CLI interface and direct smoke

- Normal authenticated user shell: `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/claude`,
  version `2.1.285 (Claude Code)`.
- Codex isolated runtime: `/usr/local/bin/claude`, version `2.1.206 (Claude Code)`.
- Help confirms support for `--print`, `--output-format text`,
  `--no-session-persistence`, `--safe-mode`, `--tools`, and `--model`.
- **Direct Claude CLI live smoke: PASS — normal authenticated user shell.**
  User-provided evidence: the confirmed safe flags and stdin request returned
  exit code 0 and stdout exactly `CLAUDE_DIRECT_OK` (latest CLI version 2.1.285).
- **llmtool live E2E smoke: PASS — normal authenticated user shell.** At the
  validated feature HEAD, a temporary HOME config selected a `claude-cli`
  profile, set `LLMTOOL_CLAUDE_CLI_HOME` to `/Users/davidsun`, and ran
  `llmtool ask 'Reply with exactly: CLAUDE_CLI_OK'`. User-provided evidence:
  stdout was exactly `CLAUDE_CLI_OK`.
- The user reported `git status --short` had no output after the smoke. No
  filesystem or Git mutation was observed from the smoke.
- A direct attempt from the Codex isolated runtime resolved the older, different
  executable, exited 1, and requested `/login`. This is classified as
  **SKIPPED-ENVIRONMENT / different runtime and unauthenticated session**, not as
  a CLI flag failure or inference failure.

## Diagnosis

The normal shell and Codex isolated runtime resolve different Claude CLI
executables and versions. The normal shell uses the nvm-installed 2.1.285 CLI;
Codex resolves `/usr/local/bin/claude` version 2.1.206 and lacks its authenticated
session. This runtime provenance difference explains why the isolated smoke is
not representative of the normal shell. The normal-shell direct and llmtool
PASS results establish the supported invocation; no provider argv or auth-logic
change is indicated. No credentials, auth files, keychain contents, or
environment dump were inspected.

`claude_cli.py` resolves the executable with `shutil.which("claude")`, so the
llmtool process `PATH` selects the first matching executable and therefore the
CLI version. `LLMTOOL_CLAUDE_CLI_HOME` changes child `HOME`, not `PATH`. Project
config cannot choose an executable; no configurable executable path was added.
PATH selection is a documented runtime dependency and minor follow-up.

The earlier Codex-isolated llmtool attempt exited 1 without the expected marker;
its underlying CLI diagnostic was intentionally not exposed. That attempt is
**SKIPPED-ENVIRONMENT**, not an inference failure. The later normal-shell E2E
smoke passed using the temporary llmtool HOME and the existing authenticated
Claude home path; no credentials were copied. Fake executable tests also
confirm the child HOME override while llmtool retains its isolated HOME.

## Validation state

- Source/security review: PASS. Provider uses argv with `shell=False`, disables
  tools, bounds output, applies a timeout, normalizes errors, and does not
  forward raw CLI stderr. Project config cannot inject an executable or auth.
- Direct Claude CLI live smoke: **PASS — normal authenticated user shell**
  (user-provided evidence; exact marker; CLI 2.1.285).
- Codex isolated direct smoke: **SKIPPED-ENVIRONMENT** (different executable,
  version 2.1.206, and unauthenticated session).
- llmtool Claude live smoke: **PASS — normal authenticated user shell**
  (user-provided evidence; temporary llmtool config; exact marker).
- Fake CLI targeted tests: **PASS, 13 tests**.
- Canonical suite: **PASS, 145 tests**, including compileall.
- Code changes and regression tests: none; no code defect was demonstrated.
- Filesystem mutation: **none observed**; Git mutation: **none** during smoke.
- Original dirty checkout: untouched.

README and ROADMAP record that A2.4 live E2E validation passed in the normal
authenticated shell, while the Codex isolated runtime resolves a different CLI.
PATH-based executable selection remains a documented runtime dependency.
