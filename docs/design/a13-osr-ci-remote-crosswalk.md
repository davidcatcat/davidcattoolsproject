# A13 CI and Remote Crosswalk (OSR-07, OSR-27)

One-page crosswalk for the PM: two traceable OSR findings, their current
consumers, and the remaining A13 validation. Main is pinned at
`be913e781e8194fdd36e256d76e021f24a1454cb`.

Sources: the OSR findings inventory at #396, comment 6552370022, and the
Coordinator test-mode requirement at #2793, comment 7210501920. Counting
context and the unverified aggregate-to-row gap are recorded in
[a13-inventory-count-reconciliation.md](a13-inventory-count-reconciliation.md).
Merged evidence notes are linked by relative filename; only supplied,
non-sensitive facts are used.

## Rows

| Field | OSR-07 | OSR-27 |
|---|---|---|
| Source ID | OSR-07 | OSR-27 |
| Linked evidence | #396 comment 6552370022; merged note [a13-profile-wiring-evidence.md](a13-profile-wiring-evidence.md) | #396 comment 6552370022; merged note [a13-remote-test-profile-boundary.md](a13-remote-test-profile-boundary.md); interface record [a13-test-execution-modes.md](a13-test-execution-modes.md); PR [#2860](https://github.com/llmtool/llmtool/pull/2860) (reviewed HEAD `0e6926c0`, merged as `50a7668a`); hosted run [46081471824](https://github.com/llmtool/llmtool/actions/runs/46081471824) (same-head PASS) |
| Consumer | main CI readers and merge consumers; the completion collector reads `ci_trust` | remote test-profile boundary and test execution modes; the combined R1+R2 work |
| Disposition | Partial: #2829 merged as `31734f77` for CI repair checks; at the pinned base the task-advance wait and rerun work in #2837 remains pending | Pending: the hardcoded legacy remote module selection and the Darwin-only exclusion are recorded, together with the required/admitted/executed module distinction; the combined R1+R2 work follows #2837, which remains pending at the pinned base |
| Owning milestone | A13.1 (external repo CI trust) and A13.2 (full evidence validation) | A13.2 (relocation; full local, github and remote execution) |
| Duplicate-of | Unverified: no direct mapping | Unverified: possible overlap with the test-mode requirement (#2793 comment 7210501920) recorded in [a13-test-execution-modes.md](a13-test-execution-modes.md); the overlap is recorded so it is not counted twice |

## OSR-07 detail

[a13-profile-wiring-evidence.md](a13-profile-wiring-evidence.md) records the
merged CI profile wiring: the Mac selectors, the main CI readers, and the
merge consumers. The completion collector reads `ci_trust`. #2829 was merged
as `31734f77` for CI repair checks. At the pinned base, the task-advance wait
and rerun work in #2837 remains pending. OSR-07 is therefore treated as
partial: external repo CI trust and full evidence validation remain A13.1 and
A13.2 respectively.

## OSR-27 detail

[a13-remote-test-profile-boundary.md](a13-remote-test-profile-boundary.md)
records the hardcoded legacy remote module selection, the Darwin-only
exclusion, and the distinction between required, admitted, and executed
modules. [a13-test-execution-modes.md](a13-test-execution-modes.md) is the
interface record for this finding. The combined R1+R2 work follows #2837,
which remains pending at the pinned base; relocation and full local, github
and remote execution belong to A13.2. The test-mode requirement at #2793
comment 7210501920 may overlap OSR-27. That possible overlap is recorded here
without counting it twice; whether the rows are the same item stays unverified
until the row-level mapping exists.

## OSR-27 follow-up (dated 2026-11-28)

PR [#2860](https://github.com/llmtool/llmtool/pull/2860) was reviewed at HEAD `0e6926c0` and merged as
`50a7668a`; hosted run [46081471824](https://github.com/llmtool/llmtool/actions/runs/46081471824)
produced a same-head PASS covering 22 modules and 1,220 tests. This
follow-up is dated 2026-11-28, the verified date of the reviewed HEAD and
the merge commit. It is test evidence for the existing project recorded
after a PR already existed; it is not pre-PR hosted evidence, and it is not
the full three-mode implementation. Pre-PR hosted evidence and external
portable modes remain A13.2. The combined remote R1+R2 work still follows
#2837, which remains pending at the pinned base
`be913e781e8194fdd36e256d76e021f24a1454cb`. The original pin and the
counting recorded in this note are preserved unchanged.

## Unverified items

Where there is no direct mapping, the raw binding points, the recheck rows,
and the duplicate identities remain unverified. No ID, count, or mapping
beyond the supplied facts above is inferred.

## Gates

- Exact-head hosted docs check: this file is verified against the exact commit
  it accompanies, at pinned main
  `be913e781e8194fdd36e256d76e021f24a1454cb`.
- Formal review by codex-reviewer.

Out of scope: code, settings, CI and external repository changes. This note is
docs only; it adds no new requirement, rollout, code or runtime claim.