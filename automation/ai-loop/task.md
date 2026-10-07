# A11b.2.2.1 — supervisor / fake-child containment harness — F3 recovery

## Authorization, revision and delivery

David supplied and authorized this implementation handoff on 2026-10-07. The first
Mac run stopped correctly at F3 NEEDS_DECISION before source/test implementation.
The coordinator has resolved that design question below. Continue the same A11b.2.2.1
slice on a fresh recovery branch; the old local branch/report are preserved as evidence,
not reused or deleted. The coordinator verified PR #44 and PR #46 are merged and
main is `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.

- Repository: davidcatcat/davidcattoolsproject.
- Canonical Mac root: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Pinned base/start: `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.
- Recovery branch: `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`.
- The stopped local branch `feat/llmtool-a11b2-2-1-containment-20261007` remains untouched at the pinned base.
- New Draft PR, created by the outer runner; no existing feature PR is reused.
- Validation report: `reports/llmtool_a11b2_2_1_containment_f3_recovery_validation_20261007.md`.
- Initial implementation plus at most two repairs; no blind provider retry.
- Stop at READY_FOR_FINAL_REVIEW. Independent security review and coordinator
  acceptance are pending; never merge or begin A11b.2.2.2 automatically.

The runner fetches main, checks exact base, clean tracked/index state, preserved
unrelated untracked artifacts, branch/PR absence and current-HEAD integration, then
creates the fresh branch from origin/main. A moved base, branch collision or local
unpublished/divergent work stops without reset, stash, force-push or deletion.
No worktree, clone or alternate checkout, including /private/tmp project checkouts.

Historical spec/roadmap prose saying documentation-only or NOT AUTHORIZED describes
its originating task, not this new user authorization. Current scope is precisely
2.2.1. Real Git ADD and 2.2.2 remain unauthorized. If a substantive design conflict
exists, return NEEDS_DECISION with evidence; do not reinterpret away the requirement.

## Read before implementation

Read these authoritative requirements and evidence in full:

- AGENTS.md, CLAUDE.md, tools/llmtool/WORKFLOW.md, tools/llmtool/ROADMAP.md.
- skills/implement-feature/SKILL.md, skills/review-change/SKILL.md,
  skills/security-review/SKILL.md and skills/safe-git/SKILL.md.
- reports/llmtool_a11b2_2_b5_containment_failure_spec_20261007.md (especially
  sections 3/4/8/9/11/18/19/21 and remediation section 26).
- reports/llmtool_a11b2_2_b5_containment_spec_review_20261007.md
  (historical BLOCKED findings).
- reports/llmtool_a11b2_2_b5_containment_spec_remediation_20261007.md.
- reports/llmtool_a11b2_2_b5_containment_spec_targeted_rereview_20261007.md.
- reports/llmtool_a11b2_2_0_cleanup_validation_20261007.md.
- reports/ai-loop/A11b.2.2.0-pr46-review-20261007-20261007T050236Z-59259.md.
- reports/ai-loop/checkpoints/A11b.2.2.1-containment-20261007-20261007T064116Z-68730.md (stopped F3 checkpoint; not a PASS).
- tools/llmtool/llmtool/git_mutation.py, git_intel.py, action_contract.py, and the existing process runner/error definitions
  located by inspection (do not assume a process_runner.py/errors.py filename).

## Scope and file contract

Implement only private supervisor + deterministic fake-child process containment.
Use the explicit allowed_files and required_files in task.json. The private module
names are reserved for this handoff; do not add an arbitrary command runner. If the
reviewed architecture requires another file, return NEEDS_DECISION with the exact
reason/path rather than expanding scope. Existing git_mutation production behavior
and Action/approval semantics remain unchanged. Extend its import-boundary tests
only as necessary to distinguish reviewed private code from forbidden public paths;
preserve all existing rejection assertions.

Never execute real product `git add`, mutate the user repository/index, implement
real ADD/post-state mutation, add a public mutation CLI or model path, issue approval,
bypass durable audit, retry/rollback, delete locks, or start 2.2.2/.3, A11b.3, A11c or
A12. Fake-child/probe processes may create disposable runtime fixtures, not project
checkouts. Their signals target only identity-bound owned synthetic processes.

The outer development runner owns Git staging/commit/push/PR creation. Its ordinary
publication staging is distinct from the prohibited llmtool Git ADD implementation.
Codex must not stage/commit/push, install globally, invoke sudo, bypass permissions,
run expensive suites/probes itself, or change the task/criteria. Return PASS for an
implemented candidate, not as a claim that pending runner tests/security gates passed.

## 1. Supervisor and trusted runtime

