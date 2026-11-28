# A13 OSR Row Index — Part 2 (seven crosswalk notes)

Date: 2026-11-28. Status: documentation only. Docs-only index; no code, CI,
runtime, or release changes.

This note lists every OSR identifier that has a row in the seven crosswalk
notes it links, each identifier once, with a relative link to every one of
those notes that carries it and the target milestone exactly as that note
states it, including any condition or outstanding milestone the note
states. Every identifier below is carried by exactly one of the seven
notes, so no identifier has two differing milestones; where two notes
differed, both milestones would be shown. No duplicate relation between rows
is asserted here; per-note duplicate identity stays as recorded in each
source note (unknown or unverified there). The 43 binding points and the 40
recheck rows are not mapped here. Exit closure stays pending.

The other five crosswalk notes are indexed by the existing OSR row index,
[a13-osr-index.md](a13-osr-index.md), in this same folder; this part does
not restate their rows.

| OSR ID | Carried by | Target milestone (exactly as stated) |
|---|---|---|
| OSR-01 | [a13-osr-policy-ownership-crosswalk.md](a13-osr-policy-ownership-crosswalk.md) | A13.0; outstanding under A13.1 and A13.4: fresh-install policy isolation and user-scoped rollout |
| OSR-02 | [a13-osr-release-metadata-crosswalk.md](a13-osr-release-metadata-crosswalk.md) | A13.4; outstanding: Owner choice and actual release artifact evidence |
| OSR-03 | [a13-osr-privacy-packaging-crosswalk.md](a13-osr-privacy-packaging-crosswalk.md) | A13.4 (stated as target phase in the source note); artifact and history privacy verification remain open under A13.4; not closed from the source note |
| OSR-04 | [a13-osr-trust-roots-crosswalk.md](a13-osr-trust-roots-crosswalk.md) | A13.1; partial resolution recorded; project registration acceptance recorded |
| OSR-05 | [a13-osr-trust-roots-crosswalk.md](a13-osr-trust-roots-crosswalk.md) | A13.1 (project-scoped validation carried); partial verification for the two recorded consumers only |
| OSR-06 | [a13-osr-registration-packaging-crosswalk.md](a13-osr-registration-packaging-crosswalk.md) | A13.1; partial — A13.1 needs project registration and trust proof |
| OSR-08 | [a13-osr-registration-packaging-crosswalk.md](a13-osr-registration-packaging-crosswalk.md) | A13.4; installed layout and artifact verification stay in A13.4 |
| OSR-13 | [a13-osr-runtime-crosswalk.md](a13-osr-runtime-crosswalk.md) | A13.5; remaining provider evidence stays open |
| OSR-14 | [a13-osr-runtime-crosswalk.md](a13-osr-runtime-crosswalk.md) | A13.5; fixed session comparisons remain, kept separate from formal review |
| OSR-15 | [a13-osr-runtime-crosswalk.md](a13-osr-runtime-crosswalk.md) | A13.4 / A13.5 / A13.7; clean-installed diagnostics and real user-owned runtime proof remain open |
| OSR-16 | [a13-osr-platform-crosswalk.md](a13-osr-platform-crosswalk.md) | A13.4 / A13.7; carried over — Windows remains deferred under the Roadmap |
| OSR-21 | [a13-osr-runtime-crosswalk.md](a13-osr-runtime-crosswalk.md) | A13.1 (policy), A13.5 (provider handoff); policy and provider handoff stay open |
| OSR-23 | [a13-osr-platform-crosswalk.md](a13-osr-platform-crosswalk.md) | A13.4 / A13.7; carried over — a configured ARM runner does not prove the siginfo path ran |
| OSR-24 | [a13-osr-registration-packaging-crosswalk.md](a13-osr-registration-packaging-crosswalk.md) | A13.4; A13.4 owns installer metadata plus update and removal evidence |
| OSR-25 | [a13-osr-platform-crosswalk.md](a13-osr-platform-crosswalk.md) | A13.4 / A13.7; carried over as packaging and scheduling facts — no runs are claimed |

## Scope notes

- Fifteen identifiers across the seven source notes; each identifier is
  listed once. Each source note carries its own evidence pin and gates; this
  index restates neither.
- Cross-references inside notes (for example packaging cross-links in the
  privacy and release-metadata notes) are not rows and are not indexed here.
- Limit: code references name file and function, without line anchors; this
  note carries no code references. Docs only: no runtime,
  external-repository, or release action.

## Gates

- Exact-head hosted docs check: must pass on the exact HEAD at review time.
- Formal review by codex-reviewer before acceptance.
