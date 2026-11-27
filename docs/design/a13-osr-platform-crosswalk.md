# A13 OSR Platform Crosswalk — platform slice (OSR-16, OSR-23, OSR-25)

Status: documentation only. No code, CI, runtime or release changes.

Pinned to main `be913e781e8194fdd36e256d76e021f24a1454cb` for this crosswalk.
The older evidence note snapshot, [a13-platform-coverage.md](a13-platform-coverage.md),
is kept as its own provenance (pinned to main
`a402c1483974ee21b055f3d6d56baac8f0487e6e` there) and supplies the A13
Platform Coverage evidence matrix from issue #2841.

## 1. Cited provenance (not re-verified by this file)

- Issue #396, comment `6552370022` — historical OSR-16, OSR-23, OSR-25 observations.
- Issue #2793, comment `7210501920` — cited as historical evidence; not re-verified by this file.

## 2. Crosswalk rows

Scope guard: this file records only OSR-16, OSR-23 and OSR-25. All other
crosswalk slices remain separate and are not merged here.

| Source ID | Linked evidence | Consumer | Disposition | Target milestone | Duplicate-of |
|---|---|---|---|---|---|
| OSR-16 | #396 comment `6552370022`; A13 Platform Coverage matrix (#2841) | A13.4 platform support boundary; A13.7 runtime acceptance | Carried over. Windows startup and platform support: Windows remains deferred under the Roadmap; the historical failure count is unverified. | A13.4 / A13.7 | Unknown (preserved) |
| OSR-23 | #396 comment `6552370022`; A13 Platform Coverage matrix (#2841) | A13.4 platform support boundary; A13.7 runtime acceptance | Carried over. Apple Silicon runtime siginfo path: a configured ARM runner does not prove that path ran. | A13.4 / A13.7 | Unknown (preserved) |
| OSR-25 | #396 comment `6552370022`; #2793 comment `7210501920`; A13 Platform Coverage matrix (#2841) | A13.4 clean install check (pending); A13.7 runtime validation (pending) | Carried over as packaging and scheduling facts: declared floor remains 3.9; normal Linux CI selects 3.14; optional Linux 3.9 is configured; Mac CI selects 3.13. No runs are claimed. | A13.4 / A13.7 | Unknown (preserved) |

## 3. Unresolved fields (preserved from the cited records)

- Raw binding point: unknown — preserved as unknown, not re-derived here.
- Recheck row: unknown — preserved as unknown; recheck remains pending.
- Duplicate identity: unknown — preserved as unknown; no duplicate is asserted.

## 4. Reading limits

- The cited comments and matrix are provenance only; nothing here re-verifies
  them.
- No full mapping is claimed: these three rows do not cover the whole OSR set.
- No CI runs, no runtime proof and no new platform support are claimed; the
  CI rows are scheduling configuration facts only.
- Any code reference names file and function, without line anchors.

## 5. Gates

- Exact-head hosted docs check at this PR result HEAD
  `b79502816424a36979c6dacf8f98e647a8f832f0`.
- The main pin `be913e781e8194fdd36e256d76e021f24a1454cb` is the crosswalk
  evidence pin, not the gate HEAD.
- Formal review by `codex-reviewer` before acceptance.
