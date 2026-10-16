# llmtool Coordinator Handoff — 2026-10-17 (Antigravity / M2)

> Documentation-only handoff. Historical drafting base: `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39`. Updated against live main `de113a9a6c0b66428966533b0a82b0c65d2c8454` after #169 and #167 merged. Recheck live GitHub and actual local identity before acting; this snapshot is not execution authority, runtime proof or an installed dispatcher.
> Repository: https://github.com/davidcatcat/davidcattoolsproject
> Tracker: https://github.com/davidcatcat/davidcattoolsproject/issues/120
> Normative sources: [AGENTS](../AGENTS.md), [single A11 Roadmap](../tools/llmtool/ROADMAP.md), [Workflow](../tools/llmtool/WORKFLOW.md), [ai-team-delivery](../skills/ai-team-delivery/SKILL.md), [safe-git](../skills/safe-git/SKILL.md). Those sources and newer explicit Owner decisions outrank a historical handoff.

## 1. Product goal and collaboration rules

Owner wants llmtool usable in its own development, replacing repeated human prompt relays with observable delivery. llmtool performs authorized execution, lifecycle, deterministic state/evidence and handoff; the main AI understands the task, chooses the Worker/route, verifies results and decides adoption. Keep Provider != Profile != Role != Agent != Skill. Do not add an intelligent router, second orchestrator, scheduler, daemon or ai-loop.

M1a real advisory and M1b adoption have been demonstrated: llmtool called Claude CLI for advice, Codex checked/corrected advice and shipped a tested independently reviewed change in #160. This is useful dogfood, not autonomous M2. M2 still needs actual foreground Reviewer Worker delivery/receipt/verdict, authorized bounded repair/tests and an eligible Draft PR or STOP without Owner prompt relays. Source/Fake/Parser acceptance is not Live acceptance. Do not turn a checklist into a new milestone or report an invented completion percentage.

The [Owner no-self-handoff decision](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6190074894) is effective. Complete authorized work in the current Session; never set NEXT_AI to that same Session. Developers own ordinary bugs, targeted tests, advisory children and bounded repairs. Only a genuinely required different environment/role/formal review warrants handoff; do not make Owner a prompt courier. Preserve C3, writer ownership, Source scope and independent review. #156 is the durable Skill wording PR; until separately accepted/merged, do not claim that its text is already in main.

## 2. Current evidence and provenance

| Delivery | Verified status / boundary |
| --- | --- |
| #159 / #160 | M1 stages documented; actual advisory adoption merged. Not full M2 or F2. |
| #162 | Review Bundle merged and actually used by the formal reviewer. Evidence preparation is not automatic dispatch. |
| #163 / #164 | Manual request/receipt/verdict roundtrip and safe decision consumer merged. Transport exit 0 is not APPROVE; approval DATA has no execution authority. Original-session auto-wake remains unverified. |
| #165 | Foreground Worker Source/Fake merged at `86a990eb4389044aa3196f25d679c2d73b4591a3`; not production installation or Live authorization. |
| #166 | WJ1 merged at `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39`, reviewed source candidate `5e58382214f6ae53217edc53c5b63850aa920fef`. Exact-head CI 38929856416 and postmerge CI 38931195385 both SUCCESS (Python 3.9/3.14). Original Claude formal Delta Review remains pending. A separately labeled CODEX_INDEPENDENT_DELTA_REVIEW does not replace it. |
| #169 | V2 readiness/Skills audit merged as `dcb2584a42e41ba095845428463ceb89b17f073d`. Template, concrete synthetic parser evidence and Live NOT_VERIFIED are separated. This is docs, not executable enablement. |
| #167 | Owner smoke record merged as `de113a9a6c0b66428966533b0a82b0c65d2c8454`. USER_VERIFIED only; not an llmtool Adapter PASS or Worker grant. |

