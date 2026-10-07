# A11b.2.2.1 — bounded repair of Darwin zombie lookup

## CURRENT invocation and authorization — assessed second native failure

David supplied the second native diagnostic ZIP and requested the next step. The
coordinator assessed a concrete API-use defect below. This authorizes ONE
continuation of run `20261007T153635Z-87533` at committed candidate
`325bde3fe74c5f998ccf1dd046487c0900716224`, on the existing unpublished branch
`feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`.
Pinned main/base remains `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T153635Z-87533
```

This CURRENT section supersedes EVERY historical startup/recovery invocation below,
including the already-used recovery from run 77009. Keep the native-pinned task ID.
Do not create/switch a branch, reset, stash, discard files or rewrite history. The
runner checks the old contract at control
`9c8b36d01dd0edb1233cc0fcc1be8c8b62cf006f`, exact stopped HEAD, tracked/index
cleanliness, failure/source hashes and no published feature branch/PR. It preserves
current unrelated untracked artifacts and snapshots all ten candidate files before
Codex. Read `recovery.json`, `recovery-evidence/` and the copied
`coordinator-assessment.md` first. The assessment's control-branch source is
`reports/ai-loop/checkpoints/A11b.2.2.1-sigint-failure-assessment-20261007.md`;
its latest second-failure section governs this recovery.

The read-only stale-probe check remains required. Snapshot PIDs 95817/95818 do not
prove current ownership and do not authorize signals. If retained processes remain,
inspect fresh state/full host path/parent-child relation for coordinator-directed
operator cleanup of the confirmed stuck fake-test parent only. No reboot requirement,
blind remembered-PID/PGID kill or zombie signal. A failed preflight consumes no
recovery. Operator fixture cleanup is not containment PASS.

## CURRENT evidence and repair contract

The captured ten source/test/report hashes match the stopped summary, and all nine
runtime source/test hashes match the native JSON. Related checks, preliminary
Claude review and both full Python checks passed on this candidate. Native
`fd_inheritance` passed; ordinary SIGINT failed and the twelve later scenarios
were NOT RUN. All those results are historical evidence for these exact bytes.

The new traceback establishes:
`Anchor.kill_group -> verify -> identity -> _info` fails at the FIRST
`PROC_PIDTBSDINFO` query with `IdentityUnknown: kernel identity unavailable`.
It does not reach `os.getsid` or the new ESRCH-owned-zombie fallback.
The host retained `primary=interrupted`, signal 2, separate status EOF evidence,
and unresolved ownership. Diagnostic retention now works and must be preserved.

The candidate passes `arg=0` to EVERY `proc_pidinfo` flavor. Apple XNU
`xnu-11417.140.69/bsd/kern/proc_info.c` enables zombie lookup for
`PROC_PIDTBSDINFO` only when `arg` is nonzero, then calls
`proc_find_zombref` if ordinary lookup fails:
https://github.com/apple-oss-distributions/xnu/blob/xnu-11417.140.69/bsd/kern/proc_info.c

This is a demonstrated API-use defect in a path that requires zombie evidence.
The old formatter discarded the API return length and errno, so do not claim that
the supplied JSON measured ESRCH, proved zombie state, or demonstrated final
containment. A corrected native run must establish those facts.

1. **Repair the actual libproc boundary.** Use an explicit reviewed zombie-capable
   BSD query with nonzero arg for `PROC_PIDTBSDINFO`. Review every flavor-3
   caller needed for live/zombie transition, owned-anchor verification, race,
   probe cleanup and native kqueue evidence. Do not set nonzero arg blindly for
   other flavors: the native thread-count/task query must retain its proper
   flavor-specific argument. An explicit helper/keyword or documented per-flavor
   policy is acceptable within the existing file allowlist.
