# A13 Profile Wiring Evidence Index

Wiring subset of the A13 portability inventory. Evidence index only: not a
design document and not a rollout runbook.

## Pinned snapshot and authority

- Main SHA pinned: `7acc2508146285380d0bee1626405c01aa166099` (current index head;
  retains earlier snapshot `ab6b10a9` for the original four PR records).
- Authority: issue #2793, Coordinator review comment 7210501920.
- Original four PR records (`#2824`, `#2825`, `#2827`, `#2830`) are MERGED and
  present in commit `ab6b10a9` (supplied and checked by the Advisor; recorded as
  given).
- Added PR records (`#2829` `31734f77`, `#2832` `c494c928`, `#2835` `e8e883fb`)
  are MERGED and present at main `7acc2508146285380d0bee1626405c01aa166099`,
  with regression test files inspected directly in the worktree.
- Profile source: `tools/llmtool/llmtool/ci_profile.py`; `CiProfile` values
  constructed by `default_profile` (name validation in `_validate_name`).

## Recorded wiring by merged PR

Each row is sourced from the listed PR record only. "Profile" names the CI or
test profile to which the listed modules are recorded as consumers in that
record.

| PR (source) | Merge SHA | Profile | Consumer modules | Regression test module |
|---|---|---|---|---|
| #2824 | `ff704073` | CI profile | `mac_full_due.py`, `mac_full_suite.py`, `main_ci_job_fetch.py` | `test_a13_ci_profile_wiring.py` |
| #2825 | `d3594f48` | Test profile | `task_completion.py`, `task_completion_part3.py` | `test_a13_test_profile_wiring.py` |
| #2827 | `6de84094` | CI profile | `main_incident.py`, `main_incident_cli.py` | `test_a13_ci_profile_wiring_merge.py` |
| #2830 | `a090bb2e` | CI profile | `merge_train_eligibility.py`; merge check gate in `task_advance.py` | `test_a13_ci_profile_wiring_gate.py` |
| #2829 | `31734f77` | CI profile | `review_repair.py`, `start_failure.py` (regression test also exercises `task_submission_cli.py` and `TaskStore` in `task_store.py`) | `test_a13_ci_profile_wiring_repair.py` |
| #2832 | `c494c928` | CI profile | `task_completion.py`, `task_completion_part4.py` | `test_a13_ci_profile_wiring_completion.py` |
| #2835 | `e8e883fb` | CI profile | `task_submission_cli.py` | `test_a13_ci_profile_wiring_prompt.py` |

### Substituted-profile and refusal coverage (test coverage only)

The coverage below is observed from inspecting the three in-scope regression
test files; it records unit-test coverage with mocked external boundaries
(GitHub API, route runners, and patched `ci_profile.default_profile`) and does
not represent a new test run or live runtime execution.

- **#2829 (`31734f77`) — CI repair and start-failure packet verification**
  (`tools/llmtool/tests/test_a13_ci_profile_wiring_repair.py`):
  - *Substituted-profile coverage*: `RealTaskStoreRepairAndRetryTests.test_substituted_profile_via_real_resolver_builds_persists_verifies_and_retries`
    patches `ci_profile.default_profile` to `ALT_PROFILE`, builds a CI repair
    packet via `review_repair.repair_review`, persists and re-reads it through
    `TaskStore` without adding `ci_check_names`, verifies the packet with
    `start_failure.repair_packet_matches`, fails the first start in
    `cli.run_task_claude_delegate`, and retries via `cli.run_task_retry_start`.
    `R2SubstitutedProfileTests.test_substituted_names_build_and_bind_and_original_names_are_refused`
    verifies `review_repair._ci_failure` builds packets for all `ALT_PROFILE`
    check names under `ALT_PROFILE` and `start_failure.repair_packet_matches`
    returns `True` for those packets (both via patched `default_profile` and
    `profile=ALT_PROFILE`), while `review_repair._ci_failure` refuses original
    default check names under `ALT_PROFILE` with `CI_FAILURE_INVALID`. Default-profile
    parity and unchanged `TaskStore` claim fields (`_REVIEW_REPAIR_CLAIM_FIELDS`,
    `_REVIEW_REPAIR_OPTIONAL_FIELDS`) are covered by `R1DefaultProfileTests` and
    `RealTaskStoreRepairAndRetryTests.test_default_profile_builds_persists_verifies_and_retries_without_new_claim_fields`.
  - *Refusal coverage*: `RealTaskStoreRepairAndRetryTests.test_out_of_set_name_with_matching_digest_refuses_in_verifier_and_retry`
    verifies an out-of-set check name with a matching `packet_sha256` digest
    returns `False` from `start_failure.repair_packet_matches` and raises
    `LLMToolError` (`START_RETRY_REPAIR_PACKET_MISMATCH`) in `cli.run_task_retry_start`.
    `RealTaskStoreRepairAndRetryTests.test_explicit_profile_at_repair_review_refuses_without_persisting_claim`
    verifies explicit `profile=` in `review_repair.repair_review` refuses with
    `CI_REPAIR_PROFILE_INVALID` without persisting claims or dispatching.
    `R3InvalidProfileTests` (`test_direct_and_default_bad_profile_refuses_with_fixed_code`,
    `test_bad_or_missing_profile_never_binds_a_packet`,
    `test_cold_import_with_unavailable_profile_refuses_on_call_not_import`)
    verifies missing (`None`), invalid-type, mutated-empty, path-separator,
    ambiguous sequence, and raising profiles (`CiProfileError`, `RuntimeError`)
    refuse with `CI_REPAIR_PROFILE_INVALID` in `review_repair._ci_failure`
    (on call, not import) and return `False` in `start_failure.repair_packet_matches`.