Antigravity CLI 1.4.0/Gemini 4 Argon (Medium) smoke is Owner-reported: marker `GEMINI_4_ARGON_OK`, 22.986926 s, one turn, CLI-reported input14714/output37/thinking25/cache_read0/total14751. It does not establish measured cost/savings, actual model attestation or llmtool routing. Owner-reported model menus/preferences are not runtime-verified identities. Do not expose account, credential or conversation identifiers.

Authoritative readiness detail is [the merged V2 audit](llmtool_antigravity_skill_gap_audit_20261017.md), not superseded V1 Issue comments. Use `TEMPLATE_ONLY`, `SYNTHETIC_PARSER_VERIFIED`, `USER_VERIFIED`, `SOURCE/FAKE PASS`, `LIVE_NOT_VERIFIED` and `NOT_AUTHORIZED` precisely. A static structural check can exit0 while `status=BLOCKED`, `authorized=false`, `provider_calls=0` and runtime/published pins remain NOT_VERIFIED. Preserve the accepted parser evidence without rerunning unchanged historical tests.

## 3. Actual CLI roles and workspaces

| Role | Session / workspace / current boundary |
| --- | --- |
| Original Codex Integrator | `01a12e4b-1ac5-7c11-9071-6c01c3aafb5f`; `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`. Integrations and independent acceptance only within actual authority; cannot invent Live permission. |
| Main Codex Developer | `01a12f8c-6a46-71c2-a7b3-3e6211ec2e9c`; `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex`. Primary assigned Source Developer for current llmtool core packages; AGY Skills Discovery remains its separate active assignment; advisory children are not formal non-author review. |
| Original Claude Independent Reviewer | `081de22e-d514-4288-9d1f-48f488979271`; canonical root. Quota paused until 2026-10-18; verify actual availability then. Pending original WJ1 formal review is not complete. |
| Claude Developer | `5e05868e-9b7c-41f3-b29b-de3ed351aef4`; fixed Claude root `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude`. New development remains paused under Owner allocation. |
| AGY Developer | Skills Discovery work is active under a separate assignment. Its exact Session/branch must be observed, not inferred from a model name or GitHub account. Do not touch its files, branch, artifacts, venv or shared-ref window. AGY is not original Claude Reviewer or original Codex Integrator. |

The dated operational roles above do not rewrite the approved two-fixed-worktree policy or authorize another checkout. A canonical CWD does not prove exclusive writer ownership. Check real writers/children, locks and CLAIM/RELEASE before local Git writes; do not switch a peer's checkout. This backlog update uses remote API docs only and makes no claim that local main was synchronized.

## 4. Real foreground Reviewer Worker Trial: remaining gates

**REVIEWER_JOB_TRIAL = NOT_AUTHORIZED / NOT_RUN.** Public `review_authority_factory()` remains unavailable. Do not manufacture a public execution route or grant.

1. Obtain original Claude's focused #166 WJ1 Security/Functional Delta Review when quota resumes, against the exact source and merged bytes. Codex's independent delta evidence is additional, not original Claude approval.
2. Resolve WJ5 under separately authorized provisioning: the installed wrapper is a symlink (rejected), and the resolved npm target contains `@anthropic-ai` (also rejected by `_path`). A separately authorized physical `@`-free copy/hardlink with exact version/hash/runtime pin is a conditional option, not Live PASS. Atomic npm replacement can leave an old hardlink valid under its old hash; adoption of new bytes needs explicit revalidation. Do not relax the general path validator or install anything based on this document.
3. Establish a dedicated physical Auth HOME distinct from Owner HOME, outside worktrees. No credential copying. Provisioning/login/authentication remains separately authorized and unverified.
4. Independently validate actual Production Host/HostPin, correct Worker role `llmtool-worker/claude-cli`, exact executable/model/profile/route/data pins and private PROVIDER_REVIEW ledger enforcing one-use consumption per grant. Parsing a synthetic host or creating grant DATA is not authorization.
5. Obtain explicit Owner Live Trial authority for permitted packet/data/context, route/model, cost uncertainty, one call/no retry/no fallback, UTC window and bounded cleanup. Integrator may verify/implement only granted actions; cannot self-issue Owner approval. Company Live/real rollout/Step0/GateB/Host/F2/S2 grants remain distinct.
6. Only then run one bounded foreground `llmtool -> Reviewer Worker -> receipt/verdict` trial. Record actual process ownership, nonce/PID/PGID, CLI-reported new runtime identity, deadline/exit/reap/stream/runtime-directory cleanup, packet/task/SHA binding, structured dual verdict and useful main-AI disposition. Unknown effect retains pending evidence and forbids resend. Usage/cost/internal model calls not observed stay UNKNOWN. A new Worker is never the original Claude Session implicitly resumed.
7. After useful trial evidence, implement only the separately authorized missing bounded review/repair/test/DraftPR-or-STOP behavior; measure actual Owner relays. A successful launch is not the complete M2 loop or permission for F2.

