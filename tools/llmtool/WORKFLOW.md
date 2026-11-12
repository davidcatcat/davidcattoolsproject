# llmtool development workflow

> Current collaboration, role and privacy authority is in [AGENTS.md](../../AGENTS.md). This file describes the normal delivery flow. Historical Codex Cloud, S1a/S1b, A11c and B-contract text is listed in the [workflow archive](../../docs/history/llmtool-workflow-archive-20261112.md) (#1502 WP-A); it is provenance, not current instructions.

## Current Developer and Reviewer routing (Owner decisions 2026-10-21 and 2026-11-11)

Claude Developer, OpenCode, AGY and home LLM `gw-agent` are Developer routes assigned by the PM only after checking [worker-dispatch.json](worker-dispatch.json); a PAUSED switch forbids dispatch regardless of live health. Codex is Reviewer and AI Debug only and writes no code (O1, #1502); disabling Codex Developer in code is 待實作（WP-C）. Direct OpenCode work uses a registered D6 task worktree, task-local permissions and a free DeepSeek model. D4/G3/M2 is background and does not block direct Developer work. Company `gw` (home LLM) is a Developer route only through the API Agent Gateway (`gw-agent`): it edits files inside its Task scope with line-range reads; the `company-gw` direct and proposal routes are retired (Owner 2026-11-11, #120). Preserve per-task data screening, no secrets/private data, usage and bounded-repair evidence, independent review and exact-head CI.

The formal Reviewer is from the other model family than the Developer under [AGENTS.md](../../AGENTS.md) (Codex for Claude Developer and PM changes; Claude for non-Claude work such as AGY Gemini models, OpenCode and `gw-agent`); other reviews are advisory. Formal reviews remain exact-head, read-only and subject to all applicable security, CI and Mac-native gates. Owner dispatch switches are in [worker-dispatch.json](worker-dispatch.json); the local monitor at [scripts/llmtool_monitor.py](../../scripts/llmtool_monitor.py) reports live technical state separately. The PM's next-step duty after each result is the [PM Continuation Contract](../../skills/ai-team-delivery/pm-continuation.md). Home LLM unavailability is recorded and does not block unrelated Developers.

Declare read and write paths at task claude-bind, including an admitted test module in the read paths (an unchanged test is valid). Order: commit, --record-result-task-id, --bind-native-session-task-id, then task claude-bind.


[Historical role and stop decisions are archived](../../docs/history/governance-archive-20261103.md#historical-role-and-stop-decisions).

## Normal delivery flow

`llmtool task advance` is the only normal flow (#1502 §3-1). It runs one step at a time:

1. Push the Developer's committed result branch.
2. Run the Task's admitted test modules on EC2 (`task remote-test`) before the PR is opened.
3. Open the PR.
4. Wait for exact-head Linux CI: `check (Linux 3.14)`, or `check (docs)` for a documentation-only PR, must pass before review.
5. Dispatch the formal cross-family review of that exact head.
6. After PASS, `task advance` adds the `mac-ci` label; exact-head `check (macOS 3.13)` must be SUCCESS where required.
7. Merge without syncing main when there is no conflict (M1, Owner decision #1317).

A required check must be SUCCESS. A check that does not apply to the PR is recorded NOT_RUN; skipped, cancelled or pending is never PASS. Environment problems (timeout, runner or EC2 unreachable) are not test failures: `task advance` reruns an EC2 timeout once on the same commit. Returning EC2, Linux CI and Mac CI failures to the original Developer as test-failure repairs, counted apart from review failures, is 待實作（WP-D）; today `task advance` stops at `CI_FAILED` on a CI failure.

Manual commands are recovery only: use the [manual recovery flow](../../skills/ai-team-delivery/pm-continuation.md#manual-recovery-flow) when `task advance` cannot run. Review FAIL, AI Debug, split and Owner notification rules are in the [PM Continuation Contract](../../skills/ai-team-delivery/pm-continuation.md).

## Owner outcome reports and decision boundary (2026-10-19)

Read the [Owner-approved outcome and reporting contract](../../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) when planning a milestone, review handoff or release disposition. Reuse Issue #120 and existing PR evidence. The Claude PM provides progress on request, reports major events, submits a short **evidence-backed** G1/M1b, G2/Worker Live, G3/M2 closed-loop or G4/three-task Mac-pilot report when the corresponding outcome actually passes, and escalates **only** changes to product/scope, permissions/data egress, costs, accepted material risk or the final GO/LIMITED GO/NO-GO. Otherwise keep authorized development moving. Two completed task packages with no real gate progress trigger a priority correction, not extra process or an automatic STOP. Report/notification/Owner ACK are separate observed states; a GitHub comment cannot wake ChatGPT or a Mac CLI. This policy does not add a daemon, tool, product gate or llmtool F2/Host authority.

llmtool executes an approved route and plan; it does not decide which AI owns complex work. Do not add a second controller or fixed PM CLI. Required failing security, QA or Mac/runtime gates remain blocking regardless of the Owner's general merge delegation.

[Historical AI-led operating model decisions are archived](../../docs/history/governance-archive-20261103.md#ai-led-team-operating-model).

no developer or repair. Changed source/run/epoch/contract requires new evidence
and separately permitted review, not reuse of this verdict/grant. BLOCKED returns
the specific missing gate for Coordinator disposition and preserves state.
Nonblocking Low is one #120 backlog using Issue #92 ACCEPT/DEFER/FIX; no infinite
review loop. N4/N5 status/physical-path wording is maintained here/ROADMAP; README
maintenance is included in Product A's authorized ordinary README touch.

#### Product allowlist and acceptance plan (no execution grant)

After contract acceptance, a separately pinned product task may change only
`tools/llmtool/llmtool/{task_review.py,task_review_cli.py,task_review_collector.py,cli.py}`,
`tools/llmtool/tests/{test_task_review.py,test_task_review_cli.py,test_task_review_collector.py}`,
and `tools/llmtool/{README.md,ROADMAP.md,WORKFLOW.md}`. These are thin adapters/tests
reusing TaskContract/TaskBinding/task_lifecycle, A5 hardened reads, existing
provider/lifecycle/privacy/error/diagnostics/authorization/budget components.
This is a proposed future allowlist, not permission to edit those files now.
Changes to existing shared schemas/providers/authorization/Git hardening need
explicit accepted scope amendment before authoring; do not copy or weaken them
to avoid that gate. No daemon/scheduler/second framework/logging/approval store,
F2/S2 mutation, Git write, automatic repair or PR merge. Company Round 1–3 evidence/policies remain unchanged;
no company call, rerun or new savings claim.

| Required future test | Expected behavior |
| --- | --- |
| Fake provider full journey | Paired pinned contracts, current verified collector, all actual pre-review criteria, valid WAITING_REVIEW handoff and explicit fake host grant -> one packet/call, exact-bound Security+Functional verdict, outstanding final gates visible |
| N1 combinations | SUCCESS with RUNNING/BLOCKED/FAILED/COMPLETED, WAITING_REVIEW without valid handoff, incomplete aggregate/supplementary evidence, unapproved pairing or forged review -> zero calls; final false completion rejected |
| N2 clocks/history | Historical terminal context vs fresh live TTL, exact expiry boundary, future/rollback/missing heartbeat and expiry during call -> no renewal/resume or false liveness |
| N3 identity/source | Wrong/unknown Session/author/role/root/common-dir/remote/branch/import, duplicate branch ownership, unexpected writer/test/lock/config, forged expect/events -> fail closed before call |
| Packet/route safety | Wrong SHA/digest/criterion/CI head, unsafe/aliased/changing file, oversize/deep/duplicate/binary input, secret/private path/log, arbitrary evidence URL, no issuer/route/budget -> zero calls and no sensitive echo |
| Provider/verdict | Unavailable/auth/timeout/cancel/nonzero/oversize/partial output, invalid enum/schema/identity/evidence, plain APPROVE or self-review -> non-PASS, zero retries/fallbacks, owned-child cleanup evidence |
| Drift and effects | HEAD/index/source/contract/run/epoch/sequence/Session/permission change before submission or during review invalidates eligibility/verdict; no Git writes/test/repair/extra file/journal persistence or unapproved subprocess/network calls |

Focused new tests first, then relevant S1a/TaskContract/provider/privacy/budget/
authorization regressions, required full checks and exact-head Python 3.9/3.14 CI.
Native read-only Mac collector/file/provenance cases require independently reviewed
bounded commands and exact tested source; fake provider first. Real Claude Mac
smoke is NOT_AUTHORIZED/NOT_TESTED until explicit authentic host and data-route
approval, accepted source gates and non-author Security/Functional review; a
required missing native/production gate blocks corresponding product acceptance.
Do not label fake tests live PASS or relax permissions to satisfy a gate. Deliver
one separate Draft product PR with exact evidence and independent review; author
never approves or merges it. The original docs PR required link/anchor/scope/contradiction
validation, applicable exact-head CI and independent Claude contract review only;
no application build/provider smoke or G5/G6/benchmark rerun.

Before work, pin a **Task Contract** with task ID, version/digest, goal, authorized
scope, forbidden scope, acceptance criteria and required evidence. Keep the exact
contract available at each handoff. No model, developer or reviewer may silently
change it, remove a condition or lower acceptance requirements. A user-authorized
change creates a new version, preserving its predecessor and authorization;
reassess the plan, approvals and affected evidence rather than inheriting VERIFIED.
Routine repairs within the existing scope do not require changing the contract.

Prefer an authorized, available company LLM for context preparation, summaries,
classification, log normalization, initial planning and first review when the data
is permitted for that profile. Preserve source/revision references, unknowns,
contradictions, constraints, unresolved findings and necessary evidence references.
Schema validity does not establish factual correctness. A summary never replaces
the pinned contract or original verification evidence; keep originals accessible
under the applicable data policy. First review cannot replace required independent
or external review. Company-provider unavailability is an environment/auth limit,
not a reason to declare PASS or silently choose an external provider. Documentation
work does not require a company-provider call merely to follow this preference.

Use three context layers: pinned contract, compressible working context, and
structured evidence. Company LLM compression applies only to working context;
it must retain restrictions, unresolved issues and evidence links. If context
cannot fit, use bounded chunks/references or report INCOMPLETE; never silently
drop forbidden scope or findings. These are handoff rules, not a new persistent
A11a record schema. Raw task content/evidence must not be added to diagnostics.

Record objective escalation triggers: schema failure, missing evidence, validator
reject, test failure, review disagreement or bounded repair exhausted. Escalation
raises the handling/review level (possibly to a person); fallback substitutes a
provider attempt under a recovery policy. Neither is determined by model-reported
confidence, nor does either grant permission to retry, execute or send data.
Before escalating to any destination, recheck authorized data, provider trust
boundary and required approval. A company summary can still contain sensitive
data; compression/redaction is not permission to export it. If policy denies the
transfer, stop that path and preserve bounded evidence for authorized resolution.
Use the agreed repair limit; exhaustion never permits weaker criteria.

**Company-gw guided-repair cost rule (Owner 2026-10-20; historical: the company-gw route is retired, Owner 2026-11-11, #120):** Follow
[the accepted A11c company Developer fallback and guided-repair decision](../../docs/decisions/llmtool_company_developer_assistance_fallback_20261020.md).
After the first failed company-authored candidate, prefer **at most one**
evidence-guided, narrow company-gw correction using real source/API signatures,
pinned tests and actual failure output, only if the current task authorizes the
call and has remaining known budget; skip it for unsafe, unsupported or repeated
guesswork. On another failure the **Integrator/PM chooses** whether to obtain a
narrow different-model diagnosis, transfer code repair to a qualified stronger
Developer, propose a separately authorized revised company strategy, or preserve
a resumable blocker. This is **not** automatic model fallback or a new repair
allowance. Compare recorded company, PM, Reviewer and takeover usage plus
validation/retry effort and eventual independent acceptance; unknown costs stay
UNKNOWN. A new author cannot self-approve the final change. Preserve prior
failed attempts/ledger, existing #92 and task-specific caps, egress/permission
boundaries, unchanged G3/G4/M2 gates and progress of unrelated tasks.

Treat model completion as a **claim**. Record evidence per acceptance criterion:
contract version/digest, tested revision or patch digest, command, exit status,
environment and required artifacts/references. For uncommitted changes, include
the relevant patch/artifact digest; HEAD alone does not identify tested bytes.

| Result | Meaning for an acceptance check |
| --- | --- |
| PASS | Executed on identified bytes/environment and met that criterion |
| FAIL | Executed and contradicted the criterion |
| INCOMPLETE | Interrupted, partial, missing/ambiguous evidence or unresolved disagreement prevents a decision |
| NOT-TESTED | The check was not executed; include the reason |

Keep existing SKIPPED-CONFIG/AUTH/ENVIRONMENT labels from validate-runtime as
specific non-PASS reasons, mapped to NOT-TESTED when aggregating acceptance.
Required non-PASS checks block VERIFIED/DONE; optional checks retain their explicit
status without being promoted to PASS. VERIFIED requires all contract-required
evidence; DONE additionally requires the contract's review/delivery/merge gates.
These words describe planned task acceptance, not new CLI commands, exits or A11a
run status values. Tests and deterministic checks only cover their tested cases;
they cannot guarantee all semantic correctness. Preserve required human/semantic
review evidence and disclose coverage limits even when checks pass.

Future cost evaluations must include context preparation, retry/repair, escalation
and all review calls, with reported input/output usage and trustworthy pricing
versions. Record missing tokens/cost as unknown/null, never zero; a known subtotal
is partial, not the total. Development-time paired Token Savings Benchmark evidence
may quantify dogfood ROI when its measurement contract is satisfied; it still does
not implement budget enforcement or an automatic cost router.

## Quality-gate model

Engineering evidence is split by purpose. Do not use one green result as a substitute
for a different required gate.

| Gate / skill | Question it answers |
| --- | --- |
| `review-change` | Does this diff correctly implement the requirement without obvious regressions or scope drift? |
| `bug-hunt` | Can we reproduce any observable defect in the current implementation? |
| `security-review` | What assets/trust boundaries/attack paths exist, and are there exploitable weaknesses or design gaps? |
| `qa-validation` | Does the product behave correctly through its public/user-facing interfaces across the defined matrix? |
| `validate-runtime` | Does the exact runtime/environment/provider/VPN/auth/inference layer actually work? |
| `safe-git` | Is the exact reviewed/tested revision safely published/merged with current CI evidence? |

Not every change needs every gate. The pinned Task Contract/milestone declares which
are required. A confirmed finding is fixed via normal implementation with a permanent
regression test where appropriate, then the affected gate is re-run.

## References

- [Repository guidance](../../AGENTS.md)
- [PM Continuation Contract](../../skills/ai-team-delivery/pm-continuation.md)
- [Roadmap](ROADMAP.md)
- [Workflow archive](../../docs/history/llmtool-workflow-archive-20261112.md)
