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

## Authorized native-SIGINT repair continuation — 2026-10-07

Current status: **PREPARED REPAIR CANDIDATE; new runner gates PENDING**.
This section supersedes earlier current-status wording for this continuation;
all earlier report content is preserved as historical evidence. The implementation
return is not native containment approval, security PASS or merge approval.

### Pinned recovery and original failure

- Current runner run: `20261007T153635Z-87533`; read its `task.json`, `recovery.json`,
  `coordinator-assessment.md` and saved recovery evidence before repair.
- Original run: `20261007T123142Z-77009`; pinned original control:
  `b75ea2e371fd4e8e6130a4b2be8a48e7256b25aa`.
- Read-only entry observations: canonical Mac cwd/Git root and repository remote
  identity match the task, branch remains
  `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`, and HEAD remains
  `ce29d4a6a7764e62dc993b76b33996db2fb3455b`. Tracked/index state was clean at entry.
  Base remains `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.
- The runner's recovery manifest preserves copies/hashes of all ten candidate files
  and the original summary/Mac JSON. Evidence-summary SHA-256:
  `b722cbfd422dc8e65b19c42db126de13d33fa56527013e4073a538efdde7363c`.
  Original Mac JSON SHA-256:
  `c6dbdfccdefdb35de8efc4f4f20102217eb0a102f94bb1f5ea785cc982283c07`.
- Current unrelated artifacts are preserved; historical integrity remains
  **UNVERIFIED**, and inherited validation for the repair is **NONE**. No remote
  synchronization, feature publication or historical process absence is asserted
  from Codex's local observations. No old snapshot PID/PGID was signaled.
- This is the first bounded repair call of the newly authorized continuation,
  which permits at most two calls. Source/test bytes changed; this is not a
  report-only or unchanged candidate retry.

Historical runner results at **ce29d4a**, confined to the original bytes:

| Original gate | Historical evidence only |
| --- | --- |
| Syntax | Exit 0; 0.4 seconds |
| Containment unit discovery | Exit 0; 0.8 seconds |
| Git mutation regressions | Exit 0; 40.2 seconds |
| A5 Git intelligence | Exit 0; 15.5 seconds |
| Action contract | Exit 0; 0.7 seconds |
| Preliminary read-only Claude ordinary review | Recorded PASS at ce29d4a; warned about zombie SID lookup; not formal security approval |
| Full check, venv Python | Exit 0; 216.8 seconds |
| Full check, /usr/bin/python3 | Exit 0; 336.1 seconds |
| Mandatory actual Mac acceptance | **FAIL**, exit 1; 7.3 seconds |
| fd_inheritance | Historical PASS, 9 checks, 0 skips |
| sigint | **Original FAIL**, AssertionError; original JSON omitted timing, message and traceback |
| Other twelve native scenarios, including both parent-SIGKILL variants | **NOT RUN**, no native PASS inherited |

The original evidence records Darwin/macOS 15.7.9 and Python 3.13.3. These versions
describe that failed invocation, not a new probe. The coordinator's later operator
inspection observed a stuck standalone test host and its unreaped zombie supervisor;
it did not identify the first failing operation or establish group containment.
External fixture cleanup is not native PASS. **The original SIGINT result remains
FAIL even if a later repaired invocation passes.**

### Diagnostic repair and owned-zombie continuity

Source inspection confirms that a probe's `finally` cleanup could replace the
original signal-path exception with a generic AssertionError, and that failure JSON
previously retained only its type. The repair keeps the original exception object
and stores cleanup and monitor-close errors separately. Failure evidence includes
bounded exception type, reviewed fixed message (otherwise withheld), numeric errno,
up to eight traceback locations, signal timing variant/number, expected event,
collected synthetic event/protocol rows and cleanup outcome. Tracebacks contain no
source text, locals or exception-chain dumps. OS filenames, arbitrary argv, raw
pipe fragments, stderr, environment, provider output and non-fixed exception text
are excluded. No provider data is involved in the harness.

The parent harness retains its primary exception separately in unresolved ownership,
alongside the typed primary lifecycle outcome. Later containment/stress/monitor/FD/
runtime/handler errors cannot replace that evidence. Its fixed host emits the
bounded diagnostic object on unresolved containment. The existing F1 responsibility
transfer still retains Popen, endpoints, monitor, handlers and neutral runtime;
it neither normally returns nor restores handlers while containment is unresolved.

The SID hypothesis is **source-supported, not the established native root cause**.
[Apple's getsid implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_prot.c)
uses a process lookup that can return ESRCH. The public
[process-information ABI](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h)
provides PID/PPID/UID/PGID/status/start time; its `e_tpgid` is a terminal group,
not SID. The
[BSD-info implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/proc_info.c)
has zombie-aware handling. These upstream sources and local SDK declarations are
design evidence, not proof of behavior on the recorded kernel.

Ordinary `identity()` remains strict: it still queries SID and rejects ambiguity or
drift. `Anchor.verify()` permits a separate proof only after exact ESRCH from that
ordinary query. It requires a retained direct-child Popen with unreleased status,
launch-bound PID == PGID == SID, matching parent/UID, and two fresh kernel records
that both report SZOMB and match PID/PGID/UID/PPID/seconds/microseconds. It rechecks
Popen ownership after the reads and rechecks the single-thread/default-reaper host
premise before querying. No permission error, missing row, live state, mismatch,
changed start time or released ownership becomes an identity PASS.

SID continuity in this special case derives from the already verified session-leader
launch and this still-reserved direct-child PID, not from `tpgid` or numbers cached
for an arbitrary process. The closed host has no foreign reaper; the immutable
zombie cannot change session and its unreaped PID pins the equal group ID through
the cleanup decision. This is still the accepted supervisor-as-group-anchor design,
not a separate anchor, per-member signaling design or Apple private API. Host
premises and continuity remain subject to native proof and independent security
review. If kernel identity cannot support them, the path stays unresolved.

### Added coverage and pending gates

Deterministic regressions cover primary-versus-cleanup/monitor retention; cleanup
failure alone; successful cleanup with a protocol failure; diagnostic secret/path/
argv/output withholding; strict ordinary identity rejection; owned-zombie matching
rows; live-to-zombie SID-query races; every bound field/state mismatch on either
read; absent/permission-denied rows; non-ESRCH SID faults; unprovable host; released
Popen ownership during verification; and non-Popen rejection. Existing assertions,
including no signaling after reap, are retained.

The actual-Mac kqueue scenario still registers while alive and after zombie exit,
requires real NOTE_EXIT without reap, freshly confirms zombie state, exercises the
owned continuity verifier and requires cleanup before reap/group disappearance.
It retains/prints bounded native proof metadata identifying the actual verification
mode; no mock/skip becomes native PASS. The real verification-to-signal supervisor
exit case now checks zombie continuity again before group signaling. The acceptance
interface, fourteen exact scenario names, nonzero checks, zero native skips, >=20
plain plus >=20 Popen parent-SIGKILL repetitions, and <=2-second disappearance bound
remain unchanged. Failure JSON gains diagnostics; success JSON retains its exact
contract shape. Any mandatory Mac failure still stops without automatic retry.

| New repair gate/disposition | Current status |
| --- | --- |
| Syntax, containment units, primary/cleanup regressions and new identity tests | PENDING outer runner |
| Existing Git/A5/Action regressions and public/import boundary | PENDING outer runner |
| FD topology / fixed supervisor / fake-child inheritance | PENDING exact-byte runner/native checks |
| SIGINT / SIGTERM / SIGHUP, all timings/repeated signals/restoration | PENDING actual Mac |
| Hung/lost supervisor, missing ACK, leader/descendant cleanup and F1 behavior | PENDING actual Mac |
| Kernel start time, mismatch/reuse, live/zombie continuity and non-reaping kqueue | PENDING actual Mac and review |
| Framing, code/runtime binding, direct/import/runpy/-m/wrapper boundary | PENDING runner/native checks |
| x20 plain and x20 Popen parent SIGKILL, each <=2 seconds | PENDING actual Mac; no inherited native repetitions |
| Full Python suites under venv and /usr/bin/python3 | PENDING outer runner on changed bytes |
| Preliminary and complete final-diff ordinary Claude review | PENDING outer runner |
| BLOCKER/HIGH/correctness disposition and independent security review | PENDING; security must assess owned-zombie SID continuity and F1–F4 |
| Coordinator acceptance, final-HEAD CI / READY_FOR_FINAL_REVIEW | PENDING runner/coordinator |
| Real product Git ADD / public mutation / approval issuer / audit bypass | None introduced or executed |
| 2.2.2/.3, A11b.3, A11c or A12 | Not started or authorized |

Codex performed source/evidence inspection and bounded edits only. It ran no tests,
syntax command, suites, native process/signal probes, provider calls or acceptance
generator. No acceptance JSON was fabricated. Tests, exact-byte hashes, truthful
evidence append and all Git/publication work belong to the outer runner. No stage,
commit, push, merge, branch switch, reset, stash, worktree, clone, global install,
marker deletion, task-contract change or unrelated-file edit occurred. Stop at this
prepared candidate; independent security/coordinator approval remains pending.

## Second native failure: bounded libproc repair — 2026-10-08

Current status: **PREPARED REPAIR CANDIDATE; all new runner gates PENDING**.
This append supersedes prior current-status/root-cause wording for this recovery
only. Both original native SIGINT invocations remain permanently **FAIL**.
The structured implementation return means a prepared candidate, not native
acceptance, independent security approval, publication readiness or merge approval.

### Authorization and entry evidence

- Recovery run: `20261007T162747Z-96025`, continuing stopped run
  `20261007T153635Z-87533` under the native-pinned task ID.
- Read the current runner's `recovery.json`, copied `coordinator-assessment.md`,
  `task.json`, `recovery-evidence/summary.json` and
  `recovery-evidence/mac-containment-0.json` before edits. The second-failure
  assessment governs; the first failure and previous recovery are historical.
- Canonical Mac cwd and Git root were verified as
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`; remote identity
  is `davidcatcat/davidcattoolsproject`. Branch remains
  `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`.
