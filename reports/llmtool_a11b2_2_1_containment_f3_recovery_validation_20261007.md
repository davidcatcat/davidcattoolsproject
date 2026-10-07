# A11b.2.2.1 F3 recovery — initial candidate, 2026-10-07

Status: **IMPLEMENTED CANDIDATE — runner verification PENDING**.
This is not a containment PASS, security approval, acceptance or merge decision.
The outer runner must append fresh final evidence for the actual candidate bytes.

## Authorization and provenance

- Task: `A11b.2.2.1-containment-f3-recovery-20261007`; initial implementation,
  zero runner-directed repairs used.
- Authoritative contract read: `.ai-loop/20261007T075141Z-70725/task.json`.
- Environment: actual Mac filesystem, restricted Codex environment; no runtime
  containment capability is inferred from access to this filesystem.
- Canonical cwd and Git root:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Repository: `davidcatcat/davidcattoolsproject`.
- Observed branch:
  `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`.
- Observed HEAD and pinned base:
  `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.
- Tracked/index state was clean before edits. Existing unrelated untracked files
  and the stopped branch/report were preserved. No fetch or remote-sync claim
  is made; Git integration/publication belongs to the outer runner.
- Source/test changes are uncommitted. HEAD identifies the base, not tested
  implementation bytes. The runner must bind its results to candidate hashes.

Read AGENTS.md, CLAUDE.md, implementation/review/security/safe-Git workflows,
llmtool WORKFLOW/ROADMAP, B5 spec and remediation/re-review evidence, 2.2.0
validation and PR #46 runner review. The required imported stopped-run checkpoint
is absent from the pinned main tree; read its complete existing Git blob at
`ec78614d678eca13324c83f3d6f0c073bae51f03` without checkout changes. Also read
the preserved original local report; neither is treated as a runtime PASS.
Inspected mutation/preflight, Git intelligence, Action contract, errors, packaging,
package init, provider lifecycle and the public macOS SDK process-info structures.

The new user authorization supersedes historical documentation-only slice status.
Section 8 of the recovery handoff supersedes the old separate-child-group and
supervisor-reap-first fallback. No other milestone is authorized.

## Candidate design

The five reserved private modules implement only fixed synthetic cases. No Git
command, repository/action replacement, arbitrary argv, approval, public CLI or
product execution path was added. Existing `git_mutation.py`, Git intelligence
and Action/approval semantics are unchanged. Their existing import rejection
assertions are preserved; additional containment tests scan the complete public
package for private imports, strings, dynamic entry and wrapper references.

The parent launches one supervisor using the current interpreter, `-I`, a fixed
private module, fresh PATH/LC_ALL environment, DEVNULL startup streams, two passed
pipe endpoints and a neutral disposable directory under `/private/tmp`. It creates
no project checkout there. The supervisor owns the new session/group, with
PID == PGID == SID. Its fixed fake child and controlled descendant inherit that
group and do not change session/group. The controlled leader-exit case waits for
descendant readiness before exiting and leaves the descendant alive.

F3 cleanup always uses the still-owned supervisor anchor. Parent exit observation
uses non-reaping kqueue NOTE_EXIT; `kill_group` freshly validates kernel identity
and unreaped direct-child ownership before group signaling. Only then may `reap`
consume the supervisor. After reap, verification uses passive observations only;
there is no remembered-ID signaling path. On parent EOF the supervisor performs
the authorized last-act group SIGKILL, intentionally killing itself too. Adoption
and eventual kernel reaping are verified by the actual acceptance probes.

For normal synthetic completion, the supervisor sends bounded lifecycle evidence
while retaining its direct child unreaped. Terminal evidence is explicitly not
a group-gone claim. The parent makes the group cleanup decision before supervisor
reap and confirms eventual group/child/descendant disappearance. This avoids an
attempt to SIGKILL the supervisor's group while keeping that supervisor alive to
report success. No real Git exit/post-state semantics are implemented.

F1 has a distinct `ContainmentUnresolved` responsibility transfer. It preserves
the primary failure, handlers, endpoints, monitor, process and neutral runtime.
The closed harness host emits bounded state-unknown/user-action-required evidence
and remains in operator-wait state; it does not restore handlers, normally return,
reap an ambiguous anchor, guess a group, retry or spawn another process. Formal
review must assess this explicit retained-responsibility behavior.

F2 uses the public Darwin `proc_pidinfo` ABI plus kqueue NOTE_EXIT. Unit and native
acceptance coverage include registration failure, fast exit and live/zombie
registration. F3 additionally binds PID/PGID/SID/UID/PPID/kernel start time and
tests mismatch, reuse, released ownership, unexpected supervisor death and
verification-to-signal timing. The identity snapshot detects drift; the reserved
unreaped supervisor PID supplies continuity, not an atomic snapshot handle.

F4 restricts entry to the reviewed synchronous standalone test parent. Main
Python thread/count, native kernel thread count and default SIGCHLD checks are
necessary conditions; the fixed host has no asynchronous plugin/provider/native
fork callbacks or foreign reaper. Thread counts alone are not a proof of that
premise. Arbitrary embedding hosts are rejected; future product-host approval is
outside this slice. The extra close_fds Popen variant is explicitly harness-only.

The wire protocol uses bounded strict JSON, a four-byte prefix, incremental
nonblocking I/O and monotonic deadlines. Origins and SHA-256 manifests bind all
private containment modules, preflight/mutation, Git intelligence, Action contract,
errors, package init and the fixed harness host, along with interpreter/install identity. Supervisor
verification runs independently before READY and again before fixed child spawn.
The reviewed engineering host pins its fixture manifest before entering the
window. Synthetic domain action/preflight digests bind the fake case only; they
are not a fake product approval or durable-audit authorization. Isolated import
failure has no PYTHONPATH/cwd/interpreter fallback or global-install workaround.

## Required dispositions and pending gates

No deterministic tests, native process probes, full suites or provider calls were
run by Codex. Test/probe behavior described here is implemented coverage awaiting
execution, not a measured result. No acceptance JSON has been generated here.

| Requested disposition | Initial candidate / evidence status |
| --- | --- |
| Private harness and separate supervisor | Implemented; syntax/unit/ordinary review PENDING runner |
| FD ownership, CLOEXEC, fixed exec and fake-child FD enumeration | Implemented; PENDING actual checks |
| Parent SIGKILL plain x20, each <=2 seconds | Acceptance code requires real repetitions; zero executed by Codex; PENDING |
| Parent SIGKILL extra Popen x20, each <=2 seconds | Harness-only variant implemented; zero executed by Codex; PENDING |
| Real SIGINT / SIGTERM / SIGHUP including timing/repetition | Acceptance cases implemented; PENDING |
| Hung/lost supervisor, missing ACK, bounded escalation | Fixed faults and anchor escalation implemented; PENDING |
| Leader exit with live descendant and eventual group disappearance | Controlled readiness and native zombie/live assertions implemented; PENDING |
| PID/PGID mismatch/reuse, live/zombie anchors, signal-after-reap rejection | Guards and adversarial/native cases implemented; PENDING |
| Kernel start-time identity and F3 continuity | Public Darwin ABI and reserved PID/group topology implemented; mandatory Mac proof PENDING |
| Non-reaping kqueue live/zombie/fast-exit/registration-failure | Implemented; actual Mac checks PENDING |
| Framing, partial I/O, bounds, EOF/EPIPE, deadlines | Unit/acceptance coverage implemented; PENDING runner |
| Code/runtime origin/hash binding and internal handshake | Implemented; drift/boundary/native evidence PENDING |
| Import/direct/runpy/-m/wrapper/public boundary | New static/dynamic coverage; existing rejection assertions preserved; PENDING |
| F1 unresolved behavior / primary failure preservation | Explicit retained-responsibility path; execution/review PENDING |
| F4 Python/native single-thread trusted host premise | Fixed-host restriction and entry checks; verification/review PENDING |
| Existing Git/A5/Action regressions | Unchanged behavior; runner checks PENDING |
| Full checks, tools/llmtool/.venv/bin/python | PENDING outer runner; no current PASS |
| Full checks, /usr/bin/python3 | PENDING outer runner; no current PASS or isolated supervisor support claim |
| Strict fresh --output PATH evidence, nonzero checks, zero skips | Acceptance entrypoint implemented; fresh invocation/evidence validation PENDING |
| BLOCKER/HIGH/correctness disposition | No independent candidate verdict; runner ordinary review and formal security review PENDING |
| Real product Git ADD / post-state mutation | None implemented or executed |
| Public mutation / approval issuer / durable-audit bypass | None introduced |
| A11b.2.2.2/.3, A11b.3, A11c, A12 | Not started; no authorization inferred |

`tests/run_containment_acceptance.py --output PATH` reads the runner's adjacent
pinned task.json, invalidates stale output, records current source/test hashes,
runs the 14 exact scenarios and writes PASS rows only after successful checks.
Failures/unsupported cases exit nonzero and preserve invocation evidence, including
completed/active SIGKILL repetition evidence. EPERM is inconclusive, never absence.
There are no scenario skips, schema-only success or historical-result substitutions.
Tests print position, outcome and duration, with a 20-second progress heartbeat.

If a mandatory native failure leaves identity uncertain after host death, the
probe does not signal released remembered numbers to force green evidence. It
retains failure/identity evidence for operator resolution and stops. Prior to
host death, assertion cleanup can use the reviewed host's still-owned anchor.

## Runner handoff

The outer runner owns syntax, containment units, Git/A5/Action tests, ordinary
Claude review, both Python full suites, actual Mac acceptance, final hashes/report
append, complete final diff re-review, publication and exact-HEAD CI. Native probe
failure/unsupported/instability stops; there is no automatic probe repair or blind
provider retry. Independent security review must explicitly assess the approved
F3 supervisor anchor and F1–F4 dispositions before coordinator acceptance.

Prepared for runner verification and subsequent independent security review.
**READY_FOR_FINAL_REVIEW is not yet reached**; required runner gates are pending.
No security PASS, merge readiness, remote synchronization or completed acceptance
is claimed. Stop after this candidate; do not start another slice.

No stage, commit, push, merge, worktree, clone, global install, sudo, task-contract
edit, user-repository/index mutation, lock deletion or unrelated-file edit occurred.
Only contracted source/test/report files were added. Raw provider streams/private
logs are not included; curated evidence alone belongs in Git.

## Authorized quota continuation — 2026-10-07

This section supersedes candidate-completion wording above for the retained partial
implementation. The earlier text remains historical partial-candidate evidence.
The interrupted invocation supplied no inherited tests, ordinary review, Mac
containment result or security verdict. All current verification gates remain
**PENDING / NOT TESTED**. No acceptance JSON was generated by Codex.

Continuation contract: `.ai-loop/20261007T113658Z-74880/task.json`.
Recovery provenance and retained input hashes/copies are recorded in
`.ai-loop/20261007T113658Z-74880/recovery.json` and its `recovery-inputs/` directory.
The prior run is `20261007T075141Z-70725`; prior control is
`523c30afa16384152d3a1c2bd0a03cd2020a8fdf`. This is completion of that partial
initial candidate, with zero runner-directed repairs used here.

Read the existing allowed files before editing; continued their implementation
without replacing the candidate or removing historical evidence. Read the required
guidance, B5 specification/reviews/remediation, 2.2.0 report, PR #46 ordinary-review
evidence and stopped F3 checkpoint. The imported checkpoint remains absent from
the pinned checkout; its complete existing blob was read through
`git show ec78614d678eca13324c83f3d6f0c073bae51f03:<checkpoint path>` without
changing branches or repository files. Inspected the public installed SDK ABI,
existing `_Reads.query`, `_GitRunner.run`, Action contract, errors and packaging.

Canonical root, repository remote identity, recovery branch and HEAD were observed
again; root/branch/base match the contract and tracked/index state was clean.
Unrelated untracked artifacts were observed and left untouched. The recovery
manifest explicitly records **prior unrelated-file integrity UNVERIFIED**;
earlier preservation wording does not establish a retrospective integrity PASS.
No fetch, remote synchronization, Git state mutation, provider call, deterministic
test, syntax check, expensive suite or native probe was performed by Codex.

### Changes made while completing the retained candidate

- Fixed the EOF last-act identity check: parent death reparents the supervisor.
  Self-signaling now permits that PPID change while freshly matching its own
  PID/PGID/SID/UID/kernel start time. The supervisor remains its own live group
  anchor. The parent's ownership check still requires exact direct-child PPID.
- Removed the outer probe's snapshot-based grandchild-group signaling. Failed
  probes ask the still-owned host to contain through its supervisor anchor;
  ambiguity retains evidence and requires external action. The intentionally
  unresolved F1 fixture is resolved by its external controller signaling only
  its own identity-bound, unreaped host, causing real EOF group cleanup.
- Added behavioral parent cleanup tests: signal scope before pipes, exact launch
  and endpoint handoff, immediate unused-end closure, group decision before reap,
  disappearance before FD closure/handler restoration, and interruption retained
  despite START EPIPE and unresolved cleanup. These deterministic tests use mocks
  and unit-owned pipes, and are not native containment evidence.
- Added a real-Mac F1 scenario within `hung_supervisor`: a fixed synthetic identity
  mismatch retains the live host, handlers, endpoints and primary timeout;
  repeated real signals cannot produce normal completion. External fixture
  resolution uses host SIGKILL/EOF and bounded passive disappearance checks.
- Bound the synthetic case/timeout and reviewed manifest into domain-specific
  action/preflight digests; tightened binding types and no-follow artifact reads.
  These fixture digests issue no Action approval and bypass no durable audit.
- Added fake-child/descendant launch binding to the code manifest, nonce and
  kernel peer/anchor identities through fixed internal metadata. No parent
  control/status/audit FD reaches a fake child. Both child and descendant enumerate
  descriptors; descendant readiness has bounded incremental framing.
- Added coverage for partial writes/backpressure, pre-READY and pre-spawn binding
  rejection, self-anchor reparenting, missing child handshake, fixed timeout,
  idempotent signaling and handler restoration. Non-reaping notifications validate
  the expected process/filter/NOTE_EXIT and reject error events.
- The inheritance-stress variant now reports and verifies its extra finite child
  is executing at parent SIGKILL, and checks its eventual disappearance too.
  Restoration occurs after that owned finite child is reaped. This extra spawn
  remains strictly harness-only.
- Hardened the fixed `--output PATH` entrypoint's pinned-contract parsing and
  nonzero check/repetition requirements. All 14 scenario names and the exact PASS
  evidence schema are unchanged. Failure or unsupported evidence remains nonzero;
  no historical or schema-only success substitutes for execution.

Trusted installation/administration remains the reviewed deployment premise.
File identity/origin/hash checks detect drift; they do not isolate execution from
an administrator or malicious same-UID actor able to replace installed code.
The standalone isolated host rejects traced/profiled or arbitrary embedding entry;
Python/native counts plus default SIGCHLD supplement the closed-host premise,
rather than proving absence of arbitrary future native fork callbacks. No global
installation, permissions change, alternate interpreter or import fallback was used.

### Current gate disposition and stopping point

Every gate in the earlier disposition table is still pending on the resulting
source/test bytes: syntax and containment units; Git/A5/Action regressions; FD
inheritance; real SIGINT/SIGTERM/SIGHUP timing and restoration; hung/missing-ACK/
lost-supervisor escalation; live-descendant leader-exit cleanup; identity reuse,
kernel start time and verification-to-signal continuity; non-reaping kqueue;
framing; code/runtime binding and public boundary; x20 plain and x20 Popen host
SIGKILL, each with the unchanged <=2-second bound; both Python full suites; fresh
Mac evidence; ordinary final-diff review; and exact final-HEAD CI.

The outer runner must append truthful commands, results, failures and hashes.
The runner's mandatory native failure/unsupported/instability policy remains STOP,
with evidence retained and no automatic probe repair. Formal independent security
review, BLOCKER/HIGH/correctness disposition and coordinator acceptance remain
pending. That review must explicitly verify the approved supervisor-anchor
remediation and F1–F4. No security PASS or merge approval is claimed.

Implementation candidate prepared for runner verification. **READY_FOR_FINAL_REVIEW
has not yet been reached**; required checks and independent review remain pending.
No real product Git ADD, post-state mutation, public mutation path, approval issuer,
audit bypass, lock deletion, retry or rollback was introduced. Existing
`git_mutation.py`, Git/A5 and Action/approval behavior and all rejection assertions
remain unchanged. No 2.2.2/.3, A11b.3, A11c or A12 work was started or authorized.
Only allowed files were edited. No stage, commit, push, merge, worktree, clone,
alternate checkout or unrelated-file edit occurred. Stop at this candidate.

## Authorized timeout continuation — current candidate, 2026-10-07

Current status: **IMPLEMENTED CANDIDATE; all runner gates PENDING / NOT TESTED**.
This section records completion of the retained initial candidate after the
interrupted Codex call. Earlier candidate-completion wording and the quota
continuation remain historical evidence, with no inherited validation verdict.
The structured implementation return is not a test, containment or security PASS.

- Current contract: `.ai-loop/20261007T123142Z-77009/task.json`.
- Retained before-edit hashes and copies:
  `.ai-loop/20261007T123142Z-77009/recovery.json` and `recovery-inputs/`.
- Prior interrupted run: `20261007T113658Z-74880`; timeout recovery changes only
  the Codex command deadline, as authorized. No runner-directed repairs used here.
- Read-only observations again establish the canonical Mac root, repository
  identity, recovery branch and pinned HEAD `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.
  The observed tracked/index state at entry was clean; retained candidate files
  were untracked. No fetch or current remote-synchronization claim is made.
