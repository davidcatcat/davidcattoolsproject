# llmtool DG-1 process lifecycle validation — 2026-10-05

Status: LOCAL VALIDATION PASS; final-head CI/publication evidence follows in the Draft PR and delivery. Runtime lifecycle defect, not a security vulnerability. Independent
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