- Entry HEAD: `325bde3fe74c5f998ccf1dd046487c0900716224`; tracked/index state
  was clean. Pinned base remains `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`;
  stopped control is `9c8b36d01dd0edb1233cc0fcc1be8c8b62cf006f`.
- The runner preserved ten candidate files and their hashes. Second-failure
  summary SHA-256: `ae855afa2409d12fa68161717335505ca68431b93ed8a466a76d8e0b47cab09b`;
  native JSON SHA-256: `f025bb4d0ad8f910ecd9ccd56755ce0b84a55fb26ec25ce241c07ffe69d28281`.
- This is the first Codex repair call in this newly authorized continuation
  (at most two calls). Source and test bytes changed before runner checks.
- Read-only `ps -ww` inspection of the two retained snapshot PIDs was attempted
  but rejected by the sandbox (`operation not permitted`). Their current
  presence/absence, full host path and parent-child relationship are therefore
  **UNVERIFIED by this Codex call**. The outer runner retains ownership of the
  required stale-process preflight. No snapshot PID/PGID was signaled, no probe
  was launched, and no marker or fixture was deleted. Operator fixture cleanup
  would not constitute containment PASS.
- Unrelated untracked artifacts were left untouched; retrospective integrity
  remains UNVERIFIED. No fetch or remote synchronization is claimed.

