# A11a confirmed Bug Hunt fixes — 2026-10-05

Status: LOCAL VALIDATION PASS; final publication CI is recorded in the Draft PR/final delivery. Not merge approval. Claude re-review and ChatGPT adjudication remain required.

## Task Contract v1

- Task: reproduce and minimally fix only B1–B7 from the complete original Bug Hunt report; permanent deterministic offline regression tests must fail before production fixes and pass afterward. A non-reproduced bug is NOT REPRODUCED ON CURRENT BASE and receives no speculative fix.
- Canonical checkout: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`; no clone, worktree or project copy. Temporary runtime/fixture artifacts are allowed.
- Fresh fetched base: `e3869828b0435ad79fe54c22701e914bc8a29cb0`; branch `fix/a11a-post-acceptance-bugs-20261005`. Latest main and Bug Hunt base match; intervening PR #25 acceptance commits change documentation only, with no B1–B7 production fixes.
- Original report pinned at remote `docs/llmtool-a11a-bug-hunt-20261005`, SHA `feddc7e15d682824bd684bbd123a9be43766bf5e`; original historical bytes are not edited or imported into this branch.
- Roles: Claude bug finder/later reviewer; Codex reproducer/developer/regression author and sole Git writer; ChatGPT final adjudication/merge decision. No autonomous merge.
- Forbidden: L1/L2, M2/M4/M5, doctor P1–P4, N1 remediation; QA/A11b/executor/retry/fallback; weakened assertions/security/bounds; deletion of unknown or unrelated user artifacts; force push/history rewrite/reset/stash.
- Gates: per-bug baseline reproduction/failed permanent test, minimal fix and focused pass; full canonical Python 3.9 and checkout venv, available local 3.14; diff/security review; grouped normal commits with B2 independent; normal push/draft PR; final exact-head GitHub 3.9/3.14 canonical and clean-tree CI. Stop for Claude re-review.
- Preserve unrelated benchmark/reasoning artifacts. Three explicitly named Claude synthetic case directories may only be removed after safe provenance/content/tracking checks; retaining them is allowed.

## B2 proposed invariants (before production changes)

Eviction needs a durable, bounded intent outside the run before any destructive step.
Only a fully validated, eligible candidate acquired under root EX → run NB may create
that intent. Intent binds exact directory identity and the complete original known-file
inventory/fingerprints. Publish complete intent atomically after file/directory fsync;
partial unpublished intent remains unknown and is never treated as cleanup authority.
Recovery under the same lock order validates the intent and the remaining namespace,
owner/private modes, single links, identities and fingerprints before deleting anything.
Missing original files are authorized only by the committed intent; unknown, replaced,
symlinked, hardlinked or active entries are preserved. Keep the run lock until the last
data file is removed; a lockless directory must be empty and match the committed identity.
Keep intent through rmdir and runs fsync, then remove/sync intent. Recovery handles an
already removed directory without touching a replacement. No malformed run inference,
filename/owner/mode/grace-only deletion, repair or cleanup of N1/M2 data is authorized.
Existing count/byte accounting, bounded descriptors, sorting and abandoned grace remain.
SIGKILL interruption evidence is distinct from untested power-loss/network-FS durability
and the existing unmanaged same-UID namespace limitation.

The preceding proposal is retained as pre-implementation history. Self-review tightened
its lockless-directory rule: the final design below never deletes an unpinned, empty
directory after restart. This is an in-scope safety refinement, not authorization to
repair unknown data.

## Contract and evidence identity

Task ID: `A11A-B1-B7-20261005`, version 1. The unchanged bullet payload under
Task Contract v1 was pinned before tests/production changes. Its UTF-8 SHA256,
calculated at documentation cutoff, is
`199866aa0cdb50b5aaeb8bbea7c9c905a95ce435133fc36332116618d7455de5`.

Tested final implementation: `cd31abea98303aac306ef440727af6c57129ee8f`.
This report is committed separately; its eventual publication HEAD contains identical
production/test bytes. Final publication HEAD, Draft PR number and exact-head CI jobs
are authoritative in the PR description/Checks and final delivery, recorded after
push. This pre-publication report cannot embed its own future commit SHA or treat an
older CI run as final-head evidence. No recursive report-only evidence commit is used.

Environment: actual canonical Mac checkout, Darwin x86_64. All commands below were
launched from that root; synthetic fixtures/runtime directories are not project copies.
Provider behavior is mocked/offline; no live provider, auth inspection, user-config
mutation, retry or fallback was performed.

## Reproduction before production fixes

The freshly fetched base and Bug Hunt base were both
`e3869828b0435ad79fe54c22701e914bc8a29cb0`. The [complete original report](https://github.com/davidcatcat/davidcattoolsproject/blob/feddc7e15d682824bd684bbd123a9be43766bf5e/reports/llmtool_a11a_full_bug_hunt_20261005.md) was read,
and intervening history inspected; no subsequent B1–B7 fixes existed. Production
`git diff --name-only` was empty when the initial five new regression files ran.

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v test_diagnostics_cli_status_regressions test_diagnostics_metadata_regressions test_diagnostics_classification_regressions test_diagnostics_commit_regressions test_diagnostics_eviction_regressions
```