- Earlier unrelated-file integrity remains **UNVERIFIED**, as recorded by the
  runner. Existing artifacts were left untouched during this continuation.
- The stopped F3 checkpoint was read from the known control commit
  `01cd4757b23ce0ec03f6b2c1e0848f849ebda01a`; it is absent from pinned main.
  No branch change, checkout or historical-file edit was needed.

### Bounded source audit and in-scope completion

Read the existing allowed candidate files and required guidance/evidence before
continuing. Inspected the existing read runners, Action contract/error definitions
and installed public SDK process-info layouts. SDK inspection is source evidence,
not a kernel identity query, process probe or platform support result.

Source audit identified two correctness gaps in the retained bytes: an already
expired receive deadline could accept buffered data, and a cleanup-time signal
could replace an established primary failure. The retained recovery inputs preserve
the before-fix source. No executed reproducer or test PASS is claimed here.

- Receive rejects before consuming a frame after its deadline and rejects a
  frame completed after that deadline. Regression tests cover both cases.
- The first established start/timeout/output/protocol/supervisor failure survives
  subsequent signals and cleanup faults. An earlier interruption remains primary
  across START EPIPE and handshake expiry. New parent lifecycle tests cover late
  signals and unresolved cleanup without restoring handlers or releasing FDs.
- Group-signal failures retain bounded sanitized secondary fault types. A fallback
  direct supervisor signal is allowed only under the same verified unreaped pin;
  it never establishes descendant containment. Group disappearance is still
  separately required, and no signal is allowed after reap. Monitor/endpoint/runtime
  cleanup failures use fixed labels, without exception text or raw output.