2. **Keep identity proof strict.** Ordinary live identity still needs real SID and
   stable fresh kernel rows. Any owned-zombie special handling still requires the
   direct unreaped Popen child, PID=PGID=SID launch anchor, UID/PPID/start-time
   identity, two fresh SZOMB rows, host/no-reaper premise and ownership recheck
   BEFORE group signaling/reaping. Keep mismatch/reuse/race/permission/missing-row
   failures closed. Do not catch every IdentityUnknown as a valid zombie, convert
   ESRCH into PASS, use tpgid as SID, or signal a remembered numeric group.
3. **Retain bounded API failure evidence.** Clear ctypes errno before the call and
   capture its actual returned byte count/errno immediately. Record requested
   size, flavor and arg through the reviewed bounded formatter as needed, without
   environment, credentials, arbitrary argv or raw provider output. A short/zero
   result must remain a failure; adding diagnostics must not hide primary errors.
4. **Add regression tests at the C-call boundary.** Existing tests mocked `_info`
   or `identity` and therefore missed the arg=0 bug. Mock the libproc callable
   itself and assert actual flavor/arg/size, including zombie-capable BSD and
   unchanged task arguments. Exercise the initial BSD-read-before-SID ordering,
   live-to-zombie transition and two-row owned-zombie proof. Cover short/zero
   returns, errno/permission failures, mismatched/reused start identity, missing
   rows and a reap race; rejection must never signal an unrelated group.
   Native zombie/kqueue evidence must use the real repaired libproc path.
5. **Preserve the original scope and all acceptance gates.** No actual Git ADD,
   user index mutation, public CLI/model path, approval issuer, rollback/retry/
   lock deletion, new milestone, alternate checkout or A11b.2.2.2. No weakening
   F1 ownership retention, non-reaping order, deadlines, single-thread premise,
   code binding or identity continuity. If the API correction reveals a new
   design BLOCKER/HIGH or cannot safely prove continuity, return NEEDS_DECISION/
   NOT SUPPORTED. Do not speculate a workaround into PASS.
6. **Append truthful validation evidence.** Keep both original native SIGINT
   failures (77009 and 87533) permanently labeled FAIL and historical full/review
   evidence tied to its tested bytes. Record this API defect and new regression
   coverage. New full/native/security results remain PENDING until actual runner
   evidence exists. No old PASS is inherited for changed bytes.

At most TWO Codex repair calls in this continuation, including the first call.
Codex edits only the ten existing allowed files. The outer runner owns tests and
Git; no suites/native probes/Git writes/global installs inside Codex. PASS means
a prepared repair candidate, not native acceptance or security approval.

At least one source/test byte must change before tests/probes; unchanged or
report-only candidates cannot trigger a blind retry. The runner reruns related
checks, independent read-only Claude review, both full Python suites and ALL
fourteen Mac scenarios, including both >=20 parent-SIGKILL variants and <=2s
disappearance. Another mandatory native failure stops immediately for assessment,
without automatic repair/probe retry. The visible diagnostic ZIP remains available.
Only after every required gate passes may the same branch publish one Draft PR,
wait for exact-HEAD CI and stop at READY_FOR_FINAL_REVIEW. Coordinator and
independent security acceptance remain pending; never merge or begin 2.2.2.

## HISTORICAL first native-failure recovery — evidence only

The following current/recovery labels belonged to the previous control revision.
Their invocation and root-cause uncertainty statements are historical, superseded
by the CURRENT section above. Preserve their evidence and original acceptance scope.

# A11b.2.2.1 — bounded recovery after native SIGINT failure

## Current invocation and authorization — 2026-10-07

David requested the next step after the coordinator assessed his uploaded native
diagnostics. This authorizes ONE continuation of the committed candidate at
`ce29d4a6a7764e62dc993b76b33996db2fb3455b`, on the existing unpublished branch
`feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`. Do not create/switch a new
branch, restart implementation, discard files, reset, stash or rewrite history.
Pinned main/base remains `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T123142Z-77009
```

This CURRENT section supersedes all historical fresh-branch, quota-run and timeout
invocations below. The task ID is retained because the native acceptance helper
pins it; the new control commit versions the revised recovery authorization. All
original containment acceptance criteria, ten allowed files, required full/Mac/
security gates and forbidden scopes remain authoritative. The code candidate is
implemented but native acceptance FAILED; no old PASS is inherited for new bytes.