Python 3.13.3; exit 1; 20 tests, 23 failed assertions/subtests, no errors. Six control
methods already passed. Captured log: `/private/tmp/llmtool-b1-b7-before.log`, SHA256
`2067611c9d33ba5db7a16601c412dd96c16f77312503ca7d4067368c197720bd`.
Tests were written first, then production fixes applied and focused tests run for
each group. Later additions strengthen interruption/replacement/publication coverage.

| Bug | Actual base behavior | Expected behavior / final disposition |
| --- | --- | --- |
| B1 | Real OS pipe, reader closed, small buffered ask output: exit 120, interpreter flush noise, stored status success. runs/trace/doctor also exit 120. | Exit 0, quiet stderr, ask status output_closed; normal stdout bytes unchanged. REPRODUCED → TEST FAILED → FIXED → TEST PASS. |
| B2 | EIO or SIGKILL after partial unlink, or empty-dir rmdir fault: next start with max_runs=1 permanently FULL. | Recover verified eviction, preserve foreign/active data, next start succeeds. REPRODUCED → TEST FAILED → FIXED → TEST PASS. |
| B3 | Invalid model/token cleared all metadata, including valid duration/token/model fields. | Validate allowlisted fields independently; omit only bad fields, account drops, preserve order. REPRODUCED → TEST FAILED → FIXED → TEST PASS. |
| B4 | Explicit save plus invalid max_runs=0: CONFIG_INVALID, provider calls=0, trace=not-saved. | Requested failure is unavailable; no-save remains not-saved; input/config precedence unchanged. REPRODUCED → TEST FAILED → FIXED → TEST PASS. |
| B5 | Symlink/non-directory lock or directory boundaries classified UNAVAILABLE; data-file symlinks already UNSAFE. | Identified no-follow violations UNSAFE; genuine EIO/EACCES UNAVAILABLE. REPRODUCED → TEST FAILED → FIXED → TEST PASS. |
| B6 | Incomplete run with error trace and no summary rendered final_error_code=none. | Human row unknown; completed success none, completed failure actual code. REPRODUCED → TEST FAILED → FIXED → TEST PASS. |
| B7 | Backend durable persisted=true, then close EIO: runtime false / trace unavailable despite readable completed artifacts. | Durable result remains true / saved, bounded secondary release warning; prepublication faults remain false. REPRODUCED → TEST FAILED → FIXED → TEST PASS. |

No finding was NOT REPRODUCED ON CURRENT BASE.

## Minimal fixes and permanent regression coverage

### B1 / B4 / B6 — CLI/status correctness

Flush output exactly once before recording ask success; flush runs/trace/doctor inside
their existing error boundary. On BrokenPipeError, redirect only the failed OS descriptor
to devnull to prevent retained buffers flushing into the broken pipe at interpreter exit.
No output is duplicated; redirected normal bytes remain unchanged. Explicit-save
bookkeeping precedes rethrowing invalid settings. Incomplete human rows render unknown
without inferring a trusted final error from an uncompleted trace; reader projection
retains its existing null contract.

`test_diagnostics_cli_status_regressions.py`:

- `test_b1_real_buffered_pipe_ask_exit_and_saved_output_closed`
- `test_b1_real_buffered_pipe_offline_commands_exit_zero_without_flush_noise`
- `test_b1_open_pipe_stdout_bytes_and_single_output_unchanged`
- `test_b4_invalid_settings_explicit_save_reports_requested_unavailable`
- `test_b4_explicit_no_save_and_primary_input_error_precedence`
- `test_b6_incomplete_final_error_unknown_completed_none_or_actual`

B1 uses subprocesses and real os.pipe() with the read end already closed, not a mocked
write exception. Synthetic ask checks exactly one provider call. Child HOME isolates
config; only the already-installed PyYAML package directory is added to PYTHONPATH
so actual system Python 3.9 can retain its user-site dependency without restoring HOME.

### B3 — Independent ordered metadata validation

Each selected field validates independently; bad model/token/duration drops only that
field and increments drop accounting. Unknown keys/values are never stringified.
Existing model redaction marker and provider insertion order are preserved.

`test_diagnostics_metadata_regressions.py`:

- `test_b3_bad_model_preserves_valid_duration_tokens_and_order`
- `test_b3_bad_token_preserves_model_and_other_tokens`
- `test_b3_valid_metadata_unknown_values_and_redaction_marker`

Coverage includes Unicode/credential-looking/control-containing model, valid duration
and tokens, negative token, all-valid metadata, redaction marker and objects whose
string/repr conversion raises.

### B5 — Unsafe classification

Reader no-follow directory/lock opens map ELOOP/ENOTDIR to STORE_UNSAFE; writer maps
the same identified violations to DIAGNOSTICS_STORE_UNSAFE. Other filesystem failures
keep their existing classification. No following, chmod, repair or path disclosure.

`test_diagnostics_classification_regressions.py`:

- `test_b5_writer_symlink_and_nondirectory_are_unsafe`
- `test_b5_reader_symlink_boundaries_are_unsafe`
- `test_b5_reader_nondirectory_missing_permissions_and_real_io`

Coverage: summary/trace/run lock/run directory/runs/store lock symlinks; writer home
symlink/non-directory; missing run NOT_FOUND; unsafe modes; safe complete read; EIO and
EACCES remain UNAVAILABLE. Existing data-file/ownership/security tests remain.

### B7 — Monotonic durable result

Runtime accepts the backend's verified durable finish result independently of secondary
close warnings. Subsequent release failures cannot clear persisted=true. The bounded
warning says saved metadata retained; primary task status/error remains unchanged.

`test_diagnostics_commit_regressions.py`:

- `test_b7_success_remains_saved_after_durable_close_fault`
- `test_b7_primary_error_reports_saved_after_durable_close_fault`
- `test_b7_prepublication_fsync_failure_still_unsaved`
- `test_b7_summary_publication_failure_still_unsaved`

The fault closes the real fd and then raises EIO only after writer.persisted=true.
Tests require all fds released, reader complete artifacts, one bounded warning,
diagnostics_persisted=true and trace=saved on primary error. Before-publication faults
remain unsaved. Two existing ask tests encoded the accepted B5/B7 bugs: their exact
expected classifications/persisted boolean were corrected, and complete reader evidence
added. No security/race/bounds assertion was removed or relaxed.

### B2 — Durable verified eviction, quarantine and conservative recovery

Under root EX → run NB, fully validate the eligible candidate, summary/trace or grace
reservation, complete known inventory and exact stat fingerprints. Write a bounded
private 0600 intent, fsync it, revalidate the original candidate, publish the intent
with native no-replace rename and fsync root **before any destructive step**. The
intent includes run_id, directory identity and all original file fingerprints; partial
unpublished intent never authorizes cleanup.

Atomically no-replace rename the locked candidate from runs/run_id to a unique root
quarantine, syncing both parents, before unlinking files. Root byte/scan accounting still
covers quarantined content; it no longer occupies a run slot. Recovery scans one intent
at a time with bounded descriptors. Before moving a still-original run, reacquire its
lock and fully revalidate the complete original snapshot. In quarantine, reacquire the
original run lock nonblocking, check owner/private modes/single links, directory identity,
the allowed remaining inventory and every original file fingerprint before each unlink.
Keep the verified run lock until all data files are gone; remove it last. Keep receipt
through rmdir and parent fsync. Active locks, unknown names and changed fingerprints fail
closed without deleting those entries. The original runs namespace may be recreated
with foreign data; it is never removed by quarantine recovery.

