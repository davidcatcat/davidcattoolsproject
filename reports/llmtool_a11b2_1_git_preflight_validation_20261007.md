# llmtool A11b.2.1 — deterministic Git ADD preflight validation (2026-10-07)

## Task contract and revision

- Canonical Mac repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Remote: `davidcatcat/davidcattoolsproject`.
- Base main: `870c32dccb6da7574f210fca889878f766f51844`, fetched before branch creation.
- Branch: `feat/llmtool-a11b2-1-git-preflight-20261007`.
- Implementation / exact tested HEAD: `f69c67a6dc4409e6ca2145a07ca24f4649d79dcd`.
- Implementation commits: `6f78c5fe36cf14ff4a67a3edcb2124ff4f049dfa` and `f69c67a6dc4409e6ca2145a07ca24f4649d79dcd`.
- Authoritative requirements: implementation spec §20 and targeted spec re-review; older filter-neutralization / conditional staged-target language is superseded.

The authorized slice is an internal, read-only verifier of A11b.1 typed `GIT_OPERATION / ADD` proposals. It returns immutable evidence and a fixed future ADD plan. It neither executes that plan nor grants approval. Acceptance requires deterministic repository/HEAD/index/file binding, helper suppression, unchanged index bytes, no ordinary CLI/import path, A5/A11b.1 regressions, and canonical checks. Independent code/security review remains a separate required gate before any later slice.

## Changed artifacts

| Kind | File | SHA-256 |
|---|---|---|
| Production | `tools/llmtool/llmtool/git_mutation.py` | `ec13807cdb12133e8d840ff6f1b2a6a2c40ee7ebce3bd1ccbf4b1cfab79ed5f1` |
| Tests | `tools/llmtool/tests/test_git_mutation.py` | `2d581e67778c45c2bd29024f34f8050d397d9b02756def5733246a09ea8fb7d0` |
| Validation | This report | Report-only publication after implementation checks |

No existing production/test file, runtime skill, action schema, CLI command, benchmark fixture/gold, or historical report changed.

## Preflight and result contract

`preflight_git_add(action)` accepts only an exact `Action`, revalidates detached canonical contract data, and requires the existing `GitOperation.ADD`. It does not accept/read `ApprovalValidation`, interpret USER source as authentication, or test authorization. Parsed proposals and successful preflight are not execution authority.

`GitAddPreflight`, `IndexEntry`, and `BoundBlob` are frozen dataclasses containing tuples, bytes and primitive values. Result evidence contains action digest, canonical root/device/inode, exact HEAD, object format, raw pre-index SHA, literal target paths, `Action.expected_state.digest`, full semantic index baseline, exact bound blob OIDs/modes, fixed future argv and detached future environment. No post-index/exit/post-verification result is fabricated: those belong to the unimplemented mutation slice and its later audit handoff.

ADD execution implemented: **NO**. Public mutation path: **NONE**.

### Repository safe subset

- Non-root POSIX execution, real non-symlink root and real `.git` directory; fd-based root identity and current-euid ownership checks.
- Root path components use no-follow directory opens. Root and target parent directories are not group/world writable. `.git` metadata is conservatively required to be owned, non-writable by other users, no-follow and single-linked for regular files.
- Ordinary single working tree; reject gitfiles, `.git` symlinks, registered linked worktrees, `commondir`, alternates, `core.worktree`, bare config, includes/includeIf, worktreeConfig, partial/promisor config and promisor pack markers.
- Attached, born, exact HEAD; reject merge/rebase/cherry-pick/revert/bisect/sequencer state and index conflicts.
- Metadata/index/attribute/file/HEAD/branch bindings are rechecked before returning. Namespace/root/config/index/content replacements are rejected. No repair, lock removal, chmod or cleanup of user data occurs.

Bounds: each Git stdout ≤1 MiB, stderr ≤64 KiB (never rendered), wall timeout 10 seconds per query; raw index ≤2 MiB and ≤20,000 entries; `.git` traversal ≤20,000 entries with bounded depth. Action file count/path/content bounds remain A11b.1's existing contract. Unsupported metadata layouts/features fail closed.

### File identity and index policy

Targets are walked from the root fd with no-follow parent opens, exact directory-entry filename bytes and nested-`.git` rejection. Target fd reads require a regular file, nlink=1, current ownership, non-group/world writable mode and same filesystem. Device/inode/mode/uid/gid/size/mtime/content SHA must match the Action. Before/after fd and named identity checks detect replacement while hashing. Symlinks, hardlinks, FIFO/socket/device/directory targets and case/normalization aliases are rejected.

