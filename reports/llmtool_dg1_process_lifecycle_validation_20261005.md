# llmtool DG-1 process lifecycle validation — 2026-10-05

Status (initial round, historical): LOCAL VALIDATION PASS; final-head CI/publication evidence follows in the Draft PR and delivery. Runtime lifecycle defect, not a security vulnerability. Independent
Claude re-review remains required; this does not start QG-QA1 or approve a merge.

## Task Contract v1

- Task ID: DG1-CLI-LIFECYCLE-20261005; reproduce DG-1 on fetched latest main, add failing permanent offline regressions, minimally fix owned Claude/Codex CLI process lifecycle.
- Repository: davidcatcat/davidcattoolsproject; only `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`. No clone/worktree/project copy/reset/stash/force/history rewrite or unrelated artifact deletion.
- Base: `e9f550c4a77af51b5a58f0d2da4bb6fe695a4b61`; branch `fix/llmtool-dg1-process-lifecycle-20261005`. Read AGENTS, WORKFLOW, applicable implementation/review/Git skills and original QG-S1 report.
- Authorized: SIGTERM/SIGHUP cleanup for two CLI adapters, existing SIGINT/timeout/success regressions, minimal shared provider lifecycle helper, deterministic fake tree tests, validation report, coherent bugfix plus optional report commit, normal push/Draft PR/final-head CI.
- Forbidden: DG-2/DG-3, TR-1/TR-2/TR-3, DA-3, QA/A11b/A11c, sandbox/executor/orchestration/approval frameworks, retries/fallback, weakened provider safety or credential/prompt/stderr/reasoning exposure.
- Acceptance: both providers terminate the owned process group including child/grandchild on TERM/HUP/INT/timeout; clean synthetic runtime afterward; normal success/argv/environment/bounds/error contracts unchanged. Handlers only record intent, ownership/spawn safe, repeated signals and cleanup idempotent, no stale-PID/unrelated-group kill, non-success Unix-consistent TERM/HUP result.
- Evidence: base reproduction before production modification; permanent failed→passed tests, focused and full canonical Python 3.9 plus checkout Python, available local 3.14, diff review/whitespace, exact final-head 3.9/3.14 CI with canonical/clean-tree steps, limits/skips and preserved artifacts. Stop for independent re-review; do not merge.

The contract payload above is pinned before tests/production changes. Its digest and
actual evidence is recorded at the documentation cutoff without changing scope.
Unchanged UTF-8 contract payload SHA256:
`72c9339c4c869d8abeca3328df3c7ea4cae92975c1de1e17d835d3a96634dc15`.

## Baseline reproduction before production changes

Fresh fetch resolved origin/main to the expected base. The report was already merged
by PR #28; provider history/source confirmed DG-1 still existed. Canonical identity and
tracked-clean preflight passed. The nine pre-existing untracked benchmark/reasoning and
Claude case paths were retained. No alternate checkout was created.

Read the complete historical [QG-S1 report](llmtool_qg_s1_security_baseline_20261005.md)
and current AGENTS/WORKFLOW. Its vulnerability count/classification and historical
results are unchanged. This task follows the user's newer instruction to fix DG-1
before QG-QA1; it does not claim a new security vulnerability or formal QA result.