Separate process, never a thread; fixed exec-style launch equivalent to
`[sys.executable, '-I', '-m', '<private supervisor module>']`, shell=False, distinct
session, trusted neutral cwd outside the repository, fresh PATH/LC_ALL allowlist.
No PYTHON*, DYLD_*, LD_*, user-site, credentials, proxies, agents or ASKPASS inheritance.
Verify interpreter/install identity. Isolated module-resolution failure rejects;
no PYTHONPATH/repository-cwd workaround or silent alternate interpreter. If the
specified Mac Python environment cannot resolve the trusted installation, report
NOT SUPPORTED/NEEDS_DECISION and preserve evidence instead of changing global setup.
Without a legitimate internal FD/launch/code handshake, direct import, runpy,
wrapper or subprocess -m invocation must fail closed without spawning any child.
There is no hidden/public supervisor CLI.

## 2. FD topology and host premise

Parent uniquely owns control/liveness writer and status reader; supervisor owns
control/liveness reader and status writer; fake child owns neither. CLOEXEC and
non-inheritable endpoints, close_fds=True, pass_fds limited to the two required
supervisor endpoints; close unused ends immediately. Supervisor sets inherited
endpoints non-inheritable before spawning its child. No control/liveness/status/audit
FD reaches fake children; verify enumeration in tests.

Install signal scope before pipe creation. The production critical window permits
only the one supervisor launch, no extra fork/spawn/thread or spawning callback.
Support only reviewed main-thread/single-thread hosts. threading.active_count()==1
alone does not prove absence of native threads/fork; explain and enforce the trusted
host premise. Unsupported/unprovable conditions reject before spawn. The extra
Popen(close_fds=True) inheritance stress variant is harness-only, never a production
permission.

## 3. Bounded protocol

Versioned strict JSON, 4-byte big-endian length prefix, max 128 KiB; reject zero/
oversize, duplicate/unknown keys, non-finite values, excessive depth/count, unknown
version/type and mid-frame EOF before unsafe allocation. Nonblocking partial
reads/writes with monotonic deadlines; EPIPE/BrokenPipeError triggers cleanup.
No pickle/eval, arbitrary argv/shell or action replacement.
START -> READY -> fixed fake-child spawn -> CHILD_IDENTITY -> optional CANCEL_ACK
-> terminal status. After START only CANCEL is accepted from parent.

## 4. Code/runtime binding

Bind protocol, action/preflight digests and caller-pinned reviewed artifact manifest,
interpreter/install identity; manifest includes supervisor, protocol/identity helpers,
mutation/preflight, action contract, errors, package init and all containment/error
path modules. Parent and supervisor independently verify origins and hashes, before
READY and again before child spawn. A self-consistent Action-supplied manifest is
not authority. Mismatch must reject with zero child spawn. This is drift detection
under trusted installation, not a same-UID isolation claim. Explain the fake-harness
internal binding without introducing fake product approval/audit authorization.

## 5. Fake child only

Fixed deterministic synthetic cases in private code; no arbitrary command seam or
Git ADD. F3 changes the topology: the supervisor itself is the session leader and
process-group leader (`PID == PGID == SID`), and every fake child/controlled descendant
stays in that supervisor-owned group. The child must not create a new session/group or
escape it. A controlled descendant case must remain after its direct leader exits, so
the test proves whole-group descendant containment rather than merely leader reaping.

## 6. Non-reaping lifecycle

On macOS implement kqueue KQ_FILTER_PROC/NOTE_EXIT or a separately reviewed equivalent.
Notification -> owned-group cleanup decision -> leader reap, never poll/wait/
communicate/consuming waitpid/context-manager reap first. Handle SIGCHLD auto-reap
and custom reapers according to B5. Test alive and zombie-at-registration NOTE_EXIT,
registration failure, fast exit and leader exit with live descendant. Never signal
remembered released IDs after reap.

## 7. Parent death, signals and supervisor failure

Real host parent-SIGKILL -> writer closure -> supervisor EOF -> fake group SIGKILL
-> leader reap -> eventual group disappearance. Run >=20 repetitions EACH for plain
and harness-only Popen(close_fds=True) variants, with a real controlled descendant.
Every repetition has <=2s monotonic latency to confirmed group disappearance;
use kill(pid,0)/killpg(pgid,0) with identity evidence, not ps. EPERM is inconclusive,
not absence. Distinguish zombies from executing descendants and safely clean owned
processes on assertion failures. Record macOS/Python/code digest. Probe FAIL,
NOT SUPPORTED or instability means STOP, not automatic retries or weaker thresholds.

