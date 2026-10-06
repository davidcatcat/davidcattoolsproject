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
- detect configured clean/smudge/process filters; for mutation, reject any configured filter driver instead of neutralizing it;
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
- R-GIT: A5 hardening for every Git call, mutation-specific reject/pin rules for
  staging semantics, fail closed on unexpected repo state, no push;
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
    action_digest: str
    repository_root: str
    root_device: int
    root_inode: int
    head: str
    object_format: str
    index_sha256: str
    paths: tuple[str, ...]
    state_digest: str

@dataclass(frozen=True)
class GitAddResult:
    before: GitAddPreflight
    after_index_sha256: str
    staged_paths: tuple[str, ...]
    post_verify_code: str
    exit_code: int

@dataclass(frozen=True)
class GitAddFailure:
    before: GitAddPreflight
    after_index_sha256: Optional[str]
    observed_staged_paths: tuple[str, ...]
    failure_code: str
    post_verify_code: str

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
9. Reject linked worktrees, gitfiles, alternate git dirs, `commondir`, object alternates,
   `core.worktree`, config includes/includeIf, split/sparse index, alternate index and
   sparse checkout. A11b.2 supports only one ordinary non-bare working-tree repository
   with a real `<root>/.git` directory and one ordinary `<root>/.git/index`.
10. Every Git subprocess must use program-generated absolute `GIT_DIR=<root>/.git` and
    `GIT_WORK_TREE=<root>`; reject `core.bare=true`. Reject bare repositories.

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
- sparse checkout or sparse index: reject unconditionally in A11b.2;
- repository administrative state that implies an in-progress Git operation: reject.

A dirty working tree is **not globally forbidden** because ADD necessarily stages existing
working-tree changes. Instead, A11b.2 must enforce a stronger exact-scope rule:

- unrelated staged changes: reject;
- unrelated conflicted paths: reject;
- target already staged, intent-to-add, assume-unchanged, skip-worktree, rename/copy, deletion, ignored-untracked, sparse, or gitlink/submodule state: reject;
- disable rename detection in mutation preflight;
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
- walk parent directories from an already-open root dir-fd using `O_DIRECTORY|O_NOFOLLOW`-style semantics;
- each parent must be owned by euid and must not be group- or world-writable;
- reject if any component is a symlink;
- open the target with `O_NOFOLLOW`; use the same fd for `fstat` and SHA-256 hashing;
- require exact directory-entry name bytes to match the contract path, rejecting case-fold/NFC/NFD aliases;
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

Normative index subset:
- active index is exactly `<root>/.git/index`; no alternate index is supported;
- open with no-follow semantics; require regular file and `nlink=1`;
- hash exact raw bytes with SHA-256 using one A11b.2.1 function reused by future Action construction;
- missing index: reject;
- existing `<root>/.git/index.lock`: reject and never delete it;
- split/shared index: reject (`core.splitIndex=true`, `sharedindex.*`, or index `link` extension);
- sparse index/checkout: reject (`index.sparse`, `core.sparseCheckout`, `info/sparse-checkout`, or `sdir` extension);
- any skip-worktree, assume-unchanged, or intent-to-add entry anywhere: reject.

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
- compare pre/post stage-0 entries semantically as `(exact path bytes, mode, object OID, stage, skip-worktree/intent-to-add flags)`;
- expected post-state is the complete pre-entry set with only authorized target entries replaced;
- every unrelated semantic entry must remain identical and no new path may appear;
- for each target, compute the Git blob OID from the A11b.1-bound original bytes using the repository object format and require the staged OID to match;
- staged mode (100644/100755) must match the bound execute bit;
- exact target path bytes must match; case/Unicode normalization substitution fails;
- raw index SHA remains evidence for race/accounting binding but is not used as proof that unrelated semantic entries changed, because Git may refresh stat/cache bytes;
- if post-state cannot be proven, return typed failure evidence and do not run reset/restore/clean.

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

Mandatory mutation-specific hardening must include A5 settings plus fail-closed
rejection/pinning of configuration that could execute helpers or alter staging semantics.

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

**Mutation-specific rule: reject, do not neutralize, transforming filters/attributes.**

Preflight rejects if any effective repository config defines
`filter.<driver>.(clean|smudge|process|required)` or if any target path's `filter`
attribute is set to a driver/value, even when that driver has no config.

For every target, use fixed hardened `git check-attr -z` queries and reject any
content-transforming attribute that is not `unspecified` or `unset`: `filter`, `text`,
`eol`, `crlf`, `ident`, `working-tree-encoding`.

Pin `core.autocrlf=false`, `core.safecrlf=false`, and `add.ignoreErrors=false`.
Reject effective `core.fileMode=false`. Keep `core.hooksPath=/dev/null` and explicitly
test `post-index-change`. Repository `include.path` / `includeIf` is rejected in this
slice. Credential/SSH/GPG pins may remain defence in depth, but they are not the
primary `git add` control.

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
- import/AST boundary: outside tests, no production CLI/entrypoint/chat/orchestration/
  benchmark module imports or exposes the mutation module;
