# A13 OSR Privacy & Distribution Scope Crosswalk

Status: dedicated item-level crosswalk for the historical OSR-03 privacy and distribution scope finding; records the evidence boundary only.
Pinned main: `7acc2508146285380d0bee1626405c01aa166099`.
Evidence: #396 comment 6552370022 (OSR-03); #2793 comment 7210501920 (exit checklist).
The affected OSR-03 content is recorded as a boundary only; it is neither quoted nor read here.
Source identifiers name file and symbol only, without line anchors. Docs only: no sensitive values, file examples, packaging run, scans, edits, or external publication.
Related: [a13-osr-registration-packaging-crosswalk.md](a13-osr-registration-packaging-crosswalk.md) (same directory; OSR-08 and OSR-24 packaging notes).

| Source ID | Evidence (file · symbol) | Consumer | Disposition | Target phase | Duplicate identity |
|-----------|--------------------------|----------|-------------|--------------|--------------------|
| OSR-03 | At the pin, `pyproject.toml` · `[tool.setuptools.packages.find]` declares setuptools package discovery (`llmtool*`, non-namespace); `pyproject.toml` · `[tool.setuptools.package-data]` declares the `skills/*/SKILL.md` skill files; `pyproject.toml` · `[tool.setuptools.data-files]` declares `worker-dispatch.json`. | Privacy and distribution scope review; artifact and history privacy verification. | Configuration evidence only; not artifact or history verification. The affected OSR-03 content is not quoted or read here, so the finding stays boundary-only. Artifact and history privacy verification remain open under A13.4. Cross-links the OSR-08 and OSR-24 packaging notes in the registration crosswalk. | A13.4 | UNKNOWN. |

Exit-checklist boundary (per #2793 comment 7210501920): OSR-03 does not close from this document; its remaining artifact and history privacy verification belongs to the A13.4 phase.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
