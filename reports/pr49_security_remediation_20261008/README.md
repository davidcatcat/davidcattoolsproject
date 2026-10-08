# PR #49 bounded Security Remediation

Starting HEAD: `111f160e702e478437800f79e9f6a1e6bfb5c87e`.
Canonical Mac: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`.
Remote identity verified and fetched: `davidcatcat/davidcattoolsproject`.
macOS 15.7.9, Python 3.13.3 / system Python 3.9.6.
Unrelated untracked files preserved. No alternate checkout, merge or real product ADD.

## Changes and ordinary review

- S49-1/P49-M1: live self PID/current group leadership and captured leader equality
  replace generic identity as the final-act prerequisite. Only `killpg(0, SIGKILL)`.
  Parent ownership, PID reuse checks and signal-before-reap are unchanged.
- S49-2: unresolved event exceptions are best effort; original retained exception and
  ownership remain live through operator-wait even with unavailable supervisor.
- [Versioned B5 amendment](../llmtool_pr49_b5_self_anchor_amendment_20261008.md)
  and WORKFLOW link document superseding topology and deferred LOW findings.
- Regressions prove real identity double-read PPID rejection, safe self act independent
  of failed inspection, other-group/stale-anchor rejection, and actual event writer
  EPIPE/EBADF/report exception paths retaining ownership without restoration or cleanup.

Ordinary diff review and `git diff --check`: PASS. Local documentation links: PASS.
This author review is not independent Security or formal QA sign-off.

## New-byte verification

Commands launched from canonical root:

- `PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest test_git_containment.IdentityTests test_git_containment.UnresolvedReportTests -q`: 19 tests PASS.
- Containment module, Python 3.13 and 3.9: 81/81 PASS each, exit 0.
- `make -C tools/llmtool check PYTHON=/usr/bin/python3`: native exit 0,
  compileall + 1247 tests in 351.578s, OK (skipped=3).
- `make -C tools/llmtool check PYTHON=.venv/bin/python`: native exit 0,
  compileall + 1247 tests in 208.122s, OK (skipped=3).
- `PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python tools/llmtool/tests/run_containment_acceptance.py --output /private/tmp/pr49-security-remediation/native.json`:
  exit 0; 14/14 PASS, skipped=0; source hashes 9/9 verified against final bytes.
  SIGKILL plain 20/20 (max 0.006389379s), Popen 20/20 (max 0.012829675s).
  All rows affirm identity/containment/group/leader/descendant absence.

[Native JSON](mac-native.json), [terminal log](mac-native.log), [pinned contract](task.json).
JSON SHA-256: `369e851a4384fb720ddbbcea23069f0e1c9e0aeb9afb86157d4ec68e5f5734e6`.
The contract is reconstructed from the runner's unchanged fixed constants; no checks
were relaxed. Runtime temporary directories are not project checkouts.

Sandbox attempts are preserved separately: system Python compileall failed on its
external cache directory, exit 2; sandbox Python 3.13 full run failed (1201 tests,
62 errors, 2 skips), exit 2, with loopback/socket and process/environment restrictions.
Both full suites were then explicitly authorized outside sandbox. Failed sandbox
results are not relabeled PASS. Three native skips are existing filesystem capability
conditions (non-UTF8 names and case collisions), not native containment scenario skips.

## Independent re-review handoff — required, not signed off

Claude: read AGENTS, WORKFLOW, security-review skill, original B5 plus amendment,
original independent report and PR security/Coordinator comments. Pin published SHA;
review cumulative PR and this remediation, reproduce both original failure paths,
verify self-group authority/no wrong-group signal, and retained report-loss responsibility.
Classify deferred LOW items without expanding remediation. Return exact-head findings
and Security verdict; do not mutate, merge or start the next slice.

Independent functional QA: read qa-validation skill, pin published SHA and source hashes;
independently assess negative assertions, real double-read PPID race, libproc failure,
report loss with unavailable supervisor, 14-scenario false-PASS protections, terminal/JSON
consistency and 20+20 native SIGKILL evidence. Execute independent checks and report
coverage limits. Author-run tests do not constitute independent QA.

CI 3.9/3.14 and both independent verdicts remain PENDING until observed on published
HEAD. PR stays Draft / MERGE BLOCKED / NO A11b.2.2.2.
