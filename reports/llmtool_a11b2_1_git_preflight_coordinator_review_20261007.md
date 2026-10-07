# llmtool A11b.2.1 — coordinator code/security review (2026-10-07)

## Reviewed revision

PR #43

Publication head:

`6fd58b2734a5831c2fae6a72f89e9276815a5ee8`

Locally tested implementation head from the submitted validation report:

`f69c67a6dc4409e6ca2145a07ca24f4649d79dcd`

The delta from the tested implementation head to the publication head is report-only.
No production or test bytes changed in that delta.

GitHub Actions PR run 37515122182 and push run 37515097286 both completed successfully
at publication head `6fd58b2734a5831c2fae6a72f89e9276815a5ee8`.

## Overall assessment

**BLOCKED — one bounded security/determinism fix is required before merge.**

The implementation otherwise preserves the authorized A11b.2.1 boundary:

- ADD preflight only;
- no `git add` execution;
- no public CLI/runtime mutation path;
- no action-schema widening;
- no approval interpretation;
- no A11b.2.2 mutation;
- fixed read-query enum rather than arbitrary Git argv;
- explicit GIT_DIR/GIT_WORK_TREE;
- filters and transforming attributes fail closed;
- full index/HEAD equality rejects all pre-staged state;
- semantic index baseline and exact bound blob OIDs are produced;
- hostile stderr/config values are not surfaced;
- A5 and A11b.1 regressions are reported green.

The development change that removed a status-based content comparison is also correct:
a status/diff-style query can execute clean conversion after a concurrent filter change;
the final implementation avoids that query class.

## F1 — BLOCKER: local core.excludesFile remains an external read surface

### Code path

`_check_config()` rejects includes, core.worktree, filter drivers, unsupported
extensions, promisor/index modes, bare repositories and fileMode=false, but it does not
reject `core.excludesFile`.

Later, `_Read.IGNORE` executes:

```text
git ls-files --others --ignored --exclude-standard -z -- <authorized paths>
```

The environment disables system/global config and omits HOME, but the command still
loads the repository's local config. Therefore a repository-local:

```ini
[core]
    excludesFile = /outside/repository/path
```

can still direct this query to read an arbitrary external ignore file.

Git's documented `--exclude-standard` behavior includes the file configured by
`core.excludesFile`. This means the current preflight's otherwise narrow repository
read boundary is not complete.

### Why this matters

This is not an arbitrary-command execution bug, but it violates the A11b.2.1 safe-subset
goal:

- untrusted repository config can cause a Git subprocess to read outside the bound repo;
- the path may refer to a FIFO/device/very large external file and turn an ignore check
  into avoidable availability/resource exposure;
- an external file can influence whether an untracked authorized target is classified
  as ignored;
- the future ADD plan currently carries the same SAFE_CONFIG and therefore does not
  eliminate this source either.

A timeout limits the subprocess duration but does not make the external read a desired
or deterministic capability.

### Required remediation

Use both fail-closed policy and defence in depth:

1. In `_check_config()`, reject a repository-local `core.excludesFile` key
   case-insensitively as `unsafe config`.
2. Add `core.excludesFile=/dev/null` (or the platform-safe null path abstraction used
   by the project, if one exists) to the fixed Git config used by all A11b.2.1 reads and
   the future ADD argv.
3. Add tests proving:
   - local `core.excludesFile=/PRIVATE_MARKER` is rejected before IGNORE query;
   - local `core.excludesFile` pointing to a FIFO is never opened/followed and the
     preflight returns promptly with `unsafe config`;
   - the spawned argv/future ADD plan contains the fixed null exclusion-file pin;
   - ordinary repository `.gitignore` and `.git/info/exclude` behavior still works
     as intended after the pin.
4. Re-run targeted A11b.2.1, A5, A11b.1, full Python suites and `git diff --check`.

Do not weaken the existing ignored-untracked-target rejection.

## Non-blocking observations

1. The implementation is deliberately conservative by comparing the complete index with
   the complete HEAD tree. This is stricter than a minimal exact-scope algorithm but is
   consistent with the reviewed N1 requirement: any pre-staged state is rejected.
2. The `.git` metadata walk is conservative and may reject large/hardlinked/special
   layouts. That is acceptable for A11b.2's explicitly narrow safe subset.
3. B5 remains open exactly where it belongs: the read-only runner retains A5-style
   cleanup, while mutation-specific TERM/HUP/SIGKILL containment remains an A11b.2.2
   gate. Nothing in this review authorizes A11b.2.2.

## Verdict

```text
A11b.2.1 COORDINATOR REVIEW:
BLOCKED

Reviewed publication head:
6fd58b2734a5831c2fae6a72f89e9276815a5ee8

Scope ADD-preflight only:
PASS

Public mutation path:
NONE

git add execution:
NOT IMPLEMENTED

Action/approval boundary:
PASS

Repository/HEAD/index binding:
PASS

Filter/attribute rejection:
PASS

Semantic index baseline:
PASS

External config/read boundary:
BLOCKED — core.excludesFile must be rejected/pinned

A5 regression evidence:
PASS per submitted validation

A11b.1 regression evidence:
PASS per submitted validation

Final-head CI:
PASS — PR + push workflow at 6fd58b2

A11b.2.2:
NOT AUTHORIZED

Ready to merge:
NO

Next:
fix F1, revalidate, then independent code/security review
```