- **#2832 (`c494c928`) — task completion Mac check**
  (`tools/llmtool/tests/test_a13_ci_profile_wiring_completion.py`):
  - *Substituted-profile coverage*: `SubstitutedProfileTests.test_mac_path_requires_and_accepts_the_profile_name`
    and `test_mac_path_does_not_accept_the_original_name` verify that under
    `ALT_PROFILE`, `task_completion._collect_ci_evidence` requires and accepts
    `ALT_PROFILE.macos_check` on Mac-requiring paths and marks it `MISSING` when
    only the original `MAC_CHECK` is present. `test_non_mac_path_excludes_the_mac_workflow_by_trust_identity`
    and `test_non_mac_path_keeps_linux_when_macos_name_matches_it` verify
    non-Mac paths exclude the Mac workflow even under `ALT_PROFILE` or
    `COLLIDING_MAC_PROFILE`. `test_github_entry_accepts_the_profile_name` and
    `test_github_entry_rejects_the_original_name` verify `task_completion.collect_github_evidence`
    accepts `ALT_PROFILE.macos_check` and reports `REQUIRED_CI_MISSING` when
    only `MAC_CHECK` is present. `SingleResolutionTests.test_github_decision_resolves_the_profile_once`
    verifies `collect_github_evidence` resolves `ci_profile.default_profile`
    once per decision, and `DefaultProfileTests` verifies default-profile parity.
  - *Refusal coverage*: `RefusedProfileTests.test_ci_entry_refuses_before_any_github_read`
    verifies missing (`None`), non-callable, raising, non-`CiProfile`,
    ambiguous sequence, mutated-empty, deleted-field, and duplicate-check-name
    profiles raise `LLMToolError` with `completion_code == 'CI_PROFILE_INVALID'`
    in `task_completion._collect_ci_evidence` before any `_github_get` call;
    `RefusedProfileTests.test_ambiguous_mac_mapping_refuses_before_any_read`
    verifies `COLLIDING_MAC_PROFILE` on Mac-requiring paths raises the same
    error in `task_completion._collect_ci_evidence` before any `_github_get`
    call; and `RefusedProfileTests.test_github_entry_refuses_before_any_read`
    verifies a missing (`None`) resolver raises the same error in
    `task_completion.collect_github_evidence` before any `_github_get` call.

- **#2835 (`e8e883fb`) — review prompt CI lines**
  (`tools/llmtool/tests/test_a13_ci_profile_wiring_prompt.py`):
  - *Substituted-profile coverage*: `R2SubstitutedProfilePromptTests.test_substituted_profile_reports_alt_names_and_no_original_names`
    verifies `task_submission_cli.run_task_review_prompt` under `ALT_PROFILE`
    emits `CI` status lines for `ALT_PROFILE.linux_code_check`,
    `ALT_PROFILE.macos_check`, and `ALT_PROFILE.docs_check` without any
    `ORIGINAL_CHECKS` names. `test_substituted_profile_with_only_original_checks_reports_none_recorded`
    verifies that when only `ORIGINAL_CHECKS` are recorded under `ALT_PROFILE`,
    the prompt outputs `'CI: no run of these checks is recorded for this HEAD yet.'`
    and omits `ORIGINAL_CHECKS`. `R1DefaultProfilePromptTests` verifies default
    check ordering and parity with explicit `default_profile()`.
  - *Refusal coverage*: `R3InvalidOrMissingProfilePromptTests`
    (`test_bad_profile_reports_ci_unavailable_without_original_names_or_check_read`,
    `test_failing_profile_resolver_reports_ci_unavailable`,
    `test_fresh_module_load_with_failing_profile_resolver`) verifies missing
    (`None`), invalid-type, mutated-empty, non-printable, ambiguous sequence,
    duplicate-check-name, and raising profiles (`CiProfileError`, `RuntimeError`,
    including after `importlib.reload(task_submission_cli)`) emit
    `'CI: unavailable; the check runs for this HEAD could not be read.'`,
    omit `ORIGINAL_CHECKS`, and do not read `commits/<HEAD>/check-runs`.

### Recorded wiring vs. proof

- Recorded here: the wiring as recorded in each PR — the named modules
  recorded as consumers of the named profile, the named regression test
  module, and for `#2829`, `#2832`, and `#2835` the substituted-profile and
  refusal test coverage observed in those test files.
- Distinguished from a new run: the index records merged PRs and static
  inspection of their test files; no new test run was executed for this
  documentation update.
- Not recorded here: that the full A13 milestone passed, that the live
  runtime passed, or any check not stated above. No claims are made for
  unstated checks.

## Pending verification

- PR `#2837` wait and rerun: pending verification.
- Combined remote R1+R2 verification: pending.
- Unwired `ProjectContext` consumers: still open (outside this wiring subset).
- Remaining A13 slices and unresolved portability items: pending
  verification until covered by a verified record.
- Historical binding-point inventory and its follow-up phases: separate from
  this index.

## Gates for this document

Docs-only check (`check (docs)`) and independent formal review by `codex-reviewer` on the new head.
