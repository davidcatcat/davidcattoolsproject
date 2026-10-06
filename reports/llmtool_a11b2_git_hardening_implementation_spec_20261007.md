# llmtool A11b.2 — Git Hardening implementation specification (2026-10-07)

## Status

**DESIGN SPEC ONLY — implementation NOT STARTED.**

This specification is the required pre-implementation security/design gate for A11b.2.

Base main:

`54ef8f6c50139e2d778e50bd7271610cdf730bb9`

A11b.1 Typed Action & Approval Contract is already merged via PR #32. It is data-only:
there is still no executor, no public mutation command, no approval issuer/UI, and no
runtime path that can authorize a mutation.

A11b.2 must preserve that boundary.

## 1. Scope

A11b.2 introduces **internal hardened Git-mutation primitives and deterministic preflight
for the one Git operation already modeled by A11b.1: `GIT_OPERATION / ADD`.**

It does NOT add:
- commit;
- apply;
- checkout/switch;
- reset;
- clean;
- branch/tag creation/deletion;
- merge/rebase/cherry-pick;
- fetch/pull;
- push;
- remote mutation;
- submodule mutation;
- arbitrary Git argv;
- shell commands;
- a public CLI mutation entrypoint;
- automatic model-driven execution;
- an approval prompt/issuer;
- retry/fallback/repair;
- A11c routing.

The production primitive may be imported and exercised directly by unit/integration
tests against disposable Git fixtures, but the normal llmtool CLI/runtime must have no
path that can invoke it until later A11b approval/policy slices explicitly wire that
authorization.

## 2. Security ancestry

A11b.2 must carry forward the following existing contracts rather than invent a second
Git stack.

### A5 Git Intelligence

Reuse or exactly mirror A5's security boundary:

- argv list only; `shell=False`;
- fresh sanitized environment;
- `GIT_CONFIG_NOSYSTEM=1`;
- `GIT_CONFIG_GLOBAL=/dev/null`;
- `GIT_ATTR_NOSYSTEM=1`;
- `GIT_OPTIONAL_LOCKS=0` for read-only preflight;
- `GIT_NO_LAZY_FETCH=1`;
- `GIT_TERMINAL_PROMPT=0`;
- `GIT_PAGER=cat`, `PAGER=cat`;
- `GIT_LITERAL_PATHSPECS=1`;
- disable fsmonitor;
- disable hooks;
- disable untracked cache;
- disable external attributes;
- disable pager/color;
- disable protocol access;
- disable maintenance/gc auto side effects;
- detect configured clean/smudge/process filters and neutralize them;
- reject partial/promisor repositories;
- bounded stdout/stderr;
- wall-clock timeout;
- process-group cleanup.

A11b.2 must not bypass A5 by calling plain ambient `git status`, `git diff`, or
`git add`.

### QG-S1 requirements

This spec concretizes the A11b requirements from the merged security baseline:

- R-EXEC: deterministic argv, no shell strings, bounded environment/time/output,
  process-tree containment, no privilege escalation;
- R-FS: canonical repo/scope, symlink/hardlink/race handling, no cross-repo path;
- R-GIT: A5 hardening for every Git call, mutation-specific neutralization, fail closed
  on unexpected repo state, no push;
- R-APR: approval/action/state binding remains mandatory later; A11b.2 does not fake it;
- R-INJ: repo/model text is data and cannot choose Git actions or bypass policy;
- R-AUD: mutation audit must eventually be durable-before-action; because A11b.2 has no
  runtime authorization path yet, it must expose the exact pre/post evidence needed by
  the later audit layer rather than inventing a temporary unaudited public path.

## 3. A11b.1 contract consumed by A11b.2

A11b.2 supports only an already-parsed A11b.1 Action with:

```text
kind = GIT_OPERATION
payload.operation = ADD
```

The action already binds:

- task/run/contract identity;
- repository_id;
- canonical_root;
- root_device;
- root_inode;
- expected_head;
- exact scope;
- exact FileExpectation for each staged file;
- expected_index_sha256;
- expected RepositoryState digest through the approval contract.

A11b.2 must not widen the action schema.

No new "command" string is accepted.

## 4. Proposed production module

Create one focused module, for example:

`tools/llmtool/llmtool/git_mutation.py`

The exact name may be improved during implementation review, but the API boundary must
remain narrow.

Recommended public-internal API:

```python
@dataclass(frozen=True)
class GitAddPreflight:
    repository_root: str
    head: str
    index_sha256: str
    paths: tuple[str, ...]
    state_digest: str

@dataclass(frozen=True)
class GitAddResult:
    before: GitAddPreflight
    after_index_sha256: str
    staged_paths: tuple[str, ...]
    exit_code: int

def preflight_git_add(action: Action) -> GitAddPreflight:
    ...

def execute_git_add(action: Action, preflight: GitAddPreflight) -> GitAddResult:
    ...
```

Names are not normative. Semantics are.

The module must not expose:
- generic `run_git(args)`;
- arbitrary argv;
- shell strings;
- arbitrary environment overlays;
- arbitrary cwd;
- arbitrary subcommands.

A private hardened runner may exist, but only fixed reviewed commands may reach it.

## 5. Repository identity gate

Before any mutation:

1. Resolve the actual repository root using hardened Git.
2. Require exact path equality with `action.repository.canonical_root` after filesystem
   canonicalization.
3. Require root `st_dev` == `root_device`.
4. Require root `st_ino` == `root_inode`.
5. Require the root is a directory owned by the current effective user.
6. Reject execution when effective UID is 0/root.
7. Reject a root that is itself a symlink.
8. Reject nested/alternate repository resolution that differs from the bound root.
9. Reject linked worktree / alternate git-dir arrangements unless explicitly proven safe
   in this slice. Default: support ordinary non-bare working-tree repositories only.
10. Reject bare repositories.

Any mismatch is fail closed before mutation.

## 6. HEAD / branch / repository-state gate

Immediately before staging:

- `HEAD` must exist;
- HEAD must equal `action.repository.expected_head`;
- detached HEAD: reject;
- unborn HEAD: reject;
- merge/cherry-pick/rebase/revert/bisect sequencer state: reject;
- unresolved conflicts: reject;
- partial/promisor repository: reject;
- submodule paths in the action scope: reject;
- sparse checkout: reject for A11b.2 unless implementation review proves deterministic
  path semantics; default fail closed;
- repository administrative state that implies an in-progress Git operation: reject.

A dirty working tree is **not globally forbidden** because ADD necessarily stages existing
working-tree changes. Instead, A11b.2 must enforce a stronger exact-scope rule:

- unrelated staged changes: reject;
- unrelated conflicted paths: reject;
- action-target paths must match their bound FileExpectation exactly;
- unrelated unstaged/untracked files may remain untouched and must not be staged,
  deleted, reset, cleaned or rewritten;
- if implementation cannot prove only the authorized paths will change in the index,
  reject.

This refines the roadmap's generic "dirty worktree fail closed" rule into an exact
mutation-safe condition suitable for ADD.

## 7. File/path gate

Every staged path comes only from `GitAddPayload.files`.

For each path:

- must be relative and already valid under the A11b.1 literal path grammar;
- must also be inside `action.scope`;
- prepend `--` before path arguments;
- never use pathspec globbing/magic;
- use `GIT_LITERAL_PATHSPECS=1`;
- resolve parent directories without following symlinks;
- reject if any path component between repo root and target is a symlink;
- target must exist and be a regular file;
- target must not be a symlink;
- target must have `st_nlink == 1`;
- target's device/inode/mode/uid/gid/size/mtime_ns must equal the bound
  `FileMetadata`;
