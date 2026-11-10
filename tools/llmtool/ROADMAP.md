# LLMTool Roadmap

Current collaboration and execution authority is in [AGENTS.md](../../AGENTS.md). The previous roadmap text is preserved verbatim in the [2026-10-28 roadmap archive](../../docs/history/llmtool-roadmap-archive-20261028.md).

## Status

| Milestone | Status | Evidence PR/issue |
| --- | --- | --- |
| A0–A10 | DONE and merged; A2.3 Anthropic HTTP API: 不做 per Owner decision 033:A ([#525 c6468216854](https://github.com/davidcatcat/davidcattoolsproject/pull/525#issuecomment-6468216854); sheet [c6467792563](https://github.com/davidcatcat/davidcattoolsproject/pull/525#issuecomment-6467792563)) — Claude is used via the Claude Code CLI; the paid API is billed separately and has no current need. | A2.4–A2.6 and A0–A10 status recorded in the [roadmap archive](../../docs/history/llmtool-roadmap-archive-20261028.md). |
| A3.5 — Interactive Chat / TUI UX | Interactive chat DONE; incremental TUI UX **IN PROGRESS**. W57 MERGED via merge train 53b (`e77e1f83`, #696/#701). W58 (PM-first help/navigation) next; P1 view design posted ([#683](https://github.com/davidcatcat/davidcattoolsproject/issues/683#issuecomment-6552986096)), slices W59–W63 start after W58. See [TUI UI contract](../../docs/meetings/2026-10-22-llmtool-tui-ui-ux-decision.md). | [UI Agent #304](https://github.com/davidcatcat/davidcattoolsproject/issues/304), [Owner-approved incremental work #683](https://github.com/davidcatcat/davidcattoolsproject/issues/683), [W57 PR #682](https://github.com/davidcatcat/davidcattoolsproject/pull/682) |
| A11a — Observability & Diagnostics | DONE and merged. | [PR #26](https://github.com/davidcatcat/davidcattoolsproject/pull/26) |
| P1 — Failure Usage Preservation | DONE and merged. | [PR #39](https://github.com/davidcatcat/davidcattoolsproject/pull/39) |
| A11b — Execution, Approval & Recovery | IN PROGRESS; F2 general user-repository mutation is NOT AUTHORIZED, except `task advance` in this repository under Owner S4 (#1019). | [PR #32](https://github.com/davidcatcat/davidcattoolsproject/pull/32), [PR #43](https://github.com/davidcatcat/davidcattoolsproject/pull/43), [PR #49](https://github.com/davidcatcat/davidcattoolsproject/pull/49), [PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84), [issue #97](https://github.com/davidcatcat/davidcattoolsproject/issues/97) |
| A11c — Integration & Real-world Acceptance | IN PROGRESS; M1b real-task adoption and full M2 are NOT_VERIFIED; T1 is HOLD_REPRIORITIZE. | [PR #128](https://github.com/davidcatcat/davidcattoolsproject/pull/128), [PR #129](https://github.com/davidcatcat/davidcattoolsproject/pull/129), [PR #131](https://github.com/davidcatcat/davidcattoolsproject/pull/131), [PR #133](https://github.com/davidcatcat/davidcattoolsproject/pull/133), [issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120) |
| A12 — Remote Frontends / Telegram Agent | FUTURE OPTIONAL. v1 design (read-only status, notifications, approve/reject only via the A11b.3 `REMOTE_OWNER` issuer; remote-approvable classes empty by default) posted on [#120](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6552977888). WP5 Phase B work-package authorization and #1234 account isolation are IN PROGRESS offline; the formal Owner authorization channel stays OFF, see [WP5 Phase B](#a12-wp5-phase-b-and-1234-account-isolation). | [issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120) |
| A13 — Distribution, Installation & Updates | **OWNER-DIRECTED DESIGN (2026-11-01); implementation by PM dispatch.** Private wheel-only distribution to other users first (D1, D9); open source later via a clean-history repo (D5). Each installation's user is its own Owner (D3). POSIX only (D4). Design specs posted; no implementation started. See [A13 — Private distribution](#a13--private-distribution-owner-decisions-2026-11-01-396-d1d14). | [issue #396](https://github.com/davidcatcat/davidcattoolsproject/issues/396) (decisions D1–D14), trust root [#369](https://github.com/davidcatcat/davidcattoolsproject/issues/369) |

## Next steps

- Verify the first M1a/M1b real-task adoption.
- Gateway-only switch (Owner 2026-11-11, [#120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)): retire the company-gw direct path, move gw-agent to its private Gateway profile, then run one real end-to-end gw-agent task and the efficiency report.
- Finish the separately granted production foreground Worker trial, then verify same-Integrator resume and delegated-Worker M2.
- Continue A11b F2 real-repository mutation design and A11b.3 approval semantics; execution remains NOT AUTHORIZED.
- A11b.3 approval issuer and A11b.5–6 budgets/failure gates: design posted ([#97](https://github.com/davidcatcat/davidcattoolsproject/issues/97#issuecomment-6552954731)); implementation needs independent Security review. Finding: most current "Owner approval" checks match URL shape only and do not verify the comment author.
- F2 real-repository mutation: action/approval contract drafted ([#97](https://github.com/davidcatcat/davidcattoolsproject/issues/97#issuecomment-6552968268)); remains **NOT AUTHORIZED** until Security review and a separate Owner grant, phase by phase (F2.0–F2.4). Owner S4 (#1019) lets `task advance` become the normal push, PR and merge path for this repository, step by step; every other F2 phase stays NOT AUTHORIZED.
- Preserve the applicable A11b, QA, Mac and route gates before full native cutover.

## A3.5 — TUI UX improvement (Owner authorization 2026-10-30)

**Product position:** `llmtool chat --tui` is the **input and display frontend** for llmtool's existing AI-provider connection and PM orchestration. It is not a replacement for Claude Code/Codex, and must not implement another PM, dispatch policy, task store, provider, Reviewer, permissions engine or CI runner. The prior [2026-10-22 UI decision](../../docs/meetings/2026-10-22-llmtool-tui-ui-ux-decision.md) and [UI Agent assignment #304](https://github.com/davidcatcat/davidcattoolsproject/issues/304) remain binding. **No new A-series milestone.**

**Current baseline (source / accepted PR evidence; not blanket Owner live acceptance):** existing full-screen chat, multiline editing, slash picker, input-history search, conversation scroll/drag-to-copy, PM queue and interrupt/pause, PM working state, Markdown/code/diff rendering, theme, OSC 8 links, PM background/status line, PTY scripted user scenarios. Relevant delivery/verification includes #636, #661, #674, #681. **[W57 #682](https://github.com/davidcatcat/davidcattoolsproject/pull/682)** improves tool descriptions, PM history and /status and hides developer-only Task mode; W57 merged in `e77e1f83`. Do not reopen delivered functions as new implementations.

The Owner authorizes **incremental improvement on the already delivered TUI**, tracked by [#683](https://github.com/davidcatcat/davidcattoolsproject/issues/683), subject to the existing PM, UI Agent, D6/dispatch, independent review, CI and real Mac UI acceptance rules:

| Priority | Scope | Acceptance / exclusions |
| --- | --- | --- |
| **P0 — operational usability (active)** | Preserve working scroll, CJK/Markdown, selection/copy, keyboard/multiline input, history/slash picker, PM state/queue; clearly separate ordinary PM discussion from authorized execution. After W57, identify evidence-backed gaps. **W58 first delivery:** concise, non-technical PM-first help/navigation over existing commands, hiding development-only entry points by default. | One small runnable UI-only slice (not another large redesign); no regression with long conversations, wheel/trackpad, selection, clipboard, resize, stream, Esc, queued messages or fallback. Exact-SHA PTY fake-provider tests **plus** separate Owner Mac Warp/Terminal.app real-use acceptance. Don't collide with unmerged W57. |
| **P1 — on-demand status and actions** | Chat/Tasks/Attention navigation, Task/Reviewer/CI evidence and state, session resume, usage/context, detailed logs **only where existing authorized Core APIs provide data**. | No synthetic state, fake Agent online presence, quota guesses or phantom /commands. Show unavailable or UNKNOWN with reason; data provenance/time where applicable. A view is not a new backend. |
| **P2 — user preferences** | Optional focus/verbose, status-line fields, keybindings/terminal modes, minimal appearance refinements. | Retain shipped theme and link support; avoid unnecessary dependencies, continuous polling, heavy repaint or background daemon. |

**Order of work:** finish ongoing W57 gates → #683 actual-code gap matrix (merged / WIP / not Mac-verified / defect / Core-dependent) → PM dispatches one bounded W58 UI Agent task → Developer self-test → independent cross-family Reviewer → exact-head CI + Mac PTY/Owner acceptance → next smallest real user-facing gap. **An issue or roadmap entry is not evidence that the Worker has started.** A11c real PM → Developer → Reviewer work stays the product-critical track; UI beautification cannot gate that cutover. The earlier 26/12/8 functional candidate inventory is *design input*, not a promise to add 46 new functions or 46 work packages.

## A13 — Private distribution (Owner decisions 2026-11-01, #396 D1–D14)

**Product position:** another user installs llmtool from a wheel and uses it as the Owner does — in their own environment, with their own repo, providers, roles and CI. The Owner's Mac keeps working unchanged after a one-time `trust init`. No security check is removed; every new setting fails closed when absent.

| Order | Work package | Depends on | Spec |
| --- | --- | --- | --- |
| 0 | SF-1 lsof path, SF-2 review origin allowlist, SF-3 doctor `overall`/`--strict` | — | [#396](https://github.com/davidcatcat/davidcattoolsproject/issues/396#issuecomment-6552607692) |
| 1 | Owner-only `trust.json` (PR-1…PR-10) | Owner Mac `trust verify-legacy` all MATCH before PR-3 | [#369](https://github.com/davidcatcat/davidcattoolsproject/issues/369#issuecomment-6552692881) |
| 2 | User-level dispatch policy v2 and roles (PR-1…PR-9) | 1 | [#396](https://github.com/davidcatcat/davidcattoolsproject/issues/396#issuecomment-6552721657) |
| 3 | Packaging and privacy T1 (A1…A7, B3) | 2 (PR-4 merged with A2) | [#396](https://github.com/davidcatcat/davidcattoolsproject/issues/396#issuecomment-6552756126) |
| 4 | PM adapter interface | — | [#396](https://github.com/davidcatcat/davidcattoolsproject/issues/396#issuecomment-6552740555) |
| 5 | Install experience: `setup`, version/migrate, uninstall, private release (R1…R10; R1/R2 can start first) | 1–3 | [#396](https://github.com/davidcatcat/davidcattoolsproject/issues/396#issuecomment-6553006696) |
| 6 | Pluggable execution backend local/ssh/github-actions (PR-1…PR-9; PR-9 needs D13 approval) | 1, #417 DET-1 | [#417](https://github.com/davidcatcat/davidcattoolsproject/issues/417#issuecomment-6552996403) |

## A12.1 — Telegram v1: read-only status and notifications (#1160)

Owner order puts A12.1 Telegram after the Mac CLI and before the A12.2 local Web UI. Requirements are not yet agreed. This work builds only what the posted [v1 design](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6552977888) fixes. Open questions wait for the Owner.

- Read-only status and notifications only.
- No approvals, Task creation, execution, free chat to the PM or STOP from Telegram.
- No standing service, listener, webhook, launchd item or runner.
- No egress except the Telegram Bot API.
- Every outbound text passes the existing sensitive-content screens.
- Only opaque Task codes, states, counts and times leave.
- A bot token and a chat ID are credentials held by the Owner. No AI creates, requests, prints or stores them, so the real transport and setup wait for the Owner.

| Part | Needs | Status |
| --- | --- | --- |
| 1. Channel module `telegram_channel.py`: status text, notification events, screened delivery over an injected transport | Nothing | PR #1170; offline, not wired |
| 2. Per-chat cursor and dedupe `telegram_cursor.py` | Nothing | PR #1165; offline, not wired |
| 3. Real Bot API transport, token file, pairing and allowlist | Owner enables new data egress and holds the token | Not started |
| 4. Foreground run command | Owner decides run mode | Not started |
| 5. Remote approve and reject | A11b.3 `REMOTE_OWNER` issuer, Security review and an Owner grant | Not started |

The cursor follows the same private state-file rules as the WP5 GitHubSync poll ledger (#1144); a shared helper can follow once both merge.

Gap analysis and open Owner questions: [#1160 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1160#issuecomment-6705724171).

## A12 WP5 Phase B and #1234 account isolation

Owner decisions on #1144, relayed verbatim in [c6744205705](https://github.com/davidcatcat/davidcattoolsproject/issues/1144#issuecomment-6744205705); design [#1350](../../docs/design/a12-wp5-phase-b-workpackage-authorization-1144.md); Task plan [c6744333732](https://github.com/davidcatcat/davidcattoolsproject/issues/1144#issuecomment-6744333732).

- GitHub Environment Approval is the main approval source; until #1234 isolation acceptance it is test and accident protection only.
- The Owner approves a work package once; Tasks, repairs, redo PRs, tests and merges inside its scope need no new approval.
- Defaults: run_cap 5 Developer runs and repairs, expiry 7 days, cost cap equal to the existing budget; the PM never raises them.
- No valid authorization means WAITING_OWNER.
- No second PM, scheduler, TaskStore or authorization system.
- A11c and other authorized work do not wait for Phase B.

| Step | Part | Status |
| --- | --- | --- |
| 1 | #1234 account and credential isolation: boundary audit design, minimal Owner steps, isolation acceptance | In progress |
| 2 | B0 GitHub Environment Approval PoC: design, then a test-only workflow after the Owner creates the protected environment and AI posts as ddavidccatssun | Planned |
| 3 | B1 work-package authorization record, validator and approval checker (offline) | Planned |
| 4 | B2 store, inheritance (#1329), revocation and run_cap counting (offline) | Planned |
| 5 | B3 live ChatGPT to GitHub to llmtool Core to PM to Developer to Reviewer to CI acceptance | After step 1 acceptance |

The formal Owner authorization channel stays OFF until #1234 isolation acceptance passes.

## Future backlog — TUI attachments (DEFERRED, Owner 2026-11-03)

**Position:** Last in this Roadmap, **after A13**. Parked for future consideration; not part of active A3.5 W58–W63, A11c or A13 delivery. This is an item in the existing backlog, **not a new A-series milestone**.

- **Images:** clipboard/screenshot paste, explicit local-path/drag input, multiple images and mixed text + image prompts, with clear attachment status and unsupported-provider feedback.
- **Text files:** explicit attach of TXT/MD/LOG/JSON/YAML/CSV and analysis alongside the user's prompt; validate source, size, permissions and content before transfer.
- **Later optional documents:** evaluate PDF/DOCX/XLSX after the first attachment workflow proves useful; do not promise unsupported native file or model capabilities.
- **Architecture boundary:** TUI only collects/displays attachment references. Core owns authorized attachment access, lifetime, safety and provider capability checks; PM/runtime adapters deliver content to Claude/Codex/other providers only when supported and authorized. Never replace protected filesystem boundaries with arbitrary path access.
- **Acceptance when resumed:** real macOS terminal paste/path UX plus proof that the target model actually received each attachment (not merely a displayed filename); multi-file and unsupported-provider/error cases must be explicit.

**Authority/status:** Research and backlog only. **NOT STARTED / NO DISPATCH AUTHORIZED.** Do not create tasks, dispatch agents, change providers/Core, or move this ahead of the current Roadmap until the Owner explicitly re-prioritizes it.
