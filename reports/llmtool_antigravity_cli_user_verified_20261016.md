# Antigravity CLI 1.4.0 — Owner-run smoke record (2026-10-16)

**Status: `USER_VERIFIED`** (Owner-run on the Mac terminal). This is **not** an
llmtool adapter result: no llmtool provider, profile or Worker invoked this CLI,
and no adapter, Role or authorization is implied. Provider != Profile != Role !=
Agent != Skill. Existing historical provider reports are unchanged; this is a
separate dated record in the repository's provider/CLI validation set.

| Item | Value |
| --- | --- |
| CLI | Antigravity CLI 1.4.0 (`agy`) |
| Account tier | Google AI Pro (no account identifier recorded) |
| Model | Gemini 4 Argon (Medium), `gemini-4-argon-medium` |
| Source | Owner terminal, 2026-10-16; reported to the AI team, not reproduced by an AI |

## Observations

- **Interactive:** reply `GEMINI_4_ARGON_OK`.
- **Headless:**
  `agy -p "Reply GEMINI_4_ARGON_OK only." --model gemini-4-argon-medium --output-format json`
- **JSON status:** `SUCCESS`; `num_turns` 1; `duration_seconds` 22.986926.
- **Usage reported by the CLI:** input 14714, output 37, thinking 25,
  cache_read 0, total 14751 tokens. These are the CLI's own reported counts for
  this single call, not an llmtool measurement and not a cost or savings claim.

## What this does and does not establish

- Established (by Owner report): the CLI authenticates on the Owner's account,
  runs interactively and headlessly, and returns machine-readable JSON with usage.
- **Not** established: any llmtool Adapter PASS, argv/envelope contract,
  timeout/cleanup/exit-code behavior under llmtool, sandbox/tool restriction,
  data-egress approval, reliability over repeated calls, or token/cost routing.
- No provider, Roadmap structure, runtime, Worker enablement or Live Trial
  authorization changes. `REVIEWER_JOB_TRIAL` stays NOT_AUTHORIZED
  ([Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)).
  Any future adapter needs its own contract, tests and independent review.

Documentation-only; validated by `git diff --check`. No application tests were run.