- target content SHA-256 must equal the bound `content_sha256`;
- reject FIFO/socket/device/directory/submodule/gitlink;
- reject `.git` and all A11b.1 denied credential/storage paths by construction;
- reject filesystem case-fold collisions or Unicode-normalization ambiguities when
  multiple authorized paths resolve to the same inode/path identity.

No file content is modified by Git ADD itself.

## 8. Index identity and race gate

A11b.1 binds `expected_index_sha256`.

A11b.2 must define one canonical index-hash algorithm and reuse it everywhere.

Recommended:
- locate the active Git index only through the reviewed repository layout;
- ordinary repository only for this slice;
- open the index with no-follow semantics where supported;
- require regular file / nlink=1;
- hash exact bytes with SHA-256;
- absent index is represented by a separately reviewed canonical sentinel only if A11b.1
  can bind it safely; otherwise reject repositories without an index in this slice.

Before mutation:
- current index hash must equal `payload.expected_index_sha256`;
- current index hash must also equal `action.expected_state.index_sha256` if present;
- disagreement between those contract fields is reject-before-mutation;
- record index inode/device/size/mtime/hash.

Immediately before spawning `git add`, revalidate:
- HEAD;
- root identity;
- every target FileExpectation;
- index identity/hash.

This second pre-spawn verification is the A11b.2 TOCTOU gate.

After `git add`:
- recompute index hash;
- collect hardened staged-status evidence;
- prove only the requested paths changed staging state;
- prove unrelated staged entries remain absent;
- if post-state cannot be proven, return failure and leave evidence for later recovery;
  do not run reset/restore/clean automatically in A11b.2.

## 9. Hardened Git ADD argv

The only mutation command in A11b.2 is semantically:

```text
git
  --no-pager
  [reviewed -c overrides]
  add
  --
  <literal authorized path 1>
  ...
```

Paths are sorted deterministically unless preserving contract order is required by the
schema; choose one rule and test it.

Mandatory mutation-specific hardening must include A5 settings plus neutralization of
configuration that could execute helpers or alter staging semantics.

At minimum review and pin:

```text
core.fsmonitor=false
core.hooksPath=/dev/null
core.untrackedCache=false
core.attributesFile=/dev/null
core.pager=cat
color.ui=false
protocol.allow=never
protocol.file.allow=never
protocol.http.allow=never
protocol.https.allow=never
protocol.ssh.allow=never
protocol.git.allow=never
protocol.ext.allow=never
maintenance.auto=false
gc.auto=0
credential.helper=
core.sshCommand=
gpg.program=
commit.gpgSign=false
tag.gpgSign=false
```

Filter keys discovered by the hardened preflight must be neutralized exactly as A5 does:
- `filter.*.clean=`
- `filter.*.smudge=`
- `filter.*.process=`
- `filter.*.required=false`

Implementation review must verify whether additional Git config can cause `git add` to
execute external processes or transform content. If so, fail closed or neutralize it.

Do not set `safe.directory=*`.
Do not trust repository config to authorize the repository.

## 10. Environment

Use a fresh allowlist environment, not `os.environ.copy()`.

Minimum inherited value:
- `PATH` only, unless implementation can use an absolute trusted Git binary.

Explicit values should include A5's:
- `LC_ALL=C`
- `GIT_CONFIG_NOSYSTEM=1`
- `GIT_CONFIG_GLOBAL=/dev/null`
- `GIT_ATTR_NOSYSTEM=1`
- `GIT_NO_LAZY_FETCH=1`
- `GIT_TERMINAL_PROMPT=0`
- `GIT_PAGER=cat`
- `PAGER=cat`
- `GIT_LITERAL_PATHSPECS=1`

For the mutation command, `GIT_OPTIONAL_LOCKS=0` must NOT be blindly copied if it would
prevent the required index lock/write. Determine experimentally/documentedly which
Git mutation behavior is required. Preflight reads should continue using optional-lock
suppression.

Never inherit:
- `HOME` unless explicitly required and isolated;
- Git dir/worktree/config overrides;
- credential variables;
- SSH agent/socket variables;
- askpass variables;
- proxy variables;
- loader injection variables;
- signing configuration;
- provider/API credentials.

