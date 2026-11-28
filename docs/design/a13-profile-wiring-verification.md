# A13.0 Profile Wiring: Verification Checklist

Date: 2026-11-29. Checklist only: no run, verification or A13.0 exit closure
is claimed. Companion: [rollout and rollback](a13-profile-wiring-revision-rollback.md).

## 1. Gate order

`AGENTS.md` (Merge gates) states "push, EC2 before the PR, PR, Linux CI,
review, PASS, `mac-ci`, Mac CI, then merge". `task_advance.py` differs on one
point: `decide` returns `PUBLISH` while no PR matches the head, and
`_publish_task` pushes and opens the PR in that one step; `RUN_REMOTE_TEST`
comes after and tests `refs/pull/<number>/head`. The EC2 receipt is still
required before review and merge. Remaining order, as the code applies it:

1. Linux CI: `_required_linux_checks` maps changed paths through
   `ci_path_classes.linux_flags` to `check (Linux 3.14)`, `check (docs)`,
   `gateway tests (Linux 3.14)` and `ai-loop helper tests (Python 3.14)`.
2. Formal cross-family review (AGENTS.md, Formal Reviewer).
3. `mac-ci` label, then exact-head `check (macOS 3.13)` when `_mac_ci_runs`
   matches `MAC_CI_PR_PATHS` and the PR is not docs only.
4. Squash merge through `_merge_main_gate` and `_merge_checks`.

Docs-only waiver: `task_advance._pr_docs_only` (via
`ci_path_classes.classify_path`) waives the EC2 steps for a docs-only PR, and
`_merge_checks` requires no Mac check for one. The separate
`task_completion_part3._docs_only` serves local test evidence only.

## 2. Default parity

Compare with these literal values, never with a fresh `default_profile()`:

- CI: `check (Linux 3.14)`, `check (docs)`, `gateway tests (Linux 3.14)`,
  `ai-loop helper tests (Python 3.14)`, `check (macOS 3.13)`,
  `llmtool-linux-ci.yml`, `llmtool-mac-ci.yml`.
- Test, in order: `tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z` as
  `unittest`, then `scripts/test_[A-Za-z0-9_]+\.py\Z` as `script`.

## 3. Non-default profile at each real consumer

Each consumer must use the substituted names, never the defaults.

- `CiProfile` consumers:
  - `mac_full_due`: `resolve_profile`, `full_jobs`, `full_runs`, `trusted`,
    `sha_result`, `decide`, `request_full`, `request_train_full`, `run_ci`
  - `mac_full_suite.request_full_suite`; `main_ci_job_fetch.inspect_jobs`
  - `task_advance`: `_resolve_ci_profile`, `_required_linux_checks`,
    `_merge_checks`
  - `merge_train_eligibility`: `_ci_gate`, `member_check`
  - `task_completion_part4`: `_resolve_ci_profile`, `_profile_check_name`,
    `_collect_ci_evidence`
  - `review_repair`: `_resolve_ci_profile`, `_ci_failure`;
    `hosted_ci_repair.failure_for_task`
  - `start_failure`: `_ci_identity_sha256`, `_matches`,
    `repair_packet_matches`
  - `task_submission_cli`: `_review_ci_checks`, `_review_ci_lines`
  - `main_incident`: `run_kind`, `check_state`, `check_states`,
    `trusted_pass`, `head_failed`, `find_heads`, `classify`,
    `classify_incident`; `main_incident_cli` passes the profile through
    `collect` and `scan_state`
- `TestProfile` consumers: `task_completion` (`_test_profile_snapshot`,
  `_profile_kind`, `_classify_test_path`, `_eligible_registered_tests`) and
  `task_completion_part3._registered_test`.

## 4. Refusals, as each consumer refuses

A missing, raising, wrong-type or invalid profile must refuse; no consumer
falls back to default names.

- Construction: `CiProfile` refuses a bad name (`CI_PROFILE_NAME_INVALID`);
  `TestProfile` refuses bad entries (`TEST_PROFILE_INVALID`).
- `MAC_FULL_SUITE_PROFILE_INVALID`: `mac_full_due`, `mac_full_suite`.
- `MAIN_INCIDENT_PROFILE_INVALID`: `main_incident_cli`, when
  `mac_full_due.resolve_profile` raises `ProfileError`.
- `ci_profile_invalid` (reason): `main_ci_job_fetch.inspect_jobs`.
- `MERGE_CI_PROFILE_INVALID`: `task_advance`.
- `TRAIN_MEMBER_CI_PROFILE_INVALID`: `merge_train_eligibility.member_check`.
- `CI_PROFILE_INVALID`: `task_completion_part4`.
- `CI_REPAIR_PROFILE_INVALID`: `review_repair._resolve_ci_profile`, reached
  by `_ci_failure`, `repair_review` and `hosted_ci_repair.failure_for_task`.
- `None` / `False`: `start_failure._ci_identity_sha256` /
  `repair_packet_matches`.
- `task_submission_cli._review_ci_checks` returns `None`, and
  `_review_ci_lines` renders the "CI: unavailable" line.
- `TEST_PROFILE_INVALID`: `task_completion._test_profile_snapshot`, and
  `_profile_kind` for one path matched with two runner kinds.

## 5. Exit and review

- A13.0 exit is the ROADMAP A13.0 row; this checklist adds no rollback drill.
- A formal review follows `.claude/skills/role-cards/reviewer.md` and
  `.claude/skills/ai-team-delivery/formal-verdict.md`; a docs PR also needs
  exact-head `check (docs)`.
