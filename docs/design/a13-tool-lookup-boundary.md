# A13 Tool Lookup Boundary — fixed paths, guarded candidates, bounded PATH

Status: **Verified subset** of the historical mapping — not the full mapping.
Pinned main: `d4380aed191a9bc534cce3adbd610563b55a8680`.
Evidence: #396 comment 6552370022 (OSR-09) and #2793 Coordinator comment 7210501920. All facts below were verified by Advisor at pinned main; source identifiers name file and symbol only, no line anchors. Packaging context lives in `docs/design/a13-platform-assumptions.md`; this note does not repeat packaging facts.

Three lookup styles are separated so each keeps its own boundary. Existing security constraints are preserved: there is no proposal to broaden PATH, set tool paths, or change dispatch. No executable or platform tests are claimed to have been run.

| Style | Source identifier (file · symbol) | Verified behavior at pinned main | Portability consequence | Acceptance handoff |
|-------|-----------------------------------|----------------------------------|-------------------------|--------------------|
| Fixed executable path | `d6_worktree_lifecycle.py` · `run git` step | Invokes `/usr/bin/git` with a controlled PATH; stops with `D6_GIT_UNAVAILABLE` if the invocation is unavailable. | Git is bound to one installed location; a platform or layout that installs git elsewhere fails fast by design instead of resolving an unexpected binary. | A13.4 |
| Guarded candidate lookup | `d6_worktree_lifecycle.py` · `resolve lsof` step | Checks the fixed candidates `/usr/sbin/lsof` and `/usr/bin/lsof`, selecting a regular file owned by root or the current user with no group- or world-write bits. | The historical single-path `lsof` limitation is addressed by candidate lookup; if no qualifying tool is present, process inspection is left unknown rather than falling back to an unvetted binary. | A13.5 |
| Limited PATH resolution | `task_completion.py` · `github get` step; `review_dispatch.py` · `resolve gh` | Resolves `gh` via the bounded `_GH_PATH` directories, not an arbitrary user PATH. | This is the current lookup boundary; `gh` must be found within those searched directories. Support for `gh` installed outside that search is not asserted. | A13.4 |

Non-goals: this note records the verified boundary only. It proposes no change to PATH composition, tool paths, or dispatch behavior, and asserts no support for tools installed outside the bounded searches above.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
