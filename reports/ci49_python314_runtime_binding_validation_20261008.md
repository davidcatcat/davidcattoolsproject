# CI-49 — Python 3.14 runtime binding fixture repair

Scope: continue Draft [PR #49](https://github.com/davidcatcat/davidcattoolsproject/pull/49), starting at `12cabadaf42559a6c48a3aa707925de021f2e2e7`. Two bounded, evidence-backed correction rounds: the confirmed ambient-interpreter fixture mismatch, then an ordinary-review correction of the added test's FD cleanup coupling. The temporary CI metadata commit was investigation only. No further blind repair is authorized. No new feature, milestone, security contract, product Git mutation or ai-loop control branch change. The coordinator handles Git because the workflow's requested `gpt-5.6-luna` Git-agent model is unavailable.

Configured Cloud checkout: `/workspace/davidcattoolsproject`; feature branch: `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`. Tests ran on diagnostic HEAD `5127663c1a4e05584386d1b462f0a662bbedca39` plus the frozen source patch. The test-file digest below identifies the tested bytes; HEAD alone does not identify uncommitted test changes.

## Confirmed cause

[Original run 37769939207](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37769939207) passed Python 3.9 and failed Python 3.14 in `BindingBoundaryTests.test_complete_pinned_manifest_and_drift_rejection`. The traceback reaches `runtime_binding()` → `file_hash(interpreter)` → the pre-read metadata rejection at `_git_containment_protocol.py:339`.

Cloud Python 3.13.5 and the available uv Python 3.14.7 interpreters satisfied the checks, so a temporary read-only, non-sensitive metadata step was added to the existing CI. [Diagnostic run 37780137205, job 113320867404](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37780137205/job/113320867404), at `5127663c1a4e05584386d1b462f0a662bbedca39`, reproduced the same rejection and recorded:

| Interpreter property | Observed value | Rejects? |
| --- | --- | --- |
| Python / realpath | 3.14.8 / `/opt/hostedtoolcache/Python/3.14.8/x64/bin/python3.14` | Resolved direct path |
| Regular file | true | No |
| Mode | `0777`, including group/world writable bits | **Yes** |
| UID / effective UID | 1001 / 1001 | No |
| Size / limit | 17,752 bytes / 67,108,864 bytes | No |
| Device / inode | 2049 / 835971 | Stable |
| Before/after/named signature | Equal device, inode, size, mtime_ns and ctime_ns | No drift |

Only `reject_writable` was true. This is a positive-test fixture assumption failure: the ambient CI interpreter is inadmissible under the existing deployment trust premise. Production correctly refuses it. The file was not chmod'ed, copied into an admissible runtime, or exempted from verification. The diagnostic prints no environment dump or file contents, and its workflow change is removed in the repair.

## Change and regression coverage

Only `tools/llmtool/tests/test_git_containment.py` has a net code/test change relative to the supplied PR HEAD. The positive binding unit test now uses a small owned `0700` fixture under a scoped `sys.executable` patch. It does not execute this file. Real `runtime_binding()`, `file_hash()` and `verify_binding()` remain in use; the path and independent SHA-256 are asserted, and all original manifest, origin, fixture, protocol and digest drift checks remain.

New negatives exercise the actual hash/binding implementation: interpreter modes `0777`, `0720` and `0702`; an unauthorized UID observation; a real directory and FIFO; a symlink rejected before open; a real sparse file exceeding 64 MiB; and ten read/named identity-drift cases covering device, inode, size, mtime_ns and ctime_ns. Only the UID and race observations are narrowly simulated. No production file, safety predicate, size limit or TOCTOU check changes. No test skip, retry or catch-and-pass is introduced.

An initial new directory test expected `ProtocolFailure`; the observed `IsADirectoryError` from `os.fdopen()` proved earlier OS rejection. The new assertion was corrected to require that exact error, and the FIFO separately checks the protocol's nonregular-file rejection. A read-only probe also confirmed a pre-existing private-reader cleanup gap: `file_hash(directory)` leaves its opened directory FD live when `fdopen()` fails. The new test captures and closes only its owned FD in `finally`. Ordinary review identified that checking the FD only after rejection would require that pre-existing leak; the final correction validates the captured directory FD before handoff and permits only `EBADF` during cleanup if the reader already closed it. The exact rejection and single-open assertions remain. Production remediation of the separate reader edge is outside CI-49 and remains a review finding; no public exploit or security acceptance is claimed. No existing assertion was relaxed.

Final test-file SHA-256: `c93d6fc55be91988f76bbc083050c9608ff5e839f02fec037e99e2f87e1f5a09`. Final source patch SHA-256 against diagnostic HEAD, excluding this report: `043fc64f82431259fb1525073d5f15a251e11df9f92e731f689189464d7ce918`.

## Validation and remaining gates

Cloud Linux Python 3.13.5 and uv Python 3.14.7: focused binding tests **13/13 PASS** and complete containment module **79/79 PASS**, each command exit 0. The commands below ran from `/workspace/davidcattoolsproject/tools/llmtool`; both isolated environments use PyYAML 6.0.3.

```sh
.venv/bin/python -m unittest discover -s tests -p test_git_containment.py -k BindingBoundaryTests -v
.venv/bin/python -m unittest discover -s tests -p test_git_containment.py -v
make check PYTHON=.venv/bin/python

/workspace/scratch/ci49-python314-venv/bin/python -m unittest discover -s tests -p test_git_containment.py -k BindingBoundaryTests -v
/workspace/scratch/ci49-python314-venv/bin/python -m unittest discover -s tests -p test_git_containment.py -v
make check PYTHON=/workspace/scratch/ci49-python314-venv/bin/python
```

Final Python 3.13.5 canonical check: compileall and **1245 tests PASS**, exit 0, 91.230 seconds, `OK (skipped=1)`. The sole existing skip is `test_case_alias_rejected_even_if_filesystem_resolves_it`: the Linux filesystem does not resolve case aliases.

The earlier uv Python 3.14.7 canonical run is retained as **FAIL**, exit 2, 1245 tests in 80.983 seconds, one existing skip. Its sole failure is the unchanged `StoreTests.test_low_nofile_capacity_cleanup_and_fd_lifetime_in_subprocess`: a fresh child observed an extra runtime FD after its baseline. Two bounded read-only diagnostics established that `import ctypes` alone retains a descriptor to the uv `python3.14` binary, even without importing or using the store; `CDLL(None)`, symbol lookup and an invalid-FD call do not add further descriptors. The store lazily imports ctypes after the test records its baseline. No store/test change, full-suite retry or unsupported PASS claim was made. Final binding/containment tests were rerun on both runtimes after the fixture cleanup changed; the authoritative Actions distribution remains a separate required CI check.

Published-head Python 3.9/3.14 CI identity and results are recorded in PR #49's CI-49 section, rather than inferred from the diagnostic run or a tracking ref. This report alone does not claim that published-head CI passed.

Ordinary code review and independent security/QA acceptance are separate gates. This repair does not grant security, QA, coordinator acceptance or merge approval. The PR remains Draft and must not be merged.

Non-blocking for the CI-49 fixture repair, with evidence preserved for separate follow-up: the uv-specific ctypes FD-baseline mismatch and the private directory-reader error-path FD cleanup gap. Neither is repaired by relaxing file trust checks or assertions. They do not close the independent security/QA gates.

**MAC ACCEPTANCE REVALIDATION REQUIRED / MERGE BLOCKED:** `AcceptanceScenarios.code_runtime_binding()` invokes the changed positive unit test via `.debug()` before its real isolated-launch check. The native launch still validates the actual interpreter through the unchanged implementation, but the shared test bytes and the runner's source-hash set changed. The earlier Mac PASS in the historical F3 recovery report cannot be inherited for this revision. Regenerate the canonical Mac acceptance evidence with the current source hashes, including the binding scenario and the complete 14-scenario runner. Cloud/mock evidence cannot close this native gate. Independent security/QA review and coordinator acceptance also remain blocking.