- primitive API does not accept/read `ApprovalValidationResult`;
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
- encrypting clean filter (git-crypt style): reject, index unchanged;
- driverless `filter=lfs` target attribute: reject, index unchanged;
- `text`/`eol`/`ident`/`working-tree-encoding`: reject;
- included config containing filter or `core.worktree`: reject;
- `core.worktree` redirection: reject;
- `core.fileMode=false`: reject;
- `post-index-change` hook: prove it is not executed;
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
- a path component beginning with dash is rejected by the A11b.1 contract; `--` remains
  defence in depth for all accepted paths;
- spaces are not permitted by the current A11b.1 grammar; Unicode alnum inside that
  grammar is tested where the host supports it;
- parent directory not owned by euid or group/world-writable: reject;
- exact directory-entry byte-name mismatch from case-fold/NFC/NFD alias: reject;
- parent symlink;
- target symlink;
- hardlink nlink>1;
- FIFO/socket/directory/device reject;
- inode swap between initial preflight and pre-spawn revalidation;
- content change between preflight and execution;
- case-fold/normalization collision where host supports it.

### Index/TOCTOU
- missing index: reject;
- pre-existing index.lock: reject and preserve it;
- split/shared/sparse index: reject;
- skip-worktree / assume-unchanged / intent-to-add anywhere: reject;
- wrong expected index hash;
- index changed after preflight;
- index inode replacement;
- race injected immediately before spawn;
- exact pre-index unchanged on rejected mutation;
- post-index hash changes on successful add;
- staged blob OID equals the Git object hash of exact bound bytes and staged mode matches
  the bound executable bit;
- unrelated stat/cache refresh with unchanged semantic entry still PASS;
- concurrent unrelated OID/path/mode mutation causes post-verify failure;
- only requested paths become staged.

### Process/error
- git executable missing;
- child nonzero;
- timeout;
- stderr flood;
- stdout flood if applicable;
- leader exits while a descendant remains: cleanup still attempts the process group;
- child spawns descendant; timeout kills group;
- real SIGTERM/SIGHUP/KeyboardInterrupt delivered to parent route through cleanup and
  restore prior signal handlers;
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

## 20. Security-review addendum — normative resolutions

This section resolves the blockers in `reports/llmtool_a11b2_git_hardening_spec_review_20261007.md`.
Where an earlier section conflicts with this addendum, this addendum is authoritative.

### B1 — CLOSED in spec

For mutation, configured filters and target `filter` attributes are rejected, not neutralized.
This prevents git-crypt plaintext or full LFS content from being staged.

### B2 — CLOSED in spec

Transforming attributes are rejected; autocrlf/safecrlf/add.ignoreErrors are pinned; `core.fileMode=false` is rejected.
Post-verification must prove staged blob OID equals the Git object hash of the exact A11b.1-bound bytes and staged mode matches the bound execute bit.

### B3 — CLOSED in spec

A11b.2 supports only a real non-symlink root with a real `.git` directory, explicit `GIT_DIR`/`GIT_WORK_TREE`, one ordinary index, no gitfile/commondir/alternates/config includes/core.worktree, and no split/sparse/special index modes.
`index.lock` pre-existence is a hard reject and is never deleted.

### B4 — CLOSED in spec

Post-verification is semantic index-entry equality, not raw-index byte equality.
Exact path bytes/mode/OID/stage/flags are compared; unrelated stat/cache refresh is tolerated only when semantic identity is unchanged.

### B5 — A11b.2.2 gate only

A11b.2.1 may implement deterministic preflight before B5 is closed.
Before mutation code (A11b.2.2) starts, targeted security review must approve:
- unconditional process-group cleanup even after the Git leader exits;
- scoped SIGTERM/SIGHUP/SIGINT handlers that route through cleanup and restore previous handlers;
- the macOS parent-SIGKILL strategy: supervisor/parent-death equivalent, or an explicitly accepted residual risk detected through index/semantic post-state recovery evidence;
- the premise that filters/hooks/fsmonitor/helpers are rejected/disabled, so the expected child tree is only Git.

### R5–R10 — required additions

- R5: add an import/AST/CLI boundary test proving no production CLI/entrypoint/chat/orchestration/benchmark path imports or exposes the mutation module.
- R6: the primitive does not accept or read `ApprovalValidationResult`; authorization remains an external caller precondition.
- R7: preflight/result/failure evidence includes action digest, root dev/inode, object format, `Action.expected_state.digest`, pre/post index SHA, exact paths, exit/failure code and post-verify code.
- R8: add adversarial cases for encrypting clean filters, driverless filter attrs, text/eol/ident/encoding attrs, post-index-change hook, included config/core.worktree, gitfile/commondir/alternates, index.lock, split/sparse/special index flags, case/NFD aliases, stat-only unrelated refresh, parent directory mode, real signals, and production import boundary.
- R9: a malicious same-UID concurrent process is outside the preventable isolation boundary; pre/post verification detects accidental/residual races but is not an OS sandbox against the same user.
- R10: QG-S1 OS sandbox requirements for executing repository code do not apply to this ADD-only subset only if tests prove all repo-code execution vectors (filters/transforming attrs/hooks/fsmonitor/helpers/alternate worktree) are rejected or disabled.

### Updated authorization

A11b.2.1 remains NOT AUTHORIZED until this addendum passes the requested targeted follow-up review.
A11b.2.2 remains additionally blocked on B5 even if A11b.2.1 is authorized.