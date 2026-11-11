# llmtool development workflow — Codex Cloud and local Mac

> Current collaboration, role and privacy authority is in [AGENTS.md](../../AGENTS.md); this file is used for Cloud handoff and Mac/Cloud verification.

## Current Developer and Reviewer routing (Owner decision 2026-10-21)

Codex and OpenCode are Developer routes assigned by the existing PM only after checking [worker-dispatch.json](worker-dispatch.json); a PAUSED switch forbids dispatch regardless of live health. Direct OpenCode work uses a registered D6 task worktree, task-local permissions and a free DeepSeek model. D4/G3/M2 is background and does not block direct Developer work. Company `gw` (home LLM) is a Developer route only through the API Agent Gateway (`gw-agent`): it edits files inside its Task scope with line-range reads; the `company-gw` direct and proposal routes are retired (Owner 2026-11-11, #120). Preserve per-task data screening, no secrets/private data, usage and bounded-repair evidence, independent review and exact-head CI.

The formal Reviewer is from the other model family than the Developer under [AGENTS.md](../../AGENTS.md) (Claude for Codex Developer changes, Codex for Claude Developer and PM changes); other reviews are advisory. Formal reviews remain exact-head, read-only and subject to all applicable security, CI and Mac-native gates. Owner dispatch switches are in [worker-dispatch.json](worker-dispatch.json); the local monitor at [scripts/llmtool_monitor.py](../../scripts/llmtool_monitor.py) reports live technical state separately. The PM's next-step duty after each result is the [PM Continuation Contract](../../skills/ai-team-delivery/pm-continuation.md). Company LLM unavailability is recorded and does not block unrelated Developers.

Declare read and write paths at task claude-bind, including an admitted test module in the read paths (an unchanged test is valid). Order: commit, --record-result-task-id, --bind-native-session-task-id, then task claude-bind.


[Historical role and stop decisions are archived](../../docs/history/governance-archive-20261103.md#historical-role-and-stop-decisions).

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

### S1b Production Enablement (B) contract (DRAFT)

**STATUS: B documentation contract ACCEPTED/MERGED via [PR #130](https://github.com/davidcatcat/davidcattoolsproject/pull/130)
at `cfa6c73e26cf6a16bc3d8a2303a45a4a21904559`, independent R1 Security/Functional
APPROVE. B1 authority adapters/fake-first source is MERGED via PR #133 with independent
Security/Functional approval; T1 additional native measurement is HOLD_REPRIORITIZE.
The real Claude provider route
NOT_AUTHORIZED/BLOCKED; production auto-handoff NOT DELIVERED.** This
concretizes work item **B** of the merged
[A/B ordered delivery contract](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6127970737)
and the [Claude v0 proposal](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6128195426)
under the Coordinator decisions recorded in Issue #120. It refines the
[S1b invocation authority](#invocation-authority-lifecycle-and-failure) section
and changes none of the S1b N1–N3, packet, verdict or limit rules above. Only
ROADMAP.md/WORKFLOW.md change here; merging it grants no code scope, ledger
creation, data route, provider call or smoke permission.

#### B1. Trust model and non-goals

Cooperative same-UID G7 trust applies. B does **not** claim an OS-level,
unforgeable Session identity, hostile-peer protection or power-loss durability.
Trust comes from three host-observable facts, never from caller JSON, `--expect`,
events, env UUID, PID, TTY, helper ancestry or model text:

| Question | Production evidence | Insufficient alone |
| --- | --- | --- |
| Who reviews, and is the reviewer independent? | **Host-owned reviewer launch**: the trusted host itself starts one fresh `claude-cli` text/no-tools child per admitted call through the existing provider and `CLIProcessLifecycle`; it records a random launch nonce, start time, owned-child kernel identity and the fixed logical reviewer role `llmtool-review/claude-cli`. Non-authorship holds by construction: no author Session, transcript or tool context is attached | Provider brand/model text; a reviewer Session ID supplied by the caller |
| Who authored the reviewed source? | **Registered author worktree** (Host Registration, B2) plus **actual Git observations**: which registered locked root currently has the owned branch checked out at the exact SHA, with clean tracked state and the registered baseline preserved | Commit author, events, PR author field |
| Did the reviewed source stay fixed? | Trusted host observations immediately before submission (no older than 5 s) and after the response: HEAD/index/clean tracked state, Git locks, writers, registration and packet digest | Developer CLI liveness/heartbeat (not required; heartbeat loss stays unknown, per N2) |

The reviewer agent ID and launch identity must differ from **every** registered
author. A pinned author whose registered root cannot be observed, an ambiguous
checkout (the branch in two roots), or any drift is BLOCKED. Same-UID code can
still edit host files; that limit is disclosed, not mitigated.

#### B2. Host Registration and Route Policy (static host file)

One Integrator-created JSON file, mode 0600, owner = current euid, regular,
single-link, no-follow, in a 0700 directory **outside every repository root,
worktree and common-dir** (proposed `~/.local/share/llmtool-host/`; final path
is a Coordinator pin). Version 1, strict keys, duplicate-key/depth/size limits
as S1b inputs, <=64 KiB. It holds only static policy; counters and state live
only in the ledger (B4). It is **not** a second approval store.

| Field group | Required content |
| --- | --- |
| `agents` | Each registered author: logical agent ID (`codex-dev`, `claude-dev`), physical root realpath + dev/inode, common-dir, origin URL, owned branch prefix, venv prefix |
| `reviewer` | Logical role `llmtool-review/claude-cli`; approved `claude` executable realpath; approved auth-home provenance (`LLMTOOL_CLAUDE_CLI_HOME` value or HOME) |
| `route_policies[]` | `ProviderRoutePolicy` (B3) entries, each with `revoked` boolean |
| `ledger` | Provider-ledger path, dev/inode and creation nonce (B4) |
| `issuer` | `capability_id` of the production issuer and the authorizing #120 comment URL |

The host file's SHA256 and each route-policy digest are published in Issue #120
when created or changed. A file that is missing, unsafe, unparsable, mismatched
with the published digest, or that names a root/ledger whose dev/inode differs is
BLOCKED with a fixed code; no auto-create, repair or re-pin. Creation, rotation
and revocation are explicit Integrator actions, never part of `task review`.

#### B3. Production Grant Issuer and Route Policy

`ProviderRoutePolicy` (immutable, domain-separated digest
`llmtool.provider-route-policy`): policy ID/version; provider `claude-cli`,
profile and exact model; allowed data classes; maximum packet bytes (<=256 KiB);
call timeout <=120 s and run <=180 s; one attempt, zero retry/fallback;
per-call and per-UTC-day call caps; `unknown_cost_accepted` (must be true for
the text route, else BLOCKED); validity window (`not_before`/`not_after` UTC);
authorizer reference.

**v1 data classes (Coordinator decision 4):** only approved source blobs and
the complete exact-SHA diff of this repository, plus the S1b packet's own
metadata (bindings, digests, check/CI references). **Denied:** company LLM logs
or outputs, company endpoints/profiles, secrets/auth files, private raw logs,
home paths, Session IDs and any Issue #66 excerpt not separately authorized.
A packet containing any other class is BLOCKED before admission.

`ProductionReviewIssuer` is the first production `HostIssuer` subclass, with a
**new** `IssuerClass.PRODUCTION_REVIEW` value. `vouches_for(digest)` is true
only when the host file loaded in this process lists that route-policy digest,
it is not revoked, and current trusted UTC is within its validity window. It is
constructed only by host code from the host file, never from model, JSON, env
or CLI input; no flag selects a fake issuer.

A **production review grant** is the ISSUED ledger receipt (B4) created from a
`decide_provider_review()` ALLOW. Its binding names: task ID, both paired
TaskBindings (preparation and final) with contract digests and criterion map;
run ID and epoch/sequence; source SHA; packet digest; pre-call observation
digest; author agent set; reviewer role and launch nonce; route-policy ID/
version/digest; provider/profile/model; export-scope digest (data classes +
path list digest); issuer class/capability ID; redeem run ID; UTC deadline.
`NEW_COST` and `NEW_DATA_EGRESS` are allowed **only** in this function and only
when covered by the vouched route policy; D1 `decide()` is unchanged and keeps
denying them. D1 fake ALLOW, G7 rights, S1a preview, a docs merge, a packet
digest, `--expect`/events or a model APPROVE are never production grants.

#### B4. Durable one-use replay protection and crash recovery

Reuse `authorization_ledger.ReceiptLedger` (Coordinator decision 3): **same
class, a separate provider-ledger file**, created once by the Integrator with
`ReceiptLedger.create()`. The D1 ledger file and D1 behavior are unchanged.
State machine and durability are the existing ones: `ISSUED -> CONSUMED ->
EFFECT_DONE | EFFECT_UNKNOWN` (or `DENIED`/`EXPIRED`); conditional UPDATE in
`BEGIN IMMEDIATE`; `synchronous=FULL`, `fullfsync` requested; durable clock
high-water; 0600/no-follow/dev-inode checks before and after the lock.

Required additional semantics (each needs the scope amendment in B6):

1. **Consume before effect:** CONSUMED is committed before the provider child
   is started; the provider call is the sink. No call without a committed
   CONSUMED row.
2. **One call per reviewed object:** at most one receipt ever per
   `(route-policy digest, source SHA, paired contract digests, packet digest)`,
   independent of run ID, so a new run cannot buy a second call on the same
   packet. A changed source/contract/packet is a new object needing a new
   decision.
3. **Redeem-time recheck:** inside the redeem transaction, re-verify that the
   route policy is still vouched (not revoked, within validity) and the UTC
   deadline has not passed; otherwise DENIED/EXPIRED with zero calls. This
   closes D1's documented "redeem does not recheck revocation" limit for the
   provider kind only.
4. **Daily cap by durable consume day:** the provider ledger stores, per
   receipt, a write-once `consumed_at` (trusted host UTC epoch seconds read
   under the write lock after the existing high-water check) and
   `consumed_day = floor(consumed_at / 86400)` (UTC day). Both are set in the
   **same** conditional UPDATE that moves ISSUED -> CONSUMED, in the same
   `BEGIN IMMEDIATE` transaction that first counts that route's rows with
   `consumed_day` equal to the current UTC day and status in
   {CONSUMED, EFFECT_DONE, EFFECT_UNKNOWN}; count >= cap -> NOT_ALLOWED,
   row stays ISSUED, zero calls. The day is never derived from `issued_at`,
   `expires_at`, the global clock high-water or a later status-update time.
   Finalize (`EFFECT_DONE`/`EFFECT_UNKNOWN`) and `recover()` change only the
   status, never `consumed_at`/`consumed_day`, so restart, recovery or late
   finalization after midnight keeps the charge on the consume day. A
   consumed-status row without both values, or with a day that disagrees
   with `consumed_at`, is LEDGER_CORRUPT (fail closed). Because the clock
   high-water is monotonic, consume days never move backward; host clock
   rollback below it is CLOCK_INVALID before any count or consume. Admission
   may pre-check the cap, but only the consume-time count is authoritative.
5. **Recovery:** `recover()` runs only at host start-up under the host's
   exclusive lock; every CONSUMED row becomes EFFECT_UNKNOWN. EFFECT_UNKNOWN
   and any uncertain provider outcome (timeout, kill, crash after spawn) count
   as **consumed**: no automatic retry, no "exactly-once" claim. A further
   attempt needs a new run ID and an explicit Coordinator decision.
6. **Ledger integrity:** a missing, replaced (dev/inode or creation nonce
   mismatch vs. B2), locked, corrupt or clock-rolled-back ledger is BLOCKED;
   re-creation is an explicit Integrator action re-published in #120.

Result labels for the provider kind are distinct from D1 (`FAKE_POLICY_ALLOWED`
and `FAKE_EFFECT_DONE` are never reused for a real call).

#### B5. Interface with Product A (freeze before B code)

Product A owns the foreground flow and its fake fixtures; B supplies only
production implementations behind four injection points. A defines these
protocols and the `ReviewCallBinding` pure data class in its own allowlisted
files and freezes them in its Draft PR; B maps them and does not edit A's flow.

```python
class ReviewAuthority(Protocol):
    def admit(self, binding: ReviewCallBinding) -> Admission: ...   # ISSUED grant or BLOCKED(code)
    def consume(self, admission: Admission) -> ConsumeToken: ...    # CONSUMED committed before call
    def finalize(self, token: ConsumeToken, outcome: CallOutcome) -> None: ...  # EFFECT_DONE / EFFECT_UNKNOWN
class ReviewerLauncher(Protocol):
    def launch(self, packet: bytes, token: ConsumeToken) -> LaunchResult: ...  # launch record + bounded raw text
class AuthorshipObserver(Protocol):
    def observe(self) -> Observation: ...   # A collector facts + B registration match
class Clock(Protocol):
    def utc(self) -> int: ...
    def monotonic(self) -> float: ...
```

| Rule | Requirement |
| --- | --- |
| Production default | A's factory returns `UnavailableReviewAuthority` -> `REVIEW_AUTHORITY_UNAVAILABLE`, zero provider calls, until B is accepted |
| Fakes | Fake authority/launcher/observer/clock exist only under `tools/llmtool/tests/`; no CLI flag, env or JSON selects them or upgrades fake to production |
| Ownership | A owns `task_review*.py`/`cli.py` flow and tests; B owns new `review_authority*.py` and the scope-amended shared modules; the only B edit inside A's files is the single factory binding, made after A merges |
| Change control | An interface mismatch is raised as an *interface decision request* in Issue #120; neither developer edits the other's branch or worktree |
| Docs overlap | A and B both may touch ROADMAP/WORKFLOW; B keeps its text in this subsection plus one ROADMAP row so a later rebase is mechanical |

#### B6. Proposed scope amendment for shared authorization code (not granted)

The heading/anchor retains the original docs proposal. The Coordinator's explicit
ISSUE120-S1B-B1 task instruction adopts the necessary-file amendment in the
[formal handoff](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6130032828)
for source/tests/Draft PR, including narrow A-R1–R4 integration fixes in A modules
and their tests. This task-specific authorization does not install Host policy,
create a production ledger or authorize provider/data egress. The B1 product
still requires non-author Security/Functional review. Original allowlist:

- **New:** `tools/llmtool/llmtool/review_authority.py` (host file loader,
  `ProviderRoutePolicy`, `ProductionReviewIssuer`, ledger adapter, launcher,
  observer match), `review_authority_cli.py` (read-only
  `llmtool review-authority check --json`; creates/changes nothing),
  `tests/test_review_authority.py`, `tests/test_review_authority_native.py`
  (Darwin-only; skipped elsewhere and never counted as PASS).
- **Amend `authorization_policy.py` (additive only):** `EffectKind.PROVIDER_REVIEW_CALL`,
  `IssuerClass.PRODUCTION_REVIEW`, `ProviderReviewBinding`, `ProviderObservedState`,
  `decide_provider_review()`. `decide()`, `ReceiptBinding` and existing labels unchanged.
- **Amend `authorization_ledger.py`:** accept a binding union (D1 `ReceiptBinding`
  or `ProviderReviewBinding`) with per-kind sink/observation match; B4 items 2–4
  apply to the provider kind only; D1 `FAKE_FILE_CREATE` path byte-for-byte
  equivalent in behavior.
- **Provider-only storage/schema amendment (explicit, R1):** the provider
  ledger file uses a **distinct provider layout**, not a reinterpretation of
  D1 schema v1. It keeps the D1 `clock` table and receipt status machine and
  adds `consumed_at INTEGER NULL`, `consumed_day INTEGER NULL`, the
  route-policy digest and the B4.2 reviewed-object key (UNIQUE), with CHECK
  constraints that consumed-status rows carry both consume values. The file
  records its layout identity as an explicit stored kind tag
  (`PROVIDER_REVIEW`) in addition to its version number; a numeric version
  alone never identifies the layout. Opening a D1 file as a provider ledger,
  or a provider file as a D1 ledger, is LEDGER_CORRUPT, fail closed. The D1
  schema v1, its existing files, `create()`/`open()` results for D1 and all
  D1 semantics stay unchanged; there is no D1 migration and no in-place
  conversion. Still one `ReceiptLedger` engine and no new approval store.
- **Tests:** add cases to `test_authorization_policy.py`/`test_authorization_ledger.py`;
  existing cases unchanged and must pass.
- **One line** in A's `task_review_cli.py` factory, and `cli.py` subcommand
  registration, after A merges.
- **Optional, separately approved:** `providers/claude_cli.py` JSON-usage mode
  (`--output-format json`) to report actual usage; without it usage/cost stay
  UNKNOWN.
- **Excluded:** diagnostics schema, TaskContract schema, Git hardening, F1/F2,
  any daemon/scheduler/second approval store.

#### B7. Threat model and required negative controls

| Threat | Control | Residual / disclosed |
| --- | --- | --- |
| Forged identity (`--expect`/events/env/PID claims) | Author from registration + actual Git observation; reviewer launched by host | Same-UID can edit host file |
| Self-review | Reviewer is a fresh host-launched child; agent ID differs from all authors | None beyond same-UID |
| Replay (same grant/packet, retry after timeout) | One receipt per reviewed object; ISSUED-only consume; EFFECT_UNKNOWN counts as consumed | None |
| Two-process race | `BEGIN IMMEDIATE` + conditional UPDATE; exactly one rowcount 1 | Requires native Mac proof |
| Crash windows | CONSUMED committed before spawn; `recover()` -> EFFECT_UNKNOWN; no retry | Possible paid call with lost result (accepted) |
| Clock rollback | Ledger high-water; monotonic used only within one process | Power-loss durability NOT_VERIFIED |
| Day-boundary undercount (R1) | Write-once `consumed_at`/`consumed_day` set atomically with CONSUMED; cap counted by consume day inside the consume transaction | Requires T0/T1 proof |
| Ledger deleted/replaced | Host-file dev/inode + creation nonce | Same-UID can change both |
| Revocation race | Redeem-time recheck in the consume transaction | None |
| Data egress | v1 data classes, existing privacy screen of packet and rendered prompt | Screen may miss content; class restriction limits blast radius |
| Credential leak | Existing minimal child env; no API key; registered auth-home provenance | None |
| Cost runaway | Per-call/day caps, `admit_next`, 120 s, no fallback | Text mode cannot cap spend; route must accept UNKNOWN cost |
| Prompt injection | A's F2 fixed host instructions + escaped packet DATA; APPROVE is evidence only | No universal LLM resistance (shadow mode, B8) |
| Executable swap | Approved `claude` realpath rechecked before/after launch | Same-UID TOCTOU disclosed |

#### B8. Acceptance stages, shadow review and cost/quality evaluation

| Stage | Content | Authority needed |
| --- | --- | --- |
| **T0 fake** (B PR, CI) | Fake provider + real ledger/host-file/issuer code: every B7 control, revocation between admit and consume, cap boundary, R1 consume-day cases (issue 23:59:30 then consume 23:59:40 vs. 00:00:20 charges the respective UTC day; cap N-1 at 23:59:59 plus consume at 00:00:00 admits on the new day; finalize or `recover()` after midnight keeps the consume day; missing/inconsistent consume values and D1/provider layout cross-open -> LEDGER_CORRUPT), binding/route/data-class mismatch, missing/unsafe host file, FAKE label isolation, D1 regression unchanged | Accepted scope amendment; exact-head 3.9/3.14 CI; non-author review |
| **T1 native Mac (fake provider)** | Real APFS ledger: two-process barrier race (one call, one REPLAY), two-process race at cap-1 across distinct receipts (exactly one CONSUMED, the other NOT_ALLOWED with zero calls), SIGKILL then restart/`recover()` keeps EFFECT_UNKNOWN counted on its consume day, clock rollback across midnight -> CLOCK_INVALID with no count or consume, SIGKILL after consume/before spawn and after response/before finalize -> EFFECT_UNKNOWN with no retry, ledger replace/delete/lock, clock rollback, owned-child cleanup | Independently reviewed bounded command on exact source; Linux CI cannot substitute |
| **T2 canary** | One real call: small public repo diff, no sensitive data; verify one consume, verdict parse, cleanup, second run REPLAY with zero calls | Coordinator single-use route approval after A+B exact-head gates |
| **T3 shadow** | >=5 real PRs reviewed by both the automatic no-tools review and the existing independent human-led review, plus seeded-defect fixtures (N1-style false SUCCESS, permission hole, prompt injection) | Coordinator enables per run |

**Shadow rule (Coordinator decision 6):** the automatic verdict is a
pre-review/triage signal only and never replaces the independent
Security/Functional APPROVE. Promotion is considered only with **0
false-APPROVE on seeded defects** and agreement with independent reviewers on
release blockers.

**Metrics (decision 7):** per PR, manual prompt-relay count and
request-to-verdict latency (primary benefit); provider calls, measured
input/output/cached tokens where reported, wall time, cap hits and
UNKNOWN-usage count. Missing usage is UNKNOWN, never 0; no token or cost saving
is claimed without paired, quality-equivalent measured evidence.

Production enablement is a later combined Coordinator decision requiring A
exact-head acceptance (fake-only), B contract and implementation acceptance,
T1 native evidence and an explicitly authorized T2. Until then S1b real-provider
route stays **NOT_AUTHORIZED/BLOCKED**. No F2/S2, Git mutation, automatic merge,
daemon or scheduler.

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
accepted via PR #129. Product A fake-only is accepted/merged via PR #131;
B1 authority adapters/fake-first source is MERGED via PR #133 with independent
product approval; T1 is HOLD_REPRIORITIZE and real provider BLOCKED pending B enablement;
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
| Codex Cloud advisory Reviewer | `gpt-6.1-sol` / high | Independently inspect requirements, source, diff and evidence; read-only pre-review only. This does not satisfy llmtool’s fixed Claude formal gate |
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


#### Product A frozen interface mapping — I-1–I-5

The [Coordinator interface disposition](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6128597185)
is implemented in A's allowed modules. `task_review.py` owns `ReviewCallBinding`,
`Admission`, `ConsumeToken`, `CallOutcome`, `LaunchResult`, `ReviewAuthority`,
`ReviewerLauncher`, `AuthorshipObserver` and `Clock`; `task_review_collector.py`
owns immutable `Observation`. The only shipped factory returns
UnavailableReviewAuthority; public ready input yields REVIEW_AUTHORITY_UNAVAILABLE
with zero calls, not a production grant. B later changes only the dedicated
factory import binding in task_review_cli.py under its separately pinned scope.

ReviewCallBinding includes all B3 fields: both original TaskBindings/digests,
criterion map, task/run/epoch/sequence, exact source/packet/pre-observation,
registered-author agent set, fixed reviewer role and preallocated nonce,
route ID/version/digest/provider/profile/model, export-scope digest over approved
data classes + path-list digest, issuer class/capability, redeem run and UTC
deadline. The nonce is host-allocated before admit without spawning; only the
trusted launch result may later attest matching actual kernel/launch provenance.
Types/strings alone never authenticate an issuer or Session.

`run_review` consumes four host services (`authority`, `launcher`, `observer`,
`clock`) plus pinned host context: `approval` (PairApproval), `packet_input`
(canonical selected bundle bytes), `route` (id/version/digest/profile/model),
`reviewer_launch_nonce`, `issuer_capability_id`, `redeem_run_id`, `budget`,
`hard_cost_cap`, `unknown_cost_permitted`. These context values originate only
from the trusted factory/test fixtures, not caller files. B maps the fields once;
an interface mismatch returns to #120 before authoring. Existing same-UID trust
limits remain; this is no additional agent or authority framework.

Admission must bind the exact call and report REVIEW_ADMITTED; consume returns
a matching committed token before launch, never an early fake sink effect.
Launcher receives immutable packet bytes/token, preserves A's fixed instructions
and escaped DATA, and returns bounded content, canonical usage metadata bytes,
matching nonce, launch-provenance digest and completion/test-only labels. Observer
returns fresh exact source/author/registration/runtime/event/state observations,
including current authority validity; clock returns trusted UTC seconds and
monotonic time. Fake implementations live only in tests and results retain their
fake label. A's injected service API is trusted host code, not an external JSON
approval endpoint. No production implementation is included.

Finalize follows only a validated consumed token and host launch outcome;
EFFECT_DONE means known completed provider effect, not an approved task. Failed
launch/unknown outcome stays EFFECT_UNKNOWN; finalization uncertainty overrides
any parsed approval with REVIEW_FINALIZE_UNKNOWN, no retry or success promotion.
A/B merge gates, B native durable replay and separately approved T2/T3 remain
required. Constructor/claim/exit0 is never execution or Git/merge authorization.

#### B1 authority adapters and fake-first acceptance

`ISSUE120-S1B-B1` implements the accepted B contract's host-only adapters, with
public `task review` factory still returning `UnavailableReviewAuthority`.
`compose_host_services` is a trusted Python host API, not a CLI/JSON/env switch.
Its immutable `HostServices` carries the existing four seams and pinned context;
the issuer allocates a random reviewer nonce/redeem run before admission.
No production Host path or policy is installed by this candidate.

`HostPin` comes from independent Integrator publication: host-file SHA256,
authorizer reference, pair digest, original packet-input digest and excluded
repository roots. Constructors do not authenticate it. The host loader checks
strict bounded static JSON, private file/directory ownership/modes, no-follow,
single link/stable inode, root/ledger identities and executable/home provenance.
The read-only `review-authority check --host <physical-file> --json` validates
structure only: exit0/structural SUCCESS retains authorized=false, published-pin
validation NOT_VERIFIED and zero calls. It does not open a ledger or issue grants.

`HostAuthorshipObserver` uses actual own-root collector facts and separately
provided trusted host baseline/writer capabilities. Missing gates, unknown
author, wrong branch/root/source or caller expectation drift block the call.
The fake tests supply synthetic facts/capabilities; no actual production
Session/baseline/writer/CI authentication is claimed by these results.

`ProductionReviewIssuer` re-loads CURRENT pinned registration/route, verifies
validity/revocation and reconstructs the exact packet from independently held
originals/pair/recorded pre-observation. Only `decide_provider_review` covers
vouched provider cost/egress; D1 `decide` and its fake labels remain unchanged.
The SAME ReceiptLedger explicitly creates/opens a separate PROVIDER_REVIEW
layout with kind tag/creation nonce; there is no D1 migration. Atomic consume
rechecks current policy, counts the route's durable UTC consume day and commits
CONSUMED before launch. Object uniqueness ignores nonce/run; cap-blocked rows
stay ISSUED. Finalize/recover never rewrite the consume charge; UNKNOWN counts,
and clock rollback/corruption/cross-layout open fail closed.

A-R1 fixed instructions define the strict verdict schema plus host echo values,
without relaxing parsing. A-R2 permits only literal `/Users/<user>/...` and
`/home/<username>/...` documentation placeholders with safe relative components;
real home/private temp paths, known Session/root strings, auth paths and
recognizable secrets stay denied. The temporary screening view changes no
exported source/diff bytes/digests. This is not permission to export every file
in this repository: unsafe complete originals still BLOCKED; any derived export
view needs a separate contract/manifest. Independent privacy/schema acceptance
remains required before T2/T3. A-R3 normalizes unexpected post-consume exceptions
while retaining attempted-call/UNKNOWN/no-retry evidence. A-R4 uses one typed
composition with matching CLI/factory/flow semantics.

The launcher delegates to the existing Claude text/no-tools argv, isolated cwd,
pipe limits and CLIProcessLifecycle. A committed token and matching packet precede
one owned child; unreaped child/process-group identity and host nonce produce a
launch-provenance digest. Text usage remains UNKNOWN. B1 tests use only a private
temporary fake script/fake HOME; no installed Claude or network is invoked.

The [independent native fixture-plan APPROVE](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6131224918)
permits 18 authority +10 provider-ledger +2 policy +5 Darwin cases with a 120s
outer bound, at most two fixture children, P1 isolated `-IB` shebangs and P2
EOF fail-closed/owned-child timeout cleanup. They cover cap/object races, crash
before commit/before effect/after response and durable recovery charges. Non-Darwin
cases remain skips; fake native evidence does not establish real Claude or
power-loss durability. Full regressions and exact-head3.9/3.14 CI accompany the
Draft PR; non-author product Security/Functional and Coordinator's exact-source
T1 release reconciliation remain separate gates. T2 real canary/T3 shadow and
production installation/egress are still NOT_AUTHORIZED. No F2/S2, auto-repair,
Git/PR mutation/merge, daemon/scheduler, second store or benchmark/G5/G6 rerun.
