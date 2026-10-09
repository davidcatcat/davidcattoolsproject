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

### Product delivery direction: A11 only; interim ai-loop (owner decision 2026-10-09)

For `llmtool`, **A11b/A11c are the single authoritative development
roadmap**; historical C0–C3 labels describe overlapping delivery and
measurement work, **not separate milestones, agent executors or blockers**.
Use the single owner mapping and real status in
[the Roadmap](../../tools/llmtool/ROADMAP.md). The current product critical
path is **A11b.2.2.2 F1 private disposable-fixture implementation (contract accepted), with independent Security/Functional QA before product integration**
([Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70)),
not restarting the C programme. C1 excerpt privacy
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


## Delivery loop, within available access

1. Read the owner's goal and repo/skill guidance; pin task ID, allowed files,
   forbidden actions, acceptance criteria, evidence gates and stop conditions.
2. Inspect the repo first. Choose the smallest product-focused solution and
   bounded budget; avoid speculative controller/QA orchestration.
3. Ask the developer to implement and test. Collect SHA-bound, reproducible
   diagnostics; keep one source/Git writer at a time.
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
