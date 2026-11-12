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

### D-007 independent mobile Codex Cloud delivery

This separate lane is **selected by Coordinator or Claude PM**, and the
owner starts/continues the Cloud task from ChatGPT mobile. It does not require
the local Mac, llmtool runtime or VPN and does not prove automatic Cloud dispatch.

1. **Scope first:** assign a unique independent task, source/base SHA, allowed
   files, acceptance tests and owner; check overlap against local and other
   Cloud work packages before coding. Do not place Mac-native, private VPN,
   Keychain or unpushed local-state work on this lane as Cloud-only acceptance.
2. **Environment:** use the original task's already-configured Cloud checkout.
   A Cloud environment must first be created/published on web/desktop and
   GitHub repository access must actually work. Do not assume /workspace is
   a checkout, add another project clone or copy Mac credentials into Cloud.
3. **Deliverable:** Cloud develops, executes available tests, **self-pre-reviews**
   and fixes within scope. With verified GitHub rights it publishes **only its
   own feature branch/Draft PR**, never main; if publication fails, retain the
   task/link/diff and report BLOCKED rather than inventing a remote SHA.
4. **Integrator pickup (2026-10-18 disposition):** the 2026-10-17 mobile
   lane specified an owner completion handoff. The Claude PM now
   picks up the completed PR/task through actually available, authorized
   CLI/GitHub workflows. Owner link sharing remains an optional fallback,
   not a required completion relay. If remote launch is unavailable, record
   the concrete task and limitation; a GitHub comment does not prove that
   Cloud has started.
   Claude PM checks original task, actual base/head SHA, full diff, conflicts,
   CI/test provenance and risks, then obtains **separate non-author**
   Security/Functional/QA and native Mac evidence when required. Cloud's
   self-review is not formal independent QA or authorization to merge.
5. **Evidence:** preserve Cloud task ID, branch/remote, base/head SHA, PR link,
   changed files, actual test commands and exit codes, self-review findings,
   untested Mac/Live requirements as NOT_TESTED, and unknown token/cost as
   UNKNOWN. Keep GitHub branch ownership isolated, never force-push peer refs
   or auto-merge main.

This **does not** activate llmtool F2 Git mutation, the native M2 autonomous
cycle, Integrator wake/resume, or a second scheduler/daemon. Follow
[safe-git](../../skills/safe-git/SKILL.md) and
[ai-team-delivery](../../skills/ai-team-delivery/SKILL.md). Cloud setup:
https://help.openai.com/en/articles/20001545-using-codex-cloud.

### Complete-work-package execution and local reviewer handoff (2026-10-14)

