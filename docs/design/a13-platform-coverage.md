# A13 Platform Coverage — Evidence Matrix

Status: documentation only. Pinned to main
`a402c1483974ee21b055f3d6d56baac8f0487e6e`.

Historical evidence cited (provenance only, not re-verified by this package):

- Issue #396, comment `6552370022` — OSR-16, OSR-23, OSR-25.
- Issue #2793, comment `7210501920`.

## 1. Declared compatibility

| Item | Evidence | Class |
|---|---|---|
| Python floor `>=3.9` | `tools/llmtool/pyproject.toml` (`requires-python`), verified at pinned main | Declared only, not a tested matrix |

## 2. Configured CI (configuration facts, not runs)

| Job | Configuration | Scope |
|---|---|---|
| Linux workflow matrix | Selects Python 3.14 | Normal path |
| Linux workflow matrix | Optional `python39` input adds Python 3.9 | Not always enabled |
| macOS workflow | Python 3.13 on `macos-15` | Ordinary PR subset |
| macOS workflow | Python 3.13 on `macos-15-intel` | Intel-labeled PRs and main FULL |

Reading limits for this table:

- These rows are scheduling configuration, not recorded runs and not evidence
  of green jobs.
- They do not establish complete native support for any platform/Python
  combination.

## 3. Unverified runtime combinations

| Combination | Status |
|---|---|
| Windows | Remains deferred under the Roadmap. The historical count of modules failing imports is not asserted to still hold. |
| ARM siginfo path (OSR-23, issue #396 comment `6552370022`) | Unverified by this package; a configured runner alone cannot settle that specific path. |
| OSR-16 / OSR-25 (issue #396 comment `6552370022`) and #2793 comment `7210501920` | Cited as historical observations; not re-verified here. |

## 4. Remaining work toward the release platform matrix

To establish the release platform matrix, each of the following still needs a
clean install plus the named runtime checks in A13.4 and A13.7:

- macOS Intel
- macOS ARM
- Linux x86_64
- Linux ARM64

Scope guard: this document introduces no new platform, no Python floor, and no
support or release decision.
