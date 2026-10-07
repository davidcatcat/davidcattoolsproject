# llmtool A11b.2.2 — B5 Mutation Process Containment & Failure Semantics Specification (2026-10-07)

## Status

**DESIGN / SECURITY GATE ONLY — A11b.2.2 mutation implementation is NOT AUTHORIZED.**

Base main:

`780970ab9e7bb78c471858cd513b28cecc3e5b89`

That main includes PR #43 and therefore:

- A11b.2.1 deterministic Git ADD preflight/state verifier is DONE/merged;
- no `git add` execution exists;
- no public mutation path exists;
- no approval issuer exists;
- no durable mutation audit writer exists;
- A11b.2.2 remains blocked on B5.

This is the B5 design remediation candidate, pending independent targeted re-review.
It is not implementation evidence or authorization to begin any A11b.2.2 slice.
The historical review remains unchanged; §26 maps the remediation to its findings.

## 1. Inputs and inherited findings

This design is constrained by:

- QG-S1 R-EXEC-6: process-tree containment must cover timeout, Ctrl-C, SIGTERM,
  SIGHUP, llmtool crash and normal exit;
- QG-S1 R-AUD-4: product mutation requires durable-before-action audit and fails
  closed if that audit cannot be persisted;
- A11b.2 ADD-only hardening spec and targeted re-review;
- A11b.2.1 merged preflight implementation;
- A11b.2.1 independent review L1:
  macOS `killpg` can raise `PermissionError` after the leader exits, and cleanup must
  not replace the intended typed failure or skip pipe closure;
- A11b.2.1 independent review L2:
  drift tests should be single-variable where practical;
- the existing accepted threat boundary:
  malicious same-UID processes are not an OS-isolated adversary, but accidental/drift
  races must still be detected.

## 2. Scope

B5 covers only the containment and failure semantics required before the internal
ADD mutation primitive can exist.

It does NOT authorize:

- a public CLI mutation command;
- model-triggered execution;
- approval semantics;
- durable audit storage implementation;
- commit/apply/reset/checkout/clean/merge/rebase/push;
- networked Git;
- retry/repair;
- A11c integration;
- A12.

A11b.2.2 remains `GIT_OPERATION / ADD` only.

## 3. Core architecture decision

### 3.1 Direct parent -> Git Popen is rejected

A11b.2.2 MUST NOT implement mutation as:

```text
llmtool parent
  -> Popen(git add)
```

with the parent alone responsible for cleanup.

Reason: on macOS, parent `SIGKILL` cannot be caught and there is no Linux-style
`PR_SET_PDEATHSIG`. A detached Git process could otherwise outlive the llmtool parent.

### 3.2 Required architecture: dedicated mutation supervisor process

The required topology is:

```text
llmtool parent
  |
  | control pipe/socket (parent writer is unique)
  v
mutation supervisor  [separate process/session]
  |
  | owns and monitors
  v
git add child         [separate process group/session]
```

The supervisor is a **process**, not a thread.

Normative properties:

1. the supervisor is in a different session/process group from the llmtool parent;
2. the Git child is in a process group that the supervisor can kill independently;
3. the parent holds the only writer for a parent-liveness control channel;
4. the supervisor holds the read side;
5. the Git child must not inherit any writer capable of keeping the parent-liveness
   channel open;
6. parent death by normal exit, exception, `SIGTERM`, `SIGHUP`, `SIGINT`,
   `os._exit`, crash or `SIGKILL` eventually causes EOF/cancel at the supervisor;
7. after mutation starts, supervisor EOF/cancel causes immediate process-group
   termination;
8. the parent receives the Git leader's PID/PGID, session and start-time identity;
   fallback signaling requires fresh identity/continuity verification (§11), never
   just a remembered group number.

No public command-line entry may expose the supervisor.

### 3.3 Exec-only launch and descriptor ownership — H1

The supervisor MUST be launched with fork+exec semantics, using `subprocess.Popen`
or an independently reviewed equivalent posix_spawn path. `multiprocessing` fork
or forkserver and bare `os.fork()` MUST NOT launch the supervisor. CLOEXEC protects
exec inheritance; it does not prevent a bare fork from retaining a writer.

Create the liveness/control and status pipes with CLOEXEC/non-inheritable FDs
(`pipe2(O_CLOEXEC)` when available, or the supported non-inheritable pipe API with
explicit verification). Ownership after the exec boundary is:

| Channel | Parent owns | Supervisor owns | Git/fake child owns |
| --- | --- | --- | --- |
| Liveness/control | The unique write end | Read end | Neither end |
| Status | Read end | The unique write end | Neither end |

The parent closes its copies of supervisor-only endpoints immediately after spawn.
Use `close_fds=True`; `pass_fds` permits **only liveness-read and status-write**.
It MUST NOT contain liveness-write, status-read, an audit FD or another control FD.
The exec handoff necessarily passes those two endpoints; the supervisor immediately
marks its received FDs non-inheritable before any further spawn. Git/fake children
use `close_fds=True`, empty `pass_fds`, and inherit no control/liveness/status/audit
FD. Every failure path closes all locally owned endpoints without leaking a writer.

Install the parent signal scope before pipe creation (§9). From pipe creation through
supervisor reap, verified group containment and closure of owned control/status
endpoints, the parent MUST NOT fork or spawn another process
(the one reviewed supervisor launch is the only exception), create another thread,
or invoke a callback that can do so. Initial implementation requires the main
thread and a reviewed single-thread host condition. `threading.active_count() == 1`
is a required Python-level check, not proof that no native thread/fork exists;
the trusted host must also guarantee the condition throughout the window. A host
unable to establish it is NOT SUPPORTED before supervisor/child spawn.

The extra `Popen(close_fds=True)` probe in §19 is a harness-only inheritance stress
variant, not permission for production to spawn an unrelated child in this window.
H1 accidental inheritance is an implementation defect, not a same-UID exception.

### 3.4 Supervisor runtime and reviewed code binding — M2

The fixed launch shape is `[sys.executable, '-I', '-m', '<private supervisor module>']`,
with `shell=False`, a separately owned session, DEVNULL stdin, bounded/discarded
startup output, and a trusted neutral cwd outside the target repository. The module
name is a reviewed code constant, never Action/model/config input. The interpreter
and installed llmtool origin must be verified trusted; failure to resolve that
installation under `-I` rejects, without a PYTHONPATH or repository-cwd fallback.

