# Claude repo review + Reviewer token plan — 2026-10-21

**Author:** Claude (formal independent Reviewer), session `4c6f4ea6…`. Requested by the Owner: 「review repo, 上傳報告, 另外你的token快用完了, 也在報告中建議」.
**State observed:** 2026-10-20 ~18:00Z. main `4c97926b`; CI on main green. This is a historical snapshot, not current queue state.

**Superseding update (2026-10-21):** #234 and #237 have since been merged. The Owner authorized an independent Codex Reviewer (GPT-6.1-Sol, Medium) as a temporary formal Reviewer while Claude quota is unavailable. Codex verdicts do not need a later duplicate Claude review. OpenCode and Codex are normal Developer routes; D4/G3/M2 is background. Company `gw` is a third, PM-selected low-risk Developer route through llmtool, without model-side file or execution authority.

## 1. Repo status

| Area | Status |
|---|---|
| main | `4c97926b` (#235 parked-code removal: −19k lines); CI green |
| **#234 rules slimming** | **Claude ACCEPT at `c69a8e58`**, CI PASS. Draft; the PM merges (Claude cannot: it changes `CLAUDE.md`). Always-loaded rules shrink ~82%. |
| #237 | Independent Codex Reviewer ACCEPT; merged after the snapshot. |
| #238 / #241 | Independent Codex Reviewer FIX; remain open pending corrections and rereview. |
| #240 | D4 realpath pin + failure stage; REVISE items open; background (D4 is off the critical path) |
| #239 | Codex coding/debug agents: closed by the PM after 4 Claude REVISE rounds (C-6 disposition) |
| Old drafts | #156/#168/#173/#194/#180/#195 dispositioned by the Coordinator/PM |
| D4 smoke | NOT LIVE VERIFIED. Attempt 1 receipt is `UNKNOWN / OPENCODE_RUNTIME_INVALID`; launch, credential handoff, request count, usage and cleanup are UNKNOWN. Symlink rejection is a supported inference. Attempt 2 returned `OPENCODE_AUTH_REQUIRED` / `provider.auth`, no assistant reply or successful completion ACK; request count and usage UNKNOWN, cleanup observed. D4 is background. |
| Developer routing | Owner 2026-10-21: Codex and OpenCode are normal Developer routes; D4/G3/M2 is not a prerequisite. Company `gw` is an additional bounded Developer route through llmtool. |
| D6 worktrees | 7 RETIRE_READY, 17 RETAIN_BLOCKED, 10 REVIEW_PENDING (several belong to merged PRs), 6 ACTIVE |

## 2. Findings / next actions (PM)

1. #234 has since merged at `145ad92e`; its Claude ACCEPT and exact-head CI PASS were recorded before merge.
2. #237 has since merged after independent Codex Reviewer ACCEPT. Keep #238 and #241 open until the reported facts are corrected and independently rereviewed.
3. Prepare the first OpenCode Developer task under the 2026-10-21 routing. The report's proposed login-refresh instruction is unsupported and withdrawn; do not infer a credential cause or request an authentication change from the retained evidence.
4. **D6 cleanup:** the counts in this 2026-10-20 snapshot are historical. Recheck current ownership, PR state and registry before retiring any tree.
5. **D4 (background):** preserve both attempt records; do not replay consumed jobs. Keep #240 as a separate repair task.

## 3. Claude Reviewer token budget — recommendations

The Owner reports that Claude's token budget is nearly used up. This session has run continuously since 10-20 with a very large context, so every turn re-reads that context. That is the same effect that exhausted the Codex PM's 5 h window.

**A. Start a fresh Claude Reviewer session (largest saving).**
- End this long session after this report. The next Reviewer session starts clean and reads only `AGENTS.md`/`CLAUDE.md` (much smaller after #234), this report and the specific PR under review.
- **Do not `claude -p --resume` this session for reviews.** A resumed review of it cost about US$2.35 per call (~1.37 M cached input tokens; #224 record). A fresh `claude -p` with a focused review packet is far cheaper.

**B. Spend Claude review only where it matters most.**

| Change type | Formal review | Temporary reviewer during Claude quota shortage |
|---|---|---|
| Credential / secret handling, Host admission, containment, sandbox/permissions | **Required** | Independent Codex Reviewer Agent (GPT-6.1-Sol, Medium), with full formal Security scope |
| Policy changes that grant or remove authority | **Required** | Independent Codex Reviewer Agent (GPT-6.1-Sol, Medium), with full formal scope |
| Ordinary source changes | Required before merge | Independent Codex Reviewer Agent (GPT-6.1-Sol, Medium) |
| Docs-only reports / records | Required when the Task Contract requests formal review | Independent Codex Reviewer Agent (GPT-6.1-Sol, Medium) |

**C. Make each review request small.** Use the existing `CLAUDE-REVIEW-REQUEST: PR #N HEAD <SHA>` with scope, the **delta since the last reviewed SHA**, and specific questions. Re-reviews then cover only the delta, not the whole PR. Batch related small PRs into one request.

**D. Temporary formal Reviewer substitution.** Owner decision 2026-10-21 authorizes a separate, read-only Codex Reviewer Agent using GPT-6.1-Sol / Medium to perform the formal Security/Functional/QA gate until Claude quota returns (expected 2026-10-25). This is not a pre-review. Do not wait solely for Claude quota and do not repeat accepted reviews after Claude returns. No paid fallback.

**E. Reviewer conventions still in effect.** Publish verdicts on GitHub, with leading `<!-- activity: … -->` markers for the Owner's monitor. Bind merges to the reviewed head with `--match-head-commit`.

## 4. Not established

No live OpenCode inference; no G3/M2 same-Integrator continuation. These facts were correct at the snapshot time; this report itself does not establish later runtime outcomes. Owner readability acceptance of the monitor Activity view: the Owner used it and asked for no changes.
