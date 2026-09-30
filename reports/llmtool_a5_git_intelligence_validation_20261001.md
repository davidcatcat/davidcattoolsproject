# llmtool A5 — Git Intelligence validation (2026-10-01)

## Scope and revision

Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/a5-git-intelligence`, created directly from fetched `origin/main`
`38b1a67b7975e4ff78afada5187ca6d396ad8723` (A4 merge). No project worktree,
clone, alternate checkout, or project copy was created. Temporary Git repositories
contain only fixture data, as explicitly required by A5 tests/manual validation.

A5 implementation, validation, and external ChatGPT review are complete with one explicit host-filesystem coverage limitation below. A5 is not merged.
Development Git writes prepare/commit/push this feature only. The runtime runner
cannot mutate Git or contact providers. No A6/A9/A10 implementation was started.
Provider != Role != Agent != Skill != Context Packet != Git Intelligence.

## Architecture and output

`llmtool/git_intel.py` contains frozen `GitRepositoryInfo`, `GitChange`, `GitStatus`,
`GitDiff`, `GitCommit`, and `GitSnapshot` models. Container inputs become detached
tuples. `collect_git_snapshot(cwd)` resolves the root using Git and returns explicit
observations; `to_dict()`, `to_json()`, and `format_git_snapshot()` are reusable.
CLI commands `llmtool git` and `llmtool git --json` bypass llmtool configuration,
credential lookup, and provider creation. A3.5 chat, ask, and A4 context-pack remain
unchanged; no automatic ContextPacket conversion or model submission is added.

Porcelain v1 NUL status preserves filenames and rename/copy original paths.
Staged, unstaged, untracked, and conflicted categories sort/deduplicate paths;
changed_files unions all categories, and a file can be staged and unstaged.
Rename destinations populate lists; original paths remain in entries. Unborn HEAD
is null; detached HEAD has null branch. Diffs preserve Git-returned text in API/JSON;
human metadata is quoted and nonprintable diff characters escaped. UTF-8
surrogateescape supports unusual filename bytes, subject to host filesystem support.

Diffs disable external drivers/textconv/color, ignore submodules, and use fixed
prefix/context/algorithm options. Each diff is limited to 2 MiB; excess fails the
entire snapshot before output. There is no partial successful output; successful
GitDiff has truncated=false and byte_count. Metadata stdout is capped at 1 MiB,
stderr is discarded and capped at 64 KiB, with 10 seconds per command.
Log is bounded to 10 SHA/subject records. Related paths are tracked HEAD blob
siblings in at most 10 sorted changed parent directories, excluding changed paths,
sorted and capped at 20. New directories are skipped safely. No recursive content
retrieval, embedding, AST, inference, or hypothesis generation is performed.

## Runtime allowlist and security review

Only fixed argv operations are accepted: rev-parse root/verified HEAD; branch
--show-current; porcelain v1 -z status; diff and cached diff with fixed safe options;
10-entry NUL-delimited log; two fixed config key-name queries (filter execution
keys and partial/promisor settings); ls-tree HEAD and validated literal HEAD
subdirectory forms. Arbitrary options and all mutation/network subcommands,
including fetch and branch creation, are rejected before subprocess creation.

Subprocesses use argv lists, shell=False, explicit cwd, bounded pipe draining,
process cleanup, and fresh environment. No os.system or model-driven execution.
Optional index writes, fsmonitor, hooks, clean/smudge/process filters, required
filters, pager, automatic maintenance, global/system config and inherited Git
configuration/credential overrides are disabled. External diff and textconv are
explicitly disabled. Protocol access is denied; partial/promisor repos are rejected
rather than lazily fetching missing objects. Submodules are not traversed.

Security sources reviewed: [Git diff](https://git-scm.com/docs/git-diff),
[Git status porcelain](https://git-scm.com/docs/git-status),
[Git filter configuration](https://git-scm.com/docs/git-config), and
[Git environment](https://git-scm.com/docs/git).

Source review and tests cover mutation rejection, shell=False, hostile filenames/
diffs as data, helper/filter/fsmonitor/hook suppression, ambient Git overrides,
no-provider/no-credential dispatch, sanitized errors, output/time bounds, and exact
Git administrative-file bytes/mtime preservation during inspection. No dangerous
mutation command string, os.system, or shell=True was found in production A5 code.
The local Git executable/PATH and stable repository config/includes are trust
assumptions; this is not an OS sandbox. Git itself reads tracked files for diffs.

## Validation

All validation was launched from the canonical repository. Python 3.13 virtualenv;
Git 2.39.5 (Apple Git-154). No network/model backend is needed by the A5 tests.

| Check | Exact result | Exit | Elapsed |
|---|---|---:|---:|
| Targeted A5 | 39 discovered: 38 passed, 1 skipped, 0 failures/errors | 0 | 12.954 s |
| Final canonical syntax + suite | 305 discovered: 304 passed, 1 skipped, 0 failures/errors; syntax passed | 0 | 39.098 s |
| git diff --check | clean | 0 | not recorded |
| Wheel build + contents/3.9 syntax validation | PASS | 0 | not recorded |
| Manual disposable fixture, human + JSON | PASS | 0 | 1.092 s |

Commands:

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_git_intel.py -v
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
git diff --check
tools/llmtool/.venv/bin/python -m pip wheel --no-deps tools/llmtool --wheel-dir /private/tmp/llmtool-a5-wheel
```

