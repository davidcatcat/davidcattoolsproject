# llmtool development workflow — Codex Cloud and local Mac

## Single A-roadmap and interim controller (owner decision 2026-10-09)

**A11b and A11c are the only active native delivery milestones.**
The C0–C3 names in older plans are historical outcome/measurement
labels mapped **once** in [the authoritative ROADMAP](ROADMAP.md),
not separate development backlogs. **A11b.2.2.2 private disposable-fixture-only F1 Git ADD** was independently accepted and merged as PR #84 (`46b17b13`). The separate [F2 real-repository mutation and A11b.3 approval design](https://github.com/davidcatcat/davidcattoolsproject/issues/97) is next; **F2 execution remains NOT AUTHORIZED**. The historical F1 records remain in [Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70).
Retain [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) separately for C1 excerpt egress privacy. Offline A11b design does not wait for full company Phase 0 / paid-token benchmarking.
Only F1's private disposable-fixture Git ADD is implemented and reviewed. **Real user-repository/public Git ADD (F2) remains NOT AUTHORIZED** pending its own approved contract, Security/QA and Mac gates.

**Until native llmtool can coordinate Codex/Claude, use the
standard Git-provided temporary `ai-loop` launcher and controller.**
The owner confirms the Mac entrypoint is again the original
`.git/ai-loop/run.sh` shortcut to the tracked `scripts/ai-loop.sh`,
without a locally customized script. The Git launcher fetches the
latest `automation/ai-loop` revision, caches the corresponding tracked
shell/Python files and executes the Python controller with
`--control-sha` for that invocation. No per-revision owner signoff
is required. The tracked source behavior is verified in Git; real
Mac checkout and execution still need Mac evidence.

**AI owns the controller release:** review the immutable candidate
diff on a non-executable staging ref, obtain independent technical/
Security review where applicable, validate exact candidate with
helper CI and required Mac probes, then publish the unchanged
reviewed revision to the executable branch. Missing evidence is
a STOP; do not treat a latest-tip fetch as a release gate. See the
single [ai-team-delivery Skill](../../skills/ai-team-delivery/SKILL.md).
No second permanent controller, no model prompt couriering by the owner,
and no relaxed provider egress permissions.


**Cutover happens in A11c, not by renaming C3:** prove on the canonical
Mac that one llmtool task can run a scoped Codex/Claude/test/repair
cycle and return a verified Draft PR or explicit STOP, with independent
Security/Functional QA and no manual prompt handoff. Only then
freeze/retire overlapping ai-loop orchestration. C1 excerpt output must
not cross any company/external LLM boundary without its final-output
safety gate and explicit authorization; other source/log/diff exports
need their own distinct trust review. Claim Token savings only after
quality-equivalent, attributable measurements for the actual route.

## AI-led team operating model

This project's delivery responsibility is documented in
[ai-team-delivery](../../skills/ai-team-delivery/SKILL.md). The **owner**
defines the tool's functionality, outcomes, constraints and acceptance,
while the **AI coordinator** acts as technical PM: task scoping, Codex
implementation, independent Claude review/QA, follow-up tracking and
authorized safe Git merges. The owner is not an agent prompt courier or
routine technical signoff authority. Only genuine product/scope/risk
decisions, or a needed Mac-local command when no execution access exists,
should interrupt the owner.

