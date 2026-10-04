# PR #24 — M1/M3 persistence hardening validation — 2026-10-04

Status: M1/M3 implementation, independent code/test review and actual Mac full
canonical Python 3.13/3.9 suites **PASS** on the correction-round-1 frozen patch
below. Publication/final-head CI must be confirmed in the delivery and PR #24;
this is not merge approval. External re-review is required after delivery.
**A11a.7 NOT STARTED.**

## Pinned Task Contract

Coordinator recorded this contract before source/test changes. This is a new,
user-authorized persistence-hardening scope; it preserves and does not silently
rewrite the earlier A11a6-offline-doctor v1 contract or its historical evidence.
Payload encoding: UTF-8/LF with one final newline.

```text
Task ID: PR24-M1-M3-persistence-hardening
Version: v1
Base: 3d0d04c78612c11ec615c8e79050616ae1fcf12d
Goal: safely recover handled start() initialization failures and bound capacity cleanup descriptor usage, addressing Claude M1 and M3 only.
Authorized scope: diagnostics store start/capacity initialization and candidate lifecycle; narrowly required store/CLI regression tests; Task Contract, validation report and existing PR24 description.
Forbidden scope: P1 doctor damaged-count/output contract; new content-reading/repair features; runs --json; ordering changes; broad M2/M4/M5 remediation; settings bounds reduction; retry/fallback/provider switching; A11a.7/later implementation; main merge; new checkout/worktree/clone; force push/history rewrite; unrelated user artifacts; Developer/Reviewer Git mutations.
M1 acceptance: inspect mkdir, run-lock create/acquire, reservation create/write/short-write/fsync/publication, trace creation and later initialization failures; handled failure rollback must execute within required held initialization locks and remove only this attempt's created identity-verified entries; preserve collisions, unknown/replaced/hardlinked/symlink data and active runs. After clearing injected faults, subsequent save must succeed where safe rollback is possible.
M1 crash acceptance: explicitly design crash-safe reservation initialization/publication and test interruption points. SIGKILL never executes except/finally; rollback is not crash recovery. Preserve fail-closed for preexisting malformed/unprovable artifacts and document unresolved crash windows. Names/owner/mode/grace alone never authorize deleting a malformed run.
M3 acceptance: scan candidates with bounded live descriptors, store metadata rather than all candidate fds; reopen, lock and fully revalidate identities/data for reconciliation/deletion. Preserve root-before-run lock order, nonblocking active-run protection, full validation, conservative capacity accounting and current completion-time/run_id ordering; do not lower max_runs.
Required tests: creation/partial-write/fsync/later initialization recovery; collision/replacement/unknown/active protection; explicit crash interruption cases; isolated subprocess low RLIMIT_NOFILE with enough runs to exceed former descriptor demand, successful start/capacity/cleanup and no fd leaks; stale completed reservation reconciliation; existing security/race/concurrency tests; unchanged ask stdout/exits/default-off persistence/single provider call.
Required evidence: canonical Mac root/branch/fetched HEAD/main/upstream; focused and full canonical tests with exits/counts/platform; actual Python 3.9 full suite where available; git diff --check; exact source/test patch digest; independent read-only review against frozen bytes; at most two correction rounds; report-only provenance changes independently checked; final-head Python 3.9/3.14 CI including offline canonical and clean-tree steps; updated PR24 and normal push.
Roles: requested Developer gpt-6.1-sol/high sole source/test writer; requested independent Reviewer gpt-6.1-sol/high read-only; gpt-5.6-luna/low unavailable in native catalog, coordinator sole Git writer. Requested model selection is recorded separately from unverified actual runtime attestation.
Delivery: publish only existing feat/a11a6-offline-doctor-cloud-20261004 / PR24, no merge; wait for external re-review; M2/M4/M5 and doctor P findings remain explicit deferred follow-ups.
Verification environment: actual canonical Mac; offline tests and isolated synthetic subprocess/filesystem fixtures. No live provider request, config mutation or credential inspection is required for this store-only scope; no provider access is authorized by this contract.
```

Contract SHA-256: `bd4c2b220f6627d00a222f0883f42dcea0ecda855ec93fa2953ea887cee1dd99`.

