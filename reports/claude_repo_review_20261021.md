# Claude repo review + Reviewer token plan — 2026-10-21

**Author:** Claude (formal independent Reviewer), session `4c6f4ea6…`. Requested by the Owner: 「review repo, 上傳報告, 另外你的token快用完了, 也在報告中建議」.
**State observed:** 2026-10-20 ~18:00Z. main `4c97926b`; CI on main green.

## 1. Repo status

| Area | Status |
|---|---|
| main | `4c97926b` (#235 parked-code removal: −19k lines); CI green |
| **#234 rules slimming** | **Claude ACCEPT at `c69a8e58`**, CI PASS. Draft; the PM merges (Claude cannot: it changes `CLAUDE.md`). Always-loaded rules shrink ~82%. |
| #237 / #238 / #241 | Claude-authored docs reports, each with an independent documentation review. Ready for the PM to merge. |
| #240 | D4 realpath pin + failure stage; REVISE items open; background (D4 is off the critical path) |
| #239 | Codex coding/debug agents: closed by the PM after 4 Claude REVISE rounds (C-6 disposition) |
| Old drafts | #156/#168/#173/#194/#180/#195 dispositioned by the Coordinator/PM |
| D4 smoke | NOT LIVE VERIFIED. Attempt 1: pre-launch config error (symlinked executable; #238). Attempt 2: launched, then `provider.auth` (likely an expired native OpenCode login). Background work. |
| Developer routing | Owner 2026-10-21: Codex Developer while quota lasts; otherwise OpenCode as a Developer session (own D6 worktree, project permissions, Task Contract → PR → Claude review → CI) |
| D6 worktrees | 7 RETIRE_READY, 17 RETAIN_BLOCKED, 10 REVIEW_PENDING (several belong to merged PRs), 6 ACTIVE |

## 2. Findings / next actions (PM)

1. **Merge #234** (`--match-head-commit c69a8e58`). This is the biggest single quota saver for every agent.
2. **Merge #237/#238/#241.**
3. **Start the first OpenCode Developer-session task** under the 2026-10-21 routing. A small real fix is ideal. If the OpenCode login has expired, the Owner runs `opencode auth login` once.
4. **D6 cleanup:** retire the 7 RETIRE_READY trees and the REVIEW_PENDING trees whose PRs merged (#218/#226/#228/#229/#230/#235/#236). Inventory the old 10-19 RETAIN_BLOCKED trees.
5. **D4 (background):** finish #240, refresh the native login, and re-run the smoke with a fresh admission when convenient.

## 3. Claude Reviewer token budget — recommendations

The Owner reports that Claude's token budget is nearly used up. This session has run continuously since 10-20 with a very large context, so every turn re-reads that context. That is the same effect that exhausted the Codex PM's 5 h window.

**A. Start a fresh Claude Reviewer session (largest saving).**
- End this long session after this report. The next Reviewer session starts clean and reads only `AGENTS.md`/`CLAUDE.md` (much smaller after #234), this report and the specific PR under review.
- **Do not `claude -p --resume` this session for reviews.** A resumed review of it cost about US$2.35 per call (~1.37 M cached input tokens; #224 record). A fresh `claude -p` with a focused review packet is far cheaper.

**B. Spend Claude review only where it matters most.**

| Change type | Formal Claude review? | Interim reviewer when Claude quota is low |
|---|---|---|
| Credential / secret handling, Host admission, containment, sandbox/permissions | **Required.** Wait for Claude. | None. Queue it. |
| Policy changes that grant or remove authority | **Required** | None. Queue it. |
| Ordinary source changes | Required before merge (existing rule) | Independent GPT/Codex pre-review may proceed; merge waits for Claude |
| Docs-only reports / records | Optional | An independent GPT/Codex documentation review is sufficient (as done for #237/#238/#241) |

**C. Make each review request small.** Use the existing `CLAUDE-REVIEW-REQUEST: PR #N HEAD <SHA>` with scope, the **delta since the last reviewed SHA**, and specific questions. Re-reviews then cover only the delta, not the whole PR. Batch related small PRs into one request.

**D. When Claude quota is exhausted (existing D3 rule).** Keep each candidate in logical `WAITING_CLAUDE_REVIEW` with its exact SHA, scope and tests. Bound the backlog; no automatic paid fallback. On recovery, review the *current* head. Never present GPT/Codex pre-review or self-review as the formal Claude verdict.

**E. Reviewer conventions still in effect.** Publish verdicts on GitHub, with leading `<!-- activity: … -->` markers for the Owner's monitor. Bind merges to the reviewed head with `--match-head-commit`.

## 4. Not established

No live OpenCode inference; no G3/M2 same-Integrator continuation. Owner readability acceptance of the monitor Activity view: the Owner used it and asked for no changes.
