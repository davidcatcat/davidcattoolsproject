# A13.0 Exit Evidence Matrix

Closure matrix only. Maps the approved A13.0 exit checklist to existing
evidence and the exact remaining PM checks. No new policy, no new
requirements, no implementation; not a design note and not a rollout
runbook.

- Pinned main: `4ffd749a95b313591c383b13e1148d80c9704cca`.
- Authority: issue #2793, comment 7210501920; Roadmap A13.0 exit.
- Advisor-verified snapshot facts, recorded as supplied.

Admitted source notes (linked; their tables are not copied here):
- `docs/design/a13-inventory-count-reconciliation.md` — admitted aggregates
  and the outstanding item-level mapping.
- `docs/design/a13-profile-wiring-evidence.md` — recorded wiring subset by
  merged PR, with profile source `tools/llmtool/llmtool/ci_profile.py`
  (`CiProfile`, `default_profile`).

## Matrix

| Exit item | Existing evidence | Missing evidence | Next objective PM check |
|---|---|---|---|
| Traceable inventory | #2842 merged: inventory counts verified at the snapshot; admitted aggregates recorded in the linked reconciliation note. | Full item-level row mapping outstanding; no IDs or counts beyond the admitted aggregates are asserted. | PM completes the crosswalk rows using the admitted minimum fields; until a verified record exists, row-level mapping stays unverified. |
| Real profile wiring and non-default regression | #2838 merged: wiring subset recorded in the linked wiring index (named consumer modules and named regression test modules per merged PR). | #2837 still open; the combined remote R1+R2 follows #2837. #2843 is an interface note, not proof that EC2 wiring ran. No runtime claim is made for unstated checks. | PM verifies #2837 closure, then checks the combined remote R1+R2 evidence against the wiring index; until then wiring evidence stays the #2838 subset. |
| Migration / rollback | None merged for exit: the #2836 rollback runbook is held on REPAIR_DEBUG_REQUIRED. | The missing #2836 exit evidence is retained as blocked; the runbook is not a substitute for it. | PM retains the missing exit evidence and re-verifies it in a verified record once the hold resolves; the hold is not waived and no end-to-end rollback drill is demanded. |
| Test mode interface handoff | #2834 merged: test mode interface; requirement recorded in `docs/design/a13-test-execution-modes.md` (`TestProfile.kind_for`, `default_profile` runner-kind boundary). | Full mode implementation is A13.2, outside the A13.0 exit. | PM records the A13.2 scope for full mode implementation; A13.0 hands off at the merged interface and declares no runtime validation. |

## Status

Closure is **pending**. Blocked work stays blocked: nothing here waives a
hold, declares runtime validation, or creates new requirements.

## Gates

- Exact-head hosted docs check at pinned main
  `4ffd749a95b313591c383b13e1148d80c9704cca`.
- Formal review by codex-reviewer.

Docs only: no code, CI, Task-state or external repository mutations.