- Native assertion backstops contain only identity-bound, unreaped owned fixtures.
  Ambiguous launches retain process references and synthetic identity evidence for
  external resolution instead of signaling guessed IDs. Failure JSON retains this
  evidence. The acceptance controller checks its own single-thread/default-reaper
  premise before native fixture creation.
- The real launch-phase signal fixture now waits after supervisor exec and immediate
  unused-end closure, before START. Its evidence proves a launched session/group
  leader exists; it does not claim a signal was delivered inside the Popen syscall.
  The handshake deadline remains two seconds, with no extra spawning callback.
- Added deterministic rejection cases for repeated START, post-START replacement
  and wrong CANCEL nonce before and after fake-child spawn. Post-spawn rejection
  must use last-act containment without polling, waiting or communicating first.
- Extended the existing mutation import-boundary test for private strings/public
  references while preserving every original mutation-import rejection assertion.
  The reviewed private-module exception permits their internal references only;
  it grants no public execution, Action approval or audit bypass.
- Acceptance progress emits its synchronous heartbeat during probe waits as well
  as checks, with no extra thread or repeated command. Probe hosts launch from the
  canonical root; supervisors still use the trusted neutral runtime cwd.

The supervisor-as-group-anchor architecture, isolated fixed launch, FD topology,
trusted-host premise, native kqueue/start-time mechanism and F1 operator-wait
behavior remain the candidate's design. No alternate anchor, snapshot-only orphan
group signal, private Apple signaling API, global setup or interpreter fallback
was introduced. The fixed fake-child cases contain no real Git ADD or post-state
mutation. Existing production Git/A5/Action behavior is unchanged.

