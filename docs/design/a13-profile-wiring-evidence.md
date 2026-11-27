# A13 Profile Wiring Evidence Index

Wiring subset of the A13 portability inventory. Evidence index only: not a
design document and not a rollout runbook.

## Pinned snapshot and authority

- Main SHA pinned: `ab6b10a9` (supplied verified main snapshot).
- Authority: issue #2793, Coordinator review comment 7210501920.
- All four PR records below are MERGED and present in commit `ab6b10a9`
  (supplied and checked by the Advisor; recorded as given).
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

### Recorded wiring vs. proof

- Recorded here: the wiring as recorded in each PR — the named modules
  recorded as consumers of the named profile, plus the named regression test
  module.
- Not recorded here: that the full A13 milestone passed, that the live
  runtime passed, or any check not stated above. No claims are made for
  unstated checks.

## Pending verification

- PR #2829 (repair/verification) and CI slices 5-7: tracked separately; not
  attested by this snapshot.
- Remaining A13 slices and unresolved portability items: pending
  verification until covered by a verified record.
- ProjectContext consumers: outside this wiring subset.
- Historical binding-point inventory and its follow-up phases: separate from
  this index.

## Gates for this document

Exact-head hosted docs check; formal review by codex-reviewer.