Initial permanent test command, from canonical root at base `e9f550c...`:

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v test_cli_provider_lifecycle
```

Python 3.13.3; exit 1; **10 tests, four failures, six controls PASS**, 28.490s.
Production `git diff --name-only` was empty. Readiness is an atomic capture after the
fake has forked its child/grandchild, not a guessed sleep-before-signal. The fake
ignores TERM/HUP/INT; process-group SIGKILL is necessary. No real CLI/auth/API is used.

| Provider/signal | Python parent returncode before fix | Owned tree after parent exit | Temporary cwd before fix |
| --- | --- | --- | --- |
| Claude / SIGTERM | -15 (shell 143) | 78933 → 78934 → 78935 still running; provider PPID=1 | llmtool-claude-22oxid3_ exists |
| Claude / SIGHUP | -1 (shell 129) | 78766 → 78767 → 78768 still running; provider PPID=1 | llmtool-claude-loorxcj2 exists |
| Codex / SIGTERM | -15 (shell 143) | 79264 → 79265 → 79266 still running; provider PPID=1 | llmtool-codex-p53fx5t3 exists |
| Codex / SIGHUP | -1 (shell 129) | 79100 → 79101 → 79102 still running; provider PPID=1 | llmtool-codex-22h7fjgh exists |

All cwd paths above were newly generated under `/private/tmp`. The test teardown
killed only its captured synthetic groups and removed only those newly captured
runtimes after recording failure; it did not touch historical QG-S1 runtime evidence.
SIGINT, timeout and normal success already passed for both providers.

Baseline log: `/private/tmp/llmtool-dg1-before.log`, SHA256
`248307bedc636f35e662069d5ba37de1b9356402100752929c0e5a1c4fffa2fb`.
DG-1: **REPRODUCED → REGRESSION FAILED → FIXED → REGRESSION PASS** for both adapters.

## Implementation and signal/cleanup invariants

Only the two existing synchronous CLI adapters share a small `cli_lifecycle.py` helper.
It is not an executor, sandbox, task/approval engine or retry manager.

1. Install scoped INT/TERM/HUP handlers before temporary-runtime creation. Handlers
   only record cancellation; they never raise, kill, wait, log or remove files. This
   lets Popen finish assigning the owned child even if a signal lands in its spawn
   window. Check cancellation before spawn, immediately after ownership, in collection
   and in short final-wait intervals. Repeated waits poll the same process, not another
   provider invocation or retry.
2. The owned child has start_new_session=True. Under the exclusive ownership invariant,
   a child whose returncode is still None has not been reaped. Terminate its process
   group with SIGKILL **before** wait/reap; do not poll first. The unreaped child pins
   its PID/PGID against reuse. Once wait sets returncode, never signal the stale group.
3. Stop/reap before leaving the TemporaryDirectory context, then restore previous
   handlers and apply the host exit. Repeated stop after successful reap is a no-op.
   The outer lifecycle is a final guard for failures during setup/collection.
4. POSIX signal ownership requires the main thread and default SIGCHLD handling.
   Worker-thread calls or ignored/custom SIGCHLD are rejected with fixed
   PROVIDER_RUNTIME_ERROR before runtime/spawn: automatic/foreign reaping would defeat
   the child identity pin. Public CLI calls use this supported synchronous context.
   No other component may wait/reap an adapter-owned child behind the lifecycle.
5. SIGINT propagates as KeyboardInterrupt to existing callers (ask exit 130 and fixed
   `llmtool: interrupted`). TERM/HUP have no prior explicit public contract; adopt
   `SystemExit(128 + signal)` after cleanup: **143/129**, never success or timeout.
   This preserves conventional shell status, while Python Popen now sees positive
   exit status instead of -15/-1 signal death. README documents the exact choice.
6. A recorded host signal retains non-success semantics even if cleanup raises; raw
   cleanup details are omitted. Repeated TERM/INT/HUP during the injected release fault
   neither interrupt cleanup nor change the first recorded cancellation result.

Both adapters retain exact argv/env construction, shell=False, close_fds, isolated cwd,
stdin prompt transport, stream bounds/deadlines, error codes, capability declarations,
safe-mode/profile and output filtering. Claude `--safe-mode --tools ""` and Codex
`llmtool-text` restricted permissions/network-off remain intact. No raw provider stderr,
reasoning or synthetic secret/prompt appears in external signal results. Normal final
stdout bytes remain `DG1_FINAL\n`; provider invocation count is one.

## Permanent regression tests

`tests/test_cli_provider_lifecycle.py` drives the actual public CLI with synthetic
configured HOME/PATH and real process groups. It verifies provider, child and grandchild
PID/PGID/state, runtime absence, exits, one invocation and safe output. Process-state
probes reject ps failures; zombie state is not mistaken for execution. The immediate
owned provider is reaped by llmtool; orphan descendants are reaped by the OS, where PID 1
may temporarily retain non-executing zombies on Linux. Tests require no **running**
owned descendant, not just absence of the immediate child.

- `test_claude_sigterm_tree_and_runtime_cleanup`
- `test_claude_sighup_tree_and_runtime_cleanup`
- `test_claude_sigint_contract_and_tree_cleanup`
- `test_claude_timeout_contract_and_tree_cleanup`
- `test_claude_normal_success_contract`
- `test_codex_sigterm_tree_and_runtime_cleanup`
- `test_codex_sighup_tree_and_runtime_cleanup`
- `test_codex_sigint_contract_and_tree_cleanup`
- `test_codex_timeout_contract_and_tree_cleanup`
- `test_codex_normal_success_contract`
- `test_claude_sigterm_after_pipes_close_still_cancels_wait`
- `test_codex_sigterm_after_pipes_close_still_cancels_wait`
- `test_claude_signal_between_spawn_and_ownership_is_not_lost`
- `test_codex_signal_between_spawn_and_ownership_is_not_lost`
- `test_claude_cleanup_fault_preserves_signal_exit_and_privacy`
- `test_codex_cleanup_fault_preserves_signal_exit_and_privacy`
- `test_termination_does_not_kill_unrelated_process_group`

`tests/test_cli_lifecycle_state.py` adds deterministic ownership/handler invariants:

- `test_normal_and_exception_restore_exact_previous_handlers`
- `test_repeated_signals_only_record_first_and_do_no_cleanup_in_handler`
- `test_sigint_unwinds_as_existing_keyboard_interrupt`
- `test_reaped_process_never_signals_stale_pid_or_group`
- `test_unreaped_owned_process_is_killed_before_reaping_and_stop_is_idempotent`
- `test_worker_thread_rejected_before_installing_handlers_or_spawning`
- `test_nondefault_child_reaper_rejected_before_lifecycle`
- `test_partial_handler_install_failure_restores_previous_handlers`

Existing provider tests are retained. The Codex kill mock now explicitly represents an
unreaped child (`returncode=None`), keeping both kill/wait assertions. The first full run
found one TypeError in an orchestration test mock for the new private `_invoke` lifecycle
argument. Its signature was updated and lifecycle assertions added; all original
output/order/privacy assertions remain. No orchestration production code changed.

## Validation commands and results

Actual Mac: canonical root, Darwin x86_64. Versions: checkout Python 3.13.3/PyYAML 6.0.3;
system Python 3.9.6/PyYAML 6.0; existing isolated runtime Python 3.14.8/PyYAML 6.0.3.
The 3.14 runtime under `/private/tmp` is only an interpreter/environment, never a project
checkout/copy. All checks launch from the canonical repo. No real provider requests.

Final source/test/README manifest SHA256 (tested before commit, proven byte-identical after commit):
`9f055cad157c2ed85b5aaa768b428909eae47d9050e47ccaef81a0e3e2bea706`.
Digest construction: sorted authorized file paths, each UTF-8 path + NUL + file bytes +
NUL; exact eight files listed below. This binds checks to the working bytes rather than
claiming base HEAD alone identifies an uncommitted fix. Commit `f8fd86bf6639338facfd03ae70fa9a05a64abb2c` matches every file digest in that manifest.

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v test_cli_provider_lifecycle test_cli_lifecycle_state test_claude_cli_provider test_codex_cli_provider test_orchestration
make -C tools/llmtool check PYTHON=/Users/davidsun/git/davidcataiporject/davidcattoolsproject/tools/llmtool/.venv/bin/python
make -C tools/llmtool check PYTHON=/usr/bin/python3
make -C tools/llmtool check PYTHON=/private/tmp/llmtool-a11a7-py314-venv/bin/python
git diff --check
```

