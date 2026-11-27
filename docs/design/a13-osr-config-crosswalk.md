# A13 Configuration and Identity Subset of the OSR Crosswalk

Short crosswalk for the OSR rows with merged configuration/identity
boundary notes. Pinned main:
`0ac818305ec8076e7f3b53cd8a75dca8cd21c60b`.

This file is a configuration and identity subset of the OSR crosswalk. It
does not modify the earlier crosswalk now in review and stays separate
from the four-row OSR-09/10/17/26 package under review. It records no
aggregate counts and does not claim or imply a full 43/40 mapping or any
closure.

## Cited sources

- #396, comment 6552370022 — the binding-point inventory these
  configuration rows draw from.
- #2793, Coordinator comment 7210501920 — the recheck record these rows
  track against.

## Crosswalk

| Source ID | Raw binding-point identity | Linked evidence | Consumer | Disposition | Target milestone | Duplicate-of |
|---|---|---|---|---|---|---|
| OSR-11 | unverified | #2852, [`a13-d6-cli-refusal-boundary.md`](a13-d6-cli-refusal-boundary.md) — missing root reaches a fixed CLI refusal, scoped to the CLI | CLI (refusal path) | merged, verified at pinned main; A13.1 runtime verification pending | A13.1 | unverified |
| OSR-12 | unverified | #2853, [`a13-config-read-boundary.md`](a13-config-read-boundary.md) — trust and worker-policy read guarantees differ | trust and worker-policy configuration read paths | merged, verified at pinned main; A13.1 project binding pending | A13.1 | unverified |
| OSR-19 | unverified | #2854, [`a13-opencode-route-boundary.md`](a13-opencode-route-boundary.md) — direct model validation is not free-only; the Host path is separate | opencode route; Host path (separate) | merged, verified at pinned main; user-owned entitlement/runtime evidence still needed | A13.5/A13.7 handoff | unverified |
| OSR-20 | unverified | #2855, [`a13-claude-config-boundary.md`](a13-claude-config-boundary.md) — provider and PM use different configuration selection | provider and PM configuration selection | merged, verified at pinned main; user-owned configuration behavior unverified; follows A13.5/A13.7 handoff | A13.5/A13.7 handoff | unverified |

## Unverified marks

- Raw binding-point identity: no exact mapping from any of these OSR IDs
  to a raw binding point in the #396 inventory is established; every row
  above is marked unverified.
- Duplicate-of: no exact source mapping exists, so no row is collapsed
  into another.

The linked notes' tables are not restated here, and no account
identifiers are copied; each note is read through its relative link.

## Gates

- Exact-head hosted docs check: this file is verified against the exact
  commit it accompanies, at pinned main
  `0ac818305ec8076e7f3b53cd8a75dca8cd21c60b`.
- Formal review by codex-reviewer.

Out of scope: code, config, CI and external repository changes. This note
is docs only and records no A13 completion claim.