Focused coverage includes clean/nested/unborn/detached repositories, staged and
unstaged diffs, combined file states, untracked files, rename/copy parsing, all
conflict codes and a real merge conflict, spaces/Unicode/tabs/unusual-byte parser
roundtrip, ordering, immutable detached containers, bounded log/diff/metadata/
stderr/time, related directory/result caps, offline CLI, and malicious helper/text
handling. Existing ask/chat/context-pack regression tests remain intact.

One integration test is skipped: this macOS filesystem rejects non-UTF-8 filename
creation (approved execution confirms EILSEQ). Parser/JSON byte preservation is
still tested and passes. This is an explicit coverage gap, not a fully tested
cross-platform unusual-byte filesystem claim. Initial targeted runs exposed a
macOS process-group signal race; cleanup now falls back to signalling the direct
child, and the final bounded-diff/timeout tests pass without resource warnings.

An unapproved final-suite attempt was blocked by loopback binding in two existing
HTTP test classes: 264 tests ran, 2 setup errors, 1 skip, make exit 2, 24.573 s.
That attempt was not PASS; the canonical command was rerun with approval.
Packaging without build isolation failed because the current virtualenv lacks
setuptools; normal isolated build then required approval for build-dependency DNS.
Approved build succeeded, adding no runtime dependency or pyproject change.
The wheel includes the exact git_intel.py/cli.py sources, three SKILL.md resources,
and the CLI entry point; all packaged Python files parse under Python 3.9 syntax.
Final wheel SHA256: `dd06ae90fd8b9a080a073afddae6af502544f3943606d681342fb34c09eec39e`.

## Manual fixture

A disposable fixture contained committed.txt with one committed version and an
unstaged edit, staged.txt added to the index, and untracked.txt. Both installed CLI
commands returned exit 0 with empty stderr. Human output showed:

```text
STAGED: "staged.txt"
UNSTAGED: "committed.txt"
UNTRACKED: "untracked.txt"
CONFLICTED: (none)
```

JSON had matching category arrays; both diffs had exact byte_count and
truncated=false (143 unstaged bytes, 134 staged bytes). No real user config was
modified. Fixture setup used local Git writes only, no remotes, credentials or
user HOME config. The fixture was automatically removed. Runtime was also read
against the canonical repo without mutations. No live provider smoke was required
or attempted: A5 is deliberately offline.

## Limitations and disposition

Snapshots are multiple reads, not atomic against concurrent changes. Global/system
ignore/filter configuration is deliberately omitted. Filter-backed working-tree
files may appear modified because conversion is disabled for safety. No contents
of untracked files are collected; existing diffs can contain supplied repository
secrets, and no content redaction is promised. Related paths are only HEAD siblings
and may not describe current worktree presence or semantic dependencies. Partial
clones/promisor repos and submodule contents are unsupported. Large snapshots fail
safely rather than truncate. One unusual-byte filesystem test remains untested on
this host. No provider capability, role, agent, validator or routing is introduced.

README/ROADMAP document A5 and correct stale A4 merge status to 38b1a67, preserving
historical validation counts. This report is part of the A5-only commit. Existing
untracked benchmark/reasoning reports are preserved and excluded from staging.
External ChatGPT review: **PASS**. No blocking correctness, security, packaging, or regression issue was found in the A5 diff. The single skipped non-UTF-8 host-filesystem integration case remains an explicit coverage limitation, not a blocker. No implementation or test files were changed by this review. Do not start A6 before A5 is merged.