Keep #161 UX HOLD, #153 CLOSED/HOLD, T1 HOLD and all existing Native/Company/F2/S2 gates. Owner `danger-full-access` and `approval_policy=never` remain unchanged. These preferences do not confer provider/egress permission.

## 5. Standard `review repo` response and handoff format

Read fresh main, active PR heads/diffs/reviews/CI and newest Issue120 evidence; reconcile it with ROADMAP/WORKFLOW and applicable Skills. Reuse unchanged SHA-pinned evidence. Do not interpret GitHub comments as automatic dispatch or assume an old local checkout contains remote main.

Report in Traditional Chinese:

1. Timestamp/exact main and relevant PR/CI snapshot; distinguish observed facts from reports.
2. Actual functional progress since the previous review, especially real usage/adoption.
3. NOW/LATER and which exact gate is blocked; never reclassify Fake/Parser success as Live.
4. Work performed this Session. Continue all executable authorized work before ending. If a genuine different executor is required, name its exact role/Session/root, actual blocker and one complete copyable prompt. If it is already running, do not duplicate its dispatch. Never claim an unobserved wake-up or set NEXT_AI to the current Session.
5. Concrete remaining distance to usable self-hosting, including true Reviewer Trial, bounded repair/tests and eligible audited PR-or-STOP. Unknown token/cost/relay figures stay UNKNOWN.

A handoff prompt names `TO: CLI / role / Session`, task/Issue/PR, verified root/branch/SHA, exact in/out scope, genuine required grants/gates, steps, tests/evidence and completion criteria. Developers own routine repair and same-session advisory review; formal non-author review remains separate. End Owner updates with `你需要做：無` or one genuinely necessary action; do not demand technical diagnosis or many prompt relays.

Before a permitted real Company LLM call, remind Owner to connect Company VPN; route only through llmtool. VPN availability is not egress permission. Company VPN can block public external documentation/GitHub: batch permitted public reads before that phase, and do not call a blocked endpoint an absent capability. Current live connectivity remains unverified; no automatic costly fallback.

## 6. Next distinct executor and reusable takeover

The currently required different executor is original Claude Independent Reviewer `081de22e-d514-4288-9d1f-48f488979271` when actual quota permits (expected 2026-10-18): perform the original focused WJ1 delta review, and any outstanding genuinely non-author documentation review, without repeating accepted product suites. Report findings/verdicts on the exact candidates. This document neither dispatches that CLI nor asks Owner to manage routine microsteps. Once the distinct review and explicit Live authority exist, the responsible Integrator should execute its authorized gates in-session rather than handing work to itself.

For a new Coordinator conversation: read fresh Issue120, main, ROADMAP/WORKFLOW, this existing handoff and merged V2 audit. Preserve role identity, worktree ownership, independent gates and all HOLDs. M1 is real; full M2 and Live Worker remain unverified. Continue actual authorized work, use one meaningful different-session handoff only when required, and always report the remaining deployment gap. Older #149/#154 handoffs are historical evidence, not current dispatch instructions.