| Check | Result | Exit / log |
| --- | --- | --- |
| Initial focused, Python 3.13 | 64 tests PASS | 0; llmtool-dg1-focused-v1.log |
| Extended focus, Python 3.13 | 79 tests PASS | 0; llmtool-dg1-focused-v2.log |
| Extended focus, Python 3.9 | 79 tests PASS | 0; llmtool-dg1-focused-py39-final.log |
| Final focused including orchestration seam | 111 tests PASS, 27.038s | 0; llmtool-dg1-focused-final.log |
| Final canonical Python 3.13.3 | 918 tests PASS, 95.669s, skipped=1 | 0; llmtool-dg1-final-py313.log |
| Final canonical Python 3.9.6 | 918 tests PASS, 147.139s, skipped=1 | 0; llmtool-dg1-final-py39.log |
| Final canonical Python 3.14.8 | 918 tests PASS, 88.264s, skipped=1 | 0; llmtool-dg1-final-py314.log |

Intermediate failed full check is historical evidence, not final PASS: 918 tests,
one orchestration private-mock TypeError and one host filesystem skip. The repaired
seam and process-state probe strengthening were followed by final focused/full checks.
The existing `test_unusual_filename_bytes_roundtrip` host filesystem/sandbox skip is
unchanged; SIGHUP tests skip conditionally only on a platform without SIGHUP. On this
Mac all DG-1 signal cases execute, no DG-1 skip. Existing safety/race/diagnostics,
timeout/output-limit, sanitized environment, restricted argv and concurrency tests remain.

