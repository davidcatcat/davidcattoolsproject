# A13.0 ProjectContext Consumer Handoff

Single public note. Records the explicit deferral of unwired
ProjectContext consumers that the A13.0 exit checklist requires,
placed outside the wiring subset. Not a
design note, not a rollout runbook. No new policy, no new consumer
interface, no implementation.

- Pinned to the spec base of this Task:
  `bc9dc7b42931b04642a03bcc785843c1810ccb57`, the base the change was
  created from and the review base.
- Authority: issue #2793, comment 7210501920; Roadmap A13.0 exit.
- Linked exit evidence:
  [a13-exit-evidence.md](./a13-exit-evidence.md) — the A13.0 exit
  evidence matrix, present at the spec base. Its "Real profile wiring
  and non-default regression" row lists "explicit deferral of unwired
  ProjectContext consumers" as still outstanding and states that it is
  placed outside the wiring subset in the linked note; the matrix does
  not name or link this file. The matrix's own "Pinned main"
  `4ffd749a95b313591c383b13e1148d80c9704cca` is cited here solely as
  the source snapshot that matrix's supplied facts were verified
  against; it is not this Task's spec base.

## Facts at the pin (supplied, recorded as supplied)

Facts are tied to the spec base
`bc9dc7b42931b04642a03bcc785843c1810ccb57`.

- The pure ProjectContext value exists in
  `tools/llmtool/llmtool/project_context.py`: `ProjectContext`
  (validated, immutable; every rejection is the fixed code
  `PROJECT_CONTEXT_INVALID` with no input value) and the
  `from_trust` factory (`ProjectContext.from_trust`, with the
  module-level alias `from_trust`). Validation never reads files,
  uses the network or starts processes.
- The Python source search at the spec base found no production
  consumer outside the defining module: no production import or call
  site of `ProjectContext` or `from_trust` was found outside
  `tools/llmtool/llmtool/project_context.py`. That statement is
  limited to production code. Test files reference the value and the
  factory (for example, `tools/llmtool/tests/test_project_context.py`
  and `tools/llmtool/tests/test_a13_contracts.py`), and tests are not
  production consumers.

## Pending phase handoffs

The following are the supplied, existing phase handoffs, recorded as
pending. Nothing here builds, wires, validates or claims that any
consumer exists, runs or has been verified in an external repository
or at runtime.

| Phase | Pending handoff |
|---|---|
| A13.1 | Project registration and trust selection. |
| A13.2 | Task and worktree, plus GitHub and test or CI consumers. |
| A13.3 | Project isolation. |

No new policy, no new consumer interface: the interface handed off is
the existing `ProjectContext` value and `from_trust` factory in
`tools/llmtool/llmtool/project_context.py`, unchanged.

## Gates

- Exact-head hosted docs check: this file is verified against the exact
  result HEAD it accompanies; the supplied facts are tied to the spec
  base `bc9dc7b42931b04642a03bcc785843c1810ccb57`, and
  `4ffd749a95b313591c383b13e1148d80c9704cca` is cited solely as the
  matrix's declared source snapshot.
- Formal review by codex-reviewer.

Docs only: no code, CI, Task-state or external repository mutations.
No runtime claim is made for unstated checks.
