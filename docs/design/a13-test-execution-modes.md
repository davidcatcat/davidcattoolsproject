# A13.0 Design Note: Test Execution Modes (#2793)

One-page note defining the A13.0 test-mode interface. It records phase
boundaries only; it leaves the selection of the GitHub pre-PR approach and all
implementation to A13.2.

Sources: #2793 Owner decision, GitHub comment 7208490147; Coordinator review,
GitHub comment 7210501920.

## Phase boundaries

- A13.0 (this note): define the execution modes, the conceptual evidence
  binding, and the runner-kind / execution-mode boundary.
- A13.2: implement the mode interface and make the GitHub pre-PR selection.

## Execution modes (explicit per project)

Each project selects one explicit mode. A mode is never inferred from failure
or environment, and there is no silent fallback.

- `local` — default for new users. The runner executes on the user's own
  machine, at the recorded commit of the working tree.
- `github` — hosted exact-head CI. Evidence comes from a hosted runner that
  checked out the exact commit being attested.
- `remote` — a configured user-owned host. Evidence comes from a runner on a
  host the user owns and has configured.

This repository continues `remote` on the existing remote EC2 host; the
existing Owner Mac policy applies unchanged. If the selected mode cannot
produce valid evidence for a requested check, the check fails.

## Evidence binding (conceptual)

A piece of test evidence is bound to, and accepted only when it matches, all
of:

1. Project or repository identity it was produced for.
2. The exact commit it covers (full, unambiguous identity).
3. The selected mode (`local`, `github`, `remote`) recorded at check time.
4. The configured test selection and outcome: which tests were selected and
   what result each produced.

Missing, mismatched, ambiguous, or unverified evidence is rejected, never
downgraded or substituted. Unverified covers anything whose binding the
accepting side cannot check.

## Runner kind is not execution mode

Runner kind is a property of the test; execution mode is where the runner
happens. The current profile in `tools/llmtool/llmtool/test_profile.py`
(`TestProfile.kind_for`, `default_profile`) answers exactly one question: for
a relative path, which runner kind runs it (`unittest` or `script`). It
carries no mode and no evidence, and it must not acquire either. A
`unittest`-kind test may execute locally, on hosted CI, or on a remote host.
The mode/evidence boundary defined here is what A13.2 builds around the
existing profile.

## GitHub pre-PR timing constraint (open)

- Branch CI could, where available, prove exact-head evidence before a PR
  exists: the pushed head is the committed push itself, so a pre-PR run can
  attest the exact commit.
- A PR-trigger-only setup has no such pre-PR run; its first run is tied to
  the PR and may check out a different ref than the pushed head. Relying on
  it for pre-PR exact-head evidence would require an explicit later gate
  design (which PR event counts, which ref it attests, how the pre-PR state
  is reconciled).

The decision remains open and belongs to A13.2. This note assumes no
workflow file, no hosted CI configuration, and no external repository
operation.

## Gates for this note

- Exact-head hosted docs check: this file is verified against the exact
  commit it accompanies.
- Formal review by codex-reviewer.

Out of scope for A13.0: code, schema, gates, credentials, workflows, and
external repository changes.