Final executed DG-1 acceptance (all three local Python versions):

| Provider | SIGTERM | SIGHUP | SIGINT | Timeout | Normal | Owned tree / runtime |
| --- | --- | --- | --- | --- | --- | --- |
| Claude CLI fake | PASS, exit 143 | PASS, exit 129 | PASS, exit 130 | PASS, exit 1 / PROVIDER_TIMEOUT | PASS, exit 0 / DG1_FINAL | No running owned provider/child/grandchild; temporary cwd absent |
| Codex CLI fake | PASS, exit 143 | PASS, exit 129 | PASS, exit 130 | PASS, exit 1 / PROVIDER_TIMEOUT | PASS, exit 0 / DG1_FINAL | No running owned provider/child/grandchild; temporary cwd absent |

Signal stdout/stderr: empty except existing SIGINT fixed interruption line. Safe timeout
retains trace=not-saved. One provider invocation per case; no retry/fallback. Spawn-window,
pipe-closed final wait, repeated-signal release-fault and unrelated-group tests all PASS.

Local final evidence log SHA256 (all logs under `/private/tmp`):

| Log | SHA256 |
| --- | --- |
| llmtool-dg1-focused-final.log | 48392910e68d09c31b2d0752d752485067d27e9aa31dad310d5d81852206ac2f |
| llmtool-dg1-final-py313.log | ada2b2f11e9e022579c9713728920584421701a14c9b816490ae94418440e758 |
| llmtool-dg1-final-py39.log | b01e82fc657100ff740e07b8098ea4e029fda1b8c055070e5e13aeacf2da5736 |
| llmtool-dg1-final-py314.log | df8f1e00e34eadb049f66781c89b3ff33812673d541385f16d2b60d4c906a988 |

`git diff --check` PASS; new Markdown fences/whitespace/local report link validate.
No source drift since the final manifest. Final exact-head CI is PENDING at report
commit cutoff, verified and linked in the final PR publication record after push.

## Files and publication

Production: `tools/llmtool/llmtool/providers/claude_cli.py`, `codex_cli.py`, new
`cli_lifecycle.py` (under the same provider directory).

Tests: new `tools/llmtool/tests/test_cli_provider_lifecycle.py` and
`test_cli_lifecycle_state.py`; modified `test_codex_cli_provider.py` (explicit unreaped
mock) and `test_orchestration.py` (private mock signature plus additional assertions).
Documentation: `tools/llmtool/README.md` (only lifecycle/exit semantics) and this report.

Implementation commit: `f8fd86bf6639338facfd03ae70fa9a05a64abb2c`,
`fix(llmtool): terminate CLI provider trees on host signals`. One coherent code/test/README
commit, followed only by a separate validation-report commit. Latest pre-publication
fetch confirmed origin/main remains the base `e9f550c...`. The final
report-only HEAD must contain unchanged source/test/README bytes. Per WORKFLOW, final
HEAD, Draft PR and exact-head CI are recorded once in the final PR description/Checks
and delivery after push; do not recursively commit a report merely to embed its own
future SHA. Required CI: Python 3.9/3.14 SUCCESS, Offline canonical checks SUCCESS and
Working tree is clean SUCCESS on that final new HEAD. Earlier CI does not qualify.

Ordinary diff self-review completed against the fetched base: ownership, signals, argv/env,
cleanup ordering, error privacy, complete diff and test evidence checked; no material
blocking finding remains within DG-1 scope. Independent Claude re-review
is pending and is not replaced by self-review or CI. No merge is authorized.