Test real SIGINT/SIGTERM/SIGHUP including pre-pipe, spawn and cleanup timing; minimal
handlers, repeated signals cannot bypass cleanup/deadline; restore only after verified
containment and reap. EOF is the death safety mechanism, handlers provide typed outcome.
For hung/lost supervisor, missing ACK, bounded wait, malformed/closed status or missing
group-gone proof: kill/reap still-owned supervisor, then fresh identity-bound group
handling. No normal return/restoration while an owned process may live. Explicitly
implement unresolved-containment exit/handler behavior (F1), preserve its evidence,
and classify as state unknown / user action required without guessing ownership.
Also inject supervisor death before CHILD_IDENTITY delivery (B5 residual window).

## 8. Identity, reuse and coordinator-approved F3 remediation

Bind PID/PGID/SID/UID and kernel start-time identity. The accepted remediation is:

- supervisor starts a new session, so its `PID == PGID == SID`; parent is its real parent;
- fake child and descendants remain in the supervisor's process group;
- parent observes supervisor exit without reaping it and never calls poll/wait/communicate before the group-cleanup decision;
- for timeout, hung supervisor, cancellation or unexpected supervisor death, parent may `killpg(supervisor_pid, SIGKILL)` only while the owned supervisor is live or an unreaped zombie and bound PID/PGID/SID/UID/start-time evidence matches;
- because the parent has not reaped that child, the supervisor PID cannot be reused; because PGID equals that reserved PID, an unrelated new process group cannot acquire that PGID before reap;
- only after group cleanup/absence is decided may parent reap the supervisor; never signal the remembered group after reap;
- on normal fake-child termination, supervisor may make the cleanup decision and reap its direct child for status while the still-live supervisor pins the group, then send terminal evidence and wait for parent finalization;
- on parent-liveness EOF/SIGKILL, supervisor performs a last-act `killpg(own_pgid, SIGKILL)`; self-termination is intentional and kills the fake group together;
- if supervisor identity is ambiguous, already reaped, or no longer `PID == PGID == SID`, do not signal a numeric PGID; classify containment/state unknown.

This replaces the stopped run's snapshot-only fallback and the earlier separate-child-process-group assumption. Do not add a separate anchor process and do not depend on Apple private audit-token signaling. Independent security review after implementation must explicitly verify this remediation.

Test live and zombie supervisor-anchor cases, mismatch/reuse, attempted signal after reap, unexpected supervisor death, hung supervisor, parent SIGKILL and verification-to-signal timing. Actual Mac probes remain mandatory.

## 9. Typed outcomes and F1–F4

Internal start-failed, timeout, interrupted, parent-lost, supervisor-lost, output-limit,
protocol-failure and containment/state-unknown outcomes; no real Git APPLIED/NOT_APPLIED
semantic flow in this slice. Implement F1 unresolved exit, F2 kqueue cases, F3 kernel
identity/continuity and F4 host premise within 2.2.1, without new milestones. Preserve
primary failure despite cleanup/protocol faults. BLOCKER/HIGH/correctness defects
block; optional Low/Note do not cause an unlimited repair loop or a zero-bug pursuit.

## 10. Boundary and tests

No CLI/router/provider/orchestrator/model/debug mutation path, approval issuer or audit
bypass. Cover AST/string/static and dynamic import, direct invocation, runpy, -m and
wrapper paths; invalid internal handshake cannot spawn a fake child. Keep A5 and
A11b.1/2.1 regressions intact. New unit tests must discover actual nonzero test cases.
CI on other platforms may report Mac-only capabilities NOT SUPPORTED/skipped; those
results never count as Mac containment PASS. Actual Mac mandatory acceptance permits
no skipped scenarios. Do not weaken tests or change required policy for a green run.

## 11. Runner test order and evidence interface

The runner calls Codex once for the initial implementation. After each candidate or
bounded repair it runs syntax, new containment tests and existing Git/A5/Action tests,
then read-only Claude ordinary review of the complete base-to-HEAD diff. On preliminary
review PASS, it runs final full checks under both tools/llmtool/.venv/bin/python and
/usr/bin/python3, plus required actual Mac acceptance. Full checks are reused only on
identical source/test bytes. --related selects repair coverage, not permission to skip
these mandatory final gates. --full also runs full checks on the initial candidate.

Implement tests/run_containment_acceptance.py with exactly the fixed `--output PATH`
evidence interface used by task.json, launched from canonical root by the outer runner
outside the Codex sandbox. It must execute real required tests/probes, require nonzero
checks and zero skips, retain failures, exit nonzero on any failure/unsupported case,
and produce fresh JSON only for that invocation. No network/global install/user repo
mutation. The output path is in the runner-owned local logs directory. Do not fabricate
JSON PASS to satisfy the validator. Independently reviewed source and raw evidence
are required; schema validation alone cannot prove semantic correctness.

JSON shape (strict keys; max 128 KiB):

- schema_version: integer 1; platform: 'darwin'; os_version: platform.mac_ver()[0];
  python_version: platform.python_version() for the invoked venv interpreter.