Only `.git/index` is supported. Missing index, existing `index.lock`, symlink/hardlink index, split/shared/sparse index and special entry flags fail closed. `index.lock` is preserved. `hash_git_index(RepositoryBinding)` provides the one raw SHA-256 algorithm for this verifier and future Action builders; no arbitrary index path is accepted. Current SHA must equal both payload and expected-state SHA.

The checksum-verified native v2/v3/v4 index parser retains exact path bytes, mode, object OID, stage, skip-worktree, intent-to-add and assume-unchanged. Unknown mandatory extensions and `link`/`sdir` are rejected. Full baseline entries must equal the exact HEAD tree, so **any staged change**, including an already-staged target, is rejected. Index stat/cache information is not part of semantic identity.

Authorized target changes, mode-only changes, and untracked nonignored targets can pass. Unrelated unstaged/untracked work, including unrelated unstaged deletion, is preserved. Target deletion cannot pass the existing-file fd binding. Staged rename/copy/deletion fails full index/HEAD equality; no rename heuristic is used. `status.renames=false` remains pinned.

Git blob OIDs are computed from exact bound bytes with `blob <length>\0` using Git-reported SHA-1/SHA-256. Expected Git file mode uses the **owner** execute bit, matching native Git; group/other execute bits alone do not produce `100755`.

### Filters, attributes and hardened queries

Filter policy: **REJECT**, including clean/smudge/process/required configuration and driverless `filter=lfs` attributes. Transforming attribute policy: **REJECT**; only unspecified/unset filter/text/eol/crlf/ident/working-tree-encoding values pass. Root/parent `.gitattributes` and `.git/info/attributes` remain subject to validation; `/dev/null` global attributes does not hide them.

The fixed read-query set is config (`--file …/.git/config --no-includes`), object-format/HEAD resolution, symbolic HEAD, recursive HEAD tree, check-attr and ignored-untracked ls-files. It does not run status/diff, content conversion, mutation, hooks or a user-specified subcommand.

Every subprocess uses `shell=False`, generated absolute GIT_DIR/GIT_WORK_TREE, literal paths, fresh allowlisted environment, disabled system/global config, lazy fetch/prompts, hooks/fsmonitor/untracked cache, pager/color, protocol helpers and maintenance/gc. Future ADD argv retains `--` plus only authorized paths and the reviewed pins, including autocrlf/safecrlf/add.ignoreErrors and credential/SSH/GPG defense-in-depth. Read queries use GIT_OPTIONAL_LOCKS=0; the future mutation environment omits that override. Raw Git output/config/content/environment never enters errors.

### Additional deterministic regressions found during development

1. A same-sized modified target plus a clean filter introduced after the initial attribute gate caused the early status-based prototype to execute a synthetic helper. `test_concurrently_introduced_filter_never_executed` **failed before the fix**, then passed. Removing the unnecessary status query leaves the complete native index/HEAD staging proof and prevents that conversion path. This is not a claim of isolation against malicious same-UID concurrency.
2. The early blob-mode predictor treated group/other execute as executable. Native Git staged mode `100644` for bound mode `0645`; `test_git_mode_uses_owner_execute_not_group_or_other_execute` **failed before the fix**, then passed with owner-execute semantics. Native staging is only test-fixture setup/reference behavior; the production verifier never spawns ADD.

## Normative review points

| Point | Disposition / evidence |
|---|---|
| N1 already-staged target | **UNCONDITIONAL REJECT**, staged and staged-plus-unstaged tests |
| N2 include/includeIf | **NO-INCLUDES DETECTION / REJECT**; include pointing to a FIFO is never followed |
| extensions.worktreeConfig | **REJECT** |
| N3 gitfile | Dedicated rejection test **PASS** |
| N3 commondir | Dedicated rejection test **PASS** |
| N3 alternates | Dedicated rejection test **PASS** |
| Public import/CLI boundary | AST scan of all other production modules; existing CLI unchanged; git-add usage rejected rc2 |
| Hooks/helpers | post-index-change, fsmonitor, external diff/textconv, credential/SSH/GPG probes produce no marker |
| Read-only evidence | Observed spawned argv contain no ADD; index bytes and unrelated contents remain unchanged |
| Semantic / object format | Native Git comparison, v4, extended flags, exact non-UTF8 parser bytes, SHA-1/SHA-256 fixtures |
| Drift | Root/.git/index/config/file replacement and during-read content changes fail closed |
| Error safety | Fixed categories, bounded capture, hostile config/filenames/stderr omitted |