The supervisor receives a fresh environment allowlist: trusted PATH and `LC_ALL=C`
only for this initial design. Do not copy the ambient environment. Exclude HOME,
PYTHONPATH/PYTHONHOME/PYTHONSTARTUP and other PYTHON* variables, user-site loading,
DYLD_*/LD_* injection, credential/agent/ASKPASS variables and proxies. Git receives
its separate generated environment from §6, not ambient overrides.

The internal handshake binds protocol version, action/preflight digests and a
**reviewed llmtool code digest**. Use a deterministic manifest of exact artifact
paths and SHA-256 values, pinned by the trusted caller's reviewed release/checkout;
do not authorize an arbitrary self-consistent manifest supplied in an Action.
At minimum bind mutation/preflight, supervisor, protocol/identity helper modules,
action contract, errors, package initialization and any project module on the
containment/error path. Also bind the selected interpreter/installation identity.

Before READY or child spawn, the supervisor independently verifies loaded module
origins and recomputes those artifact hashes against the bound manifest; the parent
verifies the same digest in READY. Recheck the relevant code bindings before child
spawn; mismatch fails closed without spawning a child. This detects deployment/code
drift under the trusted-installation premise, not execution isolation from malicious
same-UID replacement or arbitrary installed Python startup hooks.

Invocation via `-m`, subprocess, runpy or direct import without the valid internal
FD ownership, peer/launch binding, protocol and reviewed-digest handshake MUST fail
closed before child spawn. TTY/stdin input, arbitrary JSON, a code digest alone or
an A11b.1 parsed Action is not a valid handshake or approval. No standalone hidden
CLI/debug mutation path, approval bypass or executable argv option may be added.

## 4. Parent/supervisor control protocol

Use a bounded, versioned internal protocol.

Both directions use a four-byte unsigned big-endian length prefix and a maximum
payload of 128 KiB per frame; zero/oversized lengths reject before allocation.
Payloads use strict fixed-schema JSON (duplicate keys, unknown fields, nonfinite
numbers and invalid types reject), with depth <=8 and <=4,096 values. Use bounded
nonblocking read/write loops with select/kqueue and monotonic deadlines. Handle
partial prefix/payload reads and writes; mid-message EOF is cancellation, never a
valid truncated frame. Clean EOF is parent/supervisor loss as appropriate. Unknown
protocol version or message type fails closed. No pickle/eval or executable data.

The protocol must carry only deterministic internal fields. It must not carry:

- raw Git stdout/stderr;
- file contents;
- environment dumps;
- credentials;
- arbitrary command strings.

Allowed request data is limited to information required to re-run the existing
preflight and bind the mutation, for example:

- protocol version;
- reviewed code-manifest digest and internal launch/peer binding;
- canonical Action JSON/digest;
- expected preflight binding values;
- bounded timeout.

The supervisor MUST reconstruct the fixed ADD command from reviewed code. It must not
accept arbitrary argv from the parent.

The parent-liveness/control channel stays open for the complete mutation window.

The sequence is one START/handshake, READY, spawn, CHILD_IDENTITY, then terminal
status. After START, the parent can send only CANCEL, at most once; it cannot send
another action or change timeout/argv/config. Status types and counts are fixed and
bounded: READY, CHILD_IDENTITY, optional CANCEL_ACK, terminal status. CANCEL_ACK
means receipt only; it does not prove the child group has disappeared.

BrokenPipeError/EPIPE on CANCEL or status delivery means peer loss. It MUST enter
the same containment path, not escape and skip cleanup or replace the original
stable outcome. No retry/reconnect/replay of the request is allowed.

The supervisor must treat:

- EOF;
- explicit CANCEL;
- malformed/oversized protocol;
- parent protocol failure

as fail-closed cancellation.

Initial reviewed deadlines are at most 2 seconds for handshake, 1 second for
CANCEL_ACK, and 5 seconds total for parent shutdown/escalation. Use one monotonic
shutdown deadline, not repeated waits that restart the budget. Parent-loss EOF
cleanup must meet the separate <=2-second macOS group-disappearance probe bound
(§19). Any deadline change requires renewed review before execution.

## 5. Mandatory pre-spawn revalidation

The supervisor must not trust an earlier parent preflight as sufficient.

Before spawning Git, inside the supervisor:

1. parse/revalidate the canonical Action;
2. run `preflight_git_add(action)` again;
3. compare the new result against the parent-bound expected preflight evidence;
4. require exact agreement for:
   - action digest;
   - repository root/device/inode;
   - HEAD;
   - object format;
   - pre-index SHA;
   - state digest;
   - authorized paths;
   - semantic index baseline;
   - bound blob OIDs/modes;
5. check the parent control channel is still alive immediately before spawn;
6. if any field differs, do not spawn Git.

This is the final TOCTOU gate before mutation.

## 6. Git child rules

The Git child remains the already-reviewed deterministic plan:

```text
git
--no-pager
[fixed reviewed -c pins]
add
--
<authorized literal paths>
```

The supervisor must build this from the canonical preflight/action. It must not accept
arbitrary Git subcommands.

The mutation environment:

- is a fresh allowlist;
- preserves the A11b.2.1 Git hardening pins;
- omits `GIT_OPTIONAL_LOCKS=0` so Git can take the required index lock;
- inherits no secrets/credential/proxy/loader variables;
- uses `shell=False`;
- stdin is DEVNULL;
- stdout/stderr are bounded and never surfaced raw;
- `start_new_session=True` or equivalent creates a killable Git process group;
- `close_fds=True`;
- empty `pass_fds`; no control/audit/liveness/status FD is inherited by Git.

## 7. Repository-code execution premise

A11b.2.2 does not get a general OS sandbox exemption automatically.

For this ADD-only subset, no repository-controlled executable path may remain:

- filters: reject;
- transforming attributes: reject;
- hooks: disabled;
- fsmonitor: disabled;
- external diff/textconv: irrelevant/disabled;
- credential/SSH/GPG helpers: neutralized;
- alternate worktree/config include mechanisms: reject;
- protocols: deny;
- maintenance/gc: disable.

