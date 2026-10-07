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

## Coordinator F1 remediation — 2026-10-07

The preceding sections preserve the original implementation's historical results.
This section records the bounded remediation of the coordinator's sole blocker:
**`core.excludesFile` external-read surface**. The historical coordinator review
is unchanged; coordinator rereview remains pending.

### Task contract and tested revision

- Canonical Mac repository and branch: the same paths/branch recorded above.
- Synchronized PR head before remediation: `e6332d75ebf0e88bcec94c3ebd4bdc93e24efa8c`.
- Original production implementation baseline: `f69c67a6dc4409e6ca2145a07ca24f4649d79dcd`.
- Latest fetched base main: `870c32dccb6da7574f210fca889878f766f51844`.
- New tested implementation SHA: `a000e003298cd8ff9bd26f0d8a3892cd9882a11d`.
- Implementation commit: `fix(llmtool): reject external Git excludes file in ADD preflight`.
- Environment: actual Mac; Python 3.13.3 canonical venv and `/usr/bin/python3` 3.9.6.

All required targeted/full checks ran before the implementation commit, on the
`e6332d7` working tree plus the exact remediation source/test bytes below. Those
bytes were then committed unchanged as `a000e00`; the four excludes-focused tests
were rerun successfully at that exact committed HEAD. The full-suite results are
candidate-tree validation with identical committed artifact digests, not a claim
that the full-suite commands started at `a000e00`. The subsequent publication
commit changes this report only.

| Changed artifact | Remediation SHA-256 |
|---|---|
| `tools/llmtool/llmtool/git_mutation.py` | `2d520cee31d4b83c0ba41bed74fc8577c6937c9037726719379b656c592d4223` |
| `tools/llmtool/tests/test_git_mutation.py` | `f962ae4ad1553d0cd8b608ae409472f1c9c870fca5f434f6163c24f551245c9e` |

### Fix and reproduction

Local `core.excludesFile` is now **REJECTED** case-insensitively by `_check_config()`
immediately after the fixed `--no-includes` config query. The fixed error is
`Git ADD preflight rejected: unsafe config`; it omits the configured value.
The common `SAFE_CONFIG` also pins **`core.excludesFile=/dev/null`**, applying to
every hardened read and the unexecuted future ADD argv.

Before modifying production code, the four new rejection/pin tests failed against
the synchronized production baseline: the external-file and FIFO guards observed
a second query instead of immediate rejection, mixed-case keys were accepted,
and the null-file pin was absent. After adding an explicit pin-presence assertion,
the before-fix focused command (`-k excludes`) ran four test methods with 15
assertion/subtest failures, ERROR0, SKIP0, exit1. After the fix, the same command
passed 4/4, ERROR0/SKIP0, exit0, including the exact-commit retest.

Six permanent test methods were added:

- `test_external_excludes_file_rejected_before_other_queries`: only the config
  query is spawned; exact sanitized error, empty stdout/stderr, no configured path
  in exception repr or subprocess argv/kwargs, and no returned result.
- `test_excludes_file_key_is_case_insensitively_rejected`: lower/mixed/upper case.
- `test_excludes_fifo_never_followed`: actual FIFO outside the synthetic repo;
  deterministic query guard and one observed no-includes subprocess prove rejection
  before later queries, without relying on timing or waiting for a FIFO timeout.
- `test_excludes_null_pin_in_all_reads_and_future_plan`: each observed read argv
  and `GitAddPreflight.future_add_argv` contains the `-c`/null-pin pair.
- `test_info_exclude_ignored_untracked_target_rejected`: repository-local exclude
  still rejects an ignored untracked target as `file/path mismatch`.
- `test_tracked_target_info_exclude_pattern_remains_supported`: tracked target
  remains accepted despite an exclude pattern.

Existing `.gitignore` ignored-untracked and tracked-target tests remain unchanged
and pass. Repository `.gitignore` and `.git/info/exclude` support is preserved;
the fix does not globally disable repository ignore behavior.

### Actual validation

All commands launched from the canonical repo; test fixtures and logs used ordinary
OS runtime temporary directories, not `/private/tmp` project checkouts. No alternative
checkout/worktree was created. No test runs preflight against the user's actual index.

| Gate | Result |
|---|---|
| External file test | **PASS** |
| FIFO non-following test | **PASS** |
| Case-insensitive local key rejection | **PASS** |
| `.gitignore` regression, untracked and tracked | **PASS** |
| `.git/info/exclude` regression, untracked and tracked | **PASS** |
| Every read / future ADD config pin | **PASS** |
| Targeted `test_git_mutation.py` | 120 discovered: 118 PASS, 2 existing SKIP; FAIL0/ERROR0; 39.006s; exit0 |
| A5 `test_git_intel.py` | 39 discovered: 38 PASS, 1 existing SKIP; FAIL0/ERROR0; 13.279s; exit0 |
| A11b.1 `test_action_contract.py` | 21/21 PASS; FAIL0/ERROR0/SKIP0; exit0 |
| Full canonical Python 3.13.3 | 1,157 discovered: 1,154 PASS, 3 existing SKIP; FAIL0/ERROR0; 227.791s; exit0 |
| Full baseline Python 3.9.6 | 1,157 discovered: 1,154 PASS, 3 existing SKIP; FAIL0/ERROR0; 362.531s; exit0 |
| Exact `a000e00` excludes-focused retest | 4/4 PASS; FAIL0/ERROR0/SKIP0; 1.276s; exit0 |
| Canonical syntax/import checks | Included in both successful `make check` runs |
| Complete bounded diff review | Only the required production policy/pin and six new test methods; no unrelated hardening changes |
| `git diff --check` / staged diff check | **PASS** |

Commands are the targeted/A5/A11b.1 unittest discover commands above and the same
two full `make -C tools/llmtool check` commands in the original validation. The
three host skips are unchanged: case-colliding filename creation and non-UTF8
filename creation (including the existing A5 skip). No F1 test was skipped.

No new failures. The earlier CI runs recorded above remain historical evidence;
remediation publication-head CI is **PENDING / NOT CLAIMED** in this local report.
Local Python 3.14 remains unavailable and was not tested this round.

### Preserved boundaries and disposition

Filters and transforming attributes still reject. Includes/includeIf still reject
without following; core.worktree, extensions.worktreeConfig, index.lock and all
pre-staged states retain their existing fail-closed behavior. No arbitrary query
seam or raw Git stderr output was introduced. Existing security/race/import-boundary
tests pass. All nine original unrelated untracked paths remain untouched.

F1 remediation local validation: **PASS**.
Git add execution: **NOT IMPLEMENTED**.
Public mutation path: **NONE**.
A11b.2.2 started: **NO**; B5 remains deferred/blocking that later slice.
Action/approval schema, audit journal, runtime skills, A11c and A12: **UNCHANGED**.
Company calls: **0**. Codex benchmark calls: **0**. Live provider calls: **0**.

Ready for coordinator rereview: **YES**. No merge or new Claude review is started;
the coordinator's independent acceptance remains required.
