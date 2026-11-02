<a id="interim-ai-loop-controller-release-and-mac-handoff"></a>

# Mac operator handoff and frozen paths

ai-loop is a standalone tool documented in [tools/ai-loop/README.md](../../tools/ai-loop/README.md) and is not an llmtool controller.

## Mac operator handoff

The coordinator does not gain Mac access from delegation. The Owner may type one bounded, pre-reviewed command only when available tools cannot perform a required Mac action. State the exact task, command, expected result, safe failure/recovery and evidence needed. Do not make the Owner shuttle prompts, manage worktrees, or diagnose routine failures.

## Frozen paths and data export

The `--qa-low*` routes remain STOP/frozen; do not invoke or revive them. Use Mac/macOS evidence for Mac-specific behavior; Linux CI is not a substitute. The C1 local excerpts feature does not authorize excerpt export. Company/external-provider egress remains route- and data-class-specific; see [Issue #63](https://github.com/davidcatcat/davidcattoolsproject/issues/63). Do not include raw private logs, secrets or credentials in evidence.