## Preserved scope and NOT-TESTED

- DG-2 catch-all traceback and DG-3 diagnostics parent permissions are **unchanged**;
  no CLI catch-all or diagnostics traversal/store/reader changes.
- TR-1/TR-2/TR-3 and DA-3 remain unchanged. A11b/A11c and QG-QA1 remain NOT STARTED
  by this task. No executor, approval, sandbox redesign, automatic repair/retry/fallback.
- Trusted children deliberately escaping their session/group via setsid, or detached
  descendants after an already-reaped normal provider, are TR-3 and **not solved**.
  Never use a reaped provider's possibly reused PGID to guess where to kill.
- SIGKILL, host/interpreter crash, deliberate third-party reaping of the owned child,
  uninterruptible kernel tasks or real OS refusal to signal the owned group are outside
  the tested lifecycle guarantee. Existing immediate-child fallback on killpg permission
  failure remains covered; that is not proof of descendant containment under OS refusal.
- Main-thread/default-SIGCHLD synchronous execution is now a documented prerequisite;
  unsupported embedding contexts fail closed before spawn instead of starting an
  unprotected invocation. No generic multi-thread signal supervisor is introduced.
- Real Claude/Codex binaries, live APIs/auth/session behavior, native Windows and other
  non-POSIX semantics are NOT-TESTED. Synthetic process trees on this Mac plus Linux CI
  establish the supported-path regression evidence, not a new sandbox/security claim.
- Existing nine untracked benchmark/reasoning/case paths and historical QG-S1 temporary
  evidence are untouched. Only this task's self-owned synthetic runtime fixtures are
  cleaned. No credential/auth/Keychain files inspected or user config modified.

## Round 2 — Task Contract v2 (pinned before implementation)

User authorization: continue existing Draft PR #29 on 2026-10-05, addressing Claude
R1/R2 and F2 only. Contract v1 and the independent Claude review remain historical
evidence; the following policy supersedes v1's blanket rejection of ignored SIGCHLD.

- Task ID: DG1-CLI-LIFECYCLE-20261005; version 2.
- Starting reviewed HEAD: `b8fe3ddeeb78a4667fc9ca9efdb642ba7e87ed5c`; freshly fetched
  main/base: `e9f550c4a77af51b5a58f0d2da4bb6fe695a4b61`; existing branch unchanged.
- Goal/scope: preserve inherited ignored INT/TERM/HUP; temporarily default ignored
  SIGCHLD during owned-child lifecycle and restore exactly; reject custom reapers
  before spawn. Correct F2 documentation without a new signal state machine.
- Forbidden: F1 diagnostics finalization, DG-2/DG-3, TR-1/2/3, DA-3, QG-QA1,
  A11b/A11c, generic framework, retry/fallback, unrelated edits/artifact deletion,
  Claude report changes, new branch/PR, merge, force push/history rewrite.
- Acceptance: real exec-inherited IGN HUP/INT tests and ignored-SIGCHLD success for
  both fake providers; scoped state restoration on success/error/cancellation/setup
  and cleanup failure; custom handler rejected before spawn. Original TERM/HUP/INT,
  timeout, tree/grandchild containment, runtime cleanup and safety checks remain PASS.
- Evidence/delivery: regressions fail before production changes, focused and full
  canonical checks on checkout Python and actual 3.9, available local 3.14; complete
  diff review, preserved untracked baseline, normal commit/push to Draft PR #29,
  final exact-head 3.9/3.14 CI including offline/clean-tree steps. Stop for Claude
  round-2 re-review; no merge. F1 DEFERRED TO QG-QA1 TRIAGE, unchanged.

Round-2 results are recorded below after execution; older PASS results above do not
establish the new contract's acceptance.

Unchanged v2 contract UTF-8 payload SHA256:
`bc456398f22376cfe84206aace11e8db3b87bfe71c1dee296eb7d42669d93ad9`.
Payload is the text between the v2 heading and the paragraph starting “Round-2 results”,
excluding the heading, its two separating newlines and the newline before that paragraph.

### Round-2 reproduction before production changes

