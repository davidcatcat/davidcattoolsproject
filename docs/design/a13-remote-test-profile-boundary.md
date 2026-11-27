# A13 Remote Test Profile Boundary — Consumer Handoff

**Scope:** documentation only. No code, tests or gate changes, no new mode,
no external repo changes.
**Pinned main:** `d71412f7b5b379b506b049cb2438b31d8fbff952`
**Citations:** Issue #2793; Coordinator review comment 7210501920.
**Kept in A13.2:** runner relocation and execution-mode implementation.

This handoff covers the remaining test-profile consumers: what each selection
path does on pinned main, and the regression cases a behaviour-neutral wiring
change must preserve. All facts below were advisor-verified on pinned main.

## 1. Verified selection paths (pinned main)

### 1.1 Remote runner side — `remote_test_part1.py`

- The task-path module selection and the admitted-modules step derive test
  module stems from hardcoded tool test paths; the same derivation covers the
  stored-evidence fallback.
- `remote_required_modules` adds exact-head PR-changed stems and excludes
  classified Darwin-only modules.

### 1.2 Path classification — `tools/llmtool/llmtool/test_profile.py`

- `TestProfile.kind_for` matches a broader path set than the remote side:
  unittest paths may include uppercase letters, and `scripts/test_*.py`
  paths get a separate `script` runner kind (kinds are exactly `unittest`
  and `script`, as returned by `default_profile`).
- This module only classifies paths. It is not a remote-runner admission
  decision.
- `TestProfileError` (code `TEST_PROFILE_INVALID`) rejects invalid entries at
  construction: empty entry list, a non two-item pair, a non-compiled
  pattern, or a kind that is not exactly `unittest` or `script`. The error
  message never carries caller data.

### 1.3 PR-changed selection — `task_advance.py`

- The PR-changed test selection independently matches changed module paths
  and ignores removed files at the exact head.
- `remote_test_part5.py` uses that reader, and `remote_test_part2.py`
  consumes the union (declared plus PR-changed) for run admission.

### 1.4 Parity obligation

For the same exact head, validation-side selection and actual runner-side
selection must agree module for module. Validation and runner are separate
derivations (sections 1.1 and 1.3); any divergence at one head is a
regression, because run admission (`remote_test_part2.py`) trusts the union
while the runner trusts the pinned stems.

## 2. Regression table

All cases are behaviour checks against pinned main; each must hold before and
after the wiring change.

| Case | Required behaviour |
| --- | --- |
| Default declared plus PR-changed union | Declared default stems and exact-head PR-changed stems form the union; validation and runner select the identical module set at the same head. |
| Removed file | A module path removed at the exact head is ignored: it adds no stem and is not selected. |
| Darwin-only exclusion | Classified Darwin-only modules are excluded from `remote_required_modules` output. |
| Invalid path | A path matching no profile entry (`TestProfile.kind_for` returns `None`) or unmatched on the remote side is not admitted; an unmatched path must never become PASS. |
| Invalid profile | Constructing a `TestProfile` with an invalid entry raises `TestProfileError` (`TEST_PROFILE_INVALID`); no silent acceptance, no relaxed kind check. |
| Non-default profile fixture | An immutable non-default `TestProfile` is accepted and compared by pattern source, flags, and kind; `kind_for` honours entry order and whole-path `fullmatch`. |
| Lowercase module and runner constraints | The existing lowercase module-stem constraint on the remote side and the `unittest`/`script` runner-kind constraint are explicitly preserved, unchanged. |
| Skipped-only run | A run whose outcome is skipped only must never become PASS. |

## 3. Test data rule (Owner #763 A)

This document changes no tests. Any regression test added under the table
must comply: ordinary tests never contain password-, API-key-, token- or
other credential-shaped strings; refusal-flow tests mock, monkeypatch or
inject the screen function and still check real behaviour; only
sensitive-detector tests may use the #981 labelled synthetic-data exception
(never real credentials). Never disable, bypass or weaken a production check
for test convenience.

## 4. Gates

- Exact-head hosted docs check against pinned main
  `d71412f7b5b379b506b049cb2438b31d8fbff952`.
- Formal review by `codex-reviewer`.
