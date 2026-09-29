# Claude CLI Runtime Validation

Date: 2026-09-29

## Tested revision

- Repository: `davidcatcat/davidcattoolsproject`
- Validation branch: `validate/a24-runtime-diagnosis-20260929`
- Tested HEAD before this documentation update: `2aedbe933783c296dd67ad1b8a22b50f865fef4c`
- `origin/main`: `7653a80c4462b10099ac944d3e2ed65068de6541`
- A2.4 feature branch at test time: `2aedbe933783c296dd67ad1b8a22b50f865fef4c`
- Feature branch relative to main: ahead 4, behind 0
- Worktree: clean before and after runtime smoke and tests

## CLI interface and direct smoke

- Normal authenticated user shell: `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/claude`,
  version `2.1.284 (Claude Code)`.
- Codex isolated runtime: `/usr/local/bin/claude`, version `2.1.206 (Claude Code)`.
- Help confirms support for `--print`, `--output-format text`,
  `--no-session-persistence`, `--safe-mode`, `--tools`, and `--model`.
- **Direct Claude CLI live smoke: PASS — normal authenticated user shell.**
  User-provided evidence: the confirmed safe flags and stdin request returned
  exit code 0 and stdout exactly `CLAUDE_DIRECT_OK`.
- A direct attempt from the Codex isolated runtime resolved the older, different
  executable, exited 1, and requested `/login`. This is classified as
  **SKIPPED-ENVIRONMENT / different runtime and unauthenticated session**, not as
  a CLI flag failure or inference failure.

## Diagnosis

The normal shell and Codex isolated runtime resolve different Claude CLI
executables and versions. This is a concrete runtime provenance difference;
the evidence does not prove that the version difference alone caused `/login`,
because the isolated runtime also lacked the authenticated session. The normal
shell direct PASS confirms the invocation flags work there; no provider argv
or auth-logic change is indicated. No credentials, auth files, keychain
contents, or environment dump were inspected.

`claude_cli.py` resolves the executable with `shutil.which("claude")`, so the
llmtool process `PATH` selects the first matching executable and therefore the
CLI version. `LLMTOOL_CLAUDE_CLI_HOME` changes child `HOME`, not `PATH`. Project
config cannot choose an executable; no configurable executable path was added.
PATH selection is a documented runtime dependency and minor follow-up.

One llmtool smoke attempt used a temporary llmtool `HOME` and config with
`LLMTOOL_CLAUDE_CLI_HOME` pointing to the existing user home; no credentials
were copied. It exited 1 without the expected marker. The provider intentionally
normalizes CLI errors, and the captured output did not expose a specific cause.
Given the normal-shell PASS and isolated-runtime auth limitation, classify the
llmtool live smoke as **SKIPPED-ENVIRONMENT**; this is not a live inference
FAIL. No repeated request was made. The fake executable test confirms the child
`HOME` override is passed while llmtool retains its isolated `HOME`.

## Validation state

- Source/security review: PASS. Provider uses argv with `shell=False`, disables
  tools, bounds output, applies a timeout, normalizes errors, and does not
  forward raw CLI stderr. Project config cannot inject an executable or auth.
- Direct Claude CLI live smoke: **PASS — normal authenticated user shell**
  (user-provided evidence; exact marker).
- Codex isolated direct smoke: **SKIPPED-ENVIRONMENT**.
- llmtool Claude live smoke: **SKIPPED-ENVIRONMENT** (one attempt; exit 1,
  expected marker absent; no prompt, credential pattern, or reasoning leak
  found in captured streams; raw CLI stderr not exposed).
- Fake CLI targeted tests: **PASS, 13 tests**.
- Canonical suite: **PASS, 145 tests**, including compileall.
- Code changes and regression tests: none; no code defect was demonstrated.
- Original dirty checkout: untouched.

README and ROADMAP now document PATH-based executable resolution and the
different runtime provenance. A2.4 remains CURRENT pending external review.
The normal-shell direct PASS does not establish that llmtool inference
completed in the Codex isolated runtime.
