# llmtool A11b.2.2 B5 spec remediation — 2026-10-07

## Task contract and provenance

PR #44, branch `docs/llmtool-a11b2-2-b5-containment-spec-20261007`.
Canonical Mac repository:
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.

- Starting local and fetched remote HEAD:
  `23ae5e8230cdb42acaeb54bc969c940b992b620a`.
- Fetched `origin/main` / design base:
  `780970ab9e7bb78c471858cd513b28cecc3e5b89`.
- Historical Claude review inspected spec head
  `1ed7551da25633b8839005a202a565cbacc55a43`; that review was added in the starting
  PR HEAD and is preserved byte-for-byte.
- Tracked tree was clean before changes. Existing nine untracked artifact paths
  were preserved; no alternate checkout, reset, stash, clean or merge was used.

This task accepts documentation/spec remediation of H1/H2/M1/M2/M3 and the related
L/N requirements. Acceptance here means requirements and future evidence gates are
written, traced and internally checked. It does not constitute independent security
approval or execution evidence. The historical BLOCKED verdict remains in force
until independent targeted re-review; no implementation slice is authorized now.

Read completely: the B5 spec, historical review, current README and ROADMAP. Also
followed AGENTS.md, WORKFLOW.md and inspect-repo/review-change/safe-git workflows.

## Findings and normative disposition

| Finding | Disposition | Remediated spec sections |
|---|---|---|
| H1 liveness writer inheritance | CLOSED — documentation | §3.3, §6, §9, §19 |
| H2 PID/PGID reuse and kill-before-reap | CLOSED — documentation | §8, §11.1, §19 |
| M1 hung supervisor escalation | CLOSED — documentation | §4, §9, §11.2, §19 |
| M2 supervisor runtime/code binding | CLOSED — documentation | §3.4, §4, §19, §21 |
| M3 total semantic post-state classification | CLOSED — documentation | §13–§14, §17, §20 |

**H1.** Supervisor launch requires exec semantics (`Popen` or separately reviewed
posix_spawn); multiprocessing fork/forkserver and bare fork are forbidden. CLOEXEC /
non-inheritable pipes have an explicit ownership table. Only liveness-read and
status-write may cross the supervisor exec boundary; neither control/status nor
audit descriptors reach Git/fake children. The parent critical window prohibits
extra processes/threads and requires a reviewed main-thread/single-thread host.
Python thread count alone is explicitly insufficient for native-thread safety.
Supervisor death detection monitors process exit as well as status EOF. Fake-child
FD tests and the extra-Popen harness variant are mandatory, without authorizing
that extra spawn in production.

**H2.** Git exit detection cannot reap the owned leader. macOS requires kqueue
NOTE_EXIT or a reviewed non-reaping equivalent, followed by group cleanup decision,
then reap. Git poll/wait/communicate/context-manager reaping is prohibited before
that decision. Parent fallback binds PID/PGID/SID/UID and kernel start-time identity;
fresh identity verification precedes signaling. Mismatch must not kill a replacement.
Leader-absent handling distinguishes witnessed original-group continuity from
mere numeric group existence. Reuse and check-to-signal races require explicit
adversarial tests; an unsafe platform is NOT SUPPORTED before mutation.

**M1.** Missing CANCEL acknowledgement, bounded-wait expiry or missing group-gone
proof escalates to owned-supervisor SIGKILL/reap and identity-bound Git group cleanup,
then repository classification. EPIPE/status EOF/leader-only kill does not substitute
for containment proof. No ordinary return or handler restoration is allowed while
owned processes may remain. Identity uncertainty requires external/operator
resolution, not signaling unrelated groups. Future read-only post-state Git queries
start only after containment/reap/channel closure; unresolved inspection is in-process
and provisional. Hung-supervisor tests must exercise all escalation triggers.

**M2.** Fixed isolated interpreter/module launch, trusted neutral cwd and fresh
PATH/LC_ALL-only environment prevent ambient Python/loader/credential/proxy
inheritance. A versioned handshake binds reviewed code artifact hashes, interpreter
and installation identity. Supervisor independently checks module origins and
digests before READY/child spawn and rechecks before spawn. An arbitrary Action or
self-consistent manifest is not authority. Invalid private `-m`/runpy/import entry
fails closed without child spawn. Trusted installed startup code remains a stated
premise, not a newly implemented OS sandbox.

**M3.** The first-match semantic table prioritizes unsafe/unreadable index or lock,
then root/HEAD/attached branch name/OID drift, then exact expected semantic state,
then exact pre-state only when pre differs from expected, then all other states.
Clean exit0 plus expected state is APPLIED; interrupted/nonzero/unknown control
plus expected state is APPLIED_BUT_CONTROL_INTERRUPTED. No-op (`expected == pre`)
uses that same expected-state branch and cannot be NOT_APPLIED. Raw index SHA/cache
changes remain forensic evidence. Primary lifecycle failure is separate from
semantic outcome, and unresolved containment prevents a completed applied result.