Read the coordinator assessment copied by the runner into the new run's
`coordinator-assessment.md`. Its control-branch source is
`reports/ai-loop/checkpoints/A11b.2.2.1-sigint-failure-assessment-20261007.md`;
do not assume that new control document already exists on the feature branch.
The runner saves the original summary and Mac JSON under the new run's
`recovery-evidence/`, and copies/hashes all ten original candidate files before
Codex. Read them and `recovery.json` before editing. The original run is
`20261007T123142Z-77009`, pinned control `b75ea2e371fd4e8e6130a4b2be8a48e7256b25aa`.

Recovery preflight requires the exact stopped HEAD, unchanged tracked/index state,
matching original failure/source hashes and no published feature branch/PR. Current
unrelated untracked artifacts are fingerprinted and preserved; their historical
integrity is not retrospectively asserted. A read-only PID-presence check rejects
retained old probe processes and ambiguous inspection before models/spawns. Never
kill an old snapshot PID/PGID or delete a one-use marker to bypass it. If the runner
reports retained processes, inspect fresh process state, full executable/host path
and parent-child relationship before operator resolution. Reboot is not a required
step. Do not kill a remembered PID blindly or signal a zombie. The coordinator can
authorize terminating only the confirmed stuck standalone fake-test parent; the OS
then adopts its exited child for reaping. Failed preflight does not consume the
recovery, and operator cleanup is not containment PASS.

## Current bounded repair requirements

1. **Fix proven diagnostic loss first.** Preserve the primary signal/protocol/identity
   exception separately from later cleanup failures. Record bounded exception type,
   message and traceback/location, current signal timing variant, expected event,
   collected protocol/event rows and cleanup outcome. Retain ownership on unresolved
   containment; no new normal return or restored handlers while still unresolved.
   Do not include environment values, credentials, provider output or arbitrary
   process argv. Never hide the primary exception with a `finally` cleanup error.
2. **Investigate the native zombie/SID risk; do not assume the root cause.**
   `identity()` uses `os.getsid(pid)`; CANCEL can leave the supervisor exited but
   unreaped before `Anchor.kill_group`. The preliminary Claude review explicitly
   warned about this Darwin path. Keep a truthful distinction between source
   inference and observed native evidence; the old JSON contains no traceback.
3. Repair that path only with a defensible identity/continuity proof within the
   accepted supervisor-as-group-anchor topology. An ordinary live-process identity
   query must still reject mismatch/ambiguity. Any special handling of an owned
   unreaped zombie must bind the direct-child/Popen ownership, PID=PGID=SID launch
   anchor, UID/PPID/kernel start identity, fresh native zombie state and no-reaper
   host premise. Do not turn ESRCH into automatic identity PASS; do not equate
   terminal foreground group (`tpgid`) with session ID or use cached numbers for an
   arbitrary process. If no safe supported proof exists, return NEEDS_DECISION/
   NOT SUPPORTED instead of lowering identity requirements.
4. Add focused deterministic regression coverage for primary-vs-cleanup error
   retention and live/zombie identity continuity, including mismatch/reuse/race
   rejection. Expand the actual-Mac zombie/non-reaping acceptance evidence where
   needed; never mock or skip a native capability into PASS. Keep all fourteen
   original scenarios, both >=20 parent-SIGKILL variants and <=2s disappearance.
5. Preserve historical validation report content. Append this original SIGINT FAIL,
   related/full/preliminary-review results as historical evidence only. Mark new
   repair/native/full/security gates pending until the runner supplies exact-byte
   evidence. The original failure remains FAIL even if a repaired run later passes.

Codex may make at most **two repair calls in this continuation**, including its first
call. It edits only existing allowed files; the outer runner owns tests and Git.
Do not run suites, native process/signal probes, Git writes or global installs
inside the Codex call. Return PASS only for a prepared repair candidate, not native
containment approval. Diagnostic-only changes may be used if the native cause is
not yet established; their next native result must remain truthful and fail closed.