### Current runner and review dispositions

Every earlier acceptance-table gate remains **PENDING / NOT TESTED** on these bytes:
syntax and nonzero containment discovery; existing Git/A5/Action regressions;
FD inheritance; real SIGINT/TERM/HUP timing and repeated signals; hung/missing-ACK/
unexpected supervisor loss; leader exit with executing descendant; mismatch/reuse,
live/zombie anchors and verification-to-signal continuity; kernel start time;
non-reaping live/zombie/fast-exit/registration-failure kqueue; framing and deadlines;
code/runtime binding and public invocation boundary; x20 plain and x20 Popen real
parent SIGKILL with every latency <=2 seconds; strict fresh native JSON evidence;
full checks under both `tools/llmtool/.venv/bin/python` and `/usr/bin/python3`;
complete-diff ordinary review and exact publication-HEAD CI.

Codex ran no syntax checks, deterministic tests, native containment probes, full
suites or provider calls and generated no acceptance JSON. The outer runner owns
these checks, truthful results/hashes appended to this report, Git publication and
the new Draft PR. A mandatory native failure, unsupported capability or instability
still stops without automatic probe repair or weakened thresholds.

BLOCKER/HIGH/correctness disposition, independent security review and coordinator
acceptance remain pending. Security review must explicitly assess the F3 reserved
supervisor PID/PGID anchor and F1–F4. The candidate is prepared for runner validation
and subsequent independent security review; **READY_FOR_FINAL_REVIEW is not yet
reached**. No formal security PASS or merge approval is implied.

No real product Git ADD, public mutation path, approval issuer, durable-audit bypass,
lock deletion, retry or rollback was added. No 2.2.2/.3, A11b.3, A11c or A12 work
was started or authorized. Only contracted files were edited. No staging, commit,
push, merge, worktree, clone, alternate checkout, global install, permission bypass
or task-contract change occurred. Stop at this candidate; the runner owns next steps.
