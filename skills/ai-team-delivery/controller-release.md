# Interim ai-loop controller release and Mac handoff

Use this reference whenever changing, publishing or invoking the temporary ai-loop controller. D8 does not waive its source, provider-export or Mac execution safeguards.

## Mac operator handoff

The coordinator does not gain Mac access from delegation. The Owner may type one bounded, pre-reviewed command only when available tools cannot perform a required Mac action. State the exact task, command, expected result, safe failure/recovery and evidence needed. Do not make the Owner shuttle prompts, manage worktrees, or diagnose routine failures.

## Launcher evidence

The tracked `scripts/ai-loop.sh` in `automation/ai-loop` fetches the control-branch revision, materializes the revision-scoped shell/Python files under `.git/ai-loop/bootstrap/<commit>/`, refreshes `.git/ai-loop/run.sh` to the cached Git shell entrypoint, and launches Python with `--control-sha`. The Owner confirmed this was the original tracked Git launcher without local custom code. This source description does not prove the current local symlink, checkout or runtime behavior; verify those on the actual Mac before claiming them.

## Controller change/release gate

Changing `automation/ai-loop` changes code that may execute on a later Mac launch. Before moving any candidate to the executable control tip:

1. Review the immutable old/new source SHAs, complete diff and task/runtime scope with a distinct Reviewer. Changes involving shell/process execution, file/Git mutation, network, credentials, provider export, launcher or trust boundaries require independent formal Security review.
2. Stage the candidate on a non-executable ref. Run exact-candidate helper CI on the required Python versions and the Mac-native checks required for the change. The reviewed bytes must remain unchanged.
3. An authorized AI publisher promotes only the reviewed candidate, verifies destination SHA and race-free tip identity, and records exact evidence. Missing, failed or inconclusive required evidence means STOP publication; a GitHub review or green generic CI alone is not release proof.
4. On actual Mac execution, capture the fetched/executed revision, verified task identity, status/error and sanitized diagnostics when available. Never publish Mac-private logs, credential values or tokens. A revision SHA identifies bytes; it does not establish safe release or a completed run.

Routine technical release decisions are delegated to the AI team; do not ask the Owner for per-revision signoff. This gate does not permit uncontrolled source changes, arbitrary commands, new provider-data export, or relaxed independent QA/Security/Mac gates. Operator typing is not permission to execute newly fetched code.

## Frozen paths and data export

The `--qa-low*` routes remain STOP/frozen; do not invoke or revive them. Use Mac/macOS evidence for Mac-specific behavior; Linux CI is not a substitute. The C1 local excerpts feature does not authorize excerpt export. Company/external-provider egress remains route- and data-class-specific; see [Issue #63](https://github.com/davidcatcat/davidcattoolsproject/issues/63). Do not include raw private logs, secrets or credentials in evidence.
