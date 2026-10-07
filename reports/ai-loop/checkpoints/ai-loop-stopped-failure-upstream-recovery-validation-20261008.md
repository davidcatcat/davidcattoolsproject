# AI-loop stopped-failure upstream recovery validation — 2026-10-08

Status: **CONTROL-PLANE VALIDATED / MAC PREFLIGHT PENDING**

## Trigger

Run `20261007T183516Z-7365` stopped at candidate
`474679ecda36da610a96e7ec84da39a5dd3ae33b` after round-2
`full-final`. The venv full suite passed; Mac `/usr/bin/python3` failed once in
`test_diagnostics_failure_matrix.py` when a non-timeout fake Claude CLI fixture
hit the shared 0.2-second test deadline. The same targeted Mac Python 3.9 test
then passed 3/3.

PR #47 independently changed only
`tools/llmtool/tests/test_diagnostics_failure_matrix.py`: the real timeout case
keeps 0.2 seconds and other CLI fixtures use a bounded 2-second budget. PR #47
passed targeted/full Cloud validation and exact-head CI, then squash-merged to
main as `63e232e22a769f409e381e34dfe0ef1add766d8e`.

The stopped run's read-only Claude failure classification matched the exact
candidate/evidence identities but its summary was 2136 characters, exceeding the
2000-character contract. Post-stop diagnostics established the sole rejection
reason `SUMMARY_TOO_LONG`; no failed test was reclassified as PASS.

## Recovery contract

The current `automation/ai-loop/task.json` pins:

- old base: `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`
- stopped candidate: `474679ecda36da610a96e7ec84da39a5dd3ae33b`
- original control: `98b1871be51128da8dcdcf6eb32c27e2af89fa14`
- stopped summary SHA-256:
  `6d5abc6c1770e2135379f776f728b07d625674fe5c246f858303b1e985065a0e`
- round-2 failure evidence SHA-256:
  `db07a8362b2349a0061637aaebd6782d3324640223e1ae52942a06a29841c585`
- round-1 Mac evidence SHA-256:
  `51df9e877328f6da51b1d0b72ea0dad171229ab446dbbe3a97283c4dbcfc8dd0`
- new main: `63e232e22a769f409e381e34dfe0ef1add766d8e`
- classifier rejection: `SUMMARY_TOO_LONG`
- sole approved upstream path:
  `tools/llmtool/tests/test_diagnostics_failure_matrix.py`

No A11b allowed file overlaps that upstream path.

## Safety behavior

The new `stopped_failure_recovery` path:

1. requires the exact local branch/HEAD and an unpublished branch with no PR;
2. verifies the old base is an ancestor of both the stopped candidate and new main;
3. verifies candidate changes are confined to the unchanged A11b allowlist;
4. verifies old-base -> new-main differs by exactly the pinned upstream path set;
5. revalidates original task/provenance/summary/failure bytes and the classifier
   rejection from the local stopped run;
6. revalidates every stopped candidate file SHA-256;
7. verifies the prior synthetic Mac fixture cleanup is RESOLVED and performs a
   fresh read-only absence check for the recorded old PIDs; it never signals them;
8. snapshots the stopped candidate/evidence before integration;
9. creates a two-parent merge with the exact pinned new main, then proves every
   stopped A11b byte is unchanged and the upstream path equals new main;
10. starts with deterministic validation, not Codex. No historical test/review PASS
    is inherited. New blockers may enter the existing bounded evidence-bound
    Claude -> Git recommendation -> Codex repair loop.

Any extra upstream path, overlap, hash mismatch, classifier mismatch, stale PID,
published feature branch, dirty tracked/index state, ancestry drift, or merge
shape mismatch stops without weakening gates.

## Automated helper validation

A dedicated `ai-loop helper CI` workflow was added on the persistent control
branch so helper changes no longer wait for the Mac to discover control-plane
regressions.

Validated control HEAD:
`9a0df89dc4825cad1dbb6e49c8f7c3467a40312d`

GitHub Actions push run:
`37692756093`

Both matrix jobs passed:

- Python 3.9: Python syntax PASS; Bash launcher syntax PASS; offline helper suite PASS.
- Python 3.14: Python syntax PASS; Bash launcher syntax PASS; offline helper suite PASS.
- Offline helper suite: **181 tests PASS**.

The regression set includes exact stopped-failure pins, rejected-classification
binding, upstream path rejection, candidate/evidence drift rejection, strict
recovery-schema exclusivity, and a real temporary-Git two-parent integration test
that proves candidate bytes remain unchanged.

## Pending physical evidence

The control-plane CI cannot see David's local `.ai-loop/20261007T183516Z-7365`
store or unpublished candidate. The next action is therefore a **read-only Mac
preflight**:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T183516Z-7365 --check
```

This preflight calls no model, performs no merge, runs no feature/native test, and
does not consume the one-use recovery marker. A PASS is required before the actual
resume command is authorized.

No A11b production/source change, feature publication, security acceptance, merge,
A11b.2.2.2, A11c or A12 advancement is claimed by this control-plane validation.
