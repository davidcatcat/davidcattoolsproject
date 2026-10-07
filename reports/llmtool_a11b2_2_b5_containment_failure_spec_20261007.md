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

This specification resolves the B5 design question before any mutation code is written.

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
8. the parent also learns the Git process-group identity, so a live parent can kill
   the Git group if the supervisor unexpectedly exits.

The implementation mechanism may use an internal Python module/process or equivalent,
but these semantics are mandatory.

No public command-line entry may expose the supervisor.

## 4. Parent/supervisor control protocol

Use a bounded, versioned internal protocol.

The protocol must carry only deterministic internal fields. It must not carry:

- raw Git stdout/stderr;
- file contents;
- environment dumps;
- credentials;
- arbitrary command strings.

Allowed request data is limited to information required to re-run the existing
preflight and bind the mutation, for example:

- protocol version;
- canonical Action JSON/digest;
- expected preflight binding values;
- bounded timeout.

The supervisor MUST reconstruct the fixed ADD command from reviewed code. It must not
accept arbitrary argv from the parent.

The parent-liveness/control channel stays open for the complete mutation window.

The supervisor must treat:

- EOF;
- explicit CANCEL;
- malformed/oversized protocol;
- parent protocol failure

as fail-closed cancellation.

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
- no control/audit/liveness FD is inherited by Git.

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

1. attempt process-group `SIGKILL`;
2. catch at least `ProcessLookupError` and macOS `PermissionError`;
3. if the leader still exists, fall back to direct process kill;
4. always wait/reap the leader;
5. always close stdout/stderr/control/status FDs in nested `finally` blocks;
6. never allow a cleanup exception to replace the original stable failure category.

This closes A11b.2.1 review finding L1 before mutation code can reuse the runner.

### 8.4 Leader exits normally

Even after the Git leader exits:

- the supervisor must still attempt process-group cleanup before declaring the child
  tree gone;
- `PermissionError` / `ProcessLookupError` are handled as above;
- no descendant may remain.

This explicitly covers the TR-3 / leader-exits-descendant-survives case.

Actual hardened Git ADD is expected to spawn no repository-controlled helpers, but the
containment layer must not rely solely on that assumption.

## 9. Parent signal handling

During the internal mutation call, the parent must install scoped handling for:

- SIGINT;
- SIGTERM;
- SIGHUP.

Handlers must perform only minimal signal-safe coordination, such as setting a flag and
notifying the supervisor control channel.

The mutation call then:

1. waits a bounded interval for supervisor cleanup/termination;
2. restores previous signal handlers;
3. raises/returns a stable typed interruption outcome.

Rules:

- signal handlers are restored on every exit path;
- mutation from a non-main Python thread is rejected unless an independently reviewed
  equivalent signal/liveness mechanism exists;
- model/repository text cannot affect signal behavior.

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

The parent must learn the Git process-group identity once Git starts.

If:

- supervisor exits unexpectedly;
- supervisor status channel closes unexpectedly;
- supervisor sends malformed status

while the parent remains alive, the parent must attempt to kill the known Git process
group and then classify the repository state.

This provides two-sided containment:

- parent death -> supervisor cleans Git;
- supervisor death -> parent cleans Git.

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

## 14. Failure/recovery classification matrix

### 14.1 Clean success

Conditions:

- Git exit 0;
- no parent/supervisor interruption;
- no index.lock remains;
- post semantic state exactly equals expected post-state.

Outcome: `APPLIED`.

### 14.2 Known not applied

Conditions:

- mutation failed/interrupted;
- no index.lock remains;
- raw/semantic index remains exactly the pre-state.

Outcome: `NOT_APPLIED`.

Higher layers may later decide whether a fresh action/approval can retry. A11b.2.2
does not retry.

### 14.3 Applied despite interrupted/nonzero control flow

Conditions:

- interruption/nonzero/parent-loss occurred;
- no index.lock remains;
- semantic index exactly equals the expected authorized post-state.

Outcome: `APPLIED_BUT_CONTROL_INTERRUPTED`.

This is **not task success**. It means the mutation state is known and applied.
Higher layers must not blindly retry.

### 14.4 Unknown/unsafe state

Any of:

- index.lock remains;
- index unreadable;
- post semantic state is neither exact pre-state nor exact expected post-state;
- unrelated entry changed;
- target OID/mode/path differs;
- post-verification fails unexpectedly.

Outcome: `STATE_UNKNOWN` / `USER_ACTION_REQUIRED`.

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
- authorized paths/scope;
- pre-state digest.

