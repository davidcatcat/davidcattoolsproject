# A13 OSR Registration & Packaging Crosswalk

Status: crosswalk of the three historical OSR findings that affect registration and clean installation, each linked to its next acceptance milestone.
Pinned main: `7acc2508146285380d0bee1626405c01aa166099`.
Evidence: #396 comment 6552370022; #2793 comment 7210501920.
Source identifiers name file and symbol only, without line anchors. Docs only: no runtime, external-repository, or release action.
Related: [a13-platform-assumptions.md](a13-platform-assumptions.md) (same directory).

| ID | Evidence (file · symbol) | Consumer | Disposition | Milestone | Duplicate-of |
|----|--------------------------|----------|-------------|-----------|--------------|
| OSR-06 | `project_context.py` · `ProjectContext.from_trust` accepts GitHub SSH and HTTPS origins when they exactly match the configured slug. `review_origins.py` · `origin_allowed` checks exact trust membership. | Registration and origin-gated review flows. | Partial: one trust repository and registered roots remain the baseline; A13.1 needs project registration and trust proof. Not claimed: every historical SSH-only consumer was revalidated. | A13.1 | Unverified — cited evidence carries no duplicate identity. |
| OSR-08 | `pyproject.toml` · `[tool.setuptools.data-files]` declares `worker-dispatch.json` as installed data. `developer_routes/__init__.py` · `load_route_config` resolves `developer-routes.json` from the source layout by default. | Installer data declaration; route/dispatch config loading. | Source- and metadata-level observation only; installed layout and artifact verification stay in A13.4. Not claimed: a wheel or npm installation was run. | A13.4 | Unverified — cited evidence carries no duplicate identity. |
| OSR-24 | `pyproject.toml` · `[project]` metadata has no `readme`, `license`, `urls`, or `authors` fields at this snapshot. | Installer/packaging metadata. | A13.4 owns installer metadata plus update and removal evidence. Cross-references OSR-08 for layout; metadata and installed runtime packaging are kept as different observations. | A13.4 | Unverified — evidence lacks a duplicate identity; OSR-08 is a separate, cross-referenced observation. |

Raw binding points, recheck IDs, and duplicate identity are recorded as unverified where the cited evidence lacks them.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