The Mac-local `.git/ai-loop/run.sh` is the standard
Git-version shortcut restored by the owner; no custom launcher
is in scope. The tracked control-branch `scripts/ai-loop.sh` and
`scripts/ai_loop.py` are the reviewed source of execution.
AI owns technical review, staged CI and applicable Mac checks before
promoting control revisions; publishing an unreviewed latest tip is
not permitted. The user is only a physical Mac operator if tools
cannot access the Mac. Do not infer that GitHub source inspection
proves the current local symlink/launch state.  The C1 local-only excerpts feature
does not authorize sending excerpts to company/external providers
(Issue #63 remains open). The frozen `--qa-low*` route is not a
normal product release gate.

A coordinator may decide a documented **nonblocking Low** disposition
within the approved product boundary and leave a concise follow-up; it
must not invent extra product blockers/one-off tools. Required failing
security, QA or Mac/runtime gates remain blocking regardless of the
owner's general merge delegation. This document describes workflow,
not an installed autonomous Mac executor or background service.

### Bug report → user-impact triage → bounded release

Apply the [canonical ai-team-delivery defect policy](../../skills/ai-team-delivery/SKILL.md#user-impact-first-defect-triage-and-bounded-delivery-owner-decision-issue-92),
adopted in [Issue #92](https://github.com/davidcatcat/davidcattoolsproject/issues/92).
For every report capture the actual user journey, reproducible evidence
and SHA, Severity, Priority, **affected release blocker** and reviewer
disposition. A Medium false success or reachable fail-open still blocks
its own release. Another feature already isolated in main may continue.
Unclassified product/test failures keep only their affected required gate
on HOLD until independently distinguished. Test replacements must verify
the same behavior/invariants on the same SHA and required platform.

First reuse targeted user smoke/regressions; run required full Mac/CI,
functional and security gates at release time. A confirmed root-cause
family gets at most two failed repair attempts before coordinator
reassessment; overall review/token/time escalation does not waive a
failing mandatory gate. Place independent QA in the feature PR or
tracking Issue instead of opening a new report-only PR for every round.
Only the requirements owner's three P0 cases in the Skill require
direct owner yes/no escalation; provider egress and spending still
require distinct pre-existing authorization.

## Execution environment

Use Codex Cloud when selected by the user for repository investigation, development,
documentation, review, offline tests, builds and CI. Use the actual Mac for verification
that depends on its VPN, credentials, files, operating system or running services.
Cloud is a separate computer; it does not inherit the Mac's sessions or network.

| Work / verification | Environment | Evidence |
|---|---|---|
| Source/docs changes, review, unit tests, fake-runtime/mock integration, lint/build | Configured Cloud checkout; local Mac also supported | Exact SHA, commands, exits, results |
| GitHub branch, push, PR, CI | Cloud when access is available | Remote branch SHA, PR URL, CI run SHA/status |
| Company LLM `10.29.7.58:4000`, model `gw`, VPN coexistence | Actual Mac with company VPN | Route/reachability, auth if applicable, exact live marker |
| Existing Claude CLI / Codex CLI authentication, Keychain-dependent setup | Actual Mac | Executable/version, direct marker and llmtool E2E result separately |
| Mac localhost, Docker/VM/database, running processes, unpushed files, macOS isolation | Actual Mac | Actual dependency and revision tested |
| Public provider live tests | Explicitly configured and authorized environment | Real inference evidence; Cloud availability alone proves nothing |

Cloud network, tools, Git push and provider credentials depend on configuration and
must be checked, never presumed. Credentials in Cloud do not prove Mac authentication.
Do not copy Mac auth files/Keychain material into Cloud, expose an internal endpoint,
or change VPN/routes to bypass a missing prerequisite. Fake-runtime tests remain useful
Cloud evidence but are never called real provider validation.

## Development-time cost-aware dogfood — available now

### Current route evidence

Development dogfood is not a blanket company-first policy.

- `test-triage`: **only the measured L01 benchmark pipeline is validated for the
  token-saving claim**. It used ~48 KiB canonical raw output, complete FAILURE-ID
  coverage, a deterministic ±2-line excerpt selector capped at `min(32 KiB, 20%)`,
  inline selected raw, and no full-raw re-read by Codex. Plain skill invocation is not
  validated for savings; Round-1 small-evidence B01 measured −103.32%.
- `log-triage`: **EXPERIMENTAL** for savings/reliability claims.
- `context-pack`: **EXPERIMENTAL** for savings/reliability claims.

For any new/redesigned route, Company-only repeated Phase 0 is the first model gate.
Do not authorize a paired Codex benchmark unless that route first passes reliability,
quality and complete-usage accounting. P1 failure-usage preservation improves accounting
but does not turn incomplete/invalid model output into PASS.


This is an engineering workflow for the actual Mac when the company VPN/provider is
available. It dogfoods existing llmtool capabilities while developing llmtool; it is
**not** A11c production routing, does not add an executor, and does not authorize
automatic retry/fallback/repair, code mutation, Git mutation or merge.

Use this order:

1. **Deterministic first.** Run tests/lint/typecheck/build/schema checks directly and
   keep command/exit/result as source evidence. For local repository inspection,
   prefer the existing A5 `llmtool git --json` snapshot when it covers the needed
   branch/HEAD/status/diff/recent-commit facts. Use direct Git/GitHub/CI commands only
   for facts the snapshot does not provide, and prefer bounded/structured output over
   repeatedly feeding verbose command output back to Codex.
2. **Company LLM only for bounded, route-aware triage.** When data policy permits and
   the actual Mac/VPN can reach the configured company profile, first deterministically
   redact or discard secrets/sensitive values; model-side redaction is not a security
   control. `test-triage` may still be used as a bounded development aid, but do not
   inherit the L01 savings claim unless the validated benchmark mechanics (large raw
   input, complete coverage, deterministic selector, excerpt-only Codex consumption and
   validated generation settings) are actually present. `log-triage` and
   `context-pack` remain experimental aids. Do not call the gateway directly to perform delegated engineering work; a
   direct endpoint probe is reserved for explicit runtime/connectivity comparison.
3. **Strong verifier for judgement.** Codex/Claude/human must inspect source plus raw
   evidence for root cause, code changes, architecture, security/auth/permission,
   concurrency/race, data-corruption, test correctness and acceptance/merge decisions.
   Company-model confidence or prose cannot establish PASS, SAFE, DONE or merge-ready.
4. **Validate derived identifiers.** Any test/file identifier returned by company
   triage must be checked deterministically against the supplied bounded evidence before
   use as a locator; unsupported identifiers are invalid derived data, not source facts.
5. **Repair loop stays evidence-driven.** Re-run the narrow deterministic failing check
   after a fix, then the required broader regression. Company triage may be repeated on
   newly bounded failure evidence, but it never overrides a command exit/test result.
6. **Record delegation evidence.** For each company-model delegation, capture task type,
   selected runtime skill, provider/profile, **data permission basis**, bounded input
   scope/bytes, usage tokens when reported (otherwise unknown), latency, schema validity,
   identifier validity/invalid identifiers where applicable, whether Codex adopted/
   overrode the result, and the later deterministic/independent
   verification outcome. Keep this in a development report; it is not a new diagnostics
   persistence schema. The report must not copy raw secrets or an entire sensitive log;
   use a safe reference, digest or minimal redacted excerpt when raw evidence is retained
   elsewhere.

If VPN/company provider is unavailable, mark the optional dogfood step skipped/blocked
and continue with the normal strong-model workflow unless the milestone explicitly
requires live company-provider evidence. Never weaken acceptance criteria to make the
cheap path usable, and never export data to an external provider merely because the
company path failed.

### Token savings measurement

Do not claim that dogfood saves Codex tokens merely because company-provider usage is
low or because the Codex prompt becomes shorter. For a quantitative claim, run a paired
control/dogfood benchmark with the same bounded fixture, exact repository/test revision,
Codex model, reasoning effort/speed and acceptance criteria. Start each variant in a
fresh Codex session and prevent cross-variant answer/context leakage.

For the dogfood variant, Codex initially receives the company-derived summary/packet,
deterministic schema/identifier-validation result and a reference to the original
bounded fixture. The full raw fixture is not automatically injected into model context.
Before Codex relies on a company-derived claim, it must inspect the relevant original
raw evidence; record actual ranges/bytes read. A dogfood run that skips required raw
verification is invalid for a savings claim.

Record, when actually reported by the runtime/accounting source:

- Codex control: input, cached-input, output, reasoning and total tokens;
- Codex dogfood: the same fields;
- company LLM: prompt/input, completion/output and total tokens;
- measurement source for each set and whether it is exact, reported aggregate or unknown;
- wall-clock duration, Codex tool-call count and **all** company calls/retries;
- raw fixture bytes/ranges actually read by dogfood Codex;
- pre-registered run order;
- final deterministic/independent quality result and grading method;
- company result adopted/overridden and schema/identifier validity.

Do not infer exact tokens from context bytes, remaining quota, credits, percentages or
pricing. These may be recorded separately as supporting metrics only. When a source
reports `total_tokens`, use that source total for the main comparison; do not add
cached/reasoning subfields again unless that source explicitly defines them as excluded
from total.

Compute only when required totals are known and comparable:

```text
codex_tokens_saved = control_codex_total - dogfood_codex_total
codex_savings_pct = codex_tokens_saved / control_codex_total * 100

dogfood_mixed_token_volume = dogfood_codex_total + company_llm_total
mixed_token_volume_delta = control_codex_total - dogfood_mixed_token_volume
mixed_token_volume_delta_pct = mixed_token_volume_delta / control_codex_total * 100
```

A negative saving is a valid result and must be reported. The mixed-token-volume delta
combines different model/tokenizer units and is descriptive only; it is not a cost,
compute or energy metric. Always report Codex and company totals separately. Monetary
comparison requires trusted per-model pricing/version data.

Each fixture's gold labels/forbidden claims and fixture digest must be fixed before
execution. Use deterministic grading where possible; any semantic remainder must be
graded by an independent reviewer blinded to variant identity. Quality regression,
missing required raw-evidence verification, missing acceptance evidence or a materially
different task invalidates the pair for a savings claim. Keep unknown/null rather than
zero when exact usage is unavailable.

This measurement is development evidence only. It does not add an automatic cost router
or make token savings a security/QA acceptance substitute.

## 1. Establish repository and scope

Read AGENTS.md, README, ROADMAP, applicable skills and current review reports.
Local Mac canonical root remains `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Ordinary Codex/Claude development in **exactly the two existing, fixed,
separately owned worktrees** becomes ACTIVE **only after** the independently
reviewed G7 activation-status PR is merged to `main`; a draft/OPEN copy
remains DISABLED. The [safe-git exception](../../skills/safe-git/SKILL.md#owner-approved-two-agent-developer-worktree-exception-not-activated)
is the single normative policy. Before every task, require independently
verified owner/root/session/branch/child state, clean worktree, bounded
no-prune fetch, a fresh unique owned branch from verified `origin/main`,
then own-root venv/import provenance and pinned preflight/baseline; see
safe-git's activated task-turnover procedure. Retain the single-canonical
CLAIM/RELEASE fallback on any uncertain or crashed state. Required native F1
containment/acceptance stays canonical-only. No third checkout, F2
repository mutation, historical process cleanup, CI/Runner or ai-loop
control activation is authorized by G7.
In Cloud discover the task-configured checkout; do not create a clone/worktree to
repair a missing repository. Verify the repository is `davidcatcat/davidcattoolsproject`.

Run from the discovered root:

```sh
pwd
git rev-parse --show-toplevel
git remote -v
git branch --show-current
git status --short --branch
git rev-parse HEAD
git fetch origin
git rev-parse origin/main
git rev-list --left-right --count HEAD...origin/main
```

Keep credentials out of reported remote URLs. If fetch fails, report it and do not
claim tracking refs are current. Preserve unrelated changes. Work on a task feature
branch based on the confirmed intended base; do not automatically update main.
The Cloud exception does not permit other Mac paths or agent-created project copies.

## Task Contract, company LLM and evidence

This section is engineering guidance and future A11b/A11c design; it does not
implement a task engine, hooks, automatic routing, retry, fallback or escalation.
Current A9/A10 fail-fast contracts and A11a metadata-only diagnostics are unchanged.
The [roadmap](ROADMAP.md#architecture-refinement--planning-only) assigns runtime
Task Contract enforcement/execution gates to A11b and route-by-task integration,
independent verification and bounded repair to A11c. Context budgets and cost
routing require later design. The separately gated S1a preparation exception below
does not authorize production integration or introduce another milestone.

### S1a offline task event inspection contract

The Coordinator-adopted [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)
split and [roadmap](ROADMAP.md#issue-120-staged-preparation--s1a-then-s1b-then-s2)
permit S1a while A11b remains incomplete. **First** publish a separate Draft docs
PR changing only ROADMAP.md/WORKFLOW.md, obtain non-author Claude CLI Security +
Functional contract review and applicable exact-head CI, and have the authorized
Coordinator/Integrator merge it. **Only after observed merge**, a safe C2/C3
task boundary and the pinned implementation contract may product authoring start.
The docs-only contract merged via [PR #127](https://github.com/davidcatcat/davidcattoolsproject/pull/127).
The [accepted v1 product handoff](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6123940052)
resolved M1/L2 before coding. S1a offline inspection is **DONE/MERGED** via
[PR #128](https://github.com/davidcatcat/davidcattoolsproject/pull/128), reviewed
source `c9c8747b59ac49b18dbc521e8af9ac7f803212fe`, merge
`ffb4c78e72acadcdbb17d4470c4bf4a051cce247`, with independent Security/Functional
APPROVE and exact-source plus post-merge Python 3.9/3.14 CI PASS. This closes
only S1a; its offline runtime remains NOT_VERIFIED and authorized=False.

S1a's user surface is
`llmtool task inspect --contract <file> --expect <file> --events <file> --observed-at <UTC-time> --json`: read bounded
caller-supplied files, deterministically validate/replay Task Events, and emit a
state snapshot plus a read-only handoff proposal. The input event file is retained
by its caller; inspection does not append/write a ledger or authenticate it.
Reuse `task_verification.TaskContract`, `action_contract.TaskBinding`, and A11a
run/error/evidence conventions; leave the existing diagnostics schema/store and
approval system unchanged.

Required validation and evidence boundaries:

- Pin expected task/run, contract version/digest, source SHA, epoch and role/root
  bindings in mandatory independent `--expect` before replay; reject expect/events
  inode aliases. Keep TaskContract unchanged. Do not derive expectations from the event being
  checked. Bound file bytes, records, identities and evidence references. Reject
  unknown/duplicate JSON keys, malformed/truncated/oversize input and unsafe
  symlink/nonregular files without echoing raw input or secrets.
  On Mac use physical input paths such as `/private/tmp`; `/tmp` and
  `/var/folders` have symlink ancestors and are deliberately rejected (N4).
- Match every event's Agent + logical Session, Role, assigned Root/common-dir/
  branch, Task, Run, exact SHA and contract version/digest to those expectations.
  Wrong role/root/run/task/source/contract rejects; developer roots cannot claim
  `main` or a peer task branch. Developer identity cannot count as its own
  independent reviewer: reviewer agent ID **and** logical Session ID must each differ
  from all pinned authors/developers. Unknown authorship is INCOMPLETE.
  Provider/Profile/Role/Agent remain distinct concepts.
- Require unique event IDs, the pinned epoch, strictly ordered sequence and
  expected prior state; start at 1, then consecutive +1. Reject duplicates, old epochs, replay/out-of-order events,
  illegal state skips and post-terminal events. A changed SHA/contract/epoch
  invalidates old review/handoff evidence. Pin exact limits, sequence/resume rules,
  allowed transitions and stable exit/error taxonomy in the implementation
  contract before coding; no model may relax them.
- Expiry validation uses an explicit observation-time input. Missing/ambiguous
  UTC time is rejected; seconds-only `YYYY-MM-DDTHH:MM:SSZ`, no machine-clock default.
  Independent heartbeat TTL is 1..86400 seconds. Missing/ambiguous
  time, expired heartbeat or clock rollback remains stale/unknown/incomplete;
  heartbeat loss does not prove a process died. No automatic resume, renewal,
  failure declaration, process kill or workspace cleanup. Missing identity or
  execution provenance never becomes a healthy live observation.
- Return separate `reported_state`, `structural_validation` and
  `runtime_evidence_status`. Offline runtime evidence stays **NOT_VERIFIED** and
  `authorized=False`; structural success/exit0 is only input validation, even for
  a COMPLETED record. Existing `verify_task` may evaluate caller-supplied evidence
  structurally, but its PASS cannot certify that checks or independent review
  actually ran. Model claims satisfy no acceptance criterion. Required actual
  evidence binds tested SHA/patch digest, command, exit, environment and artifacts;
  missing/unverified results cannot establish PASS/VERIFIED/DONE.
- Ledger status, review text, identity strings, digest or handoff proposal never
  authorizes Git, provider, command execution or PR changes. S1a starts no agent,
  runs no command/test/Git/gh subprocess, makes no provider call, and writes no
  implicit state. Output uses sanitized bounded metadata/references, preserving
  A11a privacy rules rather than copying raw logs/diffs/prompts into diagnostics.

S1b's proposed [foreground review contract](#s1b-foreground-review-contract-draft)
defines the separate exact-SHA packet and permitted data route; it is not an
S1a provider path or an execution grant. S2 later adds permission-controlled
repair/execution under A11b's separately accepted approval/sandbox/recovery/audit/
budget gates. **F2 remains NOT AUTHORIZED**;
S1a does not imply A11b DONE, full A11c acceptance or ai-loop parity/cutover.
No daemon/scheduler, new temporary ai-loop controller or duplicate approval/logging
system. Company-LLM Round 1–3 evidence and existing measurement/privacy policy
are reused without rerunning benchmarks; no company or other provider calls in
S1a, no new savings claim, and company integration stays deferred.

The product slice needs focused positive replay and negative binding/sequence/
transition/expiry/self-review tests; false model-only completion, corrupt input,
file safety/privacy, explicit non-PASS runtime labels and no-subprocess/no-write
checks; relevant TaskContract/A11a/CLI regressions; required full checks and
exact-head Python 3.9/3.14 CI; focused native Mac file/CLI checks where applicable.
Publish a separate Draft product PR for independent Claude Security + Functional
review. Offline tests do not satisfy later live collector, concurrent ledger or
real-agent/repair acceptance. Keep required missing gates blocking, retain interim
ai-loop until canonical parity, and use Issue #92 for nonblocking Low dispositions.

### S1b foreground review contract (DRAFT)

**STATUS: contract accepted/merged via [PR #129](https://github.com/davidcatcat/davidcattoolsproject/pull/129)
at `8468164d5503953f941b24c1f2318d5decda42b5`; S1b Product A is a fake-only
candidate, independent product review PENDING. Real Claude remains BLOCKED;
actual automatic handoff NOT DELIVERED, pending B.**
This is the docs-only response to the accepted
[S1b handoff](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6127091068)
and [N1–N5 review](https://github.com/davidcatcat/davidcattoolsproject/pull/128#issuecomment-6125617914).
The original contract PR changed only ROADMAP.md/WORKFLOW.md. Independent non-author contract
review, applicable exact-head CI and authorized merge precede any product work;
then pin a product handoff, pass fresh G7 C2/C3/C4 and independent exact-branch
baseline acceptance. Neither this text nor its merge authorizes provider egress.

#### Public outcome, scope and stage predicates (N1)

The proposed product command is one foreground
`llmtool task review --contract <ready-file> --acceptance-contract <final-file> --expect <file> --events <file> --json`.
It validates preparation, collects actual read-only evidence, submits one permitted
packet to the existing independent Claude CLI text/no-tools provider, and returns
a bounded verdict/developer handoff on stdout. No background work, ledger append,
automatic artifact persistence, repair, test execution, Git/PR mutation or merge.
The collector supplies trusted current UTC plus monotonic time; caller timestamps
are claims, not the admission clock. The existing `task inspect` surface/schema,
TTL and runtime NOT_VERIFIED/authorized=False semantics remain unchanged.

Use **two separately pinned, immutable existing TaskContracts**, selected together
by the authorized Coordinator before readiness events exist: a preparation task
with only required pre-review checks/artifacts, and a distinct final acceptance
task with those checks **plus required Security and Functional REVIEW criteria**.
The packet pins both TaskBindings/task IDs, run IDs, versions/digests and the
explicit criterion mapping; both bind the same exact source SHA and author set.
The preparation goal explicitly excludes product completion. Do not derive it by
stripping criteria from a submitted final contract, silently edit a contract,
reuse final-task events under the preparation binding or manufacture prior review.
Missing/unapproved pairing is BLOCKED. This reuses TaskContract without changing
its schema or making required review optional; outstanding final review criteria
are always visible in the review result.

| Decision | Required conjunction |
| --- | --- |
| Admit review | Preparation inspection `structural_validation == SUCCESS` **and** `reported_state == WAITING_REVIEW` **and** valid latest pinned HandoffRecord; all mapped pre-review criteria supported by actual accepted evidence; no rejected supplementary evidence; fresh collector, independent reviewer, policy/route/budget gates all pass |
| Accept final completion claim | Final-task inspection SUCCESS **and** reported_state COMPLETED **and** both exact-bound reviewer verdicts APPROVE, authenticated invocation provenance and all final required actual evidence accepted under the contract |
| Any other state/result | No call or completion promotion; FAILED/BLOCKED/RUNNING/STALE/INCOMPLETE/unknown never becomes eligible from exit0 or valid JSON |

The preparation handoff's outstanding criteria must equal its own inspection
result (empty on SUCCESS); the packet/result separately lists the final REVIEW
criteria still outstanding. This prevents an impossible trigger requiring a
previous review. Rejected/failed/partial supplementary evidence still caps the
aggregate; model claims never substitute for checks. A successful review does
not append COMPLETED to either ledger, certify semantic correctness, grant
execution rights or declare DONE; a later authorized consumer must independently
verify final evidence and delivery gates. Existing S1a runtime is never upgraded.
The final check evidence must be independently validated under its final binding
against the same original execution artifacts; remapping readiness criterion IDs
does not rewrite or authenticate a caller EvidenceRecord.

#### Historical evidence and live identity (N2/N3)

Historical COMPLETED/FAILED records may supply **historical context** only after
sequence/epoch, exact source/contract/run, original execution/verdict provenance
and immutable artifact digests are verified. Report their original timestamp and
validation scope separately from present liveness. The archival structural
check may use the recorded terminal time, explicitly labeled historical rather
than current inspection SUCCESS; it cannot renew eligibility or bypass S1a TTL.
Missing original proof is NOT_VERIFIED, including structurally valid terminal
records. Changed source/contract invalidates reuse for the new task. No review
call is admitted from archived terminal records, even if historically valid.

Live eligibility uses the existing 1..86400-second pinned heartbeat TTL, current
trusted UTC and monotonic observations. Reject future/rollback/expired events,
stale collector samples, inconsistent clocks, unknown Session or partial state.
Immediately before submission and after response, take a new trusted observation
(no older than 5 seconds at decision time) and recheck source, contract pair,
task/run, epoch/last sequence/handoff, writer ownership, Session and remaining TTL.
Expiry or drift during review invalidates the verdict for acceptance. Heartbeat
loss is unknown liveness, not proof of death; no auto-resume/renew/kill/cleanup.

A small host adapter around `git_intel`, task_lifecycle and existing runtime
evidence performs the collector. Its trust source is the pre-existing reviewed
G7 root/owner/task registration and trusted CLI host Session records, selected
outside `--expect`/events/model content. Record provenance references/digests and
compare independent expectations to observations; expectations constrain values
but cannot authenticate them. Env UUID, PID, TTY, helper ancestry, a matching
caller JSON or an archive alone is insufficient logical Session proof. If the
host cannot attest a logical Session and its current ownership, BLOCKED before
the provider call; do not add a self-attestation fallback. Cooperative same-UID
trust remains the G7 assumption, not OS isolation or hostile-peer protection.

Required actual observations: physical assigned root/dev/inode/UID/mode/no-follow
ancestors; registered locked worktree/common-dir/repository/remote; unique owned
branch and exact HEAD/index/clean tracked state; registered untracked/ignored
baseline preservation; current writers/tests/locks; developer/reviewer logical
Agent/Session/Role and authorship; approved executable/prefix/import/pyvenv home
provenance. Each developer branch has one owner; reject duplicate claims,
peer/main/automation takeover, symlinks, unsafe config/env/filter/hooks, wrong-root
imports, unknown writer or drift. Do not execute a caller-selected interpreter
or import peer code to establish provenance: attest the collector's own runtime
in-process and require trusted host launch evidence for the reviewer runtime.

Local Git reads use a pinned trusted executable, fixed allowlisted argv, literal
validated refs/paths, no shell, disabled hooks/fsmonitor/filters/textconv/ext-diff,
no pager/prompts/lazy-fetch/replacement objects and bounded output/time. Reuse A5
hardened reads; any required new read primitive is individually reviewed/tested,
not arbitrary caller argv. Per-read deadline <=10 seconds, whole collection
<=30 seconds, <=32 reads, metadata <=1 MiB/read and <=2 MiB total before packet
minimization. No fetch/ref update/install/switch/clean or peer/historical-process signaling.
Bounded read timeout cleanup stops only its own Git child under the existing
A5 lifecycle principle. Remote PR/CI
reads use an explicitly permitted fixed GitHub route, repo/PR/head binding,
<=8 requests, <=10 seconds/request within that same 30-second collection bound;
no redirects to arbitrary hosts, URL fetching from events or credential export.
Missing remote permission/facts means BLOCKED, never an empty successful result.

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
`--print --output-format text --no-session-persistence --safe-mode --tools ""`,
stdin packet, argv/no shell, isolated non-repository runtime cwd, existing minimal
child environment and approved auth-home provenance. Never start an interactive
peer session or enable tools/MCP/repo access. Attest the independently assigned
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
temporary ai-loop controller, F2/S2 mutation, Git write, automatic repair or PR
merge. Interim ai-loop and company Round 1–3 evidence/policies remain unchanged;
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

Current llmtool state: A11a DONE; QG-S1 PASS/CLOSED (required future design
controls retained), QG-QA1 PASS/CLOSED through merged PR #31 and formal QA
round-2 APPROVE. A11b is IN PROGRESS: A11b.1 Typed Action & Approval Contract is
DONE/merged via PR #32 (`e5525f18`) as a data-only slice with offline validation
and no execution/mutation; A11b.2.1 read-only Git preflight and
A11b.2.2.1 synthetic containment are DONE/merged via PR #43 and PR #49;
A11b.2.2.2 **F1 private disposable-fixture-only Git ADD** is DONE/merged
via PR #84 (`46b17b13`). F2 real-repository Git mutation remains NOT
AUTHORIZED. A11c.1 S1a is DONE/MERGED via PR #128; S1b contract is
accepted via PR #129. Product A is a fake-only candidate, independent product
review PENDING, real provider BLOCKED pending B;
full production integration is NOT STARTED; A12 future optional. Historical
reports retain their original checkpoint status.
Any later executor requires the separate QG-S1 sandbox, authority, replay,
durable-audit and state-recheck controls.

Development-time company-LLM dogfood above is allowed as engineering assistance
and does not advance or satisfy A11b/A11c product gates by itself.

QG-S1 and QG-QA1 are quality gates, not new product milestone numbers.

## 2. Plan verification before implementation

Derive acceptance checks from the pinned Task Contract, then classify them as
Cloud-capable, Mac-required, or not required for this change.
A check is Mac-required because of an actual dependency, not merely because it is a
smoke/integration test. Declare required merge gates before execution; do not demote a
failed/missing required check after seeing its result. Documentation-only changes use
documentation validation and need no live provider request or application build.

## 3. Complete Cloud work

Implement the agreed scope, update tests/docs, run relevant available checks, review
the final diff and repair issues within the task. Record skipped checks and why.
Use implement-feature, review-change and safe-git. Authorized branch publication
may proceed even while required Mac checks remain pending; that is not merge readiness.
If push/PR access is unavailable, retain the patch/commit and give exact handoff steps.
Do not stop independent Cloud work merely because one Mac-only check cannot run.

## 4. Publish a Mac verification handoff

Record the following in a dated validation report under `reports/` and link it in the PR:

- Repository, feature branch, base SHA and exact implementation SHA.
- Cloud environment/platform/tool versions and commands with exit codes.
- Each Cloud check result, CI URL/run SHA, and final diff review result.
- Each pending Mac check, its prerequisite, copyable commands, expected marker/exit,
  and reason it is required (or explicitly optional).
- Separate code/documentation completion, required verification, review and merge status.

Use PASS, FAIL, SKIPPED-CONFIG, SKIPPED-AUTH, SKIPPED-ENVIRONMENT or NOT-TESTED per
[validate-runtime](../../skills/validate-runtime/SKILL.md). For pending required
checks, state **LOCAL VERIFICATION REQUIRED / MERGE BLOCKED**; this is report
bookkeeping, not a new llmtool task state or implemented command.
A screenshot/model assertion without tested revision is insufficient evidence.

## 5. Verify on the actual Mac

Run required native/canonical acceptance only at the original canonical Mac
repository; inspect branch/status and preserve unrelated work. A developer
worktree's ordinary tests never substitute for native canonical checks, and
its venv must resolve llmtool imports within its own registered root.
Fetch the published feature branch using safe-git; establish the intended tested SHA
without blindly pulling, resetting, stashing or overwriting changes. If existing work
prevents switching safely, report the blocker. Do not use `/private/tmp` worktrees.

Confirm the expected implementation is present and the installed llmtool/runtime
belongs to that checkout. Run only applicable pending checks using validate-runtime:
small marker-only requests, explicit config, bounded timeouts and no secret dumps.
Company LLM checks require the actual company VPN; check required internal/external
and GitHub connectivity separately. Mac-only services/auth remain Mac evidence.

Append platform, runtime version, exact tested SHA, commands/exits/markers and results
to the report. Skipped checks are not PASS. A failed required test blocks merge;
an environment/auth failure is not automatically a code regression.

## 6. Review and merge gate

Check the pinned contract and original evidence before accepting a completion claim.
Do not use summaries or model confidence to bypass a missing required check.
Merge only when task authorization and every gate required by the Task Contract/milestone are satisfied: applicable Cloud checks/CI, review, bug-hunt/security/QA gates, and required Mac/runtime verification. Do not mark a milestone DONE
merely because Cloud implementation or mocks passed. Optional tests can remain
NOT-TESTED when the milestone explicitly permits that, with the limitation reported.

Evidence binds to the tested code/config/test revision. Any relevant change after
Mac validation invalidates affected results and requires rerunning those checks.
Report-only commits may cite the tested implementation SHA when the reviewed diff
proves runtime/config/tests unchanged. Fetch the latest target and review integration
changes; rerun affected checks when integration alters the validated behavior.
Never reuse historical PASS as a new live result or merge automatically because CI is green.

This is a development process, not an A11 executor or A12 remote frontend feature.
It does not add commands, credentials, execution capabilities, dependencies, automatic
approval or fallback, and does not change milestone numbering or implementation status.

## References

- [Codex Cloud overview](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [Repository guidance](../../AGENTS.md)
- [Roadmap](ROADMAP.md)

## Default Codex Cloud agent roles — user decision 2026-10-04

Apply these defaults to future user-selected Codex Cloud development tasks unless
the user explicitly changes them. This is a development-tool workflow, not llmtool
runtime routing or a new milestone.

| Role | Requested model / reasoning | Responsibility |
| --- | --- | --- |
| Developer | `gpt-6.1-sol` / high | Implement the accepted scope and run required tests; sole source/test writer |
| Reviewer | `gpt-6.1-sol` / high | Independently inspect requirements, source, diff and evidence; read-only review |
| Git agent | `gpt-5.6-luna` / low | Bounded branch inspection, staging/commit, push, PR updates and exact-head CI checks; execute an authorized merge after all gates pass |
| Coordinator | Current main agent | Pin scope, coordinate handoffs, decide acceptance and handle exceptions |

The Reviewer default supersedes the earlier Astra preference for future tasks.
Preserve historical reports and requested-model records; do not relabel past runs.
Developer and Reviewer remain separate agent instances even with the same model.

Verify model availability when launching agents. Report requested model/effort
separately from actual runtime attestation; unavailable or unobservable values are
UNAVAILABLE or UNVERIFIED. Do not silently substitute models or claim successful
model selection. If the requested Git model is unavailable, the coordinator can
perform routine Git work and disclose that delegation did not occur.

Activate the Git agent for a bounded publication batch after implementation and
required review have finished. Give it only the contract/reference, repository,
branch, base/approved implementation SHA, exact file allowlist, permitted operations,
required CI/review gates and concise evidence links. Do not copy the entire development
conversation or ask it to redo code review. Small isolated Git commands can stay
with the coordinator. Delegation does not establish measured token/quota savings.

Only one agent may mutate Git state at a time. Developer freezes the approved
source/test bytes before Git handoff; Reviewer never edits them. The Git agent may
commit those approved changes but must not repair code, weaken tests, change scope
or approve its own merge. Follow `skills/safe-git/SKILL.md`; preserve unrelated work,
use no force push/history rewrite, and stage only the supplied allowlist. On conflicts,
unexpected HEAD/file changes, authentication failures or failing CI, stop that
operation and return concise evidence to the coordinator. Do not retry indefinitely.

Merge requires existing user authorization and every contract-required gate,
including external review and applicable Mac verification. Green CI alone is
insufficient. Recheck branch/target SHA before publication or merge; a changed SHA
requires coordinator assessment of affected evidence. Report final commit/PR/CI
identity once; avoid report-only commits made solely to embed their own SHA.

## PR #49 B5 topology amendment

For the authorized synthetic containment slice, use the versioned
[B5 self-anchor amendment](../../reports/llmtool_pr49_b5_self_anchor_amendment_20261008.md)
with the historical B5 spec. It records the supervisor-owned group, direct-child
parent anchor, self-group last act and retained operator-wait obligations.
PR #49 synthetic-only containment is DONE / squash-merged as `eb464628dd6096811c659cfdf663f364107a6ef4`,
with independent Security/Functional QA and native Mac evidence. Its historical
Draft/verification gates are closed for this synthetic slice only.
A11b.2.2.2 **F1 private disposable-fixture-only Git ADD** subsequently
passed independent Security/Functional QA and was merged via
[PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84).
F2 real-repository mutation remains NOT AUTHORIZED; [Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70)
retains the historical integration design and deferred controls.


### S1b Product A implementation boundary — B still required

[Accepted A/B ordered handoff](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6127970737)
adds F2 untrusted packet DATA isolation, F3 independently host-pinned pair
approval and F4 stable size errors to the merged contract. A's public review
command has no production host/issuer/provider wiring or fake flag; valid ready
inputs collect LOCAL_FACTS_ONLY then BLOCKED with B's missing gates. Actual
Session/registered inventory/writer/CI execution authentication stays unverified
without B; caller/env/PID values never supply it. The collector reads fixed
local facts and exact-commit originals with A5 hardening, shared read/time/output
budget support; it fetches no remote data or refs. B must supply the separately
approved GitHub route and original actual check provenance. No selected URL or
caller-selected executable becomes a collection command.

The private `_run_fake_review` fixture seam is synthetic-only: fake observer,
immutable PairApproval from the test host (never expect/events), one-use fake
consumer and fake provider. It has no production attestation meaning, journal
or approval store. Parsed APPROVE cannot automatically append final events or
satisfy actual final tests/review. Packet/grant/source/clock drift is rejected
before/after one fake call; unknown output/usage stays explicit. Source data
collection includes complete base/head originals, stable byte hashes and full
changed-path diff; no binary/submodule or silent omission. Hard size overflow
uses REVIEW_PACKET_TOO_LARGE plus component/limit and scoped-PR split guidance.
Fixed trusted instructions are separate from canonical escaped untrusted JSON
DATA; tests cover injected diff/source/evidence/CI strings and hostile verdicts,
without a universal model-resistance claim.

A acceptance is **fake-only**, exact-head CI plus non-author Security/Functional
review and applicable Mac read/file evidence. B remains a named Coordinator-owned
contract checkpoint before final A acceptance, then separate B source/native/
production replay gates and explicitly authorized real Claude smoke before
combined enablement. No production host or route authority is supplied here;
no B absence may be hidden behind fake success. No S2/F2/automatic repair/Git
write/merge, provider fallback, daemon/scheduler/second approval/logging stack,
company benchmark or G5/G6 repeat. Intermediate historical evidence keeps its
original scope/status and cannot renew current live readiness.