If implementation review finds any repository-controlled executable path reachable
during the actual ADD command, A11b.2.2 is BLOCKED and must add an OS sandbox or remove
that path.

## 8. Containment state machine

Once Git is spawned, the supervisor owns the mutation lifecycle.

### 8.1 Normal running state

Supervisor simultaneously monitors:

- parent control/liveness channel;
- Git stdout;
- Git stderr;
- wall-clock deadline;
- Git process exit.

The Git leader's exit notification MUST NOT reap it. On macOS, use kqueue
`KQ_FILTER_PROC` / `NOTE_EXIT`, or an independently reviewed non-reaping equivalent.
Register/handle fast-exit races while the owned leader is still unreaped; notification
setup failure enters group cleanup before reap. Stream EOF is not sufficient proof
of leader exit or tree disappearance: descendants can retain stream FDs.

Before the group-kill decision is complete, do not call Git `Popen.poll()`, `wait()`,
`communicate()`, context-manager exit, consuming waitpid/waitid, or another path that
can reap. Keep an explicit unreaped/cleanup-decided/reaped lifecycle, and never
signal a remembered PID/PGID after releasing its ownership. Initial operation must
prevent SIGCHLD auto-reaping/custom reapers; scoped default disposition and exact
restoration require the same reviewed single-thread ownership condition (§3.3).
Unsupported non-reaping/identity facilities reject before child spawn; do not
silently substitute ordinary poll/wait on macOS.

### 8.2 Cancellation conditions

Any of the following triggers containment cleanup:

- parent control EOF;
- parent CANCEL;
- timeout;
- stdout limit exceeded;
- stderr limit exceeded;
- supervisor-side internal exception;
- direct SIGINT/SIGTERM/SIGHUP delivered to supervisor.

Cancellation cleanup is bounded and deterministic.

### 8.3 Required kill behavior

For cancellation, the supervisor must stop the Git process group immediately.

A11b.2.2 does not wait for an untrusted graceful shutdown that could complete additional
mutation after authorization/control is gone.

Required sequence:

1. while the owned leader remains unreaped, attempt process-group `SIGKILL`;
2. catch at least `ProcessLookupError` and macOS `PermissionError`, retaining bounded
   secondary diagnostics; neither alone proves that the whole tree disappeared;
3. if group signaling failed and the still-owned leader exists, attempt direct
   leader kill as containment of that leader only, not proof of descendant cleanup;
4. finish the group-kill decision, then wait/reap the leader with a deadline;
5. verify no running owned member remains, and check group disappearance with the
   platform identity/liveness rules; never send another kill to a stale/reaped ID;
6. close stdout/stderr/control/status and monitoring FDs in nested `finally` blocks;
7. never let cleanup exceptions replace the primary stable failure; record cleanup
   incompleteness separately and escalate to §11 rather than claim containment.

Cleanup is idempotent. Repeated signals/cleanup calls cannot cause a second kill of
an already reaped identity. Deadlines apply to draining, signaling verification,
reaping and status delivery, not just the main Git wait.

This closes A11b.2.1 review finding L1 before mutation code can reuse the runner.

### 8.4 Leader exits normally

When the Git leader exit is observed **without reap**:

- the supervisor must still attempt process-group cleanup before declaring the child
  tree gone;
- `PermissionError` / `ProcessLookupError` are handled as above;
- cleanup/killpg decision MUST precede reap, even if exit is 0 or streams are EOF;
- no descendant may remain running before a containment-complete result.

An unreaped leader pins its PID/PGID through this decision. Reaping first and later
signaling the remembered number is forbidden even if most local probes happen to
work. Tests must distinguish zombies from running descendants and eventual group
absence; a PermissionError is never unconditionally translated into "tree gone".

This explicitly covers the TR-3 / leader-exits-descendant-survives case.

Actual hardened Git ADD is expected to spawn no repository-controlled helpers, but the
containment layer must not rely solely on that assumption.

## 9. Parent signal handling

Before creating pipes or spawning the supervisor, the parent installs scoped handling for:

- SIGINT;
- SIGTERM;
- SIGHUP.

Handlers only record the first observed cancellation in bounded state. They do not
kill, wait, reap, perform pipe/filesystem I/O or raise an exception. Normal Python
control flow sends the bounded CANCEL frame; repeated signals cannot bypass cleanup.

The mutation call then:

1. sends CANCEL if possible and starts the bounded acknowledgement/cleanup wait;
2. independently monitors supervisor process exit as well as status/control I/O;
3. on wait expiry, missing acknowledgement or missing tree-gone proof, performs the
   mandatory §11 escalation;
4. reaps the supervisor and confirms owned Git containment before closing endpoints
   and restoring previous signal/SIGCHLD dispositions;
5. only then raises/returns the stable typed interruption outcome.

Rules:

- signal handlers are restored on every exit path;
- mutation from a non-main Python thread is rejected unless an independently reviewed
  equivalent signal/liveness mechanism exists; initial support also requires §3.3's
  reviewed single-thread/no-extra-spawn condition;
- model/repository text cannot affect signal behavior.

Safety on uncatchable parent death comes from liveness EOF and correct FD ownership,
not from the signal handler. The handler mainly preserves typed outcomes. Signals
before installation or after restoration use their normal disposition; no child
has started before installation, and normal restoration occurs only after cleanup.
Unconfirmed containment cannot take the routine handler-restoration/return path.

## 10. Parent SIGKILL / crash

`SIGKILL` cannot be handled by the parent.

Therefore parent liveness is enforced by the control channel:

```text
parent SIGKILL / crash
  -> parent writer closes by OS
  -> supervisor observes EOF
  -> supervisor kills Git process group
  -> supervisor reaps Git
```

There is an unavoidable race where Git may complete between the last liveness check
and supervisor observation of EOF.

That race is not hidden. It is handled by durable intent + post/recovery state
classification (§14–§16).

## 11. Supervisor failure

### 11.1 Identity-bound fallback — H2

Immediately after Git spawn, the supervisor MUST capture and send CHILD_IDENTITY
before ordinary output draining or other work. Bind the leader PID, PGID (= leader
PID for the new session), SID, UID and OS-reported start-time identity to the current
action/launch. Record the supervisor's own owned process identity too. On macOS,
use a reviewed kernel process-information facility for start time/group/session;
neither wall-clock spawn time, a `ps` string nor `kill(pid, 0)` alone is identity.

