# A13 Claude Configuration Boundary — source note

Status: **Source note** on the two existing Claude configuration boundaries, Advisor-verified at pinned main, with the user-owned account verification still needed.
Pinned main: `4ffd749a95b313591c383b13e1148d80c9704cca`.
Evidence: historical OSR-20 (#396, comment 6552370022); #2793 Coordinator comment 7210501920. All facts below were verified by Advisor at pinned main by code inspection; source identifiers name file and symbol only, no line anchors. See `docs/design/a13-platform-assumptions.md` for context; the supplied facts are sufficient. This note contains no setup instructions, environment values, or credential material.

| ID | Source identifier (file · symbol) | Verified fact at pinned main |
|----|-----------------------------------|------------------------------|
| A13.5-a | `claude_cli.py` · `ClaudeCLIProvider._child_environment` | The step builds an allowlisted environment for the child; it selects a user-home override or `HOME` and validates that the selected path is absolute and has no control characters below space. It does not copy `CLAUDE_CONFIG_DIR` from the caller. |
| A13.7-a | `pm_runtime.py` · `open` | `open` resolves the PM role and verifies account binding, then sets `CLAUDE_CONFIG_DIR` for the child when the resolved role supplies a configuration directory. |

## Comparison of the two boundaries

| Aspect | `claude_cli.py` · `ClaudeCLIProvider._child_environment` | `pm_runtime.py` · `open` |
|--------|-------------------------------|--------------------------|
| Configuration selection | Selects a user-home override or `HOME`, validates that the selected path is absolute and has no control characters below space, and does not inherit the caller's `CLAUDE_CONFIG_DIR`. | Sets `CLAUDE_CONFIG_DIR` for the child only when the resolved role supplies a configuration directory. |
| Boundary | The generic provider path: the configuration location derives from the allowlisted environment, not from caller-provided configuration variables. | The PM runtime path, kept distinct from the generic provider path: the configuration location derives from role resolution, after account binding is verified. |
| Evidence character | Code inspection at pinned main. | Code inspection at pinned main. |
| Limits | Does not prove authentication, live provider access, or shared account behavior. | Same limit: does not prove authentication, live provider access, or shared account behavior. |

Neither inspection proves authentication, live provider access, or shared account behavior.

## User-owned account verification still needed

- **A13.5** — Verify user-owned account behavior for the generic provider path in `claude_cli.py` · `ClaudeCLIProvider._child_environment`: that a user-owned account's configuration location is selected and validated as specified is still unverified at pinned main.
- **A13.7** — Verify user-owned account behavior for the PM runtime path in `pm_runtime.py` · `open`: that account binding for a user-owned account results in the role-supplied configuration directory reaching the child is still unverified at pinned main.

Scope: this note records inspection facts only. It does not prescribe new environment variables, does not broaden any environment allowlist, and does not repeat the PM runtime selection and state storage notes recorded elsewhere.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
