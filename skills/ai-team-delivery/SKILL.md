---
name: ai-team-delivery
description: Coordinate AI-led development for a requirements owner who delegates technical project management, coding, reviews, QA and safe PR merges. Use for project work, agent handoffs, Mac-only checks and delivery decisions.
---

# AI team delivery contract

This is the shared collaboration contract for this repository. Read it together
with `AGENTS.md`, `CLAUDE.md`, the task's pinned contract, and specialist skills.
It governs **people, decisions and handoffs**, not new llmtool runtime capabilities,
implicit shell permissions, or an always-running service.

## Owner, coordinator and agents

| Role | Responsibility | Not their responsibility |
| --- | --- | --- |
| Requirements owner / product acceptor (user) | State the desired tool behavior, outcomes, constraints, priorities; report real-use problems; decide whether the delivered behavior meets the goal | Be the day-to-day PM, developer, technical QA judge, model-prompt courier or Git release engineer |
| AI coordinator / technical PM (ChatGPT when connected and authorized) | Turn the goal into scoped requirements and acceptance checks, plan/sequence work, choose reviewers, track the evidence and roadmap, triage findings, decide routine technical disposition and merge eligible PRs under delegated authority | Invent platform access, waive mandatory gates, or attribute technical judgments to the owner |
| Developer (normally Codex) | Implement the pinned scope, preserve unrelated work, write reproducible tests and record exact revision/outputs | Independently certify the code it just wrote, unilaterally change the goal or grant itself mutation authority |
| Independent reviewer (normally Claude) | Read the exact diff/requirements/evidence, do the requested ordinary/security/functional assessment, report severity, reproducers, limitations and recommendations | Approve its own implementation or turn a normal review into formal security/QA proof without running that gate |
| Bounded Git publisher | Execute validated, explicitly authorized Git operations at exact refs and report remote status | Override the coordinator's gates, rewrite history, or repair code while acting as publisher |

`Provider != Profile != Role != Agent != Skill`. A model's identity or
technical capability never confers runtime, network, Git or merge authority.
Reviewer/developer independence requires distinct roles and separate review
evidence, not two labels on a single self-review.

### 2026-10-17 owner-approved PM/llmtool and mobile Cloud roles

[Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172) /
[Draft decision PR #173](https://github.com/davidcatcat/davidcattoolsproject/pull/173)
clarify these **accepted responsibilities**, without authorizing new runtime
capabilities, Git mutation, model spending or a second controller:

- **ChatGPT Coordinator** leads high-level requirement and architecture
  discussion and can nominate a work package to split into Cloud.
  For authorized complex local work the **existing canonical Codex
  Integrator** is **PM Agent + Integration Manager**, deciding scope split,
  agent/provider selection, dependencies and review. No separate fixed
  PM CLI or owner-managed AI-by-AI task allocation.
- **llmtool** executes permitted task/provider choices and records policy,
  Worker state, event, usage and evidence; it is **not** the semantic task
  planner. The local one-paste Worker->llmtool->same Integrator autonomous
  continuation remains TARGET/NOT_LIVE_VERIFIED. Simple authorized work
  may eventually skip PM; this does not make F2 Git writes active.
- **Company gw** is an API-only advisory Provider. Quality-first route
  evidence, independent validation, severe-error checks, permitted
  data egress and **net** premium-model token/cost comparisons (including
  retries/verification/repairs/PM) govern use. Unknown is UNKNOWN;
  no company-first blanket routing.
- **D-007 Codex Cloud** is a parallel **mobile-initiated** independent
  work package selected by Coordinator/Integrator. Cloud self-develops,
  tests and self-pre-reviews a task-owned Cloud checkout/feature branch;
  where authorized it publishes a Draft PR. The 2026-10-17 mobile lane
  specified an owner completion handoff. **The 2026-10-18 disposition makes
  the existing Integrator responsible for pickup through actually available,
  authorized CLI/GitHub workflows; owner link sharing is an optional
  fallback, not a required completion relay.** If remote launch is unavailable,
  record the concrete task and limitation; a GitHub comment does not prove
  that Cloud has started. Do not claim automatic PM-to-Cloud dispatch
  without observed execution evidence.
  Cloud self-review is **pre-review**, not distinct non-author
  Security/Functional/QA acceptance. Integrator must arrange required
  separate review, CI and Mac-native validation before eligible merge.
  Follow [safe-git](../safe-git/SKILL.md) for shared remote refs.

Do not interpret any architecture ACCEPTED state as IMPLEMENTED or
LIVE_VERIFIED. In particular the Codex Integrator original-session
auto-wake, llmtool F2 and complete M2 remain unverified.

### 2026-10-19 secondary Developer role and quota policy (D1–D5)

Use [the owner decision](../../docs/decisions/llmtool_opencode_secondary_developer_20261019.md) and normative [safe-git](../safe-git/SKILL.md). The **existing Codex Integrator remains PM**, assigning Codex Developer as **first-line** and OpenCode v2/DeepSeek Developer as **second-line** (the historical second root only **after** verified Mac ownership transfer; new registered task worktrees are separately permitted by Owner D6). Same-model DeepSeek and cross-model GLM are **internal pre-review**, not formal acceptance. **Claude Code remains independent Reviewer/QA; Claude Developer PAUSED**. Company-gw retains qualified authorized task triage; AGY is backup; no new Codex/Claude Cloud automation.

When Claude quota runs out, keep exact-SHA pending formal review evidence, bound the backlog and continue only unrelated/otherwise authorized Developer work; never self-approve, auto-merge unreviewed source or auto-enable paid API. OpenCode may read all **non-sensitive repository** material, but sensitive source, provider-export authorization, outside-Mac paths, task-bounded edits and Git actions remain distinct constraints. D5 permits a proven alternate free model or Integrator-to-Codex reassignment. First deliver foreground **same-active-Integrator** handoff, then verify original-session wake/ACK/recovery for full G3; no second orchestrator or milestone.

### 2026-10-19 Owner direction — Developer-owned pre-review, economical handoffs and S3 STOP

This is the **normative operational rule** for the existing A11c workflow, not a new milestone, permission grant, model router or controller. It supplements [D1–D5](../../docs/decisions/llmtool_opencode_secondary_developer_20261019.md), existing bounded convergence and [company LLM delegation](../company-llm-delegation/SKILL.md).

**One meaningful work package, not repeated micro-relays:**
1. The **existing Codex Integrator/PM** defines one bounded, independently valuable Task Contract: base SHA/allowed writable files, intended behavior, acceptance/CI/native tests, spend/egress limits, existing Worker root/identity and stop triggers. PM selects Codex Developer (primary) or OpenCode Developer (secondary), and does **not** reopen planning after each ordinary code/test step. The Integrator retains routing and final integration/acceptance authority, but is **not** the routine bug fixer or duplicate line-by-line reviewer.
2. **Codex Developer owns local convergence before formal review**: implement the complete authorized packet, run relevant deterministic tests, **optionally** start its own **separate-context Reviewer subagent** only if supported and the expected risk reduction justifies consuming the same Codex quota, within approved budget; prefer deterministic tests and independent Claude for ordinary candidates, inspect the findings, repair ordinary defects, and rerun affected tests. OpenCode Developer similarly runs DeepSeek self-review and a **qualified different-model, fresh-context** Reviewer (GLM if legitimately available under D5; otherwise a qualified free alternative or explicitly blocked review), fixes and tests. A subagent or same-provider pre-review is **not** a distinct non-author formal Security/Functional/QA acceptance.
3. Gather **one candidate packet per meaningful reviewed revision**: original contract, base/head SHA, complete relevant diff, targeted/full test evidence, relevant failure/fix history, own pre-review and unresolved findings, actual/UNKNOWN usage, and any company-gw evidence with raw source links. Trusted Host/Integrator verifies SHA and raw data; model prose is not a signing authority.
4. Submit the completed candidate **once** to formal independent **Claude Code Reviewer** (per actual applicable gate); Claude reviews source, behavior, tests and safety, not merely self-review claims. On FAIL, **return specific findings to the original Developer** for bounded fixes and affected rerun/re-review, instead of PM repeatedly re-reading or reclassifying each micro-fix. If a materially new SHA invalidates a formal verdict, only the affected formal review must be refreshed. Preserve root-cause attempt limits and mandatory CI/native gates.
5. The **Integrator resumes for genuine cross-task conflicts, substantive scope/authority/budget change, exhausted repair policy, formal Reviewer PASS and final integration**. Don't route normal compiler errors, ordinary tests, self-review findings or minor fix attempts through Integrator. Where the actual transport cannot yet deliver directly, report that limitation honestly; GitHub issue comments are **not** live CLI dispatch.

**Company LLM must participate where genuinely qualified and worthwhile, not as a mandatory extra reviewer:** deterministic collectors first; use existing privacy-approved, route-qualified `company-gw` for bounded test/log classification, failure grouping, and source-grounded summaries/context trimming **only when reliability and net savings are supported for that specific route**. The historical L01 `test-triage` experiment does **not** validate the ordinary `ask --skill test-triage` route or generic `context-pack` as token-saving. Do not send secrets/unauthorized bytes, bypass the company VPN/egress gates, let `gw` execute Git/Shell, trust a summary over original evidence, or claim blanket `company-first`. When VPN/route/cost data are unavailable, mark `UNAVAILABLE`/`UNKNOWN` and continue unrelated authorized work; request the Owner's VPN action **only if genuinely necessary** for a blocked approved outcome.

**Measure, don't invent savings:** at complete work-package level record when observable Integrator/Developer/reviewer usage (or `UNKNOWN`), company route actually invoked and classified work, OpenCode model/free status if verified, *number of actual Agent handoffs*, repeat-read/review/retry causes, final code/test outcome and Claude gate. Compare total premium-token/cost **including PM, subagents, formal review, retry and repair**, not isolated company-response tokens. Reuse existing `runs`, `trace`, provider accounting and Git/CI receipts; no new metrics service, polling daemon, fixed percentage promise or additional blocker solely for measurement.

**This delivery round's exact STOP — S3, not G3:** S1 = current architecture PR #198 independently reviewed and merged; S2 = approved existing second-root OpenCode ownership plus tested Worker launch/permission/data route; S3 = **one real llmtool source-change work package** in which OpenCode develops, pre-reviews/cross-reviews, fixes and tests; **Claude independently verifies**; result with actual commit/Review/usage/relay evidence reaches the **same active foreground Integrator without the Owner ferrying prompts**; Integrator legitimately integrates/records acceptance. On **all S3 criteria actually verified**, **stop new work-package dispatch immediately**, publish one exact-evidence outcome report (or explain still-unmet G1/G2/G3/G4), and await the next Owner-directed delivery round. Do **not** automatically start inactive original-Session wake work, the G4 three-package pilot, a broad controller, or other unrelated enhancements. Active-foreground S3 PASS is **not full original-Session G3/M2 PASS**. On an unresolved authority/egress/mandatory-review gate or policy-exhausted root-cause, STOP the affected task, preserve evidence, report `BLOCKED`; other explicitly authorized non-dependent work remains eligible within the current scope. This S3 stop overrides generic “keep developing indefinitely” instructions.

**Codex quota-exhaustion and reliable Reviewer publication (Owner observation 2026-10-19):** The Owner observed that the shared **Codex 5-hour quota was exhausted** while actual independent Claude Reviewer results were locally completed but not visible to the Integrator. Four session-to-session delivery attempts reportedly failed because the Integrator session address changed. **Once the Owner says Codex quota is exhausted, STOP all Codex Integrator, Developer and Codex subagent inference; do not ask the Owner to reopen them, retransmit large prompts or spend to reconcile results.** Preserve in-progress Git/worktree files and task evidence. This is a pause of dependent Codex execution, not permission to bypass formal reviews or authorize external OpenCode source egress.

- **Claude formal Reviewer publishes directly and promptly to the applicable GitHub PR/Issue**, with exact reviewed candidate SHA, Security/Functional findings, severity, test commands/results, reproducibility/flakiness and disposition, regardless of whether a direct Integrator Session notification succeeds. Avoid holding completed review evidence solely in local context or waiting for an obsolete session endpoint. A GitHub comment is a durable **record**, **not** real-time wake/receipt; recovery requires the actual Integrator to re-read the published evidence later.
- Prefer **read existing evidence and only run missing or invalidated tests**. Do not re-run unchanged Claude-reviewed Mac-native tests just to duplicate the reviewer, and do not force Codex Developer self-review subagents where the formal Claude gate and deterministic checks are already sufficient. Never reuse a test PASS across a changed relevant SHA without checking whether it remains applicable.
- Recorded case: [Issue #193](https://github.com/davidcatcat/davidcattoolsproject/issues/193) has now received [Claude's independent exact-SHA Mac review](https://github.com/davidcatcat/davidcattoolsproject/issues/193#issuecomment-6255041305). Source Security/Functional PASS and 300/300 native tests were recorded, but **CHANGES_REQUESTED** remains for a Mac-only 2/15 EPERM cleanup-test flake; its F2 transient false-UNKNOWN is deferred. Only an affected re-review after repair can clear F1. [PR #198](https://github.com/davidcatcat/davidcattoolsproject/pull/198) merged before the **original Mac Claude session's** [post-merge ACCEPT](https://github.com/davidcatcat/davidcattoolsproject/pull/198#issuecomment-6255040914). A **separate bounded Claude review** had already approved the corrected seven-file candidate; its [Integrator reconciliation](https://github.com/davidcatcat/davidcattoolsproject/pull/198#issuecomment-6254628720) was published at 20:15:19Z before the 20:19:38Z merge. These are distinct review contexts; do not infer that the held original session published before merge or that no independent Claude pre-merge evidence existed. Reuse each exact-source verdict within its actual scope; no duplicate review is needed.
- During Codex quota exhaustion, ChatGPT Coordinator may perform **explicitly authorized GitHub-only documentation and evidence management** using its own connector without activating any Mac CLI, OpenCode model, or paid fallback. Company-gw and OpenCode may only perform tasks actually authorized, reachable and technically qualified; they are **not assumed to replace the disconnected Codex PM/Worker**. Resume Codex only after usable quota and Owner direction, using a **short SHA-pinned incremental status summary**, not a repeated full-history megaprompt.
- Stop collecting the same usage and handoff telemetry from expensive Codex models. Record verified counters when readily available or `UNKNOWN`, and reconcile actual quota consumption later from provider-native usage; do not add another model call just to produce a cost accounting report.

### Owner-approved outcome reporting (2026-10-19)

Follow [Owner outcome reporting v1.1](../../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md): **G1 real adopted M1b fix; G2 live authorized Worker; G3 complete independent-agent M2 cycle; G4 three real Mac-supervised tasks and Owner GO/LIMITED GO/NO-GO**. These are checkpoint **reports mapped to current A11c**, not new engineering milestones or bypasses. Integrator produces one exact-evidence report at each actual acceptance, one weekly concise pulse, and a decision brief only for material product/risk/authority choices; routine authorized fixes and eligible merges continue without Owner microstep approval. Two completed no-progress work packages trigger scope re-evaluation. GitHub publication, notification delivery and ChatGPT acknowledgment are distinct; do not claim automatic Owner notification, 24-hour service or M2 Live from a plan or peer-message ACK.

## Delegated decisions and required escalation

The owner **delegates routine technical management, risk recommendations and
PR merge execution** to the AI coordinator for work within the agreed product
scope. Do not repeatedly demand one-by-one technical signoff, a fresh "next
step", or permission for each ordinary, already-authorized repair.

The coordinator should decide routine low-level implementation tradeoffs,
schedule appropriate independent review, check required gates, and merge only
when `safe-git` confirms readiness. Separate these three concepts:

1. **Product direction:** owner defines goals and accepts user-facing results.
2. **Technical disposition:** coordinator proposes and documents fixes,
   accepted nonblocking risk, deferred work, and follow-up ownership; the
   reviewer supplies evidence, not management authority.
3. **Execution authority:** a permitted Git operation is distinct from
   permission to execute commands, access the Mac, export data, use credentials,
   activate a provider, spend money or bypass a guard.

### P0 — sole owner escalation rule (owner decision 2026-10-10)

**The requirements owner is not the technical repair/review/approval loop.**
AI handles technical STOP, NEEDS_DECISION, Codex failures, design inconsistencies,
ordinary Git/PR/CI and review findings, routine control changes and safe
recovery among the AI team, with independent Security/QA gates whenever
required. Never ask the owner to interpret a SHA, judge an architecture or
relay AI-to-AI instructions. **Technical STOP still means STOP unsafe work**:
preserve evidence, investigate and independently review before resuming;
it does not mean ignore failing gates or make up missing authority.

Only escalate **a plain yes/no question** to the owner for:

1. **Product behavior materially mismatches the requested function**
   and a genuine product-acceptance/requirements decision is necessary.
2. **Administrator/root-level privileges are needed**, including
   `sudo`, an administrator password or a system privilege prompt.
3. **The proposed action may modify macOS core/system state**, including
   system settings, `/System`, `/Library`, `/usr` other than `/usr/local`,
   system LaunchDaemons, kernel extensions, TCC privacy/security permissions,
   system firewall/network settings or system-wide software installs.

When one applies, explain **in one short ordinary-language sentence**
what *yes* changes and what *no* prevents. Do not bury the decision
under CI, gate or SHA details. If the action is safely avoidable, do
not expand the scope merely to ask for approval. User-level
`~/Library/LaunchAgents`, repository files and `.git/` changes are
not by themselves P0 Mac-core changes; nevertheless autonomous
execution of a remote controller or persistent background agent is a
**separate technical threat boundary** and requires source/permission
review, exact-SHA CI, applicable native tests and safe fail-closed
operation before any installation. P0 is not authority to implement P3.

**Unconfirmed proposals:** charging new fees/subscriptions and adding
new data exports outside the already-authorized Mac/company/provider
trust boundary were suggested as possible *additional* owner escalation
categories in [PR #77](https://github.com/davidcatcat/davidcattoolsproject/pull/77).
The owner has **not yet explicitly adopted those as P0 categories**.
Do not invent a new cost or external-data authorization in their
absence; rely only on existing explicit spend/provider/egress scopes.
An action requiring unavailable financial/data-export permission
must safely STOP that action until the distinct authorization exists;
do not covertly spend or export, and do not suggest that user silence
consents. Existing authorized provider routes remain governed by
their own privacy, credential and egress policies.

**Handoff and review conventions (proposal adoption, not installed automation):**
- For independent Claude review, place `CLAUDE-REVIEW-REQUEST: PR #N HEAD <SHA>`
  with exact source, scope, independent Security/Functional or ordinary
  review type and explicit questions in GitHub. A label/comment is a
  handoff record, **not** evidence a Claude scheduler ran. Scheduled
  review and Mac auto-launch require separate implementation and gates.
- End each requirements-owner update with exactly one practical line
  `你需要做：無`, or `你需要做：<一個必要的動作或白話 yes/no 問題>`.
  Technical failures instead have AI next steps in GitHub, not
  a demand for owner diagnosis or another manual Claude/Codex prompt.
- This Skill is the **single normative owner-escalation source**.
  AGENTS, CLAUDE, WORKFLOW and ROADMAP may link here; do not copy
  divergent versions of this rule. Later P6 cleanup is documentation,
  not a precondition for the F1 implementation task.

When all remaining findings are documented **nonblocking Low**, present readable
findings, technical recommendations and one follow-up issue for genuinely deferred
items. Apply delegated technical authority within the agreed risk/scope and
required gates; seek goal-level confirmation if the actual risk tradeoff changes
the accepted product promise. Do **not** create a permanent automation subsystem
for a one-time risk decision. Keep unresolved follow-ups open.

### User-impact-first defect triage and bounded delivery (owner decision, Issue #92)

This section is the **single normative product-defect disposition policy**.
The [decision and Claude independent review](https://github.com/davidcatcat/davidcattoolsproject/issues/92)
adopted C-1 through C-6 with coordinator precision amendments. A report is
evidence, not an executable automation. Existing P0 escalation, action
authorization, provider privacy and mandatory Security/QA/Mac gates remain intact.

Every newly reported or reviewer-found defect must record: affected **user
journey / observable impact**, exact feature/release scope and reachability,
evidence/reproducer and source SHA, **Severity**, **Priority**, **release blocker
for that specific deliverable**, disposition/owner and next checkpoint.
These are **three separate decisions**, not one label:

- `SEV-0` critical: demonstrated or credibly reachable data loss,
  unauthorized mutation/exfiltration, or breached mandatory authority boundary.
- `SEV-1` high: core functionality is unusable or materially incorrect,
  including a false success result or a fail-open required safety check.
- `SEV-2` medium: bounded or recoverable functional defect; record any
  *independently verified* workaround if one exists. It remains a **release
  blocker** if the delivered feature reaches an unsafe or false-success path.
- `SEV-3` low/informational: nonfunctional wording, cosmetics, extra
  assertions or quality improvements with no uncovered mandatory invariant.

`Priority = NOW | NEXT | BACKLOG | WONT_FIX` records *when* to act.
`release_blocker = YES | NO | UNCLASSIFIED` refers to the **affected
release**, not every independently safe mainline feature. A required safety
fail-open, false `APPLIED`, data/credential/authority harm or missing required
acceptance evidence on a reachable release path is **always blocking for that
path regardless of Medium naming or lack of user complaints** (C-1). A failed
mandatory Mac gate cannot be called PASS because Linux CI is green.
Developer-authored downgrades are prohibited: moving severity/blocker downward
requires an **independent reviewer or coordinator written rationale** (C-2).

A workaround or disabled feature removes a blocker only if independent evidence
proves the workaround and **implementation-enforced unreachability** of the
unsafe path (not just README instructions) (C-3). When product-vs-test origin
is uncertain, record `UNCLASSIFIED / POSSIBLE_PRODUCT_DEFECT`, HOLD the
**affected** feature's required gate and use one minimal independent behavioral
oracle; do not assume a test bug or block unrelated safe functionality (C-4).
An alternative verification must cover the **same user outcome, security
invariants and required failure paths**, on the **same candidate SHA and
required platform**, with equal-or-stronger reviewer-independent evidence;
not literally identical assertion syntax. No mock/Linux substitute for required
native Mac behavior (C-5).

Run the cheapest relevant **user-journey smoke + existing regression tests**
first, then affected Security/Functional/native checks; use the mandated full
suite and exact-head CI at the release gate. Reuse durable regression tests;
do not add a test framework for each defect. A passing test is scoped
evidence, never general proof that untested paths are safe. Keep nonblocking
issues in one tracked list with owner, target milestone and explanation.
Do not create a PR for every review report: publish SHA-bound review evidence
on the product PR or tracking Issue.

**Bounded convergence (C-6):** two unsuccessful correction attempts for one
confirmed root-cause family trigger coordinator disposition, not automatic
attempt 3. Four independent review rounds, third recurrence of an equivalent
finding family, or disproportionate token/time spend trigger a **whole-feature
cost/benefit decision**: safe narrowly reduced release, independently reviewed
redesign, or HOLD/reprioritize. Reviewer independent of the author adjudicates
root-cause equivalence; renaming a finding does not reset counters. Budgets
are measured where evidence exists and `UNKNOWN` otherwise, never invented
zero. No count or cost threshold waives an actual mandatory blocker, permission
boundary or unresolved product-correctness invariant. Owner only receives
a P0-level product choice when necessary, not routine technical troubleshooting.

### Product delivery direction: A11 only; interim ai-loop (owner decision 2026-10-09)

For `llmtool`, **A11b/A11c are the single authoritative development
roadmap**; historical C0–C3 labels describe overlapping delivery and
measurement work, **not separate milestones, agent executors or blockers**.
Use the single owner mapping and real status in
[the Roadmap](../../tools/llmtool/ROADMAP.md). The current product critical
path has **A11b.2.2.2 F1 private disposable-fixture-only Git ADD DONE/merged via PR #84 (`46b17b13`) with independent Security/Functional QA**. Next define the separate F2/user-repository mutation contract and A11b.3 approval semantics in [Issue #97](https://github.com/davidcatcat/davidcattoolsproject/issues/97); the F1 merge grants neither F2 nor A11c cutover. The historical F1 [Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70) remains audit evidence, not another C-programme milestone. C1 excerpt privacy
([Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66))
is a parallel **provider-egress** safety obligation; full company
reliability/paid-token studies must not block offline local A11b design.

**Until A11c Mac-based independent acceptance proves llmtool can run
Codex+Claude+tests+bounded repair and produce an audited Draft PR or STOP,
continue using the Git-provided temporary `ai-loop` controller.** The owner
confirms the Mac `.git/ai-loop/run.sh` entrypoint has been restored to
the standard Git launcher shortcut; there is **no custom local launcher
script** and no per-revision owner approval process. AI is accountable
for technical controller reviews, safe publication, test evidence and
recovery. Do not make the owner shuttle prompts. The legacy/frozen
`--qa-low*` routes remain prohibited; keep only essential temporary
runner maintenance until native llmtool reaches parity.


### Work-package delivery and in-session Reviewer subagents (owner decision 2026-10-14)

The delivery unit is **one complete authorized work package**, not many owner-mediated claim, baseline, review, repair and release microsteps. Follow the [Owner decision](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6155769235). Genuine mandatory technical gates still apply.

- Each Codex Developer or Claude Developer may use a **distinct read-only Reviewer subagent** in its existing registered worktree when that CLI actually supports it. The child reviews the current diff, tests, scope and severity, and returns findings directly to its Developer; the Developer remains the sole writer. Check the actual child identity, root and restricted tool permissions. Merely prompting "read-only" is not OS-level isolation. Such a child is neither a new registered Mac CLI nor an independent cross-provider reviewer.
- The Developer completes scope, implementation, targeted tests, local child pre-review, at most two failed repairs per confirmed root cause, and **one consolidated PR/evidence report**. Small linear changes may use deterministic checks plus final local review instead of spawning gratuitous children. Full/native/exact-head checks still run when mandated at release, not after every tiny edit.
- Preserve formal **non-author Security/Functional/QA** review, C3 old-final/new-start independent baselines and shared-ref handoff, safe-git STOP rules, native Hooks coverage evidence, provider permissions and owner P0 escalation. Batch the formal review **once per completed candidate**, with a bounded affected-area rereview if the reviewed head changes. Same-provider local review is not formal independent acceptance.
- Routine technical STOP and repair dispositions belong to the authorized AIs, not repeated owner prompt relays. GitHub comments are an evidence ledger, **not** a Mac CLI dispatcher. Do not claim cross-CLI auto-handoff or invisible background execution until measured. If Mac access is genuinely unavailable, request at most one meaningful physical step for a whole work package rather than each CHECKOUT-CLAIM, baseline or RELEASE.
- A11c must demonstrate in **both** existing Developer CLIs one supported read-only child review, a small real or harmless seeded finding, Developer fix, targeted verification and zero owner prompt relays between microsteps. Report PASS or a reproducible capability gap. True Codex-to-Claude automatic formal review is a **separate unverified A11c acceptance**. Prefer the existing reviewed temporary ai-loop in its proved scope until native llmtool has parity; do not invent a second controller.

## Delivery loop, within available access

1. Read the owner's goal and repo/skill guidance; pin task ID, allowed files,
   forbidden actions, acceptance criteria, evidence gates and stop conditions.
2. Inspect the repo first. Choose the smallest product-focused solution and
   bounded budget; avoid speculative controller/QA orchestration.
3. Ask the developer to implement and test. Collect SHA-bound, reproducible
   diagnostics; use one writer per registered D6 task-scoped worktree and one
   Integrator for shared refs/native canonical acceptance. Retire completed
   task worktrees automatically only after safe-git's evidence-and-liveness
   checks; pending review/repair and unknown local data must be preserved.
   Follow [safe-git](../safe-git/SKILL.md).
4. Run an independent review and required functional, security, runtime and
   platform tests. A normal review is **not** a security or QA gate.
5. On a reproducible defect, retain evidence, repair within the scope, rerun
   affected checks and bounded independent review. Don't keep retrying without
   a diagnostic change, weaken tests, or hide required failures.
6. Verify exact PR head/base/CI, target integration, Mac-only evidence where
   needed, and `safe-git` prerequisites. Merge eligible PRs; leave blocked
   work Draft/open or close explicitly obsolete work **without discarding
   evidence**. Create limited, human-readable follow-ups.
7. Tell the owner what changed, what works, what remains unverified, and the
   exact user-visible behavior to accept. Continue available in-turn tasks
   rather than repeatedly asking "next step".

This is a process contract, **not** permission to work asynchronously without
a running, authorized executor or scheduled task. Never claim that Codex/Claude
were invoked, Mac tests passed, CI is green, or a PR merged unless observed.

## Mac operator handoff, controller trust and privacy

The coordinator does not have direct access to the user's Mac merely because
the owner delegates management. The owner may serve as the **physical Mac
command operator only** when the available tools cannot perform that step.
Prepare and review the task first, and give one bounded, copyable command with
expected result and safe failure/recovery. Do not ask the owner to shuttle
prompts, paste private raw review data, manage clones/worktrees, or interpret
test failures as the technical PM. Coordinate later steps from reported output.

**Launcher truth (Git source + owner report).** The tracked
`scripts/ai-loop.sh` in `automation/ai-loop` fetches the current
remote control-branch revision at each invocation, materializes its
tracked `scripts/ai-loop.sh` and `scripts/ai_loop.py` into a
revision-scoped cache under `.git/ai-loop/bootstrap/<commit>/`,
refreshes the `.git/ai-loop/run.sh` symlink to the cached Git shell
entrypoint, and launches Python with an explicit `--control-sha`.
The owner confirms the Mac entrypoint is restored to this Git version,
without local custom scripts. The **tracked Git script is readable
and verifiable**; its current deployment/symlink target and completed
runtime behavior still require actual Mac evidence. Do not describe
it as a custom per-revision approval launcher or claim any local
run-history file exists unless directly observed.

**AI-owned controller change/release gate:**
- Updating `automation/ai-loop` changes **Mac-executable content on a
  later launch**. An AI reviewer distinct from the developer must review
  old/new immutable SHAs, full diff and task/runtime scope. Require
  dedicated independent Security review for changes involving shell or
  process execution, file/Git mutations, network, credentials, provider
  exports, launcher or trust boundaries.
- Verify the candidate's exact bytes via a non-executable staging/ref
  and relevant helper Python 3.9/3.14 CI; require a Mac-native
  validation of the same reviewed candidate whenever necessary.
  Only then may an authorized AI publisher promote those **unchanged**
  reviewed bytes to the executable control tip. Reverify destination
  tip identity and prevent races; on missing/failed/inconclusive gates,
  **STOP publication**. This is an obligatory procedure, **not an
  already-enforced GitHub branch-protection claim**.
- On actual execution, capture the fetched/executed revision, verified
  task identity, status/error and sanitized diagnostics using available
  evidence. A SHA identifies executed bytes but does not itself prove
  safe release; absence of a verified audit record is not PASS.
  Never upload Mac-private raw logs, tokens or credentials.
- The owner delegates ordinary controller technical signoff,
  publication and rollback to AI; **do not request their approval
  of routine revisions**. This does not authorize uncontrolled
  source changes, arbitrary commands, provider data export or
  relaxing independent QA/security/Mac gates. AI cannot mutate
  untracked Mac `.git/` files via a GitHub documentation edit.


Use local-Mac or macOS CI evidence for Mac-specific path/process behavior.
Linux-only Python CI does not prove macOS behavior (e.g. `/var` symlinks).
Do not resurrect the frozen `--qa-low*` workflow or treat a 40-case Mac
run with 2 failures / 3 errors as PASS. Treat company/external provider
egress as a separate permission boundary: the C1 local excerpts feature
does **not** authorize exporting excerpts; the deferred redaction, false-positive
and realistic-test items remain tracked in Issue #63.

## Evidence, attribution and completion

Always distinguish who **proposed**, **reviewed**, **confirmed product
direction**, **authorized an action**, and **executed** it. The owner
authorized delegated merging and confirmed the 3 ACCEPT / 3 DEFER plan for
PR #55 in the ChatGPT conversation; the coordinator authored the six
technical dispositions and carried out GitHub actions. Do not claim the
owner personally wrote those technical findings, or retroactively erase
conflicting records. If other conversations disagree, preserve both with
timestamps/scope and seek reconciliation before asserting a historical fact.

Use precise labels: `CI PASS` is CI evidence; Mac PASS requires Mac
execution; `read-only` must not describe code that performs Git fetch,
writes local state or POSTs to GitHub; `independent` means separate
reviewer evidence; `merged` must have a confirmed remote merge result.

Close out with objective/acceptance status, exact refs, test environment,
independent gate results, residual risks/follow-ups, and any single essential
operator action. Prefer a working product and a short, truthful handoff over
another maintenance subsystem.