Apply the [normative work-package policy](../../skills/ai-team-delivery/SKILL.md#work-package-delivery-and-in-session-reviewer-subagents-owner-decision-2026-10-14) and [Owner decision](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6155769235). One already-authorized Developer CLI owns a complete deliverable: plan and safe-git preflight, scoped implementation, targeted tests, a **read-only in-session Reviewer child** if supported, bounded repair/recheck and one consolidated Draft PR/evidence packet. Avoid separately asking the owner to advance each microstep.

**HISTORICAL local-reviewer rule:** The former same-provider Reviewer-child assignments for Codex Developer and Claude Developer sessions are historical. Any current Developer may use a distinct read-only child only when its CLI supports one and effective restrictive permissions are verified; the child never edits, commits, fetches, creates worktrees or acts as a second writer. A local same-provider child is **pre-review only**. Obtain non-author independent Security/Functional/QA **once per complete candidate** and bounded rereview only when affected code changes. Issue #92 budgets and required native/exact-head release gates remain binding.

Batch deterministic worktree/branch/baseline state checks where permitted, but do not skip real independently accepted C3 old-final/new-start baselines, Git shared-ref serialization, matching release, TaskContract allowlists or meaningful STOP. Preserve the task-final candidate at published HEAD so the next handoff does not mistakenly compare post-commit source to task-start baseline. No current proof exists that GitHub messages awaken Mac CLIs or that a cross-provider listener is installed. If a physical operator action is unavoidable, give one action for a work package; report an A11c capability gap rather than claim impossible unattended progress.

**VPN transition:** Before a real company LLM call remind the owner: 「接下來要使用公司 LLM，請先連上公司 VPN。」 Corporate VPN may block external Codex Hooks documentation or GitHub; batch permitted public-doc checks outside VPN-only phases where practical and never equate a blocked request with an absent API. VPN connectivity is not provider-egress authorization; the [Issue #66 privacy gate](https://github.com/davidcatcat/davidcattoolsproject/issues/66) remains in force. [VPN record](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6155066742).

### Main AI delegation and per-work-package Reviewer lifecycle

The main AI owns task understanding/decomposition, explicit Worker/provider
selection, verification and adoption. llmtool owns only authorized invocation,
policy/data checks, bounded lifecycle/state, evidence and handoff. It must not
add an AI Planner, intelligent router or model-capability scoring framework.
Company LLM selection is explicit and retains its route-specific permissions.

Prefer one foreground, no-tools, isolated Reviewer invocation per work package
to concurrent resume/background control of an existing CLI. A fresh invocation
must bind its host-owned nonce/PID/process group, actual CLI runtime identity,
exact task/source/packet, approved executable/model/profile and one-call grant.
A CLI-reported new runtime Session ID is not original Session081de22e and is
not inferred from inner model text or authenticated by that report. Never overwrite existing request/EXPECT identities.
Keep PR163/164 manual protocols unchanged until a separately accepted extension.
The main AI consumes structured findings; llmtool does not authorize their adoption
or execute repairs. Formal Source approval, transport success and action grants
remain distinct; reported review output is DATA and execution stays STOP.

The initial implementation uses fake transport and temporary fixture authority;
it must preserve unavailable public factories and existing receipt-store kinds.
A real one-call trial additionally needs independently accepted route/data/cost,
Host/executable/identity and replay/budget pins. No AI-created grant DATA alone
supplies live permission. Test actual owned exit, timeout/cancel, bounded cleanup,
blocked stdout, malformed/truncated output, wrong identity/SHA and UNKNOWN with
no retry/fallback/resume. Stop only owned processes, preserve uncertain evidence,
and never start a new scheduler, daemon or supervisor. Do not use --bare by
assumption: it changes authentication; verify installed flags without upgrading.
Record all actual model calls, unknown costs and human relays. No fake-only test
or first-turn launch proves full M2 or native Host/OS isolation.

#### Foreground Worker Source/Fake contract (Issue #120)

The trusted `compose_host_services` seam can select the explicit
`llmtool-worker/claude-cli` role. `ProviderRoutePolicy.reviewer_role` is mandatory
for new construction; only old Host-file decoding fills the absent legacy
`llmtool-review/claude-cli` value. Both role crossings deny before spawn. Worker
Host registration additionally pins version `2.1.301` or `2.1.303` and executable SHA256;
its physical executable/auth-home/version pins form `worker_runtime_digest` in
that role's route policy. The existing call `route_digest`, issuer-held Host
file digest, unpredictable pre-admission launch nonce and consumed receipt bind
these facts without changing the frozen ReviewCallBinding field set or ledger
schema. A changed executable, revoked route, source, nonce or expired binding
cannot reuse that grant. Public factories remain unavailable.

Trusted host code can now call
`run_foreground_review(issuer, ledger, budget=budget)` in `review_authority`
(or `dispatch_review_handoff(issuer, ledger, budget=budget)` in `review_handoff`
for a structured `ReviewHandoffReceipt` with `PASS`/`FAIL`/`BLOCKED` and
`execution_status` `SUCCEEDED`/`FAILED`/`BLOCKED`/`UNKNOWN` without interactive
terminal paste or manual `Enter`).
It composes the actual HostServices and calls the existing `run_review` using
only `issuer.context`; it accepts no second contract or caller-supplied services.
Only the Worker role is accepted, before receipt consumption or spawn. This
host-only entry does not install Host registration/authentication or activate a
CLI/public factory. The host must already hold independently permitted pins,
context, route and a PROVIDER_REVIEW ledger; grant DATA is not permission.

An issuer-local atomic fence admits one attempt, including failed composition,
invalid budget and cancellation; it is never reset. A new issuer remains subject
to the existing ledger's replay/cap checks. The borrowed ledger stays open.
Read-only `issuer.foreground_services` and `issuer.pending_job` retain the real
launcher before invocation, including UNKNOWN, SystemExit and BaseException.
The host must preserve these handles and uncertain evidence; inspecting them
does not authorize cleanup of other processes or another attempt. No automatic
retry, fallback, resume or global cleanup is added.

The entry captures its original absolute monotonic deadline (<=180s), charges
composition time, and passes a reduced budget to the existing review flow. The
retained Worker launcher also caps its deadline by that original absolute value,
so a delayed handoff cannot renew time. Expiry is checked immediately before
invocation and after result normalization/finalization. Existing call/window and
cleanup limits remain inside the deadline. Late results fail closed while actual
attempt/receipt accounting and pending ownership survive. Returned dual decisions
remain WORKER_DATA_ONLY, authorized=false and STOP_NO_EXECUTION_AUTHORITY.
Temporary synthetic executable/Host/ledger E2E tests exercise this production
composition (test_only=false on its real components); that flag is not Live
evidence. Runtime stays NOT_VERIFIED and production handoff NOT_DELIVERED.

The opt-in provider invocation is foreground `--print --output-format json
--no-session-persistence --safe-mode --tools "WebSearch,WebFetch" --model <pinned model>`;
only WebSearch and WebFetch are allowed. Shell remains disabled, with isolated
empty cwd, sanitized environment and pinned auth HOME.
The syntax basis is the previously inspected installed **2.1.301 help** (with
installed **2.1.303** version/telemetry codec fixture support), plus
Anthropic's [CLI reference](https://code.claude.com/docs/en/cli-reference) and
[SDKResultMessage reference](https://platform.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage).
These references are schema/design input (codec fixture, **not installed live protocol/ABI proof**).
The supported fixture codec accepts exactly one bounded `type=result`,
`subtype=success`, `is_error=false`, canonical UUID `session_id`, JSON-string
`result`. Optional keys are `usage`, `duration_ms`, `duration_api_ms`,
`num_turns`, `total_cost_usd`, `stop_reason`, `uuid`, empty
`permission_denials`, bounded/ignored `modelUsage`, and documented 2.1.303
result telemetry fields (`api_error_status`, `terminal_reason`, `fast_mode_state`,
`fast_mode_disabled_reason`, `ttft_ms`, `ttft_stream_ms`, `time_to_request_ms`,
`first_content_frame_ms`, `queued_turn_count`, `result_index`, `safety_stops`,
`subagent_stats`). Unknown fields, duplicate
keys, malformed/error/nonterminal/multiple output, private/sensitive decoded
strings and invalid Unicode fail closed. Outer UTF-8 is at most128KiB; the
unchanged strict inner verdict is at most64KiB with both decisions separately
validated. Optional unsupported/incomplete usage is UNKNOWN; accepted counts
are CLI_REPORTED, never measured cost. No total cost, raw stderr or CLI envelope
is reflected into public diagnostics.

The UUID is **CLI_REPORTED_RUNTIME_ID under a pinned executable**, not
cryptographic authentication, model self-attestation, or original Claude081.
Host nonce, actual owned PID/PGID, runtime-pin digest, actual exit/reap,
stream drain/close and isolated-directory close are recorded separately. Author,
original081 and registered EXPECT Session UUID reuse is rejected. Existing
manual Bundle/Request/Return identities stay intact. Valid Worker dual decisions
remain DATA, `authorized=false`, `STOP_NO_EXECUTION_AUTHORITY`; they do not replace
original independent Source Review or authorize repair/tests/Git/merge.

One monotonic call deadline is capped by the remaining <=180s run, <=120s call
and current grant/route windows. Work uses 80% of the remaining call window,
with cleanup reserve at most20s **inside** that deadline. Worker collection uses
nonblocking unbuffered pipes and bounded wait; all its exit paths use the new
opt-in `bounded_terminate`, never generic unbounded terminate/wait. Only a
lifecycle-owned, unreaped child group may be signalled; confirmed reaping prevents
stale PGID signalling. Default generic CLI lifecycle/text behavior is unchanged.
Unknown exit/drain/close leaves pending ownership/evidence and EFFECT_UNKNOWN,
counts the attempt and forbids resend. Unexpected runtime-directory contents
are preserved, not recursively cleaned; success requires an empty removed
runtime directory. OS/filesystem stalls are not an OS-enforced hard-real-time
isolation guarantee. At most one wrapper launch is observable; underlying
model requests/internal retries/prefetch and missing billing remain UNKNOWN.

Source/Fake acceptance does **not** authorize REVIEWER_JOB_TRIAL, real Host
registration/ledger creation, authentication setup, credentials copying, live
Claude/model/provider calls or original Session resume. A future explicit trial
grant must pin a dedicated reviewer_auth_home distinct from Owner primary HOME,
packet/data/context/route/model/executable digest/version, UTC window, cost
uncertainty, one-use receipt, cleanup/cancel/no-resend evidence and useful main-AI
adoption. Native availability, original Reviewer auto-wake, relay reduction and
full M2 are NOT_VERIFIED. #153 polling and #161 UX remain HOLD; Company Live,
Native, T1, F2/S2, A11b and Owner Full Access/never are unchanged.

### Supervised self-hosting dogfood (M1)

M1a/M1b/M2 map to [A11c.1 delivery stages](../../docs/history/llmtool-roadmap-archive-20261028.md#supervised-self-hosting-delivery-within-a11c1-m1).
They do not replace S1a/S1b/S2 or waive F2/Host/provider gates.
Follow the existing [team policy](../../skills/ai-team-delivery/SKILL.md) and
[safe-git](../../skills/safe-git/SKILL.md); Owner decisions in Issue #120 remain
traceable evidence rather than model-generated authority.

1. Choose one real task and its exact source/evidence scope. Prefer deterministic
   local facts first, then one permitted foreground advisory request. Reuse
   existing `ask`, `review-diff`, `test-excerpts` or A9 capabilities; do not label
   direct Codex development as an llmtool invocation. A partial source packet is
   explicitly a limited-context advisory, not a complete independent review.
2. Pin the actual execution root, runtime revision/import, provider profile/CLI
   and current data/cost permission. An older trusted runtime may be reported
   honestly when it already provides the needed functionality; do not claim it
   is current main. Any required synchronization follows CLAIM/RELEASE. Do not
   alter global settings, create a project copy or borrow a peer's venv.
3. Use a bounded, screened public-source or synthetic packet within the granted
   route. `review-diff` input validation is not a secret/export scanner; neither
   a public repository nor `test-excerpts` authorizes every data export. Keep raw
   private logs, credentials, company data and existing session context out.
4. For the initial pilot, make at most one admitted provider request, with a
   fixed timeout, actual owned-child exit and no automatic retry/fallback.
   Preserve pre-dispatch configuration failures separately; a correction with
   proven zero requests is not an unknown-effect request resend. Record every
   CLI invocation and actual effect rather than hiding failed attempts.
5. Verify the reply against complete relevant source and deterministic evidence
   before adoption. Limited context can omit earlier validation. Record valid,
   invalid and unconfirmed claims, including recommendations you modify or reject.
   Keep advisory authority NONE; do not execute generated commands automatically.
6. M1a requires a useful real-task artifact and its adoption/disposition record.
   M1b additionally requires the assigned Developer's real source/test change,
   before/after test evidence, commit/PR and independent review. The original
   worktree/task gates still apply; no source writes before a required new C3.
7. Record command/provider, runtime/task SHA, input/output digests, exit, elapsed,
   cleanup, actual model calls, privacy/preservation and artifact use. CLI usage
   unavailable means UNKNOWN, not zero or a savings percentage. Let AI record
   observed Owner relays, initial authorization and physical operations separately;
   do not ask the Owner to add bookkeeping markers or reconstruct old relays.

An active Developer's foreground request does not require idle-session wake.
This may reduce a courier step but is not proof of M2: the full independently
reviewed development/test/repair/Draft-PR cycle must meet its own applicable
permissions, budget, deduplication and Native acceptance. Model output and
GitHub markers never confer execution authority. Do not enable #153 polling,
a new bridge/daemon, Company Live, real Step-0 or Gate B through this workflow.
Historical CLI/company smoke and L01 benchmark evidence keep their original
scope; they do not establish a current real-task result or token saving.

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
Direct Owner escalation follows the [Owner escalation reference](../../skills/ai-team-delivery/owner-escalation.md): an incorrect or changed product requirement, accounts, passwords, credentials or keys, or a macOS core/system change. AI never uses `sudo`, `su` or root. Provider egress and spending still require distinct pre-existing authorization.

## Execution environment

Use Codex Cloud when selected by the user for repository investigation, development,
documentation, review, offline tests, builds and CI. Use the actual Mac for verification
that depends on its VPN, credentials, files, operating system or running services.
Cloud is a separate computer; it does not inherit the Mac's sessions or network.

| Work / verification | Environment | Evidence |
|---|---|---|
| Source/docs changes, review, unit tests, fake-runtime/mock integration, lint/build | Configured Cloud checkout; local Mac also supported | Exact SHA, commands, exits, results |
| GitHub branch, push, PR, CI | Cloud when access is available | Remote branch SHA, PR URL, CI run SHA/status |
| Company LLM `192.0.2.10:4000`, model `gw`, VPN coexistence | Actual Mac with company VPN | Route/reachability, auth if applicable, exact live marker |
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
Owner D6 removes the two-fixed-root cap. Authorized Developers may create
independent, registered task-specific linked worktrees, with a unique branch,
one writer, verified task/Session identity and dedicated runtime, following
the [normative safe-git D6 policy](../../skills/safe-git/SKILL.md).
Integrator serializes shared ref changes and canonical integration. Each
worktree is retired automatically after merged/accepted task completion
(or documented cancelled/rejected disposition), **only** after checking
current PR/task state, no live Agent/tests, no unpublished work or unknown
tracked/untracked/ignored files, and evidence retained off-tree. Open PR,
repair or unknown/interrupted state is not deletable by default. No force
deletion; preserve on uncertainty. Do not repeat historic G1-G7 pilot per
task. Canonical native F1 acceptance remains canonical-only; D6 grants
neither llmtool F2 mutation nor auto-merge, provider egress or new controllers.
Mac automation is NOT LIVE VERIFIED merely by merging these docs.
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
The [roadmap](../../docs/history/llmtool-roadmap-archive-20261028.md#architecture-refinement--planning-only) assigns runtime
Task Contract enforcement/execution gates to A11b and route-by-task integration,
independent verification and bounded repair to A11c. Context budgets and cost
routing require later design. The separately gated S1a preparation exception below
does not authorize production integration or introduce another milestone.

### S1a offline task event inspection contract

The Coordinator-adopted [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)
split and [roadmap](../../docs/history/llmtool-roadmap-archive-20261028.md#issue-120-staged-preparation--s1a-then-s1b-then-s2)
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
S1a does not imply A11b DONE or full A11c acceptance.
No daemon/scheduler or duplicate approval/logging
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
real-agent/repair acceptance. Keep required missing gates blocking and use Issue #92
for nonblocking Low dispositions.

### S1b foreground review contract (DRAFT)

**STATUS: contract accepted/merged via [PR #129](https://github.com/davidcatcat/davidcattoolsproject/pull/129)
at `8468164d5503953f941b24c1f2318d5decda42b5`; S1b Product A is an accepted/merged fake-only slice via PR #131; B1 fake-first source is MERGED via PR #133 with independent product review.
T1 native release is HOLD_REPRIORITIZE. Real Claude remains BLOCKED;
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

### A11c.1 Cross-CLI handoff adapter proposal (Issue #120)

**DESIGN ONLY / IMPLEMENTATION NOT AUTHORIZED by this document.** This is the
next read-only S1b preparation boundary within S1a -> S1b -> S2, not a new
milestone, dispatcher or approval system. Public
`task review` remains unavailable for production. Existing B1 source merged
via PR #133; T1 additional native measurement is HOLD_REPRIORITIZE, and
production Host/ledger installation, real-provider T2/T3 and S2/F2 remain gated.
No held T1 helper or trial is resumed by this proposal.

#### Evidence and delivery order

| Evidence | Established scope | Still unverified |
| --- | --- | --- |
| PR #141 and PR #143 | GitHub records merged workflow docs and exact-source independent Security/Functional approval plus CI for C1b.0 | A docs/source merge is not automatic CLI dispatch or privacy-egress permission |
| Claude Developer background Bash / 60-second GitHub polling / task-notification | Developer report describes review-triggered recovery and worker exit | Independent native process/notification/exit evidence; no assertion that Codex has the same notification mechanism |
| Codex 0.162.1 probe in [#6163519573](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6163519573) | Real yielded command, bounded GitHub read, duplicate-HEAD skip and one fixed-UUID queue accepted by an already running daemon | No general listener or idle polling service installed |
| Subsequent same-session synthetic marker | Local original-session record: marker user message at 2026-10-14T07:46:58.582Z and acknowledgement at 07:47:04.279Z; initial report predates delivery | Complete event-to-review without human intervention, closed-TUI recovery and cross-CLI repair loop; queue acceptance alone never proves these |

Implement in this order after a separately pinned source task is authorized:
(1) deterministic admission/mapping and fake transport/clock tests;
(2) independently reviewed Codex transport/host-observation extension of S1b;
(3) separately authorized native synthetic read-only handoff with real owned
notification/exit evidence; (4) production handoff only after applicable
Host/route/privacy/native gates. Programmatic repair/execution stays S2 after
A11b approval, containment, recovery/audit and separate F2 authority. Existing
human-authorized Developer fixes are engineering work under their own scoped
task grant and safe-git, not adapter permission to mutate.

The B contract currently launches a fresh isolated Claude text/no-tools child.
A future Codex Reviewer transport would be a separate host/session/tool boundary;
its launch provenance, restrictive tools, route, packet-export policy and
independence would require separate review before wiring. Under current authority
(AGENTS.md *Formal Reviewer*, 2026-10-25), a Codex Reviewer is the formal Reviewer
only for Claude Developer and Claude PM changes, and Claude for Codex Developer
changes; any other review is advisory. Do not select a reviewer through caller JSON or infer a
provider grant from CLI support. This docs task performs no model launch.

#### Minimal adapter: existing events and one finite worker

Keep TaskContract, TaskBinding, TaskEvent, HandoffRecord, replay and S1b
PairApproval/ReviewCallBinding/collector/packet/verdict semantics. Do not add
new core states, change their schema or create a second event/approval store.
READY_FOR_REVIEW is a transport cue for an already validated readiness
snapshot: structural SUCCESS **and** reported_state WAITING_REVIEW, latest
valid independent-reviewer handoff and actual required pre-review evidence.
It is not a new TaskState or a substitute for those conjunctions.
Use externally approved paired contracts and expected bindings; never derive
expected identity, scope, SHA or authority from the notification itself.

One task-scoped foreground owner may retain a finite deterministic child that
checks the approved GitHub/event reference every 60 seconds. It calls no model
while waiting; zero model invocations is the measurable oracle, with token
usage UNKNOWN unless actually reported. It does not keep an agent reasoning
turn/Goal alive, start a daemon, install a LaunchAgent or poll all tasks.
A proposed default waiting budget is 10 minutes, including reads/notification;
the separately approved host/task grant and existing budget/clock seams must
pin it and bounded per-read time/output limits; TaskContract schema is unchanged.
Expiry, transport error or ambiguous effect means STOP with preserved
evidence, not automatic retry/fallback or another worker.

Before notification **and** before consumption, verify this complete tuple:
repository, paired task IDs and contract digests/version, run/epoch/sequence,
exact source HEAD/base/tree and packet digest, separately registered author
agent/Session/root/common-dir/branch/venv, distinct non-author Reviewer
agent/Session/root/role, current host grant/data route, TTL and preflight.
Trusted host observations supply actual process/session/root/tool identity;
PID, env UUID, GitHub login, caller --expect and events alone cannot do so.
Both Developer and Reviewer must remain in their existing registered roots,
with one owner per root and serialized shared-ref windows.

GitHub carries bounded untrusted DATA/reference only. An independently pinned
task grant specifies authorized event source/actor, exact decision/reference
and actions; a same-account login is not independent authorship proof.
Arbitrary comments, prompt injection and a model APPROVE do not dispatch,
grant a route, renew TTL or authorize merge. Do not interpolate body text into
shell/argv or agent instructions. Keep fixed trusted review instructions
separate from the complete allowlisted exact-SHA packet and its original
evidence; retain S1b packet/verdict size and privacy limits.

For Codex prefer one `codex queue --thread <fixed-UUID> --message <fixed-reference>`
through the **verified existing** shared server/explicit local endpoint,
without profile/sandbox overrides. Do not use --last, fork, new worktree,
daemon auto-start, TUI keystroke injection or concurrent exec-resume.
CLI help also exposes `codex exec resume <Session-ID>`; it is only a separately
reviewed inactive-client fallback candidate, not a verified original-TUI wake.
Missing/unobservable Session or unsupported queue is BLOCKED.

Queue acceptance is not reviewer execution. Correlate accepted message ID,
original-session delivery, actual turn/start/result, exact-source observation
and owned worker/child exit. Parse the existing versioned verdict: transport
PASS means both Security and Functional APPROVE with valid bound evidence;
transport CHANGES_REQUESTED maps to existing CHANGES_REQUIRED. BLOCKED,
timeout, exit0-only, invalid/partial result or missing post-observation never
becomes PASS or COMPLETED. Publication uses only the separately authorized
GitHub reporter; notification does not grant publication or merge rights.

The return notification to the original Claude Developer uses its evidenced
native task-notification when an owned finite watcher actually ends. Require
the actual same-session receipt and result binding, not an invented
`claude queue` command or a copied claim. If that interface is unavailable,
record BLOCKED without automatically creating a controller. Wake carries
findings as data; source repair still requires the separate Developer grant.

Reuse existing task sequence/state replay and one-use ReceiptLedger semantics
under reviewed host composition. For real notification effects, the source
contract must define a durable one-use key over task/run/epoch, contract pair,
source SHA, reviewer/scope, transport and event ID; atomically claim before
dispatch. No production ledger is installed here. Duplicate reviewed objects
skip regardless of repeated comments or new run IDs. A queue-success/ack-loss
crash is EFFECT_UNKNOWN: preserve the claimed key, reconcile trusted delivery
IDs, never blindly resend or bypass caps. Fake fixtures test this boundary.

A changed candidate invalidates the old final review evidence, gets a new
pinned binding/packet and focused affected-area review; old review is historical.
At most two unsuccessful repairs per confirmed root cause under Issue #92,
with pinned per-task total attempt/time budgets. No blanket repeated full
suite: focused tests during authorized repair and required exact-head/native
release checks at final candidate. Exhaustion returns STOP/NEEDS_DECISION.
PASS does not merge or declare DONE without final-task actual evidence.
On result, expiry or completion, the finite worker exits; cleanup touches only
verified owned handles. Unreaped/unknown child, failed cleanup or residual
worker makes the native trial fail and preserves evidence; no PID guessing,
peer-session kill or historical-process cleanup.

#### Fake-only source preparation: D1–D4 acceptance

The accepted [FAKE-A1 source contract](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6164823204)
permits only a private synchronous adapter and fixture-owned host/transport/claim
seams. It installs no worker, queue or notification ledger. `ReceiptLedger`
D1/provider layouts are unchanged; production handoff composition is unavailable.
Fake claims exercise CONSUMED -> EFFECT_DONE/EFFECT_UNKNOWN and retain reviewed
objects across adapter instances, including new comment/run IDs. A fake success
always retains runtime NOT_VERIFIED and authorized=false.

| Additional acceptance | Required fake oracle / native limitation |
| --- | --- |
| D1: CLI update or restart, same Session | Explicit externally pinned generation re-arm, fresh host observation and replay suppression; no automatic worker resurrection |
| D1: Session ID changed | BLOCKED under the old external binding; new registration cannot erase historical authorship or permit self-review |
| D2: reviewer turn and late result | Separate wait/reviewer/grant deadlines; earliest expiry wins. Late result is historical/UNKNOWN, without receipt, PASS or renewed TTL |
| D3: evidence grading | Fake fixtures and both CLI authors' own observations are not independently accepted native process/delivery evidence |
| D4: zero model calls | Independently composed fake host lifecycle counter/source/generation sampled before and after; missing, tampered or changed counter fails closed. Worker self-report is insufficient; token usage stays UNKNOWN |

The fake host/task grant can only shorten wait600s, poll60s, read5s and reviewer120s
limits. Callback elapsed times and UTC/monotonic agreement are checked; this is
cooperative simulated time, not an OS timeout or containment of arbitrary Python
callbacks. The adapter consumes at most128 bounded cues, with existing packet,
verdict, source, depth and privacy limits. Real notification, durable production
claim composition, native identity/tools and owned-process exit remain gated.

#### Codex Pickup B1 source boundary (default BLOCKED)

[Accepted B1 pins](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6166418985)
add typed Session/socket/route/Host pins and a private fixture transport. The
public `run_pickup` always returns NATIVE_PICKUP_NOT_AUTHORIZED without invoking
caller callbacks. No native queue/RPC client, issuer or Host installation is
shipped; a fixture marker, dataclass or `test_only` flag is not authentication.
Original reviewer physical checkout and immutable reviewed source remain distinct.

The same ReceiptLedger has a separate NOTIFICATION file layout, kind CHECK,
layout version and creation nonce. It never migrates D1/provider files or charges
provider days/caps. Cross-kind and cross-file nonce replay reject. The host must
pin a private path; there is no default path or production auto-create.

| Pin | Source/fixture acceptance |
| --- | --- |
| M1 | A single fresh observation proves exclusive reviewer idle AND empty queue before dispatch. An accepted undelivered message must have exact-ID withdrawal evidence after expiry; an acknowledgement alone leaves durable UNKNOWN/pending, prohibiting another trial |
| M1 cancellation race | Only this message's authoritative turn ID can be cancelled; completed turns are not cancelled. Missing or contradictory withdrawal/turn evidence preserves pending, never controller-exit-as-success |
| H1 | Denied composition/admission receives zero Host/transport/finally callbacks |
| H2 | Consumed but dispatch intent not begun can finish ZERO_EFFECT. Once dispatch intent commits, ambiguity stays UNKNOWN. Re-arm needs the exact prior terminal receipt and a different externally vouched grant; no automatic retry |
| H3 | Notification completion is separate from review verdict. BLOCKED is not reviewed; SKIP exposes a historical verdict and never becomes PASS. New blocked-review attempts require a fresh external grant |
| H4 | Generation re-arm must repin the whole host and grant; changing rearm fields alone cannot authenticate a new Session |
| H5 | Primary failure plus up to8 fixed failure codes retain finalize/cleanup/counter failures without raw exceptions |
| L1 | Delivery/turn/tool/author-receipt events come from externally pinned host event/rollout provenance, correlated to original message/turn/packet/result IDs. Reviewer prose is never evidence |
| L3 | NOTIFICATION metadata/nonce/layout isolation and unchanged D1/provider day/cap/recovery regressions |

QUEUE_ACCEPTED, SESSION_DELIVERED, TURN_STARTED, REVIEW_COMPLETED and
AUTHOR_RECEIPTED remain separate evidence dimensions. Only two strict APPROVE
verdicts produce fixture PASS, always runtime NOT_VERIFIED/authorized=false.
Fixed queue instructions carry the effective expires_at and an EXPIRED/no-packet
cooperative fallback; they do not prove prevention of late inference.

Private fake-child collection checks bounded output, simulated time and owned
exit without launching processes. This is not an OS timeout or native lifecycle
proof. Real dequeue/cancel/tool restriction/usage/context permission (L2/L4) remain
unverified; NATIVE_PICKUP_GRANT is NOT_AUTHORIZED. T1 HOLD and F2/S2 restrictions
remain unchanged. Unresolved pending records need separately
trusted reconciliation, never deletion, auto-retry or an invented supervisor.

#### Native Delta A1 source acceptance

[Accepted source pins](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6172165502)
fix Codex 0.162.1 protocol commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`.
Source/fake PASS does not grant Native execution. Public factories remain
unavailable; neither JSON, dataclass construction nor a private fixture issuer
constitutes production Host authentication.

| Pin | Required deterministic source behavior |
| --- | --- |
| P1 / Q1 | Match message OR bound turn; retain mapping-before-tool checks. Only UserMessage, AgentMessage, Reasoning and Plan are known non-tool. All other 12 release items, unknown items, subagents/other Threads and incomplete coverage fail closed |
| N2 | Complete add/list/UserMessage echoes, including JSON envelope/ASCII/non-BMP expansion, remain at most64KiB. Before-add conservative sizing rejects oversized input without dispatch. Packet256KiB is a maximum, not a guarantee of fitting an echoed response |
| N3 | Completed requires strict verdict, original terminal, full coverage and actual child exit; Failed/Interrupted are terminal without review completion. InProgress/unknown terminal/partial coverage or missing exit never clear pending |
| P3 | One notification followed by bounded async original-author receipt observation. Durable review and receipt are independent. Confirmed completed review without receipt is PARTIAL, never closed-loop PASS; no repeat review/notification. Late contradictory tools invalidate completion to UNKNOWN/pending |
| P5 | Fixed Host-owned worker-provider / Reviewer-turn / Claude-author domains retain identity/run/generation/entrypoint coverage. Worker delta0; legal Reviewer/author inference is separate. UNKNOWN usage is never zero or a hard cost cap |
| Layout | New explicitly selected NOTIFICATION v2 file only; preserve v1, D1 and provider schemas/UTC day/cap/recovery. No migration, default path or second store |
| P2/P4 | First fresh object/receipt/dispatch only. All retries/rearm/reconciliation remain STOP/PRESERVE, including expired/unconsumed/UNKNOWN attempts |

Overall600s, review120s, receipt120s and read/client5s caps can only shrink.
Delta fixtures reserve final20s inside overall expiry for cleanup; callbacks and
pages do not renew deadlines. Synthetic owned client tests use real nonblocking
pipes/EOF/actual wait and deadline-aware owned group termination, never the old
unbounded terminate.wait. A missing actual exit remains NOT_VERIFIED/UNKNOWN.
Private synthetic sockets are distinct from original Codex/Claude endpoints.
The existing collector's developer-root/main checks are unchanged; explicit
HostRecordPin observes static exact pinned metadata and returns digests only,
never claims fresh CLI ownership from a file, UUID, PID or caller declaration.

ENFORCED_NO_TOOLS remains an unproven native capability. AUDIT_ONLY_TRIAL is a
separate explicitly accepted risk mode, not prevention or OS isolation. Before
any Native Grant, N1 packet persistence in the Full Access Session/context, N4
experimental opt-in/client-local behavior, dual inference/context/data routes,
UNKNOWN cost and original Claude owned waiter/notification/ack evidence require
independent acceptance. No Session cloning, transcript/TTY injection, Host
installation, daemon, supervisor, T1/F2/S2 or automatic merge is authorized.
Full Access stays unchanged.

#### Native Composition A1 source/fake acceptance

[Accepted composition contract](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6174941122),
[final K1/K3 pins](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6175153094)
and [independent C3 acceptance](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6175585948)
authorize only the eight-file source/fake package. They do not grant live queue,
RPC, inference, Host installation or Native Trial. Existing public native paths
stay BLOCKED. No ledger migration, new controller/store or background worker.

| Invariant | Source/fake oracle |
| --- | --- |
| K1 | Only externally bound IN_PROCESS_ZERO_CHILD; spawn count0, attempts reject permanently; persistent stream actual drain and returned close plus original terminal coverage precede ledger child_exited=True. No invented PID or dummy child |
| K3 / L6 | One initialize/initialized connection, monotonic nonreused request ID and at most one in-flight request. Fragmented/interleaved responses/notifications remain bounded. Transport disconnect/error/timeout/flood/partial JSON poisons EventCoverage; no reconnect/reset/snapshot can restore it |
| L1 / L2 | Whole binding authenticates all external Host/Session/route/context/waiter/domain pins. Domain source, owner, run, generation and complete entrypoints match before/after. Original Codec IDs feed HostEvent and EventCoverage.seal; copied hex and caller prose are not coverage/authentication |
| R1 / R1a / P3 | Legitimate BLOCKED verdict may reach the author: REVIEW_COMPLETED NOT_VERIFIED, AUTHOR_RECEIPTED PASS. Invalidation demotes completion/receipt, preserving original queue/delivery/start evidence. Missing/rejected receipt is bounded PARTIAL; notify once, no resend/rearm/UNKNOWN recovery |
| L5 | Overall600s/review120s/receipt120s/RPC5s only shrink. All normal operations use overall minus20s; cleanup/final observation uses the original overall deadline. Callback return time cannot renew TTL |

The original Claude author's owned waiter is an interface with independently
pinned registration, task/run/SHA/notification/tool/generation/channel/TTL and
exact message/turn/verdict ACK. This package exercises a fake waiter, not a
production original-Session registration. Three Host-owned inference counters
remain separate; zero worker calls is not zero Reviewer/author inference or a
hard token cost cap. Audit-only is cooperative observation, not enforced
no-tools or OS isolation; Owner Full Access/never is unchanged.

[K2 release-pinned source evidence](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6175548413)
confirms APP_SERVER_EVENTS without resume cannot provide full item/turn events.
No independent thread/subscribe exists, and resume is prohibited. HOST_ROLLOUT
cannot supply item-start/approval/transient-error evidence. The separately bound
CR-K2-B source/fake mode below accepts completed-only rollout coverage with
explicit limitations. Native remains BLOCKED/NOT_VERIFIED; live structure-only
inspection and Native execution still require separate grants. P2/P4 remain one-shot STOP/PRESERVE; L3 possible-tool types
stay fail-closed. N1 context retention, N4 opt-in and dual inference/data routes
remain Native Grant conditions. T1 HOLD and F2/S2 remain ungranted.

#### CR-K2-B HOST_ROLLOUT source/fake acceptance

[Four-file contract](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6184402548),
[R1 final pins](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6184679109)
and [independent new C3 PASS](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6185208724)
authorize only native module/test plus README/WORKFLOW Source/Fake changes.
No transport/policy/ledger/provider/CLI/schema/CI changes or #153 automation.

| Required invariant | Synthetic acceptance |
| --- | --- |
| Admission | Physical sessions/YYYY/MM/DD plain JSONL, exact filename/thread/Session/Paginated/no-fork metadata, externally admitted LF cursor and nonzero next ordinal; pin digest bound through Session and composition/grant |
| Limits | Actual 64KiB LF line, 512KiB cumulative I/O (including partial re-reads/metadata), 128 records/128 polls; strict Unicode/JSON/depth/duplicate-key rejection; shared five-second normal deadline, close included |
| Writer codec | task_started/task_complete/turn_aborted; absent error succeeds, null rejects; PascalCase completed-only items; exact text input/client/item/turn, final_answer only, commentary never verdict; aliases/images/unknown/tool/fork/compaction fail sticky |
| Coverage | Contiguous ordinals from external pin for every record; separate rollout seal domain and record digests; no fabricated ItemStarted; Failed/Interrupted never review PASS |
| Ownership/cleanup | Identity checked before/after read, shrink/rename/replacement/hardlink/UID/unsafe physical parents reject; actual extra EOF snapshot/no partial and local close; available RPC drain/close without server EOF, zero spawn |
| Composition | Existing runner/NOTIFICATION ledger/verdict/author waiter/counter domains; invalid ack or swallowed callback failure poisons coverage; unresolved effects remain UNKNOWN/pending/no resend; public route stays BLOCKED |
| Privacy | Synthetic canaries absent from errors/evidence; no raw rollout/message/reasoning persistence or true Codex HOME reads |

Delivery order: Source/Fake tests and advisory review → one exact-HEAD Draft PR /
Python3.9+3.14 CI → original non-author Claude Security+Functional Source Review.
Only after accepted source may a separately authorized structure-only census
check actual Paginated/ordinal/layout/size/type/phase feasibility; it does not
start a turn and is not Gate B. Any allowlist widening needs new evidence and
independent acceptance. A separate Native grant is still required for queue/RPC,
model/context/data routing, unknown token cost, original Host/receipt/counters.

STOP/PRESERVE on identity/cursor/source/domain drift, missing coverage, unsafe
path, partial/missing terminal at deadline, or cleanup uncertainty. No auto
re-resolution, resume, retry/recovery framework or second store. A finite EOF
snapshot cannot prove future silence; invisible approval/transient errors/item
starts remain NOT_VERIFIED. R2–R6 LOW/LATER remain grouped rather than opening
new repair loops. Owner Full Access/never stays unchanged; AUDIT_ONLY is not
ENFORCED_NO_TOOLS. T1/F2/S2/#153 remain held/ungranted.

#### Acceptance matrix and dependencies

All rows below are **future tests / NOT_VERIFIED**, not results of this docs PR.
Use synthetic events/hosts/fake transports first; do not re-review PR #143 or
re-run company Round 1-3, G5/G6 or held T1/full legacy suites.

| Case | Required oracle |
| --- | --- |
| No event / complete waiting budget | Zero model/queue invocations; finite exit and no owned worker remains |
| Valid READY_FOR_REVIEW | Existing S1a readiness conjunction, actual identity and route admission; one exact-bound notification |
| Arbitrary comment / injected body / unapproved grant | No dispatch, tool effect or data export |
| Duplicate / replay / already-reviewed HEAD | SKIP with one-use evidence; no second model turn even after worker restart |
| Wrong task/run/epoch/sequence/Session/role/root/venv | BLOCKED before notification and before review |
| HEAD/base/packet drift or expired/revoked grant | STOP; old verdict cannot be reused or polling renew TTL |
| Queue accepted but missing delivery / crash after effect | UNKNOWN, no blind resend or false PASS |
| Native Codex pickup | Original registered Reviewer receives event, starts and completes one read-only exact-SHA review without owner prompt relay |
| Reviewer PASS / changes / malformed/partial/exit0-only | Strict bound Security+Functional verdict and actual evidence; false/missing success fails closed |
| Claude result wake | Original author Session receives exact verdict; recorded native notification, no copied-text-only proof |
| Repair with no mutation grant | Findings only; no source/test/Git write, no S2/F2 activation |
| Separately authorized bounded repair | Same author root/allowlist; changed SHA invalidates review; attempts/time exhaustion STOP |
| Completion/timeout/interruption/cleanup error | Worker exits with owned-child inventory/actual exit proof; unknown cleanup never PASS |
| Privacy and reviewer independence | Unauthorized source/log/secret/endpoint/Session export blocked; author/self-review rejected |

Real native pickup requires the reviewed Codex host/transport and current
data/call permission; closed-CLI recovery remains a separate NOT_VERIFIED
case. Real programmatic mutation requires S2/A11b/F2 gates. T1 remains
HOLD_REPRIORITIZE and applicable T1-dependent enablement remains blocked.
Existing queue/Claude wait probes do not waive these. Public production
factory stays unavailable until separately accepted enablement.

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