At least one source/test byte must change before a new test/probe attempt; unchanged
or report-only candidates cannot trigger blind native retry. Then the runner runs
related checks, read-only Claude review, both full Python suites, and all mandatory
native gates on the repaired bytes. Another mandatory Mac failure stops immediately
for coordinator assessment, with no automatic retry. A curated ZIP is written in
the visible `ai-loop-diagnostics/` directory. If all required gates pass, the runner
publishes ONE Draft PR from this same branch and waits for exact-HEAD CI, then stops
at READY_FOR_FINAL_REVIEW. Independent security review and coordinator acceptance
remain pending; no automatic merge and no A11b.2.2.2.

## Historical implementation contract and original acceptance requirements

The following records the originating scope and earlier recovery decisions. Its
acceptance requirements still apply; its old startup/resume instructions do not.

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
group-gone proof: follow section 8's supervisor-anchor ordering. Freshly verify the
still-owned, unreaped supervisor, signal its owned group while that supervisor pins
the PID/PGID, and make the group-cleanup decision before any supervisor reap. Then
reap the supervisor and verify eventual group/descendant disappearance. Never reap
first and subsequently signal the remembered group number. No normal return/restoration while an owned process may live. Explicitly
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

This replaces the stopped run's snapshot-only fallback and the earlier separate-child-process-group assumption. For this recovery, section 8 also supersedes B5 section 11.2's old supervisor-kill/reap-before-group-fallback ordering: the supervisor-owned group cleanup decision MUST precede supervisor reap. All identity, disappearance, bounded cleanup, typed failure and independent review requirements remain mandatory. Do not add a separate anchor process and do not depend on Apple private audit-token signaling. Independent security review after implementation must explicitly verify this remediation.

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

The initial Mac run `20261007T064116Z-68730` stopped at NEEDS_DECISION before source/test implementation. The coordinator imported David's provided transcript to `reports/ai-loop/checkpoints/A11b.2.2.1-containment-20261007-20261007T064116Z-68730.md` on the control branch. Read that report before preparing recovery. F3 continuity was unresolved at that checkpoint. Section 8 now authorizes a supervisor-anchor recovery design, whose implementation and Mac evidence remain pending; no alternative per-member design is accepted. Preserve the old Mac branch/files; execute only the fresh recovery task.

`publish_decision_checkpoint: true` authorizes the outer runner to publish a bounded curated checkpoint to the control branch ONLY when the initial new-task Codex result is valid NEEDS_DECISION, HEAD stays at the pinned base, and only the designated acceptance report changed. No raw logs, source/index changes, feature PR, PASS, merge or automatic resume accompany that publication. Control drift/collision/API errors preserve local evidence and do not retry. Other stop types remain local for coordinator diagnosis. This update applies to future invocations; it does not rerun or repair the already stopped run.

## Repo-local launcher installation — 2026-10-07


David selected `scripts/ai-loop.sh` inside the canonical repository as the entry
point. The parent bootstrap remains compatible. `bash ai-loop.sh --install-launcher`
from the canonical parent installs only the pinned launcher and exits before task
loading/preflight/model calls; it preserves the existing stopped branch/report.
Installation is not task recovery or acceptance. After installation, only the
fresh F3 recovery task authorized above may run; do not restart the old stopped task.


## F3 recovery authorization — 2026-10-07

The prior run `20261007T064116Z-68730` is retained only as a STOP/decision checkpoint. Its snapshot-then-killpg objection is accepted. This recovery remains the same A11b.2.2.1 milestone and may implement the supervisor-as-group-anchor topology above. The old local branch/report must remain untouched; the new recovery branch is the sole implementation branch. If actual Mac probes or review find a new BLOCKER/HIGH, stop again. Do not weaken gates, start 2.2.2, or chase LOW/NOTE findings into new sub-milestones.


## Explicit initial quota recovery — 2026-10-07

