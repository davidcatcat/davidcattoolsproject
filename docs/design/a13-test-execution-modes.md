# A13.0 Design Note: Test Execution Modes (#2793)

One-page note defining the A13.0 test-mode interface (revised 2026-11-28). It
records phase boundaries only; it leaves the selection of the GitHub pre-PR
approach and all implementation to A13.2.

Sources: #2793 Owner decision, GitHub comment 7208490147; Coordinator review,
GitHub comment 7210501920; Roadmap amendment
[PR #2923](https://github.com/davidcatcat/davidcattoolsproject/pull/2923) and
formal PM-relayed Owner record
[#2793 comment 7236027900](https://github.com/davidcatcat/davidcattoolsproject/issues/2793#issuecomment-7236027900)
(2026-11-28).

## Phase boundaries

- A13.0 (this note): define the execution modes, the conceptual evidence
  binding, the runner-kind / execution-mode boundary, and the 2026-11-28
  validation/merge policy alignment.
- A13.2: implement the mode interface and make the GitHub pre-PR selection.
- Effective date: the 2026-11-28 revision takes effect for implementation only
  after formal review. Nothing here claims implementation, runtime validation,
  or exit closure, or introduces any new product decision, interface, or scope
  beyond the Roadmap section "External project validation and CI policy
  (2026-11-28 product direction)".

## Revision scope (2026-11-28): superseded vs. binding

- **Superseded (for implementation only after formal review):** only the
  earlier requirement of *exactly one automated execution mode per project* is
  superseded, per
  [PR #2923](https://github.com/davidcatcat/davidcattoolsproject/pull/2923) and
  Owner record
  [#2793 comment 7236027900](https://github.com/davidcatcat/davidcattoolsproject/issues/2793#issuecomment-7236027900).
- **Still binding:** the three automated execution modes (`local`, `github`,
  `remote`), no mode inference and no silent fallback, no cross-mode
  substitution, the runner-kind / execution-mode boundary, all evidence binding
  and failure rules, existing self-development gates and security policy,
  historical evidence, and the open GitHub pre-PR timing decision.

## Execution modes (explicit per automated check)

A project may explicitly configure zero, one, or multiple required validation
checks, with **exactly one explicitly selected execution mode** (`local`,
`github`, or `remote`) per automated check. A mode is never inferred from
failure or environment, and there is no silent fallback. Multiple checks in
different modes are not fallback or substitutes for one another.

- `local` — default proposed for new users. The runner executes on the user's
  own machine, at the recorded commit of the working tree.
- `github` — hosted exact-head CI. Evidence comes from a hosted runner that
  checked out the exact commit being attested.
- `remote` — a configured user-owned host. Evidence comes from a runner on a
  host the user owns and has configured.

This repository continues `remote` on the existing remote EC2 host; existing
self-development gates, security policy, and the Owner Mac policy apply
unchanged. If the selected mode cannot produce valid evidence for a requested
check (including when a required target toolchain is missing), the check fails
rather than falling back or skipping.

## Manual acceptance, no-test path, and merge gates

- **Separate manual acceptance:** manual acceptance is separately recorded and
  is not an automated execution mode or a substitute for a required automated
  check.
- **No-test path (`NOT_TESTED`):** a project explicitly configured with zero
  automated checks may proceed with development, but records `NOT_TESTED` (or
  equivalent) with scope and reason, never an automated PASS.
- **Mandatory independent review and merge gates:** independent formal review
  of the exact head, trust, worktree isolation, credential isolation, and
  mandatory repository checks cannot be disabled. Independent formal PASS and
  valid evidence for every configured required check are required before any
  llmtool-mediated merge (manual or automatic). Failed, pending, unavailable,
  stale, mismatched, or unknown required evidence blocks both. No manual
  acceptance, human confirmation, or preset overrides a required failed or
  missing check or branch protection.
- **Merging `NOT_TESTED`:** automatic merge of a `NOT_TESTED` revision is
  prohibited. A manual merge through llmtool requires separately recorded
  explicit authorized human acceptance for that exact project and head,
  independent formal PASS, and all applicable mandatory checks; it is allowed
  only when no required automated check is outstanding.

## Evidence binding (conceptual)

A piece of test evidence for each automated check is bound to, and accepted
only when it matches, all of:

1. Project or repository identity it was produced for.
2. The exact commit it covers (full, unambiguous identity).
3. The selected mode (`local`, `github`, `remote`) recorded at check time.
4. The configured test selection and outcome: which tests were selected and
   what result each produced.

Missing, mismatched, ambiguous, or unverified required evidence fails the
check; it is never downgraded to `NOT_TESTED`, substituted, or replaced by
manual acceptance. Unverified covers anything whose binding the accepting side
cannot check.

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
