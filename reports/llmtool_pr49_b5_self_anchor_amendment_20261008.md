# B5 amendment — PR #49 self-owned supervisor anchor (2026-10-08)

Scope: bounded S49-1/P49-M1 and S49-2 remediation, starting at
`111f160e702e478437800f79e9f6a1e6bfb5c87e`. Review basis:
[Coordinator synthesis](https://github.com/davidcatcat/davidcattoolsproject/pull/49#issuecomment-6063593009)
and the PR #49 independent security and repository reviews. The historical
[B5 specification](llmtool_a11b2_2_b5_containment_failure_spec_20261007.md)
and [independent report](llmtool_a11b2_2_1_containment_independent_review_20261008.md)
remain historical records; this amendment supersedes their topology requirements
in B5 §§3.2, 6, 11.1–11.3 for the authorized synthetic containment slice.

## Normative topology and ownership

Parent -> direct supervisor (new session, PID = PGID = SID) -> synthetic leader
and descendants (inherit supervisor session/group, `start_new_session=False`).
The unique parent liveness writer and closed synchronous host premises remain.
No child inherits a liveness writer. No real Git ADD, public mutation, approval
change or A11b.2.2.2 implementation is authorized.

Parent cleanup MUST freshly verify its actual owned, unreaped direct supervisor
using PPID, UID, kernel start time and PID/PGID/SID continuity. Group signaling
MUST precede reap. The existing zombie/non-reaping proof and PID reuse rejection
remain mandatory. After reap, numeric absence probes are passive only; they never
authorize another destructive signal.

The old Git-grandchild fallback is replaced: a grandchild may be reaped by another
process, so its remembered numeric identity does not pin the group. The parent's
unreaped direct supervisor supplies the continuity anchor even when child identity
has not been delivered or the supervisor has died. This removes reliance on an
unowned grandchild as signaling authority.

## Supervisor self cleanup

On EOF/cancel or post-spawn failure, the live supervisor MUST verify
`getpid() == captured supervisor PID == captured PGID == captured SID` and
`getpgrp() == getpid()`. It then sends `killpg(0, SIGKILL)` to its current group,
including itself. It MUST NOT signal a remembered numeric group on this path.
The live self PID cannot be reused. The closed single-thread implementation has
no setsid/setpgid, callback or foreign reaper between check and signal.

Generic libproc identity is not a prerequisite for this self act: PPID can change
between its two reads when parent death closes the liveness pipe, and inspection
can fail independently of ownership. Parent-side identity checks remain strict.
If the self leadership check fails, no other group may be signaled; this is not
proof of containment or permission for an unsafe fallback.

## Retained unresolved responsibility

Unresolved diagnostics are best effort. EPIPE, EBADF and other reporting exceptions
MUST NOT bypass retained operator-wait. Preserve original failure/exception,
process, FDs, handlers, monitor and neutral runtime ownership. No normal return,
handler restoration, reap, resource closure, uncertain signaling or success claim
is allowed merely because reporting failed or the supervisor is unavailable.

Operator termination of the retained host can cause EOF cleanup only while a
healthy supervisor remains. If the supervisor is unavailable, host termination is
not a containment guarantee: external resolution must account for descendants.

## Traceability and gates

S49-1/P49-M1: self leadership check and current-group final act; deterministic
PPID double-read rejection, identity failure independence and wrong-group rejection.
S49-2: best-effort reporting plus retained wait, including supervisor unavailable.
P49-M2: this versioned topology amendment and workflow link.

Fresh two-suite canonical Mac verification, all 14 native scenarios (20+20 SIGKILL),
exact-head CI, Claude independent Security Re-review and independent functional QA
remain separate required gates. PR stays Draft; merge and next slice are blocked.

Deferred findings (no code change in this remediation): S49-3 LOW directory-hash
raw FD leak; P49-L1 LOW fixture fault hooks requiring separation before product
execution; P49-L2 runtime compatibility/fail-closed ctypes FD limitation; P49-L3 LOW
conservative post-reap numeric absence false alarm. None is silently resolved.