### Historical exact-byte results and established defect

Results below belong only to stopped candidate **325bde3**, with its saved
source/test hash map. No historical PASS is inherited by this repair.

| Gate | Historical result at 325bde3 only |
| --- | --- |
| Syntax / containment units / mutation / A5 / Action related checks | Exit 0 each; 0.3 / 0.8 / 39.5 / 13.7 / 0.6 seconds |
| Preliminary read-only Claude ordinary review | Recorded PASS; not formal security approval |
| Full check, venv Python | Exit 0; 211.8 seconds |
| Full check, /usr/bin/python3 | Exit 0; 330.7 seconds |
| Mandatory actual Mac acceptance | **FAIL**, exit 1; 7.2 seconds |
| fd_inheritance | Historical PASS; 9 checks, zero skips |
| Ordinary sigint | **Second original FAIL**; primary interrupted, signal 2, status EOF and unresolved cleanup retained |
| Other twelve native scenarios, including both parent-SIGKILL variants | **NOT RUN**; no native acceptance inherited |

The first invocation `20261007T123142Z-77009` at **ce29d4a** also remains
**FAIL**, with its historical full/review evidence recorded above. The second
invocation records macOS 15.7.9 and venv Python 3.13.3; its summary records system
Python 3.9.6. These describe historical executions, not a new platform probe.

