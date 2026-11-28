# A13.0 Profile Wiring Verification Runbook

- **Date:** 2026-11-28
- **Status:** Draft — `PENDING` verification. This document claims no drill, run, live verification, or exit closure.
- **Scope:** Documentation only. Retains EC2 pre-PR testing (`llmtool task remote-test`) and all existing `AGENTS.md` merge gates; changes no code, workflow, or live state.

## 1. Gate Order and Path-Scoped Check Selection

All PRs follow the `AGENTS.md` `llmtool task advance` sequence without reordering:

1. Push branch.
2. Run EC2 pre-PR tests (`llmtool task remote-test`) for code/test changes.
3. Open or update PR.
4. Run path-scoped hosted Linux CI (`llmtool-linux-ci.yml`).
5. Complete independent cross-family formal review (`PASS`).
6. PM adds `mac-ci` label **only after** formal `PASS` (never before).
7. Run hosted Mac CI (`check (macOS 3.13)` in `llmtool-mac-ci.yml`), skipped (`NOT_RUN`) for pure documentation PRs.
8. Merge without syncing `main` when there is no conflict.

Linux checks are **not** required uniformly on every head. Required checks depend on changed paths via `tools/llmtool/scripts/ci_path_classes.py` (`classify_path`, `LINUX_CLASS_FLAGS`, `linux_flags`):

| Changed Path Class | Required Linux Check(s) | Mac CI (`check (macOS 3.13)`) |
| --- | --- | --- |
| `docs` only (all paths `docs`, no `*.py` or `tools/llmtool/<subdir>/*.md`) | `check (docs)` | `NOT_RUN` |
| `runtime`, `workflow`, `other` | `check (Linux 3.14)` | Required after formal `PASS` |
| `gateway` (`tools/api-agent-gateway/`) | `check (Linux 3.14)`, `gateway tests (Linux 3.14)` | Required after formal `PASS` |
| `ai-loop` (`tools/ai-loop/`) | `ai-loop helper tests (Python 3.14)` | Required after formal `PASS` |
| Mixed code + any `*.md` / `docs/` | Code checks above + `check (docs)` | Required after formal `PASS` |

If `HOSTED_CI_ENABLED` is not `true`, skipped hosted jobs are `NOT_RUN`, never `PASS`.

## 2. Per-Slice Verification Checklist

### Slice H2a: CI Profile (`tools/llmtool/llmtool/ci_profile.py`) and Consumers

- [ ] **Default parity (independent baseline):** Compare `default_profile()` in `tools/llmtool/llmtool/ci_profile.py` against a fixed literal baseline that does not change when `default_profile()` is edited:
  - `linux_code_check == 'check (Linux 3.14)'`
  - `docs_check == 'check (docs)'`
  - `gateway_check == 'gateway tests (Linux 3.14)'`
  - `ai_loop_check == 'ai-loop helper tests (Python 3.14)'`
  - `macos_check == 'check (macOS 3.13)'`
  - `linux_workflow_file == 'llmtool-linux-ci.yml'`
  - `macos_workflow_file == 'llmtool-mac-ci.yml'`
- [ ] **Non-default use at real consumers:** Supply a non-default `CiProfile` fixture (distinct check names and workflow filenames) to the real CI check-name and workflow-file consumers (status polling, gate verification, and workflow lookup) and verify they query/match the fixture values rather than hardcoded defaults.
- [ ] **Refusal of missing, invalid, or ambiguous configuration:**
  - Verify `CiProfile.__post_init__` and `_validate_name` raise `CiProfileError` (`CI_PROFILE_NAME_INVALID`, without echoing the rejected value) for missing/non-`str`, empty, whitespace-only, non-printable, or `> 100`-char values.
  - Verify consumer wiring refuses missing or ambiguous profile configuration without silent fallback to `default_profile()`.

### Slice H3a: Test Profile (`tools/llmtool/llmtool/test_profile.py`) and Consumers

- [ ] **Default parity (independent baseline):** Compare `default_profile()` in `tools/llmtool/llmtool/test_profile.py` against a fixed literal baseline independent of `_DEFAULT_PROFILE`:
  - Entry 1: `r'tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z'` -> `'unittest'`
  - Entry 2: `r'scripts/test_[A-Za-z0-9_]+\.py\Z'` -> `'script'`
  - Probe `TestProfile.kind_for` with representative matching and non-matching relative paths (`fullmatch` semantics).
- [ ] **Non-default use at real consumers:** Supply a non-default `TestProfile` fixture (custom compiled path patterns and `'unittest'` / `'script'` kinds) to the real test-path discovery and runner-selection consumers and verify they use the fixture's patterns and kinds end-to-end.
- [ ] **Refusal of missing, invalid, or ambiguous configuration:**
  - Verify `TestProfile.__init__` raises `TestProfileError` (`TEST_PROFILE_INVALID`, without echoing caller data) for non-iterable input, empty entries, non-2-item entries, non-`str` compiled `re.Pattern`, or kinds outside `('unittest', 'script')`.
  - Verify consumer wiring refuses missing or ambiguous profile configuration without silent fallback.

## 3. Evidence Table

| Slice | Check | Target / Consumer | Status | Evidence |
| --- | --- | --- | --- | --- |
| H2a | Default parity vs fixed baseline | `ci_profile.py` `default_profile` | `PENDING` | Not yet run |
| H2a | Non-default check names & workflow files | Real CI check-name & workflow consumers | `PENDING` | Not yet run |
| H2a | Missing/invalid/ambiguous refusal | `CiProfile.__post_init__`, `_validate_name`, wiring | `PENDING` | Not yet run |
| H3a | Default parity vs fixed baseline | `test_profile.py` `default_profile`, `kind_for` | `PENDING` | Not yet run |
| H3a | Non-default test paths & runner kinds | Real test-path & runner consumers | `PENDING` | Not yet run |
| H3a | Missing/invalid/ambiguous refusal | `TestProfile.__init__`, wiring | `PENDING` | Not yet run |
| Docs PR | Exact-head hosted docs check | `check (docs)` via `ci_path_classes.py` | `PENDING` | Required for this PR |
| Docs PR | Formal cross-family review | Independent Reviewer | `PENDING` | Required for this PR |
