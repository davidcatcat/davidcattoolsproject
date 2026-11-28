# A13 OSR Row Index

Dated 2026-11-28. Docs only.

This index lists every OSR identifier that has a row in the five read
notes, each identifier once, with a relative link to every one of those
notes that carries it and the target milestone exactly as that note
states it. Where two notes differ on a milestone, both statements are
shown. This index asserts no duplicate relation between rows. It covers
only these five notes; the other crosswalk notes, the 43 binding points,
and the 40 recheck rows are not mapped here. Exit closure stays pending.

| OSR ID | Carried in (relative link) | Target milestone, as stated in that note |
|---|---|---|
| OSR-07 | [a13-osr-ci-remote-crosswalk.md](./a13-osr-ci-remote-crosswalk.md) | A13.1 (external repo CI trust) and A13.2 (full evidence validation) |
| OSR-09 | [a13-osr-crosswalk.md](./a13-osr-crosswalk.md) | A13.4/A13.5 (handoff retained) |
| OSR-10 | [a13-osr-crosswalk.md](./a13-osr-crosswalk.md) | A13.4/A13.5 (handoff retained) |
| OSR-11 | [a13-osr-config-crosswalk.md](./a13-osr-config-crosswalk.md) | A13.1 |
| OSR-12 | [a13-osr-config-crosswalk.md](./a13-osr-config-crosswalk.md) | A13.1 |
| OSR-17 | [a13-osr-crosswalk.md](./a13-osr-crosswalk.md) | A13.1 (project state); A13.3 (isolation) |
| OSR-18 | [a13-osr-agy-isolation-crosswalk.md](./a13-osr-agy-isolation-crosswalk.md) | Portable provider-host acceptance carried to A13.5 and A13.7 |
| OSR-19 | [a13-osr-config-crosswalk.md](./a13-osr-config-crosswalk.md) | A13.5/A13.7 handoff |
| OSR-20 | [a13-osr-config-crosswalk.md](./a13-osr-config-crosswalk.md) | A13.5/A13.7 handoff |
| OSR-22 | [a13-osr-personal-defaults-crosswalk.md](./a13-osr-personal-defaults-crosswalk.md) | A13.5 (provider account setup, runtime evidence); A13.7 (clean-install evidence check) |
| OSR-26 | [a13-osr-crosswalk.md](./a13-osr-crosswalk.md) | A13.1 (project registration verification) |
| OSR-27 | [a13-osr-ci-remote-crosswalk.md](./a13-osr-ci-remote-crosswalk.md) | A13.2 (relocation; full local, github and remote execution) |

## Notes

- Each identifier above is carried by exactly one of the five notes, so
  no differing milestone statement is shown; the rule above applies if
  that changes.
- OSR-18 carries two rows (issue #396 comment 6552370022 and issue #2793
  comment 7210501920) in the one note that carries it; both rows state
  the milestone shown, so the identifier is listed once.
- OSR-22's sub-rows (OSR-22-a, OSR-22-b, OSR-22-x, OSR-22-y) stay inside
  the personal-defaults note and are not indexed separately.
- This index adds no code references of its own; where the carrying notes
  name code, they name file and symbol only, with no line anchors. Each
  note keeps its own main pin.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
