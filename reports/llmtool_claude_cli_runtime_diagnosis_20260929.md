# Claude CLI Runtime Validation

Date: 2026-09-29

## Tested revision

- Repository: `davidcatcat/davidcattoolsproject`
- Validation branch: `validate/a24-runtime-diagnosis-20260929`
- Tested implementation HEAD: `adf5a53a442162b7d6aa3b15df671497ab0fd1d9`
- `origin/main`: `7653a80c4462b10099ac944d3e2ed65068de6541`
- A2.4 feature branch at test time: `adf5a53a442162b7d6aa3b15df671497ab0fd1d9`
- Feature branch relative to main: ahead 3, behind 0
- Worktree: clean before and after runtime smoke and tests

## CLI interface and direct smoke

- Executable: `/usr/local/bin/claude`
- Version: `2.1.206 (Claude Code)`
- Help confirms support for `--print`, `--output-format text`,
  `--no-session-persistence`, `--safe-mode`, `--tools`, and `--model`.
- **Direct Claude CLI live smoke: PASS — normal authenticated user shell.**
  User-provided evidence: the confirmed safe flags and stdin request returned
  exit code 0 and stdout exactly `CLAUDE_DIRECT_OK`.
- A direct attempt from the Codex isolated runtime previously exited 1 and
  requested `/login`. This is classified as
  **SKIPPED-ENVIRONMENT / authenticated session unavailable**, not as a CLI
  flag failure or inference failure.

## Diagnosis

The normal user shell has a working Claude CLI session. The Codex isolated
runtime cannot access that authenticated session. The tested flags are
supported by the installed CLI, and normal-shell direct evidence confirms the
invocation works; no provider argv or auth-logic change is indicated. No
credentials, auth files, keychain contents, or environment dump were inspected.

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

README review: accurate; it describes the local CLI dependency and does not
claim a live llmtool inference PASS. ROADMAP remains A2.4 CURRENT pending
external review. The normal-shell direct PASS does not establish that llmtool
inference completed in the Codex isolated runtime.
