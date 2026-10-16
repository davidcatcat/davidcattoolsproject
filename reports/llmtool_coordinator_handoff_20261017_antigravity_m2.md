# llmtool Coordinator Handoff — 2026-10-17 (Antigravity / M2)

> Status: documentation-only snapshot. Source: ChatGPT coordinator conversation 2026-10-16–17 and observed GitHub records. Not execution, runtime permission, or independent approval. Recheck live GitHub state on takeover.
> Repository: https://github.com/davidcatcat/davidcattoolsproject
> Tracker: https://github.com/davidcatcat/davidcattoolsproject/issues/120
> Base at drafting: main `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39` (PR #166 squash merge). No local Mac Git actions performed.

## 1. Owner goal and nonnegotiable product boundaries

Owner urgently wants **llmtool to become usable and participate in its own development**, replacing the repeated manual workflow "ChatGPT review repo -> paste prompt into AI CLI -> wait -> paste results -> repeat". Optimize for actual task completion, not endless small slices, repeated QA, architecture speculation or fake-only milestones. A11c M1 dogfood already has actual model advice and developer adoption; M2 is the bounded real Developer/Reviewer/test/repair/PR-or-STOP loop, and is NOT complete.

Keep **Provider != Role != Agent**. llmtool supplies deterministic execution, transport, state, evidence/authority boundaries; a model-powered main AI/coordinator makes high-level task decomposition/routing decisions. Do not insert a broad intelligent coordinator into llmtool, duplicate Codex/Claude/Antigravity native agent features, or build another scheduler/daemon/ai-loop. Company LLM is an optional policy-authorized low-risk triage/compression provider, not an unconditional first hop or authority. Owner's company VPN may not be active; explicitly ask for VPN only at the stage a live company-LLM request is needed.

Owner permits substantial autonomous implementation within existing approved task boundaries; no need to stop after each routine technical step. Respect existing source/Git/egress/Live trial grants. Do not assert unsafe gates waived or equate a subagent review to the required formal independent Source review. Defect triage: SEV-0/1 and genuine release blockers now, lower severity appropriately deferred; avoid endless low-value repair/retest loops.

## 2. Recent chronological work and evidence

- PR #159 merged: roadmap emphasizes **M1a real llmtool advisory -> M1b developer adopts advice -> M2 bounded autonomous cycle**, without new milestones. The M1a evidence in Issue #120 used real `llmtool ask -> ClaudeCLIProvider -> Claude CLI` with exit0; M1b source work followed.
- PR #162 merged: deterministic bounded review evidence bundles (not live review).
- PR #163 merged: request/wait, receipt and structured Security/Functional verdict exchange (not auto wake).
- PR #164 merged: public decisions and STOP / no execution authority.
- PR #165 merged at `86a990eb4389044aa3196f25d679c2d73b4591a3`: foreground Reviewer Worker Source/Fake foundation, CLI JSON envelope parsing, bounded process lifecycle, role/runtime/receipt pins; NOT a live production Worker.
- Owner introduced **Antigravity CLI 1.4.0**, Google AI Pro, and actual **Gemini 4 Argon (Medium)**. In owner's terminal, interactive prompt returned `GEMINI_4_ARGON_OK`. Headless `agy -p "Reply GEMINI_4_ARGON_OK only." --model gemini-4-argon-medium --output-format json` returned status SUCCESS, response marker, 22.986926 s, 1 turn, input 14714, output 37, thinking 25, cache_read 0, total 14751 tokens. This is **USER_VERIFIED CLI smoke**, NOT llmtool adapter integration or a pricing/quota benchmark. Do not record personal login or conversation ID.
- Antigravity `/model` showed Gemini 4 Argon / 3.8 Flash, Claude Opus 5.5 / Sonnet 5.5, GPT-OSS 120B. Owner noticed Argon consumed tokens quickly; switched development preference to **Antigravity Claude Sonnet 5.5 Medium** and reserves Opus for high-difficulty or review. Native Claude Code and Codex CLI had reached their 5-hour limits at that time; verify current capacity; Antigravity Claude subscription path is distinct and not proven to share those limits.
- Gemini/Antigravity performed WJ1 three-case `bounded_terminate` cleanup fix: expired deadline still sends signal to owned group; group EPERM triggers direct-child fallback without claiming full group cleanup; failed `own()` PGID validation retains safe direct-child cleanup. Red-first 3 failing tests; 173 affected tests PASS; same-runtime reviewer subagent Security/Functional APPROVE (not original Claude review). PR #166 squash-merged into main as `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39`, exact PR head `5e58382214f6ae53217edc53c5b63850aa920fef`. PR head Python 3.9/3.14 CI reported SUCCESS. Formal original Claude WJ1 delta review remained **outstanding even after merge**; post-merge CI at reporting time was still in progress. See https://github.com/davidcatcat/davidcattoolsproject/pull/166 and https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6219886836 .
- PR #167 open: **docs-only** `reports/llmtool_antigravity_cli_user_verified_20261016.md` documenting above USER_VERIFIED CLI smoke, one new file, no adapter/runtime/Roadmap changes; diff-check validation only. https://github.com/davidcatcat/davidcattoolsproject/pull/167 . Not a prerequisite for live Worker.
- At handoff, other open PRs #156 (no self-handoff collaboration policy docs), #154 and #149 (older coordinator handoffs). Avoid treating old docs as newer authority. #161 Sky Blue UX HOLD; #153 CLOSED/HOLD; do not reactivate.
- At most recent lookup, main HEAD `9fa1aaba`; no newer product PR beyond #166 and docs #167. This is a timestamped lookup, not a prediction of current live state.

## 3. Real Reviewer Worker Trial: exact blockers

**REVIEWER_JOB_TRIAL = BLOCKED / NOT RUN**, not FAIL or PASS. The public `review_authority_factory()` remains `UnavailableReviewAuthority`. WJ5 performed **read-only** `claude --help` inspection (installed Claude Code 2.1.301): `--print`, `--output-format`, `--no-session-persistence`, `--safe-mode`, `--tools`, `--model` options were present; binary symlink target 260406992 bytes (<256MiB), requiring resolved-path pin. **Actual flags behavior is NOT_VERIFIED live.** No real model call or production Host/ledger done.

From Issue #120 comment 6219886836, remaining before Live Trial:
1. **Original independent Claude Reviewer** Session prefix `081de22e` must conduct formal focused WJ1 post-merge delta Security/Functional source review of PR #166 (PID/PGID ownership, expired deadline, EPERM, child cleanup, pending/confirmed evidence), cite exact commits, publish verdict; subagent approval cannot substitute.
2. **LJ1**: distinct `reviewer_auth_home` separated from Owner HOME.
3. Original **Codex Integrator** Session prefix `01a12e4b`, with Owner/Integrator authority as applicable, must establish narrowly scoped one-use **route/data/cost/Host/ledger grant** and HostPin/registration, after checking actual latest grants and accepted constraints.
4. Run bounded real `llmtool -> Reviewer CLI -> structured verdict/receipt` trial only after grants/registration, with model/CLI flags, process termination/reaping, timeout, accurate usage/unknown, status, output and evidentiary provenance. No automatic PR/merge power inferred from a verdict.
5. Then implement/verify bounded review-repair-recheck-to-eligible-PR or STOP loop. Real F2 repo mutation and other production/Native/Company Live gates remain separate.

**NEXT_AI: Original Claude independent Reviewer (081de22e), then original Codex Integrator (01a12e4b).** If either native CLI remains quota-limited, do not misrepresent Antigravity Sonnet acting in the same model family as the original session; work on separately authorized nonblocking tasks or wait for genuine role availability. No artificial agent impersonation.

## 4. Standard ChatGPT Coordinator operating protocol

Upon owner message `review repo`:
- Always **read fresh GitHub evidence**: latest main SHA, newest commits, active PR status/body/reviews/CI as available, Issue #120 *newest comments*, Roadmap/WORKFLOW and relevant skill only where needed; prefer connected GitHub integration. Reconcile conflicting claims. A local Mac branch/runtime cannot be verified from GitHub.
- Report in **Traditional Chinese**, concise but substantive:
  1. **Repo snapshot** (timestamp, exact main SHA, merged/open PRs, real vs reported evidence).
  2. **Progress since last review** (actual code/results, not amount of paperwork).
  3. **Blockers and severity** (owner-action required vs agent-resolvable; `SOURCE/FAKE PASS`, `USER_VERIFIED`, `LIVE PASS`, `BLOCKED`, `NOT_VERIFIED` separately).
  4. **NEXT_AI: exact provider/role/session/worktree if known**, and **one immediately copyable complete prompt**, unless currently running and no intervention required. Don't omit the next action merely because the owner said only `review repo`.
  5. **Distance to usable llmtool/self-hosting**: M1 achieved, M2 true Reviewer Worker trial, bounded repair, audited PR-or-STOP and one-command E2E still missing; distinguish first usable dogfood from full A11 acceptance.
- Do not repeatedly hand authorized work back to the same Integrator or require owner to paste every micro-task. Executor should self-review with an appropriately separate subagent, fix genuine blockers in bounded rounds, run targeted tests and applicable full/exact-head CI, publish deliverable and continue authorized work; formal independent required QA remains distinguishable. When handoff truly necessary, name the **different** actual recipient and precise copyable task. Do not falsely claim GitHub comments automatically wake CLIs.
- **Prompt convention:** directly identify `TO: [CLI / role / actual session]`, task/Issue/PR, pinned source/branch/worktree (verify freshness), goal + exact in/out scope, nonnegotiable gates, practical steps, tests/evidence, completion criteria and reporting. Include `你需要做：無` when owner intervention truly unnecessary, or name one specific intervention when unavoidable. Owner dislikes repeated permissions for routine already-authorized operations.
- **Avoid wasted Tokens:** do not rescan whole repo/repeat same-head passing tests; reuse hashed/pinned evidence; targeted red-green for actual changed code; do not loop LOW issues. Record actual usage when exposed, else UNKNOWN. Antigravity Sonnet Medium is the current efficiency experiment; Argon smoke's 14,714 input tokens for tiny output is not a comparable end-to-end benchmark.
- **Safety/permissions:** same-site reviewer Subagent is not the formal original independent Reviewer; no unreviewed live egress / Host production grant / Git F2, no spontaneous third Mac worktree, no reset/clean/stash/force or mutation of peers/other agent roots. Owner's existing two fixed Developer worktrees are Codex and Claude; Antigravity was observed working from canonical repo on an already merged feature branch, reported clean; no forced switch to main necessary. Do not treat clean GitHub PR as local evidence.
- **Company LLM** already had an earlier working OpenAI-compatible gateway smoke, but current live VPN/route is unverified; only initiate company route after connectivity and task/data authorization. No automatic fallthrough to expensive APIs without permission.
- **Git coordination:** use reviewed task-scoped PRs/Issue #120 comments for durable data, immutable SHA, genuine CLAIM/RELEASE/C3 where applicable, real independent approvals and actual CI. Documentation-only PRs can be merged separately and do not block product progress. Do not write roadmap implementation status from smoke alone.

## 5. Immediate next actions

A. Original Claude independent Reviewer: read PR #165/#166 + Issue120 #6219886836, perform **focused WJ1 delta Security/Functional review** against exact `5e583822...` and merged `9fa1aaba...`; publish verdict with precise findings to GitHub, no repeated blanket architecture audit.

B. Original Codex Integrator: on acceptable independent review, validate real local state and HostPin; plan/configure LJ1 isolated auth HOME and one-use explicit ReviewJob trial authority (routing, data boundary, cost and ledger, executable registration). If Owner decision truly necessary, request minimal scoped approval once. Preserve `NOT_AUTHORIZED` until granted.

C. Run real bounded Reviewer Worker trial; publish actual call outcome, structured verdict receipt, cleanup status, cost/usage or UNKNOWN, then advance M2 loop.

D. PR #167 may be docs-reviewed and merged independently; no forced priority ahead of B/C. #156/#154/#149 are old docs-only open PRs; evaluate staleness before merging.

## 6. Reusable takeover prompt for new ChatGPT conversation

接手我在 GitHub `davidcatcat/davidcattoolsproject` 的 llmtool Coordinator 角色。請先讀最新 GitHub main、Issue #120 最新留言、PR #165/#166/#167，以及交接文件 `reports/llmtool_coordinator_handoff_20261017_antigravity_m2.md`（其 PR 尚未合併時讀交接分支）。不要假設舊 SHA 仍是最新。目標是盡快讓 llmtool 真正參與自身開發並減少我人工轉交提示。保留 Provider != Role != Agent、既有審查和 safe-git 規範；不要重做 Orchestrator。最新 Real Worker Trial 為 BLOCKED，原始 Claude 081de22e WJ1 Delta Review 尚待完成，其後原始 Codex Integrator 01a12e4b 處理 LJ1、一次性 Grant、HostPin 和實測。每次我說 `review repo`，主動提供最新客觀狀態、NEXT_AI、可直接貼給該 AI 的完整提示及距離首次上線剩餘工作。不要讓文件／假測試取代真正 Live 驗收。
