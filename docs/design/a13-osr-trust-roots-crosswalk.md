# A13 OSR Trust Roots Crosswalk — D6 roots and protected evidence locations

Purpose: inventory of traceable rows for trusted D6 roots (OSR-04) and protected evidence locations (OSR-05), pinned to source at main.
Pinned main: `5f5249d9d634ad4b60f08dc2b853b1ce200d896b`.
Evidence: #396 comment 6552370022; #2793 comment 7210501920. Facts verified by Advisor at pinned main. Source identifiers name file and function only; no line anchors.

## Rows

| Source ID | Evidence (file · symbol, verified at pinned main) | Consumer | Disposition | Milestone | Duplicate-of |
|-----------|----------------------------------------------------|----------|-------------|-----------|--------------|
| OSR-04 (raw ID preserved as supplied) | `d6_worktree_lifecycle.py` · `create_worktree` derives its default task root from trusted configuration and checks canonical root and origin; `trust.py` · `load_trust` provides the normal trusted configuration boundary; the inspected migration still binds one repository and its configured roots | `d6_worktree_lifecycle.py` · `create_worktree`; boundary reader `trust.py` · `load_trust` | Partial resolution recorded: default task root comes from trusted configuration with canonical-root and origin checks at `create_worktree`. A13.1 project registration acceptance recorded. | A13.1 | Not yet established; raw ID preserved, second-pass recheck of rows and duplicate identity required |
| OSR-05 (raw ID preserved as supplied) | `review_bundle.py` · `protected_roots` uses configured canonical and workspace roots, and the bundle location check applies their exclusions; `safe-git/preflight.py` · `private_write` also excludes the configured protected roots | `review_bundle.py` · `protected_roots` (bundle location check consumer); `safe-git/preflight.py` · `private_write` (private write consumer) | Partial verification recorded for these two consumers only, each separately. Remaining historical consumers and any broader registry coverage left unverified. | A13.1 (project-scoped validation carried) | Not yet established; second-pass recheck required |

## In-scope notes

- [a13-config-read-boundary.md](a13-config-read-boundary.md) — OSR-12 config-read guarantees recorded per reader; the `load_trust` trust-authority read path cited above.
- [a13-platform-assumptions.md](a13-platform-assumptions.md) — A13.1 trust schema facts: trust is configured for exactly one repository with exactly one `canonical_root`, `workspace_root`, and `tasks_root`; explicit project registration is A13.1 work.

## Scope notes

- Root consumers in this note are distinct from OSR-12 config-read guarantees (recorded in the linked config-read-boundary note) and from OSR-06 repository identity.
- Unknown raw IDs are preserved as supplied; rows are recorded for recheck, and duplicate identity is a recorded follow-up, not resolved here.
- Limit: docs only. No live tests, no config values, no private paths, no policy changes, no root-bypass instructions, and no completion claim — all recorded states are partial.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
