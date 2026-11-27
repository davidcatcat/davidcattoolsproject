# A13 Remote Test Profile Boundary — Consumer Handoff

**Scope:** documentation only. No code, tests or gate changes, no new mode,
no external repo changes.
**Pinned main:** `d71412f7b5b379b506b049cb2438b31d8fbff952`
**Citations:** Issue #2793; Coordinator review comment 7210501920.
**Kept in A13.2:** runner relocation and execution-mode implementation.

This handoff lists the advisor-verified selection paths on pinned main and
the regression cases a behaviour-neutral wiring change must preserve.

## 1. Verified selection paths (pinned main)

### 1.1 `remote_test_part1.py` — `_task_path_modules`, `_admitted_modules`, `remote_required_modules`

- `_task_path_modules` (task-path module selection) and `_admitted_modules`
  (admitted-modules step) derive test module stems from hardcoded tool test
  paths; the same derivation covers the stored-evidence fallback.
- `remote_required_modules` adds exact-head PR-changed stems and excludes
  classified Darwin-only modules.

### 1.2 `tools/llmtool/llmtool/test_profile.py` — `TestProfile.kind_for`, `default_profile`, `TestProfileError`

- `TestProfile.kind_for` matches a broader path set than the remote side:
  unittest paths may include uppercase letters, and `scripts/test_*.py`
  paths get a separate `script` runner kind; `default_profile` kinds are
  exactly `unittest` and `script`.
- `TestProfile.kind_for` and `default_profile` only classify paths. They are
  not a remote-runner admission decision.
- `TestProfileError` (code `TEST_PROFILE_INVALID`) rejects invalid entries at
  construction: empty entry list, non two-item pair, non-compiled pattern,
  or a kind other than exactly `unittest` or `script`.

### 1.3 `task_advance._pr_changed_test_modules`, `remote_test_part5._pr_changed_test_modules_for_admission`, `remote_test_part2.run_remote_test`

- `task_advance._pr_changed_test_modules` independently matches changed
  module paths and ignores removed files at the exact head.
- `remote_test_part5._pr_changed_test_modules_for_admission` uses that
  reader to widen the module set for run admission.
- In `remote_test_part2.run_remote_test` the modules actually executed are
  the explicit modules supplied, which on pinned main may be any nonempty
  subset of the admitted set; the PR-changed admission helper is called
  only when that set needs widening. A declared-only subset can therefore
  run without the PR-changed module.

### 1.4 Parity obligation

Three sets must not be conflated: the **required coverage set** (default
declared stems plus exact-head PR-changed stems), the **admitted set** the
runner may execute, and the **modules actually executed** (explicit modules
passed to `remote_test_part2.run_remote_test`, a nonempty subset of the
admitted set on pinned main). Parity obligation for the future wiring
change: the validation-side derivation (`remote_test_part1._task_path_modules`,
`_admitted_modules`, `remote_required_modules`) and the admission-side
derivation (`remote_test_part5._pr_changed_test_modules_for_admission`) must
agree on the required coverage set for the same exact head; the modules
actually executed need not equal it.

## 2. Regression table

Each case must hold before and after the behaviour-neutral wiring change.

| Case | Required behaviour |
| --- | --- |
| Default declared plus PR-changed union | `remote_test_part1._task_path_modules` derives the default declared stems; `task_advance._pr_changed_test_modules` adds exact-head PR-changed stems; together they form the required coverage set validation and admission must agree on at the same head. The modules actually executed by `remote_test_part2.run_remote_test` are the explicit modules supplied (any nonempty subset of the admitted set), widened by `remote_test_part5._pr_changed_test_modules_for_admission` only when needed. |
| Removed file | Removal at the exact head adds no PR-changed stem: `task_advance._pr_changed_test_modules` ignores removed files. Declared stems are accounted separately: `remote_test_part1._task_path_modules` still derives a module from declared `read_paths` or `write_paths`, and `remote_required_modules` can select it. |
| Darwin-only exclusion | `remote_required_modules` excludes classified Darwin-only modules. |
| Invalid path | A path matching no profile entry (`TestProfile.kind_for` returns `None`) or unmatched on the remote side is not admitted; an unmatched path must never become PASS. |
| Invalid profile | Constructing a `TestProfile` with an invalid entry raises `TestProfileError` (`TEST_PROFILE_INVALID`); no silent acceptance, no relaxed kind check. |
| Non-default profile fixture | An immutable non-default `TestProfile` is accepted and compared by pattern source, flags, and kind; `kind_for` honours entry order and whole-path `fullmatch`. |
| Lowercase module and runner constraints | The existing lowercase module-stem constraint on the remote side (stems from `_task_path_modules`/`_admitted_modules`) and the `unittest`/`script` runner-kind constraint enforced by `TestProfile` are explicitly preserved, unchanged. |
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