Actual Mac, canonical root, Python 3.13.3. Fresh fetch established local and published
feature HEAD `b8fe3ddeeb78a4667fc9ca9efdb642ba7e87ed5c`, main `e9f550c...`, ahead/behind
main 3/0, tracked clean. Nine existing untracked paths were retained. The complete
Claude report was read and remains byte-identical to the starting commit.

Before changing production or README, run newly added permanent tests:

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v test_cli_provider_lifecycle test_cli_lifecycle_state
```

Host execution: exit 1; **35 methods, 7 failures and 8 error subcases**, 19.580s.
The eight errors are the ignored-SIGCHLD state test's exit-path subcases, each
rejected at entry. Existing DG-1 signal/tree/runtime controls still passed.
Production/README diff was empty at this reproduction cutoff.

| Finding | Claude fake, before fix | Codex fake, before fix | Expected |
| --- | --- | --- | --- |
| R1 inherited HUP ignore, then HUP | Exit 129, stdout empty | Exit 129, stdout empty | Exit 0, `DG1_FINAL` |
| R1 inherited INT ignore, then INT | Exit 130, stdout empty, fixed interrupted line | Same | Exit 0, `DG1_FINAL` |
| R2 inherited SIGCHLD ignore | Exit 1, `PROVIDER_RUNTIME_ERROR`, zero provider starts | Same | Exit 0, one completed/reaped provider |

These subprocess tests establish inherited ignore through an actual exec. Atomic fake
readiness follows its child/grandchild PID handshake. Only after readiness does the
test send a signal and release the fake to finish through pipe EOF and waitpid; no
guessed completion delay or real provider/API/credential is involved. Correct behavior
requires normal output, all three PIDs reaped, runtime absent and one invocation.
The runner's own dispositions remain unchanged; a child-side post-main snapshot
also requires exact IGN restoration.

Initial sandbox executions were inconclusive for containment: `/bin/ps` was denied.
The clean host rerun above is authoritative. Sandbox runs also exposed two test
bootstrap construction mistakes (parenthesis / enum repr), corrected before the
authoritative reproduction; those mistakes are not counted as bug evidence.
Fourteen synthetic groups left by denied sandbox teardown were individually verified
by this round's timestamp, limited PID range, fake argv/PGID and lsof cwd; only those
groups and newly owned runtime dirs were cleaned. Historical evidence/user artifacts
were untouched. Subsequent host tests use the existing owned-fixture cleanup.

### Round-2 fix and contract dispositions

- **R1 ACCEPTED / FIXED:** record each previous INT/TERM/HUP disposition; install the
  cancellation handler only when it is not SIG_IGN. Ignored signals remain ignored.
  Default/custom cancellation dispositions retain the original DG-1 coordinated path.
- **R2 ACCEPTED / FIXED:** DFL remains DFL; IGN is recorded before installation and
  temporarily made DFL before any runtime/spawn. Normal Python unwinding restores
  exact IGN after stop/wait. Custom SIGCHLD handlers still reject before spawn,
  without installing other handlers. The main-thread requirement remains.
- **F2 ACCEPTED / DOCUMENTATION CORRECTION (Option A):** first observed signal drives
  coordinated cleanup while scoped handlers are installed. After restoration,
  repeated signals may use normal OS disposition. On the supported cleanup path,
  process-tree/runtime cleanup has completed before restoration. No promise of
  always-positive 143/129 through the final process-exit window; no new state machine.
- **F1 DEFERRED TO QG-QA1 TRIAGE:** TERM/HUP diagnostics incomplete-run/summary behavior
  remains unchanged. No diagnostics, store, reader or ask lifecycle source modified.

Only production change this round: `providers/cli_lifecycle.py`. Adapter argv/env,
kill-before-reap, stale-PGID guard, idempotent stop, signal handler (record-only),
timeout/bounds, cwd isolation and cleanup order are unchanged. Scoped default SIGCHLD
keeps automatic reaping disabled until the owned child has been stopped/reaped.
Restoration also executes through Popen/collection errors and cleanup exceptions.

README's one lifecycle paragraph now states inherited-ignore, SIGCHLD and repeated
signal policy accurately. The historical Claude review report is not edited.

### Round-2 permanent tests and actual coverage

Six new real subprocess methods in `test_cli_provider_lifecycle.py`:

- `test_claude_inherited_sighup_ignore_completes_tree_and_runtime`
- `test_codex_inherited_sighup_ignore_completes_tree_and_runtime`
- `test_claude_inherited_sigint_ignore_completes_tree_and_runtime`
- `test_codex_inherited_sigint_ignore_completes_tree_and_runtime`
- `test_claude_inherited_sigchld_ignore_still_works_and_reaps`
- `test_codex_inherited_sigchld_ignore_still_works_and_reaps`

Four new state methods in `test_cli_lifecycle_state.py`:

- `test_ignored_termination_dispositions_are_retained_and_recorded` (INT/TERM/HUP).
- `test_default_sigchld_remains_default`.
- `test_ignored_sigchld_scoped_default_and_exact_restore_all_exit_paths`: normal;
  synthetic provider error/timeout; injected Popen failure; cleanup exception;
  recorded INT/TERM/HUP cancellation. Requires DFL inside and exact IGN outside,
  along with exact previous termination handlers. Faults are deterministic, not
  additional real provider invocations.
- `test_ignored_sigchld_restored_after_partial_handler_install_failure`.

The old blanket non-default reaper rejection test is replaced by the authorized
`test_custom_child_reaper_rejected_before_lifecycle_or_spawn`, preserving the fail-closed
assertion for custom handlers and additionally requiring zero spawn/handler writes.
Other assertions remain intact. Normal-disposition subprocess controls now explicitly
set INT/TERM/HUP defaults so a background test runner's ignore cannot accidentally
change what the original DG-1 controls test. All original child/grandchild containment,
timeout/success, spawn-window, final-wait, cleanup-fault/privacy and unrelated-group
checks remain. SIGHUP cases conditionally skip only on a platform without SIGHUP.

### Round-2 validation and publication cutoff

Final four-file code/test/README manifest SHA256:
`54e184888c670495dddfaadb02eda018ae880dc333e799c9e0fa4cc3c87872e0`.
Construction is the same sorted path + NUL + bytes + NUL scheme as v1. Files:
`tools/llmtool/README.md`, `tools/llmtool/llmtool/providers/cli_lifecycle.py`,
`tools/llmtool/tests/test_cli_lifecycle_state.py`, `tools/llmtool/tests/test_cli_provider_lifecycle.py`.

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v test_cli_provider_lifecycle test_cli_lifecycle_state test_claude_cli_provider test_codex_cli_provider
make -C tools/llmtool check PYTHON=/Users/davidsun/git/davidcataiporject/davidcattoolsproject/tools/llmtool/.venv/bin/python
make -C tools/llmtool check PYTHON=/usr/bin/python3
make -C tools/llmtool check PYTHON=/private/tmp/llmtool-a11a7-py314-venv/bin/python
git diff --check
```

