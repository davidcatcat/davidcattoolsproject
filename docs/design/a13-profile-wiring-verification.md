# A13.0 Profile Wiring: Verification and CI Order Checklist

Date: 2026-11-28  
Status: Verification checklist only — no run, live verification, or A13.0 exit closure is claimed here; pending checks are marked `[PENDING]`.

## 1. Required Checks and CI Order

Per `AGENTS.md` (Merge gates), `tools/llmtool/scripts/ci_path_classes.py`, `task_advance.py`, and `task_completion_part4.py`, the delivery and check order for `llmtool` PRs is:

1. **Developer execution (D6):** Edit only `write_paths` in the registered D6 worktree; run no tests in the D6.
2. **Branch push (`llmtool task advance`):** Push the D6 branch head.
3. **EC2 remote-test (`llmtool task remote-test`):** Required before PR for code/test PRs; skipped (`NOT_APPLICABLE_DOCS_ONLY` / optional receipt) for docs-only or scripts-only PRs (`task_completion.run_bounded_tests`, `merge_train_eligibility._receipt_optional`).
4. **PR publication (`llmtool task advance`):** Open PR against `main`.
5. **Linux CI (`llmtool Linux CI`):** Runs on `pull_request` and `main` push. `task_advance._required_linux_checks` calls `ci_path_classes.linux_flags` across both `filename` and `previous_filename` (`--no-renames` semantics):
   - `docs` (`*.md`, `AGENTS.md`, `CLAUDE.md`, `docs/**`, non-`.py` under `.claude/skills/`, top-level `tools/llmtool/*.md`) → `docs` flag → `check (docs)`.
   - `runtime` (`tools/llmtool/**` other than top-level `*.md`), `workflow` (`.github/workflows/**`), `other` (including `.claude/skills/*.py`) → `code` flag → `check (Linux 3.14)` (plus `check (docs)` if any `*.md` path is also changed).
   - `gateway` (`tools/api-agent-gateway/**`) → `code` + `gateway` flags → `check (Linux 3.14)` + `gateway tests (Linux 3.14)`.
   - `ai-loop` (`tools/ai-loop/**`) → `ai_loop` flag → `ai-loop helper tests (Python 3.14)`.
6. **Formal cross-family review:** Independent `codex-reviewer` for non-Claude Developer changes (`claude-reviewer` fallback) or `codex-reviewer` for Claude Developer and PM changes, in a separate read-only context with its own D6 Reviewer record.
7. **Mac CI (`llmtool Mac CI`, `check (macOS 3.13)`):** Required by `task_advance._merge_checks` and `task_completion_part4._collect_ci_evidence` only when `not docs_only` and `task_advance._mac_ci_runs` matches `paths + old_paths` against `task_advance.MAC_CI_PR_PATHS` (pinned to `.github/workflows/llmtool-mac-ci.yml` by `test_task_advance_mac_paths.py`). After formal `PASS`, `task_advance._merge_once` / `label_mac_ci_for_skipped_check` / `merge_train_ops._merge_locked` adds the `mac-ci` label so the Mac job runs on exact HEAD.
8. **Exact-head squash merge:** `task_advance._merge_main_gate` allows merge without syncing `main` when mergeable and no conflict exists.
9. **Main FULL Mac suite (`llmtool ci full-mac-due`):** Dispatched on `main` by `mac_full_due.request_full` (or `request_train_full` after a merge train) when `main` has no trusted FULL success and quiet/active guards pass.

## 2. Default Parity and Non-Default Consumer Fixtures

- `[PENDING]` **Default parity against an independent fixed baseline:**
  - Verify `ci_profile.default_profile` against a hardcoded expected baseline (not computed from `default_profile()`): `check (Linux 3.14)`, `check (docs)`, `gateway tests (Linux 3.14)`, `ai-loop helper tests (Python 3.14)`, `check (macOS 3.13)`, `llmtool-linux-ci.yml`, `llmtool-mac-ci.yml`.
  - Verify `test_profile.default_profile` against a hardcoded expected `(pattern, kind)` baseline in exact order: `tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z` → `unittest`, then `scripts/test_[A-Za-z0-9_]+\.py\Z` → `script`.
- `[PENDING]` **Non-default `CiProfile` fixtures at real consumers:**
  - `tools/llmtool/llmtool/mac_full_due.py`: `resolve_profile`, `full_jobs`, `full_runs`, `trusted`, `sha_result`, `decide`, `request_full`, `request_train_full`, `run_ci`.
  - `tools/llmtool/llmtool/mac_full_suite.py`: `request_full_suite`.
  - `tools/llmtool/llmtool/main_ci_job_fetch.py`: `inspect_jobs`.
  - `tools/llmtool/llmtool/task_advance.py`: `_resolve_ci_profile`, `_required_linux_checks`, `_merge_checks`, `_merge_once`, `label_mac_ci_for_skipped_check`.
  - `tools/llmtool/llmtool/merge_train_eligibility.py`: `_ci_gate`, `member_check`.
  - `tools/llmtool/llmtool/task_completion.py` & `task_completion_part4.py`: `collect_github_evidence`, `_resolve_ci_profile`, `_profile_check_name`, `_collect_ci_evidence`.
  - `tools/llmtool/llmtool/review_repair.py`: `_resolve_ci_profile`, `_ci_repair_checks`, `_ci_failure` (note: `repair_review` currently refuses an explicit `profile`).
  - `tools/llmtool/llmtool/start_failure.py`: `_ci_identity_sha256`, `_matches`, `repair_packet_matches`.
  - `tools/llmtool/llmtool/hosted_ci_repair.py`: `failure_for_task` (uses `review_repair._resolve_ci_profile`).