## 11. Process containment

Every subprocess:
- `shell=False`;
- stdin = DEVNULL;
- bounded stdout/stderr;
- wall-clock timeout;
- `start_new_session=True`;
- close_fds=True.

A11b.2 tests must prove the runner terminates the complete process group on:
- timeout;
- Ctrl-C / KeyboardInterrupt path;
- SIGTERM handling path;
- SIGHUP handling path;
- internal exception while draining output;
- normal parent shutdown if the child remains alive.

If the production architecture cannot guarantee process-tree containment on a supported
platform, that mutation path must remain unavailable there.

No sudo, setuid or privilege escalation.

## 12. Output and error contract

Git stdout/stderr are untrusted repository/tool data.

Do not expose raw stderr in normal exception text.

Use fixed bounded error messages / stable codes if the surrounding error framework
supports them.

At minimum distinguish internally:
- repository identity mismatch;
- HEAD/state mismatch;
- index mismatch;
- path/file mismatch;
- unsupported repository mode/state;
- filter/config unsafe;
- Git start failure;
- timeout;
- output limit;
- Git nonzero exit;
- post-state verification failure.

Do not include:
- environment;
- credential-looking data;
- arbitrary Git stderr;
- file contents.

## 13. Audit handoff boundary

A11b.2 does not implement the final R-AUD durable audit system, but its result objects
must make future A11b.3+ audit deterministic.

Before mutation evidence must be sufficient to bind:
- action digest;
- repository identity;
- expected HEAD;
- pre-index SHA;
- authorized paths;
- pre-state digest.

After mutation evidence must include:
- exit status;
- post-index SHA;
- exact staged paths observed;
- post-state verification result.

No raw Git stdout/stderr is required for audit.

**No public runtime mutation path may be added until durable-before-action audit and real
authorization semantics are available.**

## 14. Approval boundary

A11b.2 must not reinterpret A11b.1's `ApprovalValidationResult`.

Specifically:
- `bindings_valid=True` is not execution authorization;
- JSON `source=USER` is not identity/authentication;
- model text "approved" is irrelevant;
- no debug flag bypass;
- no provider capability bypass;
- no non-TTY auto-approval.

This slice may expose a primitive that future A11b.3 calls **after** authorization, but
A11b.2 itself must not invent a temporary authorization token or hidden CLI flag.

## 15. Implementation phases

### A11b.2.1 — deterministic preflight / state verifier

Implement first:
- hardened repository discovery;
- repository identity check;
- HEAD/branch/sequencer checks;
- promisor/filter checks;
- literal target validation;
- target FileExpectation verification;
- index hash verification;
- exact-scope staged-state verification;
- fixed argv construction.

No mutation in the first commit if practical.

### A11b.2.2 — internal Git ADD primitive

After A11b.2.1 independent review:
- execute only fixed `git add -- <paths>`;
- pre-spawn TOCTOU revalidation;
- bounded runner/process containment;
- post-index/staged-state verification;
- no CLI/router wiring.

### A11b.2.3 — failure/security regression gate

Targeted tests + full suite + independent review.

Do not start A11b.3 automatically after merge.

## 16. Required tests

Use disposable fixture repositories only. No real user repository mutation in tests.

### Contract/scope tests
- reject non-GIT_OPERATION;
- reject future/unknown Git operation;
- accept only ADD;
- reject payload path not in action scope;
- reject duplicate/colliding path identities;
- reject files not matching FileExpectation;
- reject changed content/metadata after action creation.

### Repository identity/state
- wrong canonical root;
- wrong root device/inode;
- symlink root;
- bare repo;
- linked worktree/alternate git dir if unsupported;
- detached HEAD;
- wrong HEAD;
- unborn HEAD;
- merge conflict;
- merge/rebase/cherry-pick/revert/bisect in-progress markers;
- partial/promisor;
- sparse checkout if unsupported;
- submodule/gitlink path.

