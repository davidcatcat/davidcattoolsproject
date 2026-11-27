# A13 Config-Read Boundary — source-grounded inventory note

Purpose: prevent A13 from assuming every config reader performs the same filesystem checks. Each read path below is pinned to the source file and symbol where its checks were verified; absence of a check in one reader is not evidence that other readers lack it, and vice versa.
Pinned main: `4ffd749a95b313591c383b13e1148d80c9704cca`.
Evidence: #396 comment 6552370022 (OSR-12); #2793 Coordinator comment 7210501920. All facts verified by Advisor at pinned main; source identifiers name file and function only, no line anchors.

## Verified at pinned main

1. Trust authority path — `trust.py` · `load_trust`. `load_trust` resolves the normal home via the operating-system user record. Its read trust bytes step uses no-follow opens and requires a regular, current-user-owned, 0600, single-link file with bounded stable content. This is the trust authority path; it is not an assertion about ordinary preferences or about any other config reader.
2. Worker policy file — `worker_dispatch.py` · read policy file step. This step separately uses no-follow, regular-file, single-link, size, and identity checks. Do not infer that this helper enforces current-user ownership or 0600, and do not assume its call chain has been fully audited. File-read checks are not repository or worker authorization: they protect the read; granting repository and worker authorization is a separate decision and is not established by these checks.

## Inventory

| Source symbol | Verified behavior at pinned main | A13.1 handoff |
|---------------|----------------------------------|----------------|
| `trust.py` · `load_trust` (read trust bytes step) | Home resolved via the operating-system user record; no-follow opens; requires a regular, current-user-owned, 0600, single-link file with bounded stable content. Trust authority path only; no assertion about ordinary preferences. | Carry this boundary into the project-scoped trust design: scope and per-project trust are A13.1 work, with these verified read-path checks recorded as-is. |
| `worker_dispatch.py` · read policy file step | No-follow, regular-file, single-link, size, and identity checks. No verified ownership or 0600 enforcement recorded; call chain not fully audited. File-read checks do not constitute repository or worker authorization. | Carry this boundary into the project-scoped policy design: do not assume ownership or 0600 semantics, and keep file-read checks distinct from authorization in the A13.1 record. |

## Scope notes

- Trust schema validation content and packaging/install-layout notes are owned by `docs/design/a13-platform-assumptions.md`; they are not restated here.
- This note makes no new policy recommendation.
- Limit: docs only. No real config file reads, tests, code, credentials, or policy changes. No credential-shaped strings appear in this note.
- Gates: exact-head hosted docs check; formal review by codex-reviewer.