Terminal/recovery audit records:

- outcome classification;
- exit code if known;
- post-index SHA if known;
- post-verify code;
- containment reason;
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

- index == pre-state, no lock -> NOT_APPLIED;
- index == expected post-state, no lock -> APPLIED_BUT_CONTROL_INTERRUPTED;
- anything else or lock present -> USER_ACTION_REQUIRED.

This is how the parent-SIGKILL completion race is made explicit rather than guessed.

A11b.2.2 does not implement startup recovery scanning; it defines the evidence contract
future A11b recovery code must consume.

## 18. A11b.2.2 implementation slicing

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

### L1 / cleanup

- leader already exited + `killpg` PermissionError does not leak raw OSError;
- intended typed failure survives cleanup;
- stdout/stderr/control/status FDs close;
- no child remains.

### Timeout/output/internal failure

- timeout kills process group;
- stdout limit kills process group;
- stderr limit kills process group;
- internal drain exception kills process group;
- malformed supervisor status fails closed and parent kills known Git group.

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

### Parent SIGKILL

On actual macOS host:

1. spawn outer harness parent;
2. wait until supervisor and fake Git PIDs/PGID are reported;
3. send `SIGKILL` to **parent only**;
4. verify supervisor observes EOF;
5. verify fake Git process group terminates;
6. verify supervisor exits;
7. verify no descendant remains.

This is a required local Mac gate. Mock-only evidence does not substitute for it.

### Parent normal/crash-like exit

- parent closes/loses control channel without explicit cancel;
- supervisor treats EOF as parent loss and cleans the child group.

### Supervisor loss

- parent receives child PGID;
- kill supervisor only;
- live parent detects supervisor loss;
- parent kills child PGID;
- no child remains.

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
- Git nonzero + unchanged index -> NOT_APPLIED;
- exit0 + expected state -> APPLIED;
- nonzero/interruption + expected state -> APPLIED_BUT_CONTROL_INTERRUPTED;
- unexpected semantic state -> USER_ACTION_REQUIRED;
- remaining index.lock -> USER_ACTION_REQUIRED and lock preserved.

No test may use a real user repository index.

## 21. Public/import boundary

Through all A11b.2.2 slices:

- no existing production CLI/router/orchestrator/provider code imports the mutation
  execution entry;
- tests may call private/internal APIs;
- no argparse/subcommand is added;
- no model output can invoke mutation;
- no approval object is interpreted by the mutation engine.

Add AST + string/import checks so dynamic import strings are covered, addressing
A11b.2.1 review note N4.

## 22. OS/platform policy

A11b.2.2 initial support is POSIX/macOS-first, matching the existing safe subset.

Required platform behavior:

- actual macOS parent-SIGKILL containment probe must PASS before mutation code merges;
- Linux support may be added only with equivalent process/liveness tests;
- native Windows mutation remains NOT SUPPORTED unless a reviewed job-object/process-tree
  design exists;
- root execution rejects.

## 23. No retry / no automatic rollback

Containment failure is not retried inside A11b.2.2.

A changed index invalidates the earlier state binding.

Even if ADD would be idempotent for some inputs, retry requires later policy/approval
logic to re-evaluate a fresh action/state.

Automatic rollback is forbidden in this slice.

## 24. Definition of Done for B5 design gate

B5 design is review-ready when an independent reviewer can answer YES to all:

- dedicated supervisor is required;
- parent `SIGKILL` is covered by liveness-channel EOF;
- supervisor loss is covered by parent fallback;
- Git descendants cannot survive normal/abnormal exits;
- L1 cleanup behavior is fixed before mutation;
- actual signals are tested, not only mocked handlers;
- macOS parent-SIGKILL probe is mandatory;
- post-state classification distinguishes APPLIED / NOT_APPLIED /
  APPLIED_BUT_CONTROL_INTERRUPTED / USER_ACTION_REQUIRED;
- index.lock is never auto-deleted;
- durable-before-action audit is a runtime gate, not silently deferred;
- no public mutation path is introduced;
- no real Git ADD starts before containment harness review passes.

## 25. Required independent design/security review

Before any A11b.2.2 production code:

Create:

`reports/llmtool_a11b2_2_b5_containment_spec_review_20261007.md`

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

Even a PASS review only authorizes 2.2.0 and 2.2.1.

Real `git add` remains prohibited until the 2.2.1 containment harness itself is
implemented, locally probed on macOS, and independently reviewed.