- tested_sha256: SHA-256 map of ALL existing allowed_files excluding reports/;
  read task.json's pinned contract copied in the output directory's task.json.
- scenarios: EXACT names fd_inheritance, sigint, sigterm, sighup, hung_supervisor,
  leader_exit_descendant, identity_reuse, macos_start_time, kqueue_non_reaping,
  protocol_framing, code_runtime_binding, public_import_boundary,
  parent_sigkill_plain, parent_sigkill_popen.
- Every scenario row: EXACT keys status ('PASS'), checks (positive int), skipped (0),
  repetitions (empty array except for SIGKILL variants).
- Each SIGKILL row: >=20 actual repetitions, checks >= repetition count. Each record
  has EXACT keys leader_pid (positive int), pgid (positive int), descendant_pids
  (nonempty positive-int array), latency_seconds (finite number 0..2), identity_verified,
  leader_absent, descendants_absent, group_absent, containment_confirmed (all true).

The heartbeat is not a repeated command: [Running] prints every
20 seconds while a command is active. Tests show position, PASS/FAIL and duration.
Any mandatory Mac failure or invalid evidence stops with files/logs preserved. No
blind provider retry, permission bypass or automatic probe repair. Ordinary unit/full
regression failures can use the fixed two-repair budget when reproducible/in scope.

## 12. Report, review and publication

Create the specified validation report with truthful provenance and initial pending
runner gates. Preserve before-fix evidence and do not claim old results for changed
bytes. Include every requested disposition: harness/supervisor, FD, x20 plain/x20
Popen parent-SIGKILL, SIGINT/TERM/HUP, hung escalation, leader/descendant cleanup,
PID/PGID reuse, kernel identity, non-reaping kqueue, framing, code/runtime binding,
both Python full suites, BLOCKER/HIGH, no real Git ADD, no public mutation, no 2.2.2
authorization and readiness for independent security review.

After actual gates PASS, the runner appends its final results and hashes to that report,
commits report evidence, and Claude re-reviews the complete final diff. It publishes
report/implementation to the new branch, creates one Draft PR, verifies exact final
HEAD llmtool CI, appends the handoff to the PR and stops at READY_FOR_FINAL_REVIEW.
No formal security PASS or merge approval is implied. Keep raw prompts/test/model
streams local in .ai-loop/<run-id>; curated evidence alone goes to Git. Provider auth,
quota, CLI, stale state, invalid structured output, missing files or exhausted repair
bounds stop with work preserved and require coordinator recovery, not a blind rerun.


Preflight permits normal ancestry or a verified squash handoff: exact merged PR
HEAD/branch/repository, identical tracked tree to pinned main and PR merge commit
already in pinned main. It does not reset/change the old branch or accept equal
content as proof for unpublished commits. Existing dirty/untracked protections apply.

## Observed stop and decision evidence — 2026-10-07

The initial Mac run `20261007T064116Z-68730` stopped at NEEDS_DECISION before source/test implementation. The coordinator imported David's provided transcript to `reports/ai-loop/checkpoints/A11b.2.2.1-containment-20261007-20261007T064116Z-68730.md` on the control branch. Read that report before preparing recovery. F3 continuity is unresolved; no alternative per-member design is accepted here. Keep the existing Mac branch/files and do not restart this task blindly.

`publish_decision_checkpoint: true` authorizes the outer runner to publish a bounded curated checkpoint to the control branch ONLY when the initial new-task Codex result is valid NEEDS_DECISION, HEAD stays at the pinned base, and only the designated acceptance report changed. No raw logs, source/index changes, feature PR, PASS, merge or automatic resume accompany that publication. Control drift/collision/API errors preserve local evidence and do not retry. Other stop types remain local for coordinator diagnosis. This update applies to future invocations; it does not rerun or repair the already stopped run.

## Repo-local launcher installation — 2026-10-07


David selected `scripts/ai-loop.sh` inside the canonical repository as the entry
point. The parent bootstrap remains compatible. `bash ai-loop.sh --install-launcher`
from the canonical parent installs only the pinned launcher and exits before task
loading/preflight/model calls; it preserves the existing stopped branch/report.
Installation is not task recovery or acceptance. F3 remains unresolved; do not
interpret INSTALL ONLY COMPLETE as permission to rerun the current task.


## F3 recovery authorization — 2026-10-07

The prior run `20261007T064116Z-68730` is retained only as a STOP/decision checkpoint. Its snapshot-then-killpg objection is accepted. This recovery remains the same A11b.2.2.1 milestone and may implement the supervisor-as-group-anchor topology above. The old local branch/report must remain untouched; the new recovery branch is the sole implementation branch. If actual Mac probes or review find a new BLOCKER/HIGH, stop again. Do not weaken gates, start 2.2.2, or chase LOW/NOTE findings into new sub-milestones.
