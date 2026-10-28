# LLMTool Roadmap

Current collaboration and execution authority is in [AGENTS.md](../../AGENTS.md). The previous roadmap text is preserved verbatim in the [2026-10-28 roadmap archive](../../docs/history/llmtool-roadmap-archive-20261028.md).

## Status

| Milestone | Status | Evidence PR/issue |
| --- | --- | --- |
| A0–A10 | DONE and merged; A2.3 Anthropic HTTP API: 不做 per Owner decision 033:A ([#525 c6468216854](https://github.com/davidcatcat/davidcattoolsproject/pull/525#issuecomment-6468216854); sheet [c6467792563](https://github.com/davidcatcat/davidcattoolsproject/pull/525#issuecomment-6467792563)) — Claude is used via the Claude Code CLI; the paid API is billed separately and has no current need. | A2.4–A2.6 and A0–A10 status recorded in the [roadmap archive](../../docs/history/llmtool-roadmap-archive-20261028.md). |
| A11a — Observability & Diagnostics | DONE and merged. | [PR #26](https://github.com/davidcatcat/davidcattoolsproject/pull/26) |
| P1 — Failure Usage Preservation | DONE and merged. | [PR #39](https://github.com/davidcatcat/davidcattoolsproject/pull/39) |
| A11b — Execution, Approval & Recovery | IN PROGRESS; F2 general user-repository mutation is NOT AUTHORIZED. | [PR #32](https://github.com/davidcatcat/davidcattoolsproject/pull/32), [PR #43](https://github.com/davidcatcat/davidcattoolsproject/pull/43), [PR #49](https://github.com/davidcatcat/davidcattoolsproject/pull/49), [PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84), [issue #97](https://github.com/davidcatcat/davidcattoolsproject/issues/97) |
| A11c — Integration & Real-world Acceptance | IN PROGRESS; M1b real-task adoption and full M2 are NOT_VERIFIED; T1 is HOLD_REPRIORITIZE. | [PR #128](https://github.com/davidcatcat/davidcattoolsproject/pull/128), [PR #129](https://github.com/davidcatcat/davidcattoolsproject/pull/129), [PR #131](https://github.com/davidcatcat/davidcattoolsproject/pull/131), [PR #133](https://github.com/davidcatcat/davidcattoolsproject/pull/133), [issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120) |
| A12 — Remote Frontends / Telegram Agent | FUTURE OPTIONAL. | [issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120) |
| A13 — Distribution, Installation & Updates | FUTURE / OWNER DEFERRED. | [issue #396](https://github.com/davidcatcat/davidcattoolsproject/issues/396) |

## Next steps

- Verify the first M1a/M1b real-task adoption.
- Finish the separately granted production foreground Worker trial, then verify same-Integrator resume and delegated-Worker M2.
- Continue A11b F2 real-repository mutation design and A11b.3 approval semantics; execution remains NOT AUTHORIZED.
- Preserve the applicable A11b, QA, Mac and route gates before full native cutover.
