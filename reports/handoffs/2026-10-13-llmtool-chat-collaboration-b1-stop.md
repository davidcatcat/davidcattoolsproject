# llmtool Chat Handoff — 2026-10-13 03:20 Asia/Taipei

> Purpose: durable handoff for a new ChatGPT conversation. This document records the latest *published GitHub evidence*, decisions, role assignment, and reporting contract. It is NOT a product authorization or a claim that any local uncommitted bytes have been uploaded.

## Canonical source of truth

- Repository: `davidcatcat/davidcattoolsproject`.
- Tracking issue: [#120](https://github.com/davidcatcat/davidcattoolsproject/issues/120).
- Last verified remote main: `055d1828d12cd8a4294613042ec4bc6e216a754a`; do not assume Mac canonical checkout is synchronized (last observed local main `83be9f6294c7fda515a1ec939823d02b65aa2e4d`).
- [PR #129](https://github.com/davidcatcat/davidcattoolsproject/pull/129): S1b review contract merged.
- [PR #130](https://github.com/davidcatcat/davidcattoolsproject/pull/130): S1b-B production-enablement **design contract only** merged; UTC consumed-day R1 resolved in contract. No B runtime follows from merge.
- [PR #131](https://github.com/davidcatcat/davidcattoolsproject/pull/131): S1b Product A **fake-only code** merged, Claude independent Security/Functional APPROVE; post-merge Python3.9/3.14 CI PASS. Real Claude remains unauthorized.
- Product goal: lower premium Codex/Claude tokens through cost-aware developer workflows with company LLM, without sacrificing reliability; autonomous review/handoff is an *enabler*, not the primary objective. Prefer bounded, measurable dogfood over a second agent/controller framework.

## Live status and urgent handoff

**Latest [Issue #120 B1 STOP report](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6130990707):**

- Task `ISSUE120-S1B-B1` assigned to existing **Codex Developer**; branch `feat/codex-issue120-s1b-b1-v1-20261013` from remote main `055d1828...`. G7 C2/C3/C4, independent starting baseline and shared-ref RELEASE were completed.
- **STOP / PRESERVE**: 11 source/test files in Codex fixed worktree are **UNCOMMITTED**. No B1 PR, push, complete-suite final acceptance, final-source native evidence, exact-head CI or Claude product approval yet. **Do not reset, stash, clean, re-checkout, switch branch, delete ignored assets, assume completion, or start another developer task.**
- Candidate manifest SHA256 `2c02935fe565b3547e741dab954b65f52301cf32aa5129648ade1c1152554f71`; interim digest only, not approved final SHA.
- Work-in-progress includes HostServices, production issuer/registration/route read-only validation, provider-specific reuse of ReceiptLedger with atomic UTC consumed day/cap/replay, fake launch adapter, and fixes for A-R1–R4. Product factory stays unavailable; no real provider/data export/production host file/ledger.
- Intermediate A tests **67 PASS** at earlier bytes; independently accepted initial fixture-plan **16 PASS** before later code changes. These are **not final-source acceptance**.
- **Blocking event**: independent expanded native fixture-plan reviewer hit platform **usage limit**, produced *no* approval for expanded 18 authority + 10 ledger + 2 policy + five Darwin concurrency/crash scenarios. Developer correctly preserved the branch/source and stopped before those tests. This is reviewer availability, not an observed B1 code failure.
- Immediate next action: **available independent non-author reviewer or Coordinator** reviews the expanded native fake-fixture execution plan against exact preserved source, approves/rejects safe execution; only then existing Codex Developer resumes affected tests, documentation, final full/native, Draft B1 PR and CI. Do not use author self-approval; no need to rerun completed G5/G6.
- Native test plan: owned venv Python `-B`; bounded related unittest; at most two owned child Python processes, 15s communicate; fresh physical 0700 fixture dirs, 0600 host/ledger; no real provider, network, credentials, shared worktree writes or cleanup of unrelated processes. Darwin cases include same receipt race, distinct receipt cap race, crash before COMMIT, consumed-before-effect crash, post-response crash/recovery and UTC day charge.
- Preserve exact current source and private baselines. GitHub cannot see those uncommitted files.

## AI roles and decision authority

| Role | Existing instance (reconfirm actual session/CWD) | Work |
| --- | --- | --- |
| ChatGPT Coordinator | this/new conversation | Read latest GitHub evidence, prioritize, decide severity/owner/gate, give a direct paste-ready prompt; do not pretend to modify Mac or launch CLI |
| Codex Integrator | canonical repo Codex, previously `ttys000` | Independent integration/CI/PR gates, read-only or properly authorized repository operations, decision log, guarded merge when explicitly authorized |
| Codex Developer | dedicated locked Codex worktree, previously `ttys001` | **Current B1 author**, owns 11 uncommitted files; does source/tests/Draft PR only after independent fixture-plan gate |
| Claude Developer | dedicated locked Claude worktree, previously `ttys004` | Previously authored B contract PR#130; no active assignment presently; never reviews own authored B |
| Claude Reviewer | independent canonical read-only Claude session (earlier `ttys003`, dynamic) | Non-author Security+Functional source review, severity/reproduction and independent probes; cannot edit or merge |
| Company LLM `gw` | configured llmtool `company-gw` profile on Mac | Bounded synthetic development triage; not a production authority, no direct gateway substitution |

Terminal tty, PID and model session labels can change: verify actual root, session/role, venv, registered branch before assigning work. Fixed developer worktrees are distinct from canonical review/integration root. Review request comments do not invoke CLI automatically.

## Defect policy and outstanding gates

Use the [Issue #92] grading approach: **NOW** = function wrong/security boundary/release blocker; **LATER** = nonblocking known follow-up with a deadline; **ACCEPT** = understood v1 limit. Always separate the present PR gate from future production/canary gates. Do not create repeated review loops for Low issues.

Product A's independent Claude findings on [PR#131](https://github.com/davidcatcat/davidcattoolsproject/pull/131#issuecomment-6129888660):
- **A-R1, P1 before T2**: host prompt must instruct complete strict verdict JSON schema/echo values; don't relax parser.
- **A-R2, P1 before T2/T3**: privacy detector falsely blocks literal documented path placeholders; narrowly allow known placeholders but still block real home/session/auth/log paths, preserve original exact bytes and digest.
- **A-R3, P2 during B**: after consume, unexpected launcher exceptions must preserve attempted-call accounting and `EFFECT_UNKNOWN`, no retry.
- **A-R4, during B**: align single typed immutable HostServices factory/CLI bundle.
- **A-R5/R6 ACCEPT v1**: scope limits/text SYSTEM framing disclosed, no stealth elevation.

Merged B contract requires authentic host registration/session review identity, independent grant issuer and route approval, durable one-use consume before effect, atomic UTC consumed-day limits, crash/replay/race/rollback tests and safe no-tools launcher. **No real Claude Canary without accepted A-R1/A-R2, independently reviewed B, native T1 evidence, authorized host/data route and a separate explicit T2 grant.** Until then `REVIEW_AUTHORITY_UNAVAILABLE`, `authorized=false`, `NOT_DELIVERED`.

Company LLM: local profile smoke succeeded (`COMPANY_LLM_OK`, 137+6=143 tokens). One actual L01 synthetic test-triage via llmtool reported prompt19,179, completion492, 2.63sec; JSON/4 failure IDs/3 groups and bounded excerpts correct, but summary incorrectly said three failures rather than four records. Thus **ANALYSIS FAIL (summary fidelity)** and **premium token savings UNKNOWN** in that run; do not rebrand the old Round2/3 39.82%/37.55% benchmark as new savings. Deterministically compute counts and validate model claims; don't silently modify gold or run repeated premium benchmark.

## Standard user–AI collaboration / reporting format

At each `review repo` request: **actually read GitHub Issue#120 newest comments, relevant PR state/HEAD/reviews/CI and current branch evidence**; do not rely on yesterday's summary. If no new PR exists, say so; uncommitted Mac changes are NOT visible via GitHub. Provide a concise Traditional Chinese report with:

1. **目前狀態／結論** — What *changed* since last report? Single sentence; do not equate design/CI PASS to product-ready.
2. **進度表** — Item / real status (DONE, IN_PROGRESS, BLOCKED, UNKNOWN) / evidence link or exact SHA.
3. **問題與分級** — Concrete blocking finding, reproduction/evidence, NOW vs LATER/ACCEPT, decision and owner.
4. **下一步（給哪個 AI）** — exact CLI role/known tty (verify), one task, explicit scope, done criteria; provide one paste-ready prompt only if necessary. Avoid telling the user to repeatedly paste identical prompts if already dispatched or awaiting gates. GitHub issue as mailbox, not automatic CLI trigger.
5. **你需要做** — normally `無`; only request an owner decision when real authority or privilege is missing.

For prompts handed to CLI: pin repository/Issue/PR/exact HEAD when relevant, safe worktree and no cross-root edits, allowlist, test scope, evidence/CI, product vs fake/provider permissions, stop/preserve conditions, GitHub publication and a concise requested result. Delegate code implementation to Developer; independent Reviewer assesses; Integrator handles merge. Coordinator does not personally write product source outside authorization.

After major milestones/new conversation, persist a dated handoff report in Git, preferably reviewed PR, including role contract and latest exact evidence; do not silently modify main or merge documentation without gates. New conversation should first fetch this report and live Issue#120, reconcile newer events and avoid duplicating dispatched tasks.

## First action in the next conversation

1. Inspect live [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120) and any B1 PR/new review comment newer than the STOP report.
2. If reviewer unavailable status persists, route the **bounded expanded native fixture-plan review** to an available independent non-author reviewer (can be canonical Claude Reviewer if capacity available, or another independently assigned reviewer). This is a *plan gate*, not full B1 product Security/Functional APPROVE.
3. After plan is independently approved, instruct **existing Codex Developer session** to resume **preserved uncommitted B1 branch/source**; do not recreate it or fetch/switch without checking.
4. Wait for B1 Draft PR, exact-head own native/full tests, CI, non-author Claude Security/Functional and separately scoped native T1. Production real Claude remains blocked pending explicit authority.
5. Continue company LLM quality/cost research only as separate controlled dogfood, not as reason to block B1 or claim savings.

**Handoff status:** evidence-reviewed; repository report artifact only, no claim B1 implementation or native-plan gate passed.
