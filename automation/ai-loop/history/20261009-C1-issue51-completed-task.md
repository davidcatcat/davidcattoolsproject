# CURRENT ACTIVE TASK — Issue #51 C1 deterministic test excerpts (2026-10-09)

## Assignment and authoritative state

- Repository: `davidcatcat/davidcattoolsproject`.
- Canonical Mac checkout ONLY: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Current pinned `main` commit: `8e29024656f873431f6ad83e45015b57bfe381a9` (contains squash-merged #49 and #50).
- Scope: [Issue #51](https://github.com/davidcatcat/davidcattoolsproject/issues/51) and
  [native self-development C1 plan](https://github.com/davidcatcat/davidcattoolsproject/blob/8e29024656f873431f6ad83e45015b57bfe381a9/tools/llmtool/NATIVE_SELF_DEV_MVP.md).
- New branch `feat/llmtool-c1-test-excerpts-20261009`; **do not reuse old A11b branch / old 76644 run / PR #49**.
- This is a **new task, not a recovery**. Existing stopped-run evidence is archived
  byte-for-byte in `automation/ai-loop/history/`. Old original control SHAs remain valid historical data.

## User goal and division of work

Implement an actual `llmtool` product feature using the existing runner:
**Codex CLI is the sole source-code writer, Claude CLI is the independent
read-only ordinary code reviewer, deterministic tests are run by the outer
ai-loop runner.** The user must not copy prompts between agents. If a gate
requires a policy/design/human decision, STOP with structured evidence.
The runner controls Git commits, Draft PR creation and exact-head CI, never
merges or grants approval. Keep independent security/QA signoff separate.

**This is C1 deterministic / read-only only.** NO LLM call by the new
feature; NO automatic company-model routing, token-saving claims, shell,
tests execution from product CLI, Git mutation, executor, patch, permissions
escalation, network, unstated optional A11b.2.2.2, or automatic merge.

## Minimum public behavior (implement smallest correct vertical slice)

1. Add offline command **`llmtool test-excerpts --file PATH --exit-code INT --json`**
   (or an exactly equivalent explicit, documented opt-in interface) to safely
   inspect only an explicitly selected captured log. No implicit repo traversal,
   user config, provider credentials, auth, external API or working-directory mutation.
   A nonzero captured test exit code must never become a test PASS because
   the excerpt parser returns successfully. Parser success != test success.
2. Deterministically identify **real Python unittest and pytest**
   failing test IDs and bounded anchored excerpts. Support the common
   `FAIL:`/`ERROR:` unittest header and `FAILED file::test` pytest
   summary forms, report only IDs justified by the captured raw source.
   Do not invent a root cause, traceback, test ID or assertion outcome.
   When complete known failure coverage cannot be established, return
   `INCOMPLETE`/explicit unknowns and retained nonzero exit rather than
   fabricating a full coverage or PASS claim. Unknown format must fail closed.
3. Provide versioned JSON with source `sha256`/size, supplied test exit
   code, detected format, ordered deduplicated failure IDs, byte-offset
   ranges / bounded excerpts, source references, omitted and unknown
   coverage flags, and explicit result status. Keep original raw artifact
   unchanged. No model summaries accepted as raw evidence. Avoid output
   that blindly echoes untrusted control sequences; reject or visibly
   escape ANSI/control characters. Never put raw inputs, paths, credentials
   or exception messages into stderr error reporting.
4. Enforce strict explicit-input/file bounds and race-safe/no-follow
   regular-file handling; reject symlinks, directories, oversized, malformed
   UTF-8/binary or changed inputs. C1 **does not** authorize transfer of
   captured content outside the host; treat excerpts as potentially
   sensitive, and require a separately reviewed permission/redaction gate
   before any future company/external model use.
5. Respect Python 3.9 through 3.14; preserve existing CLI/Provider, A5 Git,
   A6 Validator, A9 and A10 contracts. No new dependencies or CLI
   behavior changes outside the named new opt-in command.
6. Update `tools/llmtool/README.md` truthfully with usage and limitations:
   deterministic excerpt utility != measured savings; Phase 0/paired
   benchmark are future, separately authorized steps.

## Deterministic negative/regression coverage

Create `tools/llmtool/tests/test_test_output_excerpts.py` and augment
`test_cli.py` for the public command. Minimum fixtures/tests:

- unittest failure and error headers, repeated/duplicate IDs, multiple
  traceback blocks, test run count vs reported failures/errors;
- pytest failure summary, mixed `FAIL`/`ERROR`, parametrized node IDs;
- ANSI/control characters, Unicode, byte ranges/hash/offsets, truncation
  and max excerpt limits; explicit source provenance;
- malformed/unsupported/partial output, invalid/test-failed exit metadata,
  no recognized ID with nonzero exit, zero reported failures with nonzero
  exit, missing summaries and false-success guard;
- symlink, directory, oversize/binary data and file-state drift before/
  after bounded reads, no unintended file changes;
- no provider/HTTP calls, no implicit credential lookup, no repo scan,
  no shell subprocess, no Git writes, no full-log egress.
- reject or safely omit sensitive-content indicators; no source leakage
  in stderr, and no output schema status claiming test PASS from a
  failed external test.

Do not loosen assertions or classify skips/missing coverage as PASS just
to make the suite green. Preserve original test evidence and error codes.

## Allowed changes and boundaries

The exact allowlist is in `automation/ai-loop/task.json`:
`tools/llmtool/llmtool/test_output_excerpts.py`,
`tools/llmtool/llmtool/cli.py`,
`tools/llmtool/tests/test_test_output_excerpts.py`,
`tools/llmtool/tests/test_cli.py`, and
`tools/llmtool/README.md`.
No other source/test/doc file or report is authorized for Codex edits.
The outer runner creates its own bounded `reports/ai-loop/` handoff.

Coder must read `AGENTS.md`, relevant `skills/implement-feature/SKILL.md`,
CLI/source/tests, Issue #51, and Roadmap. Reviewer must independently
inspect cumulative code diff, contract and actual test evidence; prior
author's statements are claims, not an acceptance gate.

## Gates, budgets and handoff

- Local related: syntax + new test module + public CLI tests.
- Final canonical full: `make -C tools/llmtool check` under both existing
  Mac venv Python and `/usr/bin/python3`. Any required check failure STOPS
  without weakening gates.
- Exact publication HEAD GitHub CI: `llmtool-ci.yml` Python 3.9/3.14.
- Max 2 repair attempts, max 2 failure reviews, 1h per model call
  ceiling and 30 min per test command. Minimize duplicate log/model
  consumption. Do not add native Mac process containment probes:
  this read-only parser changes neither A11b process primitives nor
  any existing process controller.
- Final ordinary Claude review is **not** independent formal Security
  Review or Functional QA signoff. Publication ends at DRAFT PR with
  mandatory external coordinator/QA decision. No auto-merge.
- Deferred: company-only Phase 0 reliability on 48KiB+ real-output
  fixtures, paired premium token/cost measurement and C2 native
  self-development executor. Do not claim these were done by this task.

Operator: from canonical repo, run `bash scripts/ai-loop.sh --check`
first. If and only if it prints PRECHECK PASS and no unrelated work
is at risk, run `bash scripts/ai-loop.sh`. These are the **only**
normal CLI handoffs; do NOT relay Codex/Claude prompts manually.
No `--resume` on old 76644; on any STOP, preserve current state and
request fresh coordinator assessment rather than resetting/restarting.
