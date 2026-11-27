# A13.0 OSR Personal Defaults Crosswalk

Status: **Bounded crosswalk** — records verified source behavior and the remaining first-run portability evidence for OSR-22. It is not the full mapping; the separate model and session findings live in [a13-role-session-assumptions.md](./a13-role-session-assumptions.md) and are not restated here, including the model-selection rows.
Pinned main: `5f5249d9d634ad4b60f08dc2b853b1ce200d896b`.
Evidence: #396 comment 6552370022 and #2793 comment 7210501920.
All facts below were verified by Advisor at pinned main; source identifiers name file and symbol only, no line anchors.

Scope: A13.0 records OSR-22 (personal default routes and task-manifest discovery) and carries its open items to the owning phases. It proposes no new code, workflow, provider, identity, or release changes, makes no policy changes and no release claims, and no new registrations. Account values, user paths, logs, credentials, configuration values, and session identifiers are deliberately not copied into this document.

## OSR-22 crosswalk

| Field | Value |
|-------|-------|
| ID | OSR-22 |
| Evidence | #396 comment 6552370022; #2793 comment 7210501920 — verified subset at pinned main, table below |
| Consumer | `high_debug.py` (its route, via `role_config.py` · `resolve_role`); `review_dispatch.py` (its route, via `role_config.py` · `resolve_role`); `llmtool_monitor.py` · `scan_tasks` (normal caller) |
| Disposition | Verified subset recorded at the pin; first-run portability open — no claim that the launchers work on a clean machine; clean-machine runtime behavior remains UNKNOWN |
| Milestone | A13.5 (provider account setup, runtime evidence); A13.7 (clean-install evidence check) |
| duplicate-of | UNKNOWN — historical duplicate identity not verified at the pin |

## Verified subset at pinned main

| ID | Source identifier (file · symbol) | Verified fact at pinned main |
|----|-----------------------------------|------------------------------|
| OSR-22-a | `high_debug.py` · route resolution (via `role_config.py` · `resolve_role`); `review_dispatch.py` · route resolution (via `role_config.py` · `resolve_role`) | `high_debug.py` and `review_dispatch.py` both call `resolve_role` in `role_config.py` for the respective routes. `resolve_role` loads account data from user configuration, resolves configured account aliases to provider and config directory, and supports one-shot CLI/environment precedence. |
| OSR-22-b | `llmtool_monitor.py` · `scan_tasks` | `scan_tasks` accepts a pattern and requires a regular manifest in matched directories. Recent task evidence is a separate condition: the recency check compares the cutoff against the newest timestamp among the manifest, stage, and result files, so an old manifest with a recent stage or result is accepted. The normal caller leaves the pattern at `TASK_GLOB`, a macOS temporary-directory default. |

## Remaining first-run portability (open)

| ID | Open item | Owning phase |
|----|-----------|--------------|
| OSR-22-x | Provider account setup and runtime evidence for the `high_debug.py` / `review_dispatch.py` routes are carried over; whether the launchers work on a clean machine is UNKNOWN at the pin and is not claimed. | A13.5 |
| OSR-22-y | Discovery portability of `scan_tasks` / `TASK_GLOB` (macOS temporary-directory default) is open; the clean-install evidence check is retained. No new default is selected and the OSR-17 storage location is not restated here. | A13.7 |

Test data rule (Owner #763 A): ordinary tests never contain password-, API-key-, token- or other credential-shaped strings; refusal-flow tests mock, monkeypatch, or inject the screen function and still check real behaviour. Only sensitive-detector tests may use the #981 synthetic-data exception (labelled synthetic, independently reviewed, never real credentials). No production check is disabled, bypassed, or weakened for test convenience. This note is docs-only and changes no tests.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