After restart, a lockless empty quarantine cannot prove its incarnation. **Preserve it**
instead of rmdir based on filename/inode/owner/mode. Retire only the validated private
receipt. It consumes zero run slots/file bytes but remains charged to the bounded root
scan budget, consistent with deferred N1. A live verified fd/lock pins the original
incarnation and allows normal empty quarantine removal. Replaced nonempty directories,
symlink/hardlink files, unknown content, malformed/unsafe intent and active locks remain
conservative failures. No malformed legacy run/reservation repair was added.

`test_diagnostics_eviction_regressions.py`:

- `test_b2_unlink_fault_next_start_recovers_max_runs_one`
- `test_b2_sigkill_during_unlink_next_start_recovers`
- `test_b2_interrupted_empty_directory_rmdir_recovers`
- `test_b2_recovery_preserves_foreign_directory_replacement`
- `test_b2_recovery_preserves_recreated_original_run_namespace`
- `test_b2_lockless_quarantine_foreign_empty_replacement_is_preserved`
- `test_b2_recovery_preserves_symlink_hardlink_and_active_run`
- `test_b2_recovery_preserves_unknown_and_regular_file_replacement`
- `test_b2_unpublished_intent_write_failure_rolls_back_owned_file_only`
- `test_b2_malformed_or_unsafe_intents_never_authorize_foreign_deletion`

SIGKILL is deterministic in a child, at seven points: intent publication before move,
quarantine rename before parent sync, before first unlink, before second unlink, after
last lock unlink/before rmdir, after rmdir and after receipt unlink. Each verifies exit
-SIGKILL and next start with max_runs=1. Lockless empty residual is explicitly preserved;
other tested completed recoveries remove their quarantine. EIO second unlink/rmdir,
receipt short-write failure, foreign directory and original-run namespace replacement,
same-content different file inode, symlink, hardlink, unknown and active lock are covered.
SIGKILL does not execute rollback; these tests prove restart recovery at tested process
interruption points, not power-loss durability on every filesystem.

## Focused and canonical validation

Focused commands share prefix
`PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v`
unless the Python 3.9 row specifies `/usr/bin/python3`. Logs live in `/private/tmp`;
no project checkout or copy exists there.

| Group | Modules after the shared prefix | Result | Corresponding production commit |
| --- | --- | --- | --- |
| B1/B4/B6 | test_diagnostics_cli_status_regressions test_diagnostics_runtime test_diagnostics_ask_persistence test_diagnostics_reader test_doctor | Exit 0; 146 tests PASS | d3bd89f |
| B3 | test_diagnostics_metadata_regressions test_diagnostics_runtime test_diagnostics_ask_persistence | Exit 0; 82 tests PASS | df54215 |
| B5 | test_diagnostics_classification_regressions test_diagnostics_store test_diagnostics_reader test_doctor | Exit 0; 109 tests PASS | 78747ee |
| B7 | test_diagnostics_commit_regressions test_diagnostics_runtime test_diagnostics_ask_persistence test_diagnostics_store | Exit 0; 128 tests PASS | f4578ad |
| B2 final / B1 Python 3.9 compatibility | `/usr/bin/python3 -m unittest -v test_diagnostics_cli_status_regressions test_diagnostics_eviction_regressions test_diagnostics_store` with the same PYTHONPATH | Exit 0; 61 tests PASS | cd31abe / 0519ab0 |

Focused suites were run before their commits; corresponding source patches were
committed unchanged afterward. Log SHA256 evidence:

| Log | SHA256 |
| --- | --- |
| llmtool-b1-b4-b6-focused.log | d646d7fb2c4c31f4cc0b134b1cff8567944ce0981b755c8df712cd069e7ce03b |
| llmtool-b3-focused.log | 1a9715bd17a6d6b67c13ed5b0531b93e15379c3ca278e4bdb4588ceb81501ca2 |
| llmtool-b5-focused.log | 3c4d272d7602139588a7bc3022126ae8849c59c89acca48576324feeaa3376a9 |
| llmtool-b7-focused-final.log | 8f5870074ca1028699de5bea40d37d8fae9d2463dfe13d39c1828ef4b3952fdc |
| llmtool-bugfix-py39-focused-final.log | d63fdb54e388d625ea65d83adf41b5510deb52bdd6a443842d7bff7c841e9c23 |

Final local full checks run on `cd31abea98303aac306ef440727af6c57129ee8f`:

```sh
make -C tools/llmtool check PYTHON=/Users/davidsun/git/davidcataiporject/davidcattoolsproject/tools/llmtool/.venv/bin/python
make -C tools/llmtool check PYTHON=/usr/bin/python3
make -C tools/llmtool check PYTHON=/private/tmp/llmtool-a11a7-py314-venv/bin/python
git diff --check
```

The last Python is an already-existing isolated runtime, not a checkout; canonical
source/tests/cwd remain in the required repository. No new runtime was installed.

| Python / PyYAML | Command exit | Full canonical result | Captured log |
| --- | --- | --- | --- |
| 3.13.3 / 6.0.3 checkout venv | 0 | 893 tests, 80.248s, OK (skipped=1) | llmtool-bugfix-final-py313.log |
| 3.9.6 / 6.0 system Python | 0 | 893 tests, 119.917s, OK (skipped=1) | llmtool-bugfix-final-py39.log |
| 3.14.8 / 6.0.3 isolated runtime | 0 | 893 tests, 74.221s, OK (skipped=1) | llmtool-bugfix-final-py314.log |

Local skip: `test_unusual_filename_bytes_roundtrip`, because the host filesystem/sandbox
rejects non-UTF-8 filename bytes. It is existing infrastructure behavior, not a changed
assertion. The full suite includes all 26 new regression methods, settings precedence,
default-off/ephemeral CLI, single mocked provider calls, security/race/bounds, low
RLIMIT_NOFILE capacity/cleanup/fd-lifetime, stale completed reservation reconciliation
and two-process reservation/concurrency tests. All those tests PASS in the finished
local suites. No full-suite test was removed or skipped by this change.

Final source/test patch (`git diff --binary e3869828... HEAD -- tools/llmtool`) SHA256:
`ac98959569425e174bf5e0b30903456230a8787c4c432b57073cba194f9ed2ee`.
Full log SHA256: Python 3.13
`3aa04b225a2ccc76d9ed768bf54f07236c955fbebe2b6cf6cd32e327ce580524`;
Python 3.9 `17708be5d9925d71891f0c5f9aa52dc1fbe07c790e9e76e93d71499864d1472f`;
Python 3.14 `17023d59a19ff19c14640042e64fae95046378e050971b53d57c01eeaab98ea2`.

Intermediate failures are retained as history, not final evidence: the first B7 focus
found two old assertions encoding accepted B5/B7 behavior, corrected strictly above;
an initial B2 check identified APFS directory nlink mutation and prompted retaining full
file fingerprints while checking stable directory identity; a preliminary Python 3.9
full run failed three new pipe-probe cases because isolated HOME hid installed PyYAML.
The test package-path fix preserves HOME isolation and all assertions; final 3.9 PASS.
Self-review then replaced direct run deletion with quarantine so a lockless empty
restart residual is preserved rather than deleting an unprovable directory.

## Commits, changed files and publication gate

| Commit | Purpose |
| --- | --- |
| d3bd89f75fa385399156346c12947c7eb3f7db15 | B1/B4/B6 CLI/output/status correctness |
| df5421522ada3e42b14fa7bc974cb19905b66118 | B3 independent ordered metadata validation |
| 78747ee2884c497ce85e6a9e8176d77ec44177e2 | B5 unsafe no-follow classification |
| f4578ad03eb4b804de766f879bd83a5bee50f5ec | B7 durable commit result/secondary close warning |
| cbe193dd6f44ff7097dd0fa5982ac0598edf84eb | B2 initial durable verified eviction/recovery |
| 0519ab003f2f2cb02c31b6417b7dadd0cf0ed762 | B1 isolated Python 3.9 probe dependencies |
| cd31abea98303aac306ef440727af6c57129ee8f | B2-only quarantine safety refinement / stronger regressions |