The live parent detects supervisor death using process-exit detection in parallel
with status EOF. A retained status writer must not hide process exit. Use non-reaping
notification where supervisor SIGKILL may still be needed. If a parent poll/waitpid
has reaped the supervisor, mark ownership released and never kill its remembered PID.

If:

- supervisor exits unexpectedly;
- supervisor status channel closes unexpectedly;
- supervisor sends malformed status;
- CANCEL has no acknowledgement by its deadline;
- the bounded cleanup wait expires;
- any exit path lacks valid proof that the owned Git group has disappeared

while the parent remains alive, it must use §11.2, never directly return an ordinary
interrupted/success result while either owned process tree may still be running.

Fallback signaling is authorized by a fresh identity check, not a remembered PGID:

| Fresh observation | Required handling |
| --- | --- |
| Leader PID exists and bound start time, UID, PGID and SID all match | Revalidate immediately before signaling; perform bounded owned-group SIGKILL/verification only under the reviewed continuity rule below |
| Leader identity differs, or identity read/permissions are ambiguous | Do not kill that PID or group; `STATE_UNKNOWN / USER_ACTION_REQUIRED`; never treat a reused group as owned |
| Leader absent and original group is verifiably still present | Require kernel-backed continuity evidence, such as an already witnessed member with matching PID/start time/UID and original PGID/SID; then perform the same identity-bound bounded cleanup |
| Leader absent and original group proved gone | No signal; record containment evidence and proceed to post-state verification |
| Leader absent but only numeric group existence is known | Not sufficient: a new group could reuse the number; no kill, unconfirmed containment and user action required |

For the leader-absent/live-group case, the matched member must demonstrate continuity
of the **original** group; enumerating arbitrary current members or seeing a successful
`killpg(pgid, 0)` does not establish that continuity. A member continuing in the original
group retains its group ID; the reviewed platform procedure must establish that it
has not disappeared between verification and signaling. A start-time snapshot is not
an atomic process handle. The implementation must document and adversarially test the
identity-check-to-signal race; if continuity cannot be established on that platform,
reject the capability as NOT SUPPORTED before mutation, rather than blindly kill.
No process deliberately escaping its group/session is claimed contained here.

### 11.2 Hung/dead supervisor escalation — M1

On every trigger above, the parent MUST, in order:

1. SIGKILL the still-owned, unreaped supervisor if it has not exited; reap it with
   bounded handling. A reaped supervisor is never signaled again.
2. apply §11.1's freshly verified identity-bound Git group cleanup, checking bounded
   disappearance and preventing kill of an unrelated/reused PID or PGID;
3. once containment is confirmed, close owned control/status endpoints, then safely
   collect repository post-state and apply §14's classification;
4. retain the primary interruption/failure and bounded secondary cleanup evidence.

CANCEL_ACK, status EOF, supervisor exit, or a successful leader-only kill is not a
substitute for group-gone proof. Unresolved identity/cleanup means
`MUTATION_STATE_UNKNOWN / USER_ACTION_REQUIRED` with `containment_confirmed=false`.
There is no routine return or restoration while owned processes may still live:
the trusted host must retain containment responsibility and require external/operator
resolution. Do not retry, delete locks or signal unrelated processes to force completion.
If the host cannot supply that unresolved-containment handling, the engine is NOT
SUPPORTED there. A readable expected index cannot override this containment failure.

### 11.3 Residual identity-delivery window — L2

From child spawn until CHILD_IDENTITY is received by the parent, supervisor death
can leave the parent without a safe fallback identity. Send identity immediately,
but do not claim that this eliminates the window. Missing identity means unknown
containment/state, not permission to guess PID/PGID or a completed cancellation.
Preserve durable intent for future recovery and require user/external handling;
no ordinary return while the original child may still be live. Harness fault injection
must cover supervisor death before delivery. A suspended/acknowledged child-start
design would require a separate review; it is not implemented or assumed by this spec.

This provides two-sided containment:

- parent death -> supervisor cleans Git;
- supervisor death/hang after identity delivery -> live parent performs identity-bound
  fallback; unresolved identity/delivery remains an explicit non-complete failure.

Simultaneous forced death of both parent and supervisor is outside the process-local
guarantee and requires an external service manager/OS facility. It is not claimed solved.

A malicious same-UID actor that intentionally kills both processes is outside the
accepted isolation boundary.

## 12. Stable failure taxonomy

Do not overload A11b.2.1 preflight categories for mutation lifecycle failures.

A11b.2.2 should define stable internal outcomes/codes at least equivalent to:

- `MUTATION_START_FAILED`;
- `MUTATION_TIMEOUT`;
- `MUTATION_INTERRUPTED`;
- `MUTATION_PARENT_LOST`;
- `MUTATION_SUPERVISOR_LOST`;
- `MUTATION_OUTPUT_LIMIT`;
- `MUTATION_GIT_NONZERO`;
- `MUTATION_POSTVERIFY_FAILED`;
- `MUTATION_LOCK_REMAINS`;
- `MUTATION_STATE_UNKNOWN`.

Raw stdout/stderr/config/path content must not be embedded in normal error messages.

## 13. Post-state verification

After the Git process tree is confirmed dead, parent-side code performs post-state
verification using the reviewed semantic index rules.

No result is SUCCESS merely because Git returned 0.

The post-verifier records:

- canonical repository root/device/inode and safe post-read identity;
- bound pre-HEAD OID and attached symbolic branch ref name/OID;
- post-HEAD OID and branch ref name/OID, or a fixed unreadable/drift indicator;
- pre-index SHA;
- post-index SHA if readable;
- pre semantic index;
- post semantic index if readable;
- authorized target expected entries;
- whether `.git/index.lock` exists;
- Git exit code if known;
- interruption/containment reason.

Expected post-state remains:

```text
complete pre semantic index
with only authorized target entries replaced
by exact expected path/mode/blob OID entries
```

No new unrelated entry is allowed.