- `[PENDING]` **Non-default `TestProfile` fixtures at real consumers:**
  - `tools/llmtool/llmtool/task_completion.py`: `_test_profile_snapshot`, `_profile_kind`, `_classify_test_path`, `_eligible_registered_tests`.
  - `tools/llmtool/llmtool/task_completion_part3.py`: `_registered_test`.

## 3. Missing, Invalid, and Ambiguous Configuration Refusals

Verify that every consumer refuses without falling back to default names:

- **CI profile refusals:**
  - *Construction (`ci_profile.CiProfile`, `_validate_name`):* non-`str`, empty/whitespace-only, non-printable, or length `> 100` → `CiProfileError` (`CI_PROFILE_NAME_INVALID`).
  - *Missing/non-`CiProfile` (`mac_full_due.resolve_profile`, `task_completion_part4._resolve_ci_profile`, `review_repair._resolve_ci_profile`):* missing/raising `default_profile`, `None`, or non-`CiProfile` instance.
  - *Invalid workflow names (`mac_full_due.resolve_profile`):* workflow name containing `/`, `\`, `?`, `#`, equal to `.`/`..`, or `linux_workflow_file == macos_workflow_file`.
  - *Ambiguous check names:* duplicate check names across the five check fields, `macos_check` in `SHARD_JOBS`, duplicate `CiProfile` fields (`task_completion_part4._resolve_ci_profile`), or duplicate Mac/required checks in `task_completion_part4._collect_ci_evidence`.
  - *Consumer refusal codes:* `MAC_FULL_SUITE_PROFILE_INVALID` (`mac_full_due.request_full`, `mac_full_suite.request_full_suite`), `ci_profile_invalid` (`main_ci_job_fetch.inspect_jobs`), `MERGE_CI_PROFILE_INVALID` (`task_advance._merge_checks`, `_merge_once`), `TRAIN_MEMBER_CI_PROFILE_INVALID` (`merge_train_eligibility.member_check`), `CI_PROFILE_INVALID` (`task_completion_part4._resolve_ci_profile`, `_collect_ci_evidence`), `CI_REPAIR_PROFILE_INVALID` (`review_repair._resolve_ci_profile`, `repair_review` when `profile` is given), and `False` from `start_failure.repair_packet_matches`.
- **Test profile refusals:**
  - *Construction (`test_profile.TestProfile`):* non-iterable or empty `entries`, non-2-tuple entry, non-`re.Pattern` or compiled `bytes` pattern, or `kind` not in `('unittest', 'script')` → `TestProfileError` (`TEST_PROFILE_INVALID`).
  - *Missing/invalid provider (`task_completion._test_profile_snapshot`):* missing/raising `default_profile`, non-`TestProfile` return, or non-`str` `pattern.pattern` → `TEST_PROFILE_INVALID`.
  - *Ambiguous match (`task_completion._profile_kind`):* one path matching entries with different runner kinds → `TEST_PROFILE_INVALID`.

## 4. A13.0 Exit Requirement and Formal Review Gate

- **A13.0 exit requirement** (`tools/llmtool/ROADMAP.md`, A13.0 row): Current-main inventory with code/test references, target interfaces, migration/rollback and regression tests; no silent fallback to the original repository. (No end-to-end rollback drill is required.)
- **Formal review gate** (`.claude/skills/role-cards/reviewer.md`, `.claude/skills/ai-team-delivery/formal-verdict.md`):
  - Separate read-only cross-family Reviewer with its own D6 Reviewer record at the exact reviewed HEAD.
  - Unedited PR comment starting with the `review_verdict` marker, `Verdict: PASS | Version: vN | HEAD: <40-hex>`, `LLMTOOL-REVIEW-BINDING v1 task=<task_id> run=<run_id> contract=<contract_digest> head=<40-hex>`, `FORMAL SECURITY/FUNCTIONAL/QA ACCEPT`, and item-by-item acceptance verification.
  - For `codex-reviewer`, `llmtool task complete` requires `REVIEW_PASS` classification and one matching `COMPLETED` formal review dispatch claim binding identical non-empty review text to PR, HEAD, and Reviewer Task. General review does not satisfy specialist gates.
- **Docs gate:** Exact-head `check (docs)` `SUCCESS` in `llmtool Linux CI` and formal review `PASS`.
