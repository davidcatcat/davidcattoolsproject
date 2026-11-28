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
| Test mode interface handoff | #2834 merged: test mode interface; requirement recorded in `docs/design/a13-test-execution-modes.md` (revised 2026-11-28 per [PR #2923](https://github.com/davidcatcat/davidcattoolsproject/pull/2923) and Owner record [#2793 comment 7236027900](https://github.com/davidcatcat/davidcattoolsproject/issues/2793#issuecomment-7236027900): supersedes only the one-mode-per-project exclusivity in favor of zero, one, or multiple explicitly selected checks with one mode per automated check and no fallback, separate manual acceptance, the no-test `NOT_TESTED` path, evidence binding, and mandatory independent review and merge gates; runner-kind boundary in `tools/llmtool/llmtool/test_profile.py` `TestProfile.kind_for`, `default_profile`, evidence binding, historical evidence, and the open GitHub pre-PR timing decision stay binding). | Full mode implementation is A13.2, outside the A13.0 exit. The 2026-11-28 revision takes effect for A13.2 implementation only after formal review. | PM records the A13.2 scope for full mode implementation and verifies formal review of the 2026-11-28 revised notes before any A13.2 implementation; A13.0 hands off at the merged interface and declares no runtime validation or exit closure. |

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

### Dated follow-up note (2026-11-28, update)

Update to the dated 2026-11-28 note above: the merged facts below are
recorded as supplied (no test run, runtime validation, or exit closure
is claimed; closure stays pending; #2836 and #2837 remain open and
unmerged; the pinned main
`4ffd749a95b313591c383b13e1148d80c9704cca` remains unchanged as the
source snapshot):
- [PR #2869](https://github.com/davidcatcat/davidcattoolsproject/pull/2869),
  merged as `6b473b4f`: inventory rows OSR-16, OSR-23, OSR-25 in
  `a13-osr-platform-crosswalk.md`.
- [PR #2870](https://github.com/davidcatcat/davidcattoolsproject/pull/2870),
  merged as `2268ba77`: the two dry-run findings in
  `a13-dry-run-acceptance-crosswalk.md`.
- [PR #2914](https://github.com/davidcatcat/davidcattoolsproject/pull/2914),
  merged as `54b6031d`: the deferred ProjectContext consumers in
  `a13-project-context-consumer-handoff.md`; the explicit deferral of
  unwired ProjectContext consumers is recorded there, correcting the
  prior statement that it was not recorded.
- [PR #2912](https://github.com/davidcatcat/davidcattoolsproject/pull/2912),
  merged as `d1427b5c`: production refusal of double-leading-slash
  project roots on POSIX, with tests.
- [PR #2919](https://github.com/davidcatcat/davidcattoolsproject/pull/2919),
  merged as `ad367ece`: production constructor refusal of compiled
  bytes patterns, with tests; correcting the statement in the dated
  note above (dated 2026-11-28) that #2919 (head `c1240594`) was
  awaiting formal review.

### Dated follow-up note (2026-11-28, second update)

Update to the dated 2026-11-28 notes above: the merged fact below is
recorded as supplied (no test run, runtime validation, or exit closure
is claimed; closure stays pending; the pinned main
`4ffd749a95b313591c383b13e1148d80c9704cca` remains unchanged as the
source snapshot):
- [PR #2926](https://github.com/davidcatcat/davidcattoolsproject/pull/2926),
  merged as `250d9212`: `test_hosted_ci_repair.py` now covers,
  through `failure_for_task`, refusal of a missing, raising or
  wrong-type CI profile and of a substituted Linux workflow file or
  check name the trust settings do not contain, each without a
  GitHub read. Test code only. Refusal of duplicate check names is
  not covered.

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