Capture the attached branch ref name and OID in the internal pre-spawn evidence,
revalidate in both parent/supervisor binding checks and post-verification; this is
internal observed evidence, not an Action schema or caller-supplied branch override.
Parent-side fixed read-only Git subprocesses are allowed only after supervisor reap,
confirmed group containment and control/status endpoint closure, outside §3.3's
no-extra-spawn critical window. Unresolved containment permits only provisional
bounded in-process inspection; do not start another process or claim a final outcome.
Use only the reviewed fixed read subset, with no-follow/bounded repository/index
reads. Do not trust a post-index snapshot if root/HEAD/branch binding has drifted.
Raw pre/post index SHA and cache/stat changes are forensic evidence only; semantic
outcome uses exact ordered path/mode/OID/stage/flag entries and authorized changes.

## 14. Failure/recovery classification matrix

### 14.1 Total semantic decision table — M3

Containment confirmation is a prerequisite. If it is absent (§11), the result is
`STATE_UNKNOWN / USER_ACTION_REQUIRED`, `containment_confirmed=false`; any observation
while Git may still run is provisional and cannot authorize an APPLIED result.
Once containment is confirmed, take **the first matching row** below and stop.

| Priority | Condition | Semantic outcome |
| --- | --- | --- |
| 1 | `.git/index.lock` exists, or index is missing, unreadable, unsafe, invalid or changes during its bounded safe read | `USER_ACTION_REQUIRED` |
| 2 | Root/layout identity is unsafe/drifted, or HEAD / attached branch ref name or OID is unreadable or differs from the bound pre-state | `USER_ACTION_REQUIRED` |
| 3 | Complete semantic post-index equals the exact expected authorized post-state | Exit 0 and no interruption/containment/control failure: `APPLIED`; otherwise (nonzero, unknown exit, interruption, parent/supervisor loss): `APPLIED_BUT_CONTROL_INTERRUPTED` |
| 4 | Complete semantic post-index equals pre-state **and pre-state differs from expected post-state** | `NOT_APPLIED`, even if Git exit was 0; a nominal-success mismatch remains a post-verification failure |
| 5 | Any other readable semantic state or unexpected verifier failure | `USER_ACTION_REQUIRED` with stable `MUTATION_STATE_UNKNOWN`/post-verification diagnostics |

Rows are prioritized, exhaustive and mutually exclusive after first-match selection.
A remaining lock or HEAD/ref drift wins even when index entries match expected.
Raw index equality or inequality is never a condition: raw bytes may change from
stat/cache refresh while semantic pre-state is unchanged. No unrelated staging,
unexpected target OID/mode/path/stage/flags or unexplained semantic change passes.

### 14.2 No-op action (`expected == pre`)

A11b.2.1 can accept an already-matching file with no staged changes; do not change
its schema or retrospectively reject these proposals here. Row 3 handles the no-op
before row 4. A clean exit0 with exact expected state is APPLIED; nonzero/unknown
exit or interruption with the same exact state is APPLIED_BUT_CONTROL_INTERRUPTED.
NOT_APPLIED is impossible for a no-op by row 4's explicit inequality guard.
Record `no_op=true` in internal outcome evidence; APPLIED describes satisfaction
of the bound postcondition, not proof that ADD wrote new bytes.

### 14.3 Failure and recovery consequences

`APPLIED_BUT_CONTROL_INTERRUPTED` is **not task success** and must not trigger a
blind retry. `NOT_APPLIED` records known semantics, not permission to reuse old
approval. Higher layers may later assess a fresh Action/state/approval; A11b.2.2
does not retry. The semantic outcome and primary lifecycle failure are separate
fields, so cleanup/post-verification does not erase the original interruption.

Rules:

- STOP;
- preserve bounded evidence;
- no automatic `reset`, `restore`, `checkout`, `clean`, or lock deletion;
- no retry;
- later recovery/approval layers decide next action.

## 15. index.lock policy

A11b.2.2 MUST NOT delete `.git/index.lock`, including locks likely created by its own
killed Git process.

Reason:

- ownership of a remaining lock cannot be proved safely enough after abnormal process
  termination within this slice;
- deleting it would convert a containment problem into destructive recovery behavior.

A remaining lock is a hard `USER_ACTION_REQUIRED` state.

The fixed user-action message may explain that a remaining index lock blocks Git
operations; do not expose contents or presume the lock belongs to llmtool. A lock
held by the user's own concurrent Git is still conservatively preserved (N2).

This preserves the A11b.2 spec's no-auto-lock-removal rule.

## 16. Durable audit interaction

R-AUD-4 remains a hard **product runtime** prerequisite.

The future production execution order is:

```text
policy + approval complete
  -> durable mutation-intent audit event
  -> if durable write fails: STOP, no mutation
  -> call internal mutation engine
  -> post-state classification
  -> durable terminal/recovery audit event
```

The pre-action durable event must bind at least:

- contract digest;
- action digest;
- approval ID;
- repository identity;
- expected HEAD;
- pre-index SHA;
- attached branch ref name/OID, semantic pre-state and expected-post-state digest;
- reviewed code-manifest digest;
- authorized paths/scope;
- pre-state digest.

Terminal/recovery audit records:

- outcome classification;
- exit code if known;
- post-index SHA if known;
- post-verify code;
- containment reason;
- observed leader PID/PGID/start-time identity and containment-confirmed flag;
- no raw tool output or environment.

### 16.1 A11b.2.2 internal-engine exception

A11b.2.2 may test the private engine against disposable synthetic repositories before
the audit subsystem exists because those are engineering fixtures, not a product
side-effect path.

However:

- the engine stays private/internal;
- no production CLI/orchestration/provider/router module may import/call it;
- no hidden debug flag exposes it;
- no "temporary audit bypass" or fake approval type is added.

Before any product runtime caller is added, R-AUD durable-before-action and A11b.3
approval semantics are mandatory.

## 17. Crash recovery contract for future audit layer

A durable intent event with no terminal event means recovery is required.

On a later restart, recovery logic may inspect state but must not mutate automatically.

Classification:

- first establish that the original owned tree is gone; otherwise record unknown
  containment and require operator/external resolution, without automatic mutation;
- apply §14's lock/root/HEAD/branch gates first;
- semantic index == expected post-state -> APPLIED_BUT_CONTROL_INTERRUPTED (terminal
  control result is missing), including `expected == pre` no-op;
- semantic index == pre-state AND pre != expected -> NOT_APPLIED;
- anything else -> USER_ACTION_REQUIRED.

