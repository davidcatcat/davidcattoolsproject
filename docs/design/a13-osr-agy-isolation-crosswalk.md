# A13 OSR-AGY Isolation Crosswalk

Status: documentation only. Pinned to main
`5f5249d9d634ad4b60f08dc2b853b1ce200d896b`.
Companion: [A13 Platform Coverage](./a13-platform-coverage.md) (merged; that
note records its own main pin).

## 1. Which host can run the AGY Developer write-boundary check

Advisor source snapshot at pinned main:

- `developer_routes/write_boundary.py`, `for_platform`: selects the
  sandbox-exec boundary on macOS; `UnsupportedBoundary` elsewhere.
- AGY direct dispatch requires verified write isolation; the shared route
  refuses unverified evidence before provider launch as
  `BLOCKED_WRITE_ISOLATION`.
- `test_developer_write_boundary.py` checks the Linux unsupported
  classification.

Conclusion for the pinned snapshot: only macOS can run the
write-boundary check (the sandbox-exec path). This is the current
Mac-only isolation implementation. Linux is asserted only as the
`UnsupportedBoundary` classification (unsupported), not as a host that
can run the check.

## 2. Crosswalk

| Source ID | Evidence | Consumer | Disposition | Milestone | Duplicate-of |
|---|---|---|---|---|---|
| OSR-18 — issue #396, comment `6552370022` | Historical observation cited in that comment; advisor snapshot: `for_platform` in `developer_routes/write_boundary.py` selects the sandbox-exec boundary on macOS and `UnsupportedBoundary` elsewhere | AGY Developer write-boundary check; shared route refusal policy | Mac-only isolation implementation preserved as-is; limits retained, no live-provider acceptance inferred | Portable provider-host acceptance carried to A13.5 and A13.7 | #2793 comment `7210501920` (identity unverified; raw inventory absent) |
| OSR-18 — issue #2793, comment `7210501920` (exit review) | Historical observation from the #2793 exit review | Exit review of #2793 | Cited as provenance; not re-verified by this package | Portable provider-host acceptance carried to A13.5 and A13.7 | #396 comment `6552370022` (identity unverified; raw inventory absent) |

## 3. What remains to verify for portable live use

- Portable provider-host acceptance is carried to A13.5 and A13.7; it is
  not established by this package and no live-provider acceptance is
  inferred from the snapshot.
- The [platform coverage note](./a13-platform-coverage.md) lists the
  clean-install targets (macOS Intel, macOS ARM, Linux x86_64, Linux
  ARM64). Linux hosted CI checks are separate from support for an AGY
  Developer running on Linux.
- Until portable acceptance is verified, the `BLOCKED_WRITE_ISOLATION`
  refusal path (shared route, before provider launch) remains the
  operative limit.
- Raw inventory and duplicate identity are preserved as unverified where
  absent from the cited comments.

## 4. Scope guard

- Docs only. No sandbox profile details, no workarounds, no new
  technology selection, no provider calls, and no changes to isolation,
  policy, CI, or release authority.
- Source references name file and symbol only; no line anchors.
- Gates: exact-head hosted docs check; formal review by codex-reviewer.