B2 remains independent; the safety follow-up preserves history rather than amending.
A separate docs commit publishes this report. Only these task files are staged.

Production files, under `tools/llmtool/llmtool/`: `cli.py`, `diagnostics_cli.py`,
`diagnostics_reader.py`, `diagnostics_runtime.py`, `diagnostics_store.py`, `doctor.py`,
`terminal.py`.

Tests, under `tools/llmtool/tests/`: modified `test_diagnostics_ask_persistence.py`;
new `test_diagnostics_cli_status_regressions.py`,
`test_diagnostics_metadata_regressions.py`, `test_diagnostics_classification_regressions.py`,
`test_diagnostics_commit_regressions.py`, `test_diagnostics_eviction_regressions.py`.
Only new documentation is this report; original Bug Hunt and milestone review reports,
AGENTS/WORKFLOW, packaged skills, user config and provider implementation are unchanged.

Pre-publication fetch confirmed origin/main still `e3869828...`; implementation ahead 7,
behind 0. Recheck counts/remote equality after the report push. Final exact-head CI must
show Python 3.9 and 3.14 SUCCESS, and both Offline canonical checks and Working tree is
clean steps SUCCESS. The final Draft PR Checks/description and delivery record the
actual new report HEAD and CI run/job URLs/statuses; none of the earlier CI runs qualifies.
If those checks fail or are missing, READY FOR CLAUDE RE-REVIEW remains NO.

## Security, preserved artifacts and limits

Self-review covers the complete production/test diff against the fetched base and
accepted requirements. Preserve no-follow dirfd traversal, private owner/mode validation,
hardlink rejection, identity/fingerprint checks, root→run lock ordering, active protection,
unknown-data preservation, default-off persistence, deterministic existing ordering,
capacity reservation/byte bounds and abandoned grace. No independent Claude re-review
or ChatGPT adjudication has occurred for these changes; both remain external gates.

The six pre-existing benchmark/reasoning report files/directories and three Claude
case directories (`case.gnwz8ul9`, `case.k8z3qd3e`, `case.pvdqfxsv`) remain untouched.
Optional case deletion was not exercised. New tests use self-cleaning synthetic temporary
fixtures; captured evidence logs use ordinary temporary files. No task runtime artifacts
were added to the repository. Tracked tree should be clean after report commit; the
known unrelated/pre-existing untracked paths are deliberately retained.

- L1/L2 unchanged: trace link/unlink interruption and wall-clock rollback/grace remain
  deferred; no speculative fixes.
- M2/M4/M5, doctor P1–P4 and N1 remain deferred. B5 corrects only the explicitly accepted
  unsafe error classification; it does not claim the broader deferred findings fixed.
- Unknown staging, partial unpublished intent and preserved lockless empty quarantine
  retain conservative root bytes/scan accounting. Repeated residuals can exhaust the
  root scan budget; no unrequested automatic cleanup or repair is added.
- Legacy malformed runs/reservations without committed eviction intent still fail closed;
  nothing is deleted based only on filename/owner/mode/grace.
- Native no-replace rename support remains required. Same-UID adversarial stat→unlink
  replacement windows remain the existing documented boundary; this is not isolation
  from arbitrary writers of the same user's private filesystem.
- Power-loss testing, unsupported/network filesystem durability and live-provider tests
  are NOT-TESTED. Real OS pipes, EIO injections and deterministic SIGKILL are executed
  evidence for their specified cases, not a universal crash-safety claim.
- runs sorting/--json and other pre-existing limitations are unchanged. No QA/A11b,
  executor, retry, fallback, broad architecture refactor or merge is started.

All seven accepted bugs have safe scoped fixes and permanent regressions. Remaining
limits are explicit deferred/non-tested boundaries, not claims that unknown data is safe
to repair. Stop after normal branch publication, Draft PR and final-head CI for Claude
re-review; ChatGPT retains the merge decision.