Implementation commit: `506d4b61fdedcf8d0ba6216d498fd12d4cbefbcc`,
`fix(llmtool): preserve inherited signal dispositions`. Committed bytes match the
tested manifest above. All checks launched from the canonical Mac repo; the existing
3.14 interpreter environment is not a repository copy. No real provider traffic.

| Check | Actual Python | Result | Exit / duration |
| --- | --- | --- | --- |
| Focused four modules | 3.13.3 | 89 tests PASS, no skip | 0 / 31.365s |
| Full canonical | 3.13.3 | 928 tests PASS, 1 existing skip | 0 / 106.461s |
| Full canonical | 3.9.6 | 928 tests PASS, 1 existing skip | 0 / 159.661s |
| Full canonical | 3.14.8 | 928 tests PASS, 1 existing skip | 0 / 100.228s |

Only skip: existing `test_unusual_filename_bytes_roundtrip`, host rejects non-UTF-8
filename bytes. All signal cases execute on this Mac; no new lifecycle skip.

| Fake provider | Inherited HUP ignore | Inherited INT ignore | Inherited SIGCHLD ignore | Normal TERM/HUP/INT | Timeout / normal |
| --- | --- | --- | --- | --- | --- |
| Claude | PASS / 0 / DG1_FINAL | PASS / 0 / DG1_FINAL | PASS / 0 / DG1_FINAL, reaped | PASS / 143,129,130 | PASS / PROVIDER_TIMEOUT; PASS / 0 |
| Codex | PASS / 0 / DG1_FINAL | PASS / 0 / DG1_FINAL | PASS / 0 / DG1_FINAL, reaped | PASS / 143,129,130 | PASS / PROVIDER_TIMEOUT; PASS / 0 |

