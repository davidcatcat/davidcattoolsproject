# A13 Platform Assumptions — current-main inventory

Status: **Verified subset** of the historical 43-point mapping — not the full mapping.
Pinned main: `a402c1483974ee21b055f3d6d56baac8f0487e6e`.
Evidence: #2793 and Coordinator comment 7210501920. All facts below were verified by Advisor at pinned main; source identifiers name file and symbol only, no line anchors. Scope excludes profile wiring, rollout, and test-mode material.

| ID | Source identifier (file · symbol) | Verified fact at pinned main | Current assumption | Implication | Owning milestone |
|----|-----------------------------------|------------------------------|--------------------|-------------|------------------|
| A13.1-a | `trust.py` · schema validation | Trust schema validation requires a single repository slug with exact allowed origins, and exactly one `canonical_root`, `workspace_root`, and `tasks_root` set. | Trust is configured for exactly one repository; the schema carries no per-project trust distinction. | Multi-project flows cannot assume trust scoping today; explicit registration and project-scoped trust remain tracked as A13.1 hand-off work. No new trust architecture is proposed here. | A13.1 |
| A13.1-b | `task_store.py` · `TaskStore` constructor | The constructor uses `default_root` when no explicit root is passed; the explicit root parameter is not a project registry and is not a multi-project isolation guarantee. | Task state is rooted per store construction, not per project. | Project-scoped state design is recorded under A13.1; concurrent isolation acceptance is recorded under A13.3. No new state architecture is proposed here. | A13.1 (state design); A13.3 (concurrent isolation acceptance) |
| A13.4-a | `tools/llmtool/pyproject.toml` · `[tool.setuptools.data-files]`; `developer_routes` package · `load_route_config` | `worker-dispatch.json` is listed as an installed data file; `load_route_config` defaults to a `developer-routes.json` path resolved from the source layout when no path is passed. | Runtime route/dispatch config resolves against the source/install layout rather than a project-relative location. | Config-path assumption recorded for A13.4 packaging/portability work. This is an assumption record for the cited files and symbols, not a complete artifact audit. | A13.4 |

Gates: exact-head hosted docs check; formal review by codex-reviewer.
