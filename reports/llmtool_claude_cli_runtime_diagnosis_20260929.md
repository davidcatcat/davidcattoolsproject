# Claude CLI Runtime Diagnosis

Date: 2026-09-29

## Tested revision

- Repository: `davidcatcat/davidcattoolsproject`
- Validation branch: `validate/a24-runtime-diagnosis-20260929`
- Tested HEAD: `42f65e8337132d426ccea56f49b91a075a7b30ae`
- `origin/main`: `7653a80c4462b10099ac944d3e2ed65068de6541`
- A2.4 feature branch at test time: `42f65e8337132d426ccea56f49b91a075a7b30ae`
- Feature branch relative to main: ahead 2, behind 0
- Worktree: clean before and after the smoke attempt

## CLI interface and direct smoke

- Executable: `/usr/local/bin/claude`
- Version: `2.1.206 (Claude Code)`
- Help confirms support for `--print`, `--output-format text`,
  `--no-session-persistence`, `--safe-mode`, `--tools`, and `--model`.
- A direct, non-interactive request used the provider's safe flags, disabled
  tools, supplied the prompt through stdin, and ran outside the repository.
- Result: exit code 1; expected output marker absent; stderr empty.
- The captured output was not recorded. In-memory classification identified a
  request to run `/login`; no unsupported-flag signal was found.

## Diagnosis

The direct invocation failed at the local Claude CLI authentication/session
prerequisite. The tested flags are supported by this installed CLI, so this
result does not establish a provider argv defect. No credentials, auth files,
keychain contents, or environment dump were inspected.

The `LLMTOOL_CLAUDE_CLI_HOME` mapping and isolated llmtool HOME behavior remain
unverified against a successful authenticated invocation. The direct CLI
prerequisite failed first, so no llmtool live smoke was attempted.

## Validation state

- Source review: the provider argv uses the CLI flags confirmed by local help;
  subprocess uses argv with `shell=False`, and stderr is not forwarded raw.
- Direct Claude CLI live smoke: **FAIL / authentication prerequisite**.
- llmtool Claude live smoke: **NOT-TESTED**.
- Fake CLI targeted tests and canonical test suite: **NOT-RUN in this diagnosis**.
- Code changes and regression tests: none; no code root cause was demonstrated.
- Original dirty checkout: untouched.

Next step: complete Claude CLI sign-in in the intended user session, then rerun
the direct marker smoke followed by the isolated llmtool smoke. Do not treat
this report as a live inference pass.