## Actual local validation

All commands launched from the canonical repo. Disposable repositories are newly initialized synthetic test data, never clones/worktrees/copies of this project. Only test-fixture indexes are changed to construct adversarial states; ordinary authorized source staging/commits are separate publication operations. No test invokes preflight against the user's repository index.

Environment: actual Mac, Apple Git 2.39.5; canonical venv Python 3.13.3; `/usr/bin/python3` Python 3.9.6. Required filesystem/socket/fake-provider tests run outside the restricted sandbox; an earlier restricted attempt produced setup PermissionErrors and was rerun without weakening assertions.

| Gate | Command / result |
|---|---|
| Targeted preflight | `tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_git_mutation.py`: 114 discovered, 112 PASS, 2 SKIP; exit0 |
| A5 regression | Existing `test_git_intel.py`: 39 discovered, 38 PASS, 1 SKIP; exit0 |
| A11b.1 regression | Existing `test_action_contract.py`: 21/21 PASS; exit0 |
| Full Python 3.13 | Exact implementation SHA above: 1,151 discovered, 1,148 PASS, 3 SKIP, FAIL0/ERROR0; 225.430s; exit0 |
| Full Python 3.9 | Exact implementation SHA above: 1,151 discovered, 1,148 PASS, 3 SKIP, FAIL0/ERROR0; 358.726s; exit0 |
| Packaging/import baseline | Existing canonical editable package points to this checkout; new module resolves here; pyproject automatic discovery/entrypoint unchanged; Python 3.9 import PASS |
| Installed CLI smoke | `--help` rc0, `--version` rc0 (`0.1.0`), absent git-add command rc2 without traceback |
| git diff --check | PASS |

Host skips are explicit: this Mac filesystem cannot create two distinct case-colliding names, and rejects non-UTF8 entry names (also one existing A5 skip). Real case-alias rejection and decomposed-name rejection pass on this Mac; native parser exact-byte coverage still runs. Linux-host behavior remains subject to final-head CI. Python 3.14 is not installed locally. No wheel rebuild was performed locally (canonical venv lacks the build backend); editable discovery/import was checked and remote CI performs a fresh package installation.

Local full checks at the final implementation SHA use:

```sh
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

Remote exact-publication-head Python 3.9/3.14, Offline canonical checks and Working tree clean are separate CI evidence published with the Draft PR; local Mac results do not substitute for them.

## Limits and remaining gates

- A11b.2.2 started: **NO**. No ADD primitive/executor, public invocation, approval issuance, durable mutation audit, rollback/repair/retry/fallback exists.
- B5 mutation containment remains **DEFERRED / BLOCKING A11b.2.2**, exactly as the targeted spec re-review states: host TERM/HUP/crash and normal leader-exit descendants need their own reviewed mutation strategy. This slice retains A5-style read-query timeout/interrupt cleanup; its green tests do not close B5.
- This is a bounded snapshot under trusted Git/PATH and the reviewed non-malicious-same-UID concurrency assumption, not an OS sandbox or guarantee against arbitrary swaps after return. A future caller must revalidate bindings immediately before mutation and verify the exact semantic post-state.
- Actual cross-UID adversaries, privileged device creation/root execution, native Windows/non-POSIX, NFS/foreign filesystems and live process-crash mutation containment are **NOT TESTED**; ownership/device/root checks use safe deterministic mocks where necessary.
- Entire `.git` metadata traversal has deliberately conservative owner/link/mode/count/depth limits. Unknown extensions and unsupported repository layouts are rejected rather than repaired.
- Initial nine unrelated untracked paths are preserved; no benchmark/reasoning artifacts were inspected, removed or staged. Tracked implementation tree is clean after its commits.
- Round-3 reports/results, runtime skills, action contracts, A5 implementation, production providers and existing CLI bytes remain unchanged.

Company calls: **0**. Codex benchmark calls: **0**. Real provider/model calls: **0**. No external network is needed for implementation/tests; existing full-suite loopback HTTP/fake-CLI fixtures are offline test doubles.

Ready for independent code/security review: **YES**. Independent review, merge authorization and A11b.2.2 are not supplied by this report.
