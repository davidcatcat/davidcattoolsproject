# llmtool A11b.2.2.0 cleanup validation — 2026-10-07

## Task Contract and provenance

This slice addresses only L1 read-query cleanup and L2 drift-test evidence hygiene from the A11b.2.1 independent review. The user's explicit authorization follows PR #44's B5 targeted design re-review PASS and merge. Design approval does not authorize the supervisor or ADD execution slices.

- Environment/root: actual Mac, `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Remote: `git@github.com:davidcatcat/davidcattoolsproject.git`.
- Fetched base main / PR #44 merge: `18365bb079835f3b413dd135ea742213bb1a7724`; merge ancestry verified before branch creation.
- Branch: `feat/llmtool-a11b2-2-0-cleanup-20261007`.
- Implementation head: `7692fa3fb49b45b49b38cc1424eaa9811fe4763f`.
- Production file: `tools/llmtool/llmtool/git_mutation.py`.
- Test file: `tools/llmtool/tests/test_git_mutation.py`.
- No Action, approval, argv/environment policy, CLI, supervisor, audit, runtime skill, historical review or benchmark changes.

Acceptance requires preservation of timeout/output-bound/nonzero typed errors despite secondary cleanup faults, explicit pipe closure and owned-child reaping, isolated drift evidence, A5/A11b.1 regression checks, both local Python canonical suites, and no public mutation path. Coordinator review remains pending; this report does not authorize A11b.2.2.1 or A11b.2.2.2.

## L1 cleanup: PASS

Before changing production code, six initial `ReadCleanupTests` methods were run against the base implementation. They failed with seven errors including subtests (exit 1, 120.269 seconds): group `PermissionError`, selector setup/close faults and pipe-close faults could replace the intended typed failure or bypass cleanup. Unclosed-pipe warnings were observed. The initial fake-child safety backstop was 60 seconds; both children exited and were reaped. The final regression revision uses a two-second backstop and explicitly requires `-SIGKILL`, so natural expiry cannot satisfy the test. Two additional cleanup methods were then added. The initial run is evidence for the baseline defect, not a claim that all final test bytes were run before the fix.

The private `_cleanup_read_process` helper retains A5-style process-group termination with an owned-leader fallback for `ProcessLookupError`/macOS `PermissionError`. Selector release, child cleanup/reaping, stdout closure and stderr closure are nested so an earlier cleanup I/O fault does not skip later resource release. Selector construction now occurs inside the protected region. Cleanup I/O faults remain secondary to an already-raised error or interruption. If an otherwise successful query encounters a cleanup fault, it fails with fixed sanitized `Git inspection unavailable` wording.

Eight permanent cleanup methods pass:

| Test | Evidence |
| --- | --- |
| `test_exited_leader_group_permission_error_preserves_output_bound_failure` | Deterministic exited/unreaped leader model; real pipe FDs; stdout and stderr bounds; original typed error, fallback, reap and explicit FD/selector closure |
| `test_live_child_reaped_after_group_signal_failure` | Actual Python child; injected group PermissionError and ProcessLookupError; deterministic timeout; SIGKILL return code, closed pipes and no remaining waitable child |
| `test_selector_setup_failure_still_reaps_and_closes` | Setup failure cannot bypass child or pipe cleanup |
| `test_cleanup_close_fault_cannot_replace_nonzero_head_failure` | Selector-close fault retains `HEAD mismatch` |
| `test_stdout_close_fault_still_closes_stderr_and_preserves_primary` | First pipe-close fault cannot bypass the second pipe |
| `test_wait_cleanup_fault_still_closes_pipes_and_preserves_primary` | Wait fault after modeled reap retains primary failure and closes pipes |
| `test_direct_kill_cleanup_fault_still_reaps_and_preserves_primary` | Exited-leader fallback fault is secondary; wait/pipe cleanup still occurs |
| `test_cleanup_fault_on_success_is_sanitized` | Secondary cleanup fault cannot produce a successful result or expose raw OSError text |

Focused cleanup result: 8/8 PASS, FAIL 0, ERROR 0, SKIP 0; exit 0 (0.020 seconds).

These are read-only cleanup tests, not B5 supervisor or crash-containment evidence. macOS PermissionError is deterministically injected; this run does not repeat the historical timing-dependent native repro. Tests of wait/kill faults use an exited leader model; they do not prove recovery from an OS that denies every termination/reap operation.

## L2 drift evidence cleanup: PASS

| Invariant | Permanent evidence |
| --- | --- |
| Target content | Misleading `test_mid_query_instruction_file_index_root_and_config_drift` renamed to `test_target_content_change_between_queries_rejected`; it claims only the variable actually changed |
| Config | `test_config_change_between_queries_rejected` now changes benign config bytes in place, without adding a rejecting filter or replacing the parent directory; index/target bytes and absence of info attributes are checked |
| Info attributes | New `test_info_attributes_change_between_queries_rejected` changes only comments in an existing `.git/info/attributes`; config/index/target remain unchanged |
| Index | Existing `test_index_change_between_queries_rejected` retained |
| Repository root | Existing `test_root_replacement_between_queries_rejected` retained |
| Git directory | Existing `test_git_directory_replacement_between_queries_rejected` retained |
| Compound late helper risk | Renamed `test_compound_late_filter_and_attribute_change_never_executes_helper`; explicitly labeled compound helper non-execution evidence, not isolated drift evidence |

Existing assertions were retained. File inode/content/mode binding cases rebind the Action between independent checks; they are not represented as isolated mid-query config/index evidence. No test was split solely to increase the count.

## Verification commands and results

All commands were launched from the canonical Mac repo. Targeted discovery uses `PYTHONPATH=tools/llmtool` with `tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p '<file>'`. Full checks include Makefile `compileall` and unittest discovery.

| Check | Python | Discovered | PASS | FAIL | ERROR | SKIP | Seconds | Exit |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `test_git_mutation.py` | 3.13.3 | 129 | 127 | 0 | 0 | 2 | 44.082 | 0 |
| A5 `test_git_intel.py` | 3.13.3 | 39 | 38 | 0 | 0 | 1 | 14.344 | 0 |
| A11b.1 `test_action_contract.py` | 3.13.3 | 21 | 21 | 0 | 0 | 0 | 0.330 | 0 |
| Full canonical check | 3.13.3 | 1166 | 1163 | 0 | 0 | 3 | 210.428 | 0 |
| Full canonical check | 3.9.6 | 1166 | 1163 | 0 | 0 | 3 | 419.339 | 0 |

```sh
make -C tools/llmtool check PYTHON=/Users/davidsun/git/davidcataiporject/davidcattoolsproject/tools/llmtool/.venv/bin/python
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