## Related requirements

| Item | Disposition |
|---|---|
| L1 protocol | INCORPORATED: 4-byte length prefix, 128 KiB maximum frame, strict bounded JSON, partial reads/writes, mid-message EOF and unknown version/type fail closed, EPIPE-safe cleanup, nonblocking/select deadlines (§4/§19) |
| L2 spawn-to-identity window | INCORPORATED; NOT eliminated: send identity immediately, inject loss before delivery, never guess PGID or claim successful fallback; preserve unknown containment and require external resolution (§11.3/§19) |
| L3 signal ordering | INCORPORATED: install before pipes/spawn, handler only records state, restore after cleanup/reap, repeated signals cannot bypass cleanup; EOF is the parent-death safety mechanism (§9/§19) |
| N1 Mac probe | INCORPORATED: real Mac outside sandbox, plain and `Popen(close_fds=True)` variants, >=20 repetitions per variant, <=2s cleanup latency, kill(pid,0)/killpg(pgid,0), OS/Python/code digest recorded (§19) |
| N2 lock | PRESERVED: never delete index.lock, including concurrent-user lock; explain its blocking effect without presuming ownership (§15) |
| N3 platforms | PRESERVED: capability-gated Mac/Linux evidence, Windows NOT SUPPORTED; Linux PDEATHSIG only supplemental (§22) |
| N4 audit/import boundary | INCORPORATED: durable intent before product action; private engine not a bypass; AST/dynamic/-m/subprocess/runpy and indirect-wrapper scan (§16/§21) |
| N5 threat boundary | PRESERVED: simultaneous forced parent+supervisor death and malicious same-UID actors remain outside the process-local guarantee; H1 accidental inheritance is not excused |

B5 review L1/L2 are distinguished from A11b.2.1's existing cleanup and drift-test
notes. The latter remain prerequisites in §18; nothing was implemented here.

## Remaining feasibility and review gates

Kernel start-time snapshots are not atomic signaling handles. The future harness
must demonstrate its platform continuity procedure or reject unsupported operation;
this document does not claim to have solved or tested that race. The identity-delivery
window likewise remains explicit. A host without an external unresolved-containment
handling path cannot claim the specified support. Independent targeted review must
assess these premises before any harness implementation authorization is requested.

Actual macOS probes described in the Claude historical review are **historical
evidence**, not this task's tests. No process/signal probes were run in this
documentation task. No future fake-child, semantic classification or OS-containment
test is claimed PASS here.

## Documentation validation

Validated the final working-tree documentation against starting HEAD `23ae5e8`:

- `git diff --check`: PASS.
- Python 3.9.6 read-only documentation checks: PASS — all four changed documents'
  63 relative file links resolve, Markdown fences balance, B5 sections 1–26 and
  numbered section references resolve, and required finding/protocol/authorization
  text is present.
- Historical review bytes versus starting Git blob: unchanged, PASS.
  SHA-256: `3970ea2b4b62dd6f1a7fc8aa96904c33b3ab3d26db7ae3808567c396485af31f`.
- Change allowlist: exactly the four documents below; production/test/runtime/skill
  files unchanged, PASS.
- Existing unrelated untracked paths preserved, PASS.
- App builds/full suite: NOT RUN — documentation-only change; no executable or
  test file was modified. No containment implementation is available to validate.

Changed files:

1. [B5 spec](llmtool_a11b2_2_b5_containment_failure_spec_20261007.md).
2. [README](../tools/llmtool/README.md): design-remediation status and report links.
3. [ROADMAP](../tools/llmtool/ROADMAP.md): current preflight/containment slice status
   reconciled; automatic implementation authorization wording removed.
4. This remediation report.

The publication commit containing this report is the exact candidate for targeted
re-review; the starting SHA above is provenance, not a claim to have validated an
unmodified starting spec. Publication SHA is supplied with the final Git handoff.

Company/provider live calls: **0**.
Codex benchmark calls: **0**.
Production/test/runtime/skill changes: **NONE**.
Supervisor implemented: **NO**.
Runtime Git ADD implemented/executed: **NO**.
Merge performed: **NO**.

## Authorization and handoff

H1: **CLOSED — documentation; targeted re-review pending**.
H2: **CLOSED — documentation; targeted re-review pending**.
M1: **CLOSED — documentation; targeted re-review pending**.
M2: **CLOSED — documentation; targeted re-review pending**.
M3: **CLOSED — documentation; targeted re-review pending**.

A11b.2.2.0 authorized? **NO — current task is documentation only**, notwithstanding
the historical review's isolated prerequisite-cleanup permission.

A11b.2.2.1 authorized? **NO — pending independent targeted re-review and separate
explicit implementation authorization**.

A11b.2.2.2 authorized? **NO**.

Ready for independent targeted B5 design re-review: **YES**.
Stop after normal commit/push to PR #44; do not implement or merge.