Every row requires one provider invocation, no running owned tree and runtime removed.
Ignored-signal successful fixtures also wait/reap child/grandchild and require no PID
row left. Cancellation controls continue to allow the OS to reap non-executing orphan
zombies on Linux, as described in the initial round. State tests PASS: scoped DFL,
exact IGN restoration, custom reaper rejection, stop idempotence and no stale-group kill.

`git diff --check` PASS. Ordinary self-review inspected the entire round-2 production,
tests and docs diff against `b8fe3dde...`: no material blocker identified within scope.
This is not independent Claude review. No provider adapter or CLI/diagnostics source
changed. Full existing argv/env/output-limit/timeout/privacy and security/race tests PASS:
shell=False, sanitized child env, Claude safe-mode/tools-empty, Codex restricted profile/
network-off, stderr/reasoning omission remain. No retry/fallback.

Final new-head CI remains **PENDING at this documentation cutoff**. After the report-only
commit and single branch push, the final PR description/Checks and delivery record the
report commit, final HEAD and exact-head CI links/results. Source/test/README diff from
`506d4b6...` must be empty. Old initial-round CI is historical only. No recursive report
commit embeds its own future SHA, per WORKFLOW. Required new CI: Python 3.9 and 3.14,
Offline canonical checks and Working tree is clean, all SUCCESS on the final HEAD.

Logs under `/private/tmp`:

| Log | SHA256 |
| --- | --- |
| llmtool-dg1-round2-before-host.log | 46b0fff4569bb9d3f4bfd9fe00d39bcf676868c87156039cb84410f62ab7ffc4 |
| llmtool-dg1-round2-focused.log | 39040ac45f15554afe853931c9b27229a7b7a3a08b7551169198be96a59027c8 |
| llmtool-dg1-round2-py313.log | 50ed39dfdb080a752475e61586cb19065ceb108dbc5a015ebc8c2e0b45d42911 |
| llmtool-dg1-round2-py39.log | 968e76b15ea786fbc439e8064a687ae24de34741c2a62c9878ecd26090bc1b76 |
| llmtool-dg1-round2-py314.log | c5e615985b10e3d08f197971c4b7e69860d5755c151a3c5a0a79b8de5328f47d |

### Round-2 remaining gates and limitations

R1/R2 are **REPRODUCED → REGRESSION FAILED → FIXED → REGRESSION PASS**.
F2 is corrected documentation; no implementation exit-state-machine change.
**F1 DEFERRED TO QG-QA1 TRIAGE**, diagnostics lifecycle unchanged. DG-2/DG-3,
TR-1/2/3, DA-3 remain unchanged. QG-QA1/A11b/A11c not started; no merge authorized.

Historical v1 limitations still apply, with one explicit supersession: inherited
SIGCHLD ignore is now supported through scoped DFL. Custom child reapers and worker
threads still fail closed; deliberate foreign waits/reaping violate exclusive process
ownership. Real CLI/auth/live APIs, non-POSIX/Windows, SIGKILL/crash, deliberate setsid
escape and actual OS refusal/uninterruptible cleanup remain NOT-TESTED/outside scope.
Repeated signals after restoration may alter final OS exit disposition as documented.

Nine pre-existing untracked benchmark/reasoning/case paths remain, so “clean” locally
means **tracked working tree clean, baseline untracked preserved**, not an empty
porcelain output. CI has a fresh checkout and requires completely clean porcelain.
Only five round-2 files change: helper, two lifecycle test files, README and this report.
Claude's review report remains unchanged. User config/auth/session/Keychain files were
not inspected or modified. Coordinator performed Git directly; no Git-agent/model/cost
savings claim. Final required gate after publication: **Claude round-2 independent
re-review pending**. PR #29 remains Draft; stop here without merge or QA.
