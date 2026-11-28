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
| Traceable inventory | #2842 merged: inventory counts verified at the snapshot; admitted aggregates recorded in the linked reconciliation note. The two OSR row index notes, [a13-osr-index.md](./a13-osr-index.md) and [a13-osr-index-part2.md](./a13-osr-index-part2.md), give each of the 27 deduplicated OSR identifiers a row. | The 43 binding points and the 40 recheck rows are not mapped item by item; the full item-level row mapping stays outstanding, and no IDs or counts beyond the admitted aggregates are asserted. | PM completes the crosswalk rows using the admitted minimum fields; until a verified record exists, row-level mapping stays unverified. |
| Real profile wiring and non-default regression | #2838 merged: wiring subset recorded in the linked wiring index (named consumer modules and named regression test modules per merged PR). #2890 merged (merge SHA `9b5730a7`): the blank CI profile identifier regression, carried by the existing regression test [test_ci_profile_bounds.py](../../tools/llmtool/tests/test_ci_profile_bounds.py); its matrix shows constructor rejection coverage only: an empty or all-space identifier in any `CiProfile` field is refused with the fixed error code `CI_PROFILE_NAME_INVALID`, non-printable and 101-character names are refused, and 100-character names are accepted (name validation in `_validate_name`, profile source `tools/llmtool/llmtool/ci_profile.py`). No test run is claimed. | #2837: the dated 2026-11-29 follow-up note below supersedes the earlier statement in this row that #2837 was still open and records its replacement. #2843 is an interface note, not proof that EC2 wiring ran. Also not yet established and kept outstanding (not claimed as run): non-default CI name workflow and test-path regression evidence (the #2890 matrix is constructor rejection only, not workflow or test-path); the remaining missing- or ambiguous-settings refusal cases (invalid-name refusal is covered by the #2890 matrix; missing or ambiguous settings are not); and explicit deferral of unwired ProjectContext consumers. No runtime claim is made for unstated checks. | PM verifies #2837 closure, then checks the combined remote R1+R2 evidence against the wiring index; until then wiring evidence stays the #2838 subset. PM also obtains and verifies the non-default CI name workflow and test-path regression evidence; verifies the remaining refusal tests for missing or ambiguous settings (invalid-name refusal is the #2890 matrix; profile source `tools/llmtool/llmtool/ci_profile.py`, name validation in `_validate_name`); and records the explicit deferral of unwired ProjectContext consumers (placed outside this wiring subset in the linked note). None of these is claimed to have run. |
| Migration / rollback | 2026-11-29: the #2836 obligation was handed to the independent line by the Owner (relayed on #2793) and is delivered as two checklists: [rollout and rollback](a13-profile-wiring-revision-rollback.md) and [verification](a13-profile-wiring-verification.md). They replace PR #2945. | No rollback drill was run or is demanded. The checklists record four procedure gaps (install pin writer, revert commit step, runtime profile loading, train slice isolation) as gaps, not as done. | PM checks that the two checklists are merged on main and that their gaps stay recorded; no further runbook Task is needed for this row. |
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

### Dated follow-up note (2026-11-28, third update)

Update to the dated 2026-11-28 notes above: this note maps only the
regression and refusal sub-requirements of the "Real profile wiring and
non-default regression" row to the merged test modules below, all in
`tools/llmtool/tests`, recorded as supplied (no test run, runtime
validation, or exit closure is claimed); the deferral of unwired
ProjectContext consumers is recorded in the earlier update above through
PR #2914.
- Non-default CI name workflow and test-path regression evidence:
  substituted CI profile coverage in
  `test_a13_ci_profile_wiring.py`
  ([PR #2824](https://github.com/davidcatcat/davidcattoolsproject/pull/2824)),
  `test_a13_ci_profile_wiring_merge.py` (PR #2827, restored lookup PR
  #2884) and `test_a13_ci_profile_wiring_train.py`
  ([PR #2930](https://github.com/davidcatcat/davidcattoolsproject/pull/2930),
  merged as `c0684d9e`); and substituted test directory execution in the
  group `NonDefaultDirectoryExecutionTests` of
  `test_a13_test_profile_wiring.py` (changed by
  [PR #2925](https://github.com/davidcatcat/davidcattoolsproject/pull/2925),
  merged as `495f20b6`).
- Missing- or ambiguous-settings refusal evidence: invalid or ambiguous CI
  profile refusal in `test_a13_ci_profile_wiring.py` (PR #2824, extended by
  PR #2872 for one workflow file in both roles),
  `test_a13_ci_profile_wiring_merge.py` (PR #2827, restored lookup PR
  #2884) and `test_a13_ci_profile_wiring_train.py` (PR #2930, merged as
  `c0684d9e`); the group `RefusalTests` of
  `test_a13_test_profile_wiring.py` (changed by
  [PR #2825](https://github.com/davidcatcat/davidcattoolsproject/pull/2825),
  merged as `d3594f48`, and
  [PR #2919](https://github.com/davidcatcat/davidcattoolsproject/pull/2919),
  merged as `ad367ece`; PR #2925 did not change it); and duplicate CI check
  name refusal in the CI repair path only
  ([PR #2932](https://github.com/davidcatcat/davidcattoolsproject/pull/2932),
  merged as `05e96294`), which refines the duplicate-check-name statement
  in the second update above for that CI repair path.

Outstanding for the row: PR #2837 is open and the combined remote
evidence follows it; PM verification on a pinned main is pending.

### Dated follow-up note (2026-11-29)

Update to the "Real profile wiring and non-default regression" row: the
facts below are recorded as supplied (no test run, runtime validation,
or exit closure is claimed; closure stays pending):
- [PR #2950](https://github.com/davidcatcat/davidcattoolsproject/pull/2950),
  merged as `499ca239c30aa3375490005553db8ca422d8e2ca`: delivers the task
  advance CI wait, rerun and failure routing slice that reads the CI
  profile, with a new wiring regression test module.
- PR #2837 is closed unmerged and replaced by PR #2950.

This note supersedes the earlier statements that PR #2837 is open and
that the row is outstanding on it. The combined remote evidence and PM
verification on a pinned main are still pending.

### Dated follow-up note (2026-11-29, update)

Update to the "Real profile wiring and non-default regression" row:
this note records only the two merged A13.0 slices below and nothing
else, each as supplied (no test run, runtime validation, or exit
closure is claimed; closure stays pending on R1 and R2):
- [PR #2955](https://github.com/davidcatcat/davidcattoolsproject/pull/2955),
  merged as `a2cf0ea3`, titled "Read scope advice searches the
  TestProfile test locations": the module
  `read_scope_advice.py`; the regression test module
  `test_a13_read_scope_profile.py`.
- [PR #2956](https://github.com/davidcatcat/davidcattoolsproject/pull/2956),
  merged as `b26a297e`, titled "Executor test evidence paths come from
  the TestProfile": the module
  `executor_test_evidence.py`; the regression test module
  `test_executor_test_evidence.py`.

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
