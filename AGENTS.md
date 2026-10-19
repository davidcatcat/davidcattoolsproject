# Repository AI collaboration guidance

## AI team delivery and delegated authority

## Owner D7 — OpenCode PRIMARY / Codex Developer PAUSED (2026-10-19)

**New operative role order supersedes all older Codex-first text.**
OpenCode CLI/DeepSeek becomes the default local Developer. The previous
Codex Developer completed [PR #202](https://github.com/davidcatcat/davidcattoolsproject/pull/202);
its source is frozen and it must not write, retest, start Reviewer
subagents or new PRs without a further Owner decision.
The Codex Integrator remains PM/launch/receipt/merge owner when available,
not a routine code writer. Claude Code stays formal independent Reviewer.

Review chain: OpenCode tests/fixes → separate-context DeepSeek self-review →
fresh-context **authorized free different-model** review (GLM only if
actually free; otherwise qualified free alternative or BLOCKED) →
independent Claude Security/Functional/QA; FAIL returns to **OpenCode**
for bounded repair/retest/affected rereview. Only formal PASS plus
required CI/native evidence goes to Codex Integrator for merge.

Reuse merged PR #202's accepted adapter and merged PR #204's formally reviewed Host/F2 source; the registered D6
`tasks/a11c-opencode-193-s3` root holds an Owner-launched manual session,
which is **not** accepted as a Host-owned S3 invocation. No new controller, automatic paid
fallback, F2/egress waiver or historical `/claude` root takeover.
GitHub dispatch is not a Mac CLI launch; stop and report after actual S3 PASS.
**OpenCode must not self-authorize its Host.** Follow the normative
[exact-source D4 and independent Host review gate](skills/ai-team-delivery/SKILL.md#owner-d7-2026-10-19-supersession-opencode-primary-developer)
before any new provider read or use of updated admission/containment code.


**Always read [ai-team-delivery](skills/ai-team-delivery/SKILL.md) at the
start of product development, review, QA handoff or Git publication.**
The user is the requirements owner/product acceptor, **not** the day-to-day
PM, source-code reviewer, prompt courier or Mac test diagnostician. ChatGPT,
when available as the authorized coordinator, owns technical planning,
Codex/Claude delegation, evidence reconciliation, routine Low-risk
dispositions and eligible PR merges under delegated scope. **Owner D7 supersedes this: OpenCode CLI/DeepSeek is PRIMARY Developer; Codex Developer is PAUSED after frozen PR #202.** The existing D6 task worktree makes the historical `/claude` transfer unnecessary for new tasks. Claude (or a separately evidenced reviewer)
performs the independent review. The same agent's own checks do not become
independent QA or a security-review gate.

Do not ask the user to manage AI-to-AI prompts, repeatedly say "next step",
or perform routine Git/PM decisions. The user can act as the **physical Mac
operator** when the coordinator lacks that access; supply only a validated,
bounded instruction and diagnose its output yourself. Never claim that Mac,
provider or remote steps ran unless their evidence is present. The
owner confirms that `.git/ai-loop/run.sh` is the original tracked
Git-version shortcut, not a custom launcher. On the control branch
`scripts/ai-loop.sh` fetches the current Git revision and runs
`scripts/ai_loop.py` from a revision-scoped cache. AI, not the
owner, owns independently reviewed staged controller releases,
exact-candidate helper CI and applicable Mac acceptance **before**
moving the executable control tip. Ordinary technical revisions
do not require owner-by-owner confirmation. This Git source inspection
does not establish actual local Mac invocation/checkout evidence.
 Respect the frozen
`--qa-low*` STOP and separate provider-egress permissions. See the
canonical [ai-team-delivery Skill](skills/ai-team-delivery/SKILL.md).

Escalate material product/risk/authority changes, not every nonblocking
technical Low. Distinguish who proposed a technical judgment, who accepted
the product direction, who authorized Git work and who actually executed
it. Apply [safe-git](skills/safe-git/SKILL.md) and the relevant
independent evidence gates before merging.

**Owner reporting (approved 2026-10-19):** follow [Owner outcome reporting v1.1](docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) for evidence-backed M1b/M2/Mac-pilot milestone reports, one weekly compact pulse and material Owner decisions. Integrator handles routine work and independent gates; do not confuse a GitHub report with a notification delivered to ChatGPT or the Owner. The policy creates no new controller, authority or release milestone.

**Owner D1–D5 (2026-10-19):** Read [OpenCode developer allocation](docs/decisions/llmtool_opencode_secondary_developer_20261019.md) and [safe-git](skills/safe-git/SKILL.md). **D7 overrides this historical role order: OpenCode PRIMARY, Codex Developer PAUSED.** D6 task root is independent of the historical `/claude` transfer; Owner D6 allows additional registered task-scoped worktrees without a two-root ceiling. Claude Developer PAUSED, Claude Code remains formal Reviewer, company-gw unchanged, AGY backup, Cloud integration undecided. Non-sensitive whole-repo **reading** is not secret/egress/writing permission; never silently use paid fallback. Foreground active-Integrator handoff comes before separately verified original-session wake/G3.

**Owner token-efficiency + S3 stop (2026-10-19):** Before delegating llmtool work read [Developer-owned pre-review and S3 STOP](skills/ai-team-delivery/SKILL.md#2026-10-19-owner-direction--developer-owned-pre-review-economical-handoffs-and-s3-stop). **OpenCode Developer** must implement, test, self-review and fix ordinary findings before formal independent Claude Code review; **Codex Developer is PAUSED** and must not start a Reviewer subagent or new work. Prefer deterministic tests, qualified free-model pre-review and formal Claude QA. **OpenCode Developer must** perform its authorized qualified free cross-model pre-review (or record BLOCKED if no permitted qualified model is available). Codex Integrator/PM assigns complete work packages, avoids micro-handoff/token churn and handles final gates/integration, not routine duplicate code review. Company-gw participates only for route-qualified, privacy-authorized, actually useful low-cost advisory work; measure whole-work-package net token effects or `UNKNOWN`. **Current execution round forcibly STOPS on real OpenCode foreground S3 acceptance and evidence report**; do not automatically begin inactive-session wake or G4. This is not a new milestone, permission or waiver of independent QA.

**llmtool product direction:** Read
[the single A11 roadmap](tools/llmtool/ROADMAP.md) and the canonical
[ai-team-delivery Skill](skills/ai-team-delivery/SKILL.md).
C0–C3 are legacy A-delivery labels; do not create another C
milestone/implementation stream. Continue coordinating Codex/Claude via
the AI-reviewed, automatically updated **temporary ai-loop** until A11c
real-world acceptance proves native llmtool parity. A11b.2.2.2 private
**disposable-fixture-only F1 Git ADD is DONE/merged via PR #84** with
independent Security/Functional QA. F2 user-repository Git mutation is
**not authorized** until a separately reviewed approval/execution contract.
Apply the canonical user-impact-first defect triage in the Skill;
keep C1 excerpt export safety
(Issue #66) distinct from offline local executor design.

### 2026-10-17 accepted collaboration and Codex Cloud roles

Read [Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172),
[Roadmap](tools/llmtool/ROADMAP.md#owner-accepted-collaboration-architecture-and-mobile-codex-cloud-lane-2026-10-17)
and [ai-team-delivery](skills/ai-team-delivery/SKILL.md).
The **existing canonical Codex Integrator is PM + Integration Manager**
for complex tasks; it selects agents, subagents and dependencies.
**llmtool only executes/records authorized plans**, without becoming
an independent semantic PM. Original-session auto-wake and M2 Live
remain unverified.

**D-007:** Coordinator or Integrator may select an independent task for
mobile-initiated Codex Cloud development. Cloud uses its own configured
checkout and self-pre-review, then publishes an authorized task-owned
feature branch/Draft PR. **Under the 2026-10-18 handoff disposition,
the existing Integrator picks up the completed Cloud PR through actually available, authorized
CLI/GitHub workflows and arranges final assessment.** Owner link sharing
is an optional fallback, not a required completion relay. If remote launch
is unavailable, record the concrete task and limitation; a GitHub comment
does not prove that Cloud has started.
Self-review is not formal non-author Security/Functional/QA; maintain
shared-remote Git branch isolation and required native Mac gates.
No additional Mac worktree, Cloud auto-dispatch, llmtool F2 authority or
automatic main merge results from this workflow. See
[safe-git](skills/safe-git/SKILL.md).

## Canonical repository and pre-change checks

The canonical local Mac project repository directory is:

```text
/Users/davidsun/git/davidcataiporject/davidcattoolsproject
```

Local Mac Integrator/native acceptance remains at the canonical root. For
ordinary local Developer tasks, **Owner D6** permits authorized Agents to create
and retire additional registered task-scoped linked worktrees under the verified
llmtool workspace parent. There is no two-root cap or per-task G1-G7 pilot.
The authoritative create/cleanup gates and safe fallback are in
[safe-git](skills/safe-git/SKILL.md#owner-d6-2026-10-19-dynamic-task-scoped-worktree-lifecycle--new-normative-policy).
Historical Codex and Claude-named roots must not be claimed by a new Agent
without checking actual Session/process release and preserving existing data.

Before any repository-changing work, verify:

```sh
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short --branch
```

For local Mac work, the root must resolve to either the canonical Integrator
checkout or an explicitly registered, verified D6 task worktree. All other
project paths and project clones remain prohibited. Before each mutation or
retirement, verify actual task owner, root, branch, HEAD, common-dir and
working-tree data; a model's claim of completion is insufficient.
For user-selected Codex Cloud work, use only the checkout configured for that task.
Discover its root with Git and verify it is `davidcatcat/davidcattoolsproject`;
do not assume `/workspace` is the repository. Record environment, root, branch,
HEAD, remote identity and working-tree state. If identity/root cannot be established,
STOP before changes. Cloud is an explicit exception to the Mac path requirement,
not permission to create alternate local project checkouts.

For authorized documentation edits through the connected GitHub API, pin reads
and the change branch to a known base SHA and verify the final remote diff.
Do not claim local checkout, test or synchronization evidence from API operations.

Read [llmtool development workflow](tools/llmtool/WORKFLOW.md) when using Cloud or
handing verification between Cloud and Mac. Keep Mac-only VPN/auth/local-service
checks pending until verified on the actual Mac; complete independent Cloud work.

Authorized Developers may create and remove **task-scoped linked Git worktrees**
in the verified dedicated llmtool workspace parent under Owner D6, following
safe-git's normal create/retire checks. A Worktree normally retires
automatically after merged-and-accepted completion; cancelled or rejected work
requires a recorded preserve/discard disposition. Open PR/review/repair means
retain unless safe early-release/recreate is proven. Never delete an active,
dirty, ambiguous, or evidence-bearing tree, never use forced removal, and
never alter the canonical checkout. All ordinary removal decisions belong
to the Agent/Integrator without Owner micro-approval.

Independent project clones (including milestone clones), sibling/nested
repositories, arbitrary alternate checkout locations, and project checkouts
under temporary directories remain prohibited. The independently managed
Codex Cloud checkout is unchanged.

## Testing and runtime isolation

Mac Developers run ordinary targeted/regression checks from their registered,
task-owned worktrees with isolated venv/import/build state. Required native F1
containment and canonical integration acceptance remain in the unchanged
canonical root. Cloud runs only in its task-configured checkout; it does
not inherit local Mac D6 provisioning rights.

Test-generated temporary files may use normal temporary directories only when
required by the test/runtime itself. The llmtool `codex-cli` provider may continue
creating an ephemeral runtime cwd under `/tmp` or `/var/tmp` as part of its
security isolation. This runtime directory is NOT a project checkout and must
not contain a Git clone, worktree, or copy of the repository. Do not modify
`codex_cli.py` merely to eliminate this runtime isolation.

For llmtool development on the actual Mac with company VPN, follow
[development-time cost-aware dogfood](tools/llmtool/WORKFLOW.md#development-time-cost-aware-dogfood-available-now).
Use deterministic evidence first, prefer `llmtool git --json` for the local read-only
Git snapshot it already covers, and use the company LLM only through llmtool for
bounded triage/summarization of permitted data. Routing evidence is route/pipeline-
specific; read `skills/company-llm-delegation/SKILL.md` before making savings claims.
The positive L01 result belongs to its benchmark pipeline, not plain test-triage usage.
This engineering dogfood does not authorize automatic routing, code mutation,
security sign-off, merge approval, or A11c production behavior.

## Git permissions and safety

If the Codex sandbox prevents writing `.git`, for example `.git/index.lock`,
do NOT create another checkout or worktree, or move to `/tmp`. Leave source
changes in the authorized environment's repository and report the exact environment-specific Git
commands needed for staging, committing, and pushing. Those commands must run
from that repository and name only files belonging to the task.

- Preserve unrelated user work, including existing untracked files/directories.
- Do not use `git reset --hard` or `git clean -fd`.
- Do not force push or rewrite published history.
- Do not stash unrelated user work without explicit instruction.
- Inspect before modifying unfamiliar code.
- Tests and validation remain part of implementation.
- Never weaken tests just to make them pass.
- Stop for external review when the milestone requires it; do not automatically
  start the next milestone or merge main.
- Provider != Profile != Role != Agent != Skill. Capability/role/model identity never grants execution authority.

## CI incident knowledge base

Before diagnosing CI failures or cost regressions, consult [CI Knowledge Base](docs/ci/README.md) and follow [debug-ci](skills/debug-ci/SKILL.md). Add a concise reusable, evidence-linked lesson after a confirmed fix; do not create report-only commits that change an already reviewed release HEAD.

## Repository workflows

`AGENTS.md` is Codex-specific guidance; `CLAUDE.md` is Claude-specific guidance.
Read the relevant reusable workflow in `skills/<name>/SKILL.md` before using it.
Use `inspect-repo` for investigation, `implement-feature` for implementation,
`debug-ci` for failing checks, `review-change` for ordinary diff review,
`bug-hunt` for reproducible defect discovery, `security-review` for threat/security
assessment, `qa-validation` for public-interface QA, `validate-runtime` for live
runtime evidence, `company-llm-delegation` for bounded Mac+VPN company-model dogfood,
and `safe-git` for Git operations. These evidence gates are not
interchangeable. Keep one shared workflow per skill; Codex interface metadata belongs
in the skill's `agents/openai.yaml`.

Documentation-only changes require documentation validation, not application
builds. Report checks that passed and any that could not run. Do not claim remote
synchronization from local tracking references without fetching.

## Quality-gate discipline

A Task Contract/milestone decides which gates are required. Ordinary code review does
not automatically satisfy bug hunt, formal security review, QA, or live/runtime validation.
When a gate finds a real defect, preserve its reproducer/evidence, fix through the normal
implementation workflow, and re-run the affected gate. Do not start the next milestone
while a required gate is unresolved.

For llmtool, A11a is DONE and QG-S1/QG-QA1 are PASS/CLOSED. A11b is
IN PROGRESS: A11b.1 data-only contract DONE/merged via PR #32,
A11b.2.1 read-only Git preflight DONE/merged via PR #43, and
A11b.2.2.1 synthetic containment DONE/merged via PR #49, and
A11b.2.2.2 **private fixture-only F1** DONE/merged via PR #84 (`46b17b13`)
after independent Security/Functional QA and Mac native acceptance.
**F2 real user-repository mutation is NOT AUTHORIZED**, pending a separate
approval/sandbox/audit contract; A11b overall and A11c are NOT DONE.
A12 is future optional. The current
[Roadmap](tools/llmtool/ROADMAP.md) is the status authority.

## Default Cloud agent roles

For future Codex Cloud tasks, use Developer `gpt-6.1-sol` / high,
independent read-only Reviewer `gpt-6.1-sol` / high, and a bounded Git agent
`gpt-5.6-luna` / low when available. Reviewer replaces the previous Astra default.
Read [the role and handoff rules](tools/llmtool/WORKFLOW.md#default-codex-cloud-agent-roles--user-decision-2026-10-04).
Maintain one writer **per** registered worktree; Integrator serializes shared-ref/canonical operations. Follow D6 task-scoped creation and retirement gates.
Verify model availability and preserve all existing review/merge gates.