Raw byte differences cannot change this semantic ordering. Missing trusted intent
bindings prevent a definite outcome. Recovery inspection never repairs output or
deletes a lock.

This is how the parent-SIGKILL completion race is made explicit rather than guessed.

A11b.2.2 does not implement startup recovery scanning; it defines the evidence contract
future A11b recovery code must consume.

## 18. A11b.2.2 implementation slicing

**Current authorization: documentation remediation only. No slice below may start
in this task.** The historical review allowed 2.2.0 prerequisite cleanup in isolation;
that is not a current user instruction to implement it. Targeted re-review and a
separate explicit implementation authorization are required for later work.

### A11b.2.2.0 — prerequisite cleanup/evidence hardening

Before supervisor work:

- fix A11b.2.1 L1:
  - catch `PermissionError` + `ProcessLookupError`;
  - preserve original typed error;
  - nested finally closes pipes deterministically;
- split/rename L2 drift tests so config/info-attributes/index/root drift are
  single-variable evidence tests.

No mutation.

### A11b.2.2.1 — supervisor containment harness

Implement only:

- parent/supervisor control channel;
- supervisor lifecycle;
- fake child process-group ownership;
- parent-death EOF behavior;
- signal forwarding/cancellation;
- timeout/output/internal-error containment;
- leader-exit descendant cleanup;
- supervisor-death parent fallback;
- stable containment outcomes.

Also implement and independently demonstrate the exec/FD/runtime binding in §3,
framing/deadlines in §4, non-reaping identity lifecycle in §8, and hung-supervisor /
reuse / residual-window handling in §11. The private fake-child test seam must not
accept arbitrary commands from public runtime or protocol input. No OS support or
containment guarantee is claimed until those tests and real Mac probes pass.

**No `git add` execution in this slice.**

This slice must receive independent security review before 2.2.2.

### A11b.2.2.2 — internal Git ADD + post-state verifier

Only after 2.2.1 review PASS:

- supervisor re-runs preflight;
- fixed hardened ADD spawn;
- actual ADD only on disposable test repositories;
- semantic post-state verification;
- failure/recovery classification;
- no public runtime import.

### A11b.2.2.3 — failure/security regression gate

- targeted failure injection;
- A5 regression;
- A11b.1/2.1 regression;
- full suite;
- macOS local signal/SIGKILL probes;
- independent code/security review.

Do not start A11b.3 automatically after merge.

## 19. Required containment tests before real Git ADD

A11b.2.2.1 tests use a fake child executable / bounded synthetic child tree.

They must prove:

### A11b.2.1 L1 / cleanup (distinct from this review's protocol L1)

- leader already exited + `killpg` PermissionError does not leak raw OSError;
- intended typed failure survives cleanup;
- stdout/stderr/control/status FDs close;
- no child remains.

### Timeout/output/internal failure

- timeout kills process group;
- stdout limit kills process group;
- stderr limit kills process group;
- internal drain exception kills process group;
- malformed supervisor status fails closed and parent uses identity-bound group
  cleanup, never a remembered PGID alone.

### Exec/FD ownership and reviewed runtime — H1 / M2

- observe exec-only supervisor launch, exact `close_fds` / `pass_fds`, and both
  unused-end closures; reject bare fork, multiprocessing fork/forkserver and an
  inheritable liveness writer configuration;
- fake child enumerates its open descriptors: no control/liveness/status/audit FD;
- multi-threaded entry rejects before supervisor spawn; prove the production
  critical window has no additional spawn/fork or asynchronous callback path;
- hostile PYTHONPATH/PYTHONHOME/PYTHONSTARTUP, loader variables, user site,
  credentials and proxies cannot enter the fresh supervisor environment;
- wrong protocol/code digest, changed artifact before child spawn and unexpected
  module origin fail before child spawn; observe independent supervisor hashing;
- private `-m` entry without legitimate internal handshake, including a
  self-consistent arbitrary digest/Action supplied from stdin, cannot spawn;
- isolated module-resolution failure fails closed; no repository-cwd/PYTHONPATH
  workaround is allowed.

### Non-reaping ownership and reuse — H2

- record ordering: non-reaping exit notification -> group cleanup decision ->
  leader reap; fail the test if Git `poll`, `wait`, `communicate` or another reaper
  is used before the group decision;
- cover fast leader exit before monitor registration, registration failure,
  inherited SIGCHLD auto-reap/custom reaper rejection or reviewed scoped handling;
- cleanup is idempotent; after reap, no second signal uses the released PID/PGID;
- fake kernel identity mismatch/start-time reuse must result in zero kill calls
  against the replacement, plus STATE_UNKNOWN / USER_ACTION_REQUIRED;
- leader absent but witnessed original group continuity permits owned cleanup;
  numeric group existence without continuity does not; unknown continuity must
  never produce a false containment-success result;
- inject disappearance/reuse between identity checks and signal decision; prove
  the reviewed platform continuity rule or reject platform support. A mocked
  matching snapshot alone does not establish OS race safety.

### Hung supervisor — M1

- fake supervisor ignores CANCEL and remains alive; missing acknowledgement,
  bounded wait expiry and missing group-gone proof each trigger escalation;
- parent SIGKILLs and reaps the still-owned supervisor, performs identity-bound
  Git group cleanup and classifies repository state;
- no normal return/handler restoration while an owned process may remain;
  an identity-uncertain case records unresolved containment and requires external
  resolution instead of killing a replacement process.

### Protocol framing — B5 review L1

- fragmented prefix/payload reads and writes reconstruct one bounded frame;
- zero/oversized length, duplicate/unknown fields, unknown version/type and
  mid-message EOF fail closed without allocation beyond the bound;
- EPIPE/BrokenPipeError in either direction preserves cleanup and primary outcome;
- nonblocking/select deadlines cover stalled partial messages, full pipes,
  handshake, cancel acknowledgement and terminal reporting;
- no second START or post-START action/config replacement is accepted.

### Descendants

- fake leader forks/spawns a long-lived descendant then exits;
- supervisor's normal-exit cleanup kills the descendant;
- no descendant survives after result.

### Parent signals

For SIGINT, SIGTERM and SIGHUP:

- spawn a harness parent process running the containment call;
- deliver the real signal to the parent;
- supervisor receives cancel/EOF as designed;
- fake Git group is dead;
- signal handlers are restored/exit behavior is deterministic;
- no process remains.
- deliver signals before pipe creation, during supervisor spawn and during
  cleanup; record handler installation before spawn and restoration only after
  supervisor reap and confirmed group cleanup; repeated signals cannot interrupt
  that cleanup or reset its deadline (L3).

### Parent SIGKILL

On actual macOS host:

1. spawn outer harness parent;
2. wait until supervisor and fake Git PIDs/PGID are reported;
3. send `SIGKILL` to **parent only**;
4. verify supervisor observes EOF;
5. verify fake Git process group terminates;
6. verify supervisor exits;
7. verify no descendant remains.

Run on the real macOS host **outside the sandbox**. Record OS release/build, Python
version and exact tested code digest. Run at least **20 repetitions for each**:

- plain parent;
- harness-only extra unrelated `Popen(close_fds=True)` child; it must not retain
  the writer. This stress test does not authorize extra production spawns.

For every repetition, measure monotonic latency from parent SIGKILL to confirmed
Git group disappearance: **<= 2 seconds**. Verify the reported leader/descendant
PIDs and PGID with `kill(pid, 0)` / `killpg(pgid, 0)` plus identity-bound ownership
evidence; `ps` output is not the gate. PermissionError/EPERM means inconclusive,
not absent. Distinguish temporary zombies from executing descendants, require
eventual group disappearance within the bound, and ensure the harness safely
cleans only its own processes even when the assertion fails.

This is a required local Mac gate. Mock-only, sandbox-only or historical review
probe evidence does not substitute for testing the future implementation.

### Parent normal/crash-like exit

- parent closes/loses control channel without explicit cancel;
- supervisor treats EOF as parent loss and cleans the child group.

### Supervisor loss

- parent receives full child process identity;
- kill supervisor only;
- live parent detects supervisor loss;
- parent freshly verifies original child/group identity before signaling;
- no child remains.

Also inject supervisor loss between Git spawn and CHILD_IDENTITY delivery (L2).
Require explicit unresolved containment/state-unknown evidence, no guessed PGID,
and the §11.3 external-resolution path. Never label that residual-window case a
successful fallback; assess it independently before authorizing real mutation.

## 20. Required actual-Git tests after containment PASS

A11b.2.2.2 only:

- clean authorized tracked modification -> exact target staged;
- authorized new untracked file -> exact target staged;
- unrelated unstaged/untracked work remains untouched;
- no unrelated path staged;
- target OID/mode matches preflight bound bytes;
- `post-index-change` hook marker not executed;
- configured filter/transforming attribute never reaches mutation because preflight rejects;
- stale preflight -> no spawn;
- HEAD/index/file drift -> no spawn;
- pre-existing index.lock -> no spawn;
- Git nonzero + semantic pre-state != expected -> NOT_APPLIED, even when raw
  index cache/stat bytes have changed;
- exit0 + expected state -> APPLIED;
- nonzero/interruption + expected state -> APPLIED_BUT_CONTROL_INTERRUPTED;
- unexpected semantic state -> USER_ACTION_REQUIRED;
- remaining index.lock -> USER_ACTION_REQUIRED and lock preserved.
- no-op (`expected == pre`) + clean exit0 -> APPLIED; no-op + nonzero/interruption
  -> APPLIED_BUT_CONTROL_INTERRUPTED, never NOT_APPLIED;
- exit0 + semantic pre-state != expected -> NOT_APPLIED with a separate
  post-verification failure; exit code alone cannot prove staging;
- HEAD, symbolic branch ref name or ref OID drift with an otherwise expected
  index -> USER_ACTION_REQUIRED;
- index.lock or unsafe/unreadable index takes precedence over an expected
  semantic state; root identity/layout drift also invalidates the binding;
- all first-match table branches are mutually exclusive; raw SHA differences
  cannot override any semantic result or lock/HEAD gate;
- unresolved containment prevents an ordinary completed mutation result even
  if a provisional index read appears expected.

No test may use a real user repository index.

## 21. Public/import boundary

Through all A11b.2.2 slices:

- no existing production CLI/router/orchestrator/provider code imports the mutation
  execution entry;
- tests may call private/internal APIs;
- no argparse/subcommand is added;
- no model output can invoke mutation;
- no approval object is interpreted by the mutation engine.

Add AST + string/import checks covering `importlib`, `__import__`, private module
strings passed with `-m`, `subprocess.Popen` / `subprocess.run`, and
`runpy.run_module` / `runpy.run_path`. Follow indirect wrappers as well as direct
imports. Ordinary CLI/entrypoint/chat/orchestration/benchmark/provider/router paths
must not reach the private supervisor or execution entry through any of them.
Only the separately reviewed internal engine may launch the fixed supervisor
module. Standalone private-module entry remains handshake-gated (§3.4); scanning
does not replace that runtime rejection. No debug invocation or audit/approval
bypass is allowed (B5 N4 and A11b.2.1 N4).

## 22. OS/platform policy

A11b.2.2 initial support is POSIX/macOS-first, matching the existing safe subset.

Required platform behavior:

- actual macOS parent-SIGKILL containment probe must PASS before mutation code merges;
- Linux support may be added only with equivalent process/liveness tests;
- native Windows mutation remains NOT SUPPORTED unless a reviewed job-object/process-tree
  design exists;
- root execution rejects.
- non-reaping exit notification, controlled SIGCHLD/reaping semantics and the
  reviewed kernel start-time/group-continuity identity mechanism are required;
  missing capabilities -> NOT SUPPORTED before child/mutation spawn;
- Linux may add `PR_SET_PDEATHSIG` as defense in depth, never as a substitute for
  EOF, identity-bound fallback or equivalent tests (N3).

## 23. No retry / no automatic rollback

Containment failure is not retried inside A11b.2.2.

A changed index invalidates the earlier state binding.

Even if ADD would be idempotent for some inputs, retry requires later policy/approval
logic to re-evaluate a fresh action/state.

Automatic rollback is forbidden in this slice.

## 24. Definition of Done for B5 design gate

B5 design is review-ready when an independent reviewer can answer YES to all:

- dedicated supervisor is required;
- exec-only FD ownership and single-thread/no-extra-spawn premises make parent
  `SIGKILL` observable through liveness-channel EOF;