The first targeted run inside the restricted sandbox had two EPERM errors in existing socket/non-UTF-8 filename tests and one skip. It was not recorded as PASS. The same tests were rerun on the actual host from this repo without weakening assertions: exit 0, two existing host skips. The three full-suite skips are the existing case-collision and non-UTF-8 filename cases in A5/A11b.2.1; no cleanup test is skipped.

Packaging/import/CLI checks: canonical package resolves to this checkout; installed version is 0.1.0; `--help` and `--version` exit 0; `add x`, `git add x` and `git-add x` remain invalid usage with exit 2 and no traceback. The full checks compile production/tests. No packaging entrypoint/discovery change was made. All non-read-runner preflight/helper function/class ASTs are identical to base; production modules outside `git_mutation.py` contain no new import/reference to it. There is still one Popen site, confined to the fixed read-query runner.

Full suites tested the frozen source/test bytes below on base plus the candidate patch. Those bytes are identical to the implementation commit. At committed head `7692fa3fb49b45b49b38cc1424eaa9811fe4763f`, cleanup, between-query drift and public-import tests were reconfirmed on both Python versions: 15/15 PASS each, FAIL/ERROR/SKIP 0, exit 0 (3.13: 2.930 seconds; 3.9: 2.943 seconds). No later source/test changes were made.

```sh
PYTHONPATH=tools/llmtool tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_git_mutation.py -k ReadCleanupTests -k between_queries -k production_import_boundary
PYTHONPATH=tools/llmtool /usr/bin/python3 -m unittest discover -s tools/llmtool/tests -p test_git_mutation.py -k ReadCleanupTests -k between_queries -k production_import_boundary
```

| Artifact | SHA-256 |
| --- | --- |
| `git_mutation.py` | `25fe6514ad9f4eed6f15d4a691b59e3d66440ebd8cff949a38a328dc65467ed8` |
| `test_git_mutation.py` | `a744d3f7889a356b4fb0a61c7c27f98aec56365c0c4068d7bdefea1c19c081bd` |
| Source/test candidate binary diff against base | `9363a32668ca5383504079508ce4eda9ceaa6f2fcdde6964e8553e1d5e0d3387` |

Git diff check: PASS. Final-head remote CI is pending publication at report creation; local results are not remote CI evidence. Coordinator review and merge authorization remain separate gates.

## Scope, limitations and handoff

- Production scope: read-only `_Reads.query` resource cleanup only.
- Real git add executed by the product/this slice: NO. Existing disposable Git-fixture setup uses Git ADD to construct baseline states; no ADD primitive or user-repo/index mutation was executed.
- Public mutation path: NONE.
- Supervisor implemented: NO.
- A11b.2.2.1 started: NO.
- A11b.2.2.2 started: NO.
- Action/approval semantics and preflight safe subset: unchanged.
- B5 LOW/NOTE follow-up implementation: NONE.
- Company live calls: 0.
- Codex benchmark calls: 0.
- Live external provider calls: 0; canonical tests use offline fakes/local fixtures.
- Original nine unrelated untracked report/artifact paths: preserved, not staged or deleted.
- No alternate checkout/worktree, force push, history rewrite or merge.

L1 cleanup: PASS

L2 drift evidence cleanup: PASS

Ready for coordinator review: YES — required local checks pass; independent coordinator review remains pending. No authorization to begin the next slice.
