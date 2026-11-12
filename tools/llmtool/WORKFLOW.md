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

#### Exact-SHA packet and privacy boundary

The immutable packet version 1 pins: both original contracts/bindings and
criterion map; author and independent reviewer bindings; base/subject exact SHA,
selected source blobs and complete changed-path diff; epoch/sequence/handoff/check
digest; pre-submission collector observation digest; selected actual command/exit/
environment/artifact evidence; exact-head CI repo/run/job/conclusion references;
approved provider/profile/model route and host permission reference. Every part
has its source identifier, byte length and SHA256. Canonical UTF-8 JSON (sorted
keys, compact separators, `ensure_ascii=False`, no duplicate keys/nonfinite
numbers) has SHA256 of UTF-8 `llmtool.review-packet.v1` + NUL + canonical packet
bytes, excluding its digest field. Preserve original TaskContract digest rules;
packet serialization does not redefine them. Source/diff SHA and original artifact digests are independently
verified, not simply copied from caller assertions. A packet hash proves byte
identity, not authorization or that tests ran.

Limits: total exported packet <=256 KiB; each unchanged TaskContract <=64 KiB
and its existing limits; diff plus selected source <=128 KiB; selected test/CI
metadata <=32 KiB; <=32 source paths and <=128 evidence references. The total
limit always wins. Inputs retain S1a safe stable no-follow regular-file/inode
checks, UTF-8/duplicate-key/record limits, plus S1b JSON depth <=16 before object
construction. No binary/submodule/external path
export; reject oversize, incomplete/truncated diff or absent relevant source
rather than silently summarizing away a changed path. Selected context must
include enough original code for each reviewed change; otherwise BLOCKED.

Only an explicitly allowlisted source/evidence export scope may leave the host.
Before any call, deterministically screen paths/content/metadata using existing
privacy components and deny secrets/auth files/credentials/private raw logs,
prompts, home paths/Session IDs and sensitive findings. Export stable opaque
identity/provenance references; local trusted collector retains the comparison
to actual private identity. Screen the entire rendered prompt and returned
verdict too; refusal outputs fixed codes, never rejected content. Approval of a
hash or redaction does not grant data routing. Issue #66's excerpt route is a
separate prerequisite if excerpts are selected, not blanket diff/log permission.
Unresolved privacy or provenance is BLOCKED. No raw packet/provider text in
diagnostics/journal; originals remain at their authorized source for inspection.

#### Invocation authority, lifecycle and failure

Use existing action/policy/approval interfaces and `run_budget`, not a new approval
ledger. D1 receipts/synthetic HostIssuer, G7 development rights, a S1a preview and
model approval are **not production authority**. Before a real call, a separately
reviewed host grant must name authentic issuer/delegated scope, task/paired
contracts/run/SHA/epoch, reviewer Session/route/profile/model, exact packet digest,
data scope, deadline and one-call allowance; recheck revocation/expiry and actual
state just before submission. Missing accepted production host/issuer wiring
blocks real invocation; contract merge and fake-provider tests do not create it.
The trusted host must enforce one-use admission across invocations/restarts;
reusing the same grant, including after timeout/unknown outcome, is rejected.
Its production authority/replay mechanism needs separate accepted evidence;
do not repurpose D1's synthetic sink or add a second approval store here.
This round grants no such wiring or smoke permission. No explicit verdict file
write is in v1; adding one requires its own scope/permission review.

Invoke only approved `claude-cli` via its existing provider and CLIProcessLifecycle:
`--print --output-format text --no-session-persistence --safe-mode --tools "WebSearch,WebFetch"`,
stdin packet, argv/no shell, isolated non-repository runtime cwd, existing minimal
child environment and approved auth-home provenance. Only WebSearch and WebFetch
are allowed; do not enable other tools, MCP or repo access. Never start an interactive
peer session. Attest the independently assigned
reviewer role/logical invocation Session through the trusted host and owned-child
launch record; provider brand/model text does not establish non-authorship.
Both reviewer agent ID and Session ID differ from **every** pinned author.
Ephemeral provider cwd/owned-child lifecycle is the existing isolation path,
not another project checkout or daemon; stop only this call's owned children.

S1b ceiling: one provider attempt, zero retries/fallback/repairs, configured call
timeout <=120 seconds, whole foreground run <=180 seconds, packet byte limits
above, parsed response <=64 KiB. Existing provider transport stdout/stderr ceiling
remains 1 MiB each; oversize verdict is rejected, not truncated or echoed.
Reuse prospective budget admission and record actual elapsed/calls/usage;
unknown tokens/cost stay null, never zero. Claude text mode cannot attest/enforce
a hard monetary/token cap: if the approved route requires such a cap, BLOCKED
before calling, not a fabricated guarantee. Route must expressly accept the
one-call/time/byte bounds and unknown-cost limitation.

Unavailable/auth/timeout/cancel/nonzero exit/partial or invalid output, missing
authority, stale/drifted observations or unknown cleanup returns a sanitized
BLOCKED/INCOMPLETE reason with invocation status and evidence references. Never
fallback to company/Codex/premium, retry, infer PASS from exit0 or claim successful
cleanup without lifecycle evidence. Preserve existing provider error codes and
A11a diagnostics schema. No implicit journal or packet persistence; stdout is
the explicit result channel. A provider failure cannot renew a grant or task.

#### Verdict/result schema and bounded developer handoff

Version 1 verdict is one strict JSON object (no fences/prose/unknown or duplicate
keys), <=64 KiB, bounded depth <=16. Required fields: `schema_version`, `review_id`,
both `ready_task` and `acceptance_task` TaskBindings, `source_sha`, `epoch`,
`sequence`, `packet_digest`, `observation_digest`, opaque `reviewer_binding_digest`,
`security`, `functional`, `findings`, `criterion_evidence`. Each decision is exactly
APPROVE/CHANGES_REQUIRED/BLOCKED. <=32 findings have unique ID, Security/Functional
category, #92 severity/priority, explicit release_blocker boolean, bounded
control-free summary/reproducer and allowlisted path/criterion/evidence refs.
<=32 criterion-evidence entries reference existing final criterion IDs and packet
evidence/digests; no model-supplied command, URL or path becomes executable input.
Text fields <=1024 UTF-8 bytes; IDs/digests follow existing validators. Missing
criteria, contradictory blocker+APPROVE, binding mismatch, self-review, absent
provenance or malformed verdict is INCOMPLETE, never accepted review evidence.
Severity/priority/blocker consistency follows #92's user-impact rules; an
explicit false blocker flag cannot waive a required security/functional gate.

The host envelope records `structural_validation`, `reported_state`, separate
`collector_status`, `invocation_status`, `verdict_validation`, independent
Security/Functional decisions and outstanding final criteria, plus task/source/
packet/evidence references and the host's post-response observation digest
(which cannot exist in the pre-call packet). The verdict echoes the pre-call
observation digest; the host compares the post-call observation before accepting
it. `authorized=False` is retained: this result grants
no subsequent effect. Collector validation authenticates only its stated
observations; actual invocation evidence proves a call ran, not test execution
or semantic correctness. The S1a runtime field remains NOT_VERIFIED; don't
overwrite it with collector/invocation success. Result exit0 requires both
validated APPROVE verdicts and accepted provenance, but still means review result,
not COMPLETED/DONE/merge. Valid CHANGES_REQUIRED/BLOCKED or incomplete prerequisites
exit1; invalid local input/schema exit2 via existing sanitized error handling.

CHANGES_REQUIRED returns a bounded handoff referencing exact source/contract,
findings, required criteria and originals to the registered developer; it starts
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