David reported that Codex usage has recovered and asked to continue. The run
`20261007T075141Z-70725` at control `523c30afa16384152d3a1c2bd0a03cd2020a8fdf` stopped during
initial Codex with exit 1 and the recorded usage-limit error. This authorizes only
`bash scripts/ai-loop.sh --resume 20261007T075141Z-70725` on the existing unpublished
F3 recovery branch at the pinned main/base. The JSON implementation scope,
acceptance criteria, test commands, full checks, Mac probes and repair bounds
remain identical to that interrupted contract.

Retain existing allowed source/test/report files as the partial candidate. Read
and continue them; do not restart implementation or overwrite historical evidence.
The runner snapshots these files before Codex and preserves all current unrelated
untracked files, including old reports, launcher, benchmarks and case directories.
The previous runner did not save an initial unrelated-file inventory: integrity
before this recovery is UNVERIFIED, not retrospectively asserted. No old test,
review or containment PASS is inherited. Run all required gates on the resulting
bytes before publication. This is a fresh bounded implementation continuation,
not a Codex session resume, general retry service or approval bypass. Another
quota interruption or failure stops again and requires coordinator assessment.

Normal invocation still rejects task-file/branch collisions. Recovery rejects a
changed contract, branch/base, published branch/PR, non-quota/decision/test failure,
unsafe paths or missing provenance. Do not delete files or reset to pass a guard.

A one-use recovery claim is recorded before Codex starts. Repeating this recovery
after another stop is rejected; do not delete its marker to bypass the guard.


## Current recovery: interrupted initial Codex timeout — 2026-10-07

This section supersedes the preceding quota-run invocation for the current launch.
The continuation `20261007T113658Z-74880` at control `01cd4757b23ce0ec03f6b2c1e0848f849ebda01a`
stopped with `command timed out: codex`. The supplied final log segment contains
ongoing source/test edits; it is not evidence of implementation completion or PASS.
Continue that partial candidate on the same unpublished F3 recovery branch/base:

```sh
bash scripts/ai-loop.sh --resume 20261007T113658Z-74880
```

Only the Codex call deadline changes from 1800 to 3600 seconds. Other command/test/
Claude deadlines, allowed/required files, repair bounds and all full/native Mac/
review/security gates stay unchanged. Complete a bounded audit of the current
candidate against the contract, implement only missing requirements or correctness
repairs, and return structured output for the outer runner. Do not restart from
scratch, perform optional refactoring or run expensive suites/probes yourself.

The new explicit one-use recovery verifies the pinned prior task and timeout STOP,
requires unchanged preserved-untracked fingerprints from that run's recovery.json,
rejects new out-of-scope files, staged/index changes, unrelated tracked edits or a
published branch/PR, and rejects active ai-loop Codex exec calls bound to this repository's log paths. Allowed tracked
edits and untracked candidate files are retained in the new recovery-input snapshot
before another model call. No old test/review evidence is inherited. The process
check is not proof of B5 CLI descendant containment. Another failure stops; do not
delete either old recovery marker, files, reports or logs to retry blindly.


## Current AI deadlines and progress — David's update 2026-10-07

David requested simple progress descriptions instead of `[Running] ...` and a
three-hour deadline. This supersedes the earlier 30/60-minute AI deadlines:
Codex and Claude each have a bounded 10800-second limit per call. Test/other command
deadlines remain `timeout_seconds=1800`; CLI turn limits, repair bounds, scope,
required full/Mac checks, publication/security/merge gates are unchanged.

Progress states the assigned phase/purpose, elapsed time, deadline and whether local
CLI log bytes changed. It does not claim which file the model is currently reading,
completion percentage, successful validation or access to live internal reasoning.
Unchanged logs are a wait observation, not proof of a hang. Raw provider output,
thoughts, prompts or credentials are not streamed to the terminal. The running
process keeps its pinned runner/contract; these settings apply on the next launch,
not retroactively to David's current Claude review. Do not interrupt/restart that
run merely to refresh progress. Existing one-use recovery/publication guards stay.
