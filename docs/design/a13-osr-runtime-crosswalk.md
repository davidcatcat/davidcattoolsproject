# A13 Item-Level OSR Runtime Crosswalk (4 Rows)

Item-level crosswalk rows for the OSR runtime subset: OSR-13, OSR-14,
OSR-15 and OSR-21 only. No other subset is touched. Pinned to main
`0ac818305ec8076e7f3b53cd8a75dca8cd21c60b`.

Sources: #396, comment 6552370022 (historical OSR inventory and aggregate
counts); #2793, Coordinator comment 7210501920 (recheck rows).

Evidence files (merged and advisor-verified), linked in the same directory:
- [./a13-role-session-assumptions.md](./a13-role-session-assumptions.md)
- [./a13-pm-runtime-binding-boundary.md](./a13-pm-runtime-binding-boundary.md)

| source_id | linked evidence | consumer | disposition | owning milestone | duplicate_of |
|---|---|---|---|---|---|
| OSR-13 | In file `role_config.py`, function `resolve_role` (`role_config.resolve_role`) is a configurable lookup, and file `review_dispatch.py` uses it ([./a13-role-session-assumptions.md](./a13-role-session-assumptions.md)) | File `review_dispatch.py` (uses `role_config.py`, function `resolve_role`) | Lookup side resolved at the pinned main; remaining provider evidence stays open | A13.5 | unverified (raw ID unavailable) |
| OSR-14 | In file `review_exchange.py`, function `_context` (the context step) still has fixed session comparisons, separate from formal review ([./a13-role-session-assumptions.md](./a13-role-session-assumptions.md)) | File `review_exchange.py`, function `_context` (context step) | Fixed session comparisons remain, kept separate from formal review | A13.5 | unverified (raw ID unavailable) |
| OSR-15 | No longer Claude-only: dedicated PM runtime binding — file `pm_runtime.py`, function `create_dedicated_pm` — can select Claude or Codex; source inspection asserts no live access ([./a13-pm-runtime-binding-boundary.md](./a13-pm-runtime-binding-boundary.md)) | PM runtime binding — file `pm_runtime.py`, function `create_dedicated_pm` (Claude or Codex) | Partially resolved: Claude-only status withdrawn; clean-installed diagnostics and real user-owned runtime proof remain open. Source inspection does not prove current runtime access or expand authority | A13.4 / A13.5 / A13.7 | unverified (raw ID unavailable) |
| OSR-21 | File `worker_dispatch.py`, symbol `WORKERS` (`worker_dispatch.WORKERS`) remains a fixed set ([./a13-role-session-assumptions.md](./a13-role-session-assumptions.md)) | File `worker_dispatch.py` (symbol `WORKERS`) | Fixed worker set remains; policy and provider handoff stay open | A13.1 (policy), A13.5 (provider handoff) | unverified (raw ID unavailable) |

## Scope notes

- These four rows only. This file does **not** announce the full historical
  mapping complete; rows outside this table remain unverified.
- `duplicate_of` and original raw IDs are recorded as unverified where
  unavailable. No duplicates are claimed and no ID or count is inferred.
- Every code reference names an actual `.py` file and its function or symbol,
  without line anchors.
- Limit: docs only. No policy, credentials, code or CI changes.

## Gates

- Exact-head hosted docs check: this file is verified against the exact HEAD
  commit that accompanies it (the check runs against the HEAD, not against the
  pinned base SHA). Content is pinned to main
  `0ac818305ec8076e7f3b53cd8a75dca8cd21c60b`.
- Formal review by codex-reviewer.
