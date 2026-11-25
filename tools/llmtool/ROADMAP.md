# LLMTool Roadmap

Current collaboration and execution authority is in [AGENTS.md](../../AGENTS.md). The roadmap text replaced on 2026-11-23 is preserved verbatim in the [2026-11-23 roadmap archive](../../docs/history/llmtool-roadmap-archive-20261123.md); earlier text is in the [2026-10-28 archive](../../docs/history/llmtool-roadmap-archive-20261028.md).

Status snapshot: 2026-11-23. Status comes from the archived roadmap text and the GitHub records linked below. A closed Issue or merged PR is evidence of that record only; where the records do not establish a milestone's state, the row says UNKNOWN and why.

## Status

| Milestone | Current status | Evidence |
| --- | --- | --- |
| A0–A10 | DONE and merged (archived roadmap). A2.3 Anthropic HTTP API not done per Owner decision 033:A; Claude runs through the Claude Code CLI. | [PR #525](https://github.com/davidcatcat/davidcattoolsproject/pull/525) merged |
| A3.5 — Interactive Chat / TUI UX | Interactive chat DONE; incremental TUI UX IN PROGRESS under #683. W57 merged as #701; W58 PM-first help/navigation next, then W59–W63. #682 and #696 closed not merged. | [#701](https://github.com/davidcatcat/davidcattoolsproject/pull/701) merged (`e77e1f83`); [#683](https://github.com/davidcatcat/davidcattoolsproject/issues/683) open |
| A11a — Observability & Diagnostics | DONE and merged. | [PR #26](https://github.com/davidcatcat/davidcattoolsproject/pull/26) merged |
| P1 — Failure Usage Preservation | DONE and merged. | [PR #39](https://github.com/davidcatcat/davidcattoolsproject/pull/39) merged |
| A11b — Execution, Approval & Recovery | IN PROGRESS. F2 general user-repository mutation NOT AUTHORIZED, except `task advance` in this repository under Owner S4. | [#1019](https://github.com/davidcatcat/davidcattoolsproject/issues/1019) open; [PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84) merged |
| A11c — Integration & Real-world Acceptance | IN PROGRESS toward the 2026-11-26 Owner closure conditions: G1–G3 VERIFIED (G1 report, G3 PM wake evidence), G2 verified on the current Direct Developer execution, usage and budget evidence rather than the old Host/Worker path, C1–C5 observed, and Owner GO. GO scope is limited to the routes enabled now; Claude Developer and Codex Reviewer are PAUSED and outside this GO. A11c is DONE only after G1, G2 (new basis) and G3 VERIFIED, C1–C5 passed and Owner GO. | [PR #133](https://github.com/davidcatcat/davidcattoolsproject/pull/133) merged; [Owner A11c decision, #1019 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1019#issuecomment-7173127619) |
| A12 — Remote Frontends / Telegram Agent | FUTURE OPTIONAL, future work. Telegram pieces merged offline; #1165 and #1170 closed not merged; no token, network call, run command or approvals. WP5 Phase B / #1234 isolation acceptance UNKNOWN: #1234, #1144 and #1329 are CLOSED, but closure is record state only. Owner authorization channel OFF. | [#120](https://github.com/davidcatcat/davidcattoolsproject/issues/120) closed; [#2250](https://github.com/davidcatcat/davidcattoolsproject/pull/2250) merged; [#1350](https://github.com/davidcatcat/davidcattoolsproject/pull/1350) design merged |
| A13 — Distribution, Installation & Updates | FUTURE WORK. Owner-directed private wheel-only design (2026-11-01), implementation by PM dispatch. No implementation recorded here; issue closure does not establish it, so implementation status UNKNOWN. | [#396](https://github.com/davidcatcat/davidcattoolsproject/issues/396) closed; [#369](https://github.com/davidcatcat/davidcattoolsproject/issues/369) closed |

## Next steps

- A3.5: #683 is open. Dispatch the bounded W58 PM-first help/navigation slice, then W59–W63; each slice needs exact-head PTY tests and Owner Mac acceptance.
- A11b: keep every F2 phase NOT AUTHORIZED; `task advance` remains the only mutation path for this repository under #1019 (open). A11b.3 approval and any F2 phase need independent Security review and a separate Owner grant.
- A11c: per the 2026-11-26 Owner decision, the remaining work in order is: the G1 report, the G3 PM wake evidence, the usage collection fixes, then the C1–C5 observation window (C1 automatic closed loop; C2 usage and budget records; C3 stability; C4 delivery quality; C5 ten consecutive real ROOT tasks with at least two per ALLOWED Developer route), then Owner GO within the enabled-route scope. See the [Owner A11c decision, #1019 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1019#issuecomment-7173127619).
- A12: keep the Telegram path future and offline. Real Bot API egress, token, pairing, run command and go-live wait for the Owner and an independent security review; WP5 Phase B acceptance is UNKNOWN.
- A13: wait for PM dispatch of implementation; no implementation has started.
- A12 and A13 stay future work and do not move ahead of the A11c track.
- TUI attachments stay deferred (Owner 2026-11-03), after A13; no dispatch authorized.