- supervisor loss/hang uses identity-bound fallback/escalation, with the
  pre-identity-delivery residual window explicitly unresolved rather than hidden;
- group cleanup precedes leader reap and no released PID/PGID is signaled;
- supervisor interpreter/environment/code digest/handshake are bound;
- framing, EOF/EPIPE and every wait have bounded, nonblocking cleanup semantics;
- Git descendants cannot survive a reported contained normal/abnormal result;
- L1 cleanup behavior is fixed before mutation;
- actual signals are tested, not only mocked handlers;
- macOS parent-SIGKILL probe is mandatory;
- post-state classification distinguishes APPLIED / NOT_APPLIED /
  APPLIED_BUT_CONTROL_INTERRUPTED / USER_ACTION_REQUIRED;
- index.lock is never auto-deleted;
- durable-before-action audit is a runtime gate, not silently deferred;
- no public mutation path is introduced;
- no real Git ADD starts before containment harness review passes.

This document is a remediation candidate. A design review PASS is not proof of
implementation correctness, OS race safety or completed Mac probes.

## 25. Required independent design/security review

Before any A11b.2.2 production code:

Preserve the existing historical review unchanged. Create a fresh targeted
re-review report, for example:

`reports/llmtool_a11b2_2_b5_containment_spec_targeted_rereview_20261007.md`

Reviewer must explicitly assess:

```text
A11b.2.2 B5 DESIGN REVIEW:
PASS
or
BLOCKED

Reviewed spec head:
<exact SHA>

Dedicated supervisor architecture:
PASS / finding

Parent control-pipe EOF on SIGKILL:
PASS / finding

Supervisor-loss parent fallback:
PASS / finding

Leader-exit descendant cleanup:
PASS / finding

L1 PermissionError cleanup:
PASS / finding

SIGINT/SIGTERM/SIGHUP semantics:
PASS / finding

Actual macOS SIGKILL test requirement:
PASS / finding

Pre-spawn revalidation:
PASS / finding

Post-state classification:
PASS / finding

index.lock policy:
PASS / finding

Durable-before-action audit interaction:
PASS / finding

Public/import boundary:
PASS / finding

Same-UID / simultaneous-death threat boundary:
PASS / finding

New blockers:
NONE / details

May implement A11b.2.2.0:
YES / NO

May implement A11b.2.2.1 containment harness:
YES / NO

May implement real git add (A11b.2.2.2):
NO
```

Even a PASS review does not itself authorize implementation. It permits requesting
separate user authorization for an explicitly bounded slice; current authorization
remains documentation remediation only (§18).

Real `git add` remains prohibited until the 2.2.1 containment harness itself is
implemented, locally probed on macOS, and independently reviewed.

## 26. Remediation traceability and remaining gates

`CLOSED` below means the requested normative text and future test requirements are
present in this remediation candidate. It does **not** supersede the historical
BLOCKED review, assert a new independent PASS or prove runtime containment.

| Review item | Documentation disposition | Normative requirements / future evidence |
|---|---|---|
| H1 liveness-writer inheritance | CLOSED | §3.3, §6, §9, §19: exec-only launch, FD ownership, no extra process, single-thread premises, process-exit detection |
| H2 PID/PGID reuse | CLOSED | §8, §11.1, §19: non-reaping notification, kill-before-reap, kernel start-time/group continuity, replacement rejection |
| M1 hung supervisor | CLOSED | §4, §9, §11.2, §19: bounded escalation, owned supervisor SIGKILL/reap, verified Git cleanup, no ordinary return with unresolved containment |
| M2 runtime/code binding | CLOSED | §3.4, §4, §19, §21: isolated interpreter, fresh environment, reviewed code-manifest handshake, no hidden private CLI |
| M3 total post-state classification | CLOSED | §13–§14, §17, §20: lock/HEAD/ref priority, semantic-only first-match outcomes, explicit no-op rule |
| B5 L1 framing/EPIPE | INCORPORATED | §4, §19: bounded length prefix, partial IO/EOF, nonblocking deadlines, cleanup despite broken pipe |
| B5 L2 identity-delivery window | INCORPORATED; residual remains | §11.3, §19: immediate identity report, failure injection, unknown containment/external resolution, no guessed fallback |
| B5 L3 handler order | INCORPORATED | §3.3, §9, §19: install before spawn, restore after containment, EOF safety versus typed signal outcome |
| N1 actual Mac probes | INCORPORATED; NOT RUN here | §19: outside sandbox, plain/Popen variants, >=20 repetitions each, <=2s latency, kill(0) checks, OS/Python record |
| N2 remaining index lock | PRESERVED / clarified | §15: preserve even concurrent-user lock; fixed user-action explanation |
| N3 platform support | PRESERVED / clarified | §22: capability-gated Mac/Linux, Windows NOT SUPPORTED, PDEATHSIG optional defense only |
| N4 private/import/audit boundary | INCORPORATED | §16, §21: intent durable before runtime action, dynamic/-m/subprocess/runpy scan, no bypass |
| N5 same-UID/simultaneous death | PRESERVED | §3.3, §11: explicit threat boundary cannot excuse llmtool-caused FD inheritance |

Implementation feasibility remains a hard future review gate: start-time checks
alone are not atomic signaling handles; §11.1's continuity mechanism must withstand
reuse races. The spawn-to-identity-delivery window is not eliminated, and a host
unable to retain responsibility for unresolved containment cannot claim support.
Those limitations must be tested and assessed in 2.2.1 before anyone requests
authorization for 2.2.2; do not infer feasibility from this text.

Current slice authorization:

- A11b.2.2.0: **NO — documentation only in this task**.
- A11b.2.2.1: **NO — pending independent targeted re-review and separate authorization**.
- A11b.2.2.2: **NO — containment implementation, Mac probes and independent review absent**.

API references for future implementation review:

- [Python 3.9 subprocess: Popen, descriptor passing and child waiting](https://docs.python.org/3.9/library/subprocess.html).
- [Python 3.9 select: kqueue process-event constants](https://docs.python.org/3.9/library/select.html).
- [Apple kqueue manual: EVFILT_PROC / NOTE_EXIT](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/kqueue.2.html).
- [Python 3.9 isolated-mode option](https://docs.python.org/3.9/using/cmdline.html#cmdoption-I).
