# A13 OSR Release Metadata Crosswalk

Status: OSR-02 (license and notices) row of the release-evidence crosswalk, linked to its owning milestone.
Pinned main: `bd0aa273b02bbde011e2c98a6ada18165e45bcae`.
Evidence: #396 comment 6552370022; #2793 comment 7210501920.
Source identifiers name file and directory only, without line anchors. Docs only: no runtime, external-repository, or release action.
Related: [a13-osr-registration-packaging-crosswalk.md](a13-osr-registration-packaging-crosswalk.md) (same directory).

| ID | Evidence (file · path) | Consumer | Disposition | Milestone | Duplicate-of |
|----|--------------------------|----------|-------------|-----------|--------------|
| OSR-02 | Tracked root at the pin carries no `LICENSE`, `COPYING`, or `NOTICE` file, and `tools/llmtool` carries none. `pyproject.toml` · `[project]` metadata declares no license field at this snapshot; packaging metadata cross-references OSR-24 in [a13-osr-registration-packaging-crosswalk.md](a13-osr-registration-packaging-crosswalk.md). | Release and packaging consumers that require license and notice evidence accompanying distribution. | Partial: absence at the pin and undeclared metadata are recorded only. OSR-02 concerns distribution authorization and evidence, kept distinct from OSR-24 packaging metadata. Outstanding under A13.4: Owner choice and actual release artifact evidence. Not claimed: any license selection, legal rights, third-party claims, or that any artifact was built. | A13.4 | UNKNOWN — cited evidence carries no duplicate identity. |

Raw binding points and recheck IDs are recorded as unverified where the cited evidence lacks them.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
