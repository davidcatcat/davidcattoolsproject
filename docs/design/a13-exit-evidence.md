# A13.0 Exit Evidence Matrix

Closure matrix only. Maps the approved A13.0 exit checklist to existing
evidence and the exact remaining PM checks. No new policy, no new
requirements, no implementation; not a design note and not a rollout
runbook.

- Pinned main: `4ffd749a95b313591c383b13e1148d80c9704cca`.
- Authority: issue #2793, comment 7210501920; Roadmap A13.0 exit.
- Advisor-verified snapshot facts, recorded as supplied.

Admitted source notes (linked; their tables are not copied here):
- [a13-inventory-count-reconciliation.md](./a13-inventory-count-reconciliation.md)
  — admitted aggregates and the outstanding item-level mapping.
- [a13-profile-wiring-evidence.md](./a13-profile-wiring-evidence.md) —
  recorded wiring subset by merged PR, with profile source
  `tools/llmtool/llmtool/ci_profile.py` (`CiProfile`, `default_profile`).

## Matrix

| Exit item | Existing evidence | Missing evidence | Next objective PM check |
|---|---|---|---|
| Traceable inventory | #2842 merged: inventory counts verified at the snapshot; admitted aggregates recorded in the linked reconciliation note. | Full item-level row mapping outstanding; no IDs or counts beyond the admitted aggregates are asserted. | PM completes the crosswalk rows using the admitted minimum fields; until a verified record exists, row-level mapping stays unverified. |
| Real profile wiring and non-default regression | #2838 merged: wiring subset recorded in the linked wiring index (named consumer modules and named regression test modules per merged PR). #2890 merged (merge SHA `9b5730a7`): the blank CI profile identifier regression, carried by the existing regression test [test_ci_profile_bounds.py](../../tools/llmtool/tests/test_ci_profile_bounds.py); its matrix shows constructor rejection coverage only: an empty or all-space identifier in any `CiProfile` field is refused with the fixed error code `CI_PROFILE_NAME_INVALID`, non-printable and 101-character names are refused, and 100-character names are accepted (name validation in `_validate_name`, profile source `tools/llmtool/llmtool/ci_profile.py`). No test run is claimed. | #2837 still open; the combined remote R1+R2 follows #2837. #2843 is an interface note, not proof that EC2 wiring ran. Also not yet established and kept outstanding (not claimed as run): non-default CI name workflow and test-path regression evidence (the #2890 matrix is constructor rejection only, not workflow or test-path); the remaining missing- or ambiguous-settings refusal cases (invalid-name refusal is covered by the #2890 matrix; missing or ambiguous settings are not); and explicit deferral of unwired ProjectContext consumers. No runtime claim is made for unstated checks. | PM verifies #2837 closure, then checks the combined remote R1+R2 evidence against the wiring index; until then wiring evidence stays the #2838 subset. PM also obtains and verifies the non-default CI name workflow and test-path regression evidence; verifies the remaining refusal tests for missing or ambiguous settings (invalid-name refusal is the #2890 matrix; profile source `tools/llmtool/llmtool/ci_profile.py`, name validation in `_validate_name`); and records the explicit deferral of unwired ProjectContext consumers (placed outside this wiring subset in the linked note). None of these is claimed to have run. |
| Migration / rollback | None merged for exit: the #2836 rollback runbook is held on REPAIR_DEBUG_REQUIRED. | The missing #2836 exit evidence is retained as blocked; the runbook is not a substitute for it. | PM retains the missing exit evidence and re-verifies it in a verified record once the hold resolves; the hold is not waived and no end-to-end rollback drill is demanded. |
| Test mode interface handoff | #2834 merged: test mode interface; requirement recorded in `docs/design/a13-test-execution-modes.md`; the runner-kind boundary lives in `tools/llmtool/llmtool/test_profile.py` (`TestProfile.kind_for`, `default_profile`). | Full mode implementation is A13.2, outside the A13.0 exit. | PM records the A13.2 scope for full mode implementation; A13.0 hands off at the merged interface and declares no runtime validation. |

### Dated follow-up note (2026-11-28)

Follow-up to the "Real profile wiring and non-default regression" row: the
profile refusal coverage recorded for that row now also includes
(no runtime validation is claimed; closure remains pending):
- [PR #2911](https://github.com/davidcatcat/davidcattoolsproject/pull/2911),
  merged as `ee0c1bf0`: added `TestProfile` constructor refusal for
  non-iterable entry containers with the fixed error code
  `TEST_PROFILE_INVALID` (test profile source
  `tools/llmtool/llmtool/test_profile.py`).
- [PR #2919](https://github.com/davidcatcat/davidcattoolsproject/pull/2919),
  head `c1240594`, awaiting formal review: extends the `TestProfile`
  constructor refusal to compiled bytes regex patterns.

## Status

Closure is **pending**. Blocked work stays blocked: nothing here waives a
hold, declares runtime validation, or creates new requirements.

## Gates

- Exact-head hosted docs check: this file is verified against the exact
  result HEAD it accompanies; the pinned main
  `4ffd749a95b313591c383b13e1148d80c9704cca` is kept solely as the source
  snapshot the supplied facts were verified against.
- Formal review by codex-reviewer.

Docs only: no code, CI, Task-state or external repository mutations.