### Dirty-state precision
- clean repo + target modification → allowed preflight;
- unrelated unstaged file → preserved and not staged;
- unrelated untracked file → preserved and not staged;
- unrelated pre-staged file → reject;
- conflicted path anywhere → reject;
- target already staged unexpectedly → reject unless exact expected state explicitly
  models and tests that case.

### Git config / helper injection
Fixture configs attempting:
- fsmonitor helper;
- hooks;
- clean filter;
- smudge filter;
- process filter;
- required filter;
- pager;
- external diff;
- textconv;
- credential helper;
- core.sshCommand;
- signing program;
- protocol/file/network access;
- maintenance/gc side effects.

Prove no helper runs.

### Path/filesystem
- path beginning with dash stays a literal after `--`;
- spaces/Unicode within A11b.1 grammar where permitted;
- parent symlink;
- target symlink;
- hardlink nlink>1;
- FIFO/socket/directory/device reject;
- inode swap between initial preflight and pre-spawn revalidation;
- content change between preflight and execution;
- case-fold/normalization collision where host supports it.

### Index/TOCTOU
- wrong expected index hash;
- index changed after preflight;
- index inode replacement;
- race injected immediately before spawn;
- exact pre-index unchanged on rejected mutation;
- post-index hash changes on successful add;
- only requested paths become staged.

### Process/error
- git executable missing;
- child nonzero;
- timeout;
- stderr flood;
- stdout flood if applicable;
- child spawns descendant; timeout kills group;
- SIGTERM/SIGHUP/KeyboardInterrupt cleanup probes;
- raw hostile stderr never appears in public exception.

### Regression
- existing A5 read-only Git tests remain PASS;
- existing A11b.1 action-contract tests remain PASS;
- full llmtool suite PASS on supported Python versions;
- `git diff --check` PASS;
- packaging/import tests as required by project baseline.

## 17. Explicit non-goals / deferred items

Deferred to later slices:
- true approval issuer / TTY prompt;
- durable audit journal;
- public executor command;
- file create/write/delete executor;
- commit;
- patch apply;
- rollback/repair;
- network actions;
- remote Git;
- push;
- A11c model routing;
- A12 frontend.

A11b.2 must not pre-implement these under convenience helpers.

## 18. Definition of Done

A11b.2 is complete only when:

- the implementation remains ADD-only;
- there is no public runtime mutation path;
- every Git call uses the hardened runner;
- repository/root/HEAD/index/file expectations are revalidated immediately before
  mutation;
- hostile Git config cannot execute helpers;
- unrelated work is preserved and never staged;
- expected-state drift fails closed;
- process-tree cleanup is tested;
- no raw stderr/secrets are emitted;
- A5 and A11b.1 regressions remain green;
- targeted and full suites pass;
- independent code review passes;
- targeted security review maps implementation back to R-EXEC/R-FS/R-GIT/R-APR/R-INJ/R-AUD;
- validation report records exact tested revision.

After A11b.2 merge:
- STOP;
- do not start A11b.3 without a separate explicit next-step decision.

## 19. Required pre-implementation review

Before Codex writes production code, this spec itself must receive independent security/design
review.

Reviewer must answer:

```text
A11b.2 SPEC REVIEW:
PASS / BLOCKED

Scope ADD-only:
PASS / finding

No public mutation path:
PASS / finding

A5 hardening inheritance:
PASS / finding

Repository/HEAD/index binding:
PASS / finding

Exact dirty-state policy:
PASS / finding

Path/symlink/hardlink/TOCTOU:
PASS / finding

Hostile Git config/helper suppression:
PASS / finding

Process-tree containment:
PASS / finding

Approval boundary preserved:
PASS / finding

Audit handoff sufficient:
PASS / finding

Test matrix sufficient:
PASS / finding

New blockers:
NONE / details

May implement A11b.2.1:
YES / NO
```