## Preflight and review baseline

- Actual canonical root/cwd:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `feat/a11a6-offline-doctor-cloud-20261004`, local and fetched remote
  HEAD `3d0d04c78612c11ec615c8e79050616ae1fcf12d`, upstream divergence 0/0.
- Fetched origin/main: `da027f0d728e3b3ff605a6d404c49c8beb08ec28`;
  HEAD/main ahead 4, behind 0. No main integration is needed or performed.
- PR: [#24](https://github.com/davidcatcat/davidcattoolsproject/pull/24).
- Tracked preflight tree clean. Existing untracked benchmark/reasoning reports
  are unrelated and will remain untouched.
- Read current AGENTS.md, llmtool WORKFLOW.md and inspect-repo/implement-feature/
  review-change/safe-git skills on this branch.
- Original findings:
  [Claude A11a.6 review](llmtool_a11a6_offline_doctor_review_20261004.md).
  Its tested head was 525f981; fetched 3d0d04c also includes that review report.
- Prior doctor validation remains historical:
  [A11a.6 validation](llmtool_a11a6_offline_doctor_validation_20261004.md).

## Claude finding dispositions

| Finding | Authorization/disposition | Reason |
| --- | --- | --- |
| M1 | ACCEPTED / FIXED within the stated safe initialization boundary | Staged initialization keeps malformed unpublished reservations outside runs; locked file-only rollback and native no-replace directory publication are separately tested. Legacy malformed data and post-publication ambiguity remain preserved/fail-closed. |
| M3 | ACCEPTED / FIXED | Candidate scanning retains metadata rather than candidate descriptors; reopened locked full validation precedes mutation; low-limit and leak tests pass. |
| M2 | DEFERRED, not fixed | Late trace/summary publication remnants and damaged content recovery are outside the narrowly authorized initialization fixes. Any incidental overlap must be identified without claiming M2 solved. |
| M4 | DEFERRED, not fixed | Root-lock full trace validation cost/caching redesign is outside scope; full validation is retained. |
| M5 | DEFERRED, not fixed | Reader scan-limit semantics are unchanged. |
| P1–P5 | DEFERRED, not fixed | No doctor damaged-count, body-reading/repair, credential-label/error taxonomy or merge-status contract changes are authorized here. |

The review's suggested malformed-run deletion based only on known filenames,
private owner/mode and grace is rejected for this scope: those facts do not prove
ownership by this initialization attempt or validate safe deletion. Unprovable
malformed reservations remain fail-closed and preserved.

## Implementation and identity

Frozen source/test patch against base 3d0d04c:
`f0df2717fe9bf3c89e015eb27a8cb756571ad92027b45e05cb0391f46d344bf2`.
Encoding is the raw output of `git diff --` followed by exactly these two paths:

| File | SHA-256 |
| --- | --- |
| tools/llmtool/llmtool/diagnostics_store.py | a3438b7ef491afdf6456cdfd6976bf77533f85627e8da2246f74e72f55bd905b |
| tools/llmtool/tests/test_diagnostics_store.py | 00cc0c6911d1239b1227bab4c5751f163af21960eb9ae4bb563f5f93214267f0 |

Coordinator independently checked the Developer's frozen digest before validation.
The exact committed implementation/publication SHA is recorded in PR #24 and the
final delivery after commit; this report does not fabricate its own commit SHA.
Only the two paths above and this new validation report belong to this hardening
delivery. Existing doctor/reader/ask/config/provider/settings/packaged skill bytes,
original Claude review and previous validation reports are unchanged from base.

### M1: staged initialization and conservative recovery

`start()` holds root EX, creates a private `.run-init-<random>` staging directory
under the anchored store root, outside `runs/`, and acquires its new run EX lock.
It records inode identities for only its O_EXCL-created regular files. Reservation
bytes are completed and fsynced there; trace creation/fsync, directory preparation,
writer construction and complete reservation/event validation precede publication.
Failed attempts remove only matching private single-link created files while the
necessary locks remain held. Unknown/replaced/symlink/hardlinked files or a busy
run lock prevent rollback. **No staging directory is ever removed by rollback**:
POSIX mkdir returns no creation-bound descriptor, so its first stat/open cannot
prove the directory belongs to this attempt against an unmanaged namespace writer.

This closes the independent Reviewer's reproduced mkdir→first-stat replacement
case without weakening `max_runs=1`: the replacement directory inode is retained,
but failed staging is not counted as a run, so the next safe save succeeds after
the injected pre-publication fault is cleared. Unknown staging bytes and names
remain included in existing conservative root byte/scan accounting.

The commit point is native atomic no-replace **directory** publication into runs:
Darwin `renameatx_np` with `RENAME_EXCL=0x4`, Linux `renameat2` with
`RENAME_NOREPLACE=1`, through a narrow ctypes binding. There is no overwriting
os.rename fallback. Native symbol/kernel/filesystem absence or rejection fails
closed. Darwin ABI/flag and actual calls were checked on this Mac (local SDK
sys/stdio.h plus the store tests); final Linux CI must separately verify the Linux
runtime branch. These APIs are documented by
[Apple's safe-save API reference](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/APFS_Guide/ToolsandAPIs/ToolsandAPIs.html)
and [Linux rename manual](https://man7.org/linux/man-pages/man2/rename.2.html).
Atomic exclusive renaming depends on filesystem support, as documented by
[Apple's volume capability](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsexclusiverenaming).

Source namespace versus held directory fd is checked before publication and final
run namespace versus fd afterward. Existing empty target directories and symlinks
cannot be overwritten. Once publication succeeds, failures preserve the published
run; rollback never deletes a final run directory. Parent syncs follow the rename.
Post-publication fsync failure can therefore leave a valid abandoned reservation:
at `max_runs=1`, saving waits for the unchanged one-hour grace, not immediate
recovery. The test verifies actual lock release and successful later recovery.

### Crash-safe initialization boundary and remaining limits

Three isolated subprocesses actually terminate with SIGKILL at reservation partial
write, immediately before directory publication, and immediately after successful
atomic directory publication. Except/finally/rollback do not execute for SIGKILL.

| Interruption | Observed state and recovery |
| --- | --- |
| Partial reservation write in staging | No authoritative run/reservation appears in runs; unknown staging evidence is preserved; subsequent max_runs=1 save succeeds. |
| Fully initialized but unpublished stage | Same preserved/accounted unknown staging behavior; no run slot consumed and subsequent max_runs=1 save succeeds. |
| Atomic directory publication completed | Fully initialized reservation/empty valid trace is visible; active lock is released by process death; existing abandoned grace protects then recovers the run. |

SIGKILL tests prove these process-interruption cases, **not power-loss durability**
or every kernel/filesystem failure. Legacy malformed reservations already in runs
remain fail-closed and are never deleted based on names/owner/mode/age. Retained
stages can eventually consume byte/scan budget; no new auto-repair or unknown-data
cleanup was added. A run/root on different filesystems, or unsupported exclusive
rename filesystem/kernel/libc, can reject publication; persistence then warns and
the primary ask result remains independent.

A narrow unmanaged same-UID source swap between the precheck and native rename can
move a foreign staging replacement into the run namespace. The postcheck rejects
the identity mismatch and preserves unknown data; it never deletes the foreign
directory/files. Tests explicitly retain UNKNOWN in both source and target races.
This is not a claim of complete arbitrary same-UID namespace isolation.

### M3: bounded candidate descriptors

`_capacity()` validates one locked candidate at a time, closes its directory/run
lock fds before the next scan, and stores only metadata/fingerprints. Reconciliation
and deletion reopen and lock the candidate, repeat full reservation/trace/summary
validation and compare the captured directory plus every file identity, private
mode/owner/link count, size and timestamps. Final namespace and per-entry checks
precede unlink/rmdir; replaced, changed, unknown and active entries are retained.

Root-before-run lock ordering, nonblocking run acquisition, full conservative
reservation accounting, age/grace policy and deterministic `(completion time,
run_id)` ordering are retained. max_runs bounds/defaults are unchanged. M4's root
lock/content-validation cost is not optimized; unknown-directory traversal remains
depth-dependent and bounded by the existing scan budget, not a universal constant
fd guarantee for arbitrary unknown trees.

## Required test coverage

All concrete references below are in tools/llmtool/tests/test_diagnostics_store.py:

| Acceptance | Test/evidence |
| --- | --- |
| Pre-publication fault recovery | test_start_initialization_faults_rollback_then_save_recovers: 12 stages (mkdir, directory open, constructor, lock create/acquire, reservation create/write/short-write/fsync, trace create, directory fsync, unsupported publication); fault cleared then completed save at unchanged max_runs=1. Run-lock acquisition uses a real independently held flock. |
| Successful short writes | test_initialization_reservation_short_writes_complete: real writes limited to 9 bytes, valid reservation and completed summary/trace. |
| Pre-first-stat replacement | test_mkdir_replacement_before_initial_stat_is_preserved: same-owner empty replacement inode and original directory survive; later max_runs=1 save succeeds. |
| Exclusive publication | test_native_directory_publication_never_overwrites_existing_targets: actual native call preserves existing empty-directory and symlink inode; test_unavailable_native_directory_publication_has_no_rename_fallback rejects absent symbol and traps os.rename. |
| Post-publication failure | test_postpublication_fsync_failure_preserves_valid_run_until_grace: valid run retained, actual run EX can be reacquired, unchanged grace then permits max_runs=1 completed save. |
| Namespace mutation | test_publication_namespace_races_preserve_unknown_data: source and target races retain UNKNOWN and verified staging. |
| Unknown/mutated rollback artifacts | test_start_rollback_preserves_unprovable_inventory: five cases unknown/replacement/hardlink/symlink/directory, no victim data deletion. |
| Reopen validation | test_capacity_reopen_preserves_mutated_replaced_and_active_candidates: eight cases directory/lock/trace replacement, in-place trace/summary/reservation damage, unknown file and actual active lock. |
| Conservative staging accounting | test_unknown_staging_bytes_remain_capacity_accounted: large unknown malformed staging reservation disables save, preserved unchanged. |
| Actual interruption | test_initialization_crash_interruption_windows_remain_conservative: three SIGKILL stages above. |
| Low fd budget and no leaks | test_low_nofile_capacity_cleanup_and_fd_lifetime_in_subprocess: RLIMIT_NOFILE soft=64, 80 completed runs, 10 count-pressure evictions, exact descriptor set equals baseline after every completion; marker NOFILE_64_RUNS_80_NO_LEAK_PASS. Former 160 candidate fds alone exceed this limit. |
| Legacy security/accounting/concurrency | Original 33 store tests retained: malformed reservation fail-closed, symlink/hardlink/private modes/owner, unknown/active preservation, deterministic tie-break, byte/age/count/grace, two-process capacity, independent writer warnings and stale completed reservation reconciliation. |
| Public ask contract | Existing diagnostics ask persistence/runtime/failure matrix tests unchanged and included in focused/full suites: stdout/exit/default-off/location-only behavior, internal/CLI precedence, single provider call, one bounded safe secondary warning and no retry/fallback. |

Twelve new store test methods bring store coverage from 33 to 45; full test count
from the reviewed branch's 855 to 867. This is named case coverage, not a proof of
all races or arbitrary filesystem failures.

## Independent review and bounded correction

Developer `/root/pr24_developer` and independent read-only Reviewer
`/root/pr24_reviewer` were requested as **gpt-6.1-sol / high** and the spawn tool
accepted those selections. Actual runtime model/reasoning attestation is
**UNVERIFIED**. Requested Git agent **gpt-5.6-luna / low UNAVAILABLE** in the native
model catalog; no substitute was silently spawned. Coordinator is the sole Git
writer. Developer edited only the two allowlisted source/test files; Reviewer
edited no repository or Git state. No token/cost/savings measurement is claimed.

Initial patch `04495929a5d5686540e2554df281c7e0503884e2afeaa296c94aac1fbc85a0b2`:
independent **REQUEST CHANGES**, one material R1 finding. The first design trusted
a pathname stat after mkdir as creation identity. Reviewer independently replaced
the empty directory inside the mkdir wrapper before the first stat, injected a
later reservation write failure, and observed rollback incorrectly remove the
replacement. This finding was accepted rather than waived or hidden by weaker
tests/threat assumptions. Initial Reviewer store 39/39 PASS, 9.142 s did not close
that gap; ordinary green tests alone were insufficient.

**Correction round 1** implements the staging protocol described above. Frozen
patch f0df2717 was independently identity-checked and fully reviewed. Reviewer
decision **PASS; R1 CLOSED; no remaining material code/test finding**. Actual Mac
store 45/45 PASS, exit 0, 8.787 s. Reviewer additionally injected 20 repeated
reservation-write failures, compared fd sets before/after, then succeeded with
max_runs=1 (exit 0). Reviewer explicitly retained all documented limitations.
One of at most two correction rounds was used; no second correction was needed.

Transient reviewer model usage/capacity interruptions did not establish PASS and
did not count as code correction rounds. The same requested Reviewer resumed;
no alternate model or coordinator self-approval replaced independent review.
All project source/test/report writes stopped during each actual frozen review.

## Actual Mac validation

Commands launched from the canonical repository; fixtures use ordinary physical
temporary directories only, never project checkouts. Python 3.13.3 is this
checkout's .venv; actual Python 3.9.6 is /usr/bin/python3; macOS 15.7.9 / Darwin.

```sh
PYTHONPATH=tools/llmtool tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p 'test_diagnostics*.py'
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

| Final frozen patch check | Result | Time |
| --- | --- | --- |
| Developer diagnostics focused | 234/234 PASS, exit 0 | 26.397 s |
| Reviewer independent store | 45/45 PASS, exit 0 | 8.787 s |
| Reviewer repeated-fault fd probe | PASS, 20 failures then successful max_runs=1 start, exit 0 | Not separately timed |
| Coordinator full canonical Python 3.13.3 | 867 discovered, 866 passed, 0 failure/error, 1 inherited filesystem skip, exit 0 | 78.901 s |
| Coordinator full canonical actual Python 3.9.6 | 867 discovered, 866 passed, 0 failure/error, 1 inherited filesystem skip, exit 0 | 115.954 s |

Full suites include compileall. The Mac skip is the inherited non-UTF-8 filename
filesystem case; it is not a PASS. Canonical loopback mocks/isolated runtime
fixtures ran with approved host permissions to avoid sandbox-only restrictions.
No test was weakened for sandbox access. Logs:
`/private/tmp/llmtool-pr24-m1m3-round1-py313.log` and
`/private/tmp/llmtool-pr24-m1m3-round1-py39.log`.

Historical superseded evidence: initial frozen implementation focused 228 PASS,
26.034 s; full Python 3.13 861 tests/one skip, 78.312 s, and actual 3.9
861 tests/one skip, 115.996 s, exits 0. These do not validate the correction.
During development a low-limit fixture first used incorrect DiagnosticEvent
positional arguments, and an extra reservation-temp run-ID draw broke an existing
collision fixture. Both failures were fixed without weakening the existing tests;
final staging entropy is independent of public run-ID generation.

No live provider/company request was made for this store-only scope. No user
config/credential/auth/session/Keychain/environment dump access or mutation.
Provider integration evidence here is offline/mock, not fresh live inference.
Claude's earlier company live results remain historical at its tested 525f981.

## Publication and external re-review gate

Coordinator stages only the explicit source/test/report allowlist after canonical
checks and report provenance review. Normal commit/push targets only existing
PR #24 branch; no force/history rewrite/merge/main mutation or new checkout.
PR description and final delivery record exact commit HEAD, latest fetched main,
remote identity/ahead-behind, final-head CI URLs/headSha and Python 3.9/3.14 job
plus Offline canonical checks / Working tree is clean step results. Old CI at
525f981/3d0d04c or any earlier SHA is not final hardening evidence.

Current required publication/CI gates remain pending until independently checked
after commit. External re-review remains required even if all tests/CI are green.
**M2/M4/M5 and doctor findings DEFERRED / NOT FIXED. A11a.7 NOT STARTED.**