The second traceback reaches `Anchor.kill_group -> verify -> identity -> _info`
at the first BSD query and raises `IdentityUnknown: kernel identity unavailable`.
It does not reach `getsid` or the owned-zombie fallback. Diagnostic retention
worked: primary interruption and the later cleanup exception remain separate.
The old evidence contains neither API return length nor errno and does not prove
measured ESRCH, successful SZOMB observation, or final containment.

The concrete API-use defect is the unconditional zero argument to libproc.
[Apple XNU at xnu-11417.140.69](https://github.com/apple-oss-distributions/xnu/blob/xnu-11417.140.69/bsd/kern/proc_info.c)
enables zombie lookup for BSD flavor 3 when arg is nonzero and then uses the
zombie lookup if ordinary process lookup fails. This establishes the missing
API behavior in source; the repaired native invocation must still establish
the actual kernel rows, errno on failures and complete containment behavior.

### Bounded implementation and regression coverage

The shared `_info` boundary now explicitly accepts only the reviewed flavor/model
pairs: `PROC_PIDTBSDINFO` (3, `_BSD`) uses arg 1 for live/zombie reads;
`PROC_PIDTASKINFO` (4, `_Task`) retains arg 0. Every flavor-3 caller was inspected:
ordinary identity before/after SID, two-row owned-anchor verification, fixed
verification-to-signal race, probe-host cleanup, native leader/descendant checks,
and live/zombie kqueue registration evidence. All reach the repaired helper;
none bypasses it with an independent C call. Native kqueue/zombie scenarios
continue to use real libproc with no mocked capability or skipped proof.

The helper clears ctypes errno immediately before the C call, then immediately
captures actual returned bytes and errno. A size mismatch, including zero or
short return, still raises `IdentityUnknown`, carrying only flavor, arg,
requested bytes, returned bytes and errno. The reviewed formatter accepts an
exact bounded numeric schema for this optional metadata and rejects arbitrary
fields, booleans, strings and out-of-range values. It retains the fixed primary
message and existing bounded traceback rules; no buffers, environment, argv,
credentials, provider output or private logs enter curated evidence.
BSD-query ESRCH remains a failed row and cannot trigger the SID-specific fallback.

Identity proof and ordering are unchanged: ordinary identity requires a real
SID and stable fresh kernel rows. Special owned-zombie continuity still requires
the unreaped direct Popen child, launch-bound PID=PGID=SID, UID/PPID/start time,
host/default-reaper premise, two fresh matching SZOMB rows and ownership recheck
before signaling/reaping. Neither tpgid nor numeric group existence supplies SID
or ownership. F1 retention, deadlines, code binding and group-decision-before-reap
remain in force.

New deterministic tests replace the **libproc callable**, leaving `_info`,
`identity` and `Anchor` real. They assert actual flavor/arg and SDK buffer sizes
(BSD 136, task 96), errno reset, and `use_errno=True`. Coverage includes initial
BSD-before-SID ordering; live and owned-zombie success models; live-to-zombie SID
failure; two fresh proof rows; verification-to-signal exit; zero/short/oversize
returns; ESRCH/EPERM/EACCES; live identity/SID mismatch; mismatch/reused start
identity on either zombie row; missing rows; and reaping during the second proof
row. Rejection assertions forbid group/leader signals and premature reap.
Additional tests retain actual C-call diagnostics as secondary to a primary
fixture error and reject unsafe formatter metadata. All these tests are
**PENDING execution**, and synthetic rows never count as native evidence.

### New gate dispositions

| Gate / requested disposition | Current repair status |
| --- | --- |
| Syntax, nonzero containment discovery, C-boundary regressions | PENDING outer runner |
| Git mutation / A5 / Action and public import regressions | PENDING outer runner |
| Supervisor/fake-child harness, FD topology and inheritance | PENDING exact-byte runner/native checks |
| SIGINT / SIGTERM / SIGHUP, timing and repeated signals | PENDING actual Mac |
| Hung/lost supervisor, missing ACK, F1 retention and leader/descendant cleanup | PENDING actual Mac |
| PID/PGID mismatch/reuse, kernel start time, live/zombie continuity and kqueue | PENDING actual Mac and independent review |
| Protocol framing/deadlines, code/runtime binding, public invocation boundary | PENDING runner/native checks |
| Parent SIGKILL plain x20 and Popen x20, each disappearance <=2 seconds | PENDING actual Mac; no inherited repetitions |
| Both full Python suites (venv and /usr/bin/python3) | PENDING outer runner on changed bytes |
| Preliminary and complete final-diff Claude ordinary review | PENDING outer runner |
| BLOCKER/HIGH disposition, independent security and coordinator acceptance | PENDING; source review is not security acceptance |
| Exact final-HEAD CI / READY_FOR_FINAL_REVIEW | PENDING runner; not reached |
| Real Git ADD, user index mutation, public mutation, approval issuer, audit bypass | None introduced or executed |
| A11b.2.2.2 / later slices | Not started or authorized |

Codex performed read-only inspection, source/diff review and allowlisted edits.
No tests, syntax checks, suites, native containment probes or acceptance generator
ran inside this call. No acceptance JSON or new PASS evidence was fabricated.
Only `_git_process_identity.py`, `_git_containment_protocol.py`,
`test_git_containment.py` and this report changed. No Git write, branch change,
reset, stash, worktree, clone, global install, task change or scope expansion
occurred. The outer runner must append truthful results and hashes, run all
fourteen native scenarios with original bounds, and stop immediately for
assessment on another mandatory native failure. Independent security review
and coordinator approval remain pending; never merge or begin 2.2.2.

## CURRENT run 96025 continuation — exit transition and controller cleanup, 2026-10-08

Status: **PREPARED REPAIR CANDIDATE — ALL NEW RUNNER CHECKS PENDING**.
This append preserves the historical report and supersedes its older current
two-call/manual-native-failure stopping policy. David's CURRENT authorization
permits four candidate calls including this continuation's first call, four
evidence-bound failure classifications, and stops two repeated failures with
the same blocking signature as REPAIR_STALLED. Classification, immutable
diagnostic publication, test execution and Git remain outer-runner duties.
No failing required check becomes PASS because repair stops.

### Entry provenance and original failure

- Canonical Mac cwd and Git root verified:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Repository identity: `davidcatcat/davidcattoolsproject`; branch unchanged:
  `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`.
- Entry HEAD / stopped candidate:
  `a00eda49aa0f0c3486d27589148361d67e7bdb3e`.
- Base remains `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.
  Tracked/index state was clean at entry. Existing unrelated untracked artifacts
  are preserved. No fetch or remote synchronization/publication claim is made.
- Read current `task.json`, `recovery.json`, copied coordinator assessment and
  preserved `recovery-evidence/summary.json` / `mac-containment-0.json` under
  `.ai-loop/20261007T183516Z-7365/`. Original assessed run:
  `20261007T162747Z-96025`; original control:
  `8c96e7ad60fd5c39f13f0a563afcc6cc26f84753`.
- Original summary SHA-256:
  `28961634d78941ea3f80f81e457896bca8e4c8be8f23a2e75afe42301be8dce3`.
  Original native JSON SHA-256:
  `0bbb5aa95e3887bf2756fe275e1028c7dae1e1dd01f160f2ddd92c02ad3cf5a1`.
- This is the first candidate call of this CURRENT continuation, not a fresh
  implementation. Source/test bytes changed before any runner acceptance attempt.
  Historical results bind only to their old bytes; inherited validation is NONE.

At stopped HEAD a00eda4, related checks, preliminary ordinary Claude review and
both full suites passed historically (venv 203.3 seconds; system Python 330.8
seconds). Native fd_inheritance passed 9 checks; sigint/sigterm/sighup each passed
25 checks. **hung_supervisor remained FAIL**, exit 1. The nine later native
scenarios, including both parent-SIGKILL variants, **DID NOT RUN**. The two older
SIGINT failures remain FAIL as recorded above. These are not new candidate gates.

The captured failing host received READY and CHILD_IDENTITY, then status EOF
without TERMINAL. Its primary was supervisor-lost; getsid ESRCH led to two fresh
matching BSD rows with status 2. The strict zombie verifier rejected them.
Status 2 is not SZOMB=5 and grants no zombie continuity/signaling authority.
The later fixture-cleanup error did not establish a different primary cause.

The latest coordinator correction combines fresh operator identification of the
retained `closed_status` host with captured source order to localize this failure.
That fault intentionally closes status and self-kills the supervisor-owned group;
EOF without TERMINAL is expected. The archive lacks a separate completed-subcase
record. Source order indicates progress through the preceding hung probe but
does not establish a whole-scenario PASS or measured exit-transition duration.
No unexplained supervisor crash or broad diagnostic protocol repair is asserted.
Operator cleanup of historical orphan fixtures is a prerequisite, not a native
containment PASS; this Codex call inspected/signaled no saved process IDs.

### Implemented repair within the existing architecture

`ExitNote` latches a validated NOTE_EXIT across empty reads, while still rejecting
invalid later events. `Anchor.verify(deadline=...)` preserves ordinary live SID
verification and the narrow getsid-ESRCH branch. In that branch it rechecks matching
status-2 observations within the existing shared monotonic cleanup deadline using
bounded non-consuming polling. It requires two consecutive fresh SZOMB=5 rows
matching PID/PGID/UID/PPID/kernel start identity before accepting zombie proof.
Every observed row must match; missing/short/permission rows, drift, unsupported
host/reaper conditions, external reap, unknown status or deadline expiry reject.
There is no retry of an ambiguous or failed identity query.

The real unreaped Popen pin, launch PID=PGID=SID, UID/parent relationship and closed
single-thread/default-reaper premise are checked before and after transition
observation and before signaling. Cleanup decision still precedes supervisor reap;
no released numeric group is signaled. The fixed verification-to-signal race also
waits for actual SZOMB within its original bound rather than treating NOTE_EXIT as
SZOMB. Actual observations are bounded to final/transition kernel rows and counts.
The libproc BSD arg=1 / task arg=0 policy is preserved.

The acceptance controller now freezes a bounded original inventory before cleanup,
retains the actual host Popen, revalidates launch/current kernel identity and host
premises, and sends SIGKILL only to its owned synthetic host. It does not signal
that host's remembered supervisor/group/member IDs. Host death closes the liveness
writer so the supervisor uses its own EOF containment path where applicable. The
controller makes the host-only cleanup decision before host reap, then passively
checks host, supervisor, supervisor group and every observed child/descendant/stress
member for disappearance. Permission/inspection errors, lost-group ambiguity or
unresponsive fixtures remain UNRESOLVED with inventory/handles retained.

Production F1 is unchanged: it retains ownership, handlers and endpoints and cannot
return normally. Controller cleanup is an external synthetic-fixture resolution,
not a production containment success. The explicit F1 test retains its original
two-second resolution bound; ordinary controller cleanup retains its six-second
bound. No handshake, shutdown, group-disappearance or SIGKILL latency threshold is
increased. No extra production process/thread/anchor or spawning callback is added.

Failed-probe exceptions retain their original assertion/primary evidence, exact
active subcase, bounded diagnostic rows and secondary cleanup errors. Failure JSON
records `fixture_cleanup.status`, `original_owned_processes` and actual cleanup
proof. Only confirmed cleanup may record RESOLVED with empty owned_processes;
unresolved inventory stays nonempty. Completed fixture cleanup evidence is retained
for subsequent assertions in that same subcase. Reaped fixture IDs permit passive inspection
only. Direct controller-owned native fixtures also retain cleanup inventory/proof.
Launch/FD setup failures retain ownership rather than losing an incomplete fixture.
The outer runner's independent original-inventory PID-absence guard remains required.

The hung_supervisor scenario records actual entry and completed subcases, covering
hung, closed_status, malformed_status, lost_before_identity, registration_failure,
drain_failure, missing_ACK and subsequent fixed cases. It preserves genuine hung
TERMINAL evidence, intentional closed-status EOF with primary supervisor-lost,
missing ACK, unexpected loss and every subsequent assertion. No production FD,
message, version/schema or public CLI is added. Successful acceptance JSON retains
the exact fourteen-scenario contract; failure JSON remains non-PASS and exits nonzero.

### Added coverage awaiting the outer runner

Deterministic regressions exercise status 2 to two fresh SZOMB rows, already-zombie
proof, status regression during observation, latched NOTE_EXIT, shared-deadline
expiry (including during a final read), identity/start drift, missing/short/permission
rows, external reap and zero-signal rejection. Existing strict protocol version,
schema/unknown-field/EPIPE, reuse/race, FD, binding, import/CLI and descendant tests
remain required. Controller regressions cover original assertion/subcase retention,
real Anchor/Popen ownership checks, SIGKILL-only host signaling before reap, original
inventory and absence proof, released-ID passive checks, bounded unresponsive cleanup,
retained groups, permission failures and missing supervisor identity.

New native failure-path subcases deliberately raise an assertion after observing
real unresolved/closed-status fixtures, then verify retained assertion/inventory,
actual host pin and cleanup/reap/disappearance evidence. A native launch-identity
drift subcase requires blocked cleanup with zero signal/reap, restores only that
same controller-owned fixture's true launch identity, and resolves it under the
normal ownership checks. This is fixed test injection, not an automatic provider
retry or acceptance of ambiguous IDs. These subcases are implemented, NOT RUN here.

| Required gate / disposition | Current changed-byte status |
| --- | --- |
| Syntax, nonzero containment discovery and new regressions | PENDING outer runner |
| Existing Git mutation / A5 / Action regressions | PENDING outer runner |
| Private harness/supervisor, FD inheritance and exec boundaries | PENDING runner/native |
| Real SIGINT / SIGTERM / SIGHUP, timing and repeated signals | PENDING actual Mac |
| Hung escalation, closed-status transition, missing ACK, F1 and fixture failure cleanup | PENDING actual Mac |
| Leader/descendant containment and disappearance | PENDING actual Mac |
| PID/PGID reuse, kernel identity and non-reaping kqueue | PENDING runner/native/security |
| Framing, code/runtime binding and public/import invocation boundaries | PENDING runner/native |
| Parent SIGKILL plain x20 and Popen x20, each <=2 seconds | PENDING actual Mac; no old repetitions inherited |
| Full current-venv Python suite | PENDING outer runner |
| Full /usr/bin/python3 suite | PENDING outer runner |
| Preliminary and final complete-diff independent Claude ordinary review | PENDING outer runner |
| BLOCKER/HIGH/correctness disposition | PENDING independent review and tests |
| Independent security review of anchor/transition/controller/F1–F4 | PENDING; no security approval claimed |
| Exact final-HEAD CI and one Draft PR / READY_FOR_FINAL_REVIEW | PENDING runner; not reached |
| Coordinator/functional acceptance and merge | PENDING; merge not authorized |
| Real product Git ADD, public mutation, approval issuer, audit bypass | None introduced or executed |
| A11b.2.2.2/.3, A11b.3, A11c, A12 | Not started or authorized |

Codex performed guidance/evidence/source inspection, allowlisted edits and source
diff review only. No deterministic test, syntax command, full suite, native probe,
acceptance generator, provider call or process cleanup was run. No fresh acceptance
JSON or test PASS was fabricated. This candidate changes `_git_containment.py`,
`_git_process_identity.py`, `test_git_containment.py`, `run_containment_acceptance.py`
and this report only. No stage, commit, push, merge, worktree, alternate checkout,
global install, task/allowlist/roadmap change or unrelated artifact edit occurred.
The runner must bind fresh results to changed-byte hashes, execute every pinned
gate, and append truthful final evidence. READY_FOR_FINAL_REVIEW, independent
security acceptance, functional acceptance and merge remain separate pending states.
