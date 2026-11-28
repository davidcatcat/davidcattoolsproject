# A13 OSR Policy Ownership Crosswalk

Status: crosswalk of the remaining A13.0 OSR finding — policy ownership across the installed layout and project registration.
Pinned source: `c5b8a5cb1308d4f5369b4457fb5478f0571f4a4c`.
Evidence: #396 comment 6552370022; #2793 comment 7210501920. All facts verified at the pinned source and recorded as source evidence only.
Source identifiers name file and symbol only, without line anchors. Docs only: no runtime, external-repository, or release action.
Related: [a13-osr-registration-packaging-crosswalk.md](a13-osr-registration-packaging-crosswalk.md) (OSR-08 packaging record); [a13-config-read-boundary.md](a13-config-read-boundary.md) (OSR-12 config-read record).

| ID | Evidence (file · symbol) | Consumer | Disposition | Milestone | Duplicate-of |
|----|--------------------------|----------|-------------|-----------|--------------|
| OSR-01 | The wheel metadata still declares a dispatch policy data file (`pyproject.toml` · `[tool.setuptools.data-files]`; packaging record in the OSR-08 note). The worker policy reader establishes source and installed data candidates (`worker_dispatch.py` · `_policy_candidates`) and uses trusted repository information for decision evidence (`worker_dispatch.py` · `evaluate` and `_build_reference_patterns`); the reader's file-read checks (`worker_dispatch.py` · `_read_policy_file`) are recorded in the OSR-12 note and remain distinct from the authorization decision. | Worker dispatch authorization; policy loading across source and installed layouts, and project-registration consumers. | Source evidence only: the declaration and the reader behavior are recorded at the pinned source. This row owns the owner and scope of dispatch authorization only; file-read checks remain distinct from the authorization decision. Not claimed: live activation, or that any installed artifact or layout was checked. Fresh-install policy isolation and user-scoped rollout are outstanding under A13.1 and A13.4. | A13.0 | UNKNOWN — historical duplicate identity not established by the cited evidence. |

## Owner and scope of dispatch authorization

Recorded at the pinned source (source evidence only):

1. Owner record — the wheel metadata still declares a dispatch policy data file (`pyproject.toml` · `[tool.setuptools.data-files]`). Packaging-level detail stays in the [OSR-08 note](a13-osr-registration-packaging-crosswalk.md) and is not restated here.
2. Scope record — the worker policy reader establishes source and installed data candidates (`worker_dispatch.py` · `_policy_candidates`) and uses trusted repository information for decision evidence (`worker_dispatch.py` · `evaluate` and `_build_reference_patterns`). The verified file-read checks of `worker_dispatch.py` · `_read_policy_file` and their limits stay in the [OSR-12 note](a13-config-read-boundary.md) and are not restated here.

Not established here: live activation of the declared policy file; authorization authority across the installed layout; project-registration scoping of the decision. Fresh-install policy isolation and user-scoped rollout are recorded as outstanding under A13.1 and A13.4.

## Scope notes

- Focus of this row: the owner and scope of dispatch authorization only; neither the OSR-08 packaging note nor the OSR-12 config-read note is duplicated.
- No live policy values, accounts, live paths, configuration, or steps to change authority appear in this note. No credential-shaped strings appear in this note.
- Limit: docs only. No real reads, tests, code, credentials, or policy changes.
- Gates: exact-head hosted docs check; formal review by codex-reviewer.
