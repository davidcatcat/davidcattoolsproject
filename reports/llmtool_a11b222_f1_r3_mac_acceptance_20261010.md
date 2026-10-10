# A11b.2.2.2 F1-r3 initial candidate — PENDING runner verification

Status: **PENDING**. This is the Codex implementation handoff, not acceptance,
independent review, Security PASS, Functional QA, CI PASS, or merge authorization.
The outer runner owns final immutable-candidate checks and additions to this report.

Task: `A11b222-F1-PrivateGitADD-R3-20261010`.
Authoritative control revision supplied by runner:
`5c7c5c0b6db07cb6d019cece0f321f7b3d9714e7`.
Verified local root: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/a11b222-f1-private-git-add-20261010-r3`.
Initial HEAD/base: `0b679537c487ae3eaa21702d64c96ec89ff88145`.
Initial tree: clean. Remote identity from local configuration:
`git@github.com:davidcatcat/davidcattoolsproject.git`; no fetch or remote
synchronization is claimed. Candidate remains uncommitted; runner must record its
final SHA, task-contract digest and source/test digests after verification.

## Pre-first-edit feasibility assessment

The complete merged F1 contract, its numbered MUST matrix, B5 §§3–22, the
self-anchor amendment and product integration delta were read before editing.
These are source/API feasibility decisions by Codex, not independent approval.

| MUST | Exact implementation/API mapping | Refusal regression (runner PENDING) |
| --- | --- | --- |
| A1 | `_git_add_private.runtime`, `standalone`, `verify_runtime`; `importlib.util.find_spec`, module/main origins, `sys.flags.isolated`, `wire.file_hash` | missing isolated entry, changed source manifest, import/public-route boundary |
| A2 | `run_fixture_add`, `endpoints`; `os.pipe`, `dup2`, `Popen(close_fds=True, pass_fds=(3,), shell=False)`, `fstat`, `fcntl.F_GETFL`, `/dev/fd`; restore prior FD3 and inheritable flag | missing/file/socket/write-only/extra FD; foreign pipe identity |
| A3 | Host `_main` and supervisor `_product_main`; `kernel.identity`, Darwin `proc_pidinfo` BSD PID/PPID/UID/start/PGID, `getsid` | wrong PID/UID/start identity, parent loss before spawn |
| A4 | `Reader`, `encode_frame`, `validate_frame`, `write_bytes`; bounded nonblocking `os.read/write`, `select`, monotonic deadline, big-endian prefix, strict JSON, typed `parse_action` | frame/Action/metadata overlimits, duplicate keys, depth, invalid UTF-8, EOF/trailing bytes, stale pipe, stalled transfer/EPIPE, wrong domain/digest |
| A5 | `_product_main` calls `verify_request` and repeats `fixture_check`; `g.preflight_git_add` reruns inside supervisor; complete plan and expected semantic index bound by canonical SHA-256 | bound digest/file/HEAD/index/lock drift, final fixture refusal before Git spawn |
| B1 | `_make_fixture`, `fixture_check`; `tempfile.mkdtemp` under normalized `gettempdir`, fixed child `repo`, `g._root` traverses every component with no-follow directory FDs | non-temp/canonical root, symlinked Git directory, inode drift |
| B2 | `fixture_check`; normalized overlap checks, root/project dev/inode comparison, fixed read-only `rev-parse --show-toplevel` | canonical checkout, root mismatch, aliases/symlinks |
| B3 | root marker uses `O_EXCL|O_NOFOLLOW`, 0600, exact nonce; bounded no-follow read checks owner/link count/fingerprint; marker ctime must precede `.git` birth with >1µs precision margin | absent/wrong nonce/mode/recreated-after-init/symlink marker |
| B4 | supervisor repeats independent fixture checks, full Action preflight, source checks and parent continuity before fixed spawn | last-gate refusal, lock/digest/state drift |

The installed venv resolves the product package under `-I`. A bounded stdlib
feasibility observation found that sanitized environments omit the Mac caller's
`TMPDIR`; the installed SDK declares `_CS_DARWIN_USER_TEMP_DIR=65537`, and
`os.confstr(65537)` is available. Fixture validation independently recognizes that
per-UID OS temp root and normalized stdlib temp root. Python's symbolic
`confstr_names` lacks this name here; the initial symbolic query failed and was
replaced with the SDK-confirmed numeric API. This observation proves no process
containment or Security result.

## Candidate changes

- `_git_add_private.py`: closed disposable-fixture setup vocabulary and data-only
  private launcher; canonical UTF-8 one-shot FIFO frame, pipe identity, Action and
  binding limits, product manifest, marker/root/parent checks, complete preflight
  and expected-index digest, helper rejection and first-match semantic verifier.
- `_git_add_host.py`: isolated FD3-gated Host; owns the supervisor anchor, critical
  FDs, signal scope and non-consuming monitor. Unknown/escaped or lost status
  after READY retains ownership indefinitely, including notification failure.
  No ACK/resolver/release/retry API exists.
- `_git_supervisor.py`: separately admitted product domain/manifest and independent
  final preflight; fixed Git ADD inherits its supervisor PGID/SID with no control
  FDs. Bounded discarded output, non-consuming exit status, group/child inventory
  and escape refusal. Original fake cases remain in the original fake domain.
- `_git_process_identity.py`: owned fast-exiting Git identity and bounded native
  group/child enumeration. Inventory is observation, never signaling authority.
- `_git_containment_protocol.py`: close raw FD on failed `fdopen`, preserving the
  original exception even when cleanup fails; initial schema dispatch supports
  the distinct product admission without changing fake message validation.
- `git_mutation.py`: bind observed branch, Git directory identity and config/
  attribute digests; fixed read-only top-level query and trusted PATH.
- New private/authority tests: framing, UTF-8 32-path 57,430-byte Action and
  near-limit CJK letters, unchanged emoji refusal, provenance/admission/helper
  negatives, retained ownership, native fixture positives and semantic ordering.
- Existing containment/mutation tests: extend the sealed private-module boundary
  for the new adapter only. Natural leader-exit regression is also a permanent
  native unit case, independent of parent-SIGKILL repetitions.
- `run_containment_acceptance.py`: recognize the exact r3 task/base/allowlist while
  preserving the prior contract regression. The 14 scenarios and 20+20 native
  repetition requirements remain intact; all r3 source/test files are hashed.

No controller/task/launcher files were recreated or modified. No staging, commit,
push, merge, branch switch, checkout/worktree creation, provider call, paid route,
system change or canonical-project Git ADD was performed by Codex.

## Evidence and pending gates

Codex performed source inspection only: all 11 edited Python files parsed through
`ast.parse`, and `git diff --check` reported no whitespace errors at that inspection.
No deterministic test suite, native containment probe, private fixture Git setup
or private ADD operation was executed by Codex. These static observations do not
replace any runner gate.

| Required evidence | Status |
| --- | --- |
| related syntax/private/authority/mutation checks | PENDING outer runner |
| venv full suite | PENDING outer runner |
| `/usr/bin/python3` full suite | PENDING outer runner |
| actual Mac 14 scenarios, 20+20 SIGKILL, ≤2s disappearance/identity proof | PENDING outer runner |
| exact candidate source/test manifest and immutable SHA | PENDING outer runner |
| independent ordinary Claude review and bounded evidence-driven repair | PENDING outer runner |
| exact Draft PR CI Python 3.9/3.14 | PENDING publication/CI |
| separate specialist Security and Functional QA | PENDING; merge blocking |

The same-UID trusted-installation premise is explicit: FIFO/PID/nonce/hash/marker
checks establish continuity and disposable-target provenance, not authentication
against hostile same-UID code. Strict helper denial plus native inventory needs
independent review; snapshots alone do not establish a sandbox. Check/spawn and
post-read intervals remain non-atomic under that premise. An inconclusive marker
ordering or identity refuses, and ambiguous containment stays permanently UNKNOWN.
Unrelated pre-staged work is conservatively refused by the existing preflight,
never overwritten. No F2/public launch, arbitrary user-repository mutation,
production approval/audit bypass, recovered incident, privacy or savings claim is
authorized by this candidate or any later F1 fixture PASS.

## Deterministic repair 2 — runner verification PENDING

The runner's second related-check attempt recorded syntax exit 0 and all 14
private-engine tests passing, followed by an authority suite failure (one failure,
one error). Those results precede this repair and do not verify the final candidate.
This repair changes only `tests/test_git_add_authority.py` and this report:

- The retention regression lacked the fixture root needed to evaluate the mocked
  `Popen` call, so it never acquired the supervisor anchor. Supply a mock-only root,
  assert the uncertain group cleanup was attempted, and explicitly assert that
  post-state classification is unreachable. Existing no-reap/no-restore and
  original-error assertions remain. Test-owned pipe cleanup now runs even if an
  assertion fails.
- The supervisor final-gate regression supplied arbitrary FD numbers to a real
  frame reader. Replace them with test-owned anonymous pipes and their actual
  device/inode bindings. Preserve assertions that independent preflight and the
  fixture check run, and Git spawn does not run after fixture rejection.

No production admission, containment, frame limit or mutation behavior changed in
this repair. Static syntax and whitespace validation are the only Codex checks;
the repaired authority suite, final related/full suites, Mac acceptance and
independent review remain **PENDING outer runner**. No Git publication occurred.

你需要做：無
