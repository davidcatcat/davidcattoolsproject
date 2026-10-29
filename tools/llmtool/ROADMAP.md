# LLMTool Roadmap

Current collaboration and execution authority is in [AGENTS.md](../../AGENTS.md). The previous roadmap text is preserved verbatim in the [2026-10-28 roadmap archive](../../docs/history/llmtool-roadmap-archive-20261028.md).

## Status

| Milestone | Status | Evidence PR/issue |
| --- | --- | --- |
| A0–A10 | DONE and merged; A2.3 Anthropic HTTP API: 不做 per Owner decision 033:A ([#525 c6468216854](https://github.com/davidcatcat/davidcattoolsproject/pull/525#issuecomment-6468216854); sheet [c6467792563](https://github.com/davidcatcat/davidcattoolsproject/pull/525#issuecomment-6467792563)) — Claude is used via the Claude Code CLI; the paid API is billed separately and has no current need. | A2.4–A2.6 and A0–A10 status recorded in the [roadmap archive](../../docs/history/llmtool-roadmap-archive-20261028.md). |
| A3.5 — Interactive Chat / TUI UX | Interactive chat DONE; incremental TUI UX **IN PROGRESS**. Existing production UI is retained; #682/W57 in formal review (not yet verified merged at this planning snapshot). See [TUI UI contract](../../docs/meetings/2026-10-22-llmtool-tui-ui-ux-decision.md). | [UI Agent #304](https://github.com/davidcatcat/davidcattoolsproject/issues/304), [Owner-approved incremental work #683](https://github.com/davidcatcat/davidcattoolsproject/issues/683), [W57 PR #682](https://github.com/davidcatcat/davidcattoolsproject/pull/682) |
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

## A3.5 — TUI UX improvement (Owner authorization 2026-10-30)

**Product position:** `llmtool chat --tui` is the **input and display frontend** for llmtool's existing AI-provider connection and PM orchestration. It is not a replacement for Claude Code/Codex, and must not implement another PM, dispatch policy, task store, provider, Reviewer, permissions engine or CI runner. The prior [2026-10-22 UI decision](../../docs/meetings/2026-10-22-llmtool-tui-ui-ux-decision.md) and [UI Agent assignment #304](https://github.com/davidcatcat/davidcattoolsproject/issues/304) remain binding. **No new A-series milestone.**

**Current baseline (source / accepted PR evidence; not blanket Owner live acceptance):** existing full-screen chat, multiline editing, slash picker, input-history search, conversation scroll/drag-to-copy, PM queue and interrupt/pause, PM working state, Markdown/code/diff rendering, theme, OSC 8 links, PM background/status line, PTY scripted user scenarios. Relevant delivery/verification includes #636, #661, #674, #681. **[W57 #682](https://github.com/davidcatcat/davidcattoolsproject/pull/682)** improves tool descriptions, PM history and /status and hides developer-only Task mode; at planning time its formal review/merge was still pending. Do not reopen delivered functions as new implementations.

The Owner authorizes **incremental improvement on the already delivered TUI**, tracked by [#683](https://github.com/davidcatcat/davidcattoolsproject/issues/683), subject to the existing PM, UI Agent, D6/dispatch, independent review, CI and real Mac UI acceptance rules:

| Priority | Scope | Acceptance / exclusions |
| --- | --- | --- |
| **P0 — operational usability (active)** | Preserve working scroll, CJK/Markdown, selection/copy, keyboard/multiline input, history/slash picker, PM state/queue; clearly separate ordinary PM discussion from authorized execution. After W57, identify evidence-backed gaps. **W58 first delivery:** concise, non-technical PM-first help/navigation over existing commands, hiding development-only entry points by default. | One small runnable UI-only slice (not another large redesign); no regression with long conversations, wheel/trackpad, selection, clipboard, resize, stream, Esc, queued messages or fallback. Exact-SHA PTY fake-provider tests **plus** separate Owner Mac Warp/Terminal.app real-use acceptance. Don't collide with unmerged W57. |
| **P1 — on-demand status and actions** | Chat/Tasks/Attention navigation, Task/Reviewer/CI evidence and state, session resume, usage/context, detailed logs **only where existing authorized Core APIs provide data**. | No synthetic state, fake Agent online presence, quota guesses or phantom /commands. Show unavailable or UNKNOWN with reason; data provenance/time where applicable. A view is not a new backend. |
| **P2 — user preferences** | Optional focus/verbose, status-line fields, keybindings/terminal modes, minimal appearance refinements. | Retain shipped theme and link support; avoid unnecessary dependencies, continuous polling, heavy repaint or background daemon. |

**Order of work:** finish ongoing W57 gates → #683 actual-code gap matrix (merged / WIP / not Mac-verified / defect / Core-dependent) → PM dispatches one bounded W58 UI Agent task → Developer self-test → independent cross-family Reviewer → exact-head CI + Mac PTY/Owner acceptance → next smallest real user-facing gap. **An issue or roadmap entry is not evidence that the Worker has started.** A11c real PM → Developer → Reviewer work stays the product-critical track; UI beautification cannot gate that cutover. The earlier 26/12/8 functional candidate inventory is *design input*, not a promise to add 46 new functions or 46 work packages.
