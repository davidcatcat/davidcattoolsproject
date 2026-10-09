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

Escalate to the owner for a genuine change in objectives, material unresolved
risk requiring a business choice, unforeseen financial/data exposure, trust
boundary/permission expansion, irreversible or destructive action, or a needed
Mac-local operation. Do not label a failed/missing **required** gate as Low or
ACCEPT just to reach merge.

When all remaining findings are documented **nonblocking Low**, present readable
findings, technical recommendations and one follow-up issue for genuinely deferred
items. Apply delegated technical authority within the agreed risk/scope and
required gates; seek goal-level confirmation if the actual risk tradeoff changes
the accepted product promise. Do **not** create a permanent automation subsystem
for a one-time risk decision. Keep unresolved follow-ups open.

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

The Mac-local `.git/ai-loop/run.sh` is operator-owned and **SHA-pinned**:
new control branch tips must not run without a separate explicit local
controller-SHA approval. General PR merge delegation does not authorize
silently updating this local trust root. Do not replace the launcher or request
`--approve` without identifying exact old/new SHA, diff, independent review,
real Mac tests and why the new code is necessary. Its approved execution path
has **not** been independently validated by GitHub documentation alone.

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